from flask import Blueprint, request, jsonify, current_app
from urllib.parse import urlparse
from config import Config
from utils.auth_utils import require_role, require_tenant


twilio_bp = Blueprint('twilio', __name__)


def _get_tenant_country(tenant_id, default="CA"):
    """Return the ISO country code configured on the tenant's profile.

    The phone-number search and purchase are locked to this value so a tenant
    can only ever get a number in their own country, regardless of anything the
    client sends.
    """
    supabase = getattr(current_app, "supabase_client", None)
    if not supabase:
        return default
    try:
        res = (
            supabase.table("tenants")
            .select("country")
            .eq("id", tenant_id)
            .limit(1)
            .execute()
        )
        if res.data and res.data[0].get("country"):
            return str(res.data[0]["country"]).strip().upper()[:2]
    except Exception as e:
        print(f"[WARN] Failed to read tenant country for {tenant_id}: {e}")
    return default


def _tenant_active_number_count(tenant_id):
    """Count phone numbers already attached to a tenant (any status)."""
    supabase = getattr(current_app, "supabase_client", None)
    if not supabase:
        return 0
    try:
        res = (
            supabase.table("phone_numbers")
            .select("id")
            .eq("tenant_id", tenant_id)
            .execute()
        )
        return len(res.data or [])
    except Exception as e:
        print(f"[WARN] Failed to count tenant numbers for {tenant_id}: {e}")
        return 0


@twilio_bp.route('/countries', methods=['GET'])
def list_available_countries():
    """
    List the countries where this Twilio account can buy local numbers.
    Public (no auth) so the signup form can populate its country dropdown.
    Falls back to an empty list on any error; the frontend then uses its
    built-in static list.
    """
    client = current_app.twilio_client
    if not client:
        return jsonify({"countries": []}), 200
    try:
        countries = client.available_phone_numbers.list()
        data = [
            {"code": c.country_code, "name": c.country}
            for c in countries
            if getattr(c, "country_code", None)
        ]
        data.sort(key=lambda x: x["name"] or x["code"])
        return jsonify({"countries": data}), 200
    except Exception as e:
        print(f"[WARN] Failed to list Twilio available countries: {e}")
        return jsonify({"countries": []}), 200


@twilio_bp.route('/phone-numbers', methods=['GET'])
@require_tenant
def get_phone_numbers(user_id, tenant_id, role):
    # Country is locked to the tenant's profile — the client cannot override it.
    country = _get_tenant_country(tenant_id)
    # How many items per page
    page_size = min(int(request.args.get("page_size", 20)), 100)
    # Cursor for next page (may be None/empty on first call)
    page_token = request.args.get("page_token")
    area_code = request.args.get("area_code")

    client = current_app.twilio_client

    params = {
        "page_size": page_size,
    }
    if area_code:
        params["area_code"] = area_code
    if page_token:
        params["page_token"] = page_token

    # Fetch one page of results
    page = client.available_phone_numbers(country).local.page(**params)

    numbers = [
        {"phone_number": n.phone_number, "friendly_name": n.friendly_name}
        for n in page
    ]

    # Twilio page object exposes the URL for the next page; the token is usually
    # a query parameter (e.g. PageToken). Extract or just return the full URL.
    next_page_url = getattr(page, "next_page_url", None)

    return jsonify({
        "country_code": country,
        "available_numbers": numbers,
        "next_page_url": next_page_url,  # or parse out a token and return that
    })


@twilio_bp.route('/phone-numbers/buy', methods=['POST'])
@require_role(['owner', 'admin'])
def buy_phone_number(user_id, tenant_id, role):
    """
    Purchase a phone number and attach the voice agent webhook.
    Body: { "phone_number": "+1..." } for a specific number, OR
          { "area_code": "416", "country": "CA" } for any available in that area.
    """
    data = request.get_json(silent=True) or {}

    # Enforce a single phone number per tenant. If they already have one, block
    # the purchase here (authoritative — the UI hides the buy flow too).
    if _tenant_active_number_count(tenant_id) >= 1:
        return jsonify({
            "error": "Your account already has a phone number. Only one number is allowed per account."
        }), 409

    def _resolve_call_agent_base_http_url():
        base = (Config.WEBSOCKET_BASE_URL or "").strip().rstrip("/")
        if base:
            if base.startswith("wss://"):
                return "https://" + base[len("wss://"):]
            if base.startswith("ws://"):
                return "http://" + base[len("ws://"):]
            if base.startswith("https://") or base.startswith("http://"):
                return base
            return f"https://{base.lstrip('/')}"

        configured_voice = (Config.VOICE_WEBHOOK_URL or "").strip()
        if configured_voice.startswith("https://") or configured_voice.startswith("http://"):
            parsed = urlparse(configured_voice)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}"

        return None

    call_agent_base = _resolve_call_agent_base_http_url()
    voice_url = f"{call_agent_base}/voice-agent/incoming-call" if call_agent_base else Config.VOICE_WEBHOOK_URL
    status_callback = f"{call_agent_base}/voice-agent/call-status" if call_agent_base else getattr(Config, "CALL_STATUS_CALLBACK_URL", None)

    client = current_app.twilio_client
    params = {
        "voice_url": voice_url,
        "voice_method": "POST",
    }
    if status_callback:
        params["status_callback"] = status_callback
        params["status_callback_method"] = "POST"

    phone_number = data.get("phone_number")
    area_code = data.get("area_code")
    # Country is locked to the tenant's profile, never trusted from the client.
    country = _get_tenant_country(tenant_id)

    if phone_number:
        params["phone_number"] = phone_number
    elif area_code:
        params["area_code"] = area_code
    else:
        return jsonify({"error": "Provide either phone_number or area_code"}), 400

    try:
        incoming = client.incoming_phone_numbers.create(**params)
        supabase = getattr(current_app, "supabase_client", None)
        if supabase:
            existing = (
                supabase.table("phone_numbers")
                .select("id")
                .eq("phone_number", incoming.phone_number)
                .limit(1)
                .execute()
            )
            payload = {
                "tenant_id": tenant_id,
                "phone_number": incoming.phone_number,
                "twilio_number_sid": incoming.sid,
                "country_code": country,
                "status": "active",
            }
            if existing.data and len(existing.data) > 0:
                supabase.table("phone_numbers").update(payload).eq("id", existing.data[0]["id"]).execute()
            else:
                supabase.table("phone_numbers").insert(payload).execute()

        return jsonify({
            "sid": incoming.sid,
            "phone_number": incoming.phone_number,
            "friendly_name": incoming.friendly_name,
            "voice_url": incoming.voice_url,
        }), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400
