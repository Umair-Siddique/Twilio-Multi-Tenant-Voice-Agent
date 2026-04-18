"""
Google Calendar tools for the multi-tenant AI agent.

Each tool accepts a `credentials_json` string — a JSON-serialised OAuth 2.0
token dict (access_token, refresh_token, token_uri, client_id, client_secret,
scopes) — so every tenant can supply its own credentials independently.

Tools are built with the LangChain @tool decorator and Pydantic input schemas,
making them compatible with any LangChain / LangGraph agent executor.
"""

import json
from typing import Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from config import Config


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_service(credentials_json: str):
    """Return an authenticated Google Calendar API service."""
    creds_dict = json.loads(credentials_json)
    client_id = creds_dict.get("client_id") or Config.GOOGLE_OAUTH_CLIENT_ID
    client_secret = creds_dict.get("client_secret") or Config.GOOGLE_OAUTH_CLIENT_SECRET
    creds = Credentials(
        token=creds_dict.get("access_token"),
        refresh_token=creds_dict.get("refresh_token"),
        token_uri=creds_dict.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=client_id,
        client_secret=client_secret,
        scopes=creds_dict.get("scopes"),
    )
    return build("calendar", "v3", credentials=creds)


def _safe_execute(request_fn):
    """Execute a Google API request and return a JSON-serialisable result."""
    try:
        return request_fn()
    except HttpError as exc:
        return {"error": str(exc), "status_code": exc.resp.status}
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Input schemas
# ---------------------------------------------------------------------------

class CalendarListInput(BaseModel):
    credentials_json: str = Field(
        description=(
            "JSON string of the tenant's OAuth2 credentials with keys: "
            "access_token, refresh_token, token_uri, client_id, client_secret, scopes."
        )
    )
    max_results: int = Field(
        default=100,
        description="Maximum number of calendars to return (1–250)."
    )
    show_hidden: bool = Field(
        default=False,
        description="Whether to include hidden calendars in the result."
    )


class CalendarGetInput(BaseModel):
    credentials_json: str = Field(
        description="JSON string of the tenant's OAuth2 credentials."
    )
    calendar_id: str = Field(
        description=(
            "Calendar identifier. Use 'primary' for the user's primary calendar, "
            "or the full calendar email address for other calendars."
        )
    )


class EventListInput(BaseModel):
    credentials_json: str = Field(
        description="JSON string of the tenant's OAuth2 credentials."
    )
    calendar_id: str = Field(
        default="primary",
        description="Calendar identifier. Defaults to 'primary'."
    )
    time_min: Optional[str] = Field(
        default=None,
        description=(
            "Lower bound (inclusive) for an event's end time to filter by. "
            "RFC3339 timestamp, e.g. '2024-01-01T00:00:00Z'."
        )
    )
    time_max: Optional[str] = Field(
        default=None,
        description=(
            "Upper bound (exclusive) for an event's start time to filter by. "
            "RFC3339 timestamp, e.g. '2024-01-31T23:59:59Z'."
        )
    )
    max_results: int = Field(
        default=10,
        description="Maximum number of events to return (1–2500)."
    )
    query: Optional[str] = Field(
        default=None,
        description="Free-text search term to filter events by summary, description, location, etc."
    )
    order_by: str = Field(
        default="startTime",
        description="Order of events in the response. Either 'startTime' or 'updated'."
    )
    single_events: bool = Field(
        default=True,
        description=(
            "Whether to expand recurring events into individual instances. "
            "Required when ordering by 'startTime'."
        )
    )


class EventGetInput(BaseModel):
    credentials_json: str = Field(
        description="JSON string of the tenant's OAuth2 credentials."
    )
    calendar_id: str = Field(
        default="primary",
        description="Calendar identifier. Defaults to 'primary'."
    )
    event_id: str = Field(
        description="Unique identifier of the event to retrieve."
    )


class EventCreateInput(BaseModel):
    credentials_json: str = Field(
        description="JSON string of the tenant's OAuth2 credentials."
    )
    calendar_id: str = Field(
        default="primary",
        description="Calendar identifier where the event will be created."
    )
    summary: str = Field(
        description="Title of the event."
    )
    start_datetime: str = Field(
        description=(
            "Start date/time of the event in RFC3339 format, "
            "e.g. '2024-06-15T10:00:00-07:00'. "
            "For all-day events use a date string: '2024-06-15'."
        )
    )
    end_datetime: str = Field(
        description=(
            "End date/time of the event in RFC3339 format, "
            "e.g. '2024-06-15T11:00:00-07:00'. "
            "For all-day events use a date string: '2024-06-16'."
        )
    )
    timezone: str = Field(
        default="UTC",
        description="IANA timezone for start/end times, e.g. 'America/New_York'."
    )
    description: Optional[str] = Field(
        default=None,
        description="Description or notes for the event."
    )
    location: Optional[str] = Field(
        default=None,
        description="Geographic location of the event, e.g. an address or meeting room."
    )
    attendees: Optional[list[str]] = Field(
        default=None,
        description="List of attendee email addresses to invite."
    )
    send_updates: str = Field(
        default="none",
        description=(
            "Whether to send email notifications. "
            "One of: 'all', 'externalOnly', 'none'."
        )
    )


class EventUpdateInput(BaseModel):
    credentials_json: str = Field(
        description="JSON string of the tenant's OAuth2 credentials."
    )
    calendar_id: str = Field(
        default="primary",
        description="Calendar identifier that contains the event."
    )
    event_id: str = Field(
        description="Unique identifier of the event to update."
    )
    summary: Optional[str] = Field(
        default=None,
        description="New title for the event."
    )
    start_datetime: Optional[str] = Field(
        default=None,
        description="New start date/time in RFC3339 format."
    )
    end_datetime: Optional[str] = Field(
        default=None,
        description="New end date/time in RFC3339 format."
    )
    timezone: Optional[str] = Field(
        default=None,
        description="IANA timezone for the updated start/end times."
    )
    description: Optional[str] = Field(
        default=None,
        description="New description for the event."
    )
    location: Optional[str] = Field(
        default=None,
        description="New location for the event."
    )
    send_updates: str = Field(
        default="none",
        description="Whether to send update notifications. One of: 'all', 'externalOnly', 'none'."
    )


class EventDeleteInput(BaseModel):
    credentials_json: str = Field(
        description="JSON string of the tenant's OAuth2 credentials."
    )
    calendar_id: str = Field(
        default="primary",
        description="Calendar identifier that contains the event."
    )
    event_id: str = Field(
        description="Unique identifier of the event to delete."
    )
    send_updates: str = Field(
        default="none",
        description="Whether to notify attendees of the cancellation. One of: 'all', 'externalOnly', 'none'."
    )


class EventQuickAddInput(BaseModel):
    credentials_json: str = Field(
        description="JSON string of the tenant's OAuth2 credentials."
    )
    calendar_id: str = Field(
        default="primary",
        description="Calendar identifier where the event will be added."
    )
    text: str = Field(
        description=(
            "Natural language text describing the event, "
            "e.g. 'Team meeting tomorrow at 3pm for 1 hour'."
        )
    )
    send_updates: str = Field(
        default="none",
        description="Whether to send notifications. One of: 'all', 'externalOnly', 'none'."
    )


class FreeBusyInput(BaseModel):
    credentials_json: str = Field(
        description="JSON string of the tenant's OAuth2 credentials."
    )
    time_min: str = Field(
        description="Start of the interval to query in RFC3339 format, e.g. '2024-06-15T00:00:00Z'."
    )
    time_max: str = Field(
        description="End of the interval to query in RFC3339 format, e.g. '2024-06-15T23:59:59Z'."
    )
    calendar_ids: list[str] = Field(
        default=["primary"],
        description="List of calendar IDs to query free/busy information for."
    )
    timezone: str = Field(
        default="UTC",
        description="IANA timezone to use for the query results."
    )


class EventInstancesInput(BaseModel):
    credentials_json: str = Field(
        description="JSON string of the tenant's OAuth2 credentials."
    )
    calendar_id: str = Field(
        default="primary",
        description="Calendar identifier that contains the recurring event."
    )
    event_id: str = Field(
        description="Unique identifier of the recurring event whose instances to list."
    )
    time_min: Optional[str] = Field(
        default=None,
        description="Lower bound for instance start time (RFC3339)."
    )
    time_max: Optional[str] = Field(
        default=None,
        description="Upper bound for instance start time (RFC3339)."
    )
    max_results: int = Field(
        default=10,
        description="Maximum number of instances to return."
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool("list_calendars", args_schema=CalendarListInput)
def list_calendars(
    credentials_json: str,
    max_results: int = 100,
    show_hidden: bool = False,
) -> dict:
    """
    List all calendars on the authenticated user's Google Calendar list.

    Returns a list of calendar entries, each containing id, summary, timeZone,
    accessRole, and primary flag. Useful for discovering calendar IDs before
    performing event operations.
    """
    service = _build_service(credentials_json)

    def _call():
        return service.calendarList().list(
            maxResults=max_results,
            showHidden=show_hidden,
        ).execute()

    return _safe_execute(_call)


@tool("get_calendar", args_schema=CalendarGetInput)
def get_calendar(credentials_json: str, calendar_id: str) -> dict:
    """
    Retrieve metadata for a specific Google Calendar.

    Returns calendar details including id, summary, description, location,
    timeZone, and access roles. Use 'primary' for the user's main calendar.
    """
    service = _build_service(credentials_json)
    return _safe_execute(lambda: service.calendars().get(calendarId=calendar_id).execute())


@tool("list_events", args_schema=EventListInput)
def list_events(
    credentials_json: str,
    calendar_id: str = "primary",
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int = 10,
    query: Optional[str] = None,
    order_by: str = "startTime",
    single_events: bool = True,
) -> dict:
    """
    List events on a Google Calendar within an optional time range.

    Returns a list of events with id, summary, start, end, location, description,
    status, and attendees. Supports free-text search via the query parameter.
    Use time_min/time_max (RFC3339) to scope the results to a date window.
    """
    service = _build_service(credentials_json)

    kwargs: dict = {
        "calendarId": calendar_id,
        "maxResults": max_results,
        "orderBy": order_by,
        "singleEvents": single_events,
    }
    if time_min:
        kwargs["timeMin"] = time_min
    if time_max:
        kwargs["timeMax"] = time_max
    if query:
        kwargs["q"] = query

    return _safe_execute(lambda: service.events().list(**kwargs).execute())


@tool("get_event", args_schema=EventGetInput)
def get_event(
    credentials_json: str,
    calendar_id: str = "primary",
    event_id: str = "",
) -> dict:
    """
    Retrieve a single event by its ID from a Google Calendar.

    Returns full event details including summary, description, location, start/end
    times, attendees, organiser, status, and any conferencing information.
    """
    service = _build_service(credentials_json)
    return _safe_execute(
        lambda: service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    )


@tool("create_event", args_schema=EventCreateInput)
def create_event(
    credentials_json: str,
    calendar_id: str = "primary",
    summary: str = "",
    start_datetime: str = "",
    end_datetime: str = "",
    timezone: str = "UTC",
    description: Optional[str] = None,
    location: Optional[str] = None,
    attendees: Optional[list[str]] = None,
    send_updates: str = "none",
) -> dict:
    """
    Create a new event on a Google Calendar.

    Supports standard timed events (RFC3339 start/end) and all-day events (date-only
    strings). Optionally invite attendees by email and set a location or description.
    Returns the newly created event resource including its generated event id.
    """
    service = _build_service(credentials_json)

    # Detect all-day event (date-only string has no 'T')
    if "T" in start_datetime:
        start = {"dateTime": start_datetime, "timeZone": timezone}
        end = {"dateTime": end_datetime, "timeZone": timezone}
    else:
        start = {"date": start_datetime}
        end = {"date": end_datetime}

    body: dict = {"summary": summary, "start": start, "end": end}
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": addr} for addr in attendees]

    return _safe_execute(
        lambda: service.events().insert(
            calendarId=calendar_id,
            body=body,
            sendUpdates=send_updates,
        ).execute()
    )


@tool("update_event", args_schema=EventUpdateInput)
def update_event(
    credentials_json: str,
    calendar_id: str = "primary",
    event_id: str = "",
    summary: Optional[str] = None,
    start_datetime: Optional[str] = None,
    end_datetime: Optional[str] = None,
    timezone: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    send_updates: str = "none",
) -> dict:
    """
    Update fields on an existing Google Calendar event using a patch (partial update).

    Only the fields provided will be changed; omitted fields remain unchanged.
    Returns the updated event resource. To change attendees or recurrence rules,
    retrieve the event first, modify the full body, and use this tool with all fields.
    """
    service = _build_service(credentials_json)

    patch_body: dict = {}
    if summary is not None:
        patch_body["summary"] = summary
    if description is not None:
        patch_body["description"] = description
    if location is not None:
        patch_body["location"] = location

    if start_datetime is not None:
        tz = timezone or "UTC"
        patch_body["start"] = (
            {"dateTime": start_datetime, "timeZone": tz}
            if "T" in start_datetime
            else {"date": start_datetime}
        )
    if end_datetime is not None:
        tz = timezone or "UTC"
        patch_body["end"] = (
            {"dateTime": end_datetime, "timeZone": tz}
            if "T" in end_datetime
            else {"date": end_datetime}
        )

    return _safe_execute(
        lambda: service.events().patch(
            calendarId=calendar_id,
            eventId=event_id,
            body=patch_body,
            sendUpdates=send_updates,
        ).execute()
    )


@tool("delete_event", args_schema=EventDeleteInput)
def delete_event(
    credentials_json: str,
    calendar_id: str = "primary",
    event_id: str = "",
    send_updates: str = "none",
) -> dict:
    """
    Delete an event from a Google Calendar.

    Permanently removes the event identified by event_id. If send_updates is 'all'
    or 'externalOnly', attendees receive cancellation notifications.
    Returns a confirmation message on success or an error dict on failure.
    """
    service = _build_service(credentials_json)

    def _call():
        service.events().delete(
            calendarId=calendar_id,
            eventId=event_id,
            sendUpdates=send_updates,
        ).execute()
        return {"status": "deleted", "event_id": event_id, "calendar_id": calendar_id}

    return _safe_execute(_call)


@tool("quick_add_event", args_schema=EventQuickAddInput)
def quick_add_event(
    credentials_json: str,
    calendar_id: str = "primary",
    text: str = "",
    send_updates: str = "none",
) -> dict:
    """
    Create a Google Calendar event from a natural-language text description.

    The Google Calendar API parses the text to extract the event title, date, time,
    and duration automatically. Example text: 'Coffee with John next Monday at 9am'.
    Returns the created event resource including its generated event id and parsed times.
    """
    service = _build_service(credentials_json)
    return _safe_execute(
        lambda: service.events().quickAdd(
            calendarId=calendar_id,
            text=text,
            sendUpdates=send_updates,
        ).execute()
    )


@tool("get_freebusy", args_schema=FreeBusyInput)
def get_freebusy(
    credentials_json: str,
    time_min: str = "",
    time_max: str = "",
    calendar_ids: list[str] = ["primary"],
    timezone: str = "UTC",
) -> dict:
    """
    Query free/busy information for one or more Google Calendars.

    Returns busy time intervals for each requested calendar within the given time
    window. Useful for scheduling — check availability before creating a new event.
    time_min and time_max must be RFC3339 timestamps, e.g. '2024-06-15T09:00:00Z'.
    """
    service = _build_service(credentials_json)

    body = {
        "timeMin": time_min,
        "timeMax": time_max,
        "timeZone": timezone,
        "items": [{"id": cal_id} for cal_id in calendar_ids],
    }
    return _safe_execute(lambda: service.freebusy().query(body=body).execute())


@tool("list_event_instances", args_schema=EventInstancesInput)
def list_event_instances(
    credentials_json: str,
    calendar_id: str = "primary",
    event_id: str = "",
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int = 10,
) -> dict:
    """
    List all instances of a recurring Google Calendar event.

    Given the event_id of a recurring event (master event), returns individual
    occurrence instances within the optional time_min / time_max window.
    Each instance has its own event id that can be used to update or delete
    that specific occurrence without affecting the whole series.
    """
    service = _build_service(credentials_json)

    kwargs: dict = {
        "calendarId": calendar_id,
        "eventId": event_id,
        "maxResults": max_results,
    }
    if time_min:
        kwargs["timeMin"] = time_min
    if time_max:
        kwargs["timeMax"] = time_max

    return _safe_execute(lambda: service.events().instances(**kwargs).execute())


# ---------------------------------------------------------------------------
# Convenience export: all tools as a list for use in agent executors
# ---------------------------------------------------------------------------

GOOGLE_CALENDAR_TOOLS = [
    list_calendars,
    get_calendar,
    list_events,
    get_event,
    create_event,
    update_event,
    delete_event,
    quick_add_event,
    get_freebusy,
    list_event_instances,
]
