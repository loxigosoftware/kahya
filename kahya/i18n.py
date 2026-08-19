"""i18n — language files live in lang/<code>.json.

Adding a language = copy lang/en.json to lang/xx.json and translate.
The UI language is a panel setting (config.language); the bot re-reads
it on every message, so a change applies immediately.
"""
from __future__ import annotations

import html
import json
from pathlib import Path


class I18n:
    def __init__(self, lang_dir: Path, language: str = "tr"):
        self.lang_dir = lang_dir
        self.language = language
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        path = self.lang_dir / f"{self.language}.json"
        if not path.exists():
            path = self.lang_dir / "en.json"
            if not path.exists():
                self._data = {}
                return
        self._data = json.loads(path.read_text(encoding="utf-8"))

    def set_language(self, language: str) -> None:
        if language != self.language:
            self.language = language
            self._load()

    def t(self, key: str, **kwargs) -> str:
        """Translate a dotted key like 'bot.confirm_title' with {placeholders}.

        Placeholder values are HTML-escaped so user/LLM text can never
        break the Telegram HTML formatting of the surrounding message.
        """
        node = self._data
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return key
        text = node if isinstance(node, str) else key
        if kwargs:
            try:
                text = text.format(**{k: html.escape(str(v))
                                      for k, v in kwargs.items()})
            except (KeyError, IndexError):
                pass
        return text

    def data(self) -> dict:
        return self._data


def load(cfg) -> I18n:
    return I18n(cfg.dir / "lang", cfg.language)
