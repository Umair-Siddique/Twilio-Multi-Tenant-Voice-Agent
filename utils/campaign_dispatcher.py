"""
Background dispatcher for broadcast campaigns.

Every CAMPAIGN_DISPATCH_INTERVAL_SECONDS it looks at running campaigns and,
for each one it can lease:
  1. reconciles calls that stopped reporting status (asks Twilio directly),
  2. places new calls inside the campaign's calling window, up to
     max_concurrent_calls and paced at CAMPAIGN_CALLS_PER_SECOND,
  3. sends pending emails, paced at CAMPAIGN_EMAILS_PER_MINUTE,
  4. marks the campaign completed when nothing is left to do.

All state lives in the database, so the dispatcher can restart at any time
and pick up where it left off. A campaign is leased to one dispatcher at a
time (campaigns.lease_owner / lease_expires_at), so running several gunicorn
workers or a separate `campaign_worker.py` process never double-dials.

Call progress arrives through Twilio status callbacks handled in
blueprints/campaigns.py, which share `apply_call_status` with the
reconciliation step here.
"""

import os
import socket
import threading
import time
import traceback
import uuid
from datetime import datetime, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from supabase import create_client
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client as TwilioClient

from config import Config
from utils import campaign_email
from utils.supabase_retry import supabase_call_with_retry

ACTIVE_CALL_STATUSES = ("queued", "initiated", "ringing", "in-progress")
FINAL_CALL_STATUSES = ("completed", "busy", "no-answer", "failed", "canceled", "skipped")
RETRYABLE_CALL_STATUSES = ("busy", "no-answer")
_STATUS_RANK = {"queued": 1, "initiated": 2, "ringing": 3, "in-progress": 4}

# Twilio errors that mean the account/caller-ID setup is wrong, not this one
# number. The campaign is paused instead of failing every remaining contact.
ACCOUNT_LEVEL_TWILIO_CODES = {
    20003,  # authentication / permission denied
    20005,  # account not active
    21210,  # 'From' number not verified
    21212,  # invalid 'From' number
    21215,  # geo permissions don't allow calling this country
    21219,  # trial account: 'To' number not verified
}

MAX_CALL_DURATION_SECONDS = 600
LEASE_SECONDS = 120
EMAIL_BACKOFF_SECONDS = 30 * 60
# Housekeeping that doesn't need to run on every tick.
RECONCILE_EVERY_SECONDS = 60       # look for calls that stopped reporting status
COMPLETION_CHECK_EVERY_SECONDS = 15
STATUS_RECHECK_SECONDS = 3         # how quickly a Pause / Cancel stops an in-flight batch


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


# ── Calling window ────────────────────────────────────────────────────────────

def campaign_zone(campaign):
    try:
        return ZoneInfo(campaign.get("timezone") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _parse_time(value, default):
    try:
        parts = [int(p) for p in str(value).split(":")[:2]]
        return dt_time(parts[0], parts[1] if len(parts) > 1 else 0)
    except (TypeError, ValueError, IndexError):
        return default


def in_call_window(campaign, now=None):
    """True when `now` falls on an allowed weekday and time in the campaign's timezone."""
    local = (now or utcnow()).astimezone(campaign_zone(campaign))
    days = campaign.get("call_days") or [1, 2, 3, 4, 5]
    if local.isoweekday() not in days:
        return False
    start = _parse_time(campaign.get("call_window_start"), dt_time(9, 0))
    end = _parse_time(campaign.get("call_window_end"), dt_time(18, 0))
    current = local.time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end  # window crosses midnight


# ── Twilio call parameters ────────────────────────────────────────────────────

def resolve_webhook_base_url(campaign=None):
    """Public base URL for Twilio callbacks when no request context is available."""
    for candidate in (
        (campaign or {}).get("webhook_base_url"),
        Config.CAMPAIGN_WEBHOOK_BASE_URL,
    ):
        if candidate and candidate.strip():
            return candidate.strip().rstrip("/")
    configured = (Config.VOICE_WEBHOOK_URL or "").strip()
    if configured.startswith(("https://", "http://")):
        return configured.split("/voice-agent/", 1)[0].rstrip("/")
    return None


def call_create_params(campaign, *, to_number, from_number, base_url, contact_id=None,
                       attempt=None, test=False):
    """Kwargs for twilio_client.calls.create() for a campaign call (or a test call)."""
    query = f"campaign_id={campaign['id']}"
    if contact_id:
        query += f"&contact_id={contact_id}&attempt={attempt}"
    if test:
        query += "&test=1"

    params = {
        "to": to_number,
        "from_": from_number,
        "url": f"{base_url}/campaigns/webhooks/voice?{query}",
        "method": "POST",
        "timeout": int(campaign.get("ring_timeout_seconds") or 30),
        "time_limit": MAX_CALL_DURATION_SECONDS,
    }
    if contact_id:
        params.update({
            "status_callback": f"{base_url}/campaigns/webhooks/call-status?{query}",
            "status_callback_method": "POST",
            "status_callback_event": ["initiated", "ringing", "answered", "completed"],
        })

    mode = campaign.get("voicemail_mode") or "leave_message"
    if mode == "leave_message":
        # Twilio waits for the voicemail greeting/beep before fetching TwiML, so
        # the recording lands in the voicemail; humans hear it after ~2-4 s.
        params["machine_detection"] = "DetectMessageEnd"
    elif mode == "hangup":
        params["machine_detection"] = "Enable"
    return params


# ── Shared status transitions (used by the webhook and reconciliation) ───────

def apply_call_status(supabase, campaign, contact, twilio_status, *, call_sid=None,
                      duration=None, answered_by=None, error=None):
    """
    Move a contact's call_status forward based on a Twilio call status.

    Out-of-order and duplicate updates are ignored; a busy/no-answer result
    is turned back into "pending" with a retry time while attempts remain.
    Returns the new call_status, or None when nothing changed.
    """
    status = (twilio_status or "").lower()
    if status == "queued":
        status = "initiated"
    current = contact.get("call_status")
    if current not in ACTIVE_CALL_STATUSES:
        return None  # already final, or already rescheduled for a retry

    def _update(data):
        res = supabase_call_with_retry(
            lambda: supabase.table("campaign_contacts")
            .update(data)
            .eq("id", contact["id"])
            .eq("call_status", current)
            .execute()
        )
        return data["call_status"] if res.data else None

    if status in _STATUS_RANK:
        if _STATUS_RANK[status] <= _STATUS_RANK.get(current, 0):
            return None
        data = {"call_status": status}
        if call_sid and not contact.get("call_sid"):
            data["call_sid"] = call_sid
        return _update(data)

    if status not in FINAL_CALL_STATUSES:
        return None

    data = {"call_error": error}
    if call_sid and not contact.get("call_sid"):
        data["call_sid"] = call_sid
    if duration not in (None, ""):
        try:
            data["call_duration_seconds"] = int(duration)
        except (TypeError, ValueError):
            pass
    if answered_by:
        data["answered_by"] = answered_by

    attempts = int(contact.get("call_attempts") or 0)
    may_retry = (
        status in RETRYABLE_CALL_STATUSES
        and attempts < int(campaign.get("max_call_attempts") or 1)
        and campaign.get("status") not in ("cancelled", "completed")
        and not contact.get("opted_out")
    )
    if may_retry:
        delay = timedelta(minutes=int(campaign.get("retry_delay_minutes") or 60))
        data.update({
            "call_status": "pending",
            "call_next_attempt_at": iso(utcnow() + delay),
            "call_error": f"attempt {attempts}: {status}",
        })
    else:
        data["call_status"] = status
        if status != "completed" and not error:
            data["call_error"] = status
    return _update(data)


def count_contacts(supabase, campaign_id, column, statuses):
    """Number of a campaign's contacts whose `column` is one of `statuses`."""
    res = supabase_call_with_retry(
        lambda: supabase.table("campaign_contacts")
        .select("id", count="exact")
        .eq("campaign_id", campaign_id)
        .in_(column, list(statuses))
        .limit(1)
        .execute()
    )
    return res.count or 0


def add_suppression(supabase, channel, value, reason, campaign_id=None):
    """Add a phone / email to the platform-wide do-not-contact list (no-op if present)."""
    if not value:
        return
    supabase_call_with_retry(
        lambda: supabase.table("campaign_suppressions")
        .upsert(
            {"channel": channel, "value": value, "reason": reason, "campaign_id": campaign_id},
            on_conflict="channel,value",
            ignore_duplicates=True,
        )
        .execute()
    )


def suppressed_values(supabase, channel, values):
    values = [v for v in values if v]
    if not values:
        return set()
    res = supabase_call_with_retry(
        lambda: supabase.table("campaign_suppressions")
        .select("value")
        .eq("channel", channel)
        .in_("value", values)
        .execute()
    )
    return {r["value"] for r in res.data or []}


def unsubscribe_url(base_url, secret_key, contact):
    token = campaign_email.make_unsubscribe_token(secret_key, contact.get("id"), contact["email"])
    return f"{base_url}/campaigns/unsubscribe?token={token}"


# ── Dispatcher ────────────────────────────────────────────────────────────────

class _TokenBucket:
    def __init__(self, rate_per_second, capacity):
        self.rate = max(rate_per_second, 0.0)
        self.capacity = max(capacity, 1.0)
        self.tokens = self.capacity
        self.updated = time.monotonic()

    def take(self, wanted):
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        granted = min(int(self.tokens), wanted)
        self.tokens -= granted
        return granted


def _service_client():
    """Service-role Supabase client with the same timeouts / keep-alive tuning as the app's."""
    try:
        import httpx
        from supabase.lib.client_options import ClientOptions

        http = httpx.Client(
            transport=httpx.HTTPTransport(
                retries=Config.SUPABASE_HTTP_RETRIES,
                limits=httpx.Limits(max_connections=5, max_keepalive_connections=2, keepalive_expiry=60),
            ),
            timeout=httpx.Timeout(
                connect=Config.SUPABASE_HTTP_CONNECT_TIMEOUT,
                read=Config.SUPABASE_HTTP_READ_TIMEOUT,
                write=Config.SUPABASE_HTTP_WRITE_TIMEOUT,
                pool=Config.SUPABASE_HTTP_POOL_TIMEOUT,
            ),
        )
        options = ClientOptions(headers={"Authorization": f"Bearer {Config.SUPABASE_SECRET_KEY}"}, httpx_client=http)
        client = create_client(Config.SUPABASE_URL, Config.SUPABASE_SECRET_KEY, options=options)
    except Exception:
        client = create_client(Config.SUPABASE_URL, Config.SUPABASE_SECRET_KEY)
    client.postgrest.auth(Config.SUPABASE_SECRET_KEY)
    return client


class CampaignDispatcher:
    def __init__(self, app):
        self.app = app
        self.worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.interval = max(1.0, Config.CAMPAIGN_DISPATCH_INTERVAL_SECONDS)
        self.call_spacing = 1.0 / max(Config.CAMPAIGN_CALLS_PER_SECOND, 0.01)
        emails_per_second = Config.CAMPAIGN_EMAILS_PER_MINUTE / 60.0
        self.email_bucket = _TokenBucket(emails_per_second, emails_per_second * self.interval * 2)
        self.email_backoff_until = {}  # campaign_id -> monotonic time
        self._last_run = {}            # (task, campaign_id) -> monotonic time
        self.stop_event = threading.Event()
        self.thread = None
        # Own clients: the shared app Supabase client can have a user JWT swapped
        # in by a sign-in on another thread (which would put RLS in front of our
        # queries), and the Twilio client's HTTP session isn't meant to be shared
        # across threads.
        self.supabase = _service_client()
        self._twilio = (
            TwilioClient(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
            if Config.TWILIO_ACCOUNT_SID and Config.TWILIO_AUTH_TOKEN else None
        )

    @property
    def twilio(self):
        return self._twilio or getattr(self.app, "twilio_client", None)

    def _due(self, task, campaign_id, every_seconds):
        """True (and re-arms) when `task` hasn't run for this campaign in `every_seconds`."""
        key, now = (task, campaign_id), time.monotonic()
        if now - self._last_run.get(key, float("-inf")) < every_seconds:
            return False
        self._last_run[key] = now
        return True

    # Lifecycle ---------------------------------------------------------------

    def start(self):
        self.thread = threading.Thread(target=self._run, name="campaign-dispatcher", daemon=True)
        self.thread.start()
        print(f"✅ Campaign dispatcher started ({self.worker_id})")

    def stop(self):
        self.stop_event.set()

    def _run(self):
        while not self.stop_event.wait(self.interval):
            try:
                with self.app.app_context():
                    self.tick()
            except Exception:
                print("[CampaignDispatcher] tick failed:")
                traceback.print_exc()

    # One pass over running campaigns -----------------------------------------

    def tick(self):
        now = utcnow()
        res = supabase_call_with_retry(
            lambda: self.supabase.table("campaigns").select("*").eq("status", "running").execute()
        )
        for campaign in res.data or []:
            if self.stop_event.is_set():
                return
            start_at = parse_ts(campaign.get("start_at"))
            if start_at and start_at > now:
                continue
            if not self._acquire_lease(campaign["id"]):
                continue
            try:
                self._process(campaign)
            except Exception as exc:
                print(f"[CampaignDispatcher] campaign {campaign['id']} failed:")
                traceback.print_exc()
                self._set_last_error(campaign["id"], f"Dispatcher error: {exc}")
        if self._due("reconcile", "stopped-campaigns", RECONCILE_EVERY_SECONDS):
            self._reconcile_stopped_campaigns()

    def _reconcile_stopped_campaigns(self):
        """
        Paused / cancelled campaigns aren't dispatched, but calls that were in
        flight when they stopped still need a final result if their status
        callbacks never arrived. Only campaigns stopped within the last day.
        """
        res = supabase_call_with_retry(
            lambda: self.supabase.table("campaigns")
            .select("id, status, max_call_attempts, retry_delay_minutes")
            .in_("status", ["paused", "cancelled"])
            .gte("updated_at", iso(utcnow() - timedelta(days=1)))
            .execute()
        )
        for campaign in res.data or []:
            try:
                self._reconcile_stale_calls(campaign)
            except Exception:
                print(f"[CampaignDispatcher] reconcile of stopped campaign {campaign['id']} failed:")
                traceback.print_exc()

    def _acquire_lease(self, campaign_id):
        now = utcnow()
        res = supabase_call_with_retry(
            lambda: self.supabase.table("campaigns")
            .update({"lease_owner": self.worker_id, "lease_expires_at": iso(now + timedelta(seconds=LEASE_SECONDS))})
            .eq("id", campaign_id)
            .eq("status", "running")
            .or_(
                f'lease_owner.is.null,lease_expires_at.lt."{iso(now)}",lease_owner.eq."{self.worker_id}"'
            )
            .execute()
        )
        return bool(res.data)

    def _still_running(self, campaign_id):
        res = supabase_call_with_retry(
            lambda: self.supabase.table("campaigns").select("status").eq("id", campaign_id).limit(1).execute()
        )
        return bool(res.data) and res.data[0]["status"] == "running"

    def _set_last_error(self, campaign_id, message):
        supabase_call_with_retry(
            lambda: self.supabase.table("campaigns").update({"last_error": message[:2000]}).eq("id", campaign_id).execute()
        )

    def _pause(self, campaign_id, reason):
        print(f"[CampaignDispatcher] pausing campaign {campaign_id}: {reason}")
        supabase_call_with_retry(
            lambda: self.supabase.table("campaigns")
            .update({"status": "paused", "last_error": reason[:2000], "lease_owner": None, "lease_expires_at": None})
            .eq("id", campaign_id)
            .eq("status", "running")
            .execute()
        )

    def _process(self, campaign):
        cid = campaign["id"]
        busy = False  # anything placed / still on a call → can't be finished yet
        if campaign.get("call_enabled"):
            if self._due("reconcile", cid, RECONCILE_EVERY_SECONDS):
                self._reconcile_stale_calls(campaign)
            if in_call_window(campaign):
                busy |= self._dispatch_calls(campaign)
        if campaign.get("email_enabled"):
            busy |= self._dispatch_emails(campaign)
        if not busy and self._due("completion", cid, COMPLETION_CHECK_EVERY_SECONDS):
            self._complete_if_done(campaign)

    # Calls -------------------------------------------------------------------

    def _dispatch_calls(self, campaign):
        """Place calls into free slots. Returns True while the campaign has calls placed or in flight."""
        twilio = self.twilio
        if twilio is None:
            self._set_last_error(campaign["id"], "Twilio is not configured on the server")
            return False

        cid = campaign["id"]
        limit = min(int(campaign.get("max_concurrent_calls") or 1), Config.CAMPAIGN_MAX_CONCURRENT_CALLS_LIMIT)
        active = count_contacts(self.supabase, cid, "call_status", ACTIVE_CALL_STATUSES)
        slots = limit - active
        if slots <= 0:
            return True

        now = utcnow()
        res = supabase_call_with_retry(
            lambda: self.supabase.table("campaign_contacts")
            .select("*")
            .eq("campaign_id", cid)
            .eq("call_status", "pending")
            .or_(f'call_next_attempt_at.is.null,call_next_attempt_at.lte."{iso(now)}"')
            .order("created_at").order("row_number")
            .limit(slots)
            .execute()
        )
        batch = res.data or []
        if not batch:
            return active > 0

        blocked = suppressed_values(self.supabase, "phone", [c["phone_number"] for c in batch])
        base_url = resolve_webhook_base_url(campaign)
        placed = active > 0
        status_checked = time.monotonic()
        next_dial = 0.0  # pacing: the DB work for the next contact overlaps the wait

        for contact in batch:
            if self.stop_event.is_set():
                break
            if time.monotonic() - status_checked >= STATUS_RECHECK_SECONDS:
                if not self._still_running(cid):
                    break
                status_checked = time.monotonic()
            if contact["phone_number"] in blocked:
                self._update_contact(contact["id"], "pending", {
                    "call_status": "skipped", "call_error": "suppressed (do-not-call list)",
                })
                continue

            attempt = int(contact.get("call_attempts") or 0) + 1
            claimed = self._update_contact(contact["id"], "pending", {
                "call_status": "queued",
                "call_attempts": attempt,
                "call_last_attempt_at": iso(utcnow()),
                "call_next_attempt_at": None,
                "call_sid": None,
                "answered_by": None,
                "call_duration_seconds": None,
            })
            if not claimed:
                continue

            params = call_create_params(
                campaign,
                to_number=contact["phone_number"],
                from_number=campaign["from_number"],
                base_url=base_url,
                contact_id=contact["id"],
                attempt=attempt,
            )
            wait = next_dial - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            next_dial = time.monotonic() + self.call_spacing
            try:
                call = twilio.calls.create(**params)
            except TwilioRestException as exc:
                if exc.code in ACCOUNT_LEVEL_TWILIO_CODES:
                    self._release_claim(contact, attempt)
                    self._pause(cid, f"Twilio error {exc.code}: {exc.msg}")
                    return placed
                if exc.status == 429 or (exc.status or 0) >= 500 or exc.code == 20429:
                    self._release_claim(contact, attempt, retry_in_seconds=60)
                    self._set_last_error(cid, f"Twilio temporarily unavailable ({exc.status}): {exc.msg}")
                    return placed
                self._update_contact(contact["id"], "queued", {
                    "call_status": "failed", "call_error": f"Twilio {exc.code}: {exc.msg}"[:1000],
                })
                continue
            except Exception as exc:
                self._release_claim(contact, attempt, retry_in_seconds=60)
                self._set_last_error(cid, f"Could not reach Twilio: {exc}")
                return placed

            placed = True
            # A status callback may already have moved the row past "queued".
            if not self._update_contact(contact["id"], "queued", {"call_status": "initiated", "call_sid": call.sid}):
                self._update_contact(contact["id"], None, {"call_sid": call.sid})

        return placed

    def _update_contact(self, contact_id, expected_status, data, column="call_status"):
        query = self.supabase.table("campaign_contacts").update(data).eq("id", contact_id)
        if expected_status is not None:
            query = query.eq(column, expected_status)
        res = supabase_call_with_retry(lambda: query.execute())
        return bool(res.data)

    def _release_claim(self, contact, attempt, retry_in_seconds=None):
        """Undo a claim when the call was never placed (doesn't count as an attempt)."""
        self._update_contact(contact["id"], "queued", {
            "call_status": "pending",
            "call_attempts": attempt - 1,
            "call_next_attempt_at": iso(utcnow() + timedelta(seconds=retry_in_seconds)) if retry_in_seconds else None,
        })

    def _reconcile_stale_calls(self, campaign):
        """Ask Twilio about calls that stopped sending status callbacks."""
        cutoff = utcnow() - timedelta(minutes=Config.CAMPAIGN_STALE_CALL_MINUTES)
        res = supabase_call_with_retry(
            lambda: self.supabase.table("campaign_contacts")
            .select("*")
            .eq("campaign_id", campaign["id"])
            .in_("call_status", list(ACTIVE_CALL_STATUSES))
            .lt("call_last_attempt_at", iso(cutoff))
            .limit(50)
            .execute()
        )
        twilio = self.twilio
        for contact in res.data or []:
            if not contact.get("call_sid"):
                # Claimed but the dispatcher died before Twilio accepted the call.
                exhausted = int(contact.get("call_attempts") or 0) >= int(campaign.get("max_call_attempts") or 1)
                self._update_contact(contact["id"], contact["call_status"], {
                    "call_status": "failed" if exhausted else "pending",
                    "call_error": "dispatch interrupted",
                })
                continue
            if twilio is None:
                continue
            try:
                call = twilio.calls(contact["call_sid"]).fetch()
            except TwilioRestException as exc:
                if exc.status == 404:
                    self._update_contact(contact["id"], contact["call_status"], {
                        "call_status": "failed", "call_error": "call not found at Twilio",
                    })
                continue
            apply_call_status(
                self.supabase, campaign, contact, call.status,
                call_sid=call.sid, duration=call.duration, answered_by=call.answered_by,
            )

    # Emails ------------------------------------------------------------------

    def _dispatch_emails(self, campaign):
        cid = campaign["id"]
        if self.email_backoff_until.get(cid, 0) > time.monotonic():
            return False
        if not campaign_email.smtp_configured():
            self._set_last_error(cid, "Campaign email is enabled but SMTP is not configured on the server")
            self.email_backoff_until[cid] = time.monotonic() + EMAIL_BACKOFF_SECONDS
            return False

        self._recover_stuck_emails(cid)
        allowance = self.email_bucket.take(50)
        if allowance <= 0:
            return False

        def _pending_emails():
            query = (self.supabase.table("campaign_contacts").select("*")
                     .eq("campaign_id", cid).eq("email_status", "pending"))
            if campaign.get("call_enabled"):
                # The email is the follow-up to the call: wait until this lead's call
                # (including retries) is finished. Leads that won't be called are
                # "skipped" and are emailed right away.
                query = query.in_("call_status", list(FINAL_CALL_STATUSES))
            return query.order("created_at").order("row_number").limit(allowance).execute()

        res = supabase_call_with_retry(_pending_emails)
        batch = res.data or []
        if not batch:
            return False

        blocked = suppressed_values(self.supabase, "email", [c["email"] for c in batch])
        to_send = []
        for contact in batch:
            if contact["email"] in blocked:
                self._update_contact(contact["id"], "pending", {
                    "email_status": "skipped", "email_error": "unsubscribed",
                }, column="email_status")
            elif self._update_contact(contact["id"], "pending", {"email_status": "sending"}, column="email_status"):
                to_send.append(contact)
        if not to_send:
            return True

        base_url = resolve_webhook_base_url(campaign)
        secret = self.app.secret_key
        sent_ids = set()
        try:
            with campaign_email.CampaignMailer() as mailer:
                for contact in to_send:
                    msg = campaign_email.build_message(
                        campaign,
                        contact,
                        from_email=mailer.from_email,
                        unsubscribe_url=unsubscribe_url(base_url, secret, contact),
                        sender_name=Config.CAMPAIGN_SENDER_NAME,
                        sender_address=Config.CAMPAIGN_SENDER_ADDRESS,
                    )
                    try:
                        mailer.send(msg)
                        self._update_contact(contact["id"], "sending", {
                            "email_status": "sent", "email_sent_at": iso(utcnow()), "email_error": None,
                        }, column="email_status")
                    except campaign_email.SmtpRecipientError as exc:
                        self._update_contact(contact["id"], "sending", {
                            "email_status": "failed", "email_error": str(exc)[:1000],
                        }, column="email_status")
                    sent_ids.add(contact["id"])
        except campaign_email.SmtpAccountError as exc:
            # Put the unsent ones back and retry later; calls keep going meanwhile.
            for contact in to_send:
                if contact["id"] not in sent_ids:
                    self._update_contact(contact["id"], "sending", {"email_status": "pending"}, column="email_status")
            self.email_backoff_until[cid] = time.monotonic() + EMAIL_BACKOFF_SECONDS
            self._set_last_error(cid, f"Email sending paused for 30 min: {exc}")
        return True

    def _recover_stuck_emails(self, campaign_id):
        cutoff = utcnow() - timedelta(minutes=Config.CAMPAIGN_STALE_CALL_MINUTES)
        supabase_call_with_retry(
            lambda: self.supabase.table("campaign_contacts")
            .update({"email_status": "pending"})
            .eq("campaign_id", campaign_id)
            .eq("email_status", "sending")
            .lt("updated_at", iso(cutoff))
            .execute()
        )

    # Completion --------------------------------------------------------------

    def _complete_if_done(self, campaign):
        cid = campaign["id"]
        if campaign.get("call_enabled") and count_contacts(self.supabase, cid, "call_status", ("pending",) + ACTIVE_CALL_STATUSES):
            return
        if campaign.get("email_enabled") and count_contacts(self.supabase, cid, "email_status", ("pending", "sending")):
            return
        supabase_call_with_retry(
            lambda: self.supabase.table("campaigns")
            .update({"status": "completed", "completed_at": iso(utcnow()), "lease_owner": None, "lease_expires_at": None})
            .eq("id", cid)
            .eq("status", "running")
            .execute()
        )
        print(f"[CampaignDispatcher] campaign {cid} completed")


# ── Process-wide singleton ────────────────────────────────────────────────────

_dispatcher = None
_dispatcher_pid = None
_dispatcher_lock = threading.Lock()
_last_start_failure = 0.0


def _dispatcher_alive():
    return _dispatcher is not None and _dispatcher_pid == os.getpid() and _dispatcher.thread.is_alive()


def ensure_dispatcher_running(app):
    """Start the dispatcher once per process (also after a gunicorn --preload fork)."""
    global _dispatcher, _dispatcher_pid, _last_start_failure
    if not Config.CAMPAIGN_DISPATCHER_ENABLED or not Config.SUPABASE_URL or not Config.SUPABASE_SECRET_KEY:
        return None
    if _dispatcher_alive():
        return _dispatcher
    if time.monotonic() - _last_start_failure < 60:
        return None
    with _dispatcher_lock:
        if not _dispatcher_alive():
            try:
                dispatcher = CampaignDispatcher(app)
                dispatcher.start()
                _dispatcher, _dispatcher_pid = dispatcher, os.getpid()
            except Exception as exc:
                print(f"⚠️  Campaign dispatcher failed to start: {exc}")
                _dispatcher, _last_start_failure = None, time.monotonic()
    return _dispatcher
