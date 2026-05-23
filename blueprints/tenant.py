"""
Tenant management endpoints
These demonstrate the auth flow and provide tenant configuration APIs
"""
from flask import Blueprint, request, jsonify, current_app, Response
from utils.auth_utils import require_tenant, require_role
from utils.supabase_retry import supabase_call_with_retry
from utils.voice_catalog import AVAILABLE_VOICES, get_voice_by_id, get_default_voice, DEFAULT_VOICE_ID
from functools import wraps
import traceback

_VOICE_PREVIEW_TEXT = (
    "Hello! I'm your AI voice assistant. I'm here to help you with any questions "
    "or requests you may have. How can I assist you today?"
)
_VOICE_PUBLIC_FIELDS = ["id", "name", "gender", "language_code", "provider", "description"]

tenant_bp = Blueprint('tenant', __name__)

def handle_errors(f):
    """Decorator to handle errors consistently"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            print(f"Error in {f.__name__}: {str(e)}")
            traceback.print_exc()
            return jsonify({"error": str(e)}), 500
    return decorated_function


@tenant_bp.route('/profile', methods=['GET'])
@require_tenant
@handle_errors
def get_tenant_profile(user_id, tenant_id, role):
    """
    Get tenant profile information
    Requires authentication
    """
    supabase = current_app.supabase_client
    
    # Get tenant details
    tenant_response = supabase_call_with_retry(
        lambda: supabase.table("tenants").select("*").eq("id", tenant_id).execute()
    )
    
    if not tenant_response.data or len(tenant_response.data) == 0:
        return jsonify({"error": "Tenant not found"}), 404
    
    return jsonify({
        "tenant": tenant_response.data[0],
        "user_role": role
    }), 200


@tenant_bp.route('/profile', methods=['PUT'])
@require_role(['owner', 'admin'])
@handle_errors
def update_tenant_profile(user_id, tenant_id, role):
    """
    Update tenant profile
    Requires owner or admin role
    """
    data = request.get_json()
    supabase = current_app.supabase_client
    
    # Allowed fields to update
    allowed_fields = ['name', 'timezone', 'industry', 'default_email_recipients']
    update_data = {k: v for k, v in data.items() if k in allowed_fields}
    
    if not update_data:
        return jsonify({"error": "No valid fields to update"}), 400
    
    # Update tenant
    response = supabase_call_with_retry(
        lambda: supabase.table("tenants").update(update_data).eq("id", tenant_id).execute()
    )
    
    if not response.data or len(response.data) == 0:
        return jsonify({"error": "Failed to update tenant"}), 500
    
    return jsonify({
        "message": "Tenant updated successfully",
        "tenant": response.data[0]
    }), 200


@tenant_bp.route('/agent-config', methods=['GET'])
@require_tenant
@handle_errors
def get_agent_config(user_id, tenant_id, role):
    """
    Get tenant's agent configuration
    """
    supabase = current_app.supabase_client
    
    response = supabase_call_with_retry(
        lambda: supabase.table("tenant_agent_config")
        .select("*")
        .eq("tenant_id", tenant_id)
        .execute()
    )
    
    if not response.data or len(response.data) == 0:
        return jsonify({"error": "Agent config not found"}), 404
    
    return jsonify(response.data[0]), 200


@tenant_bp.route('/agent-config', methods=['PUT'])
@require_role(['owner', 'admin'])
@handle_errors
def update_agent_config(user_id, tenant_id, role):
    """
    Update tenant's agent configuration
    Requires owner or admin role
    """
    data = request.get_json()
    supabase = current_app.supabase_client
    
    # Allowed fields to update
    allowed_fields = [
        'greeting', 'system_prompt', 'tone', 'business_hours', 'escalation_rules',
        'allowed_actions', 'custom_prompts', 'store_transcripts',
        'store_recordings', 'retention_days'
    ]
    
    update_data = {k: v for k, v in data.items() if k in allowed_fields}
    
    if not update_data:
        return jsonify({"error": "No valid fields to update"}), 400
    
    # Update agent config
    response = supabase_call_with_retry(
        lambda: supabase.table("tenant_agent_config")
        .update(update_data)
        .eq("tenant_id", tenant_id)
        .execute()
    )
    
    if not response.data or len(response.data) == 0:
        return jsonify({"error": "Failed to update agent config"}), 500
    
    return jsonify({
        "message": "Agent config updated successfully",
        "config": response.data[0]
    }), 200


@tenant_bp.route('/phone-numbers', methods=['GET'])
@require_tenant
@handle_errors
def get_phone_numbers(user_id, tenant_id, role):
    """
    Get all phone numbers for tenant
    """
    supabase = current_app.supabase_client
    
    response = supabase_call_with_retry(
        lambda: supabase.table("phone_numbers").select("*").eq("tenant_id", tenant_id).execute()
    )
    
    return jsonify({
        "phone_numbers": response.data
    }), 200


@tenant_bp.route('/users', methods=['GET'])
@require_tenant
@handle_errors
def get_tenant_users(user_id, tenant_id, role):
    """
    Get all users for tenant
    """
    supabase = current_app.supabase_client
    
    # Get tenant users with auth user details
    response = supabase_call_with_retry(
        lambda: supabase.table("tenant_users")
        .select("id, user_id, role, created_at")
        .eq("tenant_id", tenant_id)
        .execute()
    )
    
    return jsonify({
        "users": response.data
    }), 200


@tenant_bp.route('/users', methods=['POST'])
@require_role(['owner', 'admin'])
@handle_errors
def invite_user(user_id, tenant_id, role):
    """
    Invite a new user to tenant
    Requires owner or admin role
    
    Note: This creates the tenant_user link. The user must already exist in auth.users
    For a full invite flow, you'd send an email with a signup link that includes tenant_id
    """
    data = request.get_json()
    
    if not data.get('user_id') or not data.get('role'):
        return jsonify({"error": "user_id and role are required"}), 400
    
    if data['role'] not in ['owner', 'admin', 'agent', 'viewer']:
        return jsonify({"error": "Invalid role"}), 400
    
    supabase = current_app.supabase_client
    
    # Check if user already exists in tenant
    existing = supabase_call_with_retry(
        lambda: supabase.table("tenant_users")
        .select("id")
        .eq("tenant_id", tenant_id)
        .eq("user_id", data["user_id"])
        .execute()
    )
    
    if existing.data and len(existing.data) > 0:
        return jsonify({"error": "User already exists in this tenant"}), 400
    
    # Create tenant_user link
    tenant_user_data = {
        "tenant_id": tenant_id,
        "user_id": data['user_id'],
        "role": data['role']
    }
    
    response = supabase_call_with_retry(
        lambda: supabase.table("tenant_users").insert(tenant_user_data).execute()
    )
    
    if not response.data or len(response.data) == 0:
        return jsonify({"error": "Failed to add user to tenant"}), 500
    
    return jsonify({
        "message": "User added to tenant successfully",
        "tenant_user": response.data[0]
    }), 201


@tenant_bp.route('/voices', methods=['GET'])
@require_tenant
@handle_errors
def list_voices(user_id, tenant_id, role):
    """
    List all available AI voices that the tenant can select.
    """
    voices = [{k: v[k] for k in _VOICE_PUBLIC_FIELDS} for v in AVAILABLE_VOICES]
    return jsonify({"voices": voices}), 200


@tenant_bp.route('/voices/<voice_id>/preview', methods=['GET'])
@require_tenant
@handle_errors
def preview_voice(user_id, tenant_id, role, voice_id):
    """
    Stream an audio preview of the requested voice.
    Uses OpenAI TTS as an approximation — the live call uses Twilio TTS.
    """
    voice = get_voice_by_id(voice_id)
    if not voice:
        return jsonify({"error": "Voice not found"}), 404

    openai_client = current_app.openai_client
    try:
        tts_response = openai_client.audio.speech.create(
            model="tts-1",
            voice=voice["openai_preview_voice"],
            input=_VOICE_PREVIEW_TEXT,
            response_format="mp3",
        )
    except Exception as e:
        status_code = getattr(e, "status_code", None)
        if status_code == 429 or "insufficient_quota" in str(e):
            return jsonify({
                "error": "Voice preview unavailable — OpenAI TTS quota exceeded. Please top up your OpenAI account."
            }), 503
        raise

    return Response(
        tts_response.content,
        mimetype="audio/mpeg",
        headers={
            "Content-Disposition": f'inline; filename="{voice_id}-preview.mp3"',
            "Cache-Control": "public, max-age=3600",
        },
    )


@tenant_bp.route('/voice-selection', methods=['GET'])
@require_tenant
@handle_errors
def get_voice_selection(user_id, tenant_id, role):
    """
    Return the tenant's current voice selection with full metadata.
    Falls back to the default voice if none has been set.
    """
    supabase = current_app.supabase_client

    response = supabase_call_with_retry(
        lambda: supabase.table("tenant_agent_config")
        .select("voice_id")
        .eq("tenant_id", tenant_id)
        .execute()
    )

    voice_id = None
    if response.data and len(response.data) > 0:
        voice_id = response.data[0].get("voice_id")

    voice = get_voice_by_id(voice_id) if voice_id else None
    if not voice:
        voice = get_default_voice()

    return jsonify({"voice": {k: voice[k] for k in _VOICE_PUBLIC_FIELDS}}), 200


@tenant_bp.route('/voice-selection', methods=['PUT'])
@require_role(['owner', 'admin'])
@handle_errors
def set_voice_selection(user_id, tenant_id, role):
    """
    Set the tenant's AI agent voice.
    Requires owner or admin role.
    Body: { "voice_id": "<id from GET /tenant/voices>" }
    """
    data = request.get_json() or {}
    voice_id = data.get("voice_id")

    if not voice_id:
        return jsonify({"error": "voice_id is required"}), 400

    voice = get_voice_by_id(voice_id)
    if not voice:
        return jsonify({"error": f"Invalid voice_id '{voice_id}'. Use GET /tenant/voices for valid options."}), 400

    supabase = current_app.supabase_client

    response = supabase_call_with_retry(
        lambda: supabase.table("tenant_agent_config")
        .update({"voice_id": voice_id})
        .eq("tenant_id", tenant_id)
        .execute()
    )

    if not response.data or len(response.data) == 0:
        return jsonify({"error": "Failed to update voice selection"}), 500

    return jsonify({
        "message": "Voice updated successfully",
        "voice": {k: voice[k] for k in _VOICE_PUBLIC_FIELDS},
    }), 200


@tenant_bp.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "tenant"
    }), 200

