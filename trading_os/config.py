"""Runtime configuration.

Everything the app writes (SQLite database, uploaded screenshots, automatic
safety backups) lives in a single *data directory*, so backing up the whole
journal is as simple as copying one folder. The location can be changed with
the ``TRADING_OS_DATA_DIR`` environment variable or the ``--data-dir`` CLI flag.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
STATIC_DIR = PACKAGE_DIR / "static"

DEFAULT_DATA_DIR = PROJECT_DIR / "trading_os_data"
DEMO_DATA_DIR = PROJECT_DIR / "trading_os_demo_data"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_MAX_UPLOAD_MB = 10


@dataclass(frozen=True)
class AppConfig:
    """Resolved filesystem locations and server limits."""

    data_dir: Path
    max_upload_mb: int = DEFAULT_MAX_UPLOAD_MB

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


def load_config(data_dir: str | Path | None = None) -> AppConfig:
    """Build the config, giving precedence to an explicit ``data_dir``."""
    resolved = data_dir or os.environ.get("TRADING_OS_DATA_DIR") or DEFAULT_DATA_DIR
    max_upload_mb = int(os.environ.get("TRADING_OS_MAX_UPLOAD_MB", DEFAULT_MAX_UPLOAD_MB))
    config = AppConfig(data_dir=Path(resolved).expanduser().resolve(), max_upload_mb=max_upload_mb)
    config.ensure_dirs()
    return config
