-- =====================================================================
-- Migration: 2026-07-24
-- Covers the recent changes:
--   * Tenant profile "Country" field (REQUIRED)
--   * One-phone-number-per-tenant rule (OPTIONAL DB-level safety net)
--   * Call History / Analytics / Spam Numbers pages (REQUIRED — Section 3)
--
-- Everything else in these changes (rate limiting, session-expiry /
-- auto-refresh, locking the number search to the tenant country,
-- Twilio-enabled country list) is application-level and needs NO schema
-- change.
--
-- Run in the Supabase SQL editor. Every statement is idempotent and safe to
-- re-run. New installs already get the `country` column from
-- database_schema.sql, so this only matters for existing databases.
-- =====================================================================


-- ---------------------------------------------------------------------
-- SECTION 1 — REQUIRED: add `country` to tenants
-- The backend reads/writes this column; without it, country reads/writes
-- silently no-op.
-- ---------------------------------------------------------------------
ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS country VARCHAR(2) DEFAULT 'CA';

-- Backfill any rows that predate the column.
UPDATE tenants SET country = 'CA' WHERE country IS NULL;


-- ---------------------------------------------------------------------
-- SECTION 2 — OPTIONAL: enforce one phone number per tenant in the DB
--
-- The app already blocks a second purchase (returns 409) and hides the buy
-- UI once a tenant has a number. This adds a database-level guarantee as a
-- belt-and-suspenders safeguard.
--
-- ⚠️  It will FAIL if any tenant currently has more than one number. Run the
--     CHECK query first; only run the CREATE INDEX if it returns zero rows.
-- ---------------------------------------------------------------------

-- 2a. CHECK — must return NO rows before creating the unique index below.
--     (Tenants that already have more than one number, if any.)
SELECT tenant_id, COUNT(*) AS number_count
FROM phone_numbers
GROUP BY tenant_id
HAVING COUNT(*) > 1;

-- 2b. Enforce uniqueness of tenant_id in phone_numbers (one number per tenant).
--     Uncomment and run ONLY if the CHECK above returned zero rows.
-- CREATE UNIQUE INDEX IF NOT EXISTS uq_phone_numbers_one_per_tenant
--     ON phone_numbers (tenant_id);


-- ---------------------------------------------------------------------
-- SECTION 3 — REQUIRED for Call History, Analytics & Spam Numbers pages
-- These back the tenant Call History / Analytics pages and the super-admin
-- Spam Numbers page. Without them those pages return 404 / "Server
-- unreachable".
-- ---------------------------------------------------------------------

-- 3a. Spam-scoring columns on calls (used by Call History + Analytics + spam
--     monitoring). Safe defaults so existing rows read as "not spam".
ALTER TABLE calls
    ADD COLUMN IF NOT EXISTS spam_score NUMERIC DEFAULT 0,
    ADD COLUMN IF NOT EXISTS spam_flags TEXT[] DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS interrupted_count INTEGER DEFAULT 0;

-- 3b. Platform-wide spam blocklist managed by super admins.
CREATE TABLE IF NOT EXISTS spam_numbers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone_number VARCHAR(20) NOT NULL UNIQUE,
    reason TEXT,
    added_by UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
