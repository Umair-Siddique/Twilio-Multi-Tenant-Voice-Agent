-- =====================================================================
-- Migration: 2026-11-03 (revised 2026-11-07)
-- Broadcast call campaigns — the platform's own marketing outreach, run by
-- super admins: call a list of businesses (imported from an Excel / CSV
-- sheet), play a pre-recorded message, and optionally email them.
--
-- Platform-level: no tenant_id anywhere. The tables are only reachable
-- through the backend (service role) — RLS is on with no policies, so
-- tenant users' tokens can't read them via PostgREST.
--
-- Purely additive to the existing schema; the inbound voice agent is
-- unaffected. Idempotent and safe to re-run. If an earlier, tenant-scoped
-- draft of this migration was already run, section 4 converts it in place.
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1. CAMPAIGNS
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS campaigns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'running', 'paused', 'completed', 'cancelled')),

    -- Call channel: dial each contact and play the uploaded recording.
    call_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    from_number VARCHAR(20), -- E.164; a voice number on the Twilio account
    audio_storage_path TEXT, -- object path in the campaign-audio bucket
    audio_file_name TEXT,
    audio_content_type VARCHAR(100),
    audio_size_bytes BIGINT,
    -- leave_message: wait for the voicemail beep, then play the recording
    -- hangup:        hang up when a machine answers
    -- no_detection:  play immediately, no answering-machine detection
    voicemail_mode VARCHAR(20) NOT NULL DEFAULT 'leave_message'
        CHECK (voicemail_mode IN ('leave_message', 'hangup', 'no_detection')),
    opt_out_enabled BOOLEAN NOT NULL DEFAULT TRUE, -- "press 9 to stop receiving calls"
    opt_out_digit VARCHAR(1) NOT NULL DEFAULT '9' CHECK (opt_out_digit ~ '^[0-9]$'),
    max_call_attempts INTEGER NOT NULL DEFAULT 2 CHECK (max_call_attempts BETWEEN 1 AND 5),
    retry_delay_minutes INTEGER NOT NULL DEFAULT 60 CHECK (retry_delay_minutes BETWEEN 1 AND 10080),
    max_concurrent_calls INTEGER NOT NULL DEFAULT 5 CHECK (max_concurrent_calls >= 1),
    ring_timeout_seconds INTEGER NOT NULL DEFAULT 30 CHECK (ring_timeout_seconds BETWEEN 5 AND 120),
    -- Calls are only placed inside this local-time window on these ISO weekdays
    -- (1 = Monday … 7 = Sunday), in `timezone`.
    call_window_start TIME NOT NULL DEFAULT '09:00',
    call_window_end TIME NOT NULL DEFAULT '18:00',
    call_days SMALLINT[] NOT NULL DEFAULT '{1,2,3,4,5}',
    timezone VARCHAR(100),

    -- Email channel. Body supports {{business_name}}, {{contact_name}},
    -- {{email}}, {{phone}}, {{unsubscribe_url}} and any extra sheet column.
    email_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    email_subject VARCHAR(500),
    email_body_html TEXT,
    email_body_text TEXT,
    email_from_name VARCHAR(255),
    email_reply_to VARCHAR(320),

    -- Runtime
    start_at TIMESTAMPTZ,         -- optional: don't dispatch before this time
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    webhook_base_url TEXT,        -- public URL Twilio calls back on, captured at start
    lease_owner TEXT,             -- dispatcher currently driving this campaign
    lease_expires_at TIMESTAMPTZ,
    last_error TEXT,
    total_contacts INTEGER NOT NULL DEFAULT 0,

    created_by UUID REFERENCES auth.users(id) ON DELETE SET NULL, -- the super admin
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);


-- ---------------------------------------------------------------------
-- 2. CAMPAIGN CONTACTS (one row per business from the imported sheet)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS campaign_contacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    row_number INTEGER,           -- row in the source sheet
    business_name TEXT,
    contact_name TEXT,
    phone_number VARCHAR(20),     -- normalized E.164
    phone_raw TEXT,               -- as written in the sheet
    email VARCHAR(320),
    extra JSONB NOT NULL DEFAULT '{}', -- any other sheet columns

    call_status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (call_status IN ('pending', 'queued', 'initiated', 'ringing', 'in-progress',
                               'completed', 'busy', 'no-answer', 'failed', 'canceled', 'skipped')),
    call_attempts INTEGER NOT NULL DEFAULT 0,
    call_next_attempt_at TIMESTAMPTZ,
    call_last_attempt_at TIMESTAMPTZ,
    call_sid VARCHAR(100),
    answered_by VARCHAR(30),      -- human | machine_end_beep | machine_start | fax | unknown …
    call_duration_seconds INTEGER,
    call_error TEXT,
    opted_out BOOLEAN NOT NULL DEFAULT FALSE,

    email_status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (email_status IN ('pending', 'sending', 'sent', 'failed', 'skipped')),
    email_sent_at TIMESTAMPTZ,
    email_error TEXT,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);


-- ---------------------------------------------------------------------
-- 3. DO-NOT-CONTACT LIST (platform-wide)
-- Filled automatically by "press 9" opt-outs and email unsubscribes, and
-- manually from the dashboard. Checked before every call and email.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS campaign_suppressions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel VARCHAR(10) NOT NULL CHECK (channel IN ('phone', 'email')),
    value VARCHAR(320) NOT NULL,  -- E.164 phone or lowercase email
    reason VARCHAR(50) NOT NULL DEFAULT 'manual', -- call_opt_out | email_unsubscribe | manual
    campaign_id UUID REFERENCES campaigns(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);


-- ---------------------------------------------------------------------
-- 4. UPGRADE an earlier tenant-scoped draft of these tables (no-op otherwise)
-- ---------------------------------------------------------------------
DROP POLICY IF EXISTS campaigns_policy ON campaigns;
DROP POLICY IF EXISTS campaign_contacts_policy ON campaign_contacts;
DROP POLICY IF EXISTS campaign_suppressions_policy ON campaign_suppressions;
-- Dropping the column also drops its foreign key, indexes and unique constraint.
ALTER TABLE campaigns DROP COLUMN IF EXISTS tenant_id;
ALTER TABLE campaign_contacts DROP COLUMN IF EXISTS tenant_id;
ALTER TABLE campaign_suppressions DROP COLUMN IF EXISTS tenant_id;
DROP FUNCTION IF EXISTS get_campaign_call_overview(UUID, UUID, INT);


-- ---------------------------------------------------------------------
-- 5. INDEXES
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_campaigns_created ON campaigns(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);

CREATE INDEX IF NOT EXISTS idx_campaign_contacts_campaign ON campaign_contacts(campaign_id, row_number);
CREATE INDEX IF NOT EXISTS idx_campaign_contacts_call_queue
    ON campaign_contacts(campaign_id, call_status, call_next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_campaign_contacts_email_queue
    ON campaign_contacts(campaign_id, email_status);
CREATE INDEX IF NOT EXISTS idx_campaign_contacts_call_sid ON campaign_contacts(call_sid);
CREATE INDEX IF NOT EXISTS idx_campaign_contacts_last_attempt
    ON campaign_contacts(call_last_attempt_at DESC) WHERE call_attempts > 0;
-- A phone number appears at most once per campaign.
CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_contacts_phone
    ON campaign_contacts(campaign_id, phone_number) WHERE phone_number IS NOT NULL;

-- One entry per phone / email (also the upsert conflict target).
CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_suppressions_value
    ON campaign_suppressions(channel, value);


-- ---------------------------------------------------------------------
-- 6. ROW LEVEL SECURITY — on, with no policies: backend (service role) only
-- ---------------------------------------------------------------------
ALTER TABLE campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaign_contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaign_suppressions ENABLE ROW LEVEL SECURITY;


-- ---------------------------------------------------------------------
-- 7. updated_at TRIGGERS
-- ---------------------------------------------------------------------
DROP TRIGGER IF EXISTS update_campaigns_updated_at ON campaigns;
CREATE TRIGGER update_campaigns_updated_at BEFORE UPDATE ON campaigns
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_campaign_contacts_updated_at ON campaign_contacts;
CREATE TRIGGER update_campaign_contacts_updated_at BEFORE UPDATE ON campaign_contacts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();


-- ---------------------------------------------------------------------
-- 8. STATS FUNCTIONS (called by the backend via supabase.rpc)
-- SECURITY INVOKER (the default): with RLS on and no policies, only the
-- service role gets rows back.
-- ---------------------------------------------------------------------

-- Per-status counts for one or more campaigns in a single round-trip.
CREATE OR REPLACE FUNCTION get_campaign_stats(p_campaign_ids UUID[])
RETURNS TABLE(campaign_id UUID, metric TEXT, value TEXT, total BIGINT)
LANGUAGE SQL STABLE AS $$
    SELECT cc.campaign_id, 'call_status', cc.call_status, COUNT(*)
    FROM campaign_contacts cc
    WHERE cc.campaign_id = ANY(p_campaign_ids)
    GROUP BY cc.campaign_id, cc.call_status
    UNION ALL
    SELECT cc.campaign_id, 'email_status', cc.email_status, COUNT(*)
    FROM campaign_contacts cc
    WHERE cc.campaign_id = ANY(p_campaign_ids)
    GROUP BY cc.campaign_id, cc.email_status
    UNION ALL
    SELECT cc.campaign_id, 'answered_by', cc.answered_by, COUNT(*)
    FROM campaign_contacts cc
    WHERE cc.campaign_id = ANY(p_campaign_ids) AND cc.answered_by IS NOT NULL
    GROUP BY cc.campaign_id, cc.answered_by
    UNION ALL
    SELECT cc.campaign_id, 'opted_out', 'true', COUNT(*)
    FROM campaign_contacts cc
    WHERE cc.campaign_id = ANY(p_campaign_ids) AND cc.opted_out
    GROUP BY cc.campaign_id;
$$;

-- Dashboard totals across all campaigns (or one, when p_campaign_id is set).
-- calls_by_day covers the last p_days days, by last call attempt (UTC).
CREATE OR REPLACE FUNCTION get_campaign_call_overview(
    p_campaign_id UUID DEFAULT NULL,
    p_days INT DEFAULT 30
)
RETURNS TABLE(metric TEXT, value TEXT, total BIGINT)
LANGUAGE SQL STABLE AS $$
    WITH c AS (
        SELECT call_status, email_status, answered_by, opted_out,
               call_attempts, call_duration_seconds, call_last_attempt_at
        FROM campaign_contacts
        WHERE p_campaign_id IS NULL OR campaign_id = p_campaign_id
    )
    SELECT 'call_status', call_status, COUNT(*) FROM c GROUP BY call_status
    UNION ALL
    SELECT 'email_status', email_status, COUNT(*) FROM c GROUP BY email_status
    UNION ALL
    SELECT 'answered_by', answered_by, COUNT(*) FROM c WHERE answered_by IS NOT NULL GROUP BY answered_by
    UNION ALL
    SELECT 'totals', 'contacts_called', COUNT(*) FROM c WHERE call_attempts > 0
    UNION ALL
    SELECT 'totals', 'attempts', COALESCE(SUM(call_attempts), 0) FROM c
    UNION ALL
    SELECT 'totals', 'talk_seconds', COALESCE(SUM(call_duration_seconds), 0) FROM c
    UNION ALL
    SELECT 'totals', 'opted_out', COUNT(*) FROM c WHERE opted_out
    UNION ALL
    SELECT 'calls_by_day', to_char(call_last_attempt_at AT TIME ZONE 'UTC', 'YYYY-MM-DD'), COUNT(*)
    FROM c
    WHERE call_last_attempt_at >= NOW() - make_interval(days => GREATEST(p_days, 1))
    GROUP BY 2;
$$;


-- ---------------------------------------------------------------------
-- 9. STORAGE BUCKET for the recorded messages (private; Twilio fetches the
-- audio through signed URLs).
-- ---------------------------------------------------------------------
INSERT INTO storage.buckets (id, name, public)
VALUES ('campaign-audio', 'campaign-audio', false)
ON CONFLICT (id) DO NOTHING;
