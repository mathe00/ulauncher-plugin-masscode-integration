"""
Sanity checks for src.constants.

These guard against accidental edits that would silently break the
extension (e.g. a typo in a version identifier or an inverted threshold).
"""

from src import constants


class TestVersionIdentifiers:
    """MassCode version identifiers must stay distinct and stable."""

    def test_all_versions_distinct(self):
        values = {
            constants.MASSCODE_V3,
            constants.MASSCODE_V4,
            constants.MASSCODE_V5,
        }
        assert len(values) == 3

    def test_version_values(self):
        assert constants.MASSCODE_V3 == "v3"
        assert constants.MASSCODE_V4 == "v4"
        assert constants.MASSCODE_V5 == "v5"

    def test_known_spaces_list(self):
        assert constants.VAULT_CODE_SPACE in constants.VAULT_KNOWN_SPACES
        assert constants.VAULT_NOTES_SPACE in constants.VAULT_KNOWN_SPACES
        assert constants.VAULT_MATH_SPACE in constants.VAULT_KNOWN_SPACES


class TestVaultConstants:
    """V5 vault internal file names."""

    def test_meta_dir_is_hidden_dotfile(self):
        assert constants.VAULT_META_DIR.startswith(".")

    def test_state_file_name(self):
        assert constants.VAULT_STATE_FILE == "state.json"

    def test_folder_meta_files_differ(self):
        # Current and legacy folder metadata filenames must not collide
        assert (
            constants.VAULT_FOLDER_META_FILE != constants.VAULT_FOLDER_META_FILE_LEGACY
        )


class TestThresholds:
    """Search/display thresholds must remain in sane ranges."""

    def test_fuzzy_threshold_in_unit_range(self):
        assert 0 <= constants.FUZZY_SCORE_THRESHOLD <= 100

    def test_max_results_positive(self):
        assert constants.MAX_RESULTS > 0

    def test_max_history_queries_positive(self):
        assert constants.MAX_HISTORY_QUERIES > 0

    def test_default_smart_ratio_disabled_by_default(self):
        # 0.0 means the Smart Single Result feature is off by default
        assert constants.DEFAULT_SMART_RATIO_THRESHOLD == 0.0

    def test_save_subcommand(self):
        assert constants.SAVE_SUBCOMMAND == "new"


class TestPaths:
    """Derived paths must point at the real extension root."""

    def test_history_file_inside_extension_dir(self):
        assert constants.HISTORY_FILE.startswith(constants.EXTENSION_DIR)
        assert constants.HISTORY_FILE.endswith("context_history.json")

    def test_default_db_paths_use_expanduser_style_tilde(self):
        for path in (
            constants.DEFAULT_DB_PATH_V3,
            constants.DEFAULT_DB_PATH_V4,
            constants.DEFAULT_DB_PATH_V5,
        ):
            assert path.startswith("~/")


class TestSaveFeatureDefaults:
    """Save-new-snippet feature defaults."""

    def test_default_language(self):
        assert constants.DEFAULT_SNIPPET_LANGUAGE == "plain_text"

    def test_preview_limit_reasonable(self):
        # Must leave room in Ulauncher's one-line description
        assert 10 <= constants.CLIPBOARD_PREVIEW_MAX_LEN <= 200

    def test_name_max_len_reasonable(self):
        assert 10 <= constants.SNIPPET_NAME_MAX_LEN <= 100
