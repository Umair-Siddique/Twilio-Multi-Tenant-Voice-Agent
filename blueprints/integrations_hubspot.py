"""
Tenant-scoped HubSpot integration: store Private App (or OAuth) access token per tenant.
"""

from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, current_app, jsonify, request

from utils.auth_utils import require_role, require_tenant
from utils.credential_encryption import encrypt_json
from utils.hubspot_api import (
    hubspot_base_url_for_access_token,
    hubspot_client_kwargs,
    normalize_hubspot_access_token,
)

hubspot_integration_bp = Blueprint("hubspot_integration", __name__)


def handle_errors(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as exc:
            print(f"Error in {f.__name__}: {exc}")
            return jsonify({"error": str(exc)}), 500

    return decorated_function


def _validate_hubspot_access_token(access_token: str) -> None:
    """Lightweight call to confirm the token works for this portal."""
    from hubspot import HubSpot

    token = normalize_hubspot_access_token(access_token)
    client = HubSpot(**hubspot_client_kwargs(token))
    client.crm.owners.owners_api.get_page(limit=1)


def _upsert_hubspot_credentials(tenant_id: str, access_token: str) -> tuple[dict, dict]:
    supabase = current_app.supabase_client
    if not supabase:
        raise ValueError("Supabase not configured")

    token = normalize_hubspot_access_token(access_token)
    integration_lookup = (
        supabase.table("integrations")
        .select("id")
        .eq("tenant_id", tenant_id)
        .eq("type", "hubspot")
        .limit(1)
        .execute()
    )

    integration_payload = {
        "tenant_id": tenant_id,
        "type": "hubspot",
        "name": "HubSpot",
        "status": "connected",
        "connected_at": datetime.now(timezone.utc).isoformat(),
        "error_message": None,
        "config": {
            "auth": "private_app_or_oauth_token",
            "hubspot_api_base_url": hubspot_base_url_for_access_token(token),
        },
    }

    if integration_lookup.data and len(integration_lookup.data) > 0:
        integration_id = integration_lookup.data[0]["id"]
        integration_result = (
            supabase.table("integrations")
            .update(integration_payload)
            .eq("id", integration_id)
            .execute()
        )
        integration_row = integration_result.data[0]
    else:
        integration_result = supabase.table("integrations").insert(integration_payload).execute()
        integration_row = integration_result.data[0]
        integration_id = integration_row["id"]

    encrypted_credentials = encrypt_json({"access_token": token})

    credentials_lookup = (
        supabase.table("integration_credentials")
        .select("id")
        .eq("integration_id", integration_id)
        .limit(1)
        .execute()
    )

    credentials_row_payload = {
        "tenant_id": tenant_id,
        "integration_id": integration_id,
        "encrypted_credentials": encrypted_credentials,
        "access_token_expires_at": None,
        "refresh_token_encrypted": None,
    }

    if credentials_lookup.data and len(credentials_lookup.data) > 0:
        credential_result = (
            supabase.table("integration_credentials")
            .update(credentials_row_payload)
            .eq("id", credentials_lookup.data[0]["id"])
            .execute()
        )
        credential_row = credential_result.data[0]
    else:
        credential_result = (
            supabase.table("integration_credentials").insert(credentials_row_payload).execute()
        )
        credential_row = credential_result.data[0]

    return integration_row, credential_row


@hubspot_integration_bp.route("/connection", methods=["POST"])
@require_role(["owner", "admin"])
@handle_errors
def hubspot_connect(user_id, tenant_id, role):
    """
    Store the tenant's HubSpot Private App access token (or OAuth access token).
    Body: { "access_token": "pat-na1-..." } or { "private_app_access_token": "..." }
    """
    body = request.get_json(silent=True) or {}
    token = normalize_hubspot_access_token(
        body.get("access_token") or body.get("private_app_access_token") or ""
    )
    if not token:
        return jsonify({"error": "access_token or private_app_access_token is required"}), 400

    try:
        _validate_hubspot_access_token(token)
    except Exception as exc:
        return jsonify(
            {
                "error": (
                    f"HubSpot token validation failed: {exc}. "
                    "Use the Private App access token from Settings → Integrations → Private apps "
                    "(not OAuth Client ID/Secret). Ensure the app includes the "
                    "`crm.objects.owners.read` scope. EU accounts use tokens starting with pat-eu1-; "
                    "the server picks the matching regional API host automatically."
                ),
            }
        ), 400

    try:
        integration_row, _ = _upsert_hubspot_credentials(tenant_id=tenant_id, access_token=token)
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 500

    return jsonify({"message": "HubSpot connected successfully", "integration": integration_row}), 200


@hubspot_integration_bp.route("/connection", methods=["GET"])
@require_tenant
@handle_errors
def hubspot_connection_status(user_id, tenant_id, role):
    supabase = current_app.supabase_client
    row = (
        supabase.table("integrations")
        .select("id, type, status, connected_at, last_test_at, error_message, config")
        .eq("tenant_id", tenant_id)
        .eq("type", "hubspot")
        .limit(1)
        .execute()
    )
    integration = row.data[0] if row.data else None

    return jsonify(
        {
            "connected": bool(integration and integration.get("status") == "connected"),
            "integration": integration,
        }
    ), 200


@hubspot_integration_bp.route("/connection", methods=["DELETE"])
@require_role(["owner", "admin"])
@handle_errors
def hubspot_disconnect(user_id, tenant_id, role):
    supabase = current_app.supabase_client
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500

    integration_row = (
        supabase.table("integrations")
        .select("id")
        .eq("tenant_id", tenant_id)
        .eq("type", "hubspot")
        .limit(1)
        .execute()
    )
    if not integration_row.data:
        return jsonify({"message": "HubSpot integration already disconnected"}), 200

    integration_id = integration_row.data[0]["id"]
    supabase.table("integration_credentials").delete().eq("integration_id", integration_id).execute()
    supabase.table("integrations").update(
        {
            "status": "disconnected",
            "error_message": None,
            "config": {},
        }
    ).eq("id", integration_id).execute()

    return jsonify({"message": "HubSpot disconnected successfully"}), 200
