"""
Central configuration — reads from the SQLite config store (.qonboard.db).

On first run the store auto-ingests from .env in cwd.
Use `qonboard config set KEY VALUE` to update values at any time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config_store import ConfigStore


def _require(key: str) -> str:
    val = (ConfigStore.instance().get_global(key) or "").strip()
    if not val:
        raise EnvironmentError(
            f"Required config key '{key}' is missing or empty. "
            "Run 'qonboard config init' to ingest from .env, "
            "or 'qonboard config set KEY VALUE' to set it manually."
        )
    return val


def _optional(key: str, default: str = "") -> str:
    return (ConfigStore.instance().get_global(key) or default).strip()


@dataclass(frozen=True)
class Config:
    # ── Jira ──────────────────────────────────────────────────────────
    jira_url: str = field(default_factory=lambda: _require("JIRA_URL"))
    jira_username: str = field(default_factory=lambda: _require("JIRA_USERNAME"))
    jira_api_token: str = field(default_factory=lambda: _require("JIRA_API_TOKEN"))

    jira_issue_type: str = field(
        default_factory=lambda: _optional("JIRA_ISSUE_TYPE", "Customer Onboard")
    )
    jira_pending_status: str = field(
        default_factory=lambda: _optional("JIRA_PENDING_STATUS", "To Do")
    )
    jira_in_progress_status: str = field(
        default_factory=lambda: _optional("JIRA_IN_PROGRESS_STATUS", "New Tenant")
    )
    jira_done_status: str = field(
        default_factory=lambda: _optional("JIRA_DONE_STATUS", "Tenant Ready")
    )
    jira_field_environment: str = field(
        default_factory=lambda: _optional("JIRA_FIELD_ENVIRONMENT", "customfield_10479")
    )

    # ── LLM (Azure OpenAI) ────────────────────────────────────────────
    azure_openai_api_key: str = field(
        default_factory=lambda: _require("AZURE_OPENAI_API_KEY")
    )
    azure_openai_endpoint: str = field(
        default_factory=lambda: _require("AZURE_OPENAI_ENDPOINT")
    )
    azure_openai_deployment: str = field(
        default_factory=lambda: _require("AZURE_OPENAI_DEPLOYMENT")
    )
    azure_openai_api_version: str = field(
        default_factory=lambda: _optional("AZURE_OPENAI_API_VERSION", "2024-02-01")
    )

    # ── Onboard API ───────────────────────────────────────────────────
    onboard_vendor: str = field(
        default_factory=lambda: _optional("ONBOARD_VENDOR", "microsoft")
    )
    api_timeout_seconds: int = field(
        default_factory=lambda: int(_optional("API_TIMEOUT_SECONDS", "30"))
    )

    # PostgreSQL and Neo4j are per-environment — see env_config.py / config_store.py
