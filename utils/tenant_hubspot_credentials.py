"""
Load tenant HubSpot credentials (Private App or OAuth access token) for server-side use.
"""

import json
from typing import Any, Optional

from utils.credential_encryption import decrypt_json
from utils.hubspot_api import normalize_hubspot_access_token


def load_hubspot_access_token_for_tenant(supabase, tenant_id: str) -> Optional[str]:
    """
    Return the decrypted HubSpot access token for the tenant, or None if not connected.
    """
    if not supabase or not tenant_id:
        return None
    try:
        integ = (
            supabase.table("integrations")
            .select("id, status")
            .eq("tenant_id", tenant_id)
            .eq("type", "hubspot")
            .eq("status", "connected")
            .limit(1)
            .execute()
        )
        if not integ.data:
            return None
        row = (
            supabase.table("integration_credentials")
            .select("encrypted_credentials")
            .eq("integration_id", integ.data[0]["id"])
            .limit(1)
            .execute()
        )
        if not row.data:
            return None
        blob = decrypt_json(row.data[0]["encrypted_credentials"])
        token = normalize_hubspot_access_token(str(blob.get("access_token") or ""))
        if not token:
            return None
        return token
    except Exception as exc:
        print(f"[HubSpot] Failed to load token for tenant {tenant_id}: {exc}")
        return None


def tenant_may_use_hubspot_tools(allowed_actions: Any) -> bool:
    """
    If allowed_actions is empty, allow HubSpot when integration is connected.
    If non-empty, require an explicit CRM / HubSpot-related action.
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
    hubspot_keys = {"hubspot", "crm", "create_lead", "create_ticket"}
    return bool(allowed_set & hubspot_keys)
