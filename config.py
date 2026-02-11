import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY")
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")