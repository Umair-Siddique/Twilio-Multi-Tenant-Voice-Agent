"""
Campaign email: template rendering, unsubscribe tokens and a reusable SMTP
connection for sending a batch of messages.

Every message gets an unsubscribe link (footer + List-Unsubscribe /
one-click headers, which Gmail and Yahoo require from bulk senders) and the
sender's name and mailing address (CAMPAIGN_SENDER_NAME / _ADDRESS).
"""

import html
import re
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from itsdangerous import BadSignature, URLSafeSerializer

from config import Config

# {{field}} or {{field|fallback text}} — the fallback is used when the field is empty.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*(?:\|([^{}]*))?\}\}")
_TITLES = {"mr", "mrs", "ms", "miss", "dr", "prof", "sir", "madam"}
_TAG_RE = re.compile(r"<[^>]+>")
_UNSUBSCRIBE_SALT = "campaign-unsubscribe"


class SmtpAccountError(Exception):
    """The SMTP account itself is failing (auth, quota, connection) — pause, don't burn the list."""


class SmtpRecipientError(Exception):
    """This one recipient was rejected; other messages can still go out."""


# ── Unsubscribe tokens ────────────────────────────────────────────────────────

def _serializer(secret_key):
    return URLSafeSerializer(secret_key, salt=_UNSUBSCRIBE_SALT)


def make_unsubscribe_token(secret_key, contact_id, email):
    return _serializer(secret_key).dumps({"c": contact_id, "e": email})


def read_unsubscribe_token(secret_key, token):
    """Return {"c": contact_id, "e": email} or None if tampered."""
    try:
        data = _serializer(secret_key).loads(token)
    except BadSignature:
        return None
    return data if isinstance(data, dict) and data.get("e") else None


# ── Rendering ─────────────────────────────────────────────────────────────────

def _first_name(contact):
    """Sheet "First Name" when there is one, else the first word of contact_name (not a title)."""
    explicit = str((contact.get("extra") or {}).get("first_name") or "").strip()
    if explicit:
        return explicit
    words = (contact.get("contact_name") or "").split()
    if words and words[0].rstrip(".").lower() not in _TITLES:
        return words[0]
    return ""


def template_values(contact, *, sender_name=None, sender_address=None):
    """Placeholder values for one contact: standard fields plus extra sheet columns."""
    values = {k: v for k, v in (contact.get("extra") or {}).items()}
    values.update({
        "business_name": contact.get("business_name") or "",
        "contact_name": contact.get("contact_name") or "",
        "first_name": _first_name(contact),
        "email": contact.get("email") or "",
        "phone": contact.get("phone_number") or "",
        "sender_name": sender_name or "",
        "sender_address": sender_address or "",
    })
    return values


def render(template, values, *, escape):
    if not template:
        return ""

    def _sub(match):
        value = str(values.get(match.group(1), "") or "").strip()
        if not value and match.group(2) is not None:
            return match.group(2).strip()  # fallback text is part of the template, not escaped
        return html.escape(value) if escape else value

    return _PLACEHOLDER_RE.sub(_sub, template)


def html_to_text(markup):
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h[1-6]>|</li>", "\n", markup or "")
    text = html.unescape(_TAG_RE.sub("", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def build_message(campaign, contact, *, from_email, unsubscribe_url, sender_name=None,
                  sender_address=None):
    """Build the EmailMessage for one contact of a campaign."""
    values = template_values(contact, sender_name=sender_name, sender_address=sender_address)
    values["unsubscribe_url"] = unsubscribe_url

    subject = render(campaign.get("email_subject"), values, escape=False).strip()
    body_html = render(campaign.get("email_body_html"), values, escape=True)
    body_text = render(campaign.get("email_body_text"), values, escape=False)
    if not body_text and body_html:
        body_text = html_to_text(body_html)

    sender_line = " · ".join(x for x in (sender_name, sender_address) if x)
    # The template carries its own unsubscribe link (and so its own footer) only when
    # every part that will be sent contains one; otherwise the standard footer is added.
    html_part = campaign.get("email_body_html") or ""
    text_part = campaign.get("email_body_text") or ""
    has_link = all(
        "{{unsubscribe_url}}" in re.sub(r"\s+", "", part)
        for part in ((html_part, text_part) if html_part and text_part else (html_part or text_part,))
    )

    if not has_link:
        body_text = (
            f"{body_text.rstrip()}\n\n--\n"
            + (f"{sender_line}\n" if sender_line else "")
            + f"To stop receiving these emails, unsubscribe here: {unsubscribe_url}\n"
        )
        if body_html:
            footer = (
                '<p style="margin-top:32px;font-size:12px;line-height:1.5;color:#6b7280;">'
                + (f"{html.escape(sender_line)}<br>" if sender_line else "")
                + f'Don\'t want these emails? <a href="{html.escape(unsubscribe_url)}" '
                'style="color:#6b7280;">Unsubscribe</a>.</p>'
            )
            if re.search(r"(?i)</body>", body_html):
                body_html = re.sub(r"(?i)</body>", footer + "</body>", body_html, count=1)
            else:
                body_html += footer

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((campaign.get("email_from_name") or sender_name or "", from_email))
    msg["To"] = contact["email"]
    if campaign.get("email_reply_to"):
        msg["Reply-To"] = campaign["email_reply_to"]
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=from_email.split("@")[-1] if "@" in from_email else None)
    msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(body_text or " ")
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    # The default 78-char header folding turns the long List-Unsubscribe URL
    # into an RFC 2047 encoded-word, which mail clients can't use as a link.
    # Headers may be up to 998 chars (RFC 5322); bodies were already encoded.
    msg.policy = msg.policy.clone(max_line_length=998)
    return msg


# ── Sending ───────────────────────────────────────────────────────────────────

def smtp_configured():
    return bool(Config.CAMPAIGN_SMTP_USER and Config.CAMPAIGN_SMTP_PASSWORD and Config.CAMPAIGN_SMTP_FROM_EMAIL)


class CampaignMailer:
    """One SMTP connection reused across a batch: `with CampaignMailer() as m: m.send(msg)`."""

    def __init__(self):
        self.host = Config.CAMPAIGN_SMTP_HOST
        self.port = Config.CAMPAIGN_SMTP_PORT
        self.user = Config.CAMPAIGN_SMTP_USER
        self.password = Config.CAMPAIGN_SMTP_PASSWORD
        self.from_email = Config.CAMPAIGN_SMTP_FROM_EMAIL
        self._server = None

    def __enter__(self):
        if not smtp_configured():
            raise SmtpAccountError("Campaign SMTP credentials are not configured")
        try:
            timeout = Config.SMTP_TIMEOUT_SECONDS
            if self.port == 465:
                self._server = smtplib.SMTP_SSL(self.host, self.port, timeout=timeout)
            else:
                self._server = smtplib.SMTP(self.host, self.port, timeout=timeout)
                self._server.ehlo()
                self._server.starttls()
                self._server.ehlo()
            self._server.login(self.user, self.password)
        except (smtplib.SMTPException, OSError) as exc:
            self._close()
            raise SmtpAccountError(f"SMTP connection/login failed: {exc}")
        return self

    def __exit__(self, *exc):
        self._close()
        return False

    def _close(self):
        if self._server is not None:
            try:
                self._server.quit()
            except Exception:
                pass
            self._server = None

    def send(self, msg):
        try:
            refused = self._server.send_message(msg)
        except smtplib.SMTPRecipientsRefused as exc:
            raise SmtpRecipientError(f"Recipient refused: {exc.recipients}")
        except smtplib.SMTPResponseException as exc:
            detail = exc.smtp_error.decode(errors="replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
            # 421, or 5.4.5 / 5.7.x / quota / hourly-cap wording = a problem with
            # our sending account (e.g. cPanel's "Domain x has exceeded the max
            # emails per hour ... Message discarded"), not with this recipient.
            account_level = exc.smtp_code == 421 or (
                exc.smtp_code in (451, 452, 454, 550, 554)
                and any(m in detail.lower() for m in (
                    "5.4.5", "5.7.", "quota", "limit exceeded", "rate", "exceeded the max",
                    "per hour", "too many", "sending limit",
                ))
            )
            if account_level:
                raise SmtpAccountError(f"SMTP {exc.smtp_code}: {detail}")
            raise SmtpRecipientError(f"SMTP {exc.smtp_code}: {detail}")
        except (smtplib.SMTPServerDisconnected, OSError) as exc:
            raise SmtpAccountError(f"SMTP connection lost: {exc}")
        if refused:
            raise SmtpRecipientError(f"Recipient refused: {refused}")
