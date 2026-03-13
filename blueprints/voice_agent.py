"""
Minimal voice agent using Twilio ConversationRelay + OpenAI.
Includes call recording and Supabase storage.
Uses only Config.TWILIO_PHONE_NUMBER (no tenant lookup).
"""
import json
import threading
import time
import traceback
import requests
from flask import Blueprint, current_app, jsonify, request
from twilio.twiml.voice_response import Connect, ConversationRelay, Language, VoiceResponse
from config import Config
from utils.system_prompt import system_prompt, greeting_prompt

voice_agent_bp = Blueprint("voice_agent", __name__)

# Active in-memory sessions keyed by ConversationRelay session id.
active_conversations = {}


def _normalize_phone(phone):
    if not phone:
        return ""
    return "".join(ch for ch in str(phone).strip() if ch.isdigit() or ch == "+")


def _resolve_base_ws_url():
    base_url = Config.WEBSOCKET_BASE_URL
    if base_url:
        base_url = base_url.strip().rstrip("/")
    if not base_url or base_url == "wss://your-domain.com":
        scheme = "wss" if request.is_secure else "ws"
        base_url = f"{scheme}://{request.host}"
    elif base_url.startswith("https://"):
        base_url = "wss://" + base_url[len("https://"):]
    elif base_url.startswith("http://"):
        base_url = "ws://" + base_url[len("http://"):]
    elif not (base_url.startswith("wss://") or base_url.startswith("ws://")):
        base_url = f"wss://{base_url.lstrip('/')}"
    return base_url


# def _build_system_prompt():
#     default_prompt = (
#         "You are a helpful company assistant on a phone call. "
#         "Speak naturally, be concise, and ask clarifying questions when needed."
#     )
#     return Config.COMPANY_ASSISTANT_PROMPT or default_prompt


def _start_recording_after_delay(app, call_sid, recording_status_callback_url, delay_seconds=3):
    """Start recording after a short delay so the call is in-progress (does not rely on status callback)."""
    def run():
        time.sleep(delay_seconds)
        with app.app_context():
            try:
                client = getattr(app, "twilio_client", None)
                if not client:
                    return
                client.calls(call_sid).recordings.create(
                    recording_status_callback=recording_status_callback_url,
                    recording_status_callback_event=["completed"],
                    recording_channels="dual",
                )
                print(f"[Recording] Started recording for call {call_sid} (delayed start)")
            except Exception as e:
                print(f"[Recording] Delayed start recording failed: {e}")

    t = threading.Thread(target=run, daemon=True)
    t.start()


def _create_call_record(call_sid, from_number, to_number, direction="inbound"):
    """Create call record in database for recording association (no tenant)."""
    supabase = getattr(current_app, "supabase_client", None)
    if not supabase:
        return None
    try:
        payload = {
            "call_sid": call_sid,
            "from_number": from_number,
            "to_number": to_number,
            "direction": direction,
            "status": "ringing",
        }
        row = supabase.table("calls").insert(payload).execute()
        if row.data and len(row.data) > 0:
            return row.data[0]["id"]
    except Exception as e:
        print(f"Failed to create call record: {e}")
    return None


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

        if call_sid and getattr(current_app, "supabase_client", None):
            _create_call_record(call_sid, from_number, to_number)

        # Start recording after a short delay so we don't rely on Twilio sending "in-progress" (many configs only send "completed").
        app = current_app._get_current_object()
        twilio_client = getattr(app, "twilio_client", None)
        if call_sid and twilio_client and request.host:
            host = request.headers.get("X-Forwarded-Host") or request.host
            scheme = request.headers.get("X-Forwarded-Proto") or ("https" if request.is_secure else "http")
            if "ngrok" in host:
                scheme = "https"
            recording_callback_url = f"{scheme}://{host}/voice-agent/recording-status"
            _start_recording_after_delay(app, call_sid, recording_callback_url)

        ws_url = f"{_resolve_base_ws_url()}/voice-agent/websocket"
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


def _recording_status_callback_url():
    """Use same host as current request (e.g. ngrok) so Twilio calls back here when recording is ready."""
    base = Config.RECORDING_STATUS_CALLBACK_URL
    if request and request.host and ("ngrok" in request.host or "localhost" in request.host or "127.0.0.1" in request.host):
        scheme = "https" if request.is_secure or "ngrok" in request.host else "http"
        base = f"{scheme}://{request.host}/voice-agent/recording-status"
    return base


@voice_agent_bp.route("/call-status", methods=["GET", "POST"])
def handle_call_status():
    """
    Twilio status callback. Start recording when call is in-progress.
    In Twilio Console, set this number's "Status callback URL" to your ngrok URL + /voice-agent/call-status
    """
    try:
        call_status = request.values.get("CallStatus")
        call_sid = request.values.get("CallSid")
        print(f"[Recording] call-status received: CallSid={call_sid}, CallStatus={call_status}")

        if call_status != "in-progress" or not call_sid:
            return "", 200

        twilio_client = getattr(current_app, "twilio_client", None)
        if not twilio_client:
            print("[Recording] No Twilio client, cannot start recording")
            return "", 200

        callback_url = _recording_status_callback_url()
        print(f"[Recording] Starting recording for call {call_sid}, will callback to {callback_url}")
        twilio_client.calls(call_sid).recordings.create(
            recording_status_callback=callback_url,
            recording_status_callback_event=["completed"],
            recording_channels="dual",
        )
        print(f"[Recording] Started recording for call {call_sid}")
    except Exception as exc:
        print(f"[Recording] Error starting recording: {exc}")
        traceback.print_exc()
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

        if recording_status != "completed" or not call_sid or not recording_sid or not recording_url:
            return "", 200

        supabase = getattr(current_app, "supabase_client", None)
        if not supabase:
            print("[Recording] No Supabase client")
            return "", 200

        call_row = supabase.table("calls").select("id").eq("call_sid", call_sid).execute()
        if not call_row.data or len(call_row.data) == 0:
            print(f"[Recording] No call record for {call_sid}, skipping Supabase upload")
            return "", 200

        call_id = call_row.data[0]["id"]
        bucket = Config.RECORDINGS_BUCKET
        storage_path = f"recordings/{recording_sid}.mp3"

        print(f"[Recording] Downloading from Twilio: {recording_url[:60]}...")
        audio_bytes = _download_twilio_recording(recording_url)
        if not audio_bytes:
            print("[Recording] Download returned no bytes")
            return "", 200

        print(f"[Recording] Uploading to Supabase bucket={bucket} path={storage_path}")
        if _upload_to_supabase(bucket, storage_path, audio_bytes):
            supabase.table("recordings").insert({
                "call_id": call_id,
                "twilio_recording_sid": recording_sid,
                "recording_url": recording_url,
                "storage_path": storage_path,
                "duration_seconds": int(recording_duration) if recording_duration else None,
                "status": "available",
            }).execute()
            print(f"[Recording] Saved to Supabase: {storage_path}")
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
                        active_conversations[session_id] = [{"role": "system", "content": system_prompt}]

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
                        active_conversations[session_id] = [{"role": "system", "content": system_prompt}]

                    messages = active_conversations[session_id]
                    messages.append({"role": "user", "content": transcript})
                    ai_response = get_openai_response(messages)
                    messages.append({"role": "assistant", "content": ai_response})

                    # ConversationRelay output format (text token).
                    ws.send(json.dumps({"type": "text", "token": ai_response, "last": True}))
                    print(f"[ConversationRelay] Sent assistant text token: {ai_response}")

                elif event_type in {"stop", "error"}:
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
