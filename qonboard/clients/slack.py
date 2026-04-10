"""
Slack incoming-webhook notification for onboarding completion.

Sends a structured message with tenant details, monitoring credentials,
and user list to the webhook URL configured via SLACK_WEBHOOK_URL.

If the config key is empty the notification is silently skipped.
"""

from __future__ import annotations

import logging

import requests

from ..config_store import ConfigStore

logger = logging.getLogger(__name__)

_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)


def _get_webhook_url() -> str | None:
    url = (ConfigStore.instance().get_global("SLACK_WEBHOOK_URL") or "").strip()
    return url or None


def send_onboarding_complete(
    *,
    ticket_key: str,
    ticket_summary: str,
    env_name: str,
    tenant_id: str,
    subscriber_id: str,
    monitor_email: str,
    monitor_password: str,
    users: list[dict],
    timeout: int = 15,
) -> None:
    """Post an onboarding-complete message to the configured Slack webhook.

    Does nothing if SLACK_WEBHOOK_URL is not set.
    """
    webhook_url = _get_webhook_url()
    if not webhook_url:
        logger.debug("SLACK_WEBHOOK_URL not configured — skipping notification")
        return

    user_lines = "\n".join(
        f"  - {u['firstname']} {u['lastname']}  `{u['email']}`"
        for u in users
    )

    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Onboarding Complete - {ticket_key}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Ticket:*\n{ticket_key} - {ticket_summary}"},
                    {"type": "mrkdwn", "text": f"*Environment:*\n{env_name}"},
                    {"type": "mrkdwn", "text": f"*Tenant ID:*\n`{tenant_id}`"},
                    {"type": "mrkdwn", "text": f"*Subscriber ID:*\n`{subscriber_id}`"},
                ],
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Monitor Email:*\n`{monitor_email}`"},
                    {"type": "mrkdwn", "text": f"*Monitor Password:*\n`{monitor_password}`"},
                ],
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Users onboarded:*\n{user_lines}",
                },
            },
        ],
    }

    try:
        resp = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json", "User-Agent": _CHROME_UA},
            timeout=timeout,
        )
        resp.raise_for_status()
        logger.info("[green]OK[/] Slack notification sent for %s", ticket_key)
    except requests.RequestException as exc:
        logger.warning(
            "[yellow]![/] Failed to send Slack notification: %s", exc
        )
