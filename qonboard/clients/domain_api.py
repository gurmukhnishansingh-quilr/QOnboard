"""
Domain management API client.

Two calls per environment, in two separate steps of the agent flow:

  Step 3 — bootstrap the monitoring user
    POST /bff/identity/server/platform-admin/bootstrap/tenant
    Replaces the previous DB INSERT into public."user".

  Step 6 — login as the monitoring user, then add the customer org domain
    POST /bff/auth/auth/login
    POST /bff/browser-extension/onboarding/org-domains/add
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)

_PLATFORM_ADMIN_API_KEY = "oPsGSlLwFKHfzwvAgvhNRnD1DKsSg8z8"


def bootstrap_tenant_user(
    domain: str,
    email: str,
    password: str,
    subscriber_id: str,
    tenant_id: str,
    timeout: int = 30,
) -> dict:
    """Create (or upsert) a credentials-type platform-admin user for the tenant.

    Used in step 3 in place of a direct DB INSERT into public."user".
    """
    url = f"https://{domain}/bff/identity/server/platform-admin/bootstrap/tenant"
    resp = requests.post(
        url,
        json={
            "email": email,
            "password": password,
            "subscriberId": subscriber_id,
            "tenantId": tenant_id,
        },
        headers={
            "Content-Type": "application/json",
            "User-Agent": _CHROME_UA,
            "x-api-key": _PLATFORM_ADMIN_API_KEY,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    try:
        result = resp.json()
    except Exception:  # noqa: BLE001
        result = {"raw": resp.text}
    logger.debug("Bootstrap tenant user %s on %s -> %s", email, domain, resp.status_code)
    return result


def login(domain: str, email: str, password: str, timeout: int = 30) -> str:
    """Login with credentials and return the JWT access token.

    Raises requests.HTTPError on non-2xx.
    Raises KeyError if the response does not contain a token.
    """
    url = f"https://{domain}/bff/auth/auth/login"
    resp = requests.post(
        url,
        json={"email": email, "password": password},
        headers={"Content-Type": "application/json", "User-Agent": _CHROME_UA},
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
            "User-Agent": _CHROME_UA,
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
