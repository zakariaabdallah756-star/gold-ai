"""Translations for server-side text: guardrail messages, notifications, reports, API errors.

The English source text is the key: ``tr("Risk {risk}% is above your {cap}% maximum.", risk=2.5, cap=2)``
returns the Italian template from ``locales/it/server/*.json`` when the current language is Italian, and the
English text otherwise (or when the catalog has no entry), formatted with ``str.format`` - so format specs
such as ``{rr:.2f}`` work in both languages. Literal braces in a template with parameters are doubled.

The current language is a context variable: the HTTP middleware sets it from the account settings for
each request, background jobs set it with :func:`using`. Tests and code without a context get English.

Browser text lives in ``locales/<lang>/web/*.json`` and is served by ``GET /api/i18n/{language}``.
:func:`N_` marks text that is translated later (labels in constant tables) so the catalog checks find it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"
DEFAULT_LANGUAGE = "en"
LANGUAGES = {"en": "English", "it": "Italiano"}  # code -> name in its own language
# Language name the AI is asked to write in.
AI_LANGUAGE_NAMES = {"en": "English", "it": "Italian"}

_current: ContextVar[str] = ContextVar("trading_os_language", default=DEFAULT_LANGUAGE)


def normalize(language: str | None) -> str:
    return language if language in LANGUAGES else DEFAULT_LANGUAGE


@lru_cache(maxsize=16)
def catalog(language: str, part: str) -> dict[str, str]:
    """Merged ``locales/<language>/<part>/*.json`` ({English: translation}); empty for English."""
    if language == DEFAULT_LANGUAGE or language not in LANGUAGES:
        return {}
    merged: dict[str, str] = {}
    for path in sorted((LOCALES_DIR / language / part).glob("*.json")):
        merged.update(json.loads(path.read_text(encoding="utf-8")))
    return merged


def current() -> str:
    return _current.get()


@contextmanager
def using(language: str | None) -> Iterator[str]:
    """Run a block in ``language`` (unknown codes fall back to English)."""
    token = _current.set(normalize(language))
    try:
        yield _current.get()
    finally:
        _current.reset(token)


def set_current(language: str | None):
    """Set the language for the rest of this context; returns a token for :func:`reset`."""
    return _current.set(normalize(language))


def reset(token) -> None:
    _current.reset(token)


def tr(text: str, /, **params) -> str:
    """``text`` in the current language, formatted with ``params``."""
    language = _current.get()
    template = catalog(language, "server").get(text, text) if language != DEFAULT_LANGUAGE else text
    if not params:
        return template
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):  # a broken translation must never break the message
        return text.format(**params)


def tr_n(count: int | float, singular: str, plural: str, /, **params) -> str:
    """``singular`` when ``count`` is 1, else ``plural`` - translated; ``{n}`` is the count."""
    return tr(singular if count == 1 else plural, n=count, **params)


def N_(text: str) -> str:  # noqa: N802 - the conventional gettext marker name
    """Mark ``text`` for translation without translating it now (translate it with ``tr`` where shown)."""
    return text


def ai_language_name(language: str | None = None) -> str:
    return AI_LANGUAGE_NAMES[normalize(language or current())]


def stored_language(session_factory: sessionmaker[Session]) -> str:
    """The language saved in the account settings (English when unset or unreadable)."""
    from ..models import AccountSettings

    try:
        with session_factory() as db:
            return normalize(db.scalar(select(AccountSettings.language).limit(1)))
    except Exception:  # the app must keep working even if the settings table is not ready
        return DEFAULT_LANGUAGE


def web_catalog(language: str) -> dict[str, str]:
    return catalog(normalize(language), "web")
