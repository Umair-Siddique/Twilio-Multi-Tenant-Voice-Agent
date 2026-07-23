"""
Super Admin (Product Owner) APIs for tenant management.

Authentication: Bearer token belonging to a user in the `super_admins` table.

Cross-tenant (platform-wide) endpoints
---------------------------------------
GET    /admin/tenants                        List ALL tenants (pagination + filters)
GET    /admin/users                          List ALL users across every tenant
GET    /admin/agent-configs                  List ALL agent configs across every tenant
GET    /admin/twilio/numbers                 List ALL phone numbers across every tenant
GET    /admin/twilio/numbers/unassigned      Numbers in Twilio not linked to any tenant
GET    /admin/monitoring/calls               ALL calls across every tenant
GET    /admin/monitoring/recordings          ALL recordings across every tenant
GET    /admin/monitoring/email-logs          ALL email logs across every tenant
GET    /admin/monitoring/analytics           Platform-wide usage analytics
GET    /admin/monitoring/health              Live health check for every dependency

Per-tenant endpoints (pass tenant_id in path)
----------------------------------------------
POST   /admin/tenants                        Create a new tenant + owner user
GET    /admin/tenants/<id>                   Get single tenant details + agent config
DELETE /admin/tenants/<id>                   Hard-delete a tenant
PATCH  /admin/tenants/<id>/status            Suspend / activate a tenant
GET    /admin/tenants/<id>/usage             Aggregated usage stats for one tenant
GET    /admin/tenants/<id>/users             List users belonging to one tenant
POST   /admin/tenants/<id>/agent-pack        Apply agent pack template to a tenant

Required Supabase table:
    CREATE TABLE super_admins (
        id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id    UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
        email      TEXT NOT NULL,
        created_at TIMESTAMPTZ DEFAULT now()
    );
"""

from flask import Blueprint, request, jsonify, current_app
from functools import wraps
from datetime import datetime, timezone, timedelta
import traceback

from utils.auth_utils import verify_token
from utils.supabase_retry import supabase_call_with_retry
from config import Config

super_admin_bp = Blueprint("super_admin", __name__)


# ── Auth decorator ────────────────────────────────────────────────────────────

def require_super_admin(f):
    """Verify Bearer token and confirm user is in the super_admins table."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Authorization token required"}), 401

        token = auth_header.split(" ", 1)[1]
        user_id, err = verify_token(token)
        if err:
            return jsonify({"error": err}), 401

        supabase = current_app.supabase_client
        if not supabase:
            return jsonify({"error": "Supabase not configured"}), 500

        try:
            row = supabase_call_with_retry(
                lambda: supabase.table("super_admins")
                .select("id")
                .eq("user_id", user_id)
                .execute()
            )
        except Exception as e:
            return jsonify({"error": f"Super-admin check failed: {e}"}), 500

        if not row.data:
            return jsonify({"error": "Forbidden: super-admin access required"}), 403

        kwargs["admin_user_id"] = user_id
        return f(*args, **kwargs)

    return decorated


# ── Error handler ─────────────────────────────────────────────────────────────

def handle_errors(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": str(e)}), 500
    return decorated


# ── Helper ────────────────────────────────────────────────────────────────────

def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── 1. Create tenant ──────────────────────────────────────────────────────────

@super_admin_bp.route("/tenants", methods=["POST"])
@require_super_admin
@handle_errors
def create_tenant(admin_user_id):
    """
    Create a new tenant and an owner user account.

    Body:
    {
        "company_name": "Acme Corp",
        "owner_email": "owner@acme.com",
        "owner_password": "Secure123!",
        "timezone": "America/Toronto",
        "industry": "Healthcare"
    }
    """
    data = request.get_json(silent=True) or {}
    company_name = data.get("company_name", "").strip()
    owner_email  = data.get("owner_email", "").strip()
    owner_password = data.get("owner_password", "")

    if not company_name:
        return jsonify({"error": "company_name is required"}), 400
    if not owner_email:
        return jsonify({"error": "owner_email is required"}), 400
    if not owner_password or len(owner_password) < 6:
        return jsonify({"error": "owner_password must be at least 6 characters"}), 400

    supabase = current_app.supabase_client

    # 1. Create auth user
    try:
        auth_resp = supabase.auth.admin.create_user({
            "email": owner_email,
            "password": owner_password,
            "email_confirm": True,
        })
        if not auth_resp.user:
            return jsonify({"error": "Failed to create owner user"}), 500
        user_id = auth_resp.user.id
    except Exception as e:
        err = str(e).lower()
        if "already registered" in err or "already exists" in err:
            return jsonify({"error": "Email already registered"}), 400
        raise

    # 2. Create tenant record
    tenant_data = {
        "name": company_name,
        "timezone": data.get("timezone", "America/Toronto"),
        "industry": data.get("industry"),
        "country": str(data.get("country") or "CA").strip().upper()[:2],
        "status": "active",
        "default_email_recipients": [owner_email],
    }
    tenant_resp = supabase_call_with_retry(
        lambda: supabase.table("tenants").insert(tenant_data).execute()
    )
    if not tenant_resp.data:
        return jsonify({"error": "Failed to create tenant"}), 500
    tenant_id = tenant_resp.data[0]["id"]

    # 3. Link owner to tenant
    supabase_call_with_retry(
        lambda: supabase.table("tenant_users").insert({
            "tenant_id": tenant_id,
            "user_id": user_id,
            "role": "owner",
        }).execute()
    )

    # 4. Create default agent config
    try:
        supabase.table("tenant_agent_config").insert({
            "tenant_id": tenant_id,
            "greeting": "Thank you for calling. How may I assist you today?",
            "tone": "professional",
            "business_hours": {},
            "escalation_rules": {},
            "allowed_actions": [],
            "store_transcripts": True,
            "store_recordings": True,
            "retention_days": 90,
        }).execute()
    except Exception:
        pass  # non-critical

    return jsonify({
        "message": "Tenant created successfully",
        "tenant": tenant_resp.data[0],
        "owner": {"user_id": user_id, "email": owner_email},
    }), 201


# ── 2. List tenants ───────────────────────────────────────────────────────────

@super_admin_bp.route("/tenants", methods=["GET"])
@require_super_admin
@handle_errors
def list_tenants(admin_user_id):
    """
    List all tenants with optional filters.

    Query params:
        status   – active | suspended | inactive
        search   – partial match on tenant name
        page     – 1-based page number (default 1)
        per_page – results per page (default 20, max 100)
    """
    supabase = current_app.supabase_client

    status   = request.args.get("status")
    search   = request.args.get("search", "").strip()
    page     = max(1, int(request.args.get("page", 1)))
    per_page = min(100, max(1, int(request.args.get("per_page", 20))))
    offset   = (page - 1) * per_page

    query = supabase.table("tenants").select("*", count="exact")

    if status:
        query = query.eq("status", status)
    if search:
        query = query.ilike("name", f"%{search}%")

    resp = supabase_call_with_retry(
        lambda: query.order("created_at", desc=True).range(offset, offset + per_page - 1).execute()
    )

    total = resp.count if resp.count is not None else len(resp.data)
    return jsonify({
        "tenants": resp.data,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": -(-total // per_page),  # ceiling division
        },
    }), 200


# ── 3. Get single tenant ──────────────────────────────────────────────────────

@super_admin_bp.route("/tenants/<tenant_id>", methods=["GET"])
@require_super_admin
@handle_errors
def get_tenant(admin_user_id, tenant_id):
    """Get full details for one tenant including agent config."""
    supabase = current_app.supabase_client

    tenant_resp = supabase_call_with_retry(
        lambda: supabase.table("tenants").select("*").eq("id", tenant_id).execute()
    )
    if not tenant_resp.data:
        return jsonify({"error": "Tenant not found"}), 404

    config_resp = supabase_call_with_retry(
        lambda: supabase.table("tenant_agent_config")
        .select("*")
        .eq("tenant_id", tenant_id)
        .execute()
    )

    return jsonify({
        "tenant": tenant_resp.data[0],
        "agent_config": config_resp.data[0] if config_resp.data else None,
    }), 200


# ── 4. Delete tenant ──────────────────────────────────────────────────────────

@super_admin_bp.route("/tenants/<tenant_id>", methods=["DELETE"])
@require_super_admin
@handle_errors
def delete_tenant(admin_user_id, tenant_id):
    """
    Hard-delete a tenant and all related records.
    Auth users are NOT deleted (they may belong to other tenants).

    Pass ?confirm=true to execute; otherwise returns a dry-run summary.
    """
    supabase = current_app.supabase_client
    confirm = request.args.get("confirm", "").lower() == "true"

    tenant_resp = supabase_call_with_retry(
        lambda: supabase.table("tenants").select("id,name,status").eq("id", tenant_id).execute()
    )
    if not tenant_resp.data:
        return jsonify({"error": "Tenant not found"}), 404

    tenant = tenant_resp.data[0]

    # Collect counts for dry-run summary
    users_resp = supabase_call_with_retry(
        lambda: supabase.table("tenant_users").select("id", count="exact").eq("tenant_id", tenant_id).execute()
    )
    phones_resp = supabase_call_with_retry(
        lambda: supabase.table("phone_numbers").select("id", count="exact").eq("tenant_id", tenant_id).execute()
    )

    summary = {
        "tenant_name": tenant["name"],
        "users_to_unlink": users_resp.count or 0,
        "phone_numbers_to_remove": phones_resp.count or 0,
    }

    if not confirm:
        return jsonify({
            "message": "Dry run — pass ?confirm=true to proceed",
            "impact": summary,
        }), 200

    # Delete in dependency order
    supabase_call_with_retry(
        lambda: supabase.table("tenant_agent_config").delete().eq("tenant_id", tenant_id).execute()
    )
    supabase_call_with_retry(
        lambda: supabase.table("phone_numbers").delete().eq("tenant_id", tenant_id).execute()
    )
    supabase_call_with_retry(
        lambda: supabase.table("tenant_users").delete().eq("tenant_id", tenant_id).execute()
    )
    supabase_call_with_retry(
        lambda: supabase.table("tenants").delete().eq("id", tenant_id).execute()
    )

    return jsonify({
        "message": f"Tenant '{tenant['name']}' deleted successfully",
        "deleted": summary,
    }), 200


# ── 5. Update tenant status (suspend / activate) ──────────────────────────────

@super_admin_bp.route("/tenants/<tenant_id>/status", methods=["PATCH"])
@require_super_admin
@handle_errors
def update_tenant_status(admin_user_id, tenant_id):
    """
    Change tenant status.

    Body:
    {
        "status": "suspended" | "active" | "inactive",
        "reason": "Non-payment"          // required when suspending
    }
    """
    data = request.get_json(silent=True) or {}
    new_status = data.get("status", "").strip()
    reason     = data.get("reason", "").strip()

    allowed_statuses = {"active", "suspended", "inactive"}
    if new_status not in allowed_statuses:
        return jsonify({"error": f"status must be one of: {', '.join(allowed_statuses)}"}), 400

    if new_status == "suspended" and not reason:
        return jsonify({"error": "reason is required when suspending a tenant"}), 400

    supabase = current_app.supabase_client

    tenant_resp = supabase_call_with_retry(
        lambda: supabase.table("tenants").select("id,name,status").eq("id", tenant_id).execute()
    )
    if not tenant_resp.data:
        return jsonify({"error": "Tenant not found"}), 404

    update_payload = {"status": new_status}

    updated = supabase_call_with_retry(
        lambda: supabase.table("tenants").update(update_payload).eq("id", tenant_id).execute()
    )

    return jsonify({
        "message": f"Tenant status updated to '{new_status}'",
        "tenant": updated.data[0] if updated.data else None,
    }), 200


# ── 6. Tenant usage stats ─────────────────────────────────────────────────────

@super_admin_bp.route("/tenants/<tenant_id>/usage", methods=["GET"])
@require_super_admin
@handle_errors
def get_tenant_usage(admin_user_id, tenant_id):
    """
    Aggregated usage stats for a tenant.

    Returns counts for: users, phone numbers, call logs (if table exists).
    """
    supabase = current_app.supabase_client

    tenant_resp = supabase_call_with_retry(
        lambda: supabase.table("tenants").select("id,name,status,created_at").eq("id", tenant_id).execute()
    )
    if not tenant_resp.data:
        return jsonify({"error": "Tenant not found"}), 404

    users_resp = supabase_call_with_retry(
        lambda: supabase.table("tenant_users").select("id,role", count="exact").eq("tenant_id", tenant_id).execute()
    )
    phones_resp = supabase_call_with_retry(
        lambda: supabase.table("phone_numbers").select("id,status", count="exact").eq("tenant_id", tenant_id).execute()
    )

    call_count = None
    try:
        calls_resp = supabase_call_with_retry(
            lambda: supabase.table("calls").select("id", count="exact").eq("tenant_id", tenant_id).execute()
        )
        call_count = calls_resp.count or 0
    except Exception:
        pass

    # Breakdown of users by role
    role_breakdown = {}
    for row in (users_resp.data or []):
        role_breakdown[row["role"]] = role_breakdown.get(row["role"], 0) + 1

    # Active vs inactive phone numbers
    active_phones = sum(1 for p in (phones_resp.data or []) if p.get("status") == "active")

    return jsonify({
        "tenant": tenant_resp.data[0],
        "usage": {
            "total_users": users_resp.count or 0,
            "users_by_role": role_breakdown,
            "total_phone_numbers": phones_resp.count or 0,
            "active_phone_numbers": active_phones,
            "total_calls": call_count,
        },
    }), 200


# ── 7. List users of a tenant ─────────────────────────────────────────────────

@super_admin_bp.route("/tenants/<tenant_id>/users", methods=["GET"])
@require_super_admin
@handle_errors
def get_tenant_users(admin_user_id, tenant_id):
    """List all users linked to a tenant."""
    supabase = current_app.supabase_client

    tenant_resp = supabase_call_with_retry(
        lambda: supabase.table("tenants").select("id,name").eq("id", tenant_id).execute()
    )
    if not tenant_resp.data:
        return jsonify({"error": "Tenant not found"}), 404

    users_resp = supabase_call_with_retry(
        lambda: supabase.table("tenant_users")
        .select("id, user_id, role, created_at")
        .eq("tenant_id", tenant_id)
        .order("created_at")
        .execute()
    )

    return jsonify({
        "tenant_id": tenant_id,
        "users": users_resp.data or [],
    }), 200


# ── 9. All users across all tenants ──────────────────────────────────────────

@super_admin_bp.route("/users", methods=["GET"])
@require_super_admin
@handle_errors
def list_all_users(admin_user_id):
    """
    List every user across every tenant.

    Query params:
        tenant_id – filter to one tenant
        role      – owner | admin | agent | viewer
        page      – 1-based (default 1)
        per_page  – max 100 (default 20)
    """
    supabase = current_app.supabase_client

    tenant_filter = request.args.get("tenant_id")
    role_filter   = request.args.get("role")
    page          = max(1, int(request.args.get("page", 1)))
    per_page      = min(100, max(1, int(request.args.get("per_page", 20))))
    offset        = (page - 1) * per_page

    query = (
        supabase.table("tenant_users")
        .select("id, user_id, role, created_at, tenants(id, name, status)", count="exact")
    )
    if tenant_filter:
        query = query.eq("tenant_id", tenant_filter)
    if role_filter:
        query = query.eq("role", role_filter)

    resp = supabase_call_with_retry(
        lambda: query.order("created_at", desc=True).range(offset, offset + per_page - 1).execute()
    )

    total = resp.count if resp.count is not None else len(resp.data)
    return jsonify({
        "users": resp.data or [],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": -(-total // per_page),
        },
    }), 200


# ── 10. All agent configs across all tenants ──────────────────────────────────

@super_admin_bp.route("/agent-configs", methods=["GET"])
@require_super_admin
@handle_errors
def list_all_agent_configs(admin_user_id):
    """
    List the agent configuration for every tenant in one call.
    Useful for auditing voice settings, system prompts, and allowed actions.

    Query params:
        page     – 1-based (default 1)
        per_page – max 100 (default 20)
    """
    supabase = current_app.supabase_client

    page     = max(1, int(request.args.get("page", 1)))
    per_page = min(100, max(1, int(request.args.get("per_page", 20))))
    offset   = (page - 1) * per_page

    resp = supabase_call_with_retry(
        lambda: supabase.table("tenant_agent_config")
        .select("*, tenants(id, name, status, industry)", count="exact")
        .order("created_at", desc=True)
        .range(offset, offset + per_page - 1)
        .execute()
    )

    total = resp.count if resp.count is not None else len(resp.data)
    return jsonify({
        "agent_configs": resp.data or [],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": -(-total // per_page),
        },
    }), 200


# ═══════════════════════════════════════════════════════════════════════════════
# TWILIO NUMBER MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

# ── 9. List all purchased numbers (DB view enriched from Twilio) ──────────────

@super_admin_bp.route("/twilio/numbers", methods=["GET"])
@require_super_admin
@handle_errors
def list_all_numbers(admin_user_id):
    """
    List every phone number across all tenants stored in the DB.
    Optionally enriched with live Twilio data.

    Query params:
        tenant_id – filter to a single tenant
        status    – active | inactive | released
        enrich    – true to fetch live voice_url/status from Twilio (slower)
        page      – 1-based (default 1)
        per_page  – max 100 (default 20)
    """
    supabase = current_app.supabase_client
    twilio   = current_app.twilio_client

    tenant_filter = request.args.get("tenant_id")
    status_filter = request.args.get("status")
    enrich        = request.args.get("enrich", "").lower() == "true"
    page          = max(1, int(request.args.get("page", 1)))
    per_page      = min(100, max(1, int(request.args.get("per_page", 20))))
    offset        = (page - 1) * per_page

    query = (
        supabase.table("phone_numbers")
        .select("*, tenants(id, name, status)", count="exact")
    )
    if tenant_filter:
        query = query.eq("tenant_id", tenant_filter)
    if status_filter:
        query = query.eq("status", status_filter)

    resp = supabase_call_with_retry(
        lambda: query.order("created_at", desc=True).range(offset, offset + per_page - 1).execute()
    )

    numbers = resp.data or []

    if enrich and twilio:
        # Build a SID → Twilio record map in one API call
        try:
            twilio_numbers = {n.sid: n for n in twilio.incoming_phone_numbers.list(limit=1000)}
            for num in numbers:
                sid = num.get("twilio_number_sid")
                tw  = twilio_numbers.get(sid)
                num["twilio"] = {
                    "voice_url":   tw.voice_url   if tw else None,
                    "status":      tw.status       if tw else "not_found",
                    "friendly_name": tw.friendly_name if tw else None,
                } if sid else None
        except Exception as e:
            for num in numbers:
                num["twilio"] = None
            print(f"[WARN] Twilio enrichment failed: {e}")

    total = resp.count or len(numbers)
    return jsonify({
        "numbers": numbers,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": -(-total // per_page),
        },
    }), 200


# ── 10. Unassigned numbers (in Twilio but not linked to any tenant) ───────────

@super_admin_bp.route("/twilio/numbers/unassigned", methods=["GET"])
@require_super_admin
@handle_errors
def list_unassigned_numbers(admin_user_id):
    """
    Numbers that exist in the Twilio account but have no tenant assignment
    in the phone_numbers table (or whose DB record has no tenant_id).
    Useful for auditing orphaned numbers that are still being billed.
    """
    supabase = current_app.supabase_client
    twilio   = current_app.twilio_client

    if not twilio:
        return jsonify({"error": "Twilio client not configured"}), 503

    # All SIDs tracked in DB
    db_resp = supabase_call_with_retry(
        lambda: supabase.table("phone_numbers")
        .select("twilio_number_sid, tenant_id, phone_number")
        .execute()
    )
    db_sids_with_tenant = {
        row["twilio_number_sid"]: row.get("tenant_id")
        for row in (db_resp.data or [])
        if row.get("twilio_number_sid")
    }

    # All numbers in Twilio account
    twilio_numbers = twilio.incoming_phone_numbers.list(limit=1000)

    unassigned = []
    for num in twilio_numbers:
        tenant_id = db_sids_with_tenant.get(num.sid)
        if tenant_id is None:
            # either not in DB at all, or in DB with no tenant_id
            unassigned.append({
                "sid":           num.sid,
                "phone_number":  num.phone_number,
                "friendly_name": num.friendly_name,
                "voice_url":     num.voice_url,
                "in_db":         num.sid in db_sids_with_tenant,
            })

    return jsonify({
        "unassigned_count": len(unassigned),
        "unassigned_numbers": unassigned,
    }), 200


# ── 11. Release a number ──────────────────────────────────────────────────────

@super_admin_bp.route("/twilio/numbers/<sid>", methods=["DELETE"])
@require_super_admin
@handle_errors
def release_number(admin_user_id, sid):
    """
    Release a phone number from Twilio and remove it from the DB.

    Path param: sid – Twilio IncomingPhoneNumber SID (e.g. PN...)
    Query param: confirm=true required to execute (dry-run otherwise).

    WARNING: this immediately stops billing AND the number becomes
    available to other Twilio customers. It cannot be reclaimed.
    """
    supabase = current_app.supabase_client
    twilio   = current_app.twilio_client

    if not twilio:
        return jsonify({"error": "Twilio client not configured"}), 503

    confirm = request.args.get("confirm", "").lower() == "true"

    # Look up in DB
    db_resp = supabase_call_with_retry(
        lambda: supabase.table("phone_numbers")
        .select("*, tenants(id, name)")
        .eq("twilio_number_sid", sid)
        .execute()
    )
    db_row = db_resp.data[0] if db_resp.data else None

    # Verify the number actually exists in Twilio
    try:
        twilio_num = twilio.incoming_phone_numbers(sid).fetch()
    except Exception:
        twilio_num = None

    if not twilio_num and not db_row:
        return jsonify({"error": f"Number SID '{sid}' not found in Twilio or DB"}), 404

    summary = {
        "sid":          sid,
        "phone_number": twilio_num.phone_number if twilio_num else db_row.get("phone_number"),
        "tenant_name":  (db_row["tenants"]["name"] if db_row and db_row.get("tenants") else None),
        "in_twilio":    twilio_num is not None,
        "in_db":        db_row is not None,
    }

    if not confirm:
        return jsonify({
            "message": "Dry run — pass ?confirm=true to release this number. THIS IS IRREVERSIBLE.",
            "number": summary,
        }), 200

    errors = []

    # 1. Release from Twilio
    if twilio_num:
        try:
            twilio.incoming_phone_numbers(sid).delete()
        except Exception as e:
            errors.append(f"Twilio release failed: {e}")

    # 2. Remove from DB (or mark released)
    if db_row:
        try:
            supabase_call_with_retry(
                lambda: supabase.table("phone_numbers")
                .delete()
                .eq("twilio_number_sid", sid)
                .execute()
            )
        except Exception as e:
            errors.append(f"DB removal failed: {e}")

    if errors:
        return jsonify({"message": "Partial failure during release", "errors": errors, "number": summary}), 500

    return jsonify({
        "message": f"Number {summary['phone_number']} released successfully",
        "number": summary,
    }), 200


# ═══════════════════════════════════════════════════════════════════════════════
# GLOBAL MONITORING
# ═══════════════════════════════════════════════════════════════════════════════

# ── 12. All calls across tenants ──────────────────────────────────────────────

@super_admin_bp.route("/monitoring/calls", methods=["GET"])
@require_super_admin
@handle_errors
def all_calls(admin_user_id):
    """
    Platform-wide call log view across all tenants.

    Query params:
        tenant_id  – filter to one tenant
        status     – e.g. completed | failed | in-progress
        from_date  – ISO date string (inclusive), e.g. 2025-04-01
        to_date    – ISO date string (inclusive), e.g. 2025-05-08
        page       – 1-based (default 1)
        per_page   – max 100 (default 20)
    """
    supabase = current_app.supabase_client

    tenant_filter = request.args.get("tenant_id")
    status_filter = request.args.get("status")
    from_date     = request.args.get("from_date")
    to_date       = request.args.get("to_date")
    page          = max(1, int(request.args.get("page", 1)))
    per_page      = min(100, max(1, int(request.args.get("per_page", 20))))
    offset        = (page - 1) * per_page

    try:
        query = (
            supabase.table("calls")
            .select("*, tenants(id, name)", count="exact")
        )
        if tenant_filter:
            query = query.eq("tenant_id", tenant_filter)
        if status_filter:
            query = query.eq("status", status_filter)
        if from_date:
            query = query.gte("created_at", from_date)
        if to_date:
            query = query.lte("created_at", f"{to_date}T23:59:59Z")

        resp = supabase_call_with_retry(
            lambda: query.order("created_at", desc=True).range(offset, offset + per_page - 1).execute()
        )
    except Exception as e:
        return jsonify({"error": f"calls table unavailable: {e}"}), 503

    calls = resp.data or []

    # For calls still showing ringing/in-progress that are older than 60 s,
    # the status callback either failed or hasn't arrived yet — pull the real
    # status and duration directly from Twilio and patch both the response and
    # the DB so the next request doesn't repeat this work.
    twilio = current_app.twilio_client
    if twilio:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
        stale = [
            c for c in calls
            if c.get("status") in ("ringing", "in-progress")
            and c.get("call_sid")
            and c.get("created_at")
            and datetime.fromisoformat(c["created_at"].replace("Z", "+00:00")) < cutoff
        ]
        for call in stale:
            try:
                tw = twilio.calls(call["call_sid"]).fetch()
                patch = {"status": tw.status}
                if tw.status == "completed":
                    if tw.duration:
                        patch["duration_seconds"] = int(tw.duration)
                    if tw.end_time:
                        patch["end_time"] = tw.end_time.isoformat()
                elif tw.status in ("busy", "failed", "no-answer", "canceled"):
                    if tw.end_time:
                        patch["end_time"] = tw.end_time.isoformat()
                supabase_call_with_retry(
                    lambda sid=call["call_sid"], p=patch:
                        supabase.table("calls").update(p).eq("call_sid", sid).execute()
                )
                call.update(patch)
            except Exception:
                pass

    total = resp.count or len(calls)
    return jsonify({
        "calls": calls,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": -(-total // per_page),
        },
    }), 200


# ── 13. All recordings across tenants ────────────────────────────────────────

@super_admin_bp.route("/monitoring/recordings", methods=["GET"])
@require_super_admin
@handle_errors
def all_recordings(admin_user_id):
    """
    Platform-wide recording view across all tenants.

    Query params:
        tenant_id – filter to one tenant
        status    – processing | available | deleted
        from_date – ISO date string (inclusive), e.g. 2025-04-01
        to_date   – ISO date string (inclusive), e.g. 2025-05-08
        page      – 1-based (default 1)
        per_page  – max 100 (default 20)
    """
    supabase = current_app.supabase_client

    tenant_filter = request.args.get("tenant_id")
    status_filter = request.args.get("status")
    from_date     = request.args.get("from_date")
    to_date       = request.args.get("to_date")
    page          = max(1, int(request.args.get("page", 1)))
    per_page      = min(100, max(1, int(request.args.get("per_page", 20))))
    offset        = (page - 1) * per_page

    query = (
        supabase.table("recordings")
        .select(
            "id, twilio_recording_sid, recording_url, storage_path, "
            "duration_seconds, file_size_bytes, status, created_at, "
            "tenants(id, name), calls(call_sid, from_number, to_number, direction)",
            count="exact",
        )
    )
    if tenant_filter:
        query = query.eq("tenant_id", tenant_filter)
    if status_filter:
        query = query.eq("status", status_filter)
    if from_date:
        query = query.gte("created_at", from_date)
    if to_date:
        query = query.lte("created_at", f"{to_date}T23:59:59Z")

    resp = supabase_call_with_retry(
        lambda: query.order("created_at", desc=True).range(offset, offset + per_page - 1).execute()
    )

    total = resp.count if resp.count is not None else len(resp.data)
    return jsonify({
        "recordings": resp.data or [],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": -(-total // per_page),
        },
    }), 200


# ── 14. System health ─────────────────────────────────────────────────────────

@super_admin_bp.route("/monitoring/health", methods=["GET"])
@require_super_admin
@handle_errors
def system_health(admin_user_id):
    """
    Live health check for every external dependency.
    Returns individual component status and an overall platform status.
    """
    checks = {}

    # Supabase DB
    try:
        supabase = current_app.supabase_client
        supabase_call_with_retry(
            lambda: supabase.table("tenants").select("id").limit(1).execute()
        )
        checks["supabase"] = {"status": "ok"}
    except Exception as e:
        checks["supabase"] = {"status": "error", "detail": str(e)}

    # Twilio
    try:
        twilio = current_app.twilio_client
        if twilio:
            account = twilio.api.accounts(twilio.account_sid).fetch()
            checks["twilio"] = {"status": "ok", "account_status": account.status}
        else:
            checks["twilio"] = {"status": "not_configured"}
    except Exception as e:
        checks["twilio"] = {"status": "error", "detail": str(e)}

    # OpenAI
    try:
        openai_client = current_app.openai_client
        models = openai_client.models.list()
        checks["openai"] = {"status": "ok", "models_available": bool(models.data)}
    except Exception as e:
        checks["openai"] = {"status": "error", "detail": str(e)}

    overall = "ok" if all(v["status"] == "ok" for v in checks.values()) else "degraded"

    return jsonify({
        "overall": overall,
        "timestamp": _now_iso(),
        "components": checks,
    }), 200 if overall == "ok" else 207


# ── 14. Platform-wide usage analytics ────────────────────────────────────────

@super_admin_bp.route("/monitoring/analytics", methods=["GET"])
@require_super_admin
@handle_errors
def usage_analytics(admin_user_id):
    """
    Aggregated platform metrics for the product owner dashboard.

    Returns:
    - Tenant counts by status
    - Total users and phone numbers
    - Call volume (total, last 30 days, last 7 days)
    - Top tenants by call volume
    """
    supabase = current_app.supabase_client
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    last_7d  = (now - timedelta(days=7)).isoformat()
    last_30d = (now - timedelta(days=30)).isoformat()

    # ── Tenant stats ──────────────────────────────────────────────────────────
    tenants_resp = supabase_call_with_retry(
        lambda: supabase.table("tenants").select("id, status", count="exact").execute()
    )
    tenant_rows = tenants_resp.data or []
    tenant_total = tenants_resp.count if tenants_resp.count is not None else len(tenant_rows)

    tenants_by_status: dict = {}
    for row in tenant_rows:
        s = row.get("status", "unknown")
        tenants_by_status[s] = tenants_by_status.get(s, 0) + 1

    # ── User stats ────────────────────────────────────────────────────────────
    users_resp = supabase_call_with_retry(
        lambda: supabase.table("tenant_users").select("id", count="exact").execute()
    )

    # ── Phone number stats ────────────────────────────────────────────────────
    phones_resp = supabase_call_with_retry(
        lambda: supabase.table("phone_numbers").select("id", count="exact").execute()
    )
    active_phones_resp = supabase_call_with_retry(
        lambda: supabase.table("phone_numbers").select("id", count="exact").eq("status", "active").execute()
    )
    active_phones = active_phones_resp.count or 0

    # ── Call stats (optional table) ───────────────────────────────────────────
    call_stats = None
    top_tenants = []
    try:
        total_calls_resp = supabase_call_with_retry(
            lambda: supabase.table("calls").select("id", count="exact").execute()
        )
        calls_7d_resp = supabase_call_with_retry(
            lambda: supabase.table("calls").select("id", count="exact").gte("created_at", last_7d).execute()
        )
        calls_30d_resp = supabase_call_with_retry(
            lambda: supabase.table("calls").select("id", count="exact").gte("created_at", last_30d).execute()
        )

        # Top 10 tenants by total calls — uses a DB-side aggregation via RPC
        # to avoid pulling every call row into Python memory.
        # Required migration: see database_schema.sql for get_top_tenants_by_calls() function.
        try:
            top_resp = supabase_call_with_retry(
                lambda: supabase.rpc("get_top_tenants_by_calls", {"limit_n": 10}).execute()
            )
            top_tenants = top_resp.data or []
        except Exception:
            # Fallback: fetch only tenant_id column and aggregate in Python (avoids join overhead)
            top_fallback = supabase_call_with_retry(
                lambda: supabase.table("calls").select("tenant_id").limit(5000).execute()
            )
            tenant_call_counts: dict = {}
            for row in (top_fallback.data or []):
                tid = row.get("tenant_id")
                if tid:
                    tenant_call_counts[tid] = tenant_call_counts.get(tid, 0) + 1
            top_ids = sorted(tenant_call_counts, key=lambda t: tenant_call_counts[t], reverse=True)[:10]
            # Fetch names for the top 10 only
            names_resp = supabase_call_with_retry(
                lambda: supabase.table("tenants").select("id, name").in_("id", top_ids).execute()
            ) if top_ids else None
            name_map = {r["id"]: r["name"] for r in (names_resp.data or [])} if names_resp else {}
            top_tenants = [
                {"tenant_id": tid, "tenant_name": name_map.get(tid, tid), "call_count": tenant_call_counts[tid]}
                for tid in top_ids
            ]

        call_stats = {
            "total_calls":        total_calls_resp.count or 0,
            "calls_last_7_days":  calls_7d_resp.count or 0,
            "calls_last_30_days": calls_30d_resp.count or 0,
        }
    except Exception:
        pass

    return jsonify({
        "generated_at": _now_iso(),
        "tenants": {
            "total":     tenant_total,
            "by_status": tenants_by_status,
            "by_plan":   {},
        },
        "users": {
            "total": users_resp.count or 0,
        },
        "phone_numbers": {
            "total":  phones_resp.count or 0,
            "active": active_phones,
        },
        "calls": call_stats,
        "top_tenants_by_calls": top_tenants,
    }), 200


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT PACKS  (platform-wide industry templates)
# ═══════════════════════════════════════════════════════════════════════════════

# ── 15. List agent packs ──────────────────────────────────────────────────────

@super_admin_bp.route("/agent-packs", methods=["GET"])
@require_super_admin
@handle_errors
def list_agent_packs(admin_user_id):
    """List all platform-wide agent pack templates."""
    supabase = current_app.supabase_client

    resp = supabase_call_with_retry(
        lambda: supabase.table("agent_packs").select("*").order("name").execute()
    )
    return jsonify({"agent_packs": resp.data or []}), 200


# ── 16. Get single agent pack ─────────────────────────────────────────────────

@super_admin_bp.route("/agent-packs/<pack_id>", methods=["GET"])
@require_super_admin
@handle_errors
def get_agent_pack(admin_user_id, pack_id):
    """Get a single agent pack template."""
    supabase = current_app.supabase_client

    resp = supabase_call_with_retry(
        lambda: supabase.table("agent_packs").select("*").eq("id", pack_id).execute()
    )
    if not resp.data:
        return jsonify({"error": "Agent pack not found"}), 404
    return jsonify({"agent_pack": resp.data[0]}), 200


# ── 17. Create agent pack ─────────────────────────────────────────────────────

@super_admin_bp.route("/agent-packs", methods=["POST"])
@require_super_admin
@handle_errors
def create_agent_pack(admin_user_id):
    """
    Create a new platform-wide agent pack template.

    Body:
    {
        "name": "Healthcare",
        "industry": "Healthcare",
        "default_greeting": "Thank you for calling. How may I assist you?",
        "default_tone": "professional",
        "default_rules": { "max_call_duration": 600 },
        "default_prompts": { "system": "You are a healthcare assistant..." },
        "allowed_actions": ["create_booking", "handoff_to_human"],
        "description": "Optimised for medical practices"
    }
    """
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    supabase = current_app.supabase_client

    payload = {
        "name":             name,
        "industry":         data.get("industry"),
        "description":      data.get("description"),
        "default_greeting": data.get("default_greeting", "Thank you for calling. How may I assist you?"),
        "default_tone":     data.get("default_tone", "professional"),
        "default_rules":    data.get("default_rules", {}),
        "default_prompts":  data.get("default_prompts", {}),
        "allowed_actions":  data.get("allowed_actions", []),
    }

    resp = supabase_call_with_retry(
        lambda: supabase.table("agent_packs").insert(payload).execute()
    )
    if not resp.data:
        return jsonify({"error": "Failed to create agent pack"}), 500

    return jsonify({"message": "Agent pack created", "agent_pack": resp.data[0]}), 201


# ── 18. Update agent pack ─────────────────────────────────────────────────────

@super_admin_bp.route("/agent-packs/<pack_id>", methods=["PUT"])
@require_super_admin
@handle_errors
def update_agent_pack(admin_user_id, pack_id):
    """Update an existing agent pack template."""
    supabase = current_app.supabase_client

    existing = supabase_call_with_retry(
        lambda: supabase.table("agent_packs").select("id").eq("id", pack_id).execute()
    )
    if not existing.data:
        return jsonify({"error": "Agent pack not found"}), 404

    data = request.get_json(silent=True) or {}
    allowed = ["name", "industry", "description", "default_greeting", "default_tone",
               "default_rules", "default_prompts", "allowed_actions"]
    payload = {k: v for k, v in data.items() if k in allowed}

    if not payload:
        return jsonify({"error": "No valid fields to update"}), 400

    resp = supabase_call_with_retry(
        lambda: supabase.table("agent_packs").update(payload).eq("id", pack_id).execute()
    )
    return jsonify({"message": "Agent pack updated", "agent_pack": resp.data[0] if resp.data else None}), 200


# ── 19. Delete agent pack ─────────────────────────────────────────────────────

@super_admin_bp.route("/agent-packs/<pack_id>", methods=["DELETE"])
@require_super_admin
@handle_errors
def delete_agent_pack(admin_user_id, pack_id):
    """Delete a platform agent pack template."""
    supabase = current_app.supabase_client

    existing = supabase_call_with_retry(
        lambda: supabase.table("agent_packs").select("id,name").eq("id", pack_id).execute()
    )
    if not existing.data:
        return jsonify({"error": "Agent pack not found"}), 404

    supabase_call_with_retry(
        lambda: supabase.table("agent_packs").delete().eq("id", pack_id).execute()
    )
    return jsonify({"message": f"Agent pack '{existing.data[0]['name']}' deleted"}), 200


# ── 20. Assign agent pack to a tenant ────────────────────────────────────────

@super_admin_bp.route("/tenants/<tenant_id>/agent-pack", methods=["POST"])
@require_super_admin
@handle_errors
def assign_agent_pack(admin_user_id, tenant_id):
    """
    Apply an agent pack template to a tenant's agent config.
    Overwrites greeting, tone, default_rules, default_prompts, and allowed_actions
    in tenant_agent_config with the pack's defaults.

    Body: { "pack_id": "<uuid>" }
    """
    data    = request.get_json(silent=True) or {}
    pack_id = data.get("pack_id", "").strip()
    if not pack_id:
        return jsonify({"error": "pack_id is required"}), 400

    supabase = current_app.supabase_client

    tenant_resp = supabase_call_with_retry(
        lambda: supabase.table("tenants").select("id,name").eq("id", tenant_id).execute()
    )
    if not tenant_resp.data:
        return jsonify({"error": "Tenant not found"}), 404

    pack_resp = supabase_call_with_retry(
        lambda: supabase.table("agent_packs").select("*").eq("id", pack_id).execute()
    )
    if not pack_resp.data:
        return jsonify({"error": "Agent pack not found"}), 404

    pack = pack_resp.data[0]
    config_payload = {
        "greeting":        pack.get("default_greeting"),
        "tone":            pack.get("default_tone"),
        "escalation_rules": pack.get("default_rules", {}),
        "allowed_actions": pack.get("allowed_actions", []),
        "agent_pack_id":   pack_id,
    }
    if pack.get("default_prompts"):
        config_payload["system_prompt"] = pack["default_prompts"].get("system")

    # Upsert — update if exists, insert if not
    existing_cfg = supabase_call_with_retry(
        lambda: supabase.table("tenant_agent_config").select("id").eq("tenant_id", tenant_id).execute()
    )
    if existing_cfg.data:
        supabase_call_with_retry(
            lambda: supabase.table("tenant_agent_config").update(config_payload).eq("tenant_id", tenant_id).execute()
        )
    else:
        config_payload["tenant_id"] = tenant_id
        supabase_call_with_retry(
            lambda: supabase.table("tenant_agent_config").insert(config_payload).execute()
        )

    return jsonify({
        "message": f"Agent pack '{pack['name']}' applied to tenant '{tenant_resp.data[0]['name']}'",
        "applied_pack": pack,
    }), 200


# ═══════════════════════════════════════════════════════════════════════════════
# TWILIO NUMBER ASSIGNMENT
# ═══════════════════════════════════════════════════════════════════════════════

# ── 21. Assign existing number to a tenant ────────────────────────────────────

@super_admin_bp.route("/twilio/numbers/<sid>/assign", methods=["POST"])
@require_super_admin
@handle_errors
def assign_number_to_tenant(admin_user_id, sid):
    """
    Assign a Twilio number to a tenant.
    If the number exists in the DB, its tenant_id is updated.
    If it only exists in Twilio (never imported), it is auto-imported first.

    Body: { "tenant_id": "<uuid>" }
    """
    data      = request.get_json(silent=True) or {}
    tenant_id = data.get("tenant_id", "").strip()
    if not tenant_id:
        return jsonify({"error": "tenant_id is required"}), 400

    supabase = current_app.supabase_client
    twilio   = current_app.twilio_client

    # 1. Verify the target tenant exists
    tenant_resp = supabase_call_with_retry(
        lambda: supabase.table("tenants").select("id,name").eq("id", tenant_id).execute()
    )
    if not tenant_resp.data:
        return jsonify({"error": "Tenant not found"}), 404

    tenant_name = tenant_resp.data[0]["name"]

    # 2. Check if the number is already in the DB
    num_resp = supabase_call_with_retry(
        lambda: supabase.table("phone_numbers")
        .select("*, tenants(id, name)")
        .eq("twilio_number_sid", sid)
        .execute()
    )

    if num_resp.data:
        # Number is in DB — update its tenant assignment
        previous_tenant = num_resp.data[0].get("tenants")
        phone_number    = num_resp.data[0].get("phone_number", sid)

        updated = supabase_call_with_retry(
            lambda: supabase.table("phone_numbers")
            .update({"tenant_id": tenant_id, "status": "active"})
            .eq("twilio_number_sid", sid)
            .execute()
        )
        return jsonify({
            "message": f"Number {phone_number} assigned to tenant '{tenant_name}'",
            "number": updated.data[0] if updated.data else None,
            "previous_tenant": previous_tenant,
        }), 200

    # 3. Number not in DB — fetch it from Twilio and import it
    if not twilio:
        return jsonify({"error": "Number not in DB and Twilio client not configured"}), 503

    try:
        twilio_num = twilio.incoming_phone_numbers(sid).fetch()
    except Exception as e:
        return jsonify({"error": f"SID '{sid}' not found in Twilio: {str(e)}"}), 404

    new_record = supabase_call_with_retry(
        lambda: supabase.table("phone_numbers").insert({
            "tenant_id":        tenant_id,
            "phone_number":     twilio_num.phone_number,
            "twilio_number_sid": sid,
            "status":           "active",
        }).execute()
    )

    return jsonify({
        "message": f"Number {twilio_num.phone_number} imported from Twilio and assigned to tenant '{tenant_name}'",
        "number": new_record.data[0] if new_record.data else None,
        "previous_tenant": None,
    }), 201


# ═══════════════════════════════════════════════════════════════════════════════
# EMAIL LOGS MONITORING
# ═══════════════════════════════════════════════════════════════════════════════

# ── 22. Platform-wide email logs ──────────────────────────────────────────────

@super_admin_bp.route("/monitoring/email-logs", methods=["GET"])
@require_super_admin
@handle_errors
def email_logs(admin_user_id):
    """
    View post-call email delivery logs across all tenants.

    Query params:
        tenant_id – filter to one tenant
        status    – sent | failed | pending
        from_date – ISO date string (inclusive)
        to_date   – ISO date string (inclusive)
        page      – 1-based (default 1)
        per_page  – max 100 (default 20)
    """
    supabase = current_app.supabase_client

    tenant_filter = request.args.get("tenant_id")
    status_filter = request.args.get("status")
    from_date     = request.args.get("from_date")
    to_date       = request.args.get("to_date")
    page          = max(1, int(request.args.get("page", 1)))
    per_page      = min(100, max(1, int(request.args.get("per_page", 20))))
    offset        = (page - 1) * per_page

    try:
        query = (
            supabase.table("email_logs")
            .select("*, tenants(id, name)", count="exact")
        )
        if tenant_filter:
            query = query.eq("tenant_id", tenant_filter)
        if status_filter:
            query = query.eq("status", status_filter)
        if from_date:
            query = query.gte("sent_at", from_date)
        if to_date:
            query = query.lte("sent_at", f"{to_date}T23:59:59Z")

        resp = supabase_call_with_retry(
            lambda: query.order("sent_at", desc=True).range(offset, offset + per_page - 1).execute()
        )
    except Exception as e:
        return jsonify({"error": f"email_logs table unavailable: {e}"}), 503

    total = resp.count or len(resp.data)
    return jsonify({
        "email_logs": resp.data or [],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": -(-total // per_page),
        },
    }), 200


# ═══════════════════════════════════════════════════════════════════════════════
# SUPER ADMIN USER MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

# ── 23. List super admins ─────────────────────────────────────────────────────

@super_admin_bp.route("/super-admins", methods=["GET"])
@require_super_admin
@handle_errors
def list_super_admins(admin_user_id):
    """List all users with super admin access."""
    supabase = current_app.supabase_client

    resp = supabase_call_with_retry(
        lambda: supabase.table("super_admins").select("*").order("created_at").execute()
    )
    return jsonify({"super_admins": resp.data or []}), 200


# ── 24. Add super admin ───────────────────────────────────────────────────────

@super_admin_bp.route("/super-admins", methods=["POST"])
@require_super_admin
@handle_errors
def add_super_admin(admin_user_id):
    """
    Grant super admin access to an existing Supabase auth user.

    Body: { "email": "newadmin@company.com" }

    The user must already exist in auth.users.
    """
    data  = request.get_json(silent=True) or {}
    email = data.get("email", "").strip().lower()
    if not email:
        return jsonify({"error": "email is required"}), 400

    supabase = current_app.supabase_client

    # Find user in auth.users by email
    try:
        users_resp = supabase.auth.admin.list_users()
        users_list = users_resp if isinstance(users_resp, list) else getattr(users_resp, "users", [])
        target_user = next(
            (u for u in users_list
             if (getattr(u, "email", None) or (u.get("email") if isinstance(u, dict) else None) or "").lower() == email),
            None,
        )
    except Exception as e:
        return jsonify({"error": f"Failed to look up user: {e}"}), 500

    if not target_user:
        return jsonify({"error": f"No auth user found with email '{email}'"}), 404

    user_id = getattr(target_user, "id", None) or (target_user.get("id") if isinstance(target_user, dict) else None)

    # Check not already a super admin
    existing = supabase_call_with_retry(
        lambda: supabase.table("super_admins").select("id").eq("user_id", user_id).execute()
    )
    if existing.data:
        return jsonify({"error": "User is already a super admin"}), 400

    resp = supabase_call_with_retry(
        lambda: supabase.table("super_admins").insert({"user_id": user_id, "email": email}).execute()
    )
    return jsonify({"message": f"Super admin access granted to '{email}'", "super_admin": resp.data[0] if resp.data else None}), 201


# ── 25. Remove super admin ────────────────────────────────────────────────────

@super_admin_bp.route("/super-admins/<target_user_id>", methods=["DELETE"])
@require_super_admin
@handle_errors
def remove_super_admin(admin_user_id, target_user_id):
    """
    Revoke super admin access from a user.
    Admins cannot remove themselves.
    """
    if target_user_id == admin_user_id:
        return jsonify({"error": "You cannot remove your own super admin access"}), 400

    supabase = current_app.supabase_client

    existing = supabase_call_with_retry(
        lambda: supabase.table("super_admins").select("id,email").eq("user_id", target_user_id).execute()
    )
    if not existing.data:
        return jsonify({"error": "Super admin not found"}), 404

    supabase_call_with_retry(
        lambda: supabase.table("super_admins").delete().eq("user_id", target_user_id).execute()
    )
    return jsonify({"message": f"Super admin access revoked from '{existing.data[0]['email']}'"}), 200


# ── Spam blocklist ────────────────────────────────────────────────────────────

@super_admin_bp.route("/spam-numbers", methods=["GET"])
@require_super_admin
@handle_errors
def list_spam_numbers(admin_user_id):
    """Paginated list of platform-wide blocklisted numbers."""
    supabase = current_app.supabase_client

    page = max(1, int(request.args.get("page", 1)))
    per_page = min(100, max(1, int(request.args.get("per_page", 20))))
    offset = (page - 1) * per_page

    resp = supabase_call_with_retry(
        lambda: supabase.table("spam_numbers")
        .select("*", count="exact")
        .order("created_at", desc=True)
        .range(offset, offset + per_page - 1)
        .execute()
    )
    total = resp.count if resp.count is not None else len(resp.data or [])
    return jsonify({
        "spam_numbers": resp.data or [],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": -(-total // per_page) if per_page else 1,
        },
    }), 200


@super_admin_bp.route("/spam-numbers", methods=["POST"])
@require_super_admin
@handle_errors
def add_spam_number(admin_user_id):
    """Add a phone number to the platform-wide blocklist."""
    supabase = current_app.supabase_client
    data = request.get_json(silent=True) or {}

    phone = (data.get("phone_number") or "").strip()
    if not phone:
        return jsonify({"error": "phone_number is required"}), 400
    reason = (data.get("reason") or "").strip() or None

    existing = supabase_call_with_retry(
        lambda: supabase.table("spam_numbers").select("id").eq("phone_number", phone).limit(1).execute()
    )
    if existing.data:
        return jsonify({"error": "This number is already on the blocklist"}), 409

    resp = supabase_call_with_retry(
        lambda: supabase.table("spam_numbers").insert({
            "phone_number": phone,
            "reason": reason,
            "added_by": admin_user_id,
        }).execute()
    )
    if not resp.data:
        return jsonify({"error": "Failed to add spam number"}), 500

    return jsonify({
        "message": f"'{phone}' added to the blocklist",
        "spam_number": resp.data[0],
    }), 201


@super_admin_bp.route("/spam-numbers/<spam_id>", methods=["DELETE"])
@require_super_admin
@handle_errors
def remove_spam_number(admin_user_id, spam_id):
    """Remove a number from the platform-wide blocklist."""
    supabase = current_app.supabase_client

    existing = supabase_call_with_retry(
        lambda: supabase.table("spam_numbers").select("phone_number").eq("id", spam_id).limit(1).execute()
    )
    if not existing.data:
        return jsonify({"error": "Spam number not found"}), 404

    supabase_call_with_retry(
        lambda: supabase.table("spam_numbers").delete().eq("id", spam_id).execute()
    )
    return jsonify({
        "message": f"'{existing.data[0]['phone_number']}' removed from the blocklist"
    }), 200


@super_admin_bp.route("/monitoring/spam", methods=["GET"])
@require_super_admin
@handle_errors
def spam_monitoring(admin_user_id):
    """
    Platform-wide spam call monitoring over a time window.

    Query params:
        days           – window size (default 7, max 365)
        min_spam_score – threshold to count a call as flagged
        page/per_page  – pagination for the flagged-call list
    """
    supabase = current_app.supabase_client

    try:
        days = min(365, max(1, int(request.args.get("days", 7))))
    except (ValueError, TypeError):
        days = 7
    try:
        threshold = float(request.args.get("min_spam_score", Config.SPAM_SCORE_THRESHOLD))
    except (ValueError, TypeError):
        threshold = Config.SPAM_SCORE_THRESHOLD
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(100, max(1, int(request.args.get("per_page", 20))))
    offset = (page - 1) * per_page

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    try:
        resp = supabase_call_with_retry(
            lambda: supabase.table("calls")
            .select("id, tenant_id, call_sid, from_number, to_number, status, spam_score, spam_flags, created_at")
            .gte("created_at", cutoff)
            .execute()
        )
        rows = resp.data or []
    except Exception as e:
        # spam columns not migrated yet — return an empty (but valid) report.
        print(f"[WARN] spam monitoring query failed: {e}")
        rows = []

    total_calls = len(rows)
    flagged = [r for r in rows if float(r.get("spam_score") or 0) >= threshold]
    flagged_count = len(flagged)

    offenders = {}
    for r in flagged:
        num = r.get("from_number") or "unknown"
        agg = offenders.setdefault(num, {"from_number": num, "flagged_calls": 0, "max_spam_score": 0})
        agg["flagged_calls"] += 1
        agg["max_spam_score"] = max(agg["max_spam_score"], float(r.get("spam_score") or 0))
    top_offenders = sorted(offenders.values(), key=lambda x: x["flagged_calls"], reverse=True)[:10]

    flagged_sorted = sorted(flagged, key=lambda r: r.get("created_at") or "", reverse=True)
    page_slice = flagged_sorted[offset:offset + per_page]

    return jsonify({
        "window_days": days,
        "spam_threshold": threshold,
        "summary": {
            "total_calls": total_calls,
            "flagged_calls": flagged_count,
            "flagged_rate": round(flagged_count / total_calls, 3) if total_calls else 0,
        },
        "top_offenders": top_offenders,
        "flagged_calls": page_slice,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": flagged_count,
            "pages": -(-flagged_count // per_page) if per_page else 1,
        },
    }), 200


# ── Health ────────────────────────────────────────────────────────────────────

@super_admin_bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "service": "super_admin"}), 200
