"""
HubSpot CRM tools for the multi-tenant AI agent.

Each tool accepts an `access_token` string — the tenant's HubSpot Private App
token (or OAuth2 access token) — so every tenant remains fully isolated with
no shared client state.

Tools cover the CRM core objects (Contacts, Companies, Deals, Tickets),
Engagements/Notes, Associations, and Pipelines, all built with the official
`hubspot-api-client` library (https://github.com/HubSpot/hubspot-api-python).

Tools are built with the LangChain @tool decorator and Pydantic input schemas,
making them compatible with any LangChain / LangGraph agent executor.
"""

from typing import Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from hubspot import HubSpot
from hubspot.crm.contacts import (
    SimplePublicObjectInputForCreate as ContactCreateInput,
    SimplePublicObjectInput as ContactUpdateInput,
    PublicObjectSearchRequest,
)
from hubspot.crm.companies import (
    SimplePublicObjectInputForCreate as CompanyCreateInput,
    SimplePublicObjectInput as CompanyUpdateInput,
)
from hubspot.crm.deals import (
    SimplePublicObjectInputForCreate as DealCreateInput,
    SimplePublicObjectInput as DealUpdateInput,
)
from hubspot.crm.tickets import (
    SimplePublicObjectInputForCreate as TicketCreateInput,
    SimplePublicObjectInput as TicketUpdateInput,
)
from hubspot.crm.objects.notes import (
    SimplePublicObjectInputForCreate as NoteCreateInput,
)
from hubspot.crm.associations.v4 import (
    AssociationSpec,
)
from hubspot.crm.contacts.exceptions import ApiException as ContactApiException
from hubspot.crm.companies.exceptions import ApiException as CompanyApiException
from hubspot.crm.deals.exceptions import ApiException as DealApiException
from hubspot.crm.tickets.exceptions import ApiException as TicketApiException

from utils.hubspot_api import hubspot_client_kwargs


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _client(access_token: str) -> HubSpot:
    """Return an authenticated HubSpot client for the given tenant token."""
    return HubSpot(**hubspot_client_kwargs(access_token))


def _to_dict(obj) -> dict:
    """Safely convert a HubSpot response object to a plain dict."""
    if obj is None:
        return {}
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, dict):
        return obj
    return {"result": str(obj)}


def _safe(fn):
    """Execute fn() and return its result or a structured error dict."""
    try:
        result = fn()
        if result is None:
            return {"status": "ok"}
        if isinstance(result, list):
            return [_to_dict(r) for r in result]
        return _to_dict(result)
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Input schemas
# ---------------------------------------------------------------------------

# ── Contacts ────────────────────────────────────────────────────────────────

class SearchContactsInput(BaseModel):
    access_token: str = Field(description="Tenant's HubSpot Private App access token.")
    query: str = Field(description="Free-text search query (name, email, phone, company, etc.).")
    limit: int = Field(default=10, description="Maximum number of contacts to return (1–100).")
    properties: Optional[list[str]] = Field(
        default=None,
        description=(
            "List of contact property names to include in the response. "
            "Defaults to: firstname, lastname, email, phone, company."
        ),
    )


class GetContactInput(BaseModel):
    access_token: str = Field(description="Tenant's HubSpot Private App access token.")
    contact_id: str = Field(description="HubSpot contact ID (numeric string).")
    properties: Optional[list[str]] = Field(
        default=None,
        description="Contact property names to include. Defaults to standard fields.",
    )


class CreateContactInput(BaseModel):
    access_token: str = Field(description="Tenant's HubSpot Private App access token.")
    email: str = Field(description="Contact's email address (used as unique identifier).")
    firstname: Optional[str] = Field(default=None, description="Contact's first name.")
    lastname: Optional[str] = Field(default=None, description="Contact's last name.")
    phone: Optional[str] = Field(default=None, description="Contact's phone number.")
    company: Optional[str] = Field(default=None, description="Company name associated with the contact.")
    jobtitle: Optional[str] = Field(default=None, description="Contact's job title.")
    extra_properties: Optional[dict[str, str]] = Field(
        default=None,
        description="Additional HubSpot contact properties as a key-value dict.",
    )


class UpdateContactInput(BaseModel):
    access_token: str = Field(description="Tenant's HubSpot Private App access token.")
    contact_id: str = Field(description="HubSpot contact ID to update.")
    properties: dict[str, str] = Field(
        description="Dictionary of property names and their new values to update on the contact."
    )


class DeleteContactInput(BaseModel):
    access_token: str = Field(description="Tenant's HubSpot Private App access token.")
    contact_id: str = Field(description="HubSpot contact ID to archive/delete.")


# ── Companies ────────────────────────────────────────────────────────────────

class SearchCompaniesInput(BaseModel):
    access_token: str = Field(description="Tenant's HubSpot Private App access token.")
    query: str = Field(description="Free-text search query (company name, domain, etc.).")
    limit: int = Field(default=10, description="Maximum number of companies to return.")
    properties: Optional[list[str]] = Field(
        default=None,
        description="Company property names to include in the response.",
    )


class GetCompanyInput(BaseModel):
    access_token: str = Field(description="Tenant's HubSpot Private App access token.")
    company_id: str = Field(description="HubSpot company ID.")
    properties: Optional[list[str]] = Field(
        default=None,
        description="Company property names to include.",
    )


class CreateCompanyInput(BaseModel):
    access_token: str = Field(description="Tenant's HubSpot Private App access token.")
    name: str = Field(description="Company name.")
    domain: Optional[str] = Field(default=None, description="Company website domain, e.g. 'acme.com'.")
    phone: Optional[str] = Field(default=None, description="Company phone number.")
    city: Optional[str] = Field(default=None, description="City where the company is located.")
    country: Optional[str] = Field(default=None, description="Country where the company is located.")
    industry: Optional[str] = Field(default=None, description="Industry the company operates in.")
    extra_properties: Optional[dict[str, str]] = Field(
        default=None,
        description="Additional HubSpot company properties as a key-value dict.",
    )


class UpdateCompanyInput(BaseModel):
    access_token: str = Field(description="Tenant's HubSpot Private App access token.")
    company_id: str = Field(description="HubSpot company ID to update.")
    properties: dict[str, str] = Field(
        description="Dictionary of property names and their new values."
    )


# ── Deals ────────────────────────────────────────────────────────────────────

class SearchDealsInput(BaseModel):
    access_token: str = Field(description="Tenant's HubSpot Private App access token.")
    query: str = Field(description="Free-text search query (deal name, stage, etc.).")
    limit: int = Field(default=10, description="Maximum number of deals to return.")
    properties: Optional[list[str]] = Field(
        default=None,
        description="Deal property names to include in the response.",
    )


class GetDealInput(BaseModel):
    access_token: str = Field(description="Tenant's HubSpot Private App access token.")
    deal_id: str = Field(description="HubSpot deal ID.")
    properties: Optional[list[str]] = Field(
        default=None,
        description="Deal property names to include.",
    )


class CreateDealInput(BaseModel):
    access_token: str = Field(description="Tenant's HubSpot Private App access token.")
    dealname: str = Field(description="Name/title of the deal.")
    pipeline: Optional[str] = Field(
        default="default",
        description="Pipeline ID the deal belongs to. Use 'default' for the default pipeline.",
    )
    dealstage: Optional[str] = Field(
        default=None,
        description="Stage ID within the pipeline (e.g. 'appointmentscheduled', 'closedwon').",
    )
    amount: Optional[str] = Field(default=None, description="Deal value as a string, e.g. '5000'.")
    closedate: Optional[str] = Field(
        default=None,
        description="Expected close date as a Unix timestamp in milliseconds (string).",
    )
    extra_properties: Optional[dict[str, str]] = Field(
        default=None,
        description="Additional HubSpot deal properties as a key-value dict.",
    )


class UpdateDealInput(BaseModel):
    access_token: str = Field(description="Tenant's HubSpot Private App access token.")
    deal_id: str = Field(description="HubSpot deal ID to update.")
    properties: dict[str, str] = Field(
        description="Dictionary of property names and their new values."
    )


# ── Tickets ──────────────────────────────────────────────────────────────────

class SearchTicketsInput(BaseModel):
    access_token: str = Field(description="Tenant's HubSpot Private App access token.")
    query: str = Field(description="Free-text search query (subject, status, etc.).")
    limit: int = Field(default=10, description="Maximum number of tickets to return.")
    properties: Optional[list[str]] = Field(
        default=None,
        description="Ticket property names to include in the response.",
    )


class GetTicketInput(BaseModel):
    access_token: str = Field(description="Tenant's HubSpot Private App access token.")
    ticket_id: str = Field(description="HubSpot ticket ID.")
    properties: Optional[list[str]] = Field(
        default=None,
        description="Ticket property names to include.",
    )


class CreateTicketInput(BaseModel):
    access_token: str = Field(description="Tenant's HubSpot Private App access token.")
    subject: str = Field(description="Ticket subject / title.")
    content: Optional[str] = Field(default=None, description="Ticket body / description.")
    hs_pipeline: Optional[str] = Field(
        default="0",
        description="Pipeline ID for the ticket. Defaults to '0' (Support Pipeline).",
    )
    hs_pipeline_stage: Optional[str] = Field(
        default="1",
        description="Stage ID within the pipeline. '1' = New, '2' = Waiting on contact, '3' = Waiting on us, '4' = Closed.",
    )
    hs_ticket_priority: Optional[str] = Field(
        default=None,
        description="Priority level: 'LOW', 'MEDIUM', or 'HIGH'.",
    )
    extra_properties: Optional[dict[str, str]] = Field(
        default=None,
        description="Additional HubSpot ticket properties as a key-value dict.",
    )


class UpdateTicketInput(BaseModel):
    access_token: str = Field(description="Tenant's HubSpot Private App access token.")
    ticket_id: str = Field(description="HubSpot ticket ID to update.")
    properties: dict[str, str] = Field(
        description="Dictionary of property names and their new values."
    )


# ── Notes / Engagements ───────────────────────────────────────────────────────

class CreateNoteInput(BaseModel):
    access_token: str = Field(description="Tenant's HubSpot Private App access token.")
    body: str = Field(description="Text content of the note to log.")
    timestamp: Optional[str] = Field(
        default=None,
        description=(
            "Unix timestamp in milliseconds (string) for when the note occurred. "
            "Defaults to the current time."
        ),
    )
    contact_ids: Optional[list[str]] = Field(
        default=None,
        description="HubSpot contact IDs to associate this note with.",
    )
    company_ids: Optional[list[str]] = Field(
        default=None,
        description="HubSpot company IDs to associate this note with.",
    )
    deal_ids: Optional[list[str]] = Field(
        default=None,
        description="HubSpot deal IDs to associate this note with.",
    )


# ── Associations ─────────────────────────────────────────────────────────────

class AssociateObjectsInput(BaseModel):
    access_token: str = Field(description="Tenant's HubSpot Private App access token.")
    from_object_type: str = Field(
        description="Type of the source object, e.g. 'contacts', 'companies', 'deals', 'tickets'."
    )
    from_object_id: str = Field(description="ID of the source object.")
    to_object_type: str = Field(
        description="Type of the target object, e.g. 'contacts', 'companies', 'deals', 'tickets'."
    )
    to_object_id: str = Field(description="ID of the target object.")
    association_type: str = Field(
        default="",
        description=(
            "Association type label. Common values: "
            "'contact_to_company', 'contact_to_deal', 'deal_to_contact', "
            "'deal_to_company', 'ticket_to_contact'. "
            "Leave empty to use the default association type between the two object types."
        ),
    )


# ── Pipelines ─────────────────────────────────────────────────────────────────

class ListPipelinesInput(BaseModel):
    access_token: str = Field(description="Tenant's HubSpot Private App access token.")
    object_type: str = Field(
        default="deals",
        description="CRM object type whose pipelines to list. Either 'deals' or 'tickets'.",
    )


# ── Owners ────────────────────────────────────────────────────────────────────

class ListOwnersInput(BaseModel):
    access_token: str = Field(description="Tenant's HubSpot Private App access token.")
    limit: int = Field(default=100, description="Maximum number of owners to return.")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

# ── Contacts ────────────────────────────────────────────────────────────────

@tool("search_contacts", args_schema=SearchContactsInput)
def search_contacts(
    access_token: str,
    query: str,
    limit: int = 10,
    properties: Optional[list[str]] = None,
) -> dict:
    """
    Search HubSpot contacts by name, email, phone, or company using a free-text query.

    Returns a list of matching contacts with their properties. Useful for looking up
    a caller's record before creating a new contact or logging a note.
    """
    client = _client(access_token)
    props = properties or ["firstname", "lastname", "email", "phone", "company", "jobtitle"]
    search_request = PublicObjectSearchRequest(query=query, limit=limit, properties=props)
    return _safe(
        lambda: client.crm.contacts.search_api.do_search(
            public_object_search_request=search_request
        )
    )


@tool("get_contact", args_schema=GetContactInput)
def get_contact(
    access_token: str,
    contact_id: str,
    properties: Optional[list[str]] = None,
) -> dict:
    """
    Retrieve a single HubSpot contact by their numeric contact ID.

    Returns full contact details including all requested properties,
    creation date, and last modified date.
    """
    client = _client(access_token)
    props = properties or ["firstname", "lastname", "email", "phone", "company", "jobtitle", "lifecyclestage"]
    return _safe(
        lambda: client.crm.contacts.basic_api.get_by_id(
            contact_id=contact_id,
            properties=props,
        )
    )


@tool("create_contact", args_schema=CreateContactInput)
def create_contact(
    access_token: str,
    email: str,
    firstname: Optional[str] = None,
    lastname: Optional[str] = None,
    phone: Optional[str] = None,
    company: Optional[str] = None,
    jobtitle: Optional[str] = None,
    extra_properties: Optional[dict[str, str]] = None,
) -> dict:
    """
    Create a new contact record in HubSpot CRM.

    The email address is required and acts as the unique identifier.
    Returns the created contact resource including its generated HubSpot ID.
    """
    client = _client(access_token)
    props: dict[str, str] = {"email": email}
    if firstname:
        props["firstname"] = firstname
    if lastname:
        props["lastname"] = lastname
    if phone:
        props["phone"] = phone
    if company:
        props["company"] = company
    if jobtitle:
        props["jobtitle"] = jobtitle
    if extra_properties:
        props.update(extra_properties)

    return _safe(
        lambda: client.crm.contacts.basic_api.create(
            simple_public_object_input_for_create=ContactCreateInput(properties=props)
        )
    )


@tool("update_contact", args_schema=UpdateContactInput)
def update_contact(
    access_token: str,
    contact_id: str,
    properties: dict[str, str],
) -> dict:
    """
    Update one or more properties on an existing HubSpot contact.

    Pass any valid HubSpot contact property names and their new values.
    Returns the updated contact resource. Only provided properties are changed.
    """
    client = _client(access_token)
    return _safe(
        lambda: client.crm.contacts.basic_api.update(
            contact_id=contact_id,
            simple_public_object_input=ContactUpdateInput(properties=properties),
        )
    )


@tool("delete_contact", args_schema=DeleteContactInput)
def delete_contact(access_token: str, contact_id: str) -> dict:
    """
    Archive (soft-delete) a HubSpot contact by their contact ID.

    The contact is moved to the recycling bin and can be restored within 90 days.
    Returns a confirmation on success or an error dict on failure.
    """
    client = _client(access_token)

    def _call():
        client.crm.contacts.basic_api.archive(contact_id=contact_id)
        return {"status": "archived", "contact_id": contact_id}

    return _safe(_call)


# ── Companies ────────────────────────────────────────────────────────────────

@tool("search_companies", args_schema=SearchCompaniesInput)
def search_companies(
    access_token: str,
    query: str,
    limit: int = 10,
    properties: Optional[list[str]] = None,
) -> dict:
    """
    Search HubSpot companies by name, domain, or other properties using a free-text query.

    Returns matching company records with their properties. Useful for finding
    a caller's company before associating contacts or deals.
    """
    from hubspot.crm.companies import PublicObjectSearchRequest as CompanySearchRequest

    client = _client(access_token)
    props = properties or ["name", "domain", "phone", "city", "country", "industry", "numberofemployees"]
    search_request = CompanySearchRequest(query=query, limit=limit, properties=props)
    return _safe(
        lambda: client.crm.companies.search_api.do_search(
            public_object_search_request=search_request
        )
    )


@tool("get_company", args_schema=GetCompanyInput)
def get_company(
    access_token: str,
    company_id: str,
    properties: Optional[list[str]] = None,
) -> dict:
    """
    Retrieve a single HubSpot company by its numeric company ID.

    Returns full company details including name, domain, industry, size,
    and all requested properties.
    """
    client = _client(access_token)
    props = properties or ["name", "domain", "phone", "city", "country", "industry", "annualrevenue"]
    return _safe(
        lambda: client.crm.companies.basic_api.get_by_id(
            company_id=company_id,
            properties=props,
        )
    )


@tool("create_company", args_schema=CreateCompanyInput)
def create_company(
    access_token: str,
    name: str,
    domain: Optional[str] = None,
    phone: Optional[str] = None,
    city: Optional[str] = None,
    country: Optional[str] = None,
    industry: Optional[str] = None,
    extra_properties: Optional[dict[str, str]] = None,
) -> dict:
    """
    Create a new company record in HubSpot CRM.

    Returns the created company resource including its generated HubSpot ID.
    The domain field is used by HubSpot to auto-enrich company data when available.
    """
    client = _client(access_token)
    props: dict[str, str] = {"name": name}
    if domain:
        props["domain"] = domain
    if phone:
        props["phone"] = phone
    if city:
        props["city"] = city
    if country:
        props["country"] = country
    if industry:
        props["industry"] = industry
    if extra_properties:
        props.update(extra_properties)

    return _safe(
        lambda: client.crm.companies.basic_api.create(
            simple_public_object_input_for_create=CompanyCreateInput(properties=props)
        )
    )


@tool("update_company", args_schema=UpdateCompanyInput)
def update_company(
    access_token: str,
    company_id: str,
    properties: dict[str, str],
) -> dict:
    """
    Update one or more properties on an existing HubSpot company.

    Pass any valid HubSpot company property names and their new values.
    Returns the updated company resource. Only provided properties are changed.
    """
    client = _client(access_token)
    return _safe(
        lambda: client.crm.companies.basic_api.update(
            company_id=company_id,
            simple_public_object_input=CompanyUpdateInput(properties=properties),
        )
    )


# ── Deals ────────────────────────────────────────────────────────────────────

@tool("search_deals", args_schema=SearchDealsInput)
def search_deals(
    access_token: str,
    query: str,
    limit: int = 10,
    properties: Optional[list[str]] = None,
) -> dict:
    """
    Search HubSpot deals by name, stage, or other properties using a free-text query.

    Returns matching deal records with their properties including pipeline,
    stage, amount, and close date.
    """
    from hubspot.crm.deals import PublicObjectSearchRequest as DealSearchRequest

    client = _client(access_token)
    props = properties or ["dealname", "pipeline", "dealstage", "amount", "closedate", "hubspot_owner_id"]
    search_request = DealSearchRequest(query=query, limit=limit, properties=props)
    return _safe(
        lambda: client.crm.deals.search_api.do_search(
            public_object_search_request=search_request
        )
    )


@tool("get_deal", args_schema=GetDealInput)
def get_deal(
    access_token: str,
    deal_id: str,
    properties: Optional[list[str]] = None,
) -> dict:
    """
    Retrieve a single HubSpot deal by its numeric deal ID.

    Returns full deal details including pipeline, stage, amount, close date,
    and associated contacts and companies.
    """
    client = _client(access_token)
    props = properties or ["dealname", "pipeline", "dealstage", "amount", "closedate", "hubspot_owner_id", "description"]
    return _safe(
        lambda: client.crm.deals.basic_api.get_by_id(
            deal_id=deal_id,
            properties=props,
        )
    )


@tool("create_deal", args_schema=CreateDealInput)
def create_deal(
    access_token: str,
    dealname: str,
    pipeline: Optional[str] = "default",
    dealstage: Optional[str] = None,
    amount: Optional[str] = None,
    closedate: Optional[str] = None,
    extra_properties: Optional[dict[str, str]] = None,
) -> dict:
    """
    Create a new deal record in HubSpot CRM.

    Use pipeline and dealstage IDs from list_pipelines to place the deal
    in the correct stage. Returns the created deal with its generated HubSpot ID.
    """
    client = _client(access_token)
    props: dict[str, str] = {"dealname": dealname}
    if pipeline:
        props["pipeline"] = pipeline
    if dealstage:
        props["dealstage"] = dealstage
    if amount:
        props["amount"] = amount
    if closedate:
        props["closedate"] = closedate
    if extra_properties:
        props.update(extra_properties)

    return _safe(
        lambda: client.crm.deals.basic_api.create(
            simple_public_object_input_for_create=DealCreateInput(properties=props)
        )
    )


@tool("update_deal", args_schema=UpdateDealInput)
def update_deal(
    access_token: str,
    deal_id: str,
    properties: dict[str, str],
) -> dict:
    """
    Update one or more properties on an existing HubSpot deal.

    Common updates include changing the dealstage (moving through the pipeline),
    updating the amount, or setting a new close date.
    Returns the updated deal resource.
    """
    client = _client(access_token)
    return _safe(
        lambda: client.crm.deals.basic_api.update(
            deal_id=deal_id,
            simple_public_object_input=DealUpdateInput(properties=properties),
        )
    )


# ── Tickets ──────────────────────────────────────────────────────────────────

@tool("search_tickets", args_schema=SearchTicketsInput)
def search_tickets(
    access_token: str,
    query: str,
    limit: int = 10,
    properties: Optional[list[str]] = None,
) -> dict:
    """
    Search HubSpot support tickets by subject, status, or other properties.

    Returns matching ticket records. Useful for checking existing open tickets
    for a caller before creating a duplicate.
    """
    from hubspot.crm.tickets import PublicObjectSearchRequest as TicketSearchRequest

    client = _client(access_token)
    props = properties or ["subject", "content", "hs_pipeline", "hs_pipeline_stage", "hs_ticket_priority"]
    search_request = TicketSearchRequest(query=query, limit=limit, properties=props)
    return _safe(
        lambda: client.crm.tickets.search_api.do_search(
            public_object_search_request=search_request
        )
    )


@tool("get_ticket", args_schema=GetTicketInput)
def get_ticket(
    access_token: str,
    ticket_id: str,
    properties: Optional[list[str]] = None,
) -> dict:
    """
    Retrieve a single HubSpot support ticket by its numeric ticket ID.

    Returns full ticket details including subject, content, pipeline stage,
    priority, and creation/update timestamps.
    """
    client = _client(access_token)
    props = properties or ["subject", "content", "hs_pipeline", "hs_pipeline_stage", "hs_ticket_priority", "createdate"]
    return _safe(
        lambda: client.crm.tickets.basic_api.get_by_id(
            ticket_id=ticket_id,
            properties=props,
        )
    )


@tool("create_ticket", args_schema=CreateTicketInput)
def create_ticket(
    access_token: str,
    subject: str,
    content: Optional[str] = None,
    hs_pipeline: Optional[str] = "0",
    hs_pipeline_stage: Optional[str] = "1",
    hs_ticket_priority: Optional[str] = None,
    extra_properties: Optional[dict[str, str]] = None,
) -> dict:
    """
    Create a new support ticket in HubSpot.

    Pipeline stage '1' = New, '2' = Waiting on contact, '3' = Waiting on us, '4' = Closed.
    Priority can be 'LOW', 'MEDIUM', or 'HIGH'.
    Returns the created ticket resource with its generated HubSpot ID.
    """
    client = _client(access_token)
    props: dict[str, str] = {
        "subject": subject,
        "hs_pipeline": hs_pipeline or "0",
        "hs_pipeline_stage": hs_pipeline_stage or "1",
    }
    if content:
        props["content"] = content
    if hs_ticket_priority:
        props["hs_ticket_priority"] = hs_ticket_priority
    if extra_properties:
        props.update(extra_properties)

    return _safe(
        lambda: client.crm.tickets.basic_api.create(
            simple_public_object_input_for_create=TicketCreateInput(properties=props)
        )
    )


@tool("update_ticket", args_schema=UpdateTicketInput)
def update_ticket(
    access_token: str,
    ticket_id: str,
    properties: dict[str, str],
) -> dict:
    """
    Update one or more properties on an existing HubSpot support ticket.

    Common updates include changing hs_pipeline_stage to move the ticket
    through the support pipeline or updating the content with new notes.
    Returns the updated ticket resource.
    """
    client = _client(access_token)
    return _safe(
        lambda: client.crm.tickets.basic_api.update(
            ticket_id=ticket_id,
            simple_public_object_input=TicketUpdateInput(properties=properties),
        )
    )


# ── Notes / Engagements ───────────────────────────────────────────────────────

@tool("create_note", args_schema=CreateNoteInput)
def create_note(
    access_token: str,
    body: str,
    timestamp: Optional[str] = None,
    contact_ids: Optional[list[str]] = None,
    company_ids: Optional[list[str]] = None,
    deal_ids: Optional[list[str]] = None,
) -> dict:
    """
    Log a note (engagement) in HubSpot and optionally associate it with
    contacts, companies, and/or deals.

    Ideal for recording a call summary, follow-up action, or any free-text
    activity after a voice interaction. Returns the created note with its ID.
    """
    import time as _time

    client = _client(access_token)
    ts = timestamp or str(int(_time.time() * 1000))

    props: dict[str, str] = {
        "hs_note_body": body,
        "hs_timestamp": ts,
    }

    def _call():
        note = client.crm.objects.notes.basic_api.create(
            simple_public_object_input_for_create=NoteCreateInput(
                properties=props,
                associations=[],
            )
        )
        note_dict = _to_dict(note)
        note_id = note_dict.get("id")

        # Associate the note with provided CRM objects
        if note_id:
            for cid in (contact_ids or []):
                try:
                    client.crm.objects.notes.associations_api.create(
                        note_id=note_id,
                        to_object_type="contacts",
                        to_object_id=cid,
                        association_type="note_to_contact",
                    )
                except Exception:
                    pass
            for coid in (company_ids or []):
                try:
                    client.crm.objects.notes.associations_api.create(
                        note_id=note_id,
                        to_object_type="companies",
                        to_object_id=coid,
                        association_type="note_to_company",
                    )
                except Exception:
                    pass
            for did in (deal_ids or []):
                try:
                    client.crm.objects.notes.associations_api.create(
                        note_id=note_id,
                        to_object_type="deals",
                        to_object_id=did,
                        association_type="note_to_deal",
                    )
                except Exception:
                    pass
        return note_dict

    return _safe(_call)


# ── Associations ─────────────────────────────────────────────────────────────

@tool("associate_objects", args_schema=AssociateObjectsInput)
def associate_objects(
    access_token: str,
    from_object_type: str,
    from_object_id: str,
    to_object_type: str,
    to_object_id: str,
    association_type: str = "",
) -> dict:
    """
    Create an association between two HubSpot CRM objects.

    Examples: link a contact to a company, attach a deal to a contact,
    or connect a ticket to a contact. Association types follow the pattern
    '{from_type}_to_{to_type}', e.g. 'contact_to_company'.
    Returns a confirmation on success or an error dict on failure.
    """
    client = _client(access_token)

    def _call():
        assoc_type = association_type or f"{from_object_type.rstrip('s')}_to_{to_object_type.rstrip('s')}"
        client.crm.associations.v4.basic_api.create(
            object_type=from_object_type,
            object_id=from_object_id,
            to_object_type=to_object_type,
            to_object_id=to_object_id,
            association_spec=[
                AssociationSpec(
                    association_category="HUBSPOT_DEFINED",
                    association_type_id=assoc_type,
                )
            ],
        )
        return {
            "status": "associated",
            "from": f"{from_object_type}/{from_object_id}",
            "to": f"{to_object_type}/{to_object_id}",
        }

    return _safe(_call)


# ── Pipelines ─────────────────────────────────────────────────────────────────

@tool("list_pipelines", args_schema=ListPipelinesInput)
def list_pipelines(access_token: str, object_type: str = "deals") -> dict:
    """
    List all pipelines and their stages for deals or tickets in HubSpot.

    Returns pipeline IDs, names, and ordered stage IDs with labels. Use this
    to discover valid pipeline and dealstage values before creating or updating
    deals or tickets.
    """
    client = _client(access_token)
    return _safe(
        lambda: client.crm.pipelines.pipelines_api.get_all(object_type=object_type)
    )


# ── Owners ────────────────────────────────────────────────────────────────────

@tool("list_owners", args_schema=ListOwnersInput)
def list_owners(access_token: str, limit: int = 100) -> dict:
    """
    List all HubSpot owners (users) in the portal.

    Returns owner IDs, first/last names, and email addresses. Owner IDs
    are used in the hubspot_owner_id property when assigning contacts,
    deals, or tickets to a specific team member.
    """
    client = _client(access_token)
    return _safe(lambda: client.crm.owners.owners_api.get_page(limit=limit))


# ---------------------------------------------------------------------------
# Convenience export: all tools as a list for use in agent executors
# ---------------------------------------------------------------------------

HUBSPOT_TOOLS = [
    # Contacts
    search_contacts,
    get_contact,
    create_contact,
    update_contact,
    delete_contact,
    # Companies
    search_companies,
    get_company,
    create_company,
    update_company,
    # Deals
    search_deals,
    get_deal,
    create_deal,
    update_deal,
    # Tickets
    search_tickets,
    get_ticket,
    create_ticket,
    update_ticket,
    # Notes & Engagements
    create_note,
    # Associations
    associate_objects,
    # Pipelines & Owners
    list_pipelines,
    list_owners,
]

# Voice agent: smaller tool set to limit context while covering common call flows.
HUBSPOT_VOICE_TOOLS = [
    search_contacts,
    get_contact,
    create_contact,
    update_contact,
    create_note,
    search_companies,
    get_company,
    search_tickets,
    get_ticket,
    create_ticket,
    update_ticket,
]
