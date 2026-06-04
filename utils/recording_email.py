"""
Send a post-call recording email to the tenant's configured recipients.
Attaches the MP3 recording file and includes a call summary.
"""

import smtplib
import traceback
from datetime import datetime, timezone
from email.message import EmailMessage

from config import Config


def _format_duration(seconds):
    """Convert raw seconds to a human-readable string like '2 min 34 sec'."""
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "N/A"
    if seconds < 60:
        return f"{seconds} sec"
    minutes, secs = divmod(seconds, 60)
    if secs == 0:
        return f"{minutes} min"
    return f"{minutes} min {secs} sec"


def _format_datetime(iso_str):
    """Return a readable date/time from an ISO string, e.g. 'Jun 04, 2026 at 03:45 PM UTC'."""
    if not iso_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y at %I:%M %p UTC")
    except Exception:
        return iso_str


def send_recording_email(recipients, call_data, audio_bytes, recording_sid):
    """
    Send a call recording email with the MP3 attached.

    Parameters
    ----------
    recipients : list[str]
        Tenant email addresses to send to.
    call_data : dict
        Keys: from_number, to_number, duration_seconds, start_time,
              tenant_name, call_sid
    audio_bytes : bytes
        Raw MP3 audio content to attach.
    recording_sid : str
        Twilio recording SID (used in the attachment filename).
    """
    if not recipients:
        return

    smtp_user = Config.ADMIN_EMAIL
    smtp_password = Config.ADMIN_APP_PASSWORD
    smtp_host = Config.SMTP_HOST
    smtp_port = Config.SMTP_PORT
    from_email = Config.SMTP_FROM_EMAIL or smtp_user or ""
    smtp_timeout = Config.SMTP_TIMEOUT_SECONDS

    if not smtp_user or not smtp_password:
        print("[RecordingEmail] SMTP credentials not configured — skipping")
        return

    from_number = call_data.get("from_number") or "Unknown"
    to_number = call_data.get("to_number") or "Unknown"
    duration = _format_duration(call_data.get("duration_seconds"))
    call_date = _format_datetime(call_data.get("start_time"))
    tenant_name = call_data.get("tenant_name") or "Your Account"
    call_sid = call_data.get("call_sid") or recording_sid

    # Short label for the attachment filename: recording-20260604-CA1234abcd.mp3
    date_label = datetime.now(timezone.utc).strftime("%Y%m%d")
    attachment_name = f"recording-{date_label}-{call_sid[:12]}.mp3"

    subject = f"AiDan Pro — Call Recording ({from_number})"

    plain_text = (
        f"AiDan Pro — Call Recording\n\n"
        f"Tenant: {tenant_name}\n"
        f"Caller: {from_number}\n"
        f"Called: {to_number}\n"
        f"Date: {call_date}\n"
        f"Duration: {duration}\n\n"
        f"The call recording is attached as {attachment_name}.\n\n"
        f"This is an automated message from AiDan Pro."
    )

    html_content = f"""
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>AiDan Pro — Call Recording</title>
  </head>
  <body style="margin:0; padding:0; background-color:#f3f4f6; font-family:Arial, Helvetica, sans-serif; color:#1f2937;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
           style="background-color:#f3f4f6; padding:24px 12px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                 style="max-width:600px; background:#ffffff; border-radius:14px; overflow:hidden;
                        box-shadow:0 10px 30px rgba(0,0,0,0.08);">

            <!-- Header -->
            <tr>
              <td style="background:linear-gradient(135deg, #0047ab 0%, #0b5ed7 100%); padding:24px;">
                <p style="margin:0; font-size:24px; line-height:1.3; font-weight:700; color:#ffffff;">AiDan Pro</p>
                <p style="margin:8px 0 0; font-size:14px; line-height:1.4; color:#e5edff;">
                  Call Recording Ready
                </p>
              </td>
            </tr>

            <!-- Body -->
            <tr>
              <td style="padding:28px 24px 20px;">
                <h1 style="margin:0 0 6px; font-size:20px; line-height:1.3; color:#111827;">
                  New Call Recording
                </h1>
                <p style="margin:0 0 20px; font-size:14px; line-height:1.6; color:#6b7280;">
                  {tenant_name}
                </p>

                <!-- Call details card -->
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                       style="background:#f9fafb; border:1px solid #e5e7eb; border-radius:10px;
                              margin-bottom:20px;">
                  <tr>
                    <td style="padding:18px 20px;">
                      <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                        <tr>
                          <td style="padding:6px 0; width:38%;">
                            <span style="font-size:13px; color:#6b7280; font-weight:600;
                                         text-transform:uppercase; letter-spacing:0.04em;">
                              Caller
                            </span>
                          </td>
                          <td style="padding:6px 0;">
                            <span style="font-size:14px; color:#111827; font-weight:600;">
                              {from_number}
                            </span>
                          </td>
                        </tr>
                        <tr>
                          <td style="padding:6px 0;">
                            <span style="font-size:13px; color:#6b7280; font-weight:600;
                                         text-transform:uppercase; letter-spacing:0.04em;">
                              Called
                            </span>
                          </td>
                          <td style="padding:6px 0;">
                            <span style="font-size:14px; color:#111827;">{to_number}</span>
                          </td>
                        </tr>
                        <tr>
                          <td style="padding:6px 0;">
                            <span style="font-size:13px; color:#6b7280; font-weight:600;
                                         text-transform:uppercase; letter-spacing:0.04em;">
                              Date
                            </span>
                          </td>
                          <td style="padding:6px 0;">
                            <span style="font-size:14px; color:#111827;">{call_date}</span>
                          </td>
                        </tr>
                        <tr>
                          <td style="padding:6px 0;">
                            <span style="font-size:13px; color:#6b7280; font-weight:600;
                                         text-transform:uppercase; letter-spacing:0.04em;">
                              Duration
                            </span>
                          </td>
                          <td style="padding:6px 0;">
                            <span style="font-size:14px; color:#111827;">{duration}</span>
                          </td>
                        </tr>
                      </table>
                    </td>
                  </tr>
                </table>

                <!-- Attachment notice -->
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                       style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:10px;
                              margin-bottom:8px;">
                  <tr>
                    <td style="padding:14px 18px;">
                      <p style="margin:0; font-size:14px; line-height:1.6; color:#1d4ed8;">
                        <strong>&#128241; Recording attached</strong> — the audio file
                        <code style="background:#dbeafe; padding:2px 6px; border-radius:4px;
                                     font-size:12px; color:#1e40af;">{attachment_name}</code>
                        is included with this email. Open it in any audio player.
                      </p>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

            <!-- Footer -->
            <tr>
              <td style="padding:14px 24px 20px; border-top:1px solid #e5e7eb;">
                <p style="margin:0; font-size:12px; line-height:1.5; color:#9ca3af;">
                  This is an automated message from AiDan Pro. Do not reply to this email.
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

    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = ", ".join(recipients)
        msg.set_content(plain_text)
        msg.add_alternative(html_content, subtype="html")

        if audio_bytes:
            msg.add_attachment(
                audio_bytes,
                maintype="audio",
                subtype="mpeg",
                filename=attachment_name,
            )

        with smtplib.SMTP(smtp_host, smtp_port, timeout=smtp_timeout) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)

        print(f"[RecordingEmail] Sent to {recipients} for call {call_sid}")
        return True, None
    except Exception as exc:
        print(f"[RecordingEmail] Failed to send email for call {call_sid}:")
        traceback.print_exc()
        return False, str(exc)
