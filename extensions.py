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
    """Initialize Twilio client with validation"""
    if Config.TWILIO_ACCOUNT_SID and Config.TWILIO_AUTH_TOKEN:
        try:
            app.twilio_client = Client(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
            # Test the client by fetching account info
            account = app.twilio_client.api.accounts(Config.TWILIO_ACCOUNT_SID).fetch()
            print(f"✅ Twilio client initialized successfully (Account: {account.friendly_name})")
        except Exception as e:
            print(f"⚠️  Twilio client initialization failed: {e}")
            app.twilio_client = None
    else:
        print("⚠️  Twilio credentials not configured - voice features disabled")
        app.twilio_client = None