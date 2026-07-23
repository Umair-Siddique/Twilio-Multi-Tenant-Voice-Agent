import httpx
from openai import OpenAI as OpenAIClient
from supabase import create_client
from flask_sock import Sock
from twilio.rest import Client as TwilioClient
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from config import Config


# Shared rate limiter. Blueprints import this to decorate specific routes with
# `@limiter.limit(...)`. No global default limits are set, so any route without
# an explicit decorator (Twilio webhooks, the voice websocket, integrations,
# etc.) stays completely unthrottled — only the endpoints we opt in are limited.
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=Config.RATELIMIT_STORAGE_URI,
    default_limits=[],
    headers_enabled=True,
    enabled=Config.RATELIMIT_ENABLED,
)


def init_rate_limiter(app):
    limiter.init_app(app)

    @app.errorhandler(429)
    def _ratelimit_handler(e):
        from flask import jsonify
        return jsonify({
            "error": "Too many requests. Please wait a moment and try again.",
            "detail": getattr(e, "description", None),
        }), 429

    print("✅ Rate limiter initialized" if Config.RATELIMIT_ENABLED else "⚠️  Rate limiter disabled")


def init_openai(app):
    transport = httpx.HTTPTransport(
        retries=3,
        limits=httpx.Limits(
            max_connections=Config.OPENAI_HTTP_MAX_CONNECTIONS,
            max_keepalive_connections=Config.OPENAI_HTTP_MAX_KEEPALIVE,
            keepalive_expiry=Config.OPENAI_HTTP_KEEPALIVE_EXPIRY,
        ),
    )
    http_client = httpx.Client(
        transport=transport,
        timeout=httpx.Timeout(
            connect=Config.OPENAI_HTTP_CONNECT_TIMEOUT,
            read=Config.OPENAI_HTTP_READ_TIMEOUT,
            write=Config.OPENAI_HTTP_READ_TIMEOUT,
            pool=Config.OPENAI_HTTP_CONNECT_TIMEOUT,
        ),
    )
    app.openai_client = OpenAIClient(api_key=Config.OPENAI_API_KEY, http_client=http_client)
    print("✅ OpenAI client initialized")


def init_supabase(app):
    if not Config.SUPABASE_URL or not Config.SUPABASE_SECRET_KEY:
        app.supabase_client = None
        print("⚠️  Supabase not configured - conversation storage disabled")
        return

    try:
        from supabase.lib.client_options import ClientOptions

        transport = httpx.HTTPTransport(
            retries=Config.SUPABASE_HTTP_RETRIES,
            http2=False,
            limits=httpx.Limits(
                max_connections=Config.SUPABASE_HTTP_MAX_CONNECTIONS,
                max_keepalive_connections=Config.SUPABASE_HTTP_MAX_KEEPALIVE,
                keepalive_expiry=60,  # Render LB kills idle sockets at ~75s; expire before that
            ),
        )
        httpx_client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(
                connect=Config.SUPABASE_HTTP_CONNECT_TIMEOUT,
                read=Config.SUPABASE_HTTP_READ_TIMEOUT,
                write=Config.SUPABASE_HTTP_WRITE_TIMEOUT,
                pool=Config.SUPABASE_HTTP_POOL_TIMEOUT,
            ),
        )
        # Pass the service role key as the Authorization header at init time so
        # PostgREST bypasses RLS for every table query this client makes.
        # Setting it here (ClientOptions.headers) is more reliable than calling
        # .auth() after construction because some SDK versions ignore the latter.
        options = ClientOptions(
            headers={"Authorization": f"Bearer {Config.SUPABASE_SECRET_KEY}"},
            httpx_client=httpx_client,
        )
        client = create_client(Config.SUPABASE_URL, Config.SUPABASE_SECRET_KEY, options=options)
    except Exception:
        client = create_client(Config.SUPABASE_URL, Config.SUPABASE_SECRET_KEY)

    # Belt-and-suspenders: also set it on the already-constructed PostgREST
    # session so the header is present regardless of which code path ran above.
    client.postgrest.auth(Config.SUPABASE_SECRET_KEY)

    app.supabase_client = client
    print("✅ Supabase client initialized")


def init_websocket(app):
    app.sock = Sock(app)
    print("✅ WebSocket support initialized for ConversationRelay")


def init_twilio(app):
    if not Config.TWILIO_ACCOUNT_SID or not Config.TWILIO_AUTH_TOKEN:
        app.twilio_client = None
        print("⚠️  Twilio credentials not configured - voice features disabled")
        return

    try:
        app.twilio_client = TwilioClient(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
        account = app.twilio_client.api.accounts(Config.TWILIO_ACCOUNT_SID).fetch()
        print(f"✅ Twilio client initialized (Account: {account.friendly_name})")
    except Exception as e:
        print(f"⚠️  Twilio client initialization failed: {e}")
        app.twilio_client = None


def register_blueprints(app):
    from blueprints.auth import auth_bp
    from blueprints.tenant import tenant_bp
    from blueprints.voice_agent import voice_agent_bp, register_websocket
    from blueprints.twilio_phone_numbers import twilio_bp
    from blueprints.integrations_google_calendar import google_calendar_integration_bp
    from blueprints.integrations_hubspot import hubspot_integration_bp
    from blueprints.super_admin import super_admin_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(tenant_bp, url_prefix="/tenant")
    app.register_blueprint(voice_agent_bp, url_prefix="/voice-agent")
    app.register_blueprint(twilio_bp, url_prefix="/twilio")
    app.register_blueprint(google_calendar_integration_bp, url_prefix="/integrations/google-calendar")
    app.register_blueprint(hubspot_integration_bp, url_prefix="/integrations/hubspot")
    app.register_blueprint(super_admin_bp, url_prefix="/admin")

    register_websocket(app)
    print("✅ Blueprints registered")


def init_app(app):
    init_rate_limiter(app)
    init_openai(app)
    init_supabase(app)
    init_websocket(app)
    init_twilio(app)
    register_blueprints(app)
