from flask import Flask
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from extensions import init_app


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.secret_key = Config.FLASK_SECRET_KEY or "dev-only-set-FLASK_SECRET_KEY-in-production"

    # Trust X-Forwarded-* headers from proxies (ngrok, render, etc.)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    CORS(
        app,
        supports_credentials=True,
        origins="*",
        methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        allow_headers=["Content-Type", "Authorization", "X-Requested-With", "Accept", "Origin"],
        expose_headers=["Content-Type", "Authorization"],
    )

    init_app(app)

    @app.before_request
    def _enforce_service_role():
        """
        The Supabase SDK's built-in auth-state listener calls
        postgrest.auth(session.access_token) whenever sign_in_with_password()
        succeeds, silently replacing the service role key with the user JWT.
        That leaves RLS active for all subsequent table queries, scoping every
        cross-tenant admin query to just the signed-in user's own tenant.
        Reset to the service role key before every request so the PostgREST
        client always bypasses RLS regardless of prior sign-in events.
        """
        client = getattr(app, "supabase_client", None)
        if client and Config.SUPABASE_SECRET_KEY:
            client.postgrest.auth(Config.SUPABASE_SECRET_KEY)

    return app
