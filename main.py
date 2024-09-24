import os
import sys

# Ajouter le dossier libs au sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), 'libs'))

import json
import logging
import subprocess
from ulauncher.api.client.Extension import Extension
from ulauncher.api.client.EventListener import EventListener
from ulauncher.api.shared.event import KeywordQueryEvent, ItemEnterEvent
from ulauncher.api.shared.item.ExtensionResultItem import ExtensionResultItem
from ulauncher.api.shared.action.RenderResultListAction import RenderResultListAction
from ulauncher.api.shared.action.CopyToClipboardAction import CopyToClipboardAction
from ulauncher.api.shared.action.BaseAction import BaseAction
from fuzzywuzzy import process

# Set logging level to DEBUG for detailed logs
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


class MassCodeExtension(Extension):
    def __init__(self):
        super(MassCodeExtension, self).__init__()
        self.subscribe(KeywordQueryEvent, KeywordQueryEventListener())
        self.subscribe(ItemEnterEvent, ItemEnterEventListener())

class KeywordQueryEventListener(EventListener):
    def on_event(self, event, extension):
        logger.debug("Processing KeywordQueryEvent")
        query = event.get_argument() or ""
        snippets_path = os.path.expanduser(extension.preferences['mc_db_path'])
        copy_paste_mode = extension.preferences['copy_paste_mode']
        items = []

        logger.debug(f"Query: {query}")
        logger.debug(f"Snippets Path: {snippets_path}")
        logger.debug(f"Copy Paste Mode: {copy_paste_mode}")

        try:
            with open(snippets_path, 'r') as file:
                data = json.load(file)
                all_snippets = data['snippets']
                snippet_strings = [{"name": snippet['name'], "content": ' '.join(content['value'] for content in snippet['content'])} for snippet in all_snippets]
                snippet_texts = [snippet['name'] + ' ' + snippet['content'] for snippet in snippet_strings]

                matches = process.extract(query, snippet_texts, limit=10)

                for match in matches:
                    matched_text = match[0]
                    matched_snippet = next(snippet for snippet in snippet_strings if snippet['name'] in matched_text)
                    content_str = matched_snippet['content']
                    description = (content_str[:100] + '...') if len(content_str) > 100 else content_str

                    action = self.determine_action(copy_paste_mode, content_str)
                    items.append(ExtensionResultItem(icon='images/icon.png',
                                                     name=self.wrap_text(self.highlight_match(matched_snippet['name'], query), 50),
                                                     description=description,
                                                     on_enter=action))
                logger.debug(f"Matched Snippets: {matches}")
            return RenderResultListAction(items)

        except Exception as e:
            logger.error("Error searching snippets: %s", e)
            return RenderResultListAction([
                ExtensionResultItem(icon='images/icon.png',
                                    name='Error',
                                    description='An error occurred. Please check the logs.',
                                    on_enter=CopyToClipboardAction(''))
            ])

    def highlight_match(self, text, query):
        words = query.split()
        for word in words:
            text = text.replace(word, f"<b>{word}</b>")
        return text

    def wrap_text(self, text, width):
        words = text.split()
        lines = []
        current_line = ""

        for word in words:
            if len(current_line) + len(word) + 1 <= width:
                current_line += (word + " ")
            else:
                lines.append(current_line.strip())
                current_line = word + " "

        if current_line:
            lines.append(current_line.strip())

        return '\n'.join(lines)

    def determine_action(self, mode, content):
        if mode == 'copy':
            return CopyToClipboardAction(content)
        elif mode == 'paste':
            return BaseAction(lambda: subprocess.call("xdotool type --delay 1 '{}'".format(content.replace("'", "\\'")), shell=True))
        elif mode == 'both':
            def do_both():
                if pyperclip_installed:
                    pyperclip.copy(content)
                else:
                    subprocess.call("echo '{}' | xclip -selection clipboard".format(content.replace("'", "\\'")), shell=True)
                subprocess.call("xdotool type --delay 1 '{}'".format(content.replace("'", "\\'")), shell=True)
            
            return BaseAction(do_both)

class ItemEnterEventListener(EventListener):
    def on_event(self, event, extension):
        data = event.get_data()
        if data is None:
            return RenderResultListAction([])

        snippet_content = data.get('content', '')

        return RenderResultListAction([
            ExtensionResultItem(icon='images/icon.png',
                                name='Snippet Copied',
                                description='Content copied to clipboard',
                                on_enter=CopyToClipboardAction(snippet_content))
        ])

if __name__ == '__main__':
    MassCodeExtension().run()
