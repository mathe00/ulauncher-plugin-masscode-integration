"""
Tests for the V5+ Markdown Vault loader (src.database.loader).

Coverage areas:
  - resolve_vault_space_dir: spaces layout, legacy layout, fallbacks
  - is_markdown_vault: both layouts, invalid inputs
  - parse_snippet_markdown: frontmatter extraction, YAML errors, CRLF
  - _parse_body_fragments / _extract_first_code_block / _merge_fragments
  - build_folder_lookup: current + legacy metadata, invalid entries
  - load_snippets_markdown: end-to-end vault loading on real temp fixtures
"""

import yaml

from src.database.loader import (
    build_folder_lookup,
    is_markdown_vault,
    load_snippets_markdown,
    parse_snippet_markdown,
    resolve_vault_space_dir,
)
from tests.helpers import (
    VaultBuilder,
    v5_body_multi,
    v5_body_single,
    v5_frontmatter,
)

# ===========================================================================
# resolve_vault_space_dir
# ===========================================================================


class TestResolveVaultSpaceDir:
    """Layout detection must mirror massCode's own resolution logic."""

    def test_spaces_layout_returns_code_dir(self, tmp_path):
        VaultBuilder(tmp_path, layout="spaces").build()
        resolved = resolve_vault_space_dir(str(tmp_path / "markdown-vault"))

        assert resolved == str(tmp_path / "markdown-vault" / "code")

    def test_legacy_layout_returns_vault_root(self, tmp_path):
        VaultBuilder(tmp_path, layout="legacy").build()
        resolved = resolve_vault_space_dir(str(tmp_path / "markdown-vault"))

        assert resolved == str(tmp_path / "markdown-vault")

    def test_code_dir_without_state_falls_back_to_spaces(self, tmp_path):
        # Empty new vault: code/ exists but state.json not created yet
        (tmp_path / "markdown-vault" / "code").mkdir(parents=True)

        resolved = resolve_vault_space_dir(str(tmp_path / "markdown-vault"))
        assert resolved.endswith("code")

    def test_nothing_exists_defaults_to_legacy(self, tmp_path):
        resolved = resolve_vault_space_dir(str(tmp_path))

        assert resolved == str(tmp_path)

    def test_spaces_state_takes_priority_over_legacy_state(self, tmp_path):
        # Both layouts present → spaces wins (detection order)
        VaultBuilder(tmp_path, layout="spaces").build()
        vault = tmp_path / "markdown-vault"
        # Also plant a legacy state.json at the root
        (vault / ".masscode").mkdir(exist_ok=True)
        (vault / ".masscode" / "state.json").write_text("{}", encoding="utf-8")

        assert resolve_vault_space_dir(str(vault)).endswith("code")


# ===========================================================================
# is_markdown_vault
# ===========================================================================


class TestIsMarkdownVault:
    """Vault validation accepts both layouts and rejects everything else."""

    def test_spaces_layout_is_valid(self, tmp_path):
        VaultBuilder(tmp_path, layout="spaces").build()

        assert is_markdown_vault(str(tmp_path / "markdown-vault")) is True

    def test_legacy_layout_is_valid(self, tmp_path):
        VaultBuilder(tmp_path, layout="legacy").build()

        assert is_markdown_vault(str(tmp_path / "markdown-vault")) is True

    def test_missing_directory_is_invalid(self, tmp_path):
        assert is_markdown_vault(str(tmp_path / "ghost")) is False

    def test_file_instead_of_directory_is_invalid(self, tmp_path):
        path = tmp_path / "a-file"
        path.write_text("x", encoding="utf-8")

        assert is_markdown_vault(str(path)) is False

    def test_directory_without_state_is_invalid(self, tmp_path):
        assert is_markdown_vault(str(tmp_path)) is False

    def test_tilde_path_expanded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        VaultBuilder(tmp_path, layout="legacy").build()

        assert is_markdown_vault("~/markdown-vault") is True


# ===========================================================================
# parse_snippet_markdown
# ===========================================================================


class TestParseSnippetMarkdown:
    """Markdown snippet file parsing: frontmatter + fragment body."""

    def _write_md(self, tmp_path, content: str) -> str:
        path = tmp_path / "snippet.md"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_valid_file_round_trip(self, tmp_path):
        fm = v5_frontmatter("greet", folder_id=2)
        md = f"---\n{yaml.safe_dump(fm).strip()}\n---\n{v5_body_single('print(1)')}"
        path = self._write_md(tmp_path, md)

        parsed_fm, fragments = parse_snippet_markdown(path)

        assert parsed_fm["name"] == "greet"
        assert parsed_fm["folderId"] == 2
        # Language comes from frontmatter contents[] ('plaintext' default),
        # NOT from the code fence — see TestLanguagePrecedence below.
        assert fragments == [
            {"label": "Fragment 1", "value": "print(1)", "language": "plaintext"}
        ]

    def test_crlf_line_endings_supported(self, tmp_path):
        fm = v5_frontmatter("crlf")
        body = "\r\n## Fragment: Fragment 1\r\n```py\r\nx = 1\r\n```\r\n"
        md = f"---\r\n{yaml.safe_dump(fm).strip()}\r\n---\r\n{body}"
        path = self._write_md(tmp_path, md)

        parsed_fm, fragments = parse_snippet_markdown(path)

        assert parsed_fm["name"] == "crlf"
        assert fragments[0]["value"] == "x = 1"

    def test_missing_frontmatter_returns_none(self, tmp_path, caplog):
        path = self._write_md(tmp_path, "just some text, no frontmatter")

        with caplog.at_level("WARNING"):
            fm, fragments = parse_snippet_markdown(path)

        assert fm is None
        assert fragments == []
        assert any("frontmatter" in m.lower() for m in caplog.messages)

    def test_invalid_yaml_returns_none(self, tmp_path, caplog):
        path = self._write_md(tmp_path, "---\nkey: [unclosed\n---\nbody here")

        with caplog.at_level("ERROR"):
            fm, _ = parse_snippet_markdown(path)

        assert fm is None
        assert any("YAML" in m for m in caplog.messages)

    def test_non_dict_frontmatter_returns_none(self, tmp_path):
        path = self._write_md(tmp_path, "---\n- just\n- a list\n---\nbody")

        fm, fragments = parse_snippet_markdown(path)

        assert fm is None
        assert fragments == []

    def test_unreadable_file_returns_none(self, tmp_path):
        result = parse_snippet_markdown(str(tmp_path / "missing.md"))

        assert result == (None, [])

    def test_empty_body_yields_empty_fragment(self, tmp_path):
        fm = v5_frontmatter("empty-body")
        md = f"---\n{yaml.safe_dump(fm).strip()}\n---\n"
        path = self._write_md(tmp_path, md)

        _, fragments = parse_snippet_markdown(path)

        # No '## Fragment:' heading → whole (empty) body becomes one fragment
        assert len(fragments) == 1
        assert fragments[0]["value"] == ""

    def test_language_filled_from_frontmatter_contents(self, tmp_path):
        fm = v5_frontmatter(
            "langs",
            contents=[{"id": 1, "label": "SQL part", "language": "sql"}],
        )
        # Body code fence has NO language tag; frontmatter supplies it
        body = "\n## Fragment: SQL part\n```\nSELECT 1;\n```\n"
        md = f"---\n{yaml.safe_dump(fm).strip()}\n---\n{body}"
        path = self._write_md(tmp_path, md)

        _, fragments = parse_snippet_markdown(path)

        assert fragments[0]["language"] == "sql"


# ===========================================================================
# _parse_body_fragments & _extract_first_code_block
# ===========================================================================


class TestParseBodyFragments:
    """Fragment section splitting inside the markdown body.

    IMPORTANT: code fence language tokens (```py, ```sh) are DISCARDED by
    the parser — body fragments always carry language ''. The only source
    of language metadata is the frontmatter contents[] merge.
    """

    def test_multiple_headings_split_into_fragments(self):
        from src.database.loader import _parse_body_fragments

        body = v5_body_multi([("A", "py", "a=1"), ("B", "sh", "echo b")])
        fragments = _parse_body_fragments(body)

        assert [f["label"] for f in fragments] == ["A", "B"]
        assert fragments[0]["value"] == "a=1"
        assert fragments[1]["value"] == "echo b"
        # Fence languages are never extracted — pinned as real behavior
        assert all(f["language"] == "" for f in fragments)

    def test_no_heading_treats_whole_body_as_one_fragment(self):
        """
        Whole-body fallback.

        QUIRK (pinned): _extract_first_code_block anchors its pattern with
        '^' but receives the RAW body here — when the body starts with a
        newline (the common MassCode layout: '---\\n\\n```...'), the anchor
        fails and the value comes back EMPTY. Only a body whose very first
        character is a backtick gets its block extracted.
        """
        from src.database.loader import _parse_body_fragments

        leading_newline_body = "\n```python\nsolo()\n```\n"
        fragments = _parse_body_fragments(leading_newline_body)
        assert fragments == [{"label": "Fragment 1", "value": "", "language": ""}]

    def test_no_heading_extract_when_fence_is_first_char(self):
        # The extraction works ONLY if the fence starts at position 0
        from src.database.loader import _parse_body_fragments

        flush_body = "```python\nsolo()\n```"
        fragments = _parse_body_fragments(flush_body)

        assert fragments[0]["value"] == "solo()"

    def test_section_without_code_block_gives_empty_value(self):
        from src.database.loader import _parse_body_fragments

        body = "\n## Fragment: prose only\nJust text, no fences.\n"
        fragments = _parse_body_fragments(body)

        assert fragments[0]["value"] == ""

    def test_labels_with_special_characters_kept(self):
        from src.database.loader import _parse_body_fragments

        body = v5_body_multi([("Conn (prod) — retry!", "py", "x")])
        fragments = _parse_body_fragments(body)

        assert fragments[0]["label"] == "Conn (prod) — retry!"


class TestExtractFirstCodeBlock:
    """Fenced block extraction incl. dynamic fence lengths."""

    def test_basic_three_backticks(self):
        from src.database.loader import _extract_first_code_block

        assert _extract_first_code_block("```py\nhello\n```") == "hello"

    def test_four_backtick_fence_allows_inner_triples(self):
        from src.database.loader import _extract_first_code_block

        text = "````md\n```js\ninner();\n```\n````"

        assert _extract_first_code_block(text) == "```js\ninner();\n```"

    def test_no_block_returns_empty_string(self):
        from src.database.loader import _extract_first_code_block

        assert _extract_first_code_block("no fences at all") == ""

    def test_multiline_content_preserved(self):
        from src.database.loader import _extract_first_code_block

        text = "```py\nline1\nline2\nline3\n```"

        assert _extract_first_code_block(text) == "line1\nline2\nline3"

    def test_first_block_wins_when_several_present(self):
        """
        QUIRK (pinned): with text BETWEEN two blocks, the lazy `.*?` is
        forced by the trailing '\\n\\1\\s*$' anchor to expand to the LAST
        closing fence — so the extraction spans from the first opening
        fence to the last one, swallowing intermediate prose. Only works
        as 'first block wins' when nothing follows the first block.
        """
        from src.database.loader import _extract_first_code_block

        text = "```py\nfirst\n```\ntext between\n```py\nsecond\n```"

        assert _extract_first_code_block(text) == (
            "first\n```\ntext between\n```py\nsecond"
        )

    def test_first_block_extracted_when_it_ends_the_text(self):
        from src.database.loader import _extract_first_code_block

        text = "```py\nfirst\n```"  # nothing after → clean extraction

        assert _extract_first_code_block(text) == "first"

    def test_fence_with_language_token(self):
        from src.database.loader import _extract_first_code_block

        assert _extract_first_code_block("```typescript\nconst a: number;\n```") == (
            "const a: number;"
        )


class TestLanguagePrecedence:
    """
    Language metadata can ONLY come from frontmatter contents[] entries,
    matched by fragment label. Code fence tokens are never parsed.

    Uses parse_snippet_markdown directly so fragment dicts are observable
    even for single-fragment files (the full loader would normalize them
    to plain strings).
    """

    def _parse(self, tmp_path, fm_contents, body):
        import yaml

        fm = v5_frontmatter("lang test", contents=fm_contents)
        path = tmp_path / "s.md"
        path.write_text(
            f"---\n{yaml.safe_dump(fm).strip()}\n---\n{body}", encoding="utf-8"
        )
        _, fragments = parse_snippet_markdown(str(path))
        return fragments

    def test_frontmatter_language_attached_by_label(self, tmp_path):
        fragments = self._parse(
            tmp_path,
            [{"id": 1, "label": "Query", "language": "sql"}],
            "\n## Fragment: Query\n```\nSELECT 1;\n```",
        )

        assert fragments == [
            {"label": "Query", "value": "SELECT 1;", "language": "sql"}
        ]

    def test_unmatched_labels_get_empty_language(self, tmp_path):
        fragments = self._parse(
            tmp_path,
            [{"id": 1, "label": "Fragment 1", "language": "python"}],
            "\n## Fragment: Renamed\n```\ncode\n```",
        )

        # 'Renamed' ≠ 'Fragment 1' → no frontmatter match → language ''
        assert fragments[0]["language"] == ""

    def test_default_frontmatter_supplies_plaintext(self, tmp_path):
        fragments = self._parse(tmp_path, None, v5_body_single("x"))

        assert fragments[0]["language"] == "plaintext"

    def test_frontmatter_beats_any_fence_token(self, tmp_path):
        # Even if a fence carries a language token it is never read;
        # the frontmatter value is the single source of truth.
        fragments = self._parse(
            tmp_path,
            [{"id": 1, "label": "F1", "language": "rust"}],
            "\n## Fragment: F1\n```python\nx\n```",
        )

        assert fragments[0]["language"] == "rust"


class TestMergeFragments:
    """Frontmatter/body fragment merging behavior.

    NOTE: the docstring of _merge_fragments claims frontmatter-only entries
    are 'included with empty value'; the implementation actually DROPS them.
    These tests pin the real (current) behavior so any future change is a
    conscious, visible decision.
    """

    def test_matching_label_uses_frontmatter_language(self):
        from src.database.loader import _merge_fragments

        fm = [{"id": 1, "label": "A", "language": "sql"}]
        body = [{"label": "A", "value": "SELECT", "language": ""}]

        merged = _merge_fragments(fm, body)

        assert merged == [{"label": "A", "value": "SELECT", "language": "sql"}]

    def test_body_language_wins_when_frontmatter_has_none(self):
        from src.database.loader import _merge_fragments

        fm = [{"id": 1, "label": "A"}]  # no language key
        body = [{"label": "A", "value": "v", "language": "rust"}]

        merged = _merge_fragments(fm, body)

        assert merged[0]["language"] == "rust"

    def test_unmatched_body_fragment_kept_as_is(self):
        from src.database.loader import _merge_fragments

        fm = []
        body = [{"label": "orphan", "value": "v", "language": "go"}]

        merged = _merge_fragments(fm, body)

        assert merged == [{"label": "orphan", "value": "v", "language": "go"}]

    def test_frontmatter_only_entries_are_dropped(self):
        # Documents actual behavior (differs from docstring — see class note)
        from src.database.loader import _merge_fragments

        fm = [{"id": 9, "label": "Ghost", "language": "py"}]
        body = []

        assert _merge_fragments(fm, body) == []

    def test_non_dict_frontmatter_entries_ignored(self):
        from src.database.loader import _merge_fragments

        fm = ["garbage", 42]
        body = [{"label": "A", "value": "v", "language": ""}]

        merged = _merge_fragments(fm, body)

        assert merged[0]["label"] == "A"


# ===========================================================================
# build_folder_lookup
# ===========================================================================


class TestBuildFolderLookup:
    """Folder id→name mapping built from metadata files in the space dir."""

    def test_current_meta_format(self, tmp_path):
        VaultBuilder(tmp_path).add_folder("Scripts", 42).add_folder("Notes", 7).build()
        space = str(tmp_path / "markdown-vault" / "code")

        lookup = build_folder_lookup(space)

        assert lookup == {42: "Scripts", 7: "Notes"}

    def test_legacy_meta_format_migrated(self, tmp_path):
        VaultBuilder(tmp_path).add_folder("Old", 3, legacy_meta=True).build()
        space = str(tmp_path / "markdown-vault" / "code")

        lookup = build_folder_lookup(space)

        assert lookup == {3: "Old"}

    def test_hidden_dirs_skipped(self, tmp_path):
        builder = VaultBuilder(tmp_path)
        builder.add_folder("Visible", 1).build()
        hidden = builder.space_dir / ".masscode"  # already exists; add another
        (hidden.parent / ".hidden-folder").mkdir(exist_ok=True)
        (hidden.parent / ".hidden-folder" / ".meta.yaml").write_text(
            yaml.safe_dump({"id": 99, "name": "Hidden"})
        )

        lookup = build_folder_lookup(str(builder.space_dir))

        assert 99 not in lookup

    def test_dir_without_metadata_skipped(self, tmp_path):
        builder = VaultBuilder(tmp_path)
        builder.add_folder("WithMeta", 1).build()
        (builder.space_dir / "NoMeta").mkdir(exist_ok=True)

        lookup = build_folder_lookup(str(builder.space_dir))

        assert list(lookup.values()) == ["WithMeta"]

    def test_metadata_without_id_skipped(self, tmp_path):
        builder = VaultBuilder(tmp_path)
        builder.build()
        orphan = builder.space_dir / "Orphan"
        orphan.mkdir(exist_ok=True)
        (orphan / ".meta.yaml").write_text(yaml.safe_dump({"name": "no-id"}))

        assert build_folder_lookup(str(builder.space_dir)) == {}

    def test_non_numeric_id_skipped_with_warning(self, tmp_path, caplog):
        builder = VaultBuilder(tmp_path)
        builder.build()
        bad = builder.space_dir / "BadId"
        bad.mkdir(exist_ok=True)
        (bad / ".meta.yaml").write_text(
            yaml.safe_dump({"id": "not-a-number", "name": "B"})
        )

        with caplog.at_level("WARNING"):
            lookup = build_folder_lookup(str(builder.space_dir))

        assert lookup == {}
        assert any("Invalid folder ID" in m for m in caplog.messages)

    def test_name_falls_back_to_directory_name(self, tmp_path):
        builder = VaultBuilder(tmp_path)
        builder.build()
        d = builder.space_dir / "DirName"
        d.mkdir(exist_ok=True)
        (d / ".meta.yaml").write_text(yaml.safe_dump({"id": 5}))  # no name key

        lookup = build_folder_lookup(str(builder.space_dir))

        assert lookup == {5: "DirName"}

    def test_missing_space_dir_returns_empty(self, tmp_path):
        assert build_folder_lookup(str(tmp_path / "void")) == {}


# ===========================================================================
# load_snippets_markdown — end-to-end on real fixtures
# ===========================================================================


class TestLoadSnippetsMarkdownHappyPath:
    """Full vault loading through both layouts."""

    def test_spaces_layout_loads_snippets(self, tmp_path):
        VaultBuilder(tmp_path, layout="spaces").add_snippet(
            "greet.md",
            frontmatter=v5_frontmatter("greet"),
            body=v5_body_single("print('hi')"),
        ).build()
        vault = str(tmp_path / "markdown-vault")

        snippets = load_snippets_markdown(vault)

        assert len(snippets) == 1
        assert snippets[0]["name"] == "greet"
        assert snippets[0]["content"] == "print('hi')"
        assert snippets[0]["isDeleted"] is False

    def test_legacy_layout_loads_snippets(self, tmp_path):
        VaultBuilder(tmp_path, layout="legacy").add_snippet(
            "note.md",
            frontmatter=v5_frontmatter("note"),
            body=v5_body_single("hello world", lang="text"),
        ).build()
        vault = str(tmp_path / "markdown-vault")

        snippets = load_snippets_markdown(vault)

        assert len(snippets) == 1
        assert snippets[0]["content"] == "hello world"

    def test_multi_fragment_preserved_as_list(self, tmp_path):
        VaultBuilder(tmp_path).add_snippet(
            "multi.md",
            frontmatter=v5_frontmatter("multi"),
            body=v5_body_multi([("Part A", "py", "a"), ("Part B", "sh", "b")]),
        ).build()
        vault = str(tmp_path / "markdown-vault")

        content = load_snippets_markdown(vault)[0]["content"]

        assert isinstance(content, list)
        assert [f["label"] for f in content] == ["Part A", "Part B"]
        # Labels don't match frontmatter contents[] → languages stay ''
        assert all(f["language"] == "" for f in content)

    def test_deleted_snippets_skipped_bool_and_int(self, tmp_path):
        deleted_int = v5_frontmatter("gone-int")
        deleted_int["isDeleted"] = 1
        deleted_bool = v5_frontmatter("gone-bool")
        deleted_bool["isDeleted"] = True

        (
            VaultBuilder(tmp_path)
            .add_snippet(
                "kept.md",
                frontmatter=v5_frontmatter("kept"),
                body=v5_body_single("k"),
            )
            .add_snippet("gone-int.md", frontmatter=deleted_int, body="")
            .add_snippet("gone-bool.md", frontmatter=deleted_bool, body="")
            .build()
        )
        vault = str(tmp_path / "markdown-vault")

        names = [s["name"] for s in load_snippets_markdown(vault)]

        assert names == ["kept"]

    def test_name_falls_back_to_filename_stem(self, tmp_path):
        fm = v5_frontmatter("")  # empty name
        fm["name"] = None
        VaultBuilder(tmp_path).add_snippet(
            "fallback-name.md", frontmatter=fm, body=v5_body_single("x")
        ).build()
        vault = str(tmp_path / "markdown-vault")

        assert load_snippets_markdown(vault)[0]["name"] == "fallback-name"

    def test_folder_resolved_via_lookup(self, tmp_path):
        fm = v5_frontmatter("filed", folder_id=12)
        VaultBuilder(tmp_path).add_folder("Archive", 12).add_snippet(
            "doc.md", frontmatter=fm, body=v5_body_single("data"), folder="Archive"
        ).build()
        vault = str(tmp_path / "markdown-vault")

        snippet = load_snippets_markdown(vault)[0]

        assert snippet["_folder"] == "Archive"

    def test_optional_metadata_attached(self, tmp_path):
        fm = v5_frontmatter("rich", favorites=True, description="desc!")
        VaultBuilder(tmp_path).add_snippet(
            "rich.md", frontmatter=fm, body=v5_body_single("z")
        ).build()

        snippet = load_snippets_markdown(str(tmp_path / "markdown-vault"))[0]

        assert snippet["_isFavorites"] is True
        assert snippet["_description"] == "desc!"

    def test_unregistered_files_not_loaded(self, tmp_path):
        """Files not referenced by state.json are invisible to the loader."""
        (
            VaultBuilder(tmp_path)
            .add_snippet(
                "registered.md",
                frontmatter=v5_frontmatter("reg"),
                body=v5_body_single("r"),
            )
            .add_raw_file(
                "unregistered.md",  # raw drop, never registered
                f"---\nname: unreg\n---\n{v5_body_single('u')}",
            )
            .build()
        )
        vault = str(tmp_path / "markdown-vault")

        names = [s["name"] for s in load_snippets_markdown(vault)]

        assert names == ["reg"]


class TestLoadSnippetsMarkdownErrors:
    """Graceful degradation paths of the V5 loader."""

    def test_missing_vault_dir_returns_empty(self, tmp_path):
        assert load_snippets_markdown(str(tmp_path / "ghost")) == []

    def test_missing_state_json_returns_empty(self, tmp_path, caplog):
        (tmp_path / "bare-vault").mkdir()

        with caplog.at_level("ERROR"):
            result = load_snippets_markdown(str(tmp_path / "bare-vault"))

        assert result == []
        assert any("state file not found" in m.lower() for m in caplog.messages)

    def test_corrupt_state_json_returns_empty(self, tmp_path, caplog):
        builder = VaultBuilder(tmp_path)
        builder.meta_dir.mkdir(parents=True)
        (builder.meta_dir / "state.json").write_text("{corrupt!", encoding="utf-8")

        with caplog.at_level("ERROR"):
            result = load_snippets_markdown(str(builder.vault_dir))

        assert result == []
        assert any("Invalid JSON" in m for m in caplog.messages)

    def test_entry_without_filepath_skipped(self, tmp_path, caplog):
        builder = VaultBuilder(tmp_path)
        builder.set_state({"snippets": [{"id": 1}], "counters": {"snippetId": 0}})
        builder.build()

        with caplog.at_level("WARNING"):
            result = load_snippets_markdown(str(builder.vault_dir))

        assert result == []
        assert any("no filePath" in m for m in caplog.messages)

    def test_missing_snippet_file_counted_and_skipped(self, tmp_path, caplog):
        builder = VaultBuilder(tmp_path)
        builder._entries.append({"filePath": "does-not-exist.md", "id": 1})
        builder.build()

        with caplog.at_level("WARNING"):
            result = load_snippets_markdown(str(builder.vault_dir))

        assert result == []
        assert any("Snippet file not found" in m for m in caplog.messages)

    def test_unparsable_snippet_file_counted_and_skipped(self, tmp_path, caplog):
        builder = VaultBuilder(tmp_path)
        builder.add_raw_file("broken.md", "no frontmatter here")
        builder._entries.append({"filePath": "broken.md", "id": 1})
        builder.build()

        with caplog.at_level("WARNING"):
            result = load_snippets_markdown(str(builder.vault_dir))

        assert result == []
        assert any("Failed to parse snippet" in m for m in caplog.messages)

    def test_partial_failure_still_loads_good_snippets(self, tmp_path):
        builder = VaultBuilder(tmp_path)
        builder.add_snippet(
            "good.md",
            frontmatter=v5_frontmatter("good"),
            body=v5_body_single("g"),
        )
        builder.add_raw_file("bad.md", "also no frontmatter")
        builder._entries.append({"filePath": "bad.md", "id": 2})
        builder.build()

        snippets = load_snippets_markdown(str(builder.vault_dir))

        assert [s["name"] for s in snippets] == ["good"]

    def test_empty_state_snippets_list(self, tmp_path):
        VaultBuilder(tmp_path).set_state({"snippets": [], "counters": {}}).build()

        assert load_snippets_markdown(str(tmp_path / "markdown-vault")) == []


class TestV5InboxRoundTrip:
    """Writer→Loader round trip: inbox-saved snippets must be searchable."""

    def test_saved_inbox_snippet_is_loaded(self, tmp_path):
        from src.database.writer import save_snippet_to_inbox

        VaultBuilder(tmp_path, layout="spaces").build()
        vault = str(tmp_path / "markdown-vault")

        result = save_snippet_to_inbox(
            db_path=vault,
            masscode_version="v5",
            content="inbox code",
            name="From Inbox",
        )
        assert result["success"], result

        snippets = load_snippets_markdown(vault)
        names = [s["name"] for s in snippets]

        assert "From Inbox" in names
        saved = next(s for s in snippets if s["name"] == "From Inbox")
        assert saved["content"] == "inbox code"

    def test_saved_inbox_snippet_legacy_layout(self, tmp_path):
        from src.database.writer import save_snippet_to_inbox

        VaultBuilder(tmp_path, layout="legacy").build()
        vault = str(tmp_path / "markdown-vault")

        result = save_snippet_to_inbox(
            db_path=vault,
            masscode_version="v5",
            content="legacy save",
            name="Legacy Save",
        )
        assert result["success"], result

        names = [s["name"] for s in load_snippets_markdown(vault)]
        assert "Legacy Save" in names
