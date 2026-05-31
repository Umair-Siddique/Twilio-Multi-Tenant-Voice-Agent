"""
Minimal voice agent using Twilio ConversationRelay + OpenAI.
Includes call recording and Supabase storage.
Uses only Config.TWILIO_PHONE_NUMBER (no tenant lookup).
"""
import copy
import json
import re
import threading
import time
import traceback
from datetime import datetime, timezone

import requests
from flask import Blueprint, current_app, jsonify, request
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import Connect, ConversationRelay, Language, VoiceResponse

from config import Config
from utils.system_prompt import system_prompt, greeting_prompt
from utils.tenant_google_calendar_credentials import (
    load_credentials_json_for_tenant,
    tenant_may_use_calendar_tools,
)
from utils.tenant_hubspot_credentials import (
    load_hubspot_access_token_for_tenant,
    tenant_may_use_hubspot_tools,
)

voice_agent_bp = Blueprint("voice_agent", __name__)

# Active in-memory sessions keyed by ConversationRelay session id.
active_conversations = {}
recording_started_calls = set()
recording_lock = threading.Lock()

_VOICE_CREDENTIAL_REFRESH_SECONDS = 300

# Tenant config TTL cache — avoids a Supabase round-trip on every call turn.
_tenant_config_cache: dict = {}  # {tenant_id: (config_dict, float_timestamp)}
_TENANT_CONFIG_TTL_SECS = 60


def _normalize_phone(phone):
    if not phone:
        return ""
    return "".join(ch for ch in str(phone).strip() if ch.isdigit() or ch == "+")


def _resolve_base_ws_url():
    base_url = (Config.WEBSOCKET_BASE_URL or "").strip().rstrip("/")

    # Prefer the request host for local/ngrok testing so webhook + websocket stay aligned.
    host = request.headers.get("X-Forwarded-Host") or request.host or ""
    host_lower = host.lower()
    is_local_or_tunnel = (
        "localhost" in host_lower
        or "127.0.0.1" in host_lower
        or "ngrok" in host_lower
    )
    if is_local_or_tunnel:
        forwarded_proto = (request.headers.get("X-Forwarded-Proto") or "").lower()
        scheme = "wss" if forwarded_proto == "https" or request.is_secure else "ws"
        return f"{scheme}://{host}"

    if not base_url or base_url == "wss://your-domain.com":
        scheme = "wss" if request.is_secure else "ws"
        return f"{scheme}://{request.host}"
    if base_url.startswith("https://"):
        return "wss://" + base_url[len("https://"):]
    if base_url.startswith("http://"):
        return "ws://" + base_url[len("http://"):]
    if not (base_url.startswith("wss://") or base_url.startswith("ws://")):
        return f"wss://{base_url.lstrip('/')}"
    return base_url


def _resolve_base_http_url():
    """
    Resolve a publicly reachable HTTP(S) base URL for Twilio callbacks.
    Prioritizes current request host for local/ngrok usage.
    """
    host = request.headers.get("X-Forwarded-Host") or request.host or ""
    host_lower = host.lower()
    is_local_or_tunnel = (
        "localhost" in host_lower
        or "127.0.0.1" in host_lower
        or "ngrok" in host_lower
    )
    if is_local_or_tunnel:
        forwarded_proto = (request.headers.get("X-Forwarded-Proto") or "").lower()
        scheme = "https" if forwarded_proto == "https" or request.is_secure else "http"
        return f"{scheme}://{host}"

    configured = (Config.VOICE_WEBHOOK_URL or "").strip()
    if configured.startswith("https://") or configured.startswith("http://"):
        return configured.split("/voice-agent/", 1)[0].rstrip("/")

    scheme = "https" if request.is_secure else "http"
    return f"{scheme}://{request.host}"


def _get_twilio_request_url() -> str:
    """Reconstruct the public URL Twilio signed, honouring reverse-proxy headers."""
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "").strip()
    forwarded_host  = request.headers.get("X-Forwarded-Host", "").strip()
    if forwarded_proto and forwarded_host:
        url = f"{forwarded_proto}://{forwarded_host}{request.path}"
        if request.query_string:
            url += f"?{request.query_string.decode('utf-8')}"
        return url
    return request.url


def _is_valid_twilio_request() -> bool:
    """
    Validate the X-Twilio-Signature header.
    Returns True when valid, or when TWILIO_AUTH_TOKEN is not set (dev/test mode).
    """
    if not Config.TWILIO_AUTH_TOKEN:
        return True
    validator = RequestValidator(Config.TWILIO_AUTH_TOKEN)
    url       = _get_twilio_request_url()
    post_data = request.form.to_dict() if request.method == "POST" else {}
    signature = request.headers.get("X-Twilio-Signature", "")
    return validator.validate(url, post_data, signature)


def _resolve_tenant_id_for_number(to_number):
    """Resolve tenant_id from phone_numbers mapping using Twilio To number."""
    supabase = getattr(current_app, "supabase_client", None)
    if not supabase or not to_number:
        return None
    normalized_to = _normalize_phone(to_number)
    try:
        # Try exact match first (most common for E.164 stored values).
        row = (
            supabase.table("phone_numbers")
            .select("tenant_id, phone_number, status")
            .eq("phone_number", to_number)
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        if row.data and len(row.data) > 0:
            return row.data[0]["tenant_id"]

        # Fallback: compare normalized values in Python.
        all_rows = supabase.table("phone_numbers").select("tenant_id, phone_number, status").execute()
        for item in all_rows.data or []:
            if item.get("status") == "active" and _normalize_phone(item.get("phone_number")) == normalized_to:
                return item.get("tenant_id")
    except Exception as e:
        print(f"[TenantResolution] Failed for number={to_number}: {e}")
    return None


def _resolve_tenant_id_for_call_sid(call_sid):
    """Resolve tenant_id from calls table using call_sid."""
    supabase = getattr(current_app, "supabase_client", None)
    if not supabase or not call_sid:
        return None
    try:
        row = (
            supabase.table("calls")
            .select("tenant_id")
            .eq("call_sid", call_sid)
            .limit(1)
            .execute()
        )
        if row.data and len(row.data) > 0:
            return row.data[0].get("tenant_id")
    except Exception as e:
        print(f"[TenantResolution] Failed for call_sid={call_sid}: {e}")
    return None


def _get_tenant_agent_config(tenant_id):
    """Load tenant-specific voice agent configuration, including industry from the tenants table."""
    cached = _tenant_config_cache.get(tenant_id)
    if cached:
        config, cached_at = cached
        if time.time() - cached_at < _TENANT_CONFIG_TTL_SECS:
            return config

    supabase = getattr(current_app, "supabase_client", None)
    if not supabase or not tenant_id:
        return {}
    try:
        row = (
            supabase.table("tenant_agent_config")
            .select("greeting, tone, system_prompt, custom_prompts, allowed_actions, voice_id")
            .eq("tenant_id", tenant_id)
            .limit(1)
            .execute()
        )
        config = dict(row.data[0]) if row.data else {}
        try:
            tenant_row = (
                supabase.table("tenants")
                .select("industry")
                .eq("id", tenant_id)
                .limit(1)
                .execute()
            )
            if tenant_row.data:
                config["industry"] = tenant_row.data[0].get("industry") or None
        except Exception:
            pass
        _tenant_config_cache[tenant_id] = (config, time.time())
        return config
    except Exception as e:
        print(f"[TenantConfig] Failed loading tenant config for {tenant_id}: {e}")
    return {}


def _build_system_prompt_for_tenant(tenant_config):
    """Build final system prompt from tenant override or global default, plus tone/custom_prompts."""
    tenant_override = ((tenant_config or {}).get("system_prompt") or "").strip()
    base_prompt = tenant_override if tenant_override else (system_prompt or "").strip()

    # Prepend a language enforcement rule so the model always replies in the system prompt's language.
    language_rule = (
        "CRITICAL LANGUAGE RULE: You MUST always respond in the exact same language as this system prompt "
        "and the welcome greeting — no exceptions. Even if the caller speaks a completely different language, "
        "you MUST reply only in your configured language. Never switch languages under any circumstances."
    )
    final_prompt = language_rule + "\n\n" + base_prompt

    industry = ((tenant_config or {}).get("industry") or "").strip()
    if industry:
        final_prompt += (
            f"\n\nThis assistant is tailored for the {industry} industry. "
            f"Always use any connected integrations (such as Google Calendar or HubSpot) to help callers — "
            f"these tools are available for all callers regardless of topic. "
            f"For general knowledge questions unrelated to {industry} and not achievable through an integration, "
            f"politely note your focus area: say something like 'I'm primarily focused on {industry}, but let me "
            f"see how I can help.' Never refuse to use an available tool just because the topic seems off-topic."
        )

    tone = (tenant_config or {}).get("tone")
    if tone:
        final_prompt += f"\n\nTenant tone requirement: Use a {tone} tone consistently."

    custom_prompts = (tenant_config or {}).get("custom_prompts")
    if isinstance(custom_prompts, dict) and custom_prompts:
        prompt_chunks = []
        for key in sorted(custom_prompts.keys()):
            value = custom_prompts.get(key)
            if value is not None and str(value).strip():
                prompt_chunks.append(f"{key}: {value}")
        if prompt_chunks:
            final_prompt += "\n\nTenant custom instructions:\n" + "\n".join(prompt_chunks)
    elif isinstance(custom_prompts, str) and custom_prompts.strip():
        final_prompt += f"\n\nTenant custom instructions:\n{custom_prompts.strip()}"

    return final_prompt or system_prompt


_VOICE_CALENDAR_OPENAI_CACHE = None
_VOICE_HUBSPOT_OPENAI_CACHE = None


def _strip_credentials_from_openai_tool(definition: dict) -> dict:
    """Remove credentials_json from tool schema; server injects it on execution."""
    out = copy.deepcopy(definition)
    fn = out.get("function") or {}
    params = dict(fn.get("parameters") or {})
    props = dict(params.get("properties") or {})
    props.pop("credentials_json", None)
    params["properties"] = props
    req = [x for x in (params.get("required") or []) if x != "credentials_json"]
    if req:
        params["required"] = req
    else:
        params.pop("required", None)
    fn["parameters"] = params
    out["function"] = fn
    return out


def _strip_access_token_from_openai_tool(definition: dict) -> dict:
    """Remove access_token from tool schema; server injects it on execution."""
    out = copy.deepcopy(definition)
    fn = out.get("function") or {}
    params = dict(fn.get("parameters") or {})
    props = dict(params.get("properties") or {})
    props.pop("access_token", None)
    params["properties"] = props
    req = [x for x in (params.get("required") or []) if x != "access_token"]
    if req:
        params["required"] = req
    else:
        params.pop("required", None)
    fn["parameters"] = params
    out["function"] = fn
    return out


def _voice_calendar_tool_defs_and_map():
    global _VOICE_CALENDAR_OPENAI_CACHE
    if _VOICE_CALENDAR_OPENAI_CACHE is not None:
        return _VOICE_CALENDAR_OPENAI_CACHE
    from langchain_core.utils.function_calling import convert_to_openai_tool

    from tools.google_calendar_tools import create_event, get_freebusy, list_events, quick_add_event

    raw_tools = [list_events, create_event, quick_add_event, get_freebusy]
    defs = []
    by_name = {}
    for t in raw_tools:
        d = convert_to_openai_tool(t)
        defs.append(_strip_credentials_from_openai_tool(d))
        by_name[t.name] = t
    _VOICE_CALENDAR_OPENAI_CACHE = (defs, by_name)
    return _VOICE_CALENDAR_OPENAI_CACHE


def _voice_hubspot_tool_defs_and_map():
    global _VOICE_HUBSPOT_OPENAI_CACHE
    if _VOICE_HUBSPOT_OPENAI_CACHE is not None:
        return _VOICE_HUBSPOT_OPENAI_CACHE
    from langchain_core.utils.function_calling import convert_to_openai_tool

    from tools.hubspot_tools import HUBSPOT_VOICE_TOOLS

    defs = []
    by_name = {}
    for t in HUBSPOT_VOICE_TOOLS:
        d = convert_to_openai_tool(t)
        defs.append(_strip_access_token_from_openai_tool(d))
        by_name[t.name] = t
    _VOICE_HUBSPOT_OPENAI_CACHE = (defs, by_name)
    return _VOICE_HUBSPOT_OPENAI_CACHE


def _voice_end_call_openai_tool():
    """OpenAI function schema: model requests hangup when the caller is clearly done."""
    return {
        "type": "function",
        "function": {
            "name": "end_call",
            "description": (
                "End the phone call. Use only when the caller clearly wants to hang up or is finished "
                "(goodbye, thanks bye, that is all, talk later, no more questions, etc.). "
                "Do not use if they may still need help. Give a brief spoken goodbye in the same turn when possible."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Short note, e.g. user said goodbye.",
                    }
                },
            },
        },
    }


def _warmup_openai_async(app):
    """Pre-warm the OpenAI HTTP connection during greeting playback so the first real query is fast."""
    def run():
        with app.app_context():
            try:
                client = getattr(app, "openai_client", None)
                if not client:
                    return
                model = Config.OPENAI_VOICE_FAST_MODEL or Config.OPENAI_MODEL
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "."}],
                    max_tokens=1,
                    temperature=0,
                )
            except Exception:
                pass
    threading.Thread(target=run, daemon=True).start()


def _hangup_twilio_call(call_sid):
    """Terminate an in-progress Twilio call (parent leg)."""
    if not call_sid:
        return
    twilio_client = getattr(current_app, "twilio_client", None)
    if not twilio_client:
        print(f"[Hangup] No Twilio client; cannot end CallSid={call_sid}")
        return
    try:
        twilio_client.calls(call_sid).update(status="completed")
        print(f"[Hangup] CallSid={call_sid} status set to completed")
    except Exception as exc:
        print(f"[Hangup] Failed for CallSid={call_sid}: {exc}")


def _drain_tool_cycle_user_prompts(ws, max_duration_ms=None):
    """
    After a calendar/CRM tool round, discard user speech that was captured while
    the assistant was still checking (avoids double-processing barged-in audio).
    Returns 'stop' if the session ended, otherwise None.
    """
    ms = float(
        max_duration_ms
        if max_duration_ms is not None
        else getattr(Config, "VOICE_POST_TOOL_DRAIN_MS", None)
        or 750
    )
    deadline = time.monotonic() + max(ms, 0) / 1000.0
    consecutive_empty = 0
    while time.monotonic() < deadline:
        try:
            raw = ws.receive(timeout=0.05)
        except Exception as exc:
            if "timeout" in str(exc).lower():
                consecutive_empty += 1
                if consecutive_empty >= 4:
                    break
                continue
            break
        if not raw:
            consecutive_empty += 1
            if consecutive_empty >= 4:
                break
            continue
        consecutive_empty = 0
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        event_type = data.get("event") or data.get("type")
        if event_type in {"stop", "error"}:
            return "stop"
        if event_type in {"media", "prompt", "input"}:
            print(f"[ConversationRelay] Dropping buffered user input after tool wait: {data}")
            continue
        print(f"[ConversationRelay] Ignoring non-user event during post-tool drain: {event_type}")
    return None


def _init_voice_session_state(tenant_id, tenant_config, call_sid):
    """Initial messages + optional integration credentials for tool calling."""
    tenant_system_prompt = _build_system_prompt_for_tenant(tenant_config)
    supabase = getattr(current_app, "supabase_client", None)
    allowed = tenant_config.get("allowed_actions")

    cred_json = None
    if tenant_id and supabase and tenant_may_use_calendar_tools(allowed):
        cred_json = load_credentials_json_for_tenant(supabase, tenant_id)

    hubspot_token = None
    if tenant_id and supabase and tenant_may_use_hubspot_tools(allowed):
        hubspot_token = load_hubspot_access_token_for_tenant(supabase, tenant_id)

    suffix_parts = []
    if cred_json:
        suffix_parts.append(
            "\n\nThis company has connected Google Calendar for this assistant. "
            "Use the provided tools to list events, check availability, create events, or quick-add events. "
            "To create or update a calendar event you MUST call the appropriate tool in this turn; "
            "never claim an event was created, updated, or deleted until a tool returns success. "
            "Never tell the caller you cannot access Calendar if the tools succeed. "
            "For new events, confirm title, date, start/end time (or duration), and timezone when unclear. "
            "Keep replies short and natural for a phone call."
        )
    if hubspot_token:
        suffix_parts.append(
            "\n\nThis company has connected HubSpot CRM for this assistant. "
            "Use the HubSpot tools to find or update contacts and companies, log call notes, and manage "
            "support tickets when relevant. Never ask the caller for API keys. "
            "Summarize tool results briefly for a phone conversation."
        )
    suffix_parts.append(
        "\n\nWhen the caller clearly wants to end the conversation (goodbye, thanks, that is all, "
        "speak later, etc.), give a brief polite closing in your spoken reply and call the end_call function "
        "so the phone line can disconnect. Do not use end_call if they may still need help."
    )
    suffix = "".join(suffix_parts)

    return {
        "messages": [{"role": "system", "content": tenant_system_prompt + suffix}],
        "call_sid": call_sid,
        "tenant_id": tenant_id,
        "tenant_config": tenant_config,
        "credentials_json": cred_json,
        "hubspot_access_token": hubspot_token,
        "integration_last_refresh_ts": time.time(),
    }


# def _build_system_prompt():
#     default_prompt = (
#         "You are a helpful company assistant on a phone call. "
#         "Speak naturally, be concise, and ask clarifying questions when needed."
#     )
#     return Config.COMPANY_ASSISTANT_PROMPT or default_prompt



def _create_call_record(call_sid, from_number, to_number, direction="inbound"):
    """Create call record in database for recording association."""
    supabase = getattr(current_app, "supabase_client", None)
    if not supabase:
        return None
    try:
        # Reuse existing call row if already inserted for this call SID.
        existing = (
            supabase.table("calls")
            .select("id")
            .eq("call_sid", call_sid)
            .limit(1)
            .execute()
        )
        if existing.data and len(existing.data) > 0:
            return existing.data[0]["id"]

        tenant_id = _resolve_tenant_id_for_number(to_number)

        payload = {
            "call_sid": call_sid,
            "from_number": from_number,
            "to_number": to_number,
            "direction": direction,
            "status": "ringing",
        }
        if tenant_id:
            payload["tenant_id"] = tenant_id
        else:
            print(f"[Call Record] No tenant mapping found for number {to_number}; continuing in non-tenant mode")

        row = supabase.table("calls").insert(payload).execute()
        if row.data and len(row.data) > 0:
            return row.data[0]["id"]
    except Exception as e:
        print(f"Failed to create call record: {e}")
    return None


def _start_call_recording_if_needed(call_sid):
    """
    Start recording an in-progress call via Twilio REST API.
    Uses a lock + in-memory set to avoid duplicate start attempts.
    """
    if not call_sid:
        return
    twilio_client = getattr(current_app, "twilio_client", None)
    if not twilio_client:
        return

    with recording_lock:
        if call_sid in recording_started_calls:
            return
        recording_started_calls.add(call_sid)

    try:
        base_http = _resolve_base_http_url().rstrip("/")
        callback_url = f"{base_http}/voice-agent/recording-status"
        twilio_client.calls(call_sid).recordings.create(
            recording_status_callback=callback_url,
            recording_status_callback_method="POST",
        )
        print(f"[Recording] Started recording for call {call_sid}; callback={callback_url}")
    except Exception as e:
        # Allow retry on next status callback if this attempt fails.
        with recording_lock:
            recording_started_calls.discard(call_sid)
        print(f"[Recording] Failed to start recording for call {call_sid}: {e}")


@voice_agent_bp.route("/incoming-call", methods=["POST"])
def handle_incoming_call():
    """
    Twilio webhook entrypoint. Only accepts calls to Config.TWILIO_PHONE_NUMBER.
    """
    if not _is_valid_twilio_request():
        print("[Security] /incoming-call rejected — invalid Twilio signature")
        return "Forbidden", 403

    try:
        to_number = request.form.get("To")
        from_number = request.form.get("From", "")
        call_sid = request.form.get("CallSid", "")

        tenant_id = _resolve_tenant_id_for_number(to_number)
        if not tenant_id:
            response = VoiceResponse()
            response.say("This number is not configured for the assistant. Goodbye.")
            response.hangup()
            return str(response), 200, {"Content-Type": "text/xml"}

        # Create call record (non-blocking - failures won't affect the call)
        try:
            if call_sid and getattr(current_app, "supabase_client", None):
                _create_call_record(call_sid, from_number, to_number)
        except Exception as db_error:
            print(f"[Call Record] Failed to create call record (call will continue): {db_error}")

        # Build ConversationRelay WebSocket URL and greeting (no recording API calls here).
        ws_url = f"{_resolve_base_ws_url()}/voice-agent/websocket?tenant_id={tenant_id}&call_sid={call_sid}"
        print(
            f"[ConversationRelay] CallSid={call_sid} using ws_url={ws_url} "
            f"(host={request.host}, forwarded_host={request.headers.get('X-Forwarded-Host')}, "
            f"forwarded_proto={request.headers.get('X-Forwarded-Proto')})"
        )
        tenant_config = _get_tenant_agent_config(tenant_id)
        greeting = tenant_config.get("greeting") or greeting_prompt

        from utils.voice_catalog import get_voice_by_id, get_default_voice
        voice = get_voice_by_id(tenant_config.get("voice_id")) or get_default_voice()

        connect = Connect()
        conversation_relay = ConversationRelay(
            url=ws_url,
            interruptible=True,
            welcome_greeting=greeting,
        )
        conversation_relay.append(Language(
            code=voice["language_code"],
            tts_provider=voice["twilio_tts_provider"],
            voice=voice["twilio_voice"],
        ))
        connect.append(conversation_relay)
        response = VoiceResponse()
        response.append(connect)

        return str(response), 200, {"Content-Type": "text/xml"}
    except Exception as exc:
        print(f"Error handling incoming call: {exc}")
        traceback.print_exc()
        response = VoiceResponse()
        response.say("Sorry, an error occurred. Please try again later.")
        response.hangup()
        return str(response), 200, {"Content-Type": "text/xml"}



@voice_agent_bp.route("/call-status", methods=["GET", "POST"])
def handle_call_status():
    """
    Twilio status callback. Logs call lifecycle events.
    On completed calls, triggers a fallback recording poll + store flow.
    """
    if not _is_valid_twilio_request():
        print("[Security] /call-status rejected — invalid Twilio signature")
        return "Forbidden", 403

    call_status = request.values.get("CallStatus")
    call_sid = request.values.get("CallSid")
    to_number = request.values.get("To")
    from_number = request.values.get("From")
    print(f"[CallStatus] CallSid={call_sid}, Status={call_status}")

    # Keep calls table lifecycle in sync.
    try:
        supabase = getattr(current_app, "supabase_client", None)
        if supabase and call_sid and call_status:
            update_data = {"status": call_status}
            status_lower = call_status.lower()
            if status_lower == "in-progress":
                update_data["start_time"] = datetime.now(timezone.utc).isoformat()
            if status_lower == "completed":
                update_data["end_time"] = datetime.now(timezone.utc).isoformat()
                duration = request.values.get("CallDuration")
                if duration:
                    try:
                        update_data["duration_seconds"] = int(duration)
                    except Exception:
                        pass

            # Ensure call row exists before update for cases where /incoming-call insert failed.
            if not (
                supabase.table("calls").select("id").eq("call_sid", call_sid).limit(1).execute().data
            ):
                _create_call_record(call_sid, from_number, to_number)

            supabase.table("calls").update(update_data).eq("call_sid", call_sid).execute()
    except Exception as e:
        print(f"[CallStatus] Failed updating call row for {call_sid}: {e}")

    # Start recording as soon as Twilio marks call in-progress.
    if call_sid and (call_status or "").lower() == "in-progress":
        _start_call_recording_if_needed(call_sid)

    # Fallback: when call completes, poll Twilio recordings and persist if available.
    if call_sid and (call_status or "").lower() == "completed":
        # Free the in-memory recording guard so it doesn't grow unbounded.
        with recording_lock:
            recording_started_calls.discard(call_sid)
        # Also evict cached tenant config for this call's number (stale after call ends).
        to_number = request.values.get("To")
        if to_number:
            tenant_id = _resolve_tenant_id_for_number(to_number)
            if tenant_id:
                _tenant_config_cache.pop(tenant_id, None)
        try:
            _fetch_and_store_recording_async(call_sid)
        except Exception as e:
            print(f"[Recording] Failed to start recording polling for call {call_sid}: {e}")

    return "", 200


def _download_twilio_recording(recording_url):
    """Download recording MP3 from Twilio (requires Basic Auth). Use .mp3 URL to get audio, not JSON."""
    auth = (Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
    media_url = recording_url.rstrip("/")
    if media_url.endswith(".json"):
        media_url = media_url[:-5] + ".mp3"
    elif not media_url.endswith(".mp3"):
        media_url = media_url + ".mp3"
    resp = requests.get(media_url, auth=auth, timeout=60)
    resp.raise_for_status()
    return resp.content


def _upload_to_supabase(bucket, path, content, content_type="audio/mpeg"):
    """Upload bytes to Supabase Storage."""
    supabase = getattr(current_app, "supabase_client", None)
    if not supabase:
        return None
    try:
        supabase.storage.from_(bucket).upload(
            path,
            content,
            {
                "content-type": content_type,
                # storage3 expects header values as strings
                "upsert": "true",
            },
        )
        return path
    except Exception as e:
        print(f"[Recording] Supabase upload error: {e}")
        traceback.print_exc()
        return None


def _save_recording_for_call(call_sid, recording_sid, recording_url, recording_duration=None):
    """Persist Twilio recording bytes to Supabase storage + recordings table."""
    supabase = getattr(current_app, "supabase_client", None)
    if not supabase:
        print("[Recording] No Supabase client")
        return False

    call_id = None
    tenant_id = None
    try:
        call_row = supabase.table("calls").select("id, tenant_id").eq("call_sid", call_sid).execute()
        if call_row.data and len(call_row.data) > 0:
            call_id = call_row.data[0].get("id")
            tenant_id = call_row.data[0].get("tenant_id")
    except Exception as e:
        print(f"[Recording] Could not load call row for {call_sid}: {e}")

    try:
        existing = (
            supabase.table("recordings")
            .select("id")
            .eq("twilio_recording_sid", recording_sid)
            .limit(1)
            .execute()
        )
        if existing.data and len(existing.data) > 0:
            print(f"[Recording] Recording {recording_sid} already stored, skipping duplicate")
            return True
    except Exception as e:
        print(f"[Recording] Could not check existing recording rows (continuing): {e}")

    bucket = Config.RECORDINGS_BUCKET
    if tenant_id:
        storage_path = f"tenants/{tenant_id}/recordings/{recording_sid}.mp3"
    else:
        storage_path = f"recordings/{call_sid}/{recording_sid}.mp3"

    print(f"[Recording] Downloading from Twilio: {recording_url[:60]}...")
    audio_bytes = _download_twilio_recording(recording_url)
    if not audio_bytes:
        print("[Recording] Download returned no bytes")
        return False

    print(f"[Recording] Uploading to Supabase bucket={bucket} path={storage_path}")
    if _upload_to_supabase(bucket, storage_path, audio_bytes):
        if call_id:
            payload = {
                "call_id": call_id,
                "twilio_recording_sid": recording_sid,
                "recording_url": recording_url,
                "storage_path": storage_path,
                "duration_seconds": int(recording_duration) if recording_duration else None,
                "status": "available",
            }
            if tenant_id:
                payload["tenant_id"] = tenant_id
            try:
                supabase.table("recordings").insert(payload).execute()
            except Exception as e:
                print(f"[Recording] File uploaded but recordings row insert failed: {e}")
        else:
            print(f"[Recording] File uploaded without DB row (no call row found for {call_sid})")
        print(f"[Recording] Saved to Supabase: {storage_path}")
        return True
    return False


def _fetch_and_store_recording_async(call_sid, max_attempts=6, delay_seconds=5):
    """
    Fallback recording flow: when call completes, poll Twilio recordings API for this call,
    then store the first completed recording.
    """
    app = current_app._get_current_object()

    def run():
        with app.app_context():
            twilio_client = getattr(app, "twilio_client", None)
            if not twilio_client:
                print(f"[Recording] No Twilio client available for call {call_sid}")
                return

            for attempt in range(1, max_attempts + 1):
                try:
                    recordings = twilio_client.recordings.list(call_sid=call_sid, limit=10)
                    print(
                        f"[Recording] Poll attempt {attempt} for call {call_sid}: "
                        f"found {len(recordings)} recording(s)"
                    )
                    completed = next(
                        (r for r in recordings if (getattr(r, "status", "") or "").lower() == "completed"),
                        None
                    )
                    if completed:
                        recording_sid = completed.sid
                        recording_duration = getattr(completed, "duration", None)
                        recording_uri = getattr(completed, "uri", "") or ""
                        recording_url = f"https://api.twilio.com{recording_uri}".replace(".json", "")
                        print(
                            f"[Recording] Found completed recording for call {call_sid} "
                            f"on attempt {attempt}: {recording_sid}"
                        )
                        _save_recording_for_call(
                            call_sid=call_sid,
                            recording_sid=recording_sid,
                            recording_url=recording_url,
                            recording_duration=recording_duration,
                        )
                        return
                except Exception as e:
                    print(f"[Recording] Poll attempt {attempt} failed for call {call_sid}: {e}")

                time.sleep(delay_seconds)

            print(f"[Recording] No completed recording found for call {call_sid} after {max_attempts} attempts")

    threading.Thread(target=run, daemon=True).start()


@voice_agent_bp.route("/recording-status", methods=["GET", "POST"])
def handle_recording_status():
    """
    Twilio recording status callback. Download recording and upload to Supabase.
    """
    if not _is_valid_twilio_request():
        print("[Security] /recording-status rejected — invalid Twilio signature")
        return "Forbidden", 403

    try:
        call_sid = request.values.get("CallSid")
        recording_sid = request.values.get("RecordingSid")
        recording_url = request.values.get("RecordingUrl")
        recording_status = request.values.get("RecordingStatus")
        recording_duration = request.values.get("RecordingDuration")
        print(f"[Recording] recording-status received: CallSid={call_sid}, RecordingSid={recording_sid}, Status={recording_status}")

        if (recording_status or "").lower() != "completed" or not call_sid or not recording_sid or not recording_url:
            return "", 200

        _save_recording_for_call(
            call_sid=call_sid,
            recording_sid=recording_sid,
            recording_url=recording_url,
            recording_duration=recording_duration,
        )
    except Exception as exc:
        print(f"[Recording] Error processing recording: {exc}")
        traceback.print_exc()
    return "", 200


@voice_agent_bp.route("/websocket", methods=["GET"])
def websocket_route():
    return "WebSocket endpoint available at /voice-agent/websocket", 200


def register_websocket(app):
    @app.sock.route("/voice-agent/websocket")
    def handle_websocket(ws):
        session_id = None
        ws_tenant_id = request.args.get("tenant_id")
        ws_call_sid = request.args.get("call_sid")
        try:
            while True:
                raw_message = ws.receive(timeout=1)
                if not raw_message:
                    continue

                data = json.loads(raw_message)
                event_type = data.get("event") or data.get("type")
                print(f"[ConversationRelay] Incoming message: {data}")

                if event_type in {"connected", "setup", "start"}:
                    session_id = data.get("sessionId")
                    if session_id:
                        if len(active_conversations) >= Config.MAX_CONCURRENT_SESSIONS:
                            print(
                                f"[ConversationRelay] Session cap ({Config.MAX_CONCURRENT_SESSIONS}) reached; "
                                f"rejecting session_id={session_id}"
                            )
                            break
                        call_sid = data.get("callSid") or ws_call_sid
                        tenant_id = ws_tenant_id or _resolve_tenant_id_for_call_sid(call_sid)
                        tenant_config = _get_tenant_agent_config(tenant_id) if tenant_id else {}
                        active_conversations[session_id] = _init_voice_session_state(
                            tenant_id, tenant_config, call_sid
                        )
                        print(f"[ConversationRelay] Session started session_id={session_id} call_sid={call_sid}")
                        # Pre-warm OpenAI connection while greeting plays so first user query is fast.
                        _warmup_openai_async(current_app._get_current_object())
                        # Some Twilio number configurations only send "completed" status callback.
                        # Start recording from ConversationRelay setup to avoid missing recordings.
                        if call_sid:
                            _start_call_recording_if_needed(call_sid)

                elif event_type in {"media", "prompt", "input"}:
                    transcript = (
                        data.get("text")
                        or data.get("prompt")
                        or data.get("voicePrompt")
                        or data.get("transcript")
                        or ""
                    ).strip()
                    if not transcript or not session_id:
                        continue
                    if session_id not in active_conversations:
                        call_sid = data.get("callSid") or ws_call_sid
                        tenant_id = ws_tenant_id or _resolve_tenant_id_for_call_sid(call_sid)
                        tenant_config = _get_tenant_agent_config(tenant_id) if tenant_id else {}
                        active_conversations[session_id] = _init_voice_session_state(
                            tenant_id, tenant_config, call_sid
                        )

                    messages = active_conversations[session_id]["messages"]
                    messages.append({"role": "user", "content": transcript})

                    tid = active_conversations[session_id].get("tenant_id")
                    cred_json = active_conversations[session_id].get("credentials_json")
                    hubspot_token = active_conversations[session_id].get("hubspot_access_token")
                    tenant_cfg = active_conversations[session_id].get("tenant_config") or {}
                    allowed_actions = tenant_cfg.get("allowed_actions")

                    tools_enabled = bool(
                        (cred_json and tenant_may_use_calendar_tools(allowed_actions))
                        or (hubspot_token and tenant_may_use_hubspot_tools(allowed_actions))
                    )
                    needs_tools = _voice_may_use_tools(messages, transcript)
                    wants_tools = tools_enabled and needs_tools

                    # If tools are needed but not connected, reply immediately without calling OpenAI.
                    if needs_tools and not tools_enabled:
                        not_connected_msg = _tools_not_connected_message(
                            transcript, bool(cred_json), bool(hubspot_token)
                        )
                        if not_connected_msg:
                            messages.append({"role": "assistant", "content": not_connected_msg})
                            ws.send(json.dumps({"type": "text", "token": not_connected_msg, "last": True}))
                            print(f"[ConversationRelay] Tools not connected reply: {not_connected_msg}")
                            continue

                    # Refresh integration secrets occasionally (not every turn).
                    if tools_enabled and wants_tools:
                        now = time.time()
                        last = float(active_conversations[session_id].get("integration_last_refresh_ts") or 0)
                        if (now - last) >= _VOICE_CREDENTIAL_REFRESH_SECONDS:
                            supabase = getattr(current_app, "supabase_client", None)
                            if tid and supabase:
                                if tenant_may_use_calendar_tools(allowed_actions):
                                    cred_json = load_credentials_json_for_tenant(supabase, tid)
                                    active_conversations[session_id]["credentials_json"] = cred_json
                                if tenant_may_use_hubspot_tools(allowed_actions):
                                    hubspot_token = load_hubspot_access_token_for_tenant(supabase, tid)
                                    active_conversations[session_id]["hubspot_access_token"] = hubspot_token
                                active_conversations[session_id]["integration_last_refresh_ts"] = now

                    hangup_state = {"requested": False}
                    is_goodbye = _voice_is_likely_goodbye(transcript)

                    # Stream tokens directly into ConversationRelay for minimum first-token latency.
                    # The "one moment" filler is sent from inside get_openai_response only when a
                    # real integration tool call actually fires — never for unconfigured tenants.
                    ai_response = get_openai_response(
                        messages,
                        ws=ws,
                        credentials_json=cred_json if wants_tools else None,
                        hubspot_access_token=hubspot_token if wants_tools else None,
                        hangup_state=hangup_state,
                        include_end_call=wants_tools or is_goodbye,
                    )
                    messages.append({"role": "assistant", "content": ai_response})
                    print(f"[ConversationRelay] Sent assistant text: {ai_response}")

                    if wants_tools:
                        drain_outcome = _drain_tool_cycle_user_prompts(ws)
                        if drain_outcome == "stop":
                            if session_id and session_id in active_conversations:
                                call_sid_stop = active_conversations[session_id].get("call_sid")
                                if call_sid_stop:
                                    print(
                                        f"[Recording] WebSocket stop during post-tool drain. "
                                        f"Triggering recording poll for call {call_sid_stop}"
                                    )
                                    _fetch_and_store_recording_async(call_sid_stop)
                            break

                    if hangup_state.get("requested"):
                        hangup_call_sid = active_conversations[session_id].get("call_sid")
                        app_obj = current_app._get_current_object()

                        def _delayed_hangup():
                            time.sleep(2.5)
                            with app_obj.app_context():
                                _hangup_twilio_call(hangup_call_sid)

                        threading.Thread(target=_delayed_hangup, daemon=True).start()

                elif event_type in {"stop", "error"}:
                    # Fallback trigger: when websocket session ends, try to fetch/store Twilio recording.
                    if session_id and session_id in active_conversations:
                        call_sid = active_conversations[session_id].get("call_sid")
                        if call_sid:
                            print(f"[Recording] WebSocket ended. Triggering recording poll for call {call_sid}")
                            _fetch_and_store_recording_async(call_sid)
                    break
        except Exception as exc:
            if "timeout" not in str(exc).lower() and "Connection closed: 1000" not in str(exc):
                print(f"WebSocket error: {exc}")
                traceback.print_exc()
        finally:
            if session_id and session_id in active_conversations:
                del active_conversations[session_id]
            # Clean up the recording guard for this call so the set doesn't grow.
            if ws_call_sid:
                with recording_lock:
                    recording_started_calls.discard(ws_call_sid)


def _ws_send_safe(ws, payload):
    if ws is None:
        return False
    try:
        ws.send(json.dumps(payload))
        return True
    except Exception:
        return False


# Markdown / formatting characters that get read aloud literally by TTS providers.
# Stripping per-character is safe even mid-stream (no multi-char state required).
_TTS_STRIP_CHARS = str.maketrans("", "", "*_`#~|<>{}[]\\")
_TTS_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_TTS_MULTISPACE_RE = re.compile(r"  +")


def _sanitize_for_tts_stream(text):
    """Strip markdown chars from a streamed delta. Safe per-chunk."""
    if not text:
        return text
    return text.translate(_TTS_STRIP_CHARS)


def _sanitize_for_tts_full(text):
    """Full-text sanitization for non-streamed sends — also rewrites multi-char markdown."""
    if not text:
        return text
    text = _TTS_MD_LINK_RE.sub(r"\1", text)  # [label](url) -> label
    text = text.translate(_TTS_STRIP_CHARS)
    text = _TTS_MULTISPACE_RE.sub(" ", text)
    return text.strip()


def _send_full_text(ws, text):
    clean = _sanitize_for_tts_full(text)
    if not clean:
        return
    _ws_send_safe(ws, {"type": "text", "token": clean, "last": True})


def _stream_chat_to_ws(client, ws, *, model, msgs, temperature, max_tokens, timeout):
    """Stream a non-tool chat completion; emit each delta to ConversationRelay immediately."""
    chunks = []
    ws_ok = ws is not None
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=msgs,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            stream=True,
        )
        for chunk in stream:
            try:
                delta = chunk.choices[0].delta.content
            except (IndexError, AttributeError):
                continue
            if not delta:
                continue
            clean = _sanitize_for_tts_stream(delta)
            if not clean:
                continue
            chunks.append(clean)
            if ws_ok and not _ws_send_safe(ws, {"type": "text", "token": clean, "last": False}):
                ws_ok = False
    except Exception as exc:
        print(f"[Stream] OpenAI streaming failed: {exc}")
        traceback.print_exc()

    full = "".join(chunks).strip() or "Could you repeat that, please?"
    if ws_ok:
        _ws_send_safe(ws, {"type": "text", "token": "", "last": True})
    elif ws is not None and not chunks:
        # Streaming produced nothing and ws is still open: deliver fallback.
        _send_full_text(ws, full)
    return full


def get_openai_response(
    messages,
    ws=None,
    credentials_json=None,
    hubspot_access_token=None,
    hangup_state=None,
    include_end_call=True,
):
    try:
        openai_client = current_app.openai_client
        if not openai_client:
            fallback = "I am sorry, the AI service is currently unavailable."
            _send_full_text(ws, fallback)
            return fallback

        cal_defs, cal_by_name = ([], {})
        if credentials_json:
            cal_defs, cal_by_name = _voice_calendar_tool_defs_and_map()
        hub_defs, hub_by_name = ([], {})
        if hubspot_access_token:
            hub_defs, hub_by_name = _voice_hubspot_tool_defs_and_map()

        tools_defs = []
        if include_end_call:
            tools_defs.append(_voice_end_call_openai_tool())
        if cal_defs:
            tools_defs.extend(cal_defs)
        if hub_defs:
            tools_defs.extend(hub_defs)

        has_integration_tools = bool(cal_defs or hub_defs)
        use_tools_api = bool(tools_defs)
        msgs = [dict(m) for m in messages]

        # Fast path: no tools needed at all → stream straight to ConversationRelay.
        if not use_tools_api:
            model_name = Config.OPENAI_VOICE_FAST_MODEL or Config.OPENAI_MODEL
            return _stream_chat_to_ws(
                openai_client,
                ws,
                model=model_name,
                msgs=msgs,
                temperature=0.4,
                max_tokens=int(getattr(Config, "VOICE_FAST_MAX_TOKENS", None) or 350),
                timeout=float(getattr(Config, "VOICE_FAST_TIMEOUT_SECONDS", None) or 8),
            )

        max_rounds = int(getattr(Config, "VOICE_TOOL_MAX_ROUNDS", None) or 3)
        wait_sent = False
        for round_idx in range(max_rounds):
            if has_integration_tools:
                model_name = Config.OPENAI_MODEL
                temperature = 0.5
                max_tokens = int(
                    getattr(Config, "VOICE_TOOL_MAX_TOKENS", None) or (900 if len(tools_defs) > 6 else 700)
                )
                timeout_s = float(getattr(Config, "VOICE_TOOL_TIMEOUT_SECONDS", None) or 18)
            else:
                model_name = Config.OPENAI_VOICE_FAST_MODEL or Config.OPENAI_MODEL
                temperature = 0.4
                max_tokens = int(getattr(Config, "VOICE_FAST_MAX_TOKENS", None) or 350)
                timeout_s = float(getattr(Config, "VOICE_FAST_TIMEOUT_SECONDS", None) or 8)

            response = openai_client.chat.completions.create(
                model=model_name,
                messages=msgs,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout_s,
                tools=tools_defs,
                tool_choice="auto",
            )
            msg = response.choices[0].message

            if not msg.tool_calls:
                text = (msg.content or "").strip() or "Could you repeat that, please?"
                _send_full_text(ws, text)
                return text

            # An integration tool will actually run → now (and only now) play the filler line.
            integration_calls = [tc for tc in msg.tool_calls if tc.function.name != "end_call"]
            if integration_calls and not wait_sent:
                _ws_send_safe(
                    ws,
                    {
                        "type": "text",
                        "token": (Config.VOICE_WAIT_MESSAGE or "One moment while I check that.").strip(),
                        "last": True,
                        "interruptible": False,
                        "preemptible": True,
                    },
                )
                wait_sent = True

            assistant_payload = {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments or "{}",
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
            msgs.append(assistant_payload)

            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                if name in cal_by_name:
                    tool_fn = cal_by_name[name]
                    if not credentials_json:
                        content = json.dumps({"error": "calendar_tool_unavailable"})
                    else:
                        args["credentials_json"] = credentials_json
                        try:
                            result = tool_fn.invoke(args)
                        except Exception as tool_exc:
                            print(f"[GoogleCalendar] tool {name} error: {tool_exc}")
                            traceback.print_exc()
                            result = {"error": str(tool_exc)}
                        if isinstance(result, dict):
                            content = json.dumps(result, default=str)[:12000]
                        else:
                            content = str(result)[:12000]
                elif name in hub_by_name:
                    tool_fn = hub_by_name[name]
                    if not hubspot_access_token:
                        content = json.dumps({"error": "hubspot_tool_unavailable"})
                    else:
                        args["access_token"] = hubspot_access_token
                        try:
                            result = tool_fn.invoke(args)
                        except Exception as tool_exc:
                            print(f"[HubSpot] tool {name} error: {tool_exc}")
                            traceback.print_exc()
                            result = {"error": str(tool_exc)}
                        if isinstance(result, dict):
                            content = json.dumps(result, default=str)[:12000]
                        else:
                            content = str(result)[:12000]
                elif name == "end_call":
                    if hangup_state is not None:
                        hangup_state["requested"] = True
                    content = json.dumps(
                        {"status": "ok", "message": "Hang up after your closing message to the caller."}
                    )
                else:
                    content = json.dumps({"error": "tool_unavailable"})

                msgs.append({"role": "tool", "tool_call_id": tc.id, "content": content})

        timeout_msg = "I could not finish that request in time. Please try again."
        _send_full_text(ws, timeout_msg)
        return timeout_msg
    except Exception as exc:
        print(f"OpenAI response error: {exc}")
        traceback.print_exc()
        err = "I am having trouble right now. Please try again."
        _send_full_text(ws, err)
        return err


def _voice_transcript_may_need_tools(transcript: str) -> bool:
    """
    Heuristic: only enable tool-calling when the caller likely wants calendar/CRM actions.
    Keeps normal chit-chat low latency.
    """
    t = (transcript or "").strip().lower()
    if not t:
        return False

    # Strong calendar intent.
    calendar_terms = [
        "calendar",
        "schedule",
        "book",
        "booking",
        "appointment",
        "meeting",
        "reminder",
        "event",
        "invite",
        "availability",
        "free",
        "busy",
        "reschedule",
        "cancel",
        "timezone",
        "time zone",
        "tomorrow",
        "next week",
        "next monday",
        "today",
    ]
    # Strong CRM intent.
    hubspot_terms = [
        "hubspot",
        "crm",
        "contact",
        "lead",
        "company",
        "ticket",
        "case",
        "deal",
        "pipeline",
        "note",
        "log this",
        "create a ticket",
        "update",
        "search",
        "look up",
        "find",
    ]

    # If caller asks a straightforward question, prefer fast path.
    fast_chat_terms = [
        "what is",
        "explain",
        "how do i",
        "help me understand",
        "tell me about",
        "who are you",
    ]

    if any(x in t for x in fast_chat_terms) and not any(x in t for x in (calendar_terms + hubspot_terms)):
        return False

    if any(x in t for x in calendar_terms):
        return True
    if any(x in t for x in hubspot_terms):
        return True

    # Numbers + dates often imply scheduling/logging.
    if any(ch.isdigit() for ch in t) and any(
        x in t for x in ["am", "pm", ":", "/", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    ):
        return True
    if re.search(
        r"\b(january|february|march|april|june|july|august|september|october|november|december|"
        r"jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\b",
        t,
        re.I,
    ):
        return True
    # "May" as a month (avoid matching modal "may I" by requiring a day or year nearby).
    if re.search(r"\b\d{1,2}\s+may\b|\bmay\s+\d{1,2}\b|\b20\d{2}\b.*\bmay\b|\bmay\b.*\b20\d{2}\b", t, re.I):
        return True
    if re.search(r"\b20\d{2}\b", t):
        return True

    return False


def _voice_thread_suggests_tools(messages: list, latest_user: str) -> bool:
    """
    Enable tools when the latest line is a short follow-up (confirmations, timezone, etc.)
    but earlier turns already started a calendar/CRM flow. Avoids the fast path claiming success
    without calling tools.
    """
    lu = (latest_user or "").strip().lower()
    if not lu:
        return False

    combined = ""
    for m in messages[-14:]:
        c = (m.get("content") or "").strip()
        if c:
            combined += " " + c.lower()

    thread_markers = (
        "calendar",
        "schedule",
        "reminder",
        "appointment",
        "meeting",
        "event",
        "book",
        "booking",
        "timezone",
        "time zone",
        "availability",
        "invite",
        "reschedule",
        "cancel",
        "hubspot",
        "crm",
        "contact",
        "ticket",
        "deal",
        "free busy",
    )
    if not any(tm in combined for tm in thread_markers):
        return False

    if re.match(
        r"^\s*(yes|yeah|yep|sure|ok|okay|correct|right|please|proceed|confirmed?|go ahead|do it|please do|sounds good|that'?s right|that is right|agreed)\b",
        lu,
        re.I,
    ):
        return True
    if re.search(r"\b(confirmed|proceed)\s*$", lu, re.I):
        return True

    if any(
        x in lu
        for x in (
            "pakistan",
            "karachi",
            "lahore",
            "india",
            "dubai",
            "tokyo",
            "london",
            "gmt",
            "utc",
            "est",
            "pst",
            "cet",
            "asia/",
        )
    ):
        return True

    if re.search(r"\b\d{1,2}\s*(:\d{2})?\s*(am|pm)\b", lu, re.I):
        return True
    if re.search(r"\b20\d{2}\b", lu):
        return True

    if any(
        x in lu
        for x in (
            "can't see",
            "cannot see",
            "dont see",
            "don't see",
            "didn't work",
            "not there",
            "no event",
            "create again",
            "try again",
        )
    ):
        return True

    return False


def _voice_may_use_tools(messages: list, latest_user: str) -> bool:
    return _voice_transcript_may_need_tools(latest_user) or _voice_thread_suggests_tools(
        messages, latest_user
    )


_CALENDAR_KEYWORDS = frozenset([
    "calendar", "schedule", "book", "booking", "appointment", "meeting",
    "event", "availability", "available", "remind", "reschedule", "free slot",
])
_HUBSPOT_KEYWORDS = frozenset([
    "hubspot", "crm", "contact", "lead", "company", "ticket", "deal",
    "pipeline", "note", "log",
])


def _tools_not_connected_message(transcript: str, has_calendar: bool, has_hubspot: bool) -> str | None:
    """
    Return a spoken message when the caller clearly needs a tool that isn't connected.
    Returns None when no specific missing tool can be identified (let OpenAI handle it).
    """
    t = transcript.lower()
    wants_calendar = any(k in t for k in _CALENDAR_KEYWORDS)
    wants_hubspot = any(k in t for k in _HUBSPOT_KEYWORDS)

    missing = []
    if wants_calendar and not has_calendar:
        missing.append("Google Calendar")
    if wants_hubspot and not has_hubspot:
        missing.append("HubSpot CRM")

    if not missing:
        return None

    names = " and ".join(missing)
    verb = "hasn't" if len(missing) == 1 else "haven't"
    return (
        f"I'd like to help with that, but {names} {verb} been connected for this account yet. "
        "Please ask your administrator to set it up in the portal."
    )


_GOODBYE_RE = re.compile(
    r"\b(bye|goodbye|good\s*bye|see\s+you|talk\s+(?:to\s+you\s+)?later|"
    r"that'?s\s+all|no\s+more\s+questions?|hang\s+up|end\s+(?:the\s+)?call|"
    r"have\s+a\s+good|take\s+care|i'?m\s+done|we'?re\s+done|"
    r"thanks?\s*bye|thank\s+you\s+bye|speak\s+(?:to\s+you\s+)?(?:soon|later))\b",
    re.I,
)


def _voice_is_likely_goodbye(transcript: str) -> bool:
    return bool(_GOODBYE_RE.search((transcript or "").strip()))


@voice_agent_bp.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "service": "voice_agent",
        "active_conversations": len(active_conversations),
    }), 200
