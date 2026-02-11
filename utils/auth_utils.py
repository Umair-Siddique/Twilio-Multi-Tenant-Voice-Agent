"""
Utility functions and middleware for authentication and tenant management
"""
from flask import request, jsonify, current_app
from functools import wraps


def get_supabase_client():
    """Get Supabase client from current app"""
    return current_app.supabase_client


def verify_token(token):
    """
    Verify JWT token and return user info
    Returns: (user_id, error)
    """
    try:
        supabase = get_supabase_client()
        if not supabase:
            return None, "Supabase not configured"
        
        user_response = supabase.auth.get_user(token)
        
        if not user_response.user:
            return None, "Invalid token"
        
        return user_response.user.id, None
        
    except Exception as e:
        return None, str(e)


def get_tenant_from_user(user_id):
    """
    Get tenant_id and role for a user
    Returns: (tenant_id, role, error)
    """
    try:
        supabase = get_supabase_client()
        if not supabase:
            return None, None, "Supabase not configured"
        
        response = supabase.table('tenant_users').select(
            'tenant_id, role'
        ).eq('user_id', user_id).execute()
        
        if not response.data or len(response.data) == 0:
            return None, None, "User not associated with any tenant"
        
        # Return first tenant (primary)
        tenant_info = response.data[0]
        return tenant_info['tenant_id'], tenant_info['role'], None
        
    except Exception as e:
        return None, None, str(e)


def require_auth(f):
    """
    Decorator to require authentication
    Adds user_id to kwargs
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({"error": "Authorization token required"}), 401
        
        token = auth_header.split(' ')[1]
        user_id, error = verify_token(token)
        
        if error:
            return jsonify({"error": error}), 401
        
        kwargs['user_id'] = user_id
        return f(*args, **kwargs)
    
    return decorated_function


def require_tenant(f):
    """
    Decorator to require authentication and tenant membership
    Adds user_id, tenant_id, and role to kwargs
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({"error": "Authorization token required"}), 401
        
        token = auth_header.split(' ')[1]
        user_id, error = verify_token(token)
        
        if error:
            return jsonify({"error": error}), 401
        
        tenant_id, role, error = get_tenant_from_user(user_id)
        
        if error:
            return jsonify({"error": error}), 404
        
        kwargs['user_id'] = user_id
        kwargs['tenant_id'] = tenant_id
        kwargs['role'] = role
        
        return f(*args, **kwargs)
    
    return decorated_function


def require_role(required_roles):
    """
    Decorator to require specific role(s)
    Usage: @require_role(['owner', 'admin'])
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            auth_header = request.headers.get('Authorization')
            
            if not auth_header or not auth_header.startswith('Bearer '):
                return jsonify({"error": "Authorization token required"}), 401
            
            token = auth_header.split(' ')[1]
            user_id, error = verify_token(token)
            
            if error:
                return jsonify({"error": error}), 401
            
            tenant_id, role, error = get_tenant_from_user(user_id)
            
            if error:
                return jsonify({"error": error}), 404
            
            if role not in required_roles:
                return jsonify({
                    "error": f"Insufficient permissions. Required: {', '.join(required_roles)}"
                }), 403
            
            kwargs['user_id'] = user_id
            kwargs['tenant_id'] = tenant_id
            kwargs['role'] = role
            
            return f(*args, **kwargs)
        
        return decorated_function
    return decorator


def get_tenant_from_phone(phone_number):
    """
    Resolve tenant_id from called phone number (for Twilio webhooks)
    This is the critical function for multi-tenant call routing
    
    Returns: (tenant_id, error)
    """
    try:
        supabase = get_supabase_client()
        if not supabase:
            return None, "Supabase not configured"
        
        response = supabase.table('phone_numbers').select(
            'tenant_id'
        ).eq('phone_number', phone_number).eq('status', 'active').execute()
        
        if not response.data or len(response.data) == 0:
            return None, f"No active tenant found for phone number: {phone_number}"
        
        return response.data[0]['tenant_id'], None
        
    except Exception as e:
        return None, str(e)

