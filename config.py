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
    SUPABASE_HTTP_MAX_KEEPALIVE = int(os.getenv("SUPABASE_HTTP_MAX_KEEPALIVE", "5"))
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

    # ── Address verification / region lock ────────────────────────────────────
    # Business addresses are validated with the Google Address Validation API.
    # Enable the "Address Validation API" on a Google Cloud project with billing
    # enabled, then create a server-side API key restricted to that one API.
    GOOGLE_ADDRESS_VALIDATION_API_KEY = os.getenv("GOOGLE_ADDRESS_VALIDATION_API_KEY")
    ADDRESS_VERIFICATION_ENABLED = (
        os.getenv("ADDRESS_VERIFICATION_ENABLED", "true").lower() != "false"
    )
    # Strict (default): if the verification API is unreachable or unconfigured,
    # signup is blocked rather than letting an unverified address through.
    # Set to "false" to let those accounts through flagged as "unverified".
    ADDRESS_VERIFICATION_STRICT = (
        os.getenv("ADDRESS_VERIFICATION_STRICT", "true").lower() != "false"
    )
    ADDRESS_VERIFICATION_TIMEOUT_SECONDS = float(
        os.getenv("ADDRESS_VERIFICATION_TIMEOUT_SECONDS", "10")
    )
    # USPS CASS certification — US/Puerto Rico only, off by default. When on,
    # US addresses are checked for real USPS deliverability, not just a postal
    # match. Note Google's CASS terms require reporting artificial addresses.
    ADDRESS_ENABLE_USPS_CASS = (
        os.getenv("ADDRESS_ENABLE_USPS_CASS", "false").lower() == "true"
    )

    # ── Voice agent concurrency ───────────────────────────────────────────────
    MAX_CONCURRENT_SESSIONS = int(os.getenv("MAX_CONCURRENT_SESSIONS", "100"))

    # ── Rate limiting ─────────────────────────────────────────────────────────
    # Storage backend for Flask-Limiter. Defaults to in-process memory (fine for
    # a single worker). For multi-worker / multi-instance deployments set
    # RATELIMIT_STORAGE_URI to a shared store, e.g. "redis://<host>:6379".
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_ENABLED = os.getenv("RATELIMIT_ENABLED", "true").lower() != "false"
    # Per-endpoint limits (overridable via env without code changes).
    RATELIMIT_SIGNIN = os.getenv("RATELIMIT_SIGNIN", "10 per minute")
    RATELIMIT_SIGNUP = os.getenv("RATELIMIT_SIGNUP", "5 per minute")
    RATELIMIT_FORGOT_PASSWORD = os.getenv("RATELIMIT_FORGOT_PASSWORD", "5 per minute")
    RATELIMIT_RESET_PASSWORD = os.getenv("RATELIMIT_RESET_PASSWORD", "10 per minute")
    RATELIMIT_REFRESH = os.getenv("RATELIMIT_REFRESH", "30 per minute")

    # ── Spam scoring ──────────────────────────────────────────────────────────
    # A call whose spam_score is >= this threshold is counted as "flagged".
    SPAM_SCORE_THRESHOLD = float(os.getenv("SPAM_SCORE_THRESHOLD", "0.7"))

    # ── Broadcast campaigns (outbound recorded-message calls + emails) ────────
    # The dispatcher is a background thread that dials/emails contacts of
    # running campaigns. Safe with several workers: each campaign is leased to
    # one dispatcher at a time. Set to "false" on the web service if you run
    # `python campaign_worker.py` as a separate worker process instead.
    CAMPAIGN_DISPATCHER_ENABLED = (
        os.getenv("CAMPAIGN_DISPATCHER_ENABLED", "true").lower() != "false"
    )
    CAMPAIGN_DISPATCH_INTERVAL_SECONDS = float(os.getenv("CAMPAIGN_DISPATCH_INTERVAL_SECONDS", "5"))
    # Twilio's default outbound limit is 1 call per second per account; raise
    # this only after Twilio has raised the account's CPS.
    CAMPAIGN_CALLS_PER_SECOND = float(os.getenv("CAMPAIGN_CALLS_PER_SECOND", "1"))
    # Upper bound for a campaign's max_concurrent_calls setting.
    CAMPAIGN_MAX_CONCURRENT_CALLS_LIMIT = int(os.getenv("CAMPAIGN_MAX_CONCURRENT_CALLS_LIMIT", "20"))
    CAMPAIGN_EMAILS_PER_MINUTE = float(os.getenv("CAMPAIGN_EMAILS_PER_MINUTE", "20"))
    # Calls with no status update for this long are reconciled against Twilio.
    CAMPAIGN_STALE_CALL_MINUTES = int(os.getenv("CAMPAIGN_STALE_CALL_MINUTES", "15"))
    # Public base URL Twilio calls back on (e.g. "https://api.example.com").
    # When unset it is derived from the request host / VOICE_WEBHOOK_URL.
    CAMPAIGN_WEBHOOK_BASE_URL = os.getenv("CAMPAIGN_WEBHOOK_BASE_URL")
    # Campaigns are the platform's own marketing outreach, run by super admins.
    # Caller ID default; any voice number on the Twilio account may be picked.
    CAMPAIGN_DEFAULT_FROM_NUMBER = os.getenv("CAMPAIGN_DEFAULT_FROM_NUMBER", "+12044004668")
    # Region for sheet numbers written without a +country code, and the
    # timezone a new campaign's calling hours default to.
    CAMPAIGN_DEFAULT_COUNTRY = os.getenv("CAMPAIGN_DEFAULT_COUNTRY", "CA")
    CAMPAIGN_DEFAULT_TIMEZONE = os.getenv("CAMPAIGN_DEFAULT_TIMEZONE", "America/Winnipeg")
    # Sender identity in campaign email footers. Anti-spam laws (CASL, CAN-SPAM)
    # require a physical mailing address in commercial email — set it.
    CAMPAIGN_SENDER_NAME = os.getenv("CAMPAIGN_SENDER_NAME", "Aidan Pro")
    CAMPAIGN_SENDER_ADDRESS = os.getenv("CAMPAIGN_SENDER_ADDRESS")
    CAMPAIGN_AUDIO_BUCKET = os.getenv("CAMPAIGN_AUDIO_BUCKET", "campaign-audio")
    CAMPAIGN_AUDIO_MAX_BYTES = int(os.getenv("CAMPAIGN_AUDIO_MAX_BYTES", str(10 * 1024 * 1024)))
    CAMPAIGN_IMPORT_MAX_BYTES = int(os.getenv("CAMPAIGN_IMPORT_MAX_BYTES", str(10 * 1024 * 1024)))
    CAMPAIGN_IMPORT_MAX_ROWS = int(os.getenv("CAMPAIGN_IMPORT_MAX_ROWS", "50000"))
    # Campaign emails can use a dedicated sender (recommended for bulk mail —
    # a Gmail account caps out at roughly 500 messages/day). Each setting falls
    # back to the regular SMTP settings above.
    CAMPAIGN_SMTP_HOST = os.getenv("CAMPAIGN_SMTP_HOST") or SMTP_HOST
    CAMPAIGN_SMTP_PORT = int(os.getenv("CAMPAIGN_SMTP_PORT") or SMTP_PORT)
    CAMPAIGN_SMTP_USER = os.getenv("CAMPAIGN_SMTP_USER") or ADMIN_EMAIL
    CAMPAIGN_SMTP_PASSWORD = os.getenv("CAMPAIGN_SMTP_PASSWORD") or ADMIN_APP_PASSWORD
    CAMPAIGN_SMTP_FROM_EMAIL = (
        os.getenv("CAMPAIGN_SMTP_FROM_EMAIL") or SMTP_FROM_EMAIL or CAMPAIGN_SMTP_USER
    )
