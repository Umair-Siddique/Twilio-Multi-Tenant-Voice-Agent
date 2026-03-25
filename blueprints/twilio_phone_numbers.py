from flask import Blueprint, request, jsonify, current_app
from urllib.parse import urlparse
from config import Config
from utils.auth_utils import require_role


twilio_bp = Blueprint('twilio', __name__)


@twilio_bp.route('/phone-numbers', methods=['GET'])
def get_phone_numbers():
    country = request.args.get("country", "CA")
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
    country = data.get("country", "CA")

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
