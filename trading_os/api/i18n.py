"""Interface languages and the browser's translation catalog."""

from fastapi import APIRouter, HTTPException

from ..services import i18n

router = APIRouter(prefix="/api/i18n", tags=["i18n"])


@router.get("")
def languages():
    return {"default": i18n.DEFAULT_LANGUAGE, "languages": [{"code": c, "name": n} for c, n in i18n.LANGUAGES.items()]}


@router.get("/{language}")
def messages(language: str):
    """``{English text: translation}`` for the browser (empty for English, the source language)."""
    if language not in i18n.LANGUAGES:
        raise HTTPException(status_code=404, detail=i18n.tr("Unknown language '{language}'", language=language))
    return {"language": language, "messages": i18n.web_catalog(language)}
