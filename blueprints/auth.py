from flask import Blueprint, request, jsonify, current_app
from functools import wraps
import smtplib
import traceback
import secrets
from datetime import datetime, timedelta
from email.message import EmailMessage

from config import Config

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


def _extract_action_link(generate_link_response):
    """Extract recovery action link from different Supabase client response shapes."""
    if generate_link_response is None:
        return None

    if isinstance(generate_link_response, dict):
        if generate_link_response.get("action_link"):
            return generate_link_response.get("action_link")
        if isinstance(generate_link_response.get("properties"), dict):
            return generate_link_response["properties"].get("action_link")

    if hasattr(generate_link_response, "action_link"):
        return getattr(generate_link_response, "action_link")

    properties = getattr(generate_link_response, "properties", None)
    if isinstance(properties, dict):
        return properties.get("action_link")
    if properties is not None and hasattr(properties, "action_link"):
        return getattr(properties, "action_link")

    return None


def _send_reset_email_via_smtp(email_to, reset_link):
    """Send password reset email using SMTP instead of Supabase email service."""
    smtp_user = Config.ADMIN_EMAIL
    smtp_password = Config.ADMIN_APP_PASSWORD
    smtp_host = Config.SMTP_HOST
    smtp_port = Config.SMTP_PORT
    from_email = Config.SMTP_FROM_EMAIL or smtp_user or ""
    smtp_timeout = Config.SMTP_TIMEOUT_SECONDS

    if not smtp_user or not smtp_password:
        raise ValueError("SMTP credentials not configured (ADMIN_EMAIL / ADMIN_APP_PASSWORD)")

    msg = EmailMessage()
    msg["Subject"] = "AiDan Pro - Reset your password"
    msg["From"] = from_email
    msg["To"] = email_to
    msg.set_content(
        "AiDan Pro Password Reset\n\n"
        "We received a request to reset your password.\n\n"
        f"Reset your password using this link:\n{reset_link}\n\n"
        "This link expires in 60 minutes.\n\n"
        "If you did not request this, you can ignore this email."
    )

    html_content = f"""
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>AiDan Pro - Reset your password</title>
      </head>
      <body style="margin:0; padding:0; background-color:#f3f4f6; font-family:Arial, Helvetica, sans-serif; color:#1f2937;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color:#f3f4f6; padding:24px 12px;">
          <tr>
            <td align="center">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:600px; background:#ffffff; border-radius:14px; overflow:hidden; box-shadow:0 10px 30px rgba(0,0,0,0.08);">
                <tr>
                  <td style="background:linear-gradient(135deg, #0047ab 0%, #0b5ed7 100%); padding:24px;">
                    <p style="margin:0; font-size:24px; line-height:1.3; font-weight:700; color:#ffffff;">AiDan Pro</p>
                    <p style="margin:8px 0 0; font-size:14px; line-height:1.4; color:#e5edff;">Secure account recovery</p>
                  </td>
                </tr>
                <tr>
                  <td style="padding:28px 24px 20px;">
                    <h1 style="margin:0 0 12px; font-size:22px; line-height:1.3; color:#111827;">Reset your password</h1>
                    <p style="margin:0 0 16px; font-size:15px; line-height:1.6; color:#374151;">
                      We received a request to reset your password. Click the button below to choose a new password.
                    </p>
                    <table role="presentation" cellspacing="0" cellpadding="0" style="margin:0 0 18px;">
                      <tr>
                        <td align="center" bgcolor="#0047ab" style="border-radius:10px;">
                          <a href="{reset_link}" style="display:inline-block; padding:12px 24px; font-size:15px; font-weight:700; color:#ffffff; text-decoration:none; border-radius:10px;">
                            Reset Password
                          </a>
                        </td>
                      </tr>
                    </table>
                    <p style="margin:0 0 8px; font-size:13px; line-height:1.6; color:#6b7280;">
                      This reset link expires in <strong>60 minutes</strong>.
                    </p>
                    <p style="margin:0; font-size:13px; line-height:1.6; color:#6b7280;">
                      If you did not request this, you can safely ignore this email.
                    </p>
                  </td>
                </tr>
                <tr>
                  <td style="padding:16px 24px 22px; border-top:1px solid #e5e7eb;">
                    <p style="margin:0 0 6px; font-size:12px; line-height:1.5; color:#6b7280;">
                      Button not working? Copy and paste this link into your browser:
                    </p>
                    <p style="margin:0; font-size:12px; line-height:1.5; word-break:break-all;">
                      <a href="{reset_link}" style="color:#0047ab; text-decoration:underline;">{reset_link}</a>
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </body>
    </html>
    """
    msg.add_alternative(html_content, subtype="html")

    with smtplib.SMTP(smtp_host, smtp_port, timeout=smtp_timeout) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)


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
    
    # Check if user is a super admin
    is_super_admin = False
    try:
        sa_response = supabase.table('super_admins').select('id').eq('user_id', user_id).execute()
        is_super_admin = bool(sa_response.data)
    except Exception as e:
        print(f"[WARN] super_admins check failed for {user_id}: {e}")

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
                "email": auth_response.user.email,
                "is_super_admin": is_super_admin
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


@auth_bp.route('/forgot-password', methods=['POST'])
@handle_errors
def forgot_password():
    """
    Send password reset email using SMTP (not Supabase email sending)

    Request body:
    {
        "email": "user@company.com",
        "redirect_to": "https://your-frontend.com/reset-password"  # optional
    }
    """
    data = request.get_json() or {}
    email = data.get('email')

    if not email:
        return jsonify({"error": "Email is required"}), 400

    email = email.lower().strip()

    supabase = current_app.supabase_client

    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500

    try:
        # Verify user exists (don't reveal if email exists or not in response)
        users_response = supabase.auth.admin.list_users()
        users_list = users_response if isinstance(users_response, list) else getattr(users_response, 'users', [])
        
        user_exists = False
        for user in users_list:
            user_email = getattr(user, 'email', None) if hasattr(user, 'email') else user.get('email') if isinstance(user, dict) else None
            if user_email and user_email.lower() == email:
                user_exists = True
                break
        
        if user_exists:
            # Generate secure token
            reset_token = secrets.token_urlsafe(32)
            expires_at = datetime.utcnow() + timedelta(hours=1)  # Token valid for 1 hour
            
            # Store token in Supabase
            try:
                supabase.table('password_reset_tokens').insert({
                    "token": reset_token,
                    "email": email,
                    "expires_at": expires_at.isoformat()
                }).execute()
                
                print(f"[DEBUG forgot_password] Token stored in Supabase for {email}, expires at {expires_at}")
            except Exception as token_error:
                print(f"[ERROR forgot_password] Failed to store token: {str(token_error)}")
                raise
            
            # Clean up expired tokens
            _cleanup_expired_tokens(supabase)
            
            # Build reset link
            redirect_to = data.get('redirect_to') or Config.PASSWORD_RESET_REDIRECT_URL
            reset_link = f"{redirect_to}?token={reset_token}&email={email}"
            
            # Send email, but do not block API success on transient SMTP issues.
            try:
                _send_reset_email_via_smtp(email_to=email, reset_link=reset_link)
            except Exception as email_error:
                print(f"[WARNING forgot_password] Email send failed for {email}: {str(email_error)}")
            
            print(f"[DEBUG forgot_password] Generated token for {email}, expires at {expires_at}")

        # Always return success message (don't reveal if email exists)
        return jsonify({
            "message": "If the email exists, a password reset link has been sent."
        }), 200

    except Exception as e:
        error_msg = str(e).lower()
        if "authentication failed" in error_msg or "username and password not accepted" in error_msg:
            return jsonify({
                "error": "SMTP authentication failed",
                "message": "Check ADMIN_EMAIL and ADMIN_APP_PASSWORD values."
            }), 500
        return jsonify({"error": f"Failed to send reset email: {str(e)}"}), 400


def _cleanup_expired_tokens(supabase):
    """Remove expired tokens from Supabase"""
    try:
        now = datetime.utcnow().isoformat()
        result = supabase.table('password_reset_tokens').delete().lt('expires_at', now).execute()
        if result.data:
            print(f"[DEBUG] Cleaned up {len(result.data)} expired tokens")
    except Exception as e:
        print(f"[WARNING] Failed to cleanup expired tokens: {str(e)}")


@auth_bp.route('/reset-password', methods=['POST'])
@handle_errors
def reset_password():
    """
    Reset user password after receiving the reset email link.

    Use either:
    - Authorization: Bearer <access_token_from_reset_link>
    OR
    - request body field "access_token"
    OR
    - request body with "token" (from email link) + "email" (user's email)

    Request body:
    {
        "new_password": "new_secure_password",
        "access_token": "optional_if_not_using_authorization_header",
        "token": "optional_recovery_token_from_email_link",
        "email": "required_when_using_token"
    }
    """
    data = request.get_json() or {}
    new_password = data.get('new_password')

    if not new_password:
        return jsonify({"error": "New password is required"}), 400

    if len(new_password) < 6:
        return jsonify({
            "error": "Password too weak",
            "message": "Password must be at least 6 characters long."
        }), 400

    auth_header = request.headers.get('Authorization')
    token = None

    if auth_header and auth_header.startswith('Bearer '):
        token = auth_header.split(' ')[1]
    else:
        token = data.get('access_token')

    supabase = current_app.supabase_client

    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500

    recovery_token = data.get("token")
    recovery_email = data.get("email")

    try:
        print(f"[DEBUG reset_password] Received: token={bool(token)}, recovery_token={bool(recovery_token)}, recovery_email={recovery_email}")
        
        user_id = None

        # Method 1: Use access_token directly
        if token:
            print(f"[DEBUG reset_password] Method 1: Verifying access_token with get_user")
            user_response = supabase.auth.get_user(token)
            if user_response.user:
                user_id = user_response.user.id
                print(f"[DEBUG reset_password] get_user successful, user_id={user_id}")

        # Method 2: Use recovery token + email (with proper token verification)
        elif recovery_token and recovery_email:
            print(f"[DEBUG reset_password] Method 2: Verifying recovery token")
            
            # Normalize email to lowercase
            recovery_email = recovery_email.lower().strip()
            print(f"[DEBUG reset_password] Normalized email: {recovery_email}")
            
            # Verify token exists in Supabase
            try:
                token_response = supabase.table('password_reset_tokens').select('*').eq('token', recovery_token).execute()
                
                if not token_response.data or len(token_response.data) == 0:
                    print(f"[ERROR reset_password] Token not found in Supabase")
                    return jsonify({"error": "Invalid or expired token"}), 401
                
                token_data = token_response.data[0]
                
                # Check if token expired
                token_expires_at = datetime.fromisoformat(token_data["expires_at"].replace('Z', '+00:00'))
                if datetime.utcnow().replace(tzinfo=token_expires_at.tzinfo) > token_expires_at:
                    print(f"[ERROR reset_password] Token expired at {token_expires_at}")
                    # Delete expired token
                    supabase.table('password_reset_tokens').delete().eq('token', recovery_token).execute()
                    return jsonify({"error": "Invalid or expired token"}), 401
                
                # Check if email matches
                if token_data["email"] != recovery_email:
                    print(f"[ERROR reset_password] Email mismatch: expected {token_data['email']}, got {recovery_email}")
                    return jsonify({"error": "Invalid or expired token"}), 401
                
                print(f"[DEBUG reset_password] Token verified successfully for {recovery_email}")
                
            except Exception as token_error:
                print(f"[ERROR reset_password] Token verification failed: {str(token_error)}")
                traceback.print_exc()
                return jsonify({"error": "Invalid or expired token"}), 401
            
            # Look up user by email using admin API
            try:
                users_response = supabase.auth.admin.list_users()
                users_list = users_response if isinstance(users_response, list) else getattr(users_response, 'users', [])
                
                target_user = None
                for user in users_list:
                    user_email = getattr(user, 'email', None) if hasattr(user, 'email') else user.get('email') if isinstance(user, dict) else None
                    if user_email and user_email.lower() == recovery_email:
                        target_user = user
                        break
                
                if target_user:
                    user_id = getattr(target_user, 'id', None) if hasattr(target_user, 'id') else target_user.get('id') if isinstance(target_user, dict) else None
                    print(f"[DEBUG reset_password] Found user by email, user_id={user_id}")
                else:
                    print(f"[ERROR reset_password] User not found with email: {recovery_email}")
                    return jsonify({"error": "Invalid or expired token"}), 401
                    
            except Exception as lookup_error:
                print(f"[ERROR reset_password] Failed to lookup user: {str(lookup_error)}")
                traceback.print_exc()
                return jsonify({"error": "Invalid or expired token"}), 401
        
        else:
            print(f"[ERROR reset_password] No valid authentication method provided")
            return jsonify({
                "error": "Either access_token or (token + email) is required"
            }), 401

        if not user_id:
            print(f"[ERROR reset_password] No user_id obtained, returning 401")
            return jsonify({"error": "Invalid or expired token"}), 401

        print(f"[DEBUG reset_password] Updating password for user_id={user_id}")
        
        # Retry logic for Supabase admin API (can timeout)
        max_retries = 3
        password_updated = False
        
        for attempt in range(max_retries):
            try:
                supabase.auth.admin.update_user_by_id(user_id, {"password": new_password})
                print(f"[SUCCESS reset_password] Password updated successfully on attempt {attempt + 1}")
                password_updated = True
                break
            except Exception as update_error:
                error_str = str(update_error)
                print(f"[WARNING reset_password] Attempt {attempt + 1}/{max_retries} failed: {error_str}")
                
                if attempt < max_retries - 1 and ("timeout" in error_str.lower() or "timed out" in error_str.lower()):
                    print(f"[DEBUG reset_password] Retrying after timeout...")
                    import time
                    time.sleep(1)  # Brief delay before retry
                    continue
                else:
                    # Re-raise on last attempt or non-timeout errors
                    raise
        
        # Only delete token after successful password update
        if password_updated and recovery_token:
            try:
                supabase.table('password_reset_tokens').delete().eq('token', recovery_token).execute()
                print(f"[DEBUG reset_password] Token deleted from Supabase after successful password update")
            except Exception as delete_error:
                print(f"[WARNING reset_password] Failed to delete token: {str(delete_error)}")
        
        return jsonify({"message": "Password reset successful"}), 200

    except Exception as e:
        print(f"[ERROR reset_password] Exception caught: {str(e)}")
        traceback.print_exc()
        error_msg = str(e).lower()
        if "password" in error_msg and ("weak" in error_msg or "short" in error_msg):
            return jsonify({
                "error": "Password too weak",
                "message": "Password must be at least 6 characters long."
            }), 400
        return jsonify({"error": f"Password reset failed: {str(e)}"}), 400


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

        # Check super admin status
        is_super_admin = False
        try:
            sa_response = supabase.table('super_admins').select('id').eq('user_id', user_id).execute()
            is_super_admin = bool(sa_response.data)
        except Exception as e:
            print(f"[WARN] super_admins check failed for {user_id}: {e}")

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
                "email": user_response.user.email,
                "is_super_admin": is_super_admin
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

