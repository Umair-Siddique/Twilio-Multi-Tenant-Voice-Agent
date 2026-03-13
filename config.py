import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY")
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_PHONE_NUMER = os.getenv("TWILIO_PHONE_NUMER")

    # Preferred key; keep misspelled key above for backward compatibility.
    TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

    # Voice webhook URL for newly purchased numbers (incoming calls).
    VOICE_WEBHOOK_URL = os.getenv(
        "VOICE_WEBHOOK_URL",
        "https://twilio-multi-tenant-voice-agent.onrender.com/voice-agent/incoming-call"
    )

    # Call status callback URL (for recording lifecycle).
    # Twilio calls this when call status changes (ringing -> in-progress -> completed).
    CALL_STATUS_CALLBACK_URL = os.getenv(
        "CALL_STATUS_CALLBACK_URL",
        "https://twilio-multi-tenant-voice-agent.onrender.com/voice-agent/call-status"
    )

    # Recording storage bucket in Supabase.
    RECORDINGS_BUCKET = os.getenv("RECORDINGS_BUCKET", "call-recordings")

    # Recording status callback URL (Twilio notifies when recording is ready).
    RECORDING_STATUS_CALLBACK_URL = os.getenv(
        "RECORDING_STATUS_CALLBACK_URL",
        "https://twilio-multi-tenant-voice-agent.onrender.com/voice-agent/recording-status"
    )

    # WebSocket base URL for ConversationRelay.
    WEBSOCKET_BASE_URL = os.getenv("WEBSOCKET_BASE_URL")
    CONVERSATION_LANGUAGE = os.getenv("CONVERSATION_LANGUAGE")

    OPENAI_MODEL = os.getenv("OPENAI_MODEL")

    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
    ADMIN_APP_PASSWORD = os.getenv("ADMIN_APP_PASSWORD")