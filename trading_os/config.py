"""Runtime configuration.

Everything the app writes (SQLite database, uploaded screenshots, automatic
safety backups) lives in a single *data directory*, so backing up the whole
journal is as simple as copying one folder. The location can be changed with
the ``TRADING_OS_DATA_DIR`` environment variable or the ``--data-dir`` CLI flag.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"

# Data lives inside the standalone trading_os/ folder (git-ignored there).
DEFAULT_DATA_DIR = PACKAGE_DIR / "data"
DEMO_DATA_DIR = PACKAGE_DIR / "demo_data"
# v1.0 kept the journal next to the package; it is moved in on first start.
LEGACY_DATA_DIR = PACKAGE_DIR.parent / "trading_os_data"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_MAX_UPLOAD_MB = 10


@dataclass(frozen=True)
class AppConfig:
    """Resolved filesystem locations and server limits."""

    data_dir: Path
    max_upload_mb: int = DEFAULT_MAX_UPLOAD_MB
    # Background thread (strategy alert scanner, notification watchers). Off in tests.
    background_jobs: bool = True
    # Host names (besides localhost and IP addresses) the server answers to, e.g. a LAN name or a tunnel:
    # normalised TRADING_OS_ALLOWED_HOSTS / --allow-host entries, wildcards such as *.trycloudflare.com allowed.
    allowed_hosts: tuple[str, ...] = ()
    # Entries that could not be used (shown by the CLI and on Trading Plan > Network & access).
    ignored_hosts: tuple[str, ...] = ()
    # Where the server listens (set by the CLI; shown on Trading Plan > Network & access).
    bind_host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    @property
    def db_path(self) -> Path:
        return self.data_dir / "journal.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.uploads_dir, self.backups_dir):
            path.mkdir(parents=True, exist_ok=True)


def migrate_legacy_data_dir() -> Path | None:
    """Move a v1.0 journal from ``../trading_os_data`` into ``trading_os/data``.

    Only runs when the new folder does not exist yet, so nothing is overwritten.
    Returns the new location when a move happened.
    """
    if DEFAULT_DATA_DIR.exists() or not (LEGACY_DATA_DIR / "journal.db").is_file():
        return None
    shutil.move(str(LEGACY_DATA_DIR), str(DEFAULT_DATA_DIR))
    return DEFAULT_DATA_DIR


def load_config(data_dir: str | Path | None = None) -> AppConfig:
    """Build the config, giving precedence to an explicit ``data_dir``."""
    from .services.network import parse_patterns  # here: the CLI imports this module before checking packages

    resolved = data_dir or os.environ.get("TRADING_OS_DATA_DIR") or DEFAULT_DATA_DIR
    max_upload_mb = int(os.environ.get("TRADING_OS_MAX_UPLOAD_MB", DEFAULT_MAX_UPLOAD_MB))
    background_jobs = os.environ.get("TRADING_OS_BACKGROUND_JOBS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    allowed_hosts, ignored_hosts = parse_patterns([os.environ.get("TRADING_OS_ALLOWED_HOSTS", "")])
    config = AppConfig(
        data_dir=Path(resolved).expanduser().resolve(),
        max_upload_mb=max_upload_mb,
        background_jobs=background_jobs,
        allowed_hosts=allowed_hosts,
        ignored_hosts=ignored_hosts,
    )
    config.ensure_dirs()
    return config
