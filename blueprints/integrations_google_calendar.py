"""
Tenant-scoped Google Calendar OAuth integration endpoints.
"""

from datetime import datetime, timezone
from functools import wraps
from secrets import SystemRandom
from string import ascii_letters, digits
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from flask import Blueprint, Response, current_app, jsonify, redirect, request, url_for
from google_auth_oauthlib.flow import Flow
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from config import Config
from utils.auth_utils import require_role, require_tenant
from utils.credential_encryption import encrypt_json, encrypt_text

google_calendar_integration_bp = Blueprint("google_calendar_integration", __name__)


def handle_errors(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as exc:
            print(f"Error in {f.__name__}: {exc}")
            return jsonify({"error": str(exc)}), 500

    return decorated_function


def _oauth_scopes() -> list[str]:
    raw_scopes = (Config.GOOGLE_OAUTH_SCOPES or "").strip()
    if not raw_scopes:
        return ["https://www.googleapis.com/auth/calendar"]
    return [scope.strip() for scope in raw_scopes.split(",") if scope.strip()]


def _oauth_client_config() -> dict:
    if not Config.GOOGLE_OAUTH_CLIENT_ID or not Config.GOOGLE_OAUTH_CLIENT_SECRET:
        raise ValueError("Google OAuth client is not configured")

    return {
        "web": {
            "client_id": Config.GOOGLE_OAUTH_CLIENT_ID,
            "project_id": "tenant-google-calendar-integration",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": Config.GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uris": [],
        }
    }


def _callback_url() -> str:
    configured = (Config.GOOGLE_CALENDAR_OAUTH_REDIRECT_URI or "").strip()
    if configured:
        return configured
    return url_for("google_calendar_integration.google_calendar_oauth_callback", _external=True)


def _generate_code_verifier() -> str:
    """RFC 7636 / Google Flow-compatible PKCE verifier (matches google_auth_oauthlib.flow.Flow)."""
    chars = ascii_letters + digits + "-._~"
    rnd = SystemRandom()
    return "".join(rnd.choice(chars) for _ in range(128))


def _make_state(
    tenant_id: str,
    user_id: str,
    code_verifier: str,
    frontend_redirect_url: str | None = None,
) -> str:
    serializer = URLSafeTimedSerializer(current_app.secret_key, salt="google-calendar-oauth")
    payload = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        # PKCE: a new Flow() on the callback must use the same verifier as authorization_url().
        "code_verifier": code_verifier,
        "frontend_redirect_url": frontend_redirect_url,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return serializer.dumps(payload)


def _parse_state(state: str) -> dict:
    serializer = URLSafeTimedSerializer(current_app.secret_key, salt="google-calendar-oauth")
    try:
        return serializer.loads(state, max_age=900)
    except SignatureExpired as exc:
        raise ValueError("OAuth state expired. Please restart connection flow.") from exc
    except BadSignature as exc:
        raise ValueError("Invalid OAuth state") from exc


def _add_query_params(url: str, params: dict) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key, value in params.items():
        query[key] = [value]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _upsert_google_calendar_credentials(tenant_id: str, credentials) -> tuple[dict, dict]:
    supabase = current_app.supabase_client
    if not supabase:
        raise ValueError("Supabase not configured")

    integration_lookup = (
        supabase.table("integrations")
        .select("id")
        .eq("tenant_id", tenant_id)
        .eq("type", "google_calendar")
        .limit(1)
        .execute()
    )

    integration_payload = {
        "tenant_id": tenant_id,
        "type": "google_calendar",
        "name": "Google Calendar",
        "status": "connected",
        "connected_at": datetime.now(timezone.utc).isoformat(),
        "error_message": None,
        "config": {"scopes": credentials.scopes or _oauth_scopes()},
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

    credentials_payload = {
        "access_token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        # Keep client_secret out of tenant records. It remains server-managed in env.
        "client_id": credentials.client_id or Config.GOOGLE_OAUTH_CLIENT_ID,
        "scopes": credentials.scopes or _oauth_scopes(),
    }
    encrypted_credentials = encrypt_json(credentials_payload)
    encrypted_refresh_token = (
        encrypt_text(credentials.refresh_token) if credentials.refresh_token else None
    )
    expiry = credentials.expiry.isoformat() if credentials.expiry else None

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
        "access_token_expires_at": expiry,
        "refresh_token_encrypted": encrypted_refresh_token,
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


@google_calendar_integration_bp.route("/oauth/start", methods=["POST"])
@require_role(["owner", "admin"])
@handle_errors
def google_calendar_oauth_start(user_id, tenant_id, role):
    """
    Start OAuth flow for Google Calendar.
    """
    body = request.get_json(silent=True) or {}
    frontend_redirect_url = body.get("frontend_redirect_url")

    callback_url = _callback_url()
    code_verifier = _generate_code_verifier()
    state = _make_state(
        tenant_id=tenant_id,
        user_id=user_id,
        code_verifier=code_verifier,
        frontend_redirect_url=frontend_redirect_url,
    )
    flow = Flow.from_client_config(
        _oauth_client_config(),
        scopes=_oauth_scopes(),
        redirect_uri=callback_url,
        code_verifier=code_verifier,
        autogenerate_code_verifier=False,
    )

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )

    return jsonify(
        {
            "authorization_url": auth_url,
            "callback_url": callback_url,
        }
    ), 200


@google_calendar_integration_bp.route("/oauth/callback", methods=["GET"])
@handle_errors
def google_calendar_oauth_callback():
    """
    OAuth callback endpoint that exchanges code for tokens and stores them.
    """
    error = request.args.get("error")
    state = request.args.get("state")
    frontend_redirect_url = None
    if state:
        try:
            state_payload = _parse_state(state)
            frontend_redirect_url = state_payload.get("frontend_redirect_url")
        except Exception:
            frontend_redirect_url = None

    if error:
        if frontend_redirect_url:
            return redirect(
                _add_query_params(
                    frontend_redirect_url,
                    {
                        "integration": "google_calendar",
                        "status": "error",
                        "reason": error,
                    },
                )
            )
        error_url = (Config.GOOGLE_CALENDAR_CONNECT_ERROR_URL or "").strip()
        if error_url:
            return redirect(
                _add_query_params(
                    error_url,
                    {
                        "integration": "google_calendar",
                        "status": "error",
                        "reason": error,
                    },
                )
            )
        return jsonify({"error": f"Google OAuth failed: {error}"}), 400
    if not state:
        return jsonify({"error": "Missing OAuth state"}), 400

    state_payload = _parse_state(state)
    tenant_id = state_payload.get("tenant_id")
    frontend_redirect_url = state_payload.get("frontend_redirect_url")
    code_verifier = state_payload.get("code_verifier")
    if not tenant_id:
        return jsonify({"error": "Invalid OAuth state payload"}), 400
    if not code_verifier:
        return jsonify(
            {
                "error": "Invalid OAuth state: missing PKCE verifier. Start connect again.",
            }
        ), 400

    flow = Flow.from_client_config(
        _oauth_client_config(),
        scopes=_oauth_scopes(),
        redirect_uri=_callback_url(),
        state=state,
        code_verifier=code_verifier,
        autogenerate_code_verifier=False,
    )
    flow.fetch_token(authorization_response=request.url)
    credentials = flow.credentials

    _upsert_google_calendar_credentials(tenant_id=tenant_id, credentials=credentials)

    if frontend_redirect_url:
        return redirect(
            _add_query_params(
                frontend_redirect_url,
                {
                    "integration": "google_calendar",
                    "status": "connected",
                },
            )
        )

    success_url = (Config.GOOGLE_CALENDAR_CONNECT_SUCCESS_URL or "").strip()
    if success_url:
        return redirect(
            _add_query_params(
                success_url,
                {
                    "integration": "google_calendar",
                    "status": "connected",
                },
            )
        )

    # Browser lands on backend callback URL (e.g. ngrok) — show a real page, not a JSON blob.
    html = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"/>"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>"
        "<title>Google Calendar connected</title></head><body style=\""
        "font-family:system-ui,sans-serif;max-width:36rem;margin:3rem auto;padding:0 1rem;\">"
        "<h1 style=\"font-size:1.35rem;\">Google Calendar connected</h1>"
        "<p>This window was opened by Google after authorization. "
        "You can close it and go back to your admin portal.</p>"
        "</body></html>"
    )
    return Response(html, status=200, mimetype="text/html; charset=utf-8")


@google_calendar_integration_bp.route("/connection", methods=["GET"])
@require_tenant
@handle_errors
def google_calendar_connection_status(user_id, tenant_id, role):
    """
    Return connection status for tenant Google Calendar integration.
    """
    supabase = current_app.supabase_client
    row = (
        supabase.table("integrations")
        .select("id, type, status, connected_at, last_test_at, error_message, config")
        .eq("tenant_id", tenant_id)
        .eq("type", "google_calendar")
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


@google_calendar_integration_bp.route("/connection", methods=["DELETE"])
@require_role(["owner", "admin"])
@handle_errors
def google_calendar_disconnect(user_id, tenant_id, role):
    """
    Disconnect tenant Google Calendar integration and delete stored credentials.
    """
    supabase = current_app.supabase_client
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500

    integration_row = (
        supabase.table("integrations")
        .select("id")
        .eq("tenant_id", tenant_id)
        .eq("type", "google_calendar")
        .limit(1)
        .execute()
    )
    if not integration_row.data:
        return jsonify({"message": "Google Calendar integration already disconnected"}), 200

    integration_id = integration_row.data[0]["id"]
    supabase.table("integration_credentials").delete().eq("integration_id", integration_id).execute()
    supabase.table("integrations").update(
        {
            "status": "disconnected",
            "error_message": None,
            "config": {},
        }
    ).eq("id", integration_id).execute()

    return jsonify({"message": "Google Calendar disconnected successfully"}), 200

