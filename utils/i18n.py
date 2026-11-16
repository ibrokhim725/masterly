from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"
SUPPORTED_LANGUAGES = ("uz", "ru")
DEFAULT_LANGUAGE = "uz"


class Translator:
    def __init__(self, locales_dir: Path) -> None:
        self._locales: dict[str, dict[str, str]] = {}
        self._load(locales_dir)

    def _load(self, locales_dir: Path) -> None:
        for lang in SUPPORTED_LANGUAGES:
            path = locales_dir / f"{lang}.json"
            try:
                with path.open("r", encoding="utf-8") as handle:
                    self._locales[lang] = json.load(handle)
            except FileNotFoundError:
                logger.error("Locale file not found: %s", path)
                self._locales[lang] = {}
            except json.JSONDecodeError as exc:
                logger.error("Invalid locale file %s: %s", path, exc)
                self._locales[lang] = {}

    def get(self, lang: str | None, key: str, **kwargs: object) -> str:
        language = lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
        template = (
            self._locales.get(language, {}).get(key)
            or self._locales.get(DEFAULT_LANGUAGE, {}).get(key)
            or key
        )
        if kwargs:
            try:
                return template.format(**kwargs)
            except (KeyError, IndexError, ValueError):
                logger.warning("Locale formatting failed for key %s (%s)", key, language)
                return template
        return template

    def variants(self, key: str) -> tuple[str, ...]:
        values = {self._locales[lang].get(key) for lang in SUPPORTED_LANGUAGES}
        values.discard(None)
        return tuple(values)  # type: ignore[arg-type]


translator = Translator(LOCALES_DIR)


def t(lang: str | None, key: str, **kwargs: object) -> str:
    return translator.get(lang, key, **kwargs)


def button_variants(key: str) -> tuple[str, ...]:
    return translator.variants(key)
