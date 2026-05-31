"""
Calls the BFF onboard endpoint for the correct environment domain.
"""

from __future__ import annotations

import logging

import requests

from ..config import Config
from .extractor import ExtractedDetails

logger = logging.getLogger(__name__)

_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)

# Same platform-admin key used by domain_api.bootstrap_tenant_user.
_PLATFORM_ADMIN_API_KEY = "oPsGSlLwFKHfzwvAgvhNRnD1DKsSg8z8"

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

    Uses session-cookie auth (connect.sid + sess_map) via cfg.onboard_session_cookie.

    Raises requests.HTTPError on a non-2xx response.
    """
    url = f"https://{domain}/bff/identity/auth/onboard"
    payload = {
        "email": user.email,
        "firstname": user.firstname,
        "lastname": user.lastname,
        "vendor": cfg.onboard_vendor,
    }

    headers = {
        "Content-Type": "application/json",
        "User-Agent": _CHROME_UA,
        "x-api-key": _PLATFORM_ADMIN_API_KEY,
    }
    if cfg.onboard_session_cookie:
        headers["Cookie"] = cfg.onboard_session_cookie

    logger.debug("Onboard API payload for %s: %s", user.email, payload)

    response = requests.post(
        url,
        json=payload,
        headers=headers,
        timeout=cfg.api_timeout_seconds,
    )
    response.raise_for_status()

    try:
        result = response.json()
    except Exception:  # noqa: BLE001
        result = {"raw": response.text}

    logger.info("Onboard API %s → %s", user.email, response.status_code)
    return result
