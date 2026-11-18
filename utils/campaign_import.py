"""
Parse a businesses sheet (Excel .xlsx / .xlsm or .csv) into campaign contacts.

Columns are matched by header name, case/spacing-insensitively, using the
aliases below (e.g. "Company", "Business Name" → business_name; "Mobile",
"Phone #" → phone). Lead exports such as D&B Hoovers often carry several
phone / email columns ("Direct Phone" + company "Phone", "Email" +
"Company Email"); every matching column is used, in alias order, and each
row takes the first valid value. "First Name" + "Last Name" are combined
into contact_name. Fax columns are never dialed.

Do-not-contact markers in the sheet are honoured: a truthy "TPS Flag" /
"DNC" column skips the call, and a "Direct Marketing Status" of "opted out"
skips both the call and the email. Those rows are still imported (so they
show up in results) but are never contacted.

The caller can override detection with an explicit mapping
{"phone": "<header>" | ["<header>", ...], "email": ..., ...}. Every other
column is kept in `extra` under a snake_case key, usable as an email
placeholder. Phones are normalized to E.164 with CAMPAIGN_DEFAULT_COUNTRY as the
default region; a row is kept if it has a usable phone OR email.
"""

import csv
import io
import re

import phonenumbers

# Aliases per field, most specific first. Fields in MULTI_COLUMN_FIELDS use
# every matching column (in this order) as per-row fallbacks.
FIELD_ALIASES = {
    "business_name": [
        "company name", "business name", "business", "company", "organization",
        "organisation", "organization name", "account name", "store name", "shop name", "name",
    ],
    "contact_name": [
        "contact name", "contact person", "full name", "contact", "owner name", "owner", "person",
    ],
    "phone": [
        "direct phone", "direct dial", "direct line", "phone number", "phone", "business phone",
        "company phone", "main phone", "office phone", "work phone", "telephone",
        "telephone number", "tel", "mobile number", "mobile", "mobile phone", "cell phone",
        "cell", "contact number", "phone no", "mobile no", "phone #",
    ],
    "email": [
        "email", "email address", "e mail", "e mail address", "contact email", "work email",
        "business email", "company email", "general email", "email id", "mail",
    ],
}
MULTI_COLUMN_FIELDS = ("phone", "email")
# Used only when nothing above matched — too generic to trust otherwise.
FALLBACK_ALIASES = {"phone": ["number"]}

FIRST_NAME_ALIASES = ["first name", "firstname", "given name"]
LAST_NAME_ALIASES = ["last name", "lastname", "surname", "family name"]
DNC_FLAG_ALIASES = ["tps flag", "tps", "ctps", "dnc", "dnc flag", "do not call", "dncl", "dnc status"]
OPT_OUT_ALIASES = ["direct marketing status", "marketing status", "opt out", "opt out status", "marketing opt out"]
_TRUTHY = {"true", "yes", "y", "1", "x", "listed", "registered", "dnc"}

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_SPLIT_RE = re.compile(r"[,;/|\s]+")

MAX_REPORTED_ERRORS = 100


class ImportFileError(ValueError):
    """The file itself is unusable (wrong type, no header, too many rows)."""


def _norm_header(value):
    text = str(value or "").strip().lower().replace("#", " # ")
    text = re.sub(r"[^a-z0-9#]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def slugify_key(value):
    """'Website URL' -> 'website_url' (used for extra columns / placeholders)."""
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _cell_to_text(value):
    if value is None:
        return ""
    if isinstance(value, float):
        # Excel stores long phone numbers as floats: 14165551234.0
        if value.is_integer():
            return str(int(value))
        return repr(value)
    return str(value).strip()


def _read_rows(filename, content, sheet_name=None):
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        # read_only mode also copes with exports (e.g. D&B Hoovers) whose
        # style table trips up openpyxl's full reader.
        try:
            wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as exc:
            raise ImportFileError(f"Could not read the Excel file: {exc}")
        try:
            if sheet_name:
                if sheet_name not in wb.sheetnames:
                    raise ImportFileError(
                        f"Sheet '{sheet_name}' not found. Available: {', '.join(wb.sheetnames)}"
                    )
                ws = wb[sheet_name]
            else:
                ws = wb.worksheets[0]
            return [[_cell_to_text(c) for c in row] for row in ws.iter_rows(values_only=True)]
        finally:
            wb.close()

    if name.endswith((".csv", ".txt")):
        text = None
        for encoding in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        return [[_cell_to_text(c) for c in row] for row in csv.reader(io.StringIO(text), dialect)]

    if name.endswith(".xls"):
        raise ImportFileError("Old .xls files are not supported. Save the sheet as .xlsx or .csv.")
    raise ImportFileError("Unsupported file type. Upload an .xlsx or .csv file.")


def _find(normalized, aliases, used):
    """Indexes of columns matching `aliases`, in alias order, skipping `used`."""
    found = []
    for alias in aliases:
        for i, header in enumerate(normalized):
            if header == alias and i not in used and i not in found:
                found.append(i)
    return found


def _resolve_columns(headers, explicit_mapping=None):
    """Return {field: [column_index, ...]} for the fields found in `headers`."""
    normalized = [_norm_header(h) for h in headers]
    columns = {}

    for field, wanted in (explicit_mapping or {}).items():
        if field not in FIELD_ALIASES or not wanted:
            continue
        indexes = []
        for header in (wanted if isinstance(wanted, list) else [wanted]):
            target = _norm_header(header)
            if target not in normalized:
                raise ImportFileError(f"Column '{header}' (mapped to {field}) was not found in the sheet.")
            indexes.append(normalized.index(target))
        columns[field] = indexes

    used = {i for indexes in columns.values() for i in indexes}
    for field, aliases in FIELD_ALIASES.items():
        if field in columns:
            continue
        found = _find(normalized, aliases, used) or _find(normalized, FALLBACK_ALIASES.get(field, []), used)
        if not found:
            continue
        columns[field] = found if field in MULTI_COLUMN_FIELDS else found[:1]
        used.update(columns[field])

    if "contact_name" not in columns:
        name_parts = _find(normalized, FIRST_NAME_ALIASES, used)[:1] + _find(normalized, LAST_NAME_ALIASES, used)[:1]
        if name_parts:
            columns["contact_name"] = name_parts  # joined with a space per row
            used.update(name_parts)

    flags = {
        "dnc": _find(normalized, DNC_FLAG_ALIASES, used)[:1],
        "opt_out": _find(normalized, OPT_OUT_ALIASES, used)[:1],
    }
    return columns, {k: v[0] for k, v in flags.items() if v}


def normalize_phone(raw, default_region):
    """Return E.164 for the first valid number found in `raw`, else None."""
    raw = (raw or "").strip()
    if not raw:
        return None
    region = (default_region or "US").upper()
    try:
        parsed = phonenumbers.parse(raw, region)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    # Cells like "416-555-0100 / 905-555-0199" or "Tel: (416) 555 0100 ext 2".
    for match in phonenumbers.PhoneNumberMatcher(raw, region):
        if phonenumbers.is_valid_number(match.number):
            return phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)
    return None


def normalize_email(raw):
    """Return the first valid, lowercased email found in `raw`, else None."""
    for part in _SPLIT_RE.split((raw or "").strip()):
        part = part.strip().strip("<>\"'").lower()
        if part.startswith("mailto:"):
            part = part[len("mailto:"):]
        if _EMAIL_RE.match(part):
            return part
    return None


def _is_opted_out(value):
    text = (value or "").strip().lower()
    if not text:
        return False
    if "not opted out" in text or "has not" in text or text in ("no", "n", "false", "0"):
        return False
    return "opted out" in text or "opt out" in text or text in _TRUTHY


def parse_contacts(filename, content, *, default_region, explicit_mapping=None,
                   sheet_name=None, max_rows=50000, existing_phones=None,
                   existing_emails=None):
    """
    Parse the uploaded sheet.

    Returns a dict:
        contacts       – list of contact dicts ready to insert (minus campaign_id)
        columns        – {field: [header, ...]} that was used (phone/email in priority order)
        compliance     – {"dnc": header, "opt_out": header} columns that were honoured
        extra_columns  – snake_case keys of the other columns
        summary        – counts (total_rows, valid, duplicates, invalid, without_phone,
                         without_email, invalid_phone, do_not_contact)
        errors         – up to MAX_REPORTED_ERRORS {row, reason, value} entries
    """
    rows = _read_rows(filename, content, sheet_name)

    header_idx = next((i for i, r in enumerate(rows) if any(c for c in r)), None)
    if header_idx is None:
        raise ImportFileError("The sheet is empty.")
    headers = rows[header_idx]
    data_rows = rows[header_idx + 1:]
    if len(data_rows) > max_rows:
        raise ImportFileError(f"The sheet has {len(data_rows)} rows; the limit is {max_rows} per import.")

    columns, flag_columns = _resolve_columns(headers, explicit_mapping)
    if "phone" not in columns and "email" not in columns:
        raise ImportFileError(
            "Could not find a phone or email column. Name the columns e.g. 'Phone' and 'Email', "
            "or send a column_mapping."
        )

    standard_idx = {i for indexes in columns.values() for i in indexes}
    extra_columns = [
        (i, slugify_key(h)) for i, h in enumerate(headers)
        if i not in standard_idx and slugify_key(h)
    ]

    seen_phones = set(existing_phones or ())
    seen_emails = set(existing_emails or ())
    contacts, errors = [], []
    summary = {
        "total_rows": 0, "valid": 0, "duplicates": 0, "invalid": 0,
        "without_phone": 0, "without_email": 0, "invalid_phone": 0, "do_not_contact": 0,
    }

    def _values(row, field):
        return [row[i].strip() for i in columns.get(field, []) if i < len(row) and row[i] and row[i].strip()]

    def _flag(row, name):
        idx = flag_columns.get(name)
        return row[idx] if idx is not None and idx < len(row) else ""

    def _error(row_number, reason, value=""):
        summary["invalid"] += 1
        if len(errors) < MAX_REPORTED_ERRORS:
            errors.append({"row": row_number, "reason": reason, "value": value})

    for offset, row in enumerate(data_rows):
        if not any(c for c in row):
            continue
        row_number = header_idx + offset + 2  # 1-based, header row included
        summary["total_rows"] += 1

        phone_values, email_values = _values(row, "phone"), _values(row, "email")
        phone = next((p for p in (normalize_phone(v, default_region) for v in phone_values) if p), None)
        email = next((e for e in (normalize_email(v) for v in email_values) if e), None)
        phone_raw = next((v for v in phone_values if normalize_phone(v, default_region) == phone), None) \
            if phone else (phone_values[0] if phone_values else "")
        email_raw = email_values[0] if email_values else ""

        if not phone and not email:
            reason = "invalid_phone_and_no_email" if phone_raw else "no_phone_or_email"
            _error(row_number, reason, phone_raw or email_raw)
            continue

        if (phone and phone in seen_phones) or (not phone and email in seen_emails):
            summary["duplicates"] += 1
            continue
        duplicate_email = bool(email and email in seen_emails)
        if phone:
            seen_phones.add(phone)
        if email:
            seen_emails.add(email)

        call_status, call_error = "pending", None
        if not phone:
            call_status = "skipped"
            if phone_raw:
                call_error = f"invalid_phone: {phone_raw}"
                summary["invalid_phone"] += 1
            else:
                call_error = "no_phone"
                summary["without_phone"] += 1

        email_status, email_error = "pending", None
        if duplicate_email:
            # Same mailbox on another row (e.g. two branches): call both, email once.
            email_status, email_error = "skipped", "duplicate_email"
        elif not email:
            email_status = "skipped"
            email_error = f"invalid_email: {email_raw}" if email_raw else "no_email"
            summary["without_email"] += 1

        opted_out = _is_opted_out(_flag(row, "opt_out"))
        on_dnc = (_flag(row, "dnc") or "").strip().lower() in _TRUTHY
        if opted_out or on_dnc:
            summary["do_not_contact"] += 1
            source = headers[flag_columns["opt_out" if opted_out else "dnc"]]
            if call_status == "pending":
                call_status, call_error = "skipped", f"do_not_call (sheet: {source})"
            if opted_out and email_status == "pending":
                email_status, email_error = "skipped", f"opted_out (sheet: {source})"

        extra = {}
        for idx, key in extra_columns:
            if idx < len(row) and row[idx]:
                extra[key] = row[idx]

        contacts.append({
            "row_number": row_number,
            "business_name": (_values(row, "business_name") or [None])[0],
            "contact_name": " ".join(_values(row, "contact_name")) or None,
            "phone_number": phone,
            "phone_raw": phone_raw or None,
            "email": email,
            "extra": extra,
            "call_status": call_status,
            "call_error": call_error,
            "email_status": email_status,
            "email_error": email_error,
        })
        summary["valid"] += 1

    return {
        "contacts": contacts,
        "columns": {field: [headers[i] for i in indexes] for field, indexes in columns.items()},
        "compliance": {name: headers[idx] for name, idx in flag_columns.items()},
        "extra_columns": [key for _, key in extra_columns],
        "summary": summary,
        "errors": errors,
    }
