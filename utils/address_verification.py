"""
Business-address verification and region locking.

Two independent gates are applied to every tenant address:

1. REGION LOCK (always enforced, no external dependency)
   Accounts are limited to North America and Europe — the regions where
   postal data is reliable enough to verify an address against. The country
   allowlist lives in SUPPORTED_COUNTRIES below.

2. ADDRESS VERIFICATION (requires the Google Address Validation API)
   The submitted address is validated against Google's postal data. An
   address is accepted only when it resolves to a real building-level
   premise. Street-only, city-only or unresolvable addresses are rejected.

Failure behaviour is controlled by Config.ADDRESS_VERIFICATION_STRICT:
    strict=True  (default) — an API outage/misconfiguration blocks the
                  signup, so unverified addresses can never slip through.
    strict=False — the signup proceeds and the tenant is stored with
                  status "unverified" for later review.

Docs: https://developers.google.com/maps/documentation/address-validation
"""

import requests

from config import Config


GOOGLE_ADDRESS_VALIDATION_URL = (
    "https://addressvalidation.googleapis.com/v1:validateAddress"
)

# ── Region lock ───────────────────────────────────────────────────────────────
# ISO 3166-1 alpha-2 → display name. Only these countries may open an account.
# Restricted to North America + Europe, where Google's Address Validation API
# has the postal coverage needed to actually confirm a street address.

NORTH_AMERICA_COUNTRIES = {
    "US": "United States",
    "CA": "Canada",
    "MX": "Mexico",
    "PR": "Puerto Rico",
}

EUROPE_COUNTRIES = {
    "AT": "Austria",
    "BE": "Belgium",
    "BG": "Bulgaria",
    "CH": "Switzerland",
    "CY": "Cyprus",
    "CZ": "Czechia",
    "DE": "Germany",
    "DK": "Denmark",
    "EE": "Estonia",
    "ES": "Spain",
    "FI": "Finland",
    "FR": "France",
    "GB": "United Kingdom",
    "GR": "Greece",
    "HR": "Croatia",
    "HU": "Hungary",
    "IE": "Ireland",
    "IS": "Iceland",
    "IT": "Italy",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "LV": "Latvia",
    "MT": "Malta",
    "NL": "Netherlands",
    "NO": "Norway",
    "PL": "Poland",
    "PT": "Portugal",
    "RO": "Romania",
    "SE": "Sweden",
    "SI": "Slovenia",
    "SK": "Slovakia",
}

SUPPORTED_COUNTRIES = {**NORTH_AMERICA_COUNTRIES, **EUROPE_COUNTRIES}

COUNTRY_REGIONS = {
    **{code: "North America" for code in NORTH_AMERICA_COUNTRIES},
    **{code: "Europe" for code in EUROPE_COUNTRIES},
}

# USPS CASS certification is only available for these regions.
_USPS_CASS_REGIONS = {"US", "PR"}

# Google returns the precision it was able to validate the address to.
# Anything coarser than a building means we cannot confirm the address exists.
_ACCEPTED_GRANULARITIES = {"SUB_PREMISE", "PREMISE", "PREMISE_PROXIMITY"}


def supported_countries():
    """Return the allowlist as a sorted list of {code, name, region} dicts.

    Used by the signup form to populate its country dropdown, so the UI can
    never offer a country the backend would reject.
    """
    return sorted(
        (
            {"code": code, "name": name, "region": COUNTRY_REGIONS[code]}
            for code, name in SUPPORTED_COUNTRIES.items()
        ),
        key=lambda c: c["name"],
    )


def normalize_country(value):
    """Coerce arbitrary input to an uppercase ISO 3166-1 alpha-2 code."""
    return str(value or "").strip().upper()[:2]


def is_supported_country(country_code):
    """True when the country is inside the North America / Europe region lock."""
    return normalize_country(country_code) in SUPPORTED_COUNTRIES


def region_lock_error(country_code):
    """Return an (error_body, status) tuple when a country is out of region.

    Returns None when the country is allowed, so callers can do:
        err = region_lock_error(country)
        if err:
            return jsonify(err[0]), err[1]
    """
    code = normalize_country(country_code)
    if not code:
        return (
            {
                "error": "Country is required",
                "message": "Select the country where your business is registered.",
            },
            400,
        )
    if code not in SUPPORTED_COUNTRIES:
        return (
            {
                "error": "Region not supported",
                "message": (
                    "Accounts are currently available only in North America and "
                    "Europe, where we can verify a business address. Contact "
                    "support if you operate in another region."
                ),
                "country": code,
                "supported_countries": supported_countries(),
            },
            403,
        )
    return None


def has_address_input(data):
    """True when a payload carries any address field at all."""
    return any(
        str(data.get(key) or "").strip()
        for key in ("address_line1", "address_line2", "city", "state", "postal_code")
    )


def address_columns(verification, address, country_code):
    """Map a verify_address() outcome onto the tenants table columns.

    Shared by every write path (signup, tenant profile update, admin-created
    tenants) so the stored shape is identical everywhere.

    `address_verified` is True only for a genuinely verified address — a
    "skipped" check (verification disabled) is allowed through but is not
    recorded as verified.
    """
    from datetime import datetime, timezone as _timezone

    verified = verification.get("status") == "verified"
    columns = {
        "address_line1": address.get("address_line1"),
        "address_line2": address.get("address_line2"),
        "city": address.get("city"),
        "state": address.get("state"),
        "postal_code": address.get("postal_code"),
        "country": normalize_country(country_code),
        "address_formatted": verification.get("formatted_address"),
        "address_verified": verified,
        "address_verification_status": verification.get("status"),
        "address_verified_at": (
            datetime.now(_timezone.utc).isoformat() if verified else None
        ),
    }
    # Prefer Google's normalized components over the raw user input.
    if verification.get("normalized"):
        columns.update(verification["normalized"])
    return columns


def _build_payload(address, country_code):
    """Map our address fields onto Google's PostalAddress shape."""
    address_lines = [
        line
        for line in (
            str(address.get("address_line1") or "").strip(),
            str(address.get("address_line2") or "").strip(),
        )
        if line
    ]

    postal_address = {
        "regionCode": country_code,
        "addressLines": address_lines,
    }

    locality = str(address.get("city") or "").strip()
    admin_area = str(address.get("state") or "").strip()
    postal_code = str(address.get("postal_code") or "").strip()

    if locality:
        postal_address["locality"] = locality
    if admin_area:
        postal_address["administrativeArea"] = admin_area
    if postal_code:
        postal_address["postalCode"] = postal_code

    payload = {"address": postal_address}

    # CASS is off by default and only valid for US/PR. It upgrades the check
    # to USPS-certified deliverability rather than plain postal matching.
    if Config.ADDRESS_ENABLE_USPS_CASS and country_code in _USPS_CASS_REGIONS:
        payload["enableUspsCass"] = True

    return payload


def _interpret(result):
    """Turn a Google validation result into (accepted, reason).

    Rejects anything that is not confirmed down to a real building, plus
    addresses carrying components Google could not resolve or flagged as
    suspicious.
    """
    verdict = result.get("verdict") or {}
    address = result.get("address") or {}

    granularity = verdict.get("validationGranularity", "GRANULARITY_UNSPECIFIED")
    if granularity not in _ACCEPTED_GRANULARITIES:
        return False, (
            "We could not confirm this as a complete street address. Include the "
            "building or street number and try again."
        )

    if not verdict.get("addressComplete", False):
        missing = address.get("missingComponentTypes") or []
        if missing:
            readable = ", ".join(part.replace("_", " ") for part in missing)
            return False, f"This address is incomplete — missing: {readable}."
        return False, "This address appears to be incomplete."

    unresolved = address.get("unresolvedTokens") or []
    if unresolved:
        return False, (
            "Part of this address could not be recognised: "
            f"{', '.join(unresolved)}."
        )

    for component in address.get("addressComponents") or []:
        if component.get("confirmationLevel") == "UNCONFIRMED_AND_SUSPICIOUS":
            return False, (
                "One or more parts of this address could not be verified. "
                "Please check it and try again."
            )

    return True, None


def _outcome(status, verified, message=None, **extra):
    """Uniform result shape returned to callers."""
    payload = {
        "status": status,          # verified | rejected | unverified | skipped
        "verified": verified,
        "message": message,
        "formatted_address": None,
        "normalized": None,
    }
    payload.update(extra)
    return payload


def verify_address(address, country_code):
    """Validate a business address against the Google Address Validation API.

    `address` is a dict with address_line1 / address_line2 / city / state /
    postal_code. `country_code` must already have passed the region lock.

    Returns a dict:
        status              verified | rejected | unverified | skipped
        verified            bool — True only for status "verified"
        message             human-readable reason when not verified
        formatted_address   Google's normalized single-line address
        normalized          dict of normalized components to persist

    "unverified" means the check could not run (outage/misconfiguration) and
    Config.ADDRESS_VERIFICATION_STRICT is False, so the caller may let the
    account through and flag it for review.
    """
    country_code = normalize_country(country_code)

    if not Config.ADDRESS_VERIFICATION_ENABLED:
        return _outcome(
            "skipped",
            True,
            "Address verification is disabled in this environment.",
        )

    if not str(address.get("address_line1") or "").strip():
        return _outcome("rejected", False, "Street address is required.")
    if not str(address.get("city") or "").strip():
        return _outcome("rejected", False, "City is required.")

    api_key = Config.GOOGLE_ADDRESS_VALIDATION_API_KEY
    if not api_key:
        # Not configured is a setup gap, not an outage: don't block signups /
        # profile saves over it. The address is stored as "unverified".
        print(
            "[WARN] GOOGLE_ADDRESS_VALIDATION_API_KEY is not set — address "
            "verification cannot run; saving the address as unverified."
        )
        return _outcome(
            "unverified",
            False,
            "Address could not be verified automatically.",
            unavailable=True,
        )

    try:
        response = requests.post(
            GOOGLE_ADDRESS_VALIDATION_URL,
            params={"key": api_key},
            json=_build_payload(address, country_code),
            timeout=Config.ADDRESS_VERIFICATION_TIMEOUT_SECONDS,
        )
    except Exception as e:
        print(f"[WARN] Address validation request failed: {e}")
        if Config.ADDRESS_VERIFICATION_STRICT:
            return _outcome(
                "rejected",
                False,
                "Address verification is temporarily unavailable. Please try "
                "again shortly.",
                unavailable=True,
            )
        return _outcome(
            "unverified",
            False,
            "Address could not be verified automatically.",
            unavailable=True,
        )

    if response.status_code == 400:
        # Google rejects malformed addresses with 400 — that is a user error,
        # not an outage, so surface it as a rejection regardless of strictness.
        return _outcome(
            "rejected",
            False,
            "This address could not be recognised. Please check it and try again.",
        )

    if response.status_code != 200:
        print(
            f"[WARN] Address validation returned HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )
        if Config.ADDRESS_VERIFICATION_STRICT:
            return _outcome(
                "rejected",
                False,
                "Address verification is temporarily unavailable. Please try "
                "again shortly.",
                unavailable=True,
            )
        return _outcome(
            "unverified",
            False,
            "Address could not be verified automatically.",
            unavailable=True,
        )

    result = (response.json() or {}).get("result") or {}
    accepted, reason = _interpret(result)

    google_address = result.get("address") or {}
    postal = google_address.get("postalAddress") or {}
    formatted = google_address.get("formattedAddress")

    if not accepted:
        return _outcome("rejected", False, reason, formatted_address=formatted)

    # Persist Google's normalized form rather than the raw user input, so the
    # stored address is consistent and postal-correct.
    lines = postal.get("addressLines") or []
    normalized = {
        "address_line1": (lines[0] if len(lines) > 0 else address.get("address_line1")),
        "address_line2": (lines[1] if len(lines) > 1 else address.get("address_line2")),
        "city": postal.get("locality") or address.get("city"),
        "state": postal.get("administrativeArea") or address.get("state"),
        "postal_code": postal.get("postalCode") or address.get("postal_code"),
        "country": normalize_country(postal.get("regionCode") or country_code),
    }

    return _outcome(
        "verified",
        True,
        None,
        formatted_address=formatted,
        normalized=normalized,
    )
