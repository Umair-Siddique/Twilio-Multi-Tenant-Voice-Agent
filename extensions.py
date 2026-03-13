from openai import OpenAI as OpenAIClient
from supabase import create_client, Client
from flask_sock import Sock
from config import Config
from twilio.rest import Client

def init_openai(app):
    app.openai_client = OpenAIClient(api_key=Config.OPENAI_API_KEY)

def init_supabase(app):
    """Initialize Supabase client"""
    if Config.SUPABASE_URL and Config.SUPABASE_SECRET_KEY:
        client = create_client(Config.SUPABASE_URL, Config.SUPABASE_SECRET_KEY)
        app.supabase_client = client
        
        print("✅ Supabase client initialized successfully")
    else:
        app.supabase_client = None
        print("⚠️  Supabase not configured - conversation storage disabled")

def init_websocket(app):
    """Initialize WebSocket support for ConversationRelay"""
    app.sock = Sock(app)
    print("✅ WebSocket support initialized for ConversationRelay")

def init_twilio(app):
    app.twilio_client = Client(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)