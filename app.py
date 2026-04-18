import os

from flask import Flask
from config import Config

from extensions import init_openai, init_supabase, init_websocket, init_twilio

from blueprints.auth import auth_bp
from blueprints.tenant import tenant_bp
from blueprints.voice_agent import voice_agent_bp
from blueprints.twilio_phone_numbers import twilio_bp
from blueprints.integrations_google_calendar import google_calendar_integration_bp
from blueprints.integrations_hubspot import hubspot_integration_bp

from flask_cors import CORS

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.secret_key = (
        Config.FLASK_SECRET_KEY
        or Config.SUPABASE_SECRET_KEY
        or os.environ.get("FLASK_SECRET_KEY")
        or "dev-only-set-FLASK_SECRET_KEY-in-production"
    )

    # Trust X-Forwarded-* headers (e.g. from ngrok) so request.host is the public URL
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # Allow all origins with comprehensive settings
    CORS(app, 
         supports_credentials=True, 
         origins="*",
         methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH'],
         allow_headers=['Content-Type', 'Authorization', 'X-Requested-With', 'Accept', 'Origin'],
         expose_headers=['Content-Type', 'Authorization']
    )

    # Initialize extensions
    init_openai(app)
    init_supabase(app)
    init_websocket(app)
    init_twilio(app)

    # Register blueprints
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(tenant_bp, url_prefix="/tenant")
    app.register_blueprint(voice_agent_bp, url_prefix="/voice-agent")
    app.register_blueprint(twilio_bp, url_prefix="/twilio")
    app.register_blueprint(
        google_calendar_integration_bp, url_prefix="/integrations/google-calendar"
    )
    app.register_blueprint(hubspot_integration_bp, url_prefix="/integrations/hubspot")
    
    # Register WebSocket routes (must be after blueprint registration)
    from blueprints.voice_agent import register_websocket
    register_websocket(app)

    return app
