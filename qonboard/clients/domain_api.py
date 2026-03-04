"""
Domain management API client.

Two calls per environment:
  1. Login as the monitoring user  → POST /bff/auth/auth/login
  2. Add the customer org domain   → POST /bff/browser-extension/onboarding/org-domains/add

The monitoring user's plaintext password (generated in step 3) is used for login.
The JWT returned is short-lived and only needed for the add-domain call.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)


def login(domain: str, email: str, password: str, timeout: int = 30) -> str:
    """Login with credentials and return the JWT access token.

    Raises requests.HTTPError on non-2xx.
    Raises KeyError if the response does not contain a token.
    """
    url = f"https://{domain}/bff/auth/auth/login"
    resp = requests.post(
        url,
        json={"email": email, "password": password},
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("token") or (data.get("data") or {}).get("token")
    if not token:
        raise KeyError(f"No token in login response from {url}: {list(data.keys())}")
    logger.debug("Login OK for %s at %s", email, domain)
    return token


def add_org_domain(
    domain: str,
    token: str,
    tenant_id: str,
    org_domain: str,
    timeout: int = 30,
) -> dict:
    """Register an org domain for the tenant.

    Raises requests.HTTPError on non-2xx.
    """
    url = f"https://{domain}/bff/browser-extension/onboarding/org-domains/add"
    resp = requests.post(
        url,
        json={"domain": org_domain},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "tenant": tenant_id,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    try:
        result = resp.json()
    except Exception:  # noqa: BLE001
        result = {"raw": resp.text}
    logger.debug("Add domain %s -> %s: %s", org_domain, domain, resp.status_code)
    return result
