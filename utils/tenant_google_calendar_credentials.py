"""
Load and refresh tenant Google Calendar OAuth credentials for server-side use (e.g. voice agent).
"""

import json
from datetime import datetime, timezone
from typing import Any, Optional

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials

from config import Config
from utils.credential_encryption import decrypt_json, encrypt_json, encrypt_text


def _credentials_to_json_dict(creds: Credentials) -> dict[str, Any]:
    scopes = creds.scopes
    if isinstance(scopes, str):
        scopes = [scopes]
    return {
        "access_token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri or "https://oauth2.googleapis.com/token",
        "client_id": creds.client_id or Config.GOOGLE_OAUTH_CLIENT_ID,
        "scopes": list(scopes) if scopes else None,
    }


def load_credentials_json_for_tenant(supabase, tenant_id: str) -> Optional[str]:
    """
    Return a JSON string suitable for google_calendar_tools._build_service, or None if not connected.
    Refreshes the access token when expired and persists the updated blob to Supabase.
    """
    if not supabase or not tenant_id:
        return None

    try:
        integ = (
            supabase.table("integrations")
            .select("id, status")
            .eq("tenant_id", tenant_id)
            .eq("type", "google_calendar")
            .eq("status", "connected")
            .limit(1)
            .execute()
        )
        if not integ.data:
            return None

        row = (
            supabase.table("integration_credentials")
            .select("id, encrypted_credentials, access_token_expires_at")
            .eq("integration_id", integ.data[0]["id"])
            .limit(1)
            .execute()
        )
        if not row.data:
            return None

        cred_row = row.data[0]
        blob = decrypt_json(cred_row["encrypted_credentials"])

        client_id = blob.get("client_id") or Config.GOOGLE_OAUTH_CLIENT_ID
        client_secret = blob.get("client_secret") or Config.GOOGLE_OAUTH_CLIENT_SECRET
        creds = Credentials(
            token=blob.get("access_token"),
            refresh_token=blob.get("refresh_token"),
            token_uri=blob.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=client_id,
            client_secret=client_secret,
            scopes=blob.get("scopes"),
        )

        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest())
            updated = _credentials_to_json_dict(creds)
            row_update = {
                "encrypted_credentials": encrypt_json(updated),
                "access_token_expires_at": creds.expiry.isoformat()
                if creds.expiry
                else datetime.now(timezone.utc).isoformat(),
            }
            if updated.get("refresh_token"):
                row_update["refresh_token_encrypted"] = encrypt_text(updated["refresh_token"])
            supabase.table("integration_credentials").update(row_update).eq("id", cred_row["id"]).execute()
            blob = updated

        return json.dumps(blob)
    except Exception as exc:
        print(f"[GoogleCalendar] Failed to load credentials for tenant {tenant_id}: {exc}")
        return None


def tenant_may_use_calendar_tools(allowed_actions: Any) -> bool:
    """
    If allowed_actions is empty, allow calendar when integration is connected.
    If non-empty, require an explicit calendar-related action.
    """
    if allowed_actions is None:
        return True
    if isinstance(allowed_actions, str):
        try:
            allowed_actions = json.loads(allowed_actions)
        except json.JSONDecodeError:
            return True
    if not isinstance(allowed_actions, list) or len(allowed_actions) == 0:
        return True
    allowed_set = {str(a).strip() for a in allowed_actions if a is not None}
    calendar_keys = {"schedule_meeting", "google_calendar", "create_booking"}
    return bool(allowed_set & calendar_keys)
