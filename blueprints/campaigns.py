"""
Broadcast call campaigns — the platform's own marketing outreach, run by
super admins. Import a sheet of businesses, upload the recorded message,
then call every business (playing the recording) and optionally email
them. Dialing/emailing runs in utils/campaign_dispatcher.py.

Platform-level: campaigns don't belong to any tenant, opt-outs go to one
platform-wide do-not-contact list, and the caller ID is any voice number on
the Twilio account (default CAMPAIGN_DEFAULT_FROM_NUMBER). The module uses
its own tables and Twilio webhooks, so the inbound voice agent
(/voice-agent/*) is untouched.

Super-admin endpoints (Bearer token of a user in super_admins)
--------------------------------------------------------------
GET    /admin/campaigns/overview                     Call / email stats (+ calls per day)
GET    /admin/campaigns/calls                        Every dialed business across campaigns
GET    /admin/campaigns/from-numbers                 Twilio numbers a campaign can call from
GET    /admin/campaigns                              List campaigns with progress stats
POST   /admin/campaigns                              Create a draft campaign
GET    /admin/campaigns/<id>                         Campaign details + stats
PUT    /admin/campaigns/<id>                         Update settings / email template
DELETE /admin/campaigns/<id>                         Delete (not while running)
POST   /admin/campaigns/<id>/audio                   Upload the recorded message (mp3 / wav / aiff)
GET    /admin/campaigns/<id>/audio                   Short-lived URL to preview the recording
POST   /admin/campaigns/<id>/contacts/import         Import an .xlsx / .csv sheet (dry_run=true previews)
GET    /admin/campaigns/<id>/contacts                Paginated contacts with per-contact status
DELETE /admin/campaigns/<id>/contacts                Remove all contacts (draft only)
DELETE /admin/campaigns/<id>/contacts/<contact_id>   Remove one contact (draft / paused)
GET    /admin/campaigns/<id>/export                  Download results as CSV
POST   /admin/campaigns/<id>/test-call               Call one number with the recording
POST   /admin/campaigns/<id>/test-email              Send the email to one address
POST   /admin/campaigns/<id>/start                   Start, or resume a paused campaign
POST   /admin/campaigns/<id>/pause                   Pause (calls already ringing finish)
POST   /admin/campaigns/<id>/cancel                  Cancel for good
GET    /admin/campaigns/contacts-template            Sample CSV with the expected columns
GET    /admin/campaigns/suppressions                 Do-not-contact list
POST   /admin/campaigns/suppressions                 Add phones / emails to it
DELETE /admin/campaigns/suppressions/<id>            Remove an entry

Public endpoints (linked from calls and emails)
-----------------------------------------------
GET|POST /campaigns/unsubscribe?token=…        Email unsubscribe (supports one-click POST)
POST     /campaigns/webhooks/voice             Twilio: TwiML that plays the recording
POST     /campaigns/webhooks/gather            Twilio: opt-out keypress
POST     /campaigns/webhooks/call-status       Twilio: call progress
"""

import csv
import html
import io
import json
import re
import threading
import time
import traceback
import uuid
from datetime import datetime
from functools import wraps
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, Response, current_app, jsonify, request
from twilio.base.exceptions import TwilioRestException
from twilio.twiml.voice_response import Gather, VoiceResponse

from blueprints.super_admin import require_super_admin
from blueprints.voice_agent import _is_valid_twilio_request, _resolve_base_http_url
from config import Config
from extensions import limiter
from utils import campaign_email
from utils.campaign_dispatcher import (
    ACTIVE_CALL_STATUSES,
    add_suppression,
    apply_call_status,
    call_create_params,
    count_contacts,
    ensure_dispatcher_running,
    in_call_window,
    iso,
    unsubscribe_url,
    utcnow,
)
from utils.campaign_email_templates import EMAIL_TEMPLATES
from utils.campaign_import import ImportFileError, normalize_email, normalize_phone, parse_contacts
from utils.supabase_retry import supabase_call_with_retry
from utils.voice_catalog import get_default_voice

campaigns_bp = Blueprint("campaigns", __name__)              # public webhooks + unsubscribe
admin_campaigns_bp = Blueprint("admin_campaigns", __name__)  # super-admin management API

EDITABLE_STATUSES = ("draft", "paused")
_ALWAYS_EDITABLE = ("name", "description")
_INTERNAL_FIELDS = ("lease_owner", "lease_expires_at", "webhook_base_url", "audio_storage_path")

_TEXT_FIELDS = {
    "name": 255,
    "description": 5000,
    "email_subject": 500,
    "email_body_html": 200_000,
    "email_body_text": 100_000,
    "email_from_name": 255,
}
_BOOL_FIELDS = ("call_enabled", "email_enabled", "opt_out_enabled")
_INT_FIELDS = {
    "max_call_attempts": (1, 5),
    "retry_delay_minutes": (1, 10080),
    "ring_timeout_seconds": (5, 120),
    "max_concurrent_calls": (1, None),  # upper bound from config
}
_VOICEMAIL_MODES = ("leave_message", "hangup", "no_detection")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)(:00)?$")

_AUDIO_CONTENT_TYPES = {"mp3": "audio/mpeg", "wav": "audio/wav", "aiff": "audio/aiff"}
_AUDIO_URL_TTL_SECONDS = 6 * 3600
_audio_url_cache = {}  # storage path -> (signed url, monotonic expiry)
_account_numbers_cache = {"at": 0.0, "numbers": []}

_EXPORT_COLUMNS = [
    "row_number", "business_name", "contact_name", "phone_number", "phone_raw", "email",
    "call_status", "call_attempts", "answered_by", "call_duration_seconds", "call_error",
    "call_last_attempt_at", "opted_out", "email_status", "email_sent_at", "email_error",
]


# ── Shared helpers ────────────────────────────────────────────────────────────

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


def _db():
    return current_app.supabase_client


def with_campaign(f):
    """Load the campaign named by the <uuid:campaign_id> URL part, or 404."""
    @wraps(f)
    def decorated_function(*args, campaign_id, **kwargs):
        res = supabase_call_with_retry(
            lambda: _db().table("campaigns").select("*").eq("id", str(campaign_id)).limit(1).execute()
        )
        if not res.data:
            return jsonify({"error": "Campaign not found"}), 404
        return f(*args, campaign=res.data[0], **kwargs)
    return decorated_function


def _twilio_account_numbers():
    """Voice numbers owned by the Twilio account, as [{phone_number, friendly_name}] (cached 5 min)."""
    twilio = current_app.twilio_client
    if not twilio:
        return []
    if time.monotonic() - _account_numbers_cache["at"] < 300:
        return _account_numbers_cache["numbers"]
    try:
        numbers = [
            {"phone_number": n.phone_number, "friendly_name": n.friendly_name}
            for n in twilio.incoming_phone_numbers.list(limit=1000)
        ]
    except Exception as e:
        print(f"[Campaigns] Could not list Twilio numbers: {e}")
        return _account_numbers_cache["numbers"]
    _account_numbers_cache.update(at=time.monotonic(), numbers=numbers)
    return numbers


def _is_account_number(number):
    return any(n["phone_number"] == number for n in _twilio_account_numbers())


def _webhook_base_url():
    if Config.CAMPAIGN_WEBHOOK_BASE_URL:
        return Config.CAMPAIGN_WEBHOOK_BASE_URL.strip().rstrip("/")
    return _resolve_base_http_url()


def _is_public_url(url):
    host = (urlparse(url or "").hostname or "").lower()
    return bool(host) and host not in ("localhost", "127.0.0.1", "0.0.0.0")


def _json_body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _uuid_or_none(value):
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        return None


def _page_args(default_per_page=20, max_per_page=100):
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(max_per_page, max(1, int(request.args.get("per_page", default_per_page))))
    return page, per_page, (page - 1) * per_page


def _pagination(page, per_page, total):
    return {"page": page, "per_page": per_page, "total": total, "pages": -(-total // per_page) if per_page else 1}


def _search_term():
    # Characters that would break out of a PostgREST or=() filter.
    return re.sub(r"[,()*\"\\]", " ", request.args.get("search") or "").strip()


def _campaign_stats(campaign_ids):
    stats = {
        cid: {"call_status": {}, "email_status": {}, "answered_by": {}, "opted_out": 0}
        for cid in campaign_ids
    }
    if not campaign_ids:
        return stats
    res = supabase_call_with_retry(
        lambda: _db().rpc("get_campaign_stats", {"p_campaign_ids": campaign_ids}).execute()
    )
    for row in res.data or []:
        entry = stats.get(row["campaign_id"])
        if entry is None:
            continue
        if row["metric"] == "opted_out":
            entry["opted_out"] = row["total"]
        else:
            entry[row["metric"]][row["value"]] = row["total"]
    return stats


def _summarize(campaign, stats):
    calls, emails, answered = stats["call_status"], stats["email_status"], stats["answered_by"]
    total = sum(calls.values())
    call_remaining = calls.get("pending", 0) + sum(calls.get(s, 0) for s in ACTIVE_CALL_STATUSES)
    call_actionable = total - calls.get("skipped", 0)
    email_remaining = emails.get("pending", 0) + emails.get("sending", 0)
    email_actionable = total - emails.get("skipped", 0)

    done = actionable = 0
    if campaign.get("call_enabled"):
        actionable += call_actionable
        done += call_actionable - call_remaining
    if campaign.get("email_enabled"):
        actionable += email_actionable
        done += email_actionable - email_remaining

    return {
        "contacts": total,
        "progress_percent": round(done * 100 / actionable, 1) if actionable else 0,
        "calls": {
            "by_status": calls,
            "remaining": call_remaining,
            "in_progress": sum(calls.get(s, 0) for s in ACTIVE_CALL_STATUSES),
            "answered_by_person": answered.get("human", 0),
            "voicemail": sum(v for k, v in answered.items() if k.startswith("machine")),
            "opted_out": stats["opted_out"],
        },
        "emails": {
            "by_status": emails,
            "sent": emails.get("sent", 0),
            "remaining": email_remaining,
        },
    }


def _serialize(campaign, stats=None):
    data = {k: v for k, v in campaign.items() if k not in _INTERNAL_FIELDS}
    data["has_audio"] = bool(campaign.get("audio_storage_path"))
    data["in_call_window_now"] = in_call_window(campaign) if campaign.get("call_enabled") else None
    if stats is not None:
        data["stats"] = _summarize(campaign, stats)
    return data


def _serialize_with_stats(campaign):
    return _serialize(campaign, _campaign_stats([campaign["id"]])[campaign["id"]])


def _clean_settings(data):
    """Validate a create/update body. Returns (fields, error)."""
    out = {}

    for field, max_len in _TEXT_FIELDS.items():
        if field not in data:
            continue
        value = data[field]
        if value is not None and not isinstance(value, str):
            return None, f"{field} must be a string"
        value = (value or "").strip()
        if len(value) > max_len:
            return None, f"{field} is too long (max {max_len} characters)"
        out[field] = value or None

    for field in _BOOL_FIELDS:
        if field in data:
            if not isinstance(data[field], bool):
                return None, f"{field} must be true or false"
            out[field] = data[field]

    for field, (low, high) in _INT_FIELDS.items():
        if field not in data:
            continue
        high = high or Config.CAMPAIGN_MAX_CONCURRENT_CALLS_LIMIT
        try:
            value = int(data[field])
        except (TypeError, ValueError):
            return None, f"{field} must be a number"
        if not low <= value <= high:
            return None, f"{field} must be between {low} and {high}"
        out[field] = value

    if "voicemail_mode" in data:
        if data["voicemail_mode"] not in _VOICEMAIL_MODES:
            return None, f"voicemail_mode must be one of: {', '.join(_VOICEMAIL_MODES)}"
        out["voicemail_mode"] = data["voicemail_mode"]

    if "opt_out_digit" in data:
        digit = str(data["opt_out_digit"] or "")
        if not re.fullmatch(r"[0-9]", digit):
            return None, "opt_out_digit must be a single digit 0-9"
        out["opt_out_digit"] = digit

    for field in ("call_window_start", "call_window_end"):
        if field in data:
            match = _TIME_RE.match(str(data[field] or ""))
            if not match:
                return None, f"{field} must be HH:MM (24-hour)"
            out[field] = f"{match.group(1)}:{match.group(2)}"

    if "call_days" in data:
        days = data["call_days"]
        if (not isinstance(days, list) or not days
                or not all(isinstance(d, int) and 1 <= d <= 7 for d in days)):
            return None, "call_days must be a non-empty list of weekdays 1 (Mon) – 7 (Sun)"
        out["call_days"] = sorted(set(days))

    if "timezone" in data:
        tz = data["timezone"]
        if tz:
            try:
                ZoneInfo(str(tz))
            except (ZoneInfoNotFoundError, ValueError):
                return None, f"Unknown timezone '{tz}'"
        out["timezone"] = tz or None

    if "email_reply_to" in data:
        raw = data["email_reply_to"]
        reply_to = normalize_email(raw) if raw else None
        if raw and not reply_to:
            return None, "email_reply_to is not a valid email address"
        out["email_reply_to"] = reply_to

    if "start_at" in data:
        raw = data["start_at"]
        if raw:
            try:
                parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                return None, "start_at must be an ISO 8601 date-time"
            if parsed.tzinfo is None:
                return None, "start_at must include a timezone offset, e.g. 2026-11-10T09:00:00-05:00"
            out["start_at"] = iso(parsed)
        else:
            out["start_at"] = None

    if "from_number" in data:
        number = (data["from_number"] or "").strip()
        if number and not _is_account_number(number):
            return None, "from_number must be a voice number on the Twilio account"
        out["from_number"] = number or None

    return out, None


# ── Audio helpers ─────────────────────────────────────────────────────────────

def _sniff_audio(content):
    """Return (kind, None) for a format Twilio can play, else (None, reason)."""
    if content[:3] == b"ID3" or (len(content) > 1 and content[0] == 0xFF and content[1] & 0xE0 == 0xE0):
        return "mp3", None
    if content[:4] == b"RIFF" and content[8:12] == b"WAVE":
        return "wav", None
    if content[:4] == b"FORM" and content[8:12] in (b"AIFF", b"AIFC"):
        return "aiff", None
    if content[4:8] == b"ftyp":
        name = "M4A/AAC"
    elif content[:4] == b"OggS":
        name = "OGG/Opus"
    elif content[:4] == b"\x1aE\xdf\xa3":
        name = "WebM"
    elif content[:4] == b"fLaC":
        name = "FLAC"
    else:
        return None, "Unsupported audio. Upload an MP3, WAV or AIFF file."
    return None, f"{name} audio can't be played on phone calls. Convert it to MP3 or WAV and upload again."


def _signed_audio_url(path, expires_in):
    res = supabase_call_with_retry(
        lambda: _db().storage.from_(Config.CAMPAIGN_AUDIO_BUCKET).create_signed_url(path, expires_in)
    )
    if isinstance(res, dict):
        return res.get("signedURL") or res.get("signedUrl")
    return getattr(res, "signed_url", None) or getattr(res, "signedURL", None)


def _twilio_audio_url(path):
    """Signed URL for Twilio to fetch the recording; reused so Twilio can cache it."""
    cached = _audio_url_cache.get(path)
    if cached and cached[1] - time.monotonic() > 3600:
        return cached[0]
    url = _signed_audio_url(path, _AUDIO_URL_TTL_SECONDS)
    if url:
        _audio_url_cache[path] = (url, time.monotonic() + _AUDIO_URL_TTL_SECONDS)
    return url


def _remove_audio(path):
    if not path:
        return
    try:
        _db().storage.from_(Config.CAMPAIGN_AUDIO_BUCKET).remove([path])
    except Exception as e:
        print(f"[Campaigns] Failed to remove audio {path}: {e}")
    _audio_url_cache.pop(path, None)


# ── Cross-campaign views ──────────────────────────────────────────────────────

@admin_campaigns_bp.route("/overview", methods=["GET"])
@require_super_admin
@handle_errors
def campaigns_overview(admin_user_id):
    """
    Query params:
        campaign_id – limit to one campaign
        days        – window for calls_by_day (default 30, max 365)
    """
    campaign_id = _uuid_or_none(request.args.get("campaign_id"))
    try:
        days = min(365, max(1, int(request.args.get("days", 30))))
    except (TypeError, ValueError):
        days = 30

    res = supabase_call_with_retry(
        lambda: _db().rpc("get_campaign_call_overview", {"p_campaign_id": campaign_id, "p_days": days}).execute()
    )
    metrics = {}
    for row in res.data or []:
        metrics.setdefault(row["metric"], {})[row["value"]] = int(row["total"] or 0)

    calls = metrics.get("call_status", {})
    answered = metrics.get("answered_by", {})
    emails = metrics.get("email_status", {})
    totals = metrics.get("totals", {})
    contacts_called = totals.get("contacts_called", 0)
    talk_seconds = totals.get("talk_seconds", 0)
    completed = calls.get("completed", 0)

    status_query = _db().table("campaigns").select("status")
    if campaign_id:
        status_query = status_query.eq("id", campaign_id)
    status_rows = supabase_call_with_retry(lambda: status_query.execute()).data or []
    by_status = {}
    for row in status_rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1

    return jsonify({
        "window_days": days,
        "campaigns": {"total": len(status_rows), "by_status": by_status},
        "contacts": sum(calls.values()),
        "calls": {
            "contacts_called": contacts_called,
            "attempts": totals.get("attempts", 0),
            "by_status": calls,
            "answered_by": answered,
            "completed": completed,
            "answered_by_person": answered.get("human", 0),
            "voicemail": sum(v for k, v in answered.items() if k.startswith("machine")),
            "no_answer": calls.get("no-answer", 0),
            "busy": calls.get("busy", 0),
            "failed": calls.get("failed", 0) + calls.get("canceled", 0),
            "in_progress": sum(calls.get(s, 0) for s in ACTIVE_CALL_STATUSES),
            "waiting": calls.get("pending", 0),
            "skipped": calls.get("skipped", 0),
            "opted_out": totals.get("opted_out", 0),
            "talk_seconds": talk_seconds,
            "avg_duration_seconds": round(talk_seconds / completed, 1) if completed else 0,
            "connect_rate_percent": round(completed * 100 / contacts_called, 1) if contacts_called else 0,
        },
        "emails": {
            "by_status": emails,
            "sent": emails.get("sent", 0),
            "failed": emails.get("failed", 0),
        },
        "calls_by_day": [
            {"date": d, "count": c} for d, c in sorted(metrics.get("calls_by_day", {}).items())
        ],
    }), 200


@admin_campaigns_bp.route("/calls", methods=["GET"])
@require_super_admin
@handle_errors
def list_campaign_calls(admin_user_id):
    """
    Every business that has been dialed at least once, newest attempt first.

    Query params:
        page, per_page – pagination (default 1 / 25, max 200)
        campaign_id    – one campaign
        call_status    – completed | busy | no-answer | failed | pending (retry scheduled) | …
        answered       – person | voicemail
        opted_out      – "true" for opt-outs only
        from_date, to_date – YYYY-MM-DD on the last attempt (inclusive)
        search         – business name, phone or email
    """
    page, per_page, offset = _page_args(default_per_page=25, max_per_page=200)
    campaign_id = _uuid_or_none(request.args.get("campaign_id"))
    search = _search_term()

    def _run():
        query = _db().table("campaign_contacts").select("*", count="exact").gt("call_attempts", 0)
        if campaign_id:
            query = query.eq("campaign_id", campaign_id)
        if request.args.get("call_status"):
            query = query.eq("call_status", request.args["call_status"])
        answered = request.args.get("answered")
        if answered == "person":
            query = query.eq("answered_by", "human")
        elif answered == "voicemail":
            query = query.ilike("answered_by", "machine*")
        if (request.args.get("opted_out") or "").lower() == "true":
            query = query.eq("opted_out", True)
        if request.args.get("from_date"):
            query = query.gte("call_last_attempt_at", request.args["from_date"])
        if request.args.get("to_date"):
            query = query.lte("call_last_attempt_at", f"{request.args['to_date']}T23:59:59Z")
        if search:
            query = query.or_(
                f"business_name.ilike.*{search}*,phone_number.ilike.*{search}*,email.ilike.*{search}*"
            )
        return (query.order("call_last_attempt_at", desc=True).order("id")
                .range(offset, offset + per_page - 1).execute())

    res = supabase_call_with_retry(_run)
    rows = res.data or []

    campaign_ids = sorted({r["campaign_id"] for r in rows})
    names = {}
    if campaign_ids:
        found = supabase_call_with_retry(
            lambda: _db().table("campaigns").select("id, name").in_("id", campaign_ids).execute()
        )
        names = {c["id"]: c["name"] for c in found.data or []}
    for r in rows:
        r["campaign_name"] = names.get(r["campaign_id"])

    total = res.count if res.count is not None else len(rows)
    return jsonify({"calls": rows, "pagination": _pagination(page, per_page, total)}), 200


@admin_campaigns_bp.route("/from-numbers", methods=["GET"])
@require_super_admin
@handle_errors
def list_from_numbers(admin_user_id):
    """
    Voice numbers on the Twilio account. `assigned_to` names the tenant whose
    inbound AI agent uses that number — usually not one to market from.
    """
    assigned = supabase_call_with_retry(
        lambda: _db().table("phone_numbers").select("phone_number, tenant_id").eq("status", "active").execute()
    ).data or []
    owner = {r["phone_number"]: r.get("tenant_id") for r in assigned}
    tenant_ids = sorted({t for t in owner.values() if t})
    names = {}
    if tenant_ids:
        found = supabase_call_with_retry(
            lambda: _db().table("tenants").select("id, name").in_("id", tenant_ids).execute()
        )
        names = {t["id"]: t.get("name") for t in found.data or []}

    numbers = [
        {
            **n,
            "assigned_to": names.get(owner.get(n["phone_number"])),
            "is_default": n["phone_number"] == Config.CAMPAIGN_DEFAULT_FROM_NUMBER,
        }
        for n in _twilio_account_numbers()
    ]
    numbers.sort(key=lambda n: (not n["is_default"], n["phone_number"]))
    return jsonify({"numbers": numbers, "default": Config.CAMPAIGN_DEFAULT_FROM_NUMBER}), 200


# ── Campaign CRUD ─────────────────────────────────────────────────────────────

@admin_campaigns_bp.route("", methods=["GET"])
@require_super_admin
@handle_errors
def list_campaigns(admin_user_id):
    """
    Query params:
        page     – 1-based page number (default 1)
        per_page – results per page (default 20, max 100)
        status   – filter by campaign status
    """
    page, per_page, offset = _page_args()
    status = request.args.get("status")

    def _run():
        query = _db().table("campaigns").select("*", count="exact")
        if status:
            query = query.eq("status", status)
        return query.order("created_at", desc=True).range(offset, offset + per_page - 1).execute()

    res = supabase_call_with_retry(_run)
    rows = res.data or []
    stats = _campaign_stats([r["id"] for r in rows])
    total = res.count if res.count is not None else len(rows)
    return jsonify({
        "campaigns": [_serialize(r, stats[r["id"]]) for r in rows],
        "pagination": _pagination(page, per_page, total),
    }), 200


@admin_campaigns_bp.route("", methods=["POST"])
@require_super_admin
@handle_errors
def create_campaign(admin_user_id):
    """Body: {"name": "...", ...settings}. Caller ID defaults to CAMPAIGN_DEFAULT_FROM_NUMBER."""
    data = _json_body()
    if not str(data.get("name") or "").strip():
        return jsonify({"error": "name is required"}), 400

    fields, error = _clean_settings(data)
    if error:
        return jsonify({"error": error}), 400

    fields["from_number"] = fields.get("from_number") or Config.CAMPAIGN_DEFAULT_FROM_NUMBER or None
    fields["timezone"] = fields.get("timezone") or Config.CAMPAIGN_DEFAULT_TIMEZONE
    fields.update({"created_by": admin_user_id, "status": "draft"})

    res = supabase_call_with_retry(lambda: _db().table("campaigns").insert(fields).execute())
    if not res.data:
        return jsonify({"error": "Failed to create campaign"}), 500
    return jsonify({"campaign": _serialize_with_stats(res.data[0])}), 201


@admin_campaigns_bp.route("/<uuid:campaign_id>", methods=["GET"])
@require_super_admin
@handle_errors
@with_campaign
def get_campaign(admin_user_id, campaign):
    return jsonify({"campaign": _serialize_with_stats(campaign)}), 200


@admin_campaigns_bp.route("/<uuid:campaign_id>", methods=["PUT"])
@require_super_admin
@handle_errors
@with_campaign
def update_campaign(admin_user_id, campaign):
    fields, error = _clean_settings(_json_body())
    if error:
        return jsonify({"error": error}), 400
    if "name" in fields and not fields["name"]:
        return jsonify({"error": "name cannot be empty"}), 400
    if not fields:
        return jsonify({"error": "No valid fields to update"}), 400

    if campaign["status"] not in EDITABLE_STATUSES:
        locked = sorted(set(fields) - set(_ALWAYS_EDITABLE))
        if locked:
            return jsonify({
                "error": f"Only name and description can change while the campaign is {campaign['status']}. "
                         "Pause it first to change other settings.",
                "fields": locked,
            }), 409

    res = supabase_call_with_retry(
        lambda: _db().table("campaigns").update(fields).eq("id", campaign["id"]).execute()
    )
    return jsonify({"message": "Campaign updated", "campaign": _serialize_with_stats((res.data or [campaign])[0])}), 200


@admin_campaigns_bp.route("/<uuid:campaign_id>", methods=["DELETE"])
@require_super_admin
@handle_errors
@with_campaign
def delete_campaign(admin_user_id, campaign):
    if campaign["status"] == "running":
        return jsonify({"error": "Pause or cancel the campaign before deleting it"}), 409
    supabase_call_with_retry(lambda: _db().table("campaigns").delete().eq("id", campaign["id"]).execute())
    _remove_audio(campaign.get("audio_storage_path"))
    return jsonify({"message": "Campaign deleted"}), 200


# ── Recorded message ──────────────────────────────────────────────────────────

@admin_campaigns_bp.route("/<uuid:campaign_id>/audio", methods=["POST"])
@require_super_admin
@handle_errors
@with_campaign
def upload_audio(admin_user_id, campaign):
    """Multipart form: file=<mp3 | wav | aiff>."""
    if campaign["status"] not in EDITABLE_STATUSES:
        return jsonify({"error": "Pause the campaign before replacing its recording"}), 409

    upload = request.files.get("file")
    if not upload:
        return jsonify({"error": "Upload the recording as form field 'file'"}), 400
    content = upload.read(Config.CAMPAIGN_AUDIO_MAX_BYTES + 1)
    if len(content) > Config.CAMPAIGN_AUDIO_MAX_BYTES:
        return jsonify({"error": f"Recording is too large (max {Config.CAMPAIGN_AUDIO_MAX_BYTES // (1024 * 1024)} MB)"}), 413
    kind, reason = _sniff_audio(content)
    if not kind:
        return jsonify({"error": reason}), 400

    content_type = _AUDIO_CONTENT_TYPES[kind]
    path = f"campaigns/{campaign['id']}/{uuid.uuid4().hex}.{kind}"
    _db().storage.from_(Config.CAMPAIGN_AUDIO_BUCKET).upload(
        path, content, {"content-type": content_type, "upsert": "true"}
    )

    res = supabase_call_with_retry(
        lambda: _db().table("campaigns").update({
            "audio_storage_path": path,
            "audio_file_name": (upload.filename or f"recording.{kind}")[:255],
            "audio_content_type": content_type,
            "audio_size_bytes": len(content),
        }).eq("id", campaign["id"]).execute()
    )
    _remove_audio(campaign.get("audio_storage_path"))
    return jsonify({"message": "Recording uploaded", "campaign": _serialize_with_stats((res.data or [campaign])[0])}), 200


@admin_campaigns_bp.route("/<uuid:campaign_id>/audio", methods=["GET"])
@require_super_admin
@handle_errors
@with_campaign
def get_audio(admin_user_id, campaign):
    path = campaign.get("audio_storage_path")
    if not path:
        return jsonify({"error": "No recording uploaded yet"}), 404
    return jsonify({
        "url": _signed_audio_url(path, 600),
        "expires_in": 600,
        "file_name": campaign.get("audio_file_name"),
        "content_type": campaign.get("audio_content_type"),
        "size_bytes": campaign.get("audio_size_bytes"),
    }), 200


# ── Contacts ──────────────────────────────────────────────────────────────────

def _existing_contact_keys(campaign_id):
    phones, emails, start = set(), set(), 0
    while True:
        res = supabase_call_with_retry(
            lambda: _db().table("campaign_contacts").select("phone_number, email")
            .eq("campaign_id", campaign_id).order("id").range(start, start + 999).execute()
        )
        rows = res.data or []
        for r in rows:
            if r.get("phone_number"):
                phones.add(r["phone_number"])
            if r.get("email"):
                emails.add(r["email"])
        if len(rows) < 1000:
            return phones, emails
        start += 1000


def _refresh_total_contacts(campaign_id):
    res = supabase_call_with_retry(
        lambda: _db().table("campaign_contacts").select("id", count="exact")
        .eq("campaign_id", campaign_id).limit(1).execute()
    )
    total = res.count or 0
    supabase_call_with_retry(
        lambda: _db().table("campaigns").update({"total_contacts": total}).eq("id", campaign_id).execute()
    )
    return total


@admin_campaigns_bp.route("/<uuid:campaign_id>/contacts/import", methods=["POST"])
@require_super_admin
@handle_errors
@with_campaign
def import_contacts(admin_user_id, campaign):
    """
    Multipart form:
        file            – .xlsx or .csv with a header row
        dry_run         – "true" to only validate and preview (nothing is saved)
        column_mapping  – optional JSON, e.g. {"phone": "Mobile No"} or {"phone": ["Direct Phone", "Phone"]}
        sheet           – optional Excel sheet name (default: first sheet)
        default_country – optional ISO code for numbers without +country code
                          (default: CAMPAIGN_DEFAULT_COUNTRY)
    Re-importing into the same campaign appends; numbers already in it are skipped.
    """
    if campaign["status"] not in EDITABLE_STATUSES:
        return jsonify({"error": "Pause the campaign before importing more contacts"}), 409

    upload = request.files.get("file")
    if not upload:
        return jsonify({"error": "Upload the sheet as form field 'file'"}), 400
    content = upload.read(Config.CAMPAIGN_IMPORT_MAX_BYTES + 1)
    if len(content) > Config.CAMPAIGN_IMPORT_MAX_BYTES:
        return jsonify({"error": f"File is too large (max {Config.CAMPAIGN_IMPORT_MAX_BYTES // (1024 * 1024)} MB)"}), 413

    mapping = None
    if request.form.get("column_mapping"):
        try:
            mapping = json.loads(request.form["column_mapping"])
        except ValueError:
            mapping = None
        if not isinstance(mapping, dict):
            return jsonify({"error": "column_mapping must be a JSON object"}), 400

    region = (request.form.get("default_country") or Config.CAMPAIGN_DEFAULT_COUNTRY or "US").strip().upper()[:2]
    dry_run = (request.form.get("dry_run") or "").lower() in ("1", "true", "yes")
    existing_phones, existing_emails = _existing_contact_keys(campaign["id"])

    try:
        result = parse_contacts(
            upload.filename,
            content,
            default_region=region,
            explicit_mapping=mapping,
            sheet_name=request.form.get("sheet") or None,
            max_rows=Config.CAMPAIGN_IMPORT_MAX_ROWS,
            existing_phones=existing_phones,
            existing_emails=existing_emails,
        )
    except ImportFileError as e:
        return jsonify({"error": str(e)}), 400

    contacts = result["contacts"]
    body = {
        "dry_run": dry_run,
        "default_country": region,
        "columns": result["columns"],
        "compliance": result["compliance"],
        "extra_columns": result["extra_columns"],
        "summary": result["summary"],
        "errors": result["errors"],
        "preview": contacts[:10],
    }
    if dry_run:
        return jsonify(body), 200

    for row in contacts:
        row["campaign_id"] = campaign["id"]
    for i in range(0, len(contacts), 500):
        chunk = contacts[i:i + 500]
        supabase_call_with_retry(lambda: _db().table("campaign_contacts").insert(chunk).execute())

    body["imported"] = len(contacts)
    body["total_contacts"] = _refresh_total_contacts(campaign["id"])
    return jsonify(body), 201


@admin_campaigns_bp.route("/<uuid:campaign_id>/contacts", methods=["GET"])
@require_super_admin
@handle_errors
@with_campaign
def list_contacts(admin_user_id, campaign):
    """
    Query params:
        page, per_page – pagination (default 1 / 50, max 200)
        call_status    – filter, e.g. completed | no-answer | failed
        email_status   – filter, e.g. sent | failed
        opted_out      – "true" to list opt-outs only
        search         – matches business name, phone or email
    """
    page, per_page, offset = _page_args(default_per_page=50, max_per_page=200)
    search = _search_term()

    def _run():
        query = _db().table("campaign_contacts").select("*", count="exact").eq("campaign_id", campaign["id"])
        if request.args.get("call_status"):
            query = query.eq("call_status", request.args["call_status"])
        if request.args.get("email_status"):
            query = query.eq("email_status", request.args["email_status"])
        if (request.args.get("opted_out") or "").lower() == "true":
            query = query.eq("opted_out", True)
        if search:
            query = query.or_(
                f"business_name.ilike.*{search}*,phone_number.ilike.*{search}*,email.ilike.*{search}*"
            )
        return (query.order("created_at").order("row_number").order("id")
                .range(offset, offset + per_page - 1).execute())

    res = supabase_call_with_retry(_run)
    rows = res.data or []
    total = res.count if res.count is not None else len(rows)
    return jsonify({"contacts": rows, "pagination": _pagination(page, per_page, total)}), 200


@admin_campaigns_bp.route("/<uuid:campaign_id>/contacts", methods=["DELETE"])
@require_super_admin
@handle_errors
@with_campaign
def clear_contacts(admin_user_id, campaign):
    if campaign["status"] != "draft":
        return jsonify({"error": "Contacts can only be cleared while the campaign is a draft"}), 409
    supabase_call_with_retry(
        lambda: _db().table("campaign_contacts").delete().eq("campaign_id", campaign["id"]).execute()
    )
    _refresh_total_contacts(campaign["id"])
    return jsonify({"message": "All contacts removed"}), 200


@admin_campaigns_bp.route("/<uuid:campaign_id>/contacts/<uuid:contact_id>", methods=["DELETE"])
@require_super_admin
@handle_errors
@with_campaign
def delete_contact(admin_user_id, campaign, contact_id):
    if campaign["status"] not in EDITABLE_STATUSES:
        return jsonify({"error": "Pause the campaign before removing contacts"}), 409
    res = supabase_call_with_retry(
        lambda: _db().table("campaign_contacts").delete()
        .eq("id", str(contact_id)).eq("campaign_id", campaign["id"]).execute()
    )
    if not res.data:
        return jsonify({"error": "Contact not found"}), 404
    _refresh_total_contacts(campaign["id"])
    return jsonify({"message": "Contact removed"}), 200


def _csv_safe(value):
    """Stop spreadsheet apps from evaluating cell text as a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


@admin_campaigns_bp.route("/<uuid:campaign_id>/export", methods=["GET"])
@require_super_admin
@handle_errors
@with_campaign
def export_contacts(admin_user_id, campaign):
    rows, start = [], 0
    while True:
        res = supabase_call_with_retry(
            lambda: _db().table("campaign_contacts").select("*").eq("campaign_id", campaign["id"])
            .order("created_at").order("row_number").order("id").range(start, start + 999).execute()
        )
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < 1000:
            break
        start += 1000

    extra_keys = sorted({k for r in rows for k in (r.get("extra") or {})})
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(_EXPORT_COLUMNS + extra_keys)
    for r in rows:
        values = [r.get(c) for c in _EXPORT_COLUMNS] + [(r.get("extra") or {}).get(k) for k in extra_keys]
        writer.writerow([
            v if c == "phone_number" else _csv_safe(v)
            for c, v in zip(_EXPORT_COLUMNS + extra_keys, values)
        ])

    filename = re.sub(r"[^A-Za-z0-9_-]+", "-", campaign["name"]).strip("-") or "campaign"
    return Response(
        "﻿" + out.getvalue(),  # BOM so Excel opens UTF-8 correctly
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}-results.csv"'},
    )


@admin_campaigns_bp.route("/contacts-template", methods=["GET"])
@require_super_admin
@handle_errors
def contacts_template(admin_user_id):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Business Name", "Contact Name", "Phone", "Email", "City", "Website"])
    writer.writerow(["Maple Dental Clinic", "Dr. Sarah Lee", "+1 416 555 0100", "info@mapledental.example", "Toronto", "mapledental.example"])
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="campaign-contacts-template.csv"'},
    )


@admin_campaigns_bp.route("/email-templates", methods=["GET"])
@require_super_admin
@handle_errors
def email_templates(admin_user_id):
    """Ready-made email templates the campaign settings can start from."""
    return jsonify({"templates": EMAIL_TEMPLATES}), 200


# ── Tests before launch ───────────────────────────────────────────────────────

@admin_campaigns_bp.route("/<uuid:campaign_id>/test-call", methods=["POST"])
@limiter.limit("10 per minute")
@require_super_admin
@handle_errors
@with_campaign
def test_call(admin_user_id, campaign):
    """Body: {"phone_number": "+12045550142"} — plays the recording exactly as businesses will hear it."""
    data = _json_body()
    phone = normalize_phone(str(data.get("phone_number") or ""), Config.CAMPAIGN_DEFAULT_COUNTRY)
    if not phone:
        return jsonify({"error": "phone_number is not a valid phone number"}), 400
    if not campaign.get("audio_storage_path"):
        return jsonify({"error": "Upload the recording first"}), 400
    twilio = current_app.twilio_client
    if not twilio:
        return jsonify({"error": "Twilio is not configured on the server"}), 503

    from_number = campaign.get("from_number") or Config.CAMPAIGN_DEFAULT_FROM_NUMBER
    if not from_number:
        return jsonify({"error": "No phone number to call from — set from_number"}), 400
    base_url = _webhook_base_url()
    if not _is_public_url(base_url):
        return jsonify({"error": f"Twilio can't reach {base_url}. Set CAMPAIGN_WEBHOOK_BASE_URL or use a public tunnel (ngrok)."}), 400

    params = call_create_params(campaign, to_number=phone, from_number=from_number, base_url=base_url, test=True)
    try:
        call = twilio.calls.create(**params)
    except TwilioRestException as e:
        return jsonify({"error": f"Twilio {e.code}: {e.msg}"}), 400
    return jsonify({"message": "Test call placed", "call_sid": call.sid, "to": phone, "from": from_number}), 200


@admin_campaigns_bp.route("/<uuid:campaign_id>/test-email", methods=["POST"])
@limiter.limit("10 per minute")
@require_super_admin
@handle_errors
@with_campaign
def test_email(admin_user_id, campaign):
    """Body: {"email": "you@example.com"} — rendered with the first contact's data."""
    data = _json_body()
    email = normalize_email(str(data.get("email") or ""))
    if not email:
        return jsonify({"error": "email is not a valid email address"}), 400
    if not campaign.get("email_subject") or not (campaign.get("email_body_html") or campaign.get("email_body_text")):
        return jsonify({"error": "Set email_subject and an email body first"}), 400
    if not campaign_email.smtp_configured():
        return jsonify({"error": "Campaign SMTP is not configured on the server"}), 503

    res = supabase_call_with_retry(
        lambda: _db().table("campaign_contacts").select("*").eq("campaign_id", campaign["id"])
        .not_.is_("email", "null").order("created_at").order("row_number").limit(1).execute()
    )
    sample = dict((res.data or [{}])[0]) or {
        "business_name": "Sample Business", "contact_name": "Alex Smith", "phone_number": "+12045550142",
    }
    sample.update({"id": None, "email": email})

    try:
        with campaign_email.CampaignMailer() as mailer:
            msg = campaign_email.build_message(
                campaign,
                sample,
                from_email=mailer.from_email,
                unsubscribe_url=unsubscribe_url(_webhook_base_url(), current_app.secret_key, sample),
                sender_name=Config.CAMPAIGN_SENDER_NAME,
                sender_address=Config.CAMPAIGN_SENDER_ADDRESS,
            )
            msg.replace_header("Subject", f"[TEST] {msg['Subject']}")
            mailer.send(msg)
    except (campaign_email.SmtpAccountError, campaign_email.SmtpRecipientError) as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({"message": f"Test email sent to {email}"}), 200


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@admin_campaigns_bp.route("/<uuid:campaign_id>/start", methods=["POST"])
@require_super_admin
@handle_errors
@with_campaign
def start_campaign(admin_user_id, campaign):
    """Start a draft or resume a paused campaign. Returns 400 with `problems` if it isn't ready."""
    if campaign["status"] not in EDITABLE_STATUSES:
        return jsonify({"error": f"A {campaign['status']} campaign can't be started"}), 409

    problems, warnings = [], []
    cid = campaign["id"]
    updates = {}
    calls_left = emails_left = 0

    if not campaign.get("call_enabled") and not campaign.get("email_enabled"):
        problems.append("Enable calls, emails, or both")

    if campaign.get("call_enabled"):
        calls_left = count_contacts(_db(), cid, "call_status", ("pending",) + ACTIVE_CALL_STATUSES)
        if not campaign.get("audio_storage_path"):
            problems.append("Upload the recorded message")
        from_number = campaign.get("from_number") or Config.CAMPAIGN_DEFAULT_FROM_NUMBER
        if not current_app.twilio_client:
            problems.append("Twilio is not configured on the server")
        elif not from_number:
            problems.append("No phone number to call from — set from_number")
        elif not _is_account_number(from_number):
            problems.append(f"{from_number} is not a voice number on the Twilio account")
        else:
            updates["from_number"] = from_number
        if not calls_left:
            warnings.append("No businesses left to call")
        elif not in_call_window(campaign):
            warnings.append("Outside the calling window right now; calls start when it opens")

    if campaign.get("email_enabled"):
        emails_left = count_contacts(_db(), cid, "email_status", ("pending", "sending"))
        if not emails_left:
            warnings.append("No businesses left to email")
        if not campaign.get("email_subject"):
            problems.append("Set the email subject")
        if not (campaign.get("email_body_html") or campaign.get("email_body_text")):
            problems.append("Set the email body")
        if not campaign_email.smtp_configured():
            problems.append("Campaign SMTP is not configured on the server")
        if not Config.CAMPAIGN_SENDER_ADDRESS:
            warnings.append("CAMPAIGN_SENDER_ADDRESS isn't set — marketing emails should include a mailing address")

    if (campaign.get("call_enabled") or campaign.get("email_enabled")) and not (calls_left or emails_left):
        problems.append("Nothing left to send — import a sheet of businesses first")

    base_url = _webhook_base_url()
    if not _is_public_url(base_url):
        problems.append(
            f"Twilio and email recipients can't reach {base_url}. "
            "Set CAMPAIGN_WEBHOOK_BASE_URL or use a public tunnel (ngrok)."
        )

    if problems:
        return jsonify({"error": "Campaign is not ready to start", "problems": problems}), 400

    updates.update({
        "status": "running",
        "webhook_base_url": base_url,
        "timezone": campaign.get("timezone") or Config.CAMPAIGN_DEFAULT_TIMEZONE,
        "last_error": None,
        "lease_owner": None,
        "lease_expires_at": None,
    })
    if not campaign.get("started_at"):
        updates["started_at"] = iso(utcnow())

    res = supabase_call_with_retry(
        lambda: _db().table("campaigns").update(updates)
        .eq("id", cid).in_("status", list(EDITABLE_STATUSES)).execute()
    )
    if not res.data:
        return jsonify({"error": "Campaign status changed — reload and try again"}), 409

    if not ensure_dispatcher_running(current_app._get_current_object()):
        warnings.append(
            "The dispatcher isn't running in this process. Make sure a campaign worker is running "
            "(CAMPAIGN_DISPATCHER_ENABLED or `python campaign_worker.py`)."
        )
    return jsonify({
        "message": "Campaign started",
        "warnings": warnings,
        "campaign": _serialize_with_stats(res.data[0]),
    }), 200


def _transition(campaign, allowed_from, updates, message):
    res = supabase_call_with_retry(
        lambda: _db().table("campaigns").update(updates)
        .eq("id", campaign["id"]).in_("status", list(allowed_from)).execute()
    )
    if not res.data:
        return jsonify({"error": f"A {campaign['status']} campaign can't be changed that way"}), 409
    return jsonify({"message": message, "campaign": _serialize_with_stats(res.data[0])}), 200


@admin_campaigns_bp.route("/<uuid:campaign_id>/pause", methods=["POST"])
@require_super_admin
@handle_errors
@with_campaign
def pause_campaign(admin_user_id, campaign):
    return _transition(
        campaign, ("running",),
        {"status": "paused", "lease_owner": None, "lease_expires_at": None},
        "Campaign paused. Calls already in progress will finish.",
    )


@admin_campaigns_bp.route("/<uuid:campaign_id>/cancel", methods=["POST"])
@require_super_admin
@handle_errors
@with_campaign
def cancel_campaign(admin_user_id, campaign):
    return _transition(
        campaign, ("draft", "running", "paused"),
        {"status": "cancelled", "cancelled_at": iso(utcnow()), "lease_owner": None, "lease_expires_at": None},
        "Campaign cancelled",
    )


# ── Do-not-contact list (platform-wide) ───────────────────────────────────────

@admin_campaigns_bp.route("/suppressions", methods=["GET"])
@require_super_admin
@handle_errors
def list_suppressions(admin_user_id):
    """Query params: page, per_page (max 200), channel (phone | email), search."""
    page, per_page, offset = _page_args(default_per_page=50, max_per_page=200)
    channel = request.args.get("channel")
    search = _search_term()

    def _run():
        query = _db().table("campaign_suppressions").select("*", count="exact")
        if channel:
            query = query.eq("channel", channel)
        if search:
            query = query.ilike("value", f"*{search}*")
        return query.order("created_at", desc=True).range(offset, offset + per_page - 1).execute()

    res = supabase_call_with_retry(_run)
    rows = res.data or []
    total = res.count if res.count is not None else len(rows)
    return jsonify({"suppressions": rows, "pagination": _pagination(page, per_page, total)}), 200


@admin_campaigns_bp.route("/suppressions", methods=["POST"])
@require_super_admin
@handle_errors
def add_suppressions(admin_user_id):
    """Body: {"phones": ["+1204…", …], "emails": ["a@b.com", …]} — never contacted again by any campaign."""
    data = _json_body()
    rows, invalid = [], []

    for raw in data.get("phones") or []:
        phone = normalize_phone(str(raw), Config.CAMPAIGN_DEFAULT_COUNTRY)
        if phone:
            rows.append({"channel": "phone", "value": phone, "reason": "manual"})
        else:
            invalid.append(raw)
    for raw in data.get("emails") or []:
        email = normalize_email(str(raw))
        if email:
            rows.append({"channel": "email", "value": email, "reason": "manual"})
        else:
            invalid.append(raw)

    if not rows:
        return jsonify({"error": "No valid phones or emails provided", "invalid": invalid}), 400
    unique = list({(r["channel"], r["value"]): r for r in rows}.values())
    for i in range(0, len(unique), 500):
        chunk = unique[i:i + 500]
        supabase_call_with_retry(
            lambda: _db().table("campaign_suppressions")
            .upsert(chunk, on_conflict="channel,value", ignore_duplicates=True)
            .execute()
        )
    return jsonify({"message": "Added to the do-not-contact list", "added": len(unique), "invalid": invalid}), 201


@admin_campaigns_bp.route("/suppressions/<uuid:suppression_id>", methods=["DELETE"])
@require_super_admin
@handle_errors
def delete_suppression(admin_user_id, suppression_id):
    res = supabase_call_with_retry(
        lambda: _db().table("campaign_suppressions").delete().eq("id", str(suppression_id)).execute()
    )
    if not res.data:
        return jsonify({"error": "Entry not found"}), 404
    return jsonify({"message": "Removed from the do-not-contact list"}), 200


# ── Email unsubscribe (public) ────────────────────────────────────────────────

def _page(title, message, *, form_token=None, status=200):
    form = ""
    if form_token:
        form = (
            f'<form method="post" action="?token={html.escape(form_token)}">'
            '<button type="submit" style="margin-top:16px;padding:10px 18px;border:0;border-radius:8px;'
            'background:#0047ab;color:#fff;font-size:15px;cursor:pointer;">Unsubscribe</button></form>'
        )
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title></head>
<body style="margin:0;padding:48px 16px;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;color:#1f2937;">
<div style="max-width:440px;margin:0 auto;background:#fff;border-radius:12px;padding:28px;box-shadow:0 10px 30px rgba(0,0,0,.08);">
<h1 style="margin:0 0 8px;font-size:20px;">{html.escape(title)}</h1>
<p style="margin:0;font-size:15px;line-height:1.6;color:#4b5563;">{html.escape(message)}</p>{form}
</div></body></html>"""
    return Response(body, status=status, mimetype="text/html")


@campaigns_bp.route("/unsubscribe", methods=["GET", "POST"])
def unsubscribe():
    """
    GET shows a confirmation button (so link scanners can't unsubscribe people);
    POST unsubscribes — this is also the RFC 8058 one-click target that Gmail /
    Yahoo call from the List-Unsubscribe-Post header.
    """
    token = request.args.get("token") or request.form.get("token") or ""
    data = campaign_email.read_unsubscribe_token(current_app.secret_key, token)
    if not data:
        return _page("Link not valid", "This unsubscribe link is invalid or incomplete.", status=400)

    if request.method == "GET":
        return _page("Unsubscribe", f"Stop sending emails to {data['e']}?", form_token=token)

    campaign_id = None
    if data.get("c"):
        res = supabase_call_with_retry(
            lambda: _db().table("campaign_contacts").select("campaign_id").eq("id", data["c"]).limit(1).execute()
        )
        campaign_id = (res.data or [{}])[0].get("campaign_id")
    add_suppression(_db(), "email", data["e"], "email_unsubscribe", campaign_id)
    return _page("You're unsubscribed", f"{data['e']} won't receive these emails anymore.")


# ── Twilio webhooks (public, signature-checked) ───────────────────────────────

def _twiml(response):
    return str(response), 200, {"Content-Type": "text/xml"}


_VOICE_CAMPAIGN_COLUMNS = "id, status, voicemail_mode, opt_out_enabled, opt_out_digit, audio_storage_path"


def _webhook_records(campaign_columns="*", with_contact=True):
    """Load the campaign / contact named in the callback URL (which Twilio signed)."""
    db = _db()
    campaign_id = _uuid_or_none(request.args.get("campaign_id"))
    contact_id = _uuid_or_none(request.args.get("contact_id"))
    if not campaign_id:
        return None, None
    res = supabase_call_with_retry(
        lambda: db.table("campaigns").select(campaign_columns).eq("id", campaign_id).limit(1).execute()
    )
    campaign = (res.data or [None])[0]
    contact = None
    if campaign and contact_id and with_contact:
        res = supabase_call_with_retry(
            lambda: db.table("campaign_contacts").select("*")
            .eq("id", contact_id).eq("campaign_id", campaign_id).limit(1).execute()
        )
        contact = (res.data or [None])[0]
    return campaign, contact


def _in_background(fn):
    """Run a DB write after the response so the caller isn't kept waiting on it."""
    app = current_app._get_current_object()

    def run():
        with app.app_context():
            try:
                fn()
            except Exception as e:
                print(f"[Campaigns] background write failed: {e}")
    threading.Thread(target=run, daemon=True).start()


def _say_voice():
    voice = get_default_voice()
    provider = "Polly" if voice["twilio_tts_provider"] == "Amazon Polly" else voice["twilio_tts_provider"]
    return f"{provider}.{voice['twilio_voice']}"


@campaigns_bp.route("/webhooks/voice", methods=["POST"])
def webhook_voice():
    """TwiML for an answered campaign call: play the recording (+ opt-out keypress)."""
    if not _is_valid_twilio_request():
        print("[Security] /campaigns/webhooks/voice rejected — invalid Twilio signature")
        return "Forbidden", 403

    response = VoiceResponse()
    try:
        # The person is listening to silence until this returns, so: one indexed
        # lookup, and the answered_by write happens after the response.
        campaign, _ = _webhook_records(_VOICE_CAMPAIGN_COLUMNS, with_contact=False)
        if not campaign:  # deleted
            response.hangup()
            return _twiml(response)
        # A call placed before a Pause / Cancel still plays the message when
        # answered; hanging up on someone who just picked up is worse.

        answered_by = request.values.get("AnsweredBy") or ""
        contact_id = _uuid_or_none(request.args.get("contact_id"))
        if contact_id and answered_by:
            db = _db()
            _in_background(lambda: supabase_call_with_retry(
                lambda: db.table("campaign_contacts").update({"answered_by": answered_by})
                .eq("id", contact_id).eq("campaign_id", campaign["id"]).execute()
            ))
        is_machine = answered_by.startswith("machine") or answered_by == "fax"
        if is_machine and campaign.get("voicemail_mode") == "hangup":
            response.hangup()
            return _twiml(response)

        audio_url = _twilio_audio_url(campaign["audio_storage_path"]) if campaign.get("audio_storage_path") else None
        if not audio_url:
            print(f"[Campaigns] No playable recording for campaign {campaign['id']}")
            response.hangup()
            return _twiml(response)

        if campaign.get("opt_out_enabled") and not is_machine:
            # Gather wraps the message so the key can be pressed at any point.
            gather = Gather(
                num_digits=1,
                action=f"/campaigns/webhooks/gather?{request.query_string.decode()}",
                method="POST",
                timeout=4,
            )
            gather.play(audio_url)
            gather.say(
                f"To stop receiving these calls, press {campaign.get('opt_out_digit') or '9'}.",
                voice=_say_voice(),
            )
            response.append(gather)
        else:
            response.play(audio_url)
        response.hangup()
    except Exception as e:
        print(f"[Campaigns] voice webhook error: {e}")
        traceback.print_exc()
        response = VoiceResponse()
        response.hangup()
    return _twiml(response)


@campaigns_bp.route("/webhooks/gather", methods=["POST"])
def webhook_gather():
    """Keypress during/after the message. The opt-out digit adds the number to the do-not-call list."""
    if not _is_valid_twilio_request():
        print("[Security] /campaigns/webhooks/gather rejected — invalid Twilio signature")
        return "Forbidden", 403

    response = VoiceResponse()
    try:
        campaign, contact = _webhook_records()
        digits = request.values.get("Digits") or ""
        if campaign and digits == (campaign.get("opt_out_digit") or "9"):
            if contact:
                add_suppression(_db(), "phone", contact.get("phone_number"), "call_opt_out", campaign["id"])
                supabase_call_with_retry(
                    lambda: _db().table("campaign_contacts").update({"opted_out": True})
                    .eq("id", contact["id"]).execute()
                )
            response.say("You have been removed from our call list. Goodbye.", voice=_say_voice())
    except Exception as e:
        print(f"[Campaigns] gather webhook error: {e}")
        traceback.print_exc()
    response.hangup()
    return _twiml(response)


@campaigns_bp.route("/webhooks/call-status", methods=["POST"])
def webhook_call_status():
    """Twilio call progress for a campaign contact."""
    if not _is_valid_twilio_request():
        print("[Security] /campaigns/webhooks/call-status rejected — invalid Twilio signature")
        return "Forbidden", 403

    try:
        campaign, contact = _webhook_records()
        if not contact:
            return "", 204
        call_sid = request.values.get("CallSid")
        # Ignore callbacks from an earlier attempt of the same contact.
        if request.args.get("attempt") != str(contact.get("call_attempts")):
            return "", 204
        if contact.get("call_sid") and call_sid and contact["call_sid"] != call_sid:
            return "", 204

        error = None
        if request.values.get("ErrorCode"):
            error = f"Twilio {request.values['ErrorCode']}: {request.values.get('ErrorMessage') or ''}".strip()
        elif request.values.get("CallStatus") == "failed" and request.values.get("SipResponseCode"):
            error = f"SIP {request.values['SipResponseCode']}"

        apply_call_status(
            _db(), campaign, contact, request.values.get("CallStatus"),
            call_sid=call_sid,
            duration=request.values.get("CallDuration"),
            answered_by=request.values.get("AnsweredBy"),
            error=error,
        )
    except Exception as e:
        print(f"[Campaigns] call-status webhook error: {e}")
        traceback.print_exc()
    return "", 204
