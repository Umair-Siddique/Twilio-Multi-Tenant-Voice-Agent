"""
Ready-made campaign email templates, offered in the campaign settings
("Use template"). Placeholders are filled per lead by campaign_email.render:
{{field}} or {{field|fallback}}.

The HTML is table-based with inline styles so it renders the same in Gmail,
Outlook, Apple Mail and on phones; the button is a "bulletproof" table button
(no images, so nothing is blocked by default).
"""

AIDAN_PRO_CTA_URL = "https://aidanpro.ca"

_AIDAN_PRO_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<title>Fixing the 62% unanswered call problem</title>
<style>
  @media only screen and (max-width: 620px) {
    .container { width: 100% !important; border-radius: 0 !important; }
    .px { padding-left: 22px !important; padding-right: 22px !important; }
    .hero-num { font-size: 44px !important; }
    .cta a { font-size: 15px !important; padding: 16px 18px !important; }
  }
</style>
</head>
<body style="margin:0; padding:0; background-color:#EEF2F7; -webkit-text-size-adjust:100%;">
<div style="display:none; max-height:0; overflow:hidden; mso-hide:all; font-size:1px; line-height:1px; color:#EEF2F7;">
  62% of calls to small businesses go unanswered. Here are the numbers I mentioned in my voicemail.
</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#EEF2F7;">
<tr><td align="center" style="padding:28px 12px;">

  <table role="presentation" class="container" width="600" cellpadding="0" cellspacing="0" border="0"
         style="width:600px; max-width:600px; background-color:#FFFFFF; border-radius:14px; overflow:hidden;">
    <!-- brand bar -->
    <tr><td style="background-color:#2563EB; height:6px; line-height:6px; font-size:0;">&nbsp;</td></tr>
    <tr><td class="px" style="padding:26px 40px 6px 40px; font-family:Arial, Helvetica, sans-serif;">
      <span style="font-size:22px; font-weight:bold; color:#0F172A; letter-spacing:-0.3px;">ai<span style="color:#2563EB;">DAN</span> Pro</span>
      <span style="font-size:12px; color:#64748B; padding-left:6px;">by Lumipaix</span>
    </td></tr>

    <!-- intro -->
    <tr><td class="px" style="padding:22px 40px 0 40px; font-family:Arial, Helvetica, sans-serif; font-size:16px; line-height:1.65; color:#1E293B;">
      <p style="margin:0 0 16px 0;">Hi {{first_name|there}},</p>
      <p style="margin:0 0 22px 0;">I just left you a quick voicemail, but wanted to send the numbers over so you have them in front of you.</p>
    </td></tr>

    <!-- 62% hero stat -->
    <tr><td class="px" style="padding:0 40px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
             style="background-color:#EFF6FF; border:1px solid #DBEAFE; border-radius:12px;">
        <tr><td align="center" style="padding:24px 20px; font-family:Arial, Helvetica, sans-serif;">
          <div class="hero-num" style="font-size:54px; line-height:1; font-weight:bold; color:#2563EB;">62%</div>
          <div style="font-size:15px; line-height:1.5; color:#1E3A8A; padding-top:10px;">
            of calls to small and medium businesses go unanswered&nbsp;&mdash; especially after hours.
          </div>
        </td></tr>
      </table>
    </td></tr>

    <!-- voicemail stats -->
    <tr><td class="px" style="padding:26px 40px 0 40px; font-family:Arial, Helvetica, sans-serif; font-size:16px; line-height:1.6; color:#1E293B;">
      <p style="margin:0 0 12px 0; font-weight:bold;">When a buyer hits voicemail:</p>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td width="74" valign="top" style="padding:8px 0; font-family:Arial, Helvetica, sans-serif; font-size:22px; font-weight:bold; color:#DC2626;">80%</td>
          <td valign="top" style="padding:11px 0 8px 0; font-family:Arial, Helvetica, sans-serif; font-size:15px; line-height:1.5; color:#334155; border-bottom:1px solid #F1F5F9;">won't leave a message.</td>
        </tr>
        <tr>
          <td width="74" valign="top" style="padding:8px 0; font-family:Arial, Helvetica, sans-serif; font-size:22px; font-weight:bold; color:#DC2626;">74%</td>
          <td valign="top" style="padding:11px 0 8px 0; font-family:Arial, Helvetica, sans-serif; font-size:15px; line-height:1.5; color:#334155; border-bottom:1px solid #F1F5F9;">hang up within 45 seconds to call your competitor.</td>
        </tr>
        <tr>
          <td width="74" valign="top" style="padding:8px 0; font-family:Arial, Helvetica, sans-serif; font-size:22px; font-weight:bold; color:#DC2626;">85%</td>
          <td valign="top" style="padding:11px 0 8px 0; font-family:Arial, Helvetica, sans-serif; font-size:15px; line-height:1.5; color:#334155;">of those leads are lost for life.</td>
        </tr>
      </table>
    </td></tr>

    <!-- body -->
    <tr><td class="px" style="padding:22px 40px 0 40px; font-family:Arial, Helvetica, sans-serif; font-size:16px; line-height:1.65; color:#1E293B;">
      <p style="margin:0 0 16px 0;">When someone has an urgent need, they don't wait until morning. They just call the next company on Google.</p>
      <p style="margin:0 0 16px 0;">At Lumipaix, we built <strong>aiDAN Pro</strong> to be the common man's AI&nbsp;&mdash; no complicated tech, no massive price tag, and no IT department required. It's a reliable AI voice agent that answers your inbound calls <strong>24/7</strong> and speaks naturally with your customers while you're busy or off the clock.</p>
      <p style="margin:0;">We'd love for you to explore how aiDAN Pro works for your business&nbsp;&mdash; with zero tech headaches, you'll see the difference in your captured leads right away.</p>
    </td></tr>

    <!-- button -->
    <tr><td class="px cta" align="center" style="padding:30px 40px 8px 40px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr><td align="center" bgcolor="#2563EB" style="border-radius:10px; background-color:#2563EB;">
          <a href="__CTA_URL__" target="_blank"
             style="display:block; padding:18px 24px; font-family:Arial, Helvetica, sans-serif; font-size:17px; line-height:1.35; font-weight:bold; color:#FFFFFF; text-decoration:none; letter-spacing:0.3px; border-radius:10px;">
            EXPLORE AND STOP MISSING CALLS FOR 30&nbsp;DAYS&nbsp;&mdash; NO&nbsp;RISK&nbsp;&rarr;
          </a>
        </td></tr>
      </table>
    </td></tr>

    <!-- signature -->
    <tr><td class="px" style="padding:26px 40px 34px 40px; font-family:Arial, Helvetica, sans-serif; font-size:15px; line-height:1.55; color:#1E293B;">
      <p style="margin:0 0 14px 0;">Best,</p>
      <p style="margin:0;">
        <strong style="font-size:16px; color:#0F172A;">Tim Downey</strong><br>
        <span style="color:#475569;">CEO, Lumipaix</span><br>
        <span style="color:#475569;">Makers of aiDAN Pro</span><br>
        <a href="__CTA_URL__" target="_blank" style="color:#2563EB; text-decoration:none; font-weight:bold;">aidanpro.ca</a>
      </p>
    </td></tr>
  </table>

  <!-- footer -->
  <table role="presentation" class="container" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px; max-width:600px;">
    <tr><td class="px" align="center" style="padding:20px 40px 8px 40px; font-family:Arial, Helvetica, sans-serif; font-size:12px; line-height:1.6; color:#64748B;">
      Lumipaix &middot; Makers of aiDAN Pro<br>
      {{sender_address}}<br>
      Don't want these emails? <a href="{{unsubscribe_url}}" target="_blank" style="color:#64748B; text-decoration:underline;">Unsubscribe</a>.
    </td></tr>
  </table>

</td></tr>
</table>
</body>
</html>
""".replace("__CTA_URL__", AIDAN_PRO_CTA_URL)

_AIDAN_PRO_TEXT = """Hi {{first_name|there}},

I just left you a quick voicemail, but wanted to send the numbers over so you have them in front of you.

Right now, 62% of calls to small and medium businesses go unanswered - especially after hours. When a buyer hits voicemail:

  - 80% won't leave a message.
  - 74% hang up within 45 seconds to call your competitor.
  - 85% of those leads are lost for life.

When someone has an urgent need, they don't wait until morning. They just call the next company on Google.

At Lumipaix, we built aiDAN Pro to be the common man's AI - no complicated tech, no massive price tag, and no IT department required. It's a reliable AI voice agent that answers your inbound calls 24/7 and speaks naturally with your customers while you're busy or off the clock.

We'd love for you to explore how aiDAN Pro works for your business - with zero tech headaches, you'll see the difference in your captured leads right away.

Explore and stop missing calls for 30 days - no risk:
__CTA_URL__

Best,
Tim Downey
CEO, Lumipaix
Makers of aiDAN Pro
aidanpro.ca

--
Lumipaix - Makers of aiDAN Pro
{{sender_address}}
Don't want these emails? Unsubscribe: {{unsubscribe_url}}
""".replace("__CTA_URL__", AIDAN_PRO_CTA_URL)

EMAIL_TEMPLATES = [
    {
        "id": "aidan-pro-missed-calls",
        "name": "aiDAN Pro — 62% unanswered calls (voicemail follow-up)",
        "description": "Follow-up to the voicemail: missed-call stats, aiDAN Pro pitch and a 30-day no-risk button.",
        "from_name": "Tim Downey",
        "subject": "Fixing the 62% unanswered call problem",
        "body_html": _AIDAN_PRO_HTML,
        "body_text": _AIDAN_PRO_TEXT,
    },
]
