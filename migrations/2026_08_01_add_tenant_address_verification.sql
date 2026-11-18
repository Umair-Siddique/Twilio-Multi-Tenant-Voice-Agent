-- =====================================================================
-- Migration: 2026-08-01
-- Business address capture + verification, and the North America / Europe
-- region lock.
--
-- Adds the address columns the signup and tenant-profile flows now write.
-- Without them, address reads/writes silently no-op and every account shows
-- as unverified.
--
-- The region lock itself (which countries may open an account) is
-- application-level — see utils/address_verification.py — and needs NO
-- schema change.
--
-- Run in the Supabase SQL editor. Every statement is idempotent and safe to
-- re-run. New installs already get these columns from database_schema.sql.
-- =====================================================================


-- ---------------------------------------------------------------------
-- SECTION 1 — REQUIRED: address columns on tenants
--
-- Values are stored in Google's normalized form once verified, so they are
-- postal-correct rather than whatever the user typed.
-- ---------------------------------------------------------------------
ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS address_line1 TEXT,
    ADD COLUMN IF NOT EXISTS address_line2 TEXT,
    ADD COLUMN IF NOT EXISTS city TEXT,
    ADD COLUMN IF NOT EXISTS state TEXT,
    ADD COLUMN IF NOT EXISTS postal_code TEXT,
    ADD COLUMN IF NOT EXISTS address_formatted TEXT;


-- ---------------------------------------------------------------------
-- SECTION 2 — REQUIRED: verification state
--
-- address_verification_status:
--   verified   – confirmed against Google's postal data
--   unverified – check could not run (API outage / not configured) while
--                ADDRESS_VERIFICATION_STRICT=false; needs manual review
--   skipped    – verification disabled in this environment (dev)
-- ---------------------------------------------------------------------
ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS address_verified BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS address_verification_status VARCHAR(20),
    ADD COLUMN IF NOT EXISTS address_verified_at TIMESTAMPTZ;

-- Backfill pre-existing tenants: they signed up before address capture
-- existed, so they carry no address and read as unverified.
UPDATE tenants
SET address_verified = FALSE
WHERE address_verified IS NULL;

UPDATE tenants
SET address_verification_status = 'unverified'
WHERE address_verification_status IS NULL;


-- ---------------------------------------------------------------------
-- SECTION 3 — OPTIONAL: find accounts needing review
--
-- Existing tenants are NOT retroactively blocked by this migration. Run this
-- to list accounts whose address still needs to be collected/verified.
-- ---------------------------------------------------------------------
-- SELECT id, name, country, address_verification_status, created_at
-- FROM tenants
-- WHERE address_verified IS NOT TRUE
-- ORDER BY created_at DESC;
