"""
Calls the BFF onboard endpoint for the correct environment domain.
"""

from __future__ import annotations

import logging

import requests

from ..config import Config
from .extractor import ExtractedDetails

logger = logging.getLogger(__name__)

# Maps the Jira environment field value → base domain
ENV_DOMAIN_MAP: dict[str, str] = {
    "UAE POC":  "trust.quilr.ai",
    "UAE PROD": "trust.quilr.ai",        # shares domain with UAE POC for now
    "IND POC":  "platform.quilr.ai",
    "IND PROD": "platform.quilrai.com",
    "USA POC":  "app.quilr.ai",
    "USA PROD": "app.quilrai.com",
}


def resolve_domain(environment: str) -> str:
    """Return the base domain for a given environment name.

    Raises ValueError if the environment is unknown or not yet available.
    """
    key = environment.strip()
    if key not in ENV_DOMAIN_MAP:
        raise ValueError(
            f"Unknown environment '{key}'. "
            f"Valid values: {list(ENV_DOMAIN_MAP.keys())}"
        )
    domain = ENV_DOMAIN_MAP[key]
    if domain is None:
        raise ValueError(
            f"Environment '{key}' is not available yet — skipping."
        )
    return domain


def call_onboard_api_for_user(user: ExtractedDetails, domain: str, cfg: Config) -> dict:
    """POST one user to the onboard endpoint and return the parsed JSON response.

    Raises requests.HTTPError on a non-2xx response.
    """
    url = f"https://{domain}/bff/auth/auth/onboard"
    payload = {
        "email": user.email,
        "firstname": user.firstname,
        "lastname": user.lastname,
        "vendor": cfg.onboard_vendor,
    }

    logger.debug("Onboard API payload for %s: %s", user.email, payload)

    response = requests.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=cfg.api_timeout_seconds,
    )
    response.raise_for_status()

    try:
        result = response.json()
    except Exception:  # noqa: BLE001
        result = {"raw": response.text}

    logger.info("Onboard API %s → %s", user.email, response.status_code)
    return result
