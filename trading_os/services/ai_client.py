"""Claude API access for Vision AI: client construction, credential discovery and error mapping.

The ``anthropic`` package is imported lazily so the journal keeps working when it is not
installed. Credentials come from the key saved in the trading plan, or else from whatever the
SDK resolves on its own (``ANTHROPIC_API_KEY``, ``ANTHROPIC_AUTH_TOKEN``, workload identity
federation or an ``ant auth login`` profile).
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

from ..constants import AI_EFFORTS, AI_MODELS
from ..models import AccountSettings
from .i18n import N_, tr

# Display data for the settings page (USD per million input / output tokens).
MODEL_INFO: dict[str, dict[str, Any]] = {
    "claude-opus-5-5": {"name": "Claude Opus 5.5", "input_per_mtok": 4.0, "output_per_mtok": 20.0},
    "claude-sonnet-5-5": {"name": "Claude Sonnet 5.5", "input_per_mtok": 2.0, "output_per_mtok": 10.0},
    "claude-haiku-5-5": {"name": "Claude Haiku 5.5", "input_per_mtok": 0.10, "output_per_mtok": 0.50},
    "claude-fable-5-1": {"name": "Claude Fable 5.1", "input_per_mtok": 10.0, "output_per_mtok": 50.0},
}

EFFORT_INFO: dict[str, str] = {  # English source texts; ``efforts_catalog`` translates them
    "low": N_("Fastest and cheapest - a quick read that can miss subtle structure."),
    "medium": N_("Balanced speed and depth."),
    "high": N_("Thorough analysis - recommended for chart reads."),
    "xhigh": N_("Deeper reasoning for busy, multi-timeframe charts. Slower and costs more."),
    "max": N_("Maximum depth. Slowest and most expensive."),
}

SETUP_STEPS = (  # English source texts; ``setup_steps`` translates them
    N_("Set the ANTHROPIC_API_KEY environment variable before starting Trading OS, or"),
    N_('run "ant auth login" once to store a Claude login profile, or'),
    N_("save an API key in Trading plan -> AI assistant."),
)

# Environment variables any of which lets the SDK authenticate without a saved key.
CREDENTIAL_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_IDENTITY_TOKEN",
    "ANTHROPIC_IDENTITY_TOKEN_FILE",
)


class AIUnavailable(RuntimeError):
    """Claude cannot be called at all: the SDK is missing or no credentials are configured."""


class AIRequestError(RuntimeError):
    """A Claude API call failed; ``status_code`` is the HTTP status to report to the browser."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def package_installed() -> bool:
    return importlib.util.find_spec("anthropic") is not None


def _config_dir() -> Path:
    """Directory where ``ant auth login`` stores profiles (mirrors the SDK's resolution)."""
    configured = os.environ.get("ANTHROPIC_CONFIG_DIR")
    if configured:
        return Path(configured)
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        return (Path(appdata) if appdata else Path.home() / "AppData" / "Roaming") / "Anthropic"
    return Path.home() / ".config" / "anthropic"


def _active_profile_configured() -> bool:
    base = _config_dir()
    profile = os.environ.get("ANTHROPIC_PROFILE", "").strip()
    if not profile:
        try:
            profile = (base / "active_config").read_text(encoding="utf-8").strip()
        except OSError:
            profile = ""
    profile = profile or "default"
    if Path(profile).name != profile:
        return False
    return (base / "configs" / f"{profile}.json").is_file()


def key_source(settings: AccountSettings) -> str:
    """Where credentials come from: ``saved`` | ``environment`` | ``none``."""
    if settings.ai_api_key:
        return "saved"
    if any(os.environ.get(name) for name in CREDENTIAL_ENV_VARS) or _active_profile_configured():
        return "environment"
    return "none"


def get_client(settings: AccountSettings):
    """Build an Anthropic client, or raise :class:`AIUnavailable` with a setup hint."""
    if not package_installed():
        raise AIUnavailable(tr('The "anthropic" Python package is not installed. Run: pip install anthropic'))
    if key_source(settings) == "none":
        raise AIUnavailable(tr("Claude is not configured yet - no API key or login was found."))
    import anthropic

    if settings.ai_api_key:
        return anthropic.Anthropic(api_key=settings.ai_api_key)
    return anthropic.Anthropic()


def stream_final_message(client, params: dict[str, Any]):
    """Run a streaming Messages request and return the final message.

    Streaming avoids HTTP timeouts on long outputs. SDK errors are mapped to
    :class:`AIRequestError` / :class:`AIUnavailable`, most specific first.
    """
    import anthropic

    try:
        with client.beta.messages.stream(**params) as stream:
            return stream.get_final_message()
    except anthropic.APIStatusError as exc:
        raise _status_error(exc, params) from exc
    except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
        raise AIRequestError(503, tr("Cannot reach the Claude API. Check your internet connection.")) from exc
    except anthropic.CredentialsError as exc:
        raise AIUnavailable(tr("Claude credentials could not be loaded: {error}", error=exc)) from exc
    except TypeError as exc:
        # Raised by the SDK before any network call when no credential resolves.
        if "authentication method" in str(exc):
            raise AIUnavailable(tr("Claude is not configured yet - no API key or login was found.")) from exc
        raise
    except Exception as exc:
        # The SDK wraps transport errors only on the initial request; a connection that drops while the
        # answer streams surfaces as the HTTP library's own error.
        if _transport_error(exc):
            raise AIRequestError(502, tr("The connection to Claude was interrupted. Try again.")) from exc
        raise


def _transport_error(exc: BaseException) -> bool:
    module = type(exc).__module__.split(".")[0]
    return isinstance(exc, OSError) or module in ("httpx", "httpx2", "httpcore", "h11", "h2", "anyio")


# Error types the API may send inside an already-started stream (HTTP status 200).
_STREAM_ERRORS = {"overloaded_error": 502, "api_error": 502, "rate_limit_error": 429}


def _status_error(exc, params: dict[str, Any]) -> AIRequestError:
    """Map an API status error to an :class:`AIRequestError`, most specific first."""
    import anthropic

    status = exc.status_code
    if status == 200:  # an error event in the middle of the stream: use its error type
        body = exc.body if isinstance(exc.body, dict) else {}
        error = body.get("error") if isinstance(body.get("error"), dict) else body
        status = _STREAM_ERRORS.get(str(error.get("type", "")), 502)
        if status == 429:
            return AIRequestError(429, tr("Claude API rate limit reached. Try again shortly."))
        return AIRequestError(502, tr("The Claude API had a server error. Try again in a moment."))
    if isinstance(exc, anthropic.AuthenticationError):
        return AIRequestError(401, tr("Claude rejected the API key. Check it in Trading plan -> AI assistant."))
    if isinstance(exc, anthropic.PermissionDeniedError):
        return AIRequestError(
            403,
            tr("This API key is not allowed to use {model}: {message}", model=params.get("model"), message=exc.message),
        )
    if isinstance(exc, anthropic.RateLimitError):
        retry_after = exc.response.headers.get("retry-after")
        if retry_after:
            return AIRequestError(
                429, tr("Claude API rate limit reached. Try again in {seconds} seconds.", seconds=retry_after)
            )
        return AIRequestError(429, tr("Claude API rate limit reached. Try again shortly."))
    if isinstance(exc, anthropic.BadRequestError):
        return AIRequestError(400, tr("Claude rejected the request: {message}", message=exc.message))
    if status >= 500:
        return AIRequestError(502, tr("The Claude API had a server error. Try again in a moment."))
    return AIRequestError(400, tr("Claude API error ({status}): {message}", status=status, message=exc.message))


def capped_effort(effort: str, ceiling: str) -> str:
    """The lower of ``effort`` and ``ceiling`` in :data:`AI_EFFORTS` order."""
    return AI_EFFORTS[min(AI_EFFORTS.index(effort), AI_EFFORTS.index(ceiling))]


def setup_steps() -> list[str]:
    """How to give Claude credentials, in the current language."""
    return [tr(step) for step in SETUP_STEPS]


def models_catalog() -> list[dict[str, Any]]:
    return [{"id": model, **MODEL_INFO[model]} for model in AI_MODELS]


def efforts_catalog() -> list[dict[str, str]]:
    return [{"id": effort, "description": tr(EFFORT_INFO[effort])} for effort in AI_EFFORTS]
