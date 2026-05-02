import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Core ──────────────────────────────────────────────────────────────────
    FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY")

    # ── Supabase ──────────────────────────────────────────────────────────────
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY")

    # Supabase httpx transport tuning
    SUPABASE_HTTP_RETRIES = int(os.getenv("SUPABASE_HTTP_RETRIES", "3"))
    SUPABASE_HTTP_MAX_CONNECTIONS = int(os.getenv("SUPABASE_HTTP_MAX_CONNECTIONS", "20"))
    SUPABASE_HTTP_MAX_KEEPALIVE = int(os.getenv("SUPABASE_HTTP_MAX_KEEPALIVE", "1"))
    SUPABASE_HTTP_CONNECT_TIMEOUT = float(os.getenv("SUPABASE_HTTP_CONNECT_TIMEOUT", "10"))
    SUPABASE_HTTP_READ_TIMEOUT = float(os.getenv("SUPABASE_HTTP_READ_TIMEOUT", "30"))
    SUPABASE_HTTP_WRITE_TIMEOUT = float(os.getenv("SUPABASE_HTTP_WRITE_TIMEOUT", "30"))
    SUPABASE_HTTP_POOL_TIMEOUT = float(os.getenv("SUPABASE_HTTP_POOL_TIMEOUT", "10"))

    # ── OpenAI ────────────────────────────────────────────────────────────────
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL")
    OPENAI_VOICE_FAST_MODEL = os.getenv("OPENAI_VOICE_FAST_MODEL")

    # OpenAI httpx connection pool tuning
    OPENAI_HTTP_MAX_CONNECTIONS = int(os.getenv("OPENAI_HTTP_MAX_CONNECTIONS", "10"))
    OPENAI_HTTP_MAX_KEEPALIVE = int(os.getenv("OPENAI_HTTP_MAX_KEEPALIVE", "5"))
    OPENAI_HTTP_KEEPALIVE_EXPIRY = float(os.getenv("OPENAI_HTTP_KEEPALIVE_EXPIRY", "60"))
    OPENAI_HTTP_CONNECT_TIMEOUT = float(os.getenv("OPENAI_HTTP_CONNECT_TIMEOUT", "5"))
    OPENAI_HTTP_READ_TIMEOUT = float(os.getenv("OPENAI_HTTP_READ_TIMEOUT", "30"))

    # ── Twilio ────────────────────────────────────────────────────────────────
    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
    TWILIO_PHONE_NUMER = os.getenv("TWILIO_PHONE_NUMER")  # legacy spelling; kept for backward compatibility

    VOICE_WEBHOOK_URL = os.getenv(
        "VOICE_WEBHOOK_URL",
        "https://twilio-multi-tenant-voice-agent.onrender.com/voice-agent/incoming-call",
    )
    CALL_STATUS_CALLBACK_URL = os.getenv(
        "CALL_STATUS_CALLBACK_URL",
        "https://twilio-multi-tenant-voice-agent.onrender.com/voice-agent/call-status",
    )
    RECORDING_STATUS_CALLBACK_URL = os.getenv(
        "RECORDING_STATUS_CALLBACK_URL",
        "https://twilio-multi-tenant-voice-agent.onrender.com/voice-agent/recording-status",
    )
    RECORDINGS_BUCKET = os.getenv("RECORDINGS_BUCKET", "call-recordings")
    WEBSOCKET_BASE_URL = os.getenv("WEBSOCKET_BASE_URL")
    CONVERSATION_LANGUAGE = os.getenv("CONVERSATION_LANGUAGE", "en-US")

    # ── Voice agent latency tuning ────────────────────────────────────────────
    VOICE_FAST_TIMEOUT_SECONDS = float(os.getenv("VOICE_FAST_TIMEOUT_SECONDS", "8"))
    VOICE_TOOL_TIMEOUT_SECONDS = float(os.getenv("VOICE_TOOL_TIMEOUT_SECONDS", "18"))
    VOICE_TOOL_MAX_ROUNDS = int(os.getenv("VOICE_TOOL_MAX_ROUNDS", "3"))
    VOICE_FAST_MAX_TOKENS = int(os.getenv("VOICE_FAST_MAX_TOKENS", "350"))
    VOICE_TOOL_MAX_TOKENS = int(os.getenv("VOICE_TOOL_MAX_TOKENS", "900"))
    VOICE_WAIT_MESSAGE = os.getenv("VOICE_WAIT_MESSAGE", "One moment while I check that.")
    VOICE_POST_TOOL_DRAIN_MS = float(os.getenv("VOICE_POST_TOOL_DRAIN_MS", "750"))

    # ── SMTP / email ──────────────────────────────────────────────────────────
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
    ADMIN_APP_PASSWORD = os.getenv("ADMIN_APP_PASSWORD")
    SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL")
    SMTP_TIMEOUT_SECONDS = float(os.getenv("SMTP_TIMEOUT_SECONDS", "12"))
    PASSWORD_RESET_REDIRECT_URL = os.getenv(
        "PASSWORD_RESET_REDIRECT_URL", "http://localhost:3000/reset-password"
    )

    # ── Google Calendar OAuth ─────────────────────────────────────────────────
    GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
    GOOGLE_OAUTH_SCOPES = os.getenv(
        "GOOGLE_OAUTH_SCOPES", "https://www.googleapis.com/auth/calendar"
    )
    GOOGLE_CALENDAR_OAUTH_REDIRECT_URI = os.getenv("GOOGLE_CALENDAR_OAUTH_REDIRECT_URI")
    GOOGLE_CALENDAR_CONNECT_SUCCESS_URL = os.getenv("GOOGLE_CALENDAR_CONNECT_SUCCESS_URL")
    GOOGLE_CALENDAR_CONNECT_ERROR_URL = os.getenv("GOOGLE_CALENDAR_CONNECT_ERROR_URL")

    # ── Credential encryption ─────────────────────────────────────────────────
    CREDENTIAL_ENCRYPTION_KEY = os.getenv("CREDENTIAL_ENCRYPTION_KEY")
