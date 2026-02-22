"""
Persistent state manager — tracks which onboarding steps have been
completed for each Jira ticket so restarts can resume where they left off.

State file: .onboard_state.json  (in the current working directory, git-ignored)

Structure:
{
  "PMM-4916": {
    "started_at": "2026-02-23T10:00:00+00:00",
    "steps_done": [1, 2, 3, 4],
    "tenant": {"id": "...", "subscriberid": "...", "name": "..."},
    "completed": true,
    "completed_at": "2026-02-23T10:05:00+00:00"
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
                logger.debug(
                    "Loaded onboard state from %s (%d ticket(s))", self._path, len(data)
                )
                return data
            except Exception as exc:
                logger.warning(
                    "Could not read state file %s: %s — starting fresh", self._path, exc
                )
        return {}

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Could not save state to %s: %s", self._path, exc)

    # ── Step tracking ──────────────────────────────────────────────────

    def is_step_done(self, ticket_key: str, step: int) -> bool:
        return step in self._state.get(ticket_key, {}).get("steps_done", [])

    def mark_step_done(self, ticket_key: str, step: int) -> None:
        entry = self._state.setdefault(
            ticket_key,
            {
                "started_at": datetime.now(tz=timezone.utc).isoformat(),
                "steps_done": [],
                "tenant": None,
                "completed": False,
                "completed_at": None,
            },
        )
        if step not in entry["steps_done"]:
            entry["steps_done"].append(step)
        self._save()
        logger.debug("State: %s step %d marked done", ticket_key, step)

    # ── Tenant caching ─────────────────────────────────────────────────

    def save_tenant(self, ticket_key: str, tenant: TenantRecord) -> None:
        self._state.setdefault(ticket_key, {})["tenant"] = {
            "id": tenant.id,
            "subscriberid": tenant.subscriberid,
            "name": tenant.name,
        }
        self._save()

    def get_tenant(self, ticket_key: str) -> TenantRecord | None:
        raw = self._state.get(ticket_key, {}).get("tenant")
        if not raw:
            return None
        return TenantRecord(id=raw["id"], subscriberid=raw["subscriberid"], name=raw["name"])

    # ── Completion ─────────────────────────────────────────────────────

    def is_completed(self, ticket_key: str) -> bool:
        return self._state.get(ticket_key, {}).get("completed", False)

    def mark_completed(self, ticket_key: str) -> None:
        entry = self._state.setdefault(ticket_key, {})
        entry["completed"] = True
        entry["completed_at"] = datetime.now(tz=timezone.utc).isoformat()
        self._save()
        logger.debug("State: %s marked completed", ticket_key)

    # ── Monitoring user ────────────────────────────────────────────────

    def save_monitoring_user(self, ticket_key: str, email: str, password: str) -> None:
        """Persist the generated monitoring user credentials (plaintext stored locally)."""
        self._state.setdefault(ticket_key, {})["monitoring_user"] = {
            "email": email,
            "password": password,
        }
        self._save()

    def get_monitoring_user(self, ticket_key: str) -> tuple[str, str] | None:
        """Return (email, plaintext_password) if previously saved, else None."""
        raw = self._state.get(ticket_key, {}).get("monitoring_user")
        if not raw:
            return None
        return raw["email"], raw["password"]

    # ── Info ───────────────────────────────────────────────────────────

    def get_steps_done(self, ticket_key: str) -> list[int]:
        return sorted(self._state.get(ticket_key, {}).get("steps_done", []))

    def started_at(self, ticket_key: str) -> str | None:
        return self._state.get(ticket_key, {}).get("started_at")
