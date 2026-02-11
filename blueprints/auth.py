from flask import Blueprint, request, jsonify, current_app
from functools import wraps
import traceback

auth_bp = Blueprint('auth', __name__)

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


@auth_bp.route('/signup', methods=['POST'])
@handle_errors
def signup():
    """
    Sign up a new company (tenant) with owner user
    
    Flow:
    1. Create user in Supabase Auth
    2. Create tenant record
    3. Create tenant_user linking user to tenant with role='owner'
    
    Request body:
    {
        "email": "owner@company.com",
        "password": "securepassword",
        "company_name": "Acme Inc",
        "timezone": "America/Toronto",
        "industry": "Healthcare",
        "default_email_recipients": ["notifications@company.com"]
    }
    """
    data = request.get_json()
    
    # Validate required fields
    if not data.get('email') or not data.get('password'):
        return jsonify({"error": "Email and password are required"}), 400
    
    if not data.get('company_name'):
        return jsonify({"error": "Company name is required"}), 400
    
    supabase = current_app.supabase_client
    
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500
    
    # Step 1: Create user in Supabase Auth
    try:
        auth_response = supabase.auth.sign_up({
            "email": data['email'],
            "password": data['password'],
            "options": {
                "email_redirect_to": None  # Disable email confirmation redirect
            }
        })
        
        if not auth_response.user:
            return jsonify({"error": "Failed to create user"}), 400
        
        user_id = auth_response.user.id
        
    except Exception as e:
        error_msg = str(e).lower()
        
        # Handle specific Supabase Auth errors
        if "already registered" in error_msg or "user already registered" in error_msg:
            return jsonify({
                "error": "Email already registered",
                "message": "This email address is already in use. Please sign in instead."
            }), 400
        
        if "rate limit" in error_msg or "rate_limit" in error_msg:
            return jsonify({
                "error": "Email rate limit exceeded",
                "message": "Too many signup attempts. Please wait a few minutes and try again, or disable email confirmation in Supabase Auth settings for development.",
                "solution": "Go to Supabase Dashboard > Authentication > Settings > Disable 'Enable email confirmations' for development"
            }), 429
        
        if "invalid" in error_msg and "email" in error_msg:
            return jsonify({
                "error": "Invalid email address",
                "message": "Please provide a valid email address."
            }), 400
        
        if "password" in error_msg and ("weak" in error_msg or "short" in error_msg):
            return jsonify({
                "error": "Password too weak",
                "message": "Password must be at least 6 characters long."
            }), 400
        
        # Generic error
        return jsonify({
            "error": "Authentication error",
            "message": str(e),
            "details": "If this persists, check Supabase Auth settings and rate limits."
        }), 400
    
    # Step 2: Create tenant record
    # Use RPC function to bypass RLS, or direct insert with service role
    try:
        # Try using RPC function first (if it exists)
        try:
            rpc_response = supabase.rpc('create_tenant', {
                'p_name': data['company_name'],
                'p_timezone': data.get('timezone', 'America/Toronto'),
                'p_industry': data.get('industry'),
                'p_default_email_recipients': data.get('default_email_recipients', [data['email']])
            }).execute()
            
            if rpc_response.data:
                tenant_id = rpc_response.data
            else:
                raise Exception("RPC function returned no data")
                
        except Exception as rpc_error:
            # Fallback to direct insert (service role should bypass RLS)
            # If RPC doesn't exist or fails, try direct insert
            tenant_data = {
                "name": data['company_name'],
                "timezone": data.get('timezone', 'America/Toronto'),
                "industry": data.get('industry'),
                "status": "active",
                "default_email_recipients": data.get('default_email_recipients', [data['email']])
            }
            
            tenant_response = supabase.table('tenants').insert(tenant_data).execute()
            
            if not tenant_response.data or len(tenant_response.data) == 0:
                return jsonify({
                    "error": "Failed to create tenant",
                    "details": "Service role key may not be configured correctly. Ensure SUPABASE_SECRET_KEY is the service_role key, not the anon key."
                }), 500
            
            tenant_id = tenant_response.data[0]['id']
        
    except Exception as e:
        error_msg = str(e)
        if "row-level security" in error_msg.lower() or "42501" in error_msg:
            return jsonify({
                "error": "Failed to create tenant",
                "message": "RLS policy blocking tenant creation. Please run fix_rls_service_role.sql in Supabase SQL Editor.",
                "details": error_msg,
                "solution": "1. Run fix_rls_service_role.sql in Supabase SQL Editor\n2. Verify SUPABASE_SECRET_KEY is the service_role key (not anon key)\n3. Service role key should start with 'eyJ...' and be much longer than anon key"
            }), 500
        return jsonify({"error": f"Failed to create tenant: {error_msg}"}), 500
    
    # Step 3: Create tenant_user linking user to tenant
    try:
        tenant_user_data = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "role": "owner"
        }
        
        tenant_user_response = supabase.table('tenant_users').insert(tenant_user_data).execute()
        
        if not tenant_user_response.data or len(tenant_user_response.data) == 0:
            return jsonify({"error": "Failed to link user to tenant"}), 500
        
    except Exception as e:
        return jsonify({"error": f"Failed to create tenant user: {str(e)}"}), 500
    
    # Step 4: Create default agent config for tenant
    try:
        agent_config_data = {
            "tenant_id": tenant_id,
            "greeting": "Thank you for calling. How may I assist you today?",
            "tone": "professional",
            "business_hours": {},
            "escalation_rules": {},
            "allowed_actions": [],
            "store_transcripts": True,
            "store_recordings": True,
            "retention_days": 90
        }
        
        supabase.table('tenant_agent_config').insert(agent_config_data).execute()
        
    except Exception as e:
        print(f"Warning: Failed to create default agent config: {str(e)}")
        # Non-critical, don't fail the signup
    
    return jsonify({
        "message": "Signup successful",
        "user": {
            "id": user_id,
            "email": data['email']
        },
        "tenant": {
            "id": tenant_id,
            "name": data['company_name'],
            "role": "owner"
        },
        "session": {
            "access_token": auth_response.session.access_token if auth_response.session else None,
            "refresh_token": auth_response.session.refresh_token if auth_response.session else None
        }
    }), 201


@auth_bp.route('/signin', methods=['POST'])
@handle_errors
def signin():
    """
    Sign in existing user
    
    Request body:
    {
        "email": "user@company.com",
        "password": "password"
    }
    
    Returns user info with tenant information
    """
    data = request.get_json()
    
    # Validate required fields
    if not data.get('email') or not data.get('password'):
        return jsonify({"error": "Email and password are required"}), 400
    
    supabase = current_app.supabase_client
    
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500
    
    # Sign in with Supabase Auth
    try:
        auth_response = supabase.auth.sign_in_with_password({
            "email": data['email'],
            "password": data['password']
        })
        
        if not auth_response.user:
            return jsonify({"error": "Invalid credentials"}), 401
        
        user_id = auth_response.user.id
        
    except Exception as e:
        error_msg = str(e)
        if "invalid" in error_msg.lower() or "credentials" in error_msg.lower():
            return jsonify({"error": "Invalid email or password"}), 401
        return jsonify({"error": f"Authentication error: {error_msg}"}), 401
    
    # Get user's tenant information
    try:
        tenant_user_response = supabase.table('tenant_users').select(
            'tenant_id, role, tenants(*)'
        ).eq('user_id', user_id).execute()
        
        if not tenant_user_response.data or len(tenant_user_response.data) == 0:
            return jsonify({
                "error": "User not associated with any tenant. Please contact support."
            }), 404
        
        # User might belong to multiple tenants, return the first one (primary)
        tenant_info = tenant_user_response.data[0]
        
        return jsonify({
            "message": "Sign in successful",
            "user": {
                "id": user_id,
                "email": auth_response.user.email
            },
            "tenant": {
                "id": tenant_info['tenant_id'],
                "name": tenant_info['tenants']['name'],
                "role": tenant_info['role'],
                "timezone": tenant_info['tenants']['timezone'],
                "industry": tenant_info['tenants']['industry'],
                "status": tenant_info['tenants']['status']
            },
            "session": {
                "access_token": auth_response.session.access_token,
                "refresh_token": auth_response.session.refresh_token,
                "expires_at": auth_response.session.expires_at
            }
        }), 200
        
    except Exception as e:
        return jsonify({"error": f"Failed to fetch tenant info: {str(e)}"}), 500


@auth_bp.route('/signout', methods=['POST'])
@handle_errors
def signout():
    """
    Sign out current user
    Requires Authorization header with Bearer token
    """
    auth_header = request.headers.get('Authorization')
    
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({"error": "Authorization token required"}), 401
    
    supabase = current_app.supabase_client
    
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500
    
    try:
        supabase.auth.sign_out()
        return jsonify({"message": "Sign out successful"}), 200
    except Exception as e:
        return jsonify({"error": f"Sign out failed: {str(e)}"}), 500


@auth_bp.route('/refresh', methods=['POST'])
@handle_errors
def refresh_token():
    """
    Refresh access token using refresh token
    
    Request body:
    {
        "refresh_token": "refresh_token_here"
    }
    """
    data = request.get_json()
    
    if not data.get('refresh_token'):
        return jsonify({"error": "Refresh token required"}), 400
    
    supabase = current_app.supabase_client
    
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500
    
    try:
        auth_response = supabase.auth.refresh_session(data['refresh_token'])
        
        if not auth_response.session:
            return jsonify({"error": "Failed to refresh token"}), 401
        
        return jsonify({
            "message": "Token refreshed successfully",
            "session": {
                "access_token": auth_response.session.access_token,
                "refresh_token": auth_response.session.refresh_token,
                "expires_at": auth_response.session.expires_at
            }
        }), 200
        
    except Exception as e:
        return jsonify({"error": f"Token refresh failed: {str(e)}"}), 401


@auth_bp.route('/me', methods=['GET'])
@handle_errors
def get_current_user():
    """
    Get current user information
    Requires Authorization header with Bearer token
    """
    auth_header = request.headers.get('Authorization')
    
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({"error": "Authorization token required"}), 401
    
    token = auth_header.split(' ')[1]
    
    supabase = current_app.supabase_client
    
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500
    
    try:
        # Get user from token
        user_response = supabase.auth.get_user(token)
        
        if not user_response.user:
            return jsonify({"error": "Invalid token"}), 401
        
        user_id = user_response.user.id
        
        # Get tenant information
        tenant_user_response = supabase.table('tenant_users').select(
            'tenant_id, role, tenants(*)'
        ).eq('user_id', user_id).execute()
        
        if not tenant_user_response.data or len(tenant_user_response.data) == 0:
            return jsonify({
                "error": "User not associated with any tenant"
            }), 404
        
        tenant_info = tenant_user_response.data[0]
        
        return jsonify({
            "user": {
                "id": user_id,
                "email": user_response.user.email
            },
            "tenant": {
                "id": tenant_info['tenant_id'],
                "name": tenant_info['tenants']['name'],
                "role": tenant_info['role'],
                "timezone": tenant_info['tenants']['timezone'],
                "industry": tenant_info['tenants']['industry'],
                "status": tenant_info['tenants']['status']
            }
        }), 200
        
    except Exception as e:
        return jsonify({"error": f"Failed to get user info: {str(e)}"}), 401


@auth_bp.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "auth"
    }), 200

