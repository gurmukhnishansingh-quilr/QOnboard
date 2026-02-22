"""
Persistent state manager — tracks onboarding progress per ticket and per environment.

State file: .onboard_state.json (in the current working directory, git-ignored)

Structure:
{
  "PMM-4916": {
    "started_at": "2026-02-23T10:00:00+00:00",
    "monitor_password": "<plaintext — local only, never committed>",
    "environments": {
      "UAE POC": {
        "steps_done": [1, 2, 3, 4, 5],
        "tenant": {"id": "...", "subscriberid": "...", "name": "..."},
        "monitoring_user": {"email": "...", "password": "..."},
        "completed": true,
        "completed_at": "2026-02-23T10:05:00+00:00"
      },
      "IND POC": { "steps_done": [1], ... }
    },
    "completed": false,
    "completed_at": null
  }
}
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .clients.postgres_client import TenantRecord

logger = logging.getLogger(__name__)

# Resolved at runtime so it always sits next to where the user runs the command
_DEFAULT_PATH = Path.cwd() / ".onboard_state.json"


class StateManager:
    def __init__(self, path: Path = _DEFAULT_PATH) -> None:
        self._path = path
        self._state: dict = self._load()

    # ── Persistence ────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                logger.debug("Loaded state from %s (%d ticket(s))", self._path, len(data))
                return data
            except Exception as exc:
                logger.warning("Could not read state file %s: %s — starting fresh", self._path, exc)
        return {}

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Could not save state to %s: %s", self._path, exc)

    # ── Internal helpers ───────────────────────────────────────────────

    def _ticket(self, ticket_key: str) -> dict:
        return self._state.setdefault(ticket_key, {
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "monitor_password": None,
            "environments": {},
            "completed": False,
            "completed_at": None,
        })

    def _env(self, ticket_key: str, env_name: str) -> dict:
        return self._ticket(ticket_key)["environments"].setdefault(env_name, {
            "steps_done": [],
            "tenant": None,
            "monitoring_user": None,
            "completed": False,
            "completed_at": None,
        })

    # ── Monitoring password (ticket-level, shared across all envs) ─────

    def get_monitor_password(self, ticket_key: str) -> str | None:
        return self._state.get(ticket_key, {}).get("monitor_password")

    def save_monitor_password(self, ticket_key: str, password: str) -> None:
        self._ticket(ticket_key)["monitor_password"] = password
        self._save()

    # ── Step tracking (per env) ────────────────────────────────────────

    def is_step_done(self, ticket_key: str, env_name: str, step: int) -> bool:
        return step in (
            self._state.get(ticket_key, {})
                .get("environments", {})
                .get(env_name, {})
                .get("steps_done", [])
        )

    def mark_step_done(self, ticket_key: str, env_name: str, step: int) -> None:
        env = self._env(ticket_key, env_name)
        if step not in env["steps_done"]:
            env["steps_done"].append(step)
        self._save()
        logger.debug("State: %s / %s step %d done", ticket_key, env_name, step)

    def get_steps_done(self, ticket_key: str, env_name: str) -> list[int]:
        return sorted(
            self._state.get(ticket_key, {})
                .get("environments", {})
                .get(env_name, {})
                .get("steps_done", [])
        )

    # ── Tenant caching (per env) ───────────────────────────────────────

    def save_tenant(self, ticket_key: str, env_name: str, tenant: TenantRecord) -> None:
        self._env(ticket_key, env_name)["tenant"] = {
            "id": tenant.id,
            "subscriberid": tenant.subscriberid,
            "name": tenant.name,
        }
        self._save()

    def get_tenant(self, ticket_key: str, env_name: str) -> TenantRecord | None:
        raw = (
            self._state.get(ticket_key, {})
                .get("environments", {})
                .get(env_name, {})
                .get("tenant")
        )
        if not raw:
            return None
        return TenantRecord(id=raw["id"], subscriberid=raw["subscriberid"], name=raw["name"])

    # ── Monitoring user (per env) ──────────────────────────────────────

    def save_monitoring_user(self, ticket_key: str, env_name: str, email: str) -> None:
        self._env(ticket_key, env_name)["monitoring_user"] = {"email": email}
        self._save()

    def get_monitoring_user(self, ticket_key: str, env_name: str) -> str | None:
        """Return the monitoring email if previously saved, else None."""
        raw = (
            self._state.get(ticket_key, {})
                .get("environments", {})
                .get(env_name, {})
                .get("monitoring_user")
        )
        return raw["email"] if raw else None

    # ── Environment completion ─────────────────────────────────────────

    def is_env_completed(self, ticket_key: str, env_name: str) -> bool:
        return (
            self._state.get(ticket_key, {})
                .get("environments", {})
                .get(env_name, {})
                .get("completed", False)
        )

    def mark_env_completed(self, ticket_key: str, env_name: str) -> None:
        env = self._env(ticket_key, env_name)
        env["completed"] = True
        env["completed_at"] = datetime.now(tz=timezone.utc).isoformat()
        self._save()
        logger.debug("State: %s / %s completed", ticket_key, env_name)

    # ── Ticket completion ──────────────────────────────────────────────

    def is_completed(self, ticket_key: str, all_env_names: list[str]) -> bool:
        """True only if every environment in all_env_names is completed."""
        envs = self._state.get(ticket_key, {}).get("environments", {})
        return all(envs.get(e, {}).get("completed", False) for e in all_env_names)

    def mark_completed(self, ticket_key: str) -> None:
        ticket = self._ticket(ticket_key)
        ticket["completed"] = True
        ticket["completed_at"] = datetime.now(tz=timezone.utc).isoformat()
        self._save()
        logger.debug("State: %s fully completed", ticket_key)

    def started_at(self, ticket_key: str) -> str | None:
        return self._state.get(ticket_key, {}).get("started_at")
