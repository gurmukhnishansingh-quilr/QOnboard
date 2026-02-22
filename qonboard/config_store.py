"""
SQLite-backed configuration store for QOnboard.

DB file: <AppData>/QOnboard/config.db
  - Windows : %APPDATA%\\QOnboard\\config.db
  - macOS   : ~/Library/Application Support/QOnboard/config.db
  - Linux   : ~/.local/share/QOnboard/config.db

Stored in the OS user-data directory so it persists across working directories
and is shared by every `qonboard` invocation on the machine.

On first use the store auto-ingests from any .env / .env_* files it finds in
the *current working directory* so the user doesn't need to do anything special
when setting up from an existing project.
Values can be updated at any time with `qonboard config set`.

Tables
------
global_config (key TEXT PK, value TEXT, updated_at TEXT)
env_config    (env_name TEXT, key TEXT, value TEXT, updated_at TEXT)
              primary key: (env_name, key)

ENV_FILE_MAP
------------
Maps each Jira environment name to the .env filename that holds its DB credentials.
UAE POC and UAE PROD share .env_uae because they run on the same infrastructure.
"""

from __future__ import annotations

import logging
import os
import platform
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def _app_data_dir() -> Path:
    """Return the OS-appropriate user-data directory for QOnboard."""
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    app_dir = base / "QOnboard"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


_DEFAULT_DB_PATH = _app_data_dir() / "config.db"

# Jira environment name → local .env filename
ENV_FILE_MAP: dict[str, str] = {
    "UAE POC":  ".env_uae",
    "UAE PROD": ".env_uae",
    "IND POC":  ".env_ind",
    "IND PROD": ".env_ind_prod",
    "USA POC":  ".env_us",
    "USA PROD": ".env_us_prod",
}

_GLOBAL_ENV_FILE = ".env"

_DDL = """
CREATE TABLE IF NOT EXISTS global_config (
    key        TEXT PRIMARY KEY NOT NULL,
    value      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS env_config (
    env_name   TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (env_name, key)
);
"""


class ConfigStore:
    """Singleton SQLite-backed configuration store."""

    _instance: "ConfigStore | None" = None

    def __init__(self, db_path: Path = _DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_DDL)
        self._conn.commit()
        logger.debug("ConfigStore opened: %s", db_path)
        self._maybe_ingest()

    @classmethod
    def instance(cls, db_path: Path = _DEFAULT_DB_PATH) -> "ConfigStore":
        if cls._instance is None:
            cls._instance = cls(db_path)
        return cls._instance

    # ── Global config ──────────────────────────────────────────────────

    def get_global(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM global_config WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_global(self, key: str, value: str) -> None:
        self._conn.execute(
            """INSERT INTO global_config (key, value, updated_at)
               VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
               ON CONFLICT(key) DO UPDATE SET
                   value      = excluded.value,
                   updated_at = excluded.updated_at""",
            (key, value),
        )
        self._conn.commit()
        logger.debug("global_config SET %s", key)

    # ── Env config ─────────────────────────────────────────────────────

    def get_env(self, env_name: str, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM env_config WHERE env_name = ? AND key = ?",
            (env_name, key),
        ).fetchone()
        return row["value"] if row else default

    def set_env(self, env_name: str, key: str, value: str) -> None:
        self._conn.execute(
            """INSERT INTO env_config (env_name, key, value, updated_at)
               VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
               ON CONFLICT(env_name, key) DO UPDATE SET
                   value      = excluded.value,
                   updated_at = excluded.updated_at""",
            (env_name, key, value),
        )
        self._conn.commit()
        logger.debug("env_config SET %s / %s", env_name, key)

    # ── Ingest from .env files ─────────────────────────────────────────

    def _maybe_ingest(self) -> None:
        """Auto-ingest from .env files if the store appears empty."""
        n_global = self._conn.execute("SELECT COUNT(*) FROM global_config").fetchone()[0]
        n_env    = self._conn.execute("SELECT COUNT(*) FROM env_config").fetchone()[0]
        if n_global == 0 and n_env == 0:
            logger.info("Config store is empty — auto-ingesting from .env files in cwd")
            self.ingest_from_files(force=False)

    def ingest_from_files(self, force: bool = False) -> dict:
        """Read .env / .env_* files from cwd and populate the store.

        Already-existing keys are skipped unless force=True.
        Returns {"global": N, "env": {env_name: count, ...}}.
        """
        from dotenv import dotenv_values

        cwd = Path.cwd()
        results: dict = {"global": 0, "env": {}}

        # ── Global .env ────────────────────────────────────────────────
        global_file = cwd / _GLOBAL_ENV_FILE
        if global_file.exists():
            for k, v in dotenv_values(str(global_file)).items():
                if v is None:
                    continue
                if force or self.get_global(k) is None:
                    self.set_global(k, v)
                    results["global"] += 1
            logger.info(
                "Ingested %d global key(s) from %s", results["global"], global_file
            )
        else:
            logger.warning("Global .env not found at %s — skipping", global_file)

        # ── Per-env files ──────────────────────────────────────────────
        # Multiple env names may map to the same file (e.g. UAE POC + UAE PROD → .env_uae)
        seen_files: set[str] = set()
        for env_name, filename in ENV_FILE_MAP.items():
            env_file = cwd / filename
            if not env_file.exists():
                logger.debug("Env file %s not found — skipping %s", env_file, env_name)
                continue

            count = 0
            for k, v in dotenv_values(str(env_file)).items():
                if v is None:
                    continue
                if force or self.get_env(env_name, k) is None:
                    self.set_env(env_name, k, v)
                    count += 1
            results["env"][env_name] = count

            if filename not in seen_files:
                shared = [n for n, f in ENV_FILE_MAP.items() if f == filename]
                logger.info("Ingested from %s -> env(s): %s", filename, shared)
            seen_files.add(filename)

        return results

    # ── Listing ────────────────────────────────────────────────────────

    def list_global(self) -> list[tuple[str, str, str]]:
        """Return [(key, value, updated_at)] from global_config."""
        rows = self._conn.execute(
            "SELECT key, value, updated_at FROM global_config ORDER BY key"
        ).fetchall()
        return [(r["key"], r["value"], r["updated_at"]) for r in rows]

    def list_env(self, env_name: str | None = None) -> list[tuple[str, str, str, str]]:
        """Return [(env_name, key, value, updated_at)] from env_config."""
        if env_name:
            rows = self._conn.execute(
                "SELECT env_name, key, value, updated_at FROM env_config "
                "WHERE env_name = ? ORDER BY key",
                (env_name,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT env_name, key, value, updated_at FROM env_config "
                "ORDER BY env_name, key"
            ).fetchall()
        return [(r["env_name"], r["key"], r["value"], r["updated_at"]) for r in rows]

    def close(self) -> None:
        self._conn.close()
        logger.debug("ConfigStore closed")
