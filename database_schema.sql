-- =====================================================
-- Multi-Tenant AI Call Agent - Database Schema
-- =====================================================
-- This schema implements strict tenant isolation using Row Level Security (RLS)
-- All tables include tenant_id for data separation
-- Auth users are linked through tenant_users table
--
-- IMPORTANT: This script is organized in phases:
-- 1. Create all tables
-- 2. Create all indexes
-- 3. Enable RLS on all tables
-- 4. Create all RLS policies (all tables exist now)
-- 5. Create helper functions
-- 6. Create triggers

-- =====================================================
-- PHASE 1: CREATE ALL TABLES
-- =====================================================

-- 1. TENANTS TABLE
CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    timezone VARCHAR(100) DEFAULT 'America/Toronto',
    industry VARCHAR(100),
    status VARCHAR(50) DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'suspended')),
    default_email_recipients TEXT[], -- Array of email addresses for call reports
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. TENANT USERS TABLE (Links Auth Users to Tenants)
CREATE TABLE tenant_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    role VARCHAR(50) DEFAULT 'agent' CHECK (role IN ('owner', 'admin', 'agent', 'viewer')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, user_id)
);

-- 3. PHONE NUMBERS TABLE
CREATE TABLE phone_numbers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    phone_number VARCHAR(20) NOT NULL UNIQUE, -- E.164 format
    twilio_number_sid VARCHAR(100) UNIQUE,
    country_code VARCHAR(5) DEFAULT 'CA',
    status VARCHAR(50) DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'pending')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. AGENT PACKS TABLE (Industry Templates)
CREATE TABLE agent_packs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    industry VARCHAR(100) NOT NULL,
    description TEXT,
    default_greeting TEXT,
    default_tone VARCHAR(100) DEFAULT 'professional',
    default_rules JSONB DEFAULT '{}', -- Business rules, escalation logic
    default_prompts JSONB DEFAULT '{}', -- System prompts and conversation templates
    default_allowed_actions JSONB DEFAULT '[]', -- Array of action names
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. TENANT AGENT CONFIG TABLE
CREATE TABLE tenant_agent_config (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE UNIQUE,
    agent_pack_id UUID REFERENCES agent_packs(id),
    greeting TEXT,
    tone VARCHAR(100) DEFAULT 'professional',
    business_hours JSONB DEFAULT '{}', -- { "monday": {"start": "09:00", "end": "17:00"}, ... }
    escalation_rules JSONB DEFAULT '{}', -- Rules for when to escalate to human
    allowed_actions JSONB DEFAULT '[]', -- Array of enabled action names
    custom_prompts JSONB DEFAULT '{}', -- Tenant-specific prompt overrides
    store_transcripts BOOLEAN DEFAULT true,
    store_recordings BOOLEAN DEFAULT true,
    retention_days INTEGER DEFAULT 90,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6. CALLS TABLE
CREATE TABLE calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    call_sid VARCHAR(100) UNIQUE NOT NULL, -- Twilio Call SID
    from_number VARCHAR(20) NOT NULL,
    to_number VARCHAR(20) NOT NULL,
    direction VARCHAR(20) DEFAULT 'inbound' CHECK (direction IN ('inbound', 'outbound')),
    status VARCHAR(50) DEFAULT 'initiated' CHECK (status IN ('initiated', 'ringing', 'in-progress', 'completed', 'failed', 'busy', 'no-answer')),
    outcome VARCHAR(100), -- booking_created, lead_captured, escalated, etc.
    start_time TIMESTAMPTZ,
    end_time TIMESTAMPTZ,
    duration_seconds INTEGER,
    extracted_fields JSONB DEFAULT '{}', -- Caller info, intent, extracted data
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 7. CALL MESSAGES TABLE (Conversation Turns)
CREATE TABLE call_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    speaker VARCHAR(20) NOT NULL CHECK (speaker IN ('caller', 'agent', 'system')),
    text TEXT NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 8. RECORDINGS TABLE
CREATE TABLE recordings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    twilio_recording_sid VARCHAR(100) UNIQUE NOT NULL,
    recording_url TEXT NOT NULL, -- Twilio recording URL
    storage_path TEXT, -- Optional: path in Supabase Storage if copied
    duration_seconds INTEGER,
    file_size_bytes BIGINT,
    status VARCHAR(50) DEFAULT 'available' CHECK (status IN ('processing', 'available', 'deleted')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 9. INTEGRATIONS TABLE
CREATE TABLE integrations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    type VARCHAR(100) NOT NULL, -- simplybook, google_calendar, google_meet, zoom, hubspot, sendgrid
    name VARCHAR(255) NOT NULL, -- Display name
    status VARCHAR(50) DEFAULT 'disconnected' CHECK (status IN ('connected', 'disconnected', 'error')),
    connected_at TIMESTAMPTZ,
    last_test_at TIMESTAMPTZ,
    error_message TEXT,
    config JSONB DEFAULT '{}', -- Integration-specific configuration
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, type)
);

-- 10. INTEGRATION CREDENTIALS TABLE (Encrypted)
CREATE TABLE integration_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    integration_id UUID NOT NULL REFERENCES integrations(id) ON DELETE CASCADE UNIQUE,
    encrypted_credentials TEXT NOT NULL, -- Encrypted JSON blob
    access_token_expires_at TIMESTAMPTZ,
    refresh_token_encrypted TEXT, -- For OAuth
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 11. BOOKING REQUESTS TABLE (Fallback when integration unavailable)
CREATE TABLE booking_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    requested_service VARCHAR(255),
    requested_datetime TIMESTAMPTZ,
    caller_name VARCHAR(255),
    caller_phone VARCHAR(20),
    caller_email VARCHAR(255),
    additional_notes TEXT,
    status VARCHAR(50) DEFAULT 'pending' CHECK (status IN ('pending', 'confirmed', 'cancelled', 'completed')),
    processed_at TIMESTAMPTZ,
    processed_by UUID REFERENCES auth.users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 12. EMAIL LOGS TABLE
CREATE TABLE email_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    call_id UUID REFERENCES calls(id) ON DELETE CASCADE,
    email_type VARCHAR(100) NOT NULL, -- call_summary, booking_confirmation, error_notification
    recipients TEXT[] NOT NULL,
    subject VARCHAR(500),
    status VARCHAR(50) DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    error_message TEXT,
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);


-- =====================================================
-- PHASE 2: CREATE ALL INDEXES
-- =====================================================

-- Tenants indexes
CREATE INDEX idx_tenants_status ON tenants(status);

-- Tenant users indexes
CREATE INDEX idx_tenant_users_tenant ON tenant_users(tenant_id);
CREATE INDEX idx_tenant_users_user ON tenant_users(user_id);

-- Phone numbers indexes
CREATE INDEX idx_phone_numbers_tenant ON phone_numbers(tenant_id);
CREATE INDEX idx_phone_numbers_phone ON phone_numbers(phone_number);

-- Agent packs indexes
CREATE INDEX idx_agent_packs_industry ON agent_packs(industry);

-- Tenant agent config indexes
CREATE INDEX idx_tenant_agent_config_tenant ON tenant_agent_config(tenant_id);

-- Calls indexes
CREATE INDEX idx_calls_tenant ON calls(tenant_id);
CREATE INDEX idx_calls_sid ON calls(call_sid);
CREATE INDEX idx_calls_start_time ON calls(tenant_id, start_time DESC);
CREATE INDEX idx_calls_status ON calls(tenant_id, status);

-- Call messages indexes
CREATE INDEX idx_call_messages_tenant ON call_messages(tenant_id);
CREATE INDEX idx_call_messages_call ON call_messages(call_id, timestamp);

-- Recordings indexes
CREATE INDEX idx_recordings_tenant ON recordings(tenant_id);
CREATE INDEX idx_recordings_call ON recordings(call_id);
CREATE INDEX idx_recordings_sid ON recordings(twilio_recording_sid);

-- Integrations indexes
CREATE INDEX idx_integrations_tenant ON integrations(tenant_id);
CREATE INDEX idx_integrations_type ON integrations(tenant_id, type);

-- Integration credentials indexes
CREATE INDEX idx_integration_credentials_tenant ON integration_credentials(tenant_id);
CREATE INDEX idx_integration_credentials_integration ON integration_credentials(integration_id);

-- Booking requests indexes
CREATE INDEX idx_booking_requests_tenant ON booking_requests(tenant_id);
CREATE INDEX idx_booking_requests_status ON booking_requests(tenant_id, status);
CREATE INDEX idx_booking_requests_datetime ON booking_requests(tenant_id, requested_datetime);

-- Email logs indexes
CREATE INDEX idx_email_logs_tenant ON email_logs(tenant_id);
CREATE INDEX idx_email_logs_call ON email_logs(call_id);
CREATE INDEX idx_email_logs_status ON email_logs(tenant_id, status);


-- =====================================================
-- PHASE 3: ENABLE ROW LEVEL SECURITY ON ALL TABLES
-- =====================================================

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE phone_numbers ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_packs ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_agent_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE call_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE recordings ENABLE ROW LEVEL SECURITY;
ALTER TABLE integrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE integration_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE booking_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_logs ENABLE ROW LEVEL SECURITY;


-- =====================================================
-- PHASE 4: CREATE ALL RLS POLICIES
-- (All tables now exist, so policies can reference them)
-- =====================================================

-- Tenants RLS Policies: Separate policies for different operations

-- Policy for SELECT, UPDATE, DELETE: Users can only access their own tenant
CREATE POLICY tenant_isolation_policy ON tenants
    FOR SELECT
    USING (
        id IN (
            SELECT tenant_id FROM tenant_users 
            WHERE user_id = auth.uid()
        )
    );

CREATE POLICY tenant_update_policy ON tenants
    FOR UPDATE
    USING (
        id IN (
            SELECT tenant_id FROM tenant_users 
            WHERE user_id = auth.uid()
        )
    );

CREATE POLICY tenant_delete_policy ON tenants
    FOR DELETE
    USING (
        id IN (
            SELECT tenant_id FROM tenant_users 
            WHERE user_id = auth.uid()
        )
    );

-- Policy for INSERT: Allow authenticated users to create tenants
-- During signup, user creates their own tenant (service role handles validation)
CREATE POLICY tenant_insert_policy ON tenants
    FOR INSERT
    WITH CHECK (auth.uid() IS NOT NULL);

-- Tenant users RLS Policy: Users can see their own tenant memberships
CREATE POLICY tenant_users_policy ON tenant_users
    FOR ALL
    USING (user_id = auth.uid());

-- Phone numbers RLS Policy: Users can only see phone numbers for their tenant
CREATE POLICY phone_numbers_policy ON phone_numbers
    FOR ALL
    USING (
        tenant_id IN (
            SELECT tenant_id FROM tenant_users 
            WHERE user_id = auth.uid()
        )
    );

-- Agent packs RLS Policy: All authenticated users can read agent packs
CREATE POLICY agent_packs_read_policy ON agent_packs
    FOR SELECT
    USING (auth.uid() IS NOT NULL);

-- Tenant agent config RLS Policy: Users can only access config for their tenant
CREATE POLICY tenant_agent_config_policy ON tenant_agent_config
    FOR ALL
    USING (
        tenant_id IN (
            SELECT tenant_id FROM tenant_users 
            WHERE user_id = auth.uid()
        )
    );

-- Calls RLS Policy: Users can only see calls for their tenant
CREATE POLICY calls_policy ON calls
    FOR ALL
    USING (
        tenant_id IN (
            SELECT tenant_id FROM tenant_users 
            WHERE user_id = auth.uid()
        )
    );

-- Call messages RLS Policy: Users can only see messages for their tenant's calls
CREATE POLICY call_messages_policy ON call_messages
    FOR ALL
    USING (
        tenant_id IN (
            SELECT tenant_id FROM tenant_users 
            WHERE user_id = auth.uid()
        )
    );

-- Recordings RLS Policy: Users can only see recordings for their tenant's calls
CREATE POLICY recordings_policy ON recordings
    FOR ALL
    USING (
        tenant_id IN (
            SELECT tenant_id FROM tenant_users 
            WHERE user_id = auth.uid()
        )
    );

-- Integrations RLS Policy: Users can only see integrations for their tenant
CREATE POLICY integrations_policy ON integrations
    FOR ALL
    USING (
        tenant_id IN (
            SELECT tenant_id FROM tenant_users 
            WHERE user_id = auth.uid()
        )
    );

-- Integration credentials RLS Policy: Only owners/admins can see credentials
CREATE POLICY integration_credentials_policy ON integration_credentials
    FOR ALL
    USING (
        tenant_id IN (
            SELECT tenant_id FROM tenant_users 
            WHERE user_id = auth.uid()
            AND role IN ('owner', 'admin')
        )
    );

-- Booking requests RLS Policy: Users can only see booking requests for their tenant
CREATE POLICY booking_requests_policy ON booking_requests
    FOR ALL
    USING (
        tenant_id IN (
            SELECT tenant_id FROM tenant_users 
            WHERE user_id = auth.uid()
        )
    );

-- Email logs RLS Policy: Users can only see email logs for their tenant
CREATE POLICY email_logs_policy ON email_logs
    FOR ALL
    USING (
        tenant_id IN (
            SELECT tenant_id FROM tenant_users 
            WHERE user_id = auth.uid()
        )
    );


-- =====================================================
-- PHASE 5: HELPER FUNCTIONS
-- =====================================================

-- Function to get tenant_id for current user (useful for application code)
CREATE OR REPLACE FUNCTION get_user_tenant_id()
RETURNS UUID AS $$
    SELECT tenant_id FROM tenant_users 
    WHERE user_id = auth.uid() 
    LIMIT 1;
$$ LANGUAGE SQL SECURITY DEFINER;

-- Function to check if user has specific role
CREATE OR REPLACE FUNCTION user_has_role(required_role TEXT)
RETURNS BOOLEAN AS $$
    SELECT EXISTS (
        SELECT 1 FROM tenant_users 
        WHERE user_id = auth.uid() 
        AND role = required_role
    );
$$ LANGUAGE SQL SECURITY DEFINER;

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- =====================================================
-- PHASE 6: TRIGGERS FOR AUTO-UPDATING updated_at
-- =====================================================

CREATE TRIGGER update_tenants_updated_at BEFORE UPDATE ON tenants
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_tenant_users_updated_at BEFORE UPDATE ON tenant_users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_phone_numbers_updated_at BEFORE UPDATE ON phone_numbers
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_agent_packs_updated_at BEFORE UPDATE ON agent_packs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_tenant_agent_config_updated_at BEFORE UPDATE ON tenant_agent_config
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_calls_updated_at BEFORE UPDATE ON calls
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_recordings_updated_at BEFORE UPDATE ON recordings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_integrations_updated_at BEFORE UPDATE ON integrations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_integration_credentials_updated_at BEFORE UPDATE ON integration_credentials
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_booking_requests_updated_at BEFORE UPDATE ON booking_requests
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();


-- =====================================================
-- SAMPLE AGENT PACKS (Optional - can be inserted after schema creation)
-- =====================================================

-- These are commented out but can be run to populate initial agent packs
/*
INSERT INTO agent_packs (name, industry, description, default_greeting, default_tone, default_allowed_actions) VALUES
('Healthcare Receptionist', 'Healthcare', 'Professional medical office assistant', 'Thank you for calling. How may I assist you today?', 'professional', '["create_booking", "reschedule_booking", "cancel_booking", "handoff_to_human"]'),
('Real Estate Agent', 'Real Estate', 'Property inquiry and showing scheduler', 'Hello! Thank you for your interest in our properties. How can I help you today?', 'friendly', '["schedule_meeting", "create_lead", "handoff_to_human"]'),
('Tech Support', 'Technology', 'Technical support and ticket creation', 'Welcome to technical support. Please describe the issue you are experiencing.', 'helpful', '["create_ticket", "handoff_to_human"]'),
('Sales Assistant', 'Sales', 'Lead qualification and follow-up scheduler', 'Thank you for your interest! I would love to learn more about your needs.', 'enthusiastic', '["create_lead", "schedule_meeting", "handoff_to_human"]');
*/


-- =====================================================
-- NOTES ON TENANT FLOW AND AUTH
-- =====================================================

/*
TENANT ONBOARDING FLOW:

1. New Company Signs Up
   - Create user in Supabase Auth (auth.users)
   - Create tenant record in tenants table
   - Create tenant_user record linking user to tenant with role='owner'

2. Tenant Isolation
   - Every table (except agent_packs) has tenant_id
   - RLS policies check tenant_users table to verify user belongs to tenant
   - Backend resolves tenant_id from phone_number for call webhooks

3. Authentication Flow
   - React Admin Portal: Uses Supabase Auth client with user JWT
   - Python Backend: Uses service role key (bypasses RLS) for webhook handling
   - Backend validates Twilio signatures for webhook security

4. Multi-User Support
   - Tenant owner can invite additional users
   - Create auth.users entry + tenant_users entry
   - Roles: owner (full access), admin (config), agent (view only), viewer (read-only)

5. Security Layers
   - RLS enforces tenant isolation at database level
   - Backend enforces tenant resolution via phone_number mapping
   - Integration credentials are encrypted and tenant-scoped
   - Storage paths use tenant_id prefix: tenants/{tenant_id}/recordings/

6. Service vs User Access
   - Backend (service role): Full access, must manually enforce tenant_id in queries
   - React UI (user tokens): RLS automatically filters by tenant_id
   - Recording URLs: Generate signed URLs with expiration

IMPORTANT: When backend handles Twilio webhooks, it must:
1. Validate Twilio signature
2. Look up tenant_id from phone_numbers table using To parameter
3. Use tenant_id for all subsequent queries and actions
4. Never trust caller-provided data for tenant resolution
*/
