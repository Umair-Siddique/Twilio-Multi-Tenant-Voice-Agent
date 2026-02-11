from flask import Flask
from config import Config

from extensions import init_openai, init_supabase

from blueprints.auth import auth_bp
from blueprints.tenant import tenant_bp

from flask_cors import CORS
from flask_sock import Sock

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

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

    # Register blueprints
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(tenant_bp, url_prefix="/tenant")


    return app
