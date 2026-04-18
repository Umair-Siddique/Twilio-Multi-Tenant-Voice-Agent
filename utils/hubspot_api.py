"""
HubSpot CRM API helpers: token normalization and regional base URLs.

Private app tokens are scoped to a data center (shard). The official Python client
defaults to https://api.hubapi.com; EU/APAC tokens must use the matching host
or HubSpot returns 401 INVALID_AUTHENTICATION / "credentials not found".
"""

import re

# Shard after "pat-" in tokens like pat-eu1-xxxx → regional CRM API base.
# See: https://developers.hubspot.com/docs/api/working-with-oauth (multi-region)
_HUBSPOT_BASE_URL_BY_SHARD: dict[str, str] = {
    "na1": "https://api.hubapi.com",
    "na2": "https://api.hubapi.com",
    "na3": "https://api.hubapi.com",
    "eu1": "https://api-eu1.hubapi.com",
    "eu2": "https://api-eu2.hubapi.com",
    "eu3": "https://api-eu3.hubapi.com",
    "ap1": "https://api-ap1.hubapi.com",
    "ap2": "https://api-ap2.hubapi.com",
}


def normalize_hubspot_access_token(raw: str) -> str:
    """Strip whitespace, surrounding quotes, and optional 'Bearer ' prefix."""
    t = (raw or "").strip()
    if (t.startswith('"') and t.endswith('"')) or (t.startswith("'") and t.endswith("'")):
        t = t[1:-1].strip()
    lower = t.lower()
    if lower.startswith("bearer "):
        t = t[7:].strip()
    # Strip BOM / zero-width spaces sometimes copied from browsers
    t = t.replace("\ufeff", "").replace("\u200b", "").strip()
    return t


def hubspot_base_url_for_access_token(access_token: str) -> str:
    """
    Return the CRM API base URL for this token.
    OAuth JWTs and unknown shapes default to the US primary host.
    """
    t = normalize_hubspot_access_token(access_token)
    m = re.match(r"^pat-([a-z0-9]+)-", t, flags=re.IGNORECASE)
    if not m:
        return "https://api.hubapi.com"
    shard = m.group(1).lower()
    return _HUBSPOT_BASE_URL_BY_SHARD.get(shard, "https://api.hubapi.com")


def hubspot_client_kwargs(access_token: str) -> dict:
    """
    Keyword args for hubspot.HubSpot(...) for this access token.

    The generated OpenAPI clients use Configuration.host (not base_url).
    Passing base_url is ignored, so EU/regional tokens would always hit the US host.
    """
    token = normalize_hubspot_access_token(access_token)
    return {
        "access_token": token,
        "host": hubspot_base_url_for_access_token(token),
    }
