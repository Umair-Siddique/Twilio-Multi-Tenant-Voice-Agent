"""
Tenant management endpoints
These demonstrate the auth flow and provide tenant configuration APIs
"""
from flask import Blueprint, request, jsonify, current_app
from utils.auth_utils import require_tenant, require_role
from functools import wraps
import traceback

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
    tenant_response = supabase.table('tenants').select('*').eq('id', tenant_id).execute()
    
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
    response = supabase.table('tenants').update(update_data).eq('id', tenant_id).execute()
    
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
    
    response = supabase.table('tenant_agent_config').select('*').eq('tenant_id', tenant_id).execute()
    
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
        'greeting', 'tone', 'business_hours', 'escalation_rules',
        'allowed_actions', 'custom_prompts', 'store_transcripts',
        'store_recordings', 'retention_days'
    ]
    
    update_data = {k: v for k, v in data.items() if k in allowed_fields}
    
    if not update_data:
        return jsonify({"error": "No valid fields to update"}), 400
    
    # Update agent config
    response = supabase.table('tenant_agent_config').update(
        update_data
    ).eq('tenant_id', tenant_id).execute()
    
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
    
    response = supabase.table('phone_numbers').select('*').eq('tenant_id', tenant_id).execute()
    
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
    response = supabase.table('tenant_users').select(
        'id, user_id, role, created_at'
    ).eq('tenant_id', tenant_id).execute()
    
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
    existing = supabase.table('tenant_users').select('id').eq(
        'tenant_id', tenant_id
    ).eq('user_id', data['user_id']).execute()
    
    if existing.data and len(existing.data) > 0:
        return jsonify({"error": "User already exists in this tenant"}), 400
    
    # Create tenant_user link
    tenant_user_data = {
        "tenant_id": tenant_id,
        "user_id": data['user_id'],
        "role": data['role']
    }
    
    response = supabase.table('tenant_users').insert(tenant_user_data).execute()
    
    if not response.data or len(response.data) == 0:
        return jsonify({"error": "Failed to add user to tenant"}), 500
    
    return jsonify({
        "message": "User added to tenant successfully",
        "tenant_user": response.data[0]
    }), 201


@tenant_bp.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "tenant"
    }), 200

