"""
Voice catalog for the AI agent.
Defines all available voices that tenants can select.
Preview audio is generated via OpenAI TTS as an approximation.
Actual calls use the Twilio TTS provider and voice specified here.
"""

AVAILABLE_VOICES = [
    # ── Google Neural2 ── high-quality neural voices ──────────────────────
    {
        "id": "google-en-US-Neural2-C",
        "name": "Ava",
        "gender": "female",
        "language_code": "en-US",
        "provider": "Google",
        "description": "Warm and expressive female voice",
        "twilio_tts_provider": "Google",
        "twilio_voice": "en-US-Neural2-C",
        "openai_preview_voice": "nova",
    },
    {
        "id": "google-en-US-Neural2-F",
        "name": "Emma",
        "gender": "female",
        "language_code": "en-US",
        "provider": "Google",
        "description": "Clear and professional female voice",
        "twilio_tts_provider": "Google",
        "twilio_voice": "en-US-Neural2-F",
        "openai_preview_voice": "shimmer",
    },
    {
        "id": "google-en-US-Neural2-A",
        "name": "James",
        "gender": "male",
        "language_code": "en-US",
        "provider": "Google",
        "description": "Deep and authoritative male voice",
        "twilio_tts_provider": "Google",
        "twilio_voice": "en-US-Neural2-A",
        "openai_preview_voice": "onyx",
    },
    {
        "id": "google-en-US-Neural2-D",
        "name": "Ethan",
        "gender": "male",
        "language_code": "en-US",
        "provider": "Google",
        "description": "Friendly and natural male voice",
        "twilio_tts_provider": "Google",
        "twilio_voice": "en-US-Neural2-D",
        "openai_preview_voice": "echo",
    },
    # ── Google Studio ── premium studio-quality voices ─────────────────────
    {
        "id": "google-en-US-Studio-O",
        "name": "Oliver",
        "gender": "male",
        "language_code": "en-US",
        "provider": "Google",
        "description": "Premium studio-quality male voice",
        "twilio_tts_provider": "Google",
        "twilio_voice": "en-US-Studio-O",
        "openai_preview_voice": "echo",
    },
    {
        "id": "google-en-US-Studio-Q",
        "name": "Quinn",
        "gender": "female",
        "language_code": "en-US",
        "provider": "Google",
        "description": "Premium studio-quality female voice",
        "twilio_tts_provider": "Google",
        "twilio_voice": "en-US-Studio-Q",
        "openai_preview_voice": "nova",
    },
    # ── Amazon Polly Neural ────────────────────────────────────────────────
    {
        "id": "amazon-Joanna",
        "name": "Joanna",
        "gender": "female",
        "language_code": "en-US",
        "provider": "Amazon",
        "description": "Natural and engaging female voice",
        "twilio_tts_provider": "Amazon Polly",
        "twilio_voice": "Joanna",
        "openai_preview_voice": "nova",
    },
    {
        "id": "amazon-Matthew",
        "name": "Matthew",
        "gender": "male",
        "language_code": "en-US",
        "provider": "Amazon",
        "description": "Clear and steady male voice",
        "twilio_tts_provider": "Amazon Polly",
        "twilio_voice": "Matthew",
        "openai_preview_voice": "echo",
    },
    {
        "id": "amazon-Salli",
        "name": "Salli",
        "gender": "female",
        "language_code": "en-US",
        "provider": "Amazon",
        "description": "Bright and energetic female voice",
        "twilio_tts_provider": "Amazon Polly",
        "twilio_voice": "Salli",
        "openai_preview_voice": "shimmer",
    },
    {
        "id": "amazon-Joey",
        "name": "Joey",
        "gender": "male",
        "language_code": "en-US",
        "provider": "Amazon",
        "description": "Young and friendly male voice",
        "twilio_tts_provider": "Amazon Polly",
        "twilio_voice": "Joey",
        "openai_preview_voice": "alloy",
    },
]

DEFAULT_VOICE_ID = "google-en-US-Neural2-F"

_voice_index = {v["id"]: v for v in AVAILABLE_VOICES}


def get_voice_by_id(voice_id: str) -> dict | None:
    return _voice_index.get(voice_id)


def get_default_voice() -> dict:
    return _voice_index[DEFAULT_VOICE_ID]
