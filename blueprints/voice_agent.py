"""
Minimal voice agent using Twilio ConversationRelay + OpenAI.
Includes call recording and Supabase storage.
Uses only Config.TWILIO_PHONE_NUMBER (no tenant lookup).
"""
import json
import threading
import time
import traceback
from datetime import datetime, timezone
import requests
from flask import Blueprint, current_app, jsonify, request
from twilio.twiml.voice_response import Connect, ConversationRelay, Language, VoiceResponse
from config import Config
from utils.system_prompt import system_prompt, greeting_prompt

voice_agent_bp = Blueprint("voice_agent", __name__)

# Active in-memory sessions keyed by ConversationRelay session id.
active_conversations = {}
recording_started_calls = set()
recording_lock = threading.Lock()


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
            .select("tenant_id, phone_number")
            .eq("phone_number", to_number)
            .limit(1)
            .execute()
        )
        if row.data and len(row.data) > 0:
            return row.data[0]["tenant_id"]

        # Fallback: compare normalized values in Python.
        all_rows = supabase.table("phone_numbers").select("tenant_id, phone_number").execute()
        for item in all_rows.data or []:
            if _normalize_phone(item.get("phone_number")) == normalized_to:
                return item.get("tenant_id")
    except Exception as e:
        print(f"[TenantResolution] Failed for number={to_number}: {e}")
    return None


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
    try:
        to_number = request.form.get("To")
        from_number = request.form.get("From", "")
        call_sid = request.form.get("CallSid", "")

        configured_number = (
            Config.TWILIO_PHONE_NUMBER
            or Config.TWILIO_PHONE_NUMER
        )
        if not configured_number or _normalize_phone(to_number) != _normalize_phone(configured_number):
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
        ws_url = f"{_resolve_base_ws_url()}/voice-agent/websocket"
        print(
            f"[ConversationRelay] CallSid={call_sid} using ws_url={ws_url} "
            f"(host={request.host}, forwarded_host={request.headers.get('X-Forwarded-Host')}, "
            f"forwarded_proto={request.headers.get('X-Forwarded-Proto')})"
        )
        greeting = greeting_prompt

        connect = Connect()
        conversation_relay = ConversationRelay(
            url=ws_url,
            interruptible=True,
            welcome_greeting=greeting,
        )
        conversation_relay.append(Language(code=Config.CONVERSATION_LANGUAGE or "en-US"))
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
                        call_sid = data.get("callSid")
                        active_conversations[session_id] = {
                            "messages": [{"role": "system", "content": system_prompt}],
                            "call_sid": call_sid,
                        }
                        print(f"[ConversationRelay] Session started session_id={session_id} call_sid={call_sid}")
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
                        active_conversations[session_id] = {
                            "messages": [{"role": "system", "content": system_prompt}],
                            "call_sid": data.get("callSid"),
                        }

                    messages = active_conversations[session_id]["messages"]
                    messages.append({"role": "user", "content": transcript})
                    ai_response = get_openai_response(messages)
                    messages.append({"role": "assistant", "content": ai_response})

                    # ConversationRelay output format (text token).
                    ws.send(json.dumps({"type": "text", "token": ai_response, "last": True}))
                    print(f"[ConversationRelay] Sent assistant text token: {ai_response}")

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


def get_openai_response(messages):
    try:
        openai_client = current_app.openai_client
        if not openai_client:
            return "I am sorry, the AI service is currently unavailable."

        response = openai_client.chat.completions.create(
            model=Config.OPENAI_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=160,
        )
        if response.choices:
            return (response.choices[0].message.content or "").strip() or "Could you repeat that, please?"
        return "Could you repeat that, please?"
    except Exception as exc:
        print(f"OpenAI response error: {exc}")
        traceback.print_exc()
        return "I am having trouble right now. Please try again."


@voice_agent_bp.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "service": "voice_agent",
        "active_conversations": len(active_conversations),
    }), 200
