"""
Minimal voice agent using Twilio ConversationRelay + OpenAI.
"""
import json
import traceback
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


@voice_agent_bp.route("/incoming-call", methods=["POST"])
def handle_incoming_call():
    """
    Twilio webhook entrypoint. Only configured Twilio number is accepted.
    """
    try:
        to_number = request.form.get("To")
        configured_number = (
            Config.TWILIO_PHONE_NUMBER
            or Config.TWILIO_PHONE_NUMER
        )

        response = VoiceResponse()
        if not configured_number:
            response.say("Service number is not configured. Goodbye.")
            response.hangup()
            return str(response), 200, {"Content-Type": "text/xml"}

        if _normalize_phone(to_number) != _normalize_phone(configured_number):
            response.say("This number is not configured for the assistant. Goodbye.")
            response.hangup()
            return str(response), 200, {"Content-Type": "text/xml"}

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
        response.append(connect)

        return str(response), 200, {"Content-Type": "text/xml"}
    except Exception as exc:
        print(f"Error handling incoming call: {exc}")
        traceback.print_exc()
        response = VoiceResponse()
        response.say("Sorry, an error occurred. Please try again later.")
        response.hangup()
        return str(response), 200, {"Content-Type": "text/xml"}


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
