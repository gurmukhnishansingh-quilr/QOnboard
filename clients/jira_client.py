"""
Jira client — fetches Customer Onboard tickets using Jira REST API v3.

API v3 is required because Atlassian removed the /rest/api/2/search endpoint.
The side-effect is that the description field is returned as Atlassian Document
Format (ADF) — a JSON tree — instead of plain text. _adf_to_text() flattens it
before passing to the LLM extractor.

environment is read from a custom field; configure its ID via:
    JIRA_FIELD_ENVIRONMENT  (default: customfield_10103)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from jira import JIRA, Issue

from config import Config
from clients.extractor import extract_customer_details, AzureOpenAIConfig, ExtractedDetails

logger = logging.getLogger(__name__)


# ── ADF helpers ────────────────────────────────────────────────────────────────

# Node types that should be separated by a newline rather than a space
_BLOCK_NODES = {
    "paragraph", "heading", "bulletList", "orderedList",
    "listItem", "blockquote", "codeBlock", "rule", "panel",
}


def _normalize_adf(obj):
    """Recursively convert python-jira PropertyHolder objects to plain dicts.

    python-jira wraps every ADF JSON object as a PropertyHolder rather than
    leaving them as plain dicts, so isinstance(node, dict) checks fail without
    this normalisation step.
    """
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    if isinstance(obj, list):
        return [_normalize_adf(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _normalize_adf(v) for k, v in obj.items()}
    # PropertyHolder and any other jira wrapper — convert via __dict__
    if hasattr(obj, "__dict__"):
        return {k: _normalize_adf(v) for k, v in vars(obj).items()}
    return str(obj)


def _adf_to_text(node) -> str:
    """Recursively flatten a normalised ADF node (plain dict) to plain text."""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    node_type = node.get("type", "")
    if node_type == "text":
        return node.get("text", "")
    if node_type == "hardBreak":
        return "\n"
    parts = [_adf_to_text(child) for child in node.get("content", [])]
    parts = [p for p in parts if p]
    sep = "\n" if node_type in _BLOCK_NODES else " "
    return sep.join(parts)


# ── ADF writer ─────────────────────────────────────────────────────────────────

def _text_to_adf(text: str) -> dict:
    """Convert a plain-text comment (with optional {code} blocks) to ADF.

    Jira Cloud API v3 rejects raw-string comment bodies — they must be ADF.
    Paragraphs are separated by blank lines; {code}...{code} becomes a
    codeBlock node.
    """
    content: list[dict] = []
    parts = re.split(r"\{code\}", text)

    for i, part in enumerate(parts):
        if i % 2 == 1:
            # Inside a {code} block → codeBlock node
            stripped = part.strip()
            if stripped:
                content.append({
                    "type": "codeBlock",
                    "attrs": {},
                    "content": [{"type": "text", "text": stripped}],
                })
        else:
            # Plain text — split on blank lines into paragraphs
            for para in re.split(r"\n{2,}", part):
                para = para.strip()
                if para:
                    content.append({
                        "type": "paragraph",
                        "content": [{"type": "text", "text": para}],
                    })

    if not content:
        content.append({"type": "paragraph", "content": [{"type": "text", "text": text}]})

    return {"type": "doc", "version": 1, "content": content}


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class OnboardTicket:
    key: str
    summary: str
    environment: str
    users: list[ExtractedDetails]   # one entry per user in the ticket description


# ── Client ─────────────────────────────────────────────────────────────────────

class JiraClient:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._az_cfg = AzureOpenAIConfig(
            api_key=cfg.azure_openai_api_key,
            endpoint=cfg.azure_openai_endpoint,
            deployment=cfg.azure_openai_deployment,
            api_version=cfg.azure_openai_api_version,
        )
        self._jira = JIRA(
            server=cfg.jira_url,
            basic_auth=(cfg.jira_username, cfg.jira_api_token),
            options={"rest_api_version": "3"},
        )
        logger.info("Connected to Jira: %s (API v3)", cfg.jira_url)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_pending_tickets(self) -> list[OnboardTicket]:
        """Return one OnboardTicket per open Jira issue (each may have multiple users)."""
        jql = (
            f'issuetype = "{self._cfg.jira_issue_type}" '
            f'AND status in ("{self._cfg.jira_pending_status}") '
            f'ORDER BY created ASC'
        )
        logger.debug("JQL: %s", jql)
        issues = self._jira.search_issues(jql, maxResults=False)
        tickets: list[OnboardTicket] = []
        for issue in issues:
            ticket = self._parse_issue(issue)
            if ticket:
                tickets.append(ticket)
        logger.info("Found %d ticket(s)", len(tickets))
        return tickets

    def fetch_ticket(self, ticket_key: str) -> OnboardTicket | None:
        """Fetch and parse a single ticket by key."""
        issue = self._jira.issue(ticket_key)
        return self._parse_issue(issue)

    def mark_in_progress(self, ticket_key: str) -> None:
        """Transition ticket to In Progress (best-effort)."""
        self._transition(ticket_key, self._cfg.jira_in_progress_status)

    def mark_done(self, ticket_key: str) -> None:
        """Transition ticket to Done (best-effort)."""
        self._transition(ticket_key, self._cfg.jira_done_status)

    def add_comment(self, ticket_key: str, body: str) -> None:
        """Post a comment in ADF format (required by Jira Cloud API v3)."""
        url = f"{self._cfg.jira_url}/rest/api/3/issue/{ticket_key}/comment"
        r = self._jira._session.post(url, json={"body": _text_to_adf(body)})
        r.raise_for_status()
        logger.debug("Comment added to %s", ticket_key)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _parse_issue(self, issue: Issue) -> OnboardTicket | None:
        """Parse one Jira issue into an OnboardTicket with all its users."""
        fields = issue.fields

        # ── Environment — custom Jira field ───────────────────────────
        env_raw = getattr(fields, self._cfg.jira_field_environment, None)
        if env_raw is None:
            logger.warning("Ticket %s skipped — environment field is missing", issue.key)
            return None
        environment = str(env_raw.value if hasattr(env_raw, "value") else env_raw).strip()
        if not environment:
            logger.warning("Ticket %s skipped — environment field is empty", issue.key)
            return None

        # ── Description — ADF (API v3) or plain string (API v2) ───────
        raw_desc = getattr(fields, "description", None)
        if raw_desc is None:
            logger.warning("Ticket %s skipped — description is missing", issue.key)
            return None

        # Normalise PropertyHolder → plain dict, then flatten ADF → plain text
        description = _adf_to_text(_normalize_adf(raw_desc)) if not isinstance(raw_desc, str) else raw_desc
        description = description.strip()
        if not description:
            logger.warning("Ticket %s skipped — description is blank after ADF conversion", issue.key)
            return None

        logger.debug("Ticket %s description (plain text):\n%s", issue.key, description)

        # ── Extract all users via LLM ──────────────────────────────────
        try:
            users = extract_customer_details(description, self._az_cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ticket %s skipped — LLM extraction failed: %s", issue.key, exc)
            return None

        logger.info("Ticket %s — %d user(s) extracted", issue.key, len(users))
        return OnboardTicket(
            key=issue.key,
            summary=getattr(fields, "summary", ""),
            environment=environment,
            users=users,
        )

    def _transition(self, ticket_key: str, status_name: str) -> None:
        if not status_name:
            return
        try:
            transitions = self._jira.transitions(ticket_key)
            for t in transitions:
                if t["name"].lower() == status_name.lower():
                    self._jira.transition_issue(ticket_key, t["id"])
                    logger.info("Transitioned %s → %s", ticket_key, status_name)
                    return
            logger.warning(
                "No transition named '%s' found for %s", status_name, ticket_key
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not transition %s to '%s': %s", ticket_key, status_name, exc
            )
