"""
Central configuration loaded from environment variables / .env file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        raise EnvironmentError(
            f"Required environment variable '{key}' is missing or empty. "
            "Copy .env.example → .env and fill in your values."
        )
    return val


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


@dataclass(frozen=True)
class Config:
    # ── Jira ──────────────────────────────────────────────────────────
    jira_url: str = field(default_factory=lambda: _require("JIRA_URL"))
    jira_username: str = field(default_factory=lambda: _require("JIRA_USERNAME"))
    jira_api_token: str = field(default_factory=lambda: _require("JIRA_API_TOKEN"))

    jira_issue_type: str = field(
        default_factory=lambda: _optional("JIRA_ISSUE_TYPE", "Customer Onboard")
    )
    # Status names used to filter and transition issues
    jira_pending_status: str = field(
        default_factory=lambda: _optional("JIRA_PENDING_STATUS", "Open")
    )
    jira_in_progress_status: str = field(
        default_factory=lambda: _optional("JIRA_IN_PROGRESS_STATUS", "In Progress")
    )
    jira_done_status: str = field(
        default_factory=lambda: _optional("JIRA_DONE_STATUS", "Done")
    )

    # Custom field ID for the environment selector on the issue
    jira_field_environment: str = field(
        default_factory=lambda: _optional("JIRA_FIELD_ENVIRONMENT", "customfield_10103")
    )

    # ── LLM (Azure OpenAI) ────────────────────────────────────────────
    # Used to extract firstname / lastname / email from the ticket description
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

    # PostgreSQL and Neo4j are per-environment.
    # See envs/.env.<env>.example for each environment's DB settings.
