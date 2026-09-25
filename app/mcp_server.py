"""MCP server: exposes the Personal API's endpoints as tools for MCP clients (Claude Desktop, Claude Code).

    python -m app.mcp_server        # speaks MCP over stdio; the client starts it, you don't run it by hand

It is a thin wrapper that calls the running HTTP API (API_URL, default http://127.0.0.1:8000),
so the API's validation and auth apply unchanged. The X-API-Key comes from this repo's .env and
is never exposed to the client. Errors come back as short, readable tool errors, never tracebacks.
"""

import logging
import sys
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from app.config import Settings

log = logging.getLogger("app.mcp_server")

REPO_ROOT = Path(__file__).resolve().parents[1]

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
CREATE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
UPDATE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)
DELETE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)
ADJUST = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)

INSTRUCTIONS = """\
Tools for the user's Personal API: one source of truth for their pantry, schedule, habits,
contacts, notes, and their indexed Obsidian vault (searchable by meaning).
Start with get_context for a quick situational summary, or search_knowledge to find what the
user has written about a topic. Tools whose description starts with WRITES DATA or DELETES DATA
change the user's records; only use them when the user asked for that change."""

# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


def _describe_error(r: httpx.Response) -> str:
    try:
        detail: Any = r.json().get("detail", r.text)
    except ValueError:
        detail = r.text[:300]
    if isinstance(detail, list):  # FastAPI validation errors
        parts = []
        for err in detail:
            loc = [str(x) for x in err.get("loc", []) if x not in ("body", "query", "path")]
            parts.append(f"{'.'.join(loc) or 'input'}: {err.get('msg', 'invalid')}")
        detail = "; ".join(parts)
    code = r.status_code
    if code == 401:
        return "The Personal API rejected the API key. Check that API_KEY in the repo's .env matches the running server."
    if code == 404:
        return f"Not found: {detail}"
    if code == 409:
        return f"Conflict: {detail}"
    if code == 422:
        return f"Invalid input: {detail}"
    if code == 503:
        return f"Temporarily unavailable: {detail}"
    if code >= 500:
        return f"The Personal API hit an internal error (HTTP {code}). Details are in logs/api.log."
    return f"The Personal API returned HTTP {code}: {detail}"


class ApiClient:
    def __init__(self, base_url: str, api_key: str | None, *, timeout: float = 90.0, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        headers = {"X-API-Key": api_key} if api_key else {}
        self._client = httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=timeout, transport=transport)

    async def request(self, method: str, path: str, *, params: dict | None = None, json: dict | None = None) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None and v != []}
        try:
            r = await self._client.request(method, path, params=params, json=json)
        except httpx.ConnectError:
            raise ToolError(
                f"The Personal API is not reachable at {self.base_url}. Is it running? On Windows it is the "
                "Task Scheduler task \\Personal-API\\API server (or run: python -m app.serve)."
            ) from None
        except httpx.TimeoutException:
            raise ToolError(f"The Personal API at {self.base_url} did not answer in time. Try again in a moment.") from None
        except httpx.HTTPError as e:
            raise ToolError(f"Could not talk to the Personal API at {self.base_url} ({e.__class__.__name__}).") from None
        if r.status_code == 204:
            return None
        if r.is_success:
            return r.json()
        raise ToolError(_describe_error(r))


def _id(value: str, what: str) -> str:
    try:
        return str(uuid.UUID(value.strip()))
    except (ValueError, AttributeError):
        raise ToolError(f"'{value}' is not a valid {what} id (expected a UUID like 3f2b8c1e-...).") from None


def _changes(fields: dict[str, Any], clear: list[str] | None) -> dict[str, Any]:
    """PATCH body: only the fields that were given, plus explicit nulls for `clear`."""
    body = {k: v for k, v in fields.items() if v is not None}
    for name in clear or []:
        if name in body:
            raise ToolError(f"'{name}' was both set and cleared; pick one.")
        body[name] = None
    if not body:
        raise ToolError("Nothing to update: pass at least one field to change or a field name in `clear`.")
    return body


def _listing(rows: list) -> dict:
    return {"count": len(rows), "items": rows}


# Shared parameter types
Limit = Annotated[int, Field(ge=1, le=500, description="Max rows to return (default 100, max 500)")]
Offset = Annotated[int, Field(ge=0, description="Rows to skip, for paging")]
Tags = Annotated[list[str] | None, Field(description="Tags; normalized to lowercase_snake_case by the API")]
IsoDate = Annotated[str | None, Field(description="Date as YYYY-MM-DD")]
IsoDateTime = Annotated[str | None, Field(description="Date-time with timezone, e.g. 2026-10-01T09:00:00-06:00 or ...Z")]


def build_server(api: ApiClient) -> MCPServer:
    mcp = MCPServer("personal-api", title="Personal API", instructions=INSTRUCTIONS)

    # -----------------------------------------------------------------------
    # Knowledge and context
    # -----------------------------------------------------------------------

    @mcp.tool(annotations=READ)
    async def search_knowledge(
        q: Annotated[str, Field(min_length=1, max_length=1000, description="What to look for, in plain language")],
        limit: Annotated[int, Field(ge=1, le=50, description="Number of results (default 10, max 50)")] = 10,
        source: Annotated[str | None, Field(description="Only search one source, e.g. 'vault'")] = None,
    ) -> dict:
        """Read-only. Search the user's indexed notes and documents (their Obsidian vault) BY MEANING,
        not by exact words: finds passages about a topic even when phrased differently. Use this for
        questions like "what have I written about X", project notes, plans, prompts, research.
        Does NOT search pantry, schedule, habits, contacts or the notes table; use those tools instead.
        Returns ranked passages with a similarity score (higher is closer; ~0.7+ is a strong match),
        the file path, heading path and start/end line numbers so the source can be cited."""
        return await api.request("GET", "/knowledge/search", params={"q": q, "limit": limit, "source": source})

    @mcp.tool(annotations=READ)
    async def get_context(
        q: Annotated[str | None, Field(max_length=1000, description="Optional topic: adds the top matching vault notes")] = None,
        include: Annotated[
            list[Literal["pantry", "schedule", "habits", "knowledge"]] | None,
            Field(description="Sections to return in full detail. Omit for the lightweight summary only."),
        ] = None,
        days: Annotated[int, Field(ge=1, le=31, description="Look-ahead for schedule and expiring pantry items (default 7)")] = 7,
        knowledge_limit: Annotated[int, Field(ge=1, le=20, description="Vault notes to return for q (default 5)")] = 5,
        source: Annotated[str | None, Field(description="Only search notes in this source, e.g. 'vault'")] = None,
    ) -> dict:
        """Read-only. A fast situational snapshot of the user's day. The DEFAULT call is a lightweight
        baseline: summary counts and flags only (events today and upcoming, the next event, pantry
        totals, out-of-stock and expiring counts, active habits and check-ins today/this week).
        Pass `q` to also get the top vault notes about a topic. For full detail, pass `include`
        (e.g. ["schedule"] for the upcoming event list, ["pantry","schedule","habits"] for everything),
        or use the dedicated tools (list_schedule, list_pantry, list_habits) for complete lists."""
        return await api.request(
            "GET",
            "/me/context",
            params={
                "q": q,
                "include": ",".join(include) if include else None,
                "days": days,
                "knowledge_limit": knowledge_limit,
                "source": source,
            },
        )

    # -----------------------------------------------------------------------
    # Pantry
    # -----------------------------------------------------------------------

    @mcp.tool(annotations=READ)
    async def list_pantry(
        q: Annotated[str | None, Field(description="Name contains (case-insensitive)")] = None,
        location: Annotated[str | None, Field(description="Exact location, e.g. pantry, fridge, freezer")] = None,
        tag: Annotated[list[str] | None, Field(description="Item must have ALL of these dietary tags, e.g. ['gluten_free']")] = None,
        exclude_tag: Annotated[list[str] | None, Field(description="Item must have NONE of these tags, e.g. ['contains_pork']")] = None,
        expiring_within_days: Annotated[int | None, Field(ge=0, description="Expiry on or before today + N days (includes expired)")] = None,
        in_stock: Annotated[bool | None, Field(description="true: qty > 0, false: qty = 0")] = None,
        sort: Annotated[Literal["name", "expiry", "updated"], Field(description="Sort order")] = "name",
        limit: Limit = 100,
        offset: Offset = 0,
    ) -> dict:
        """Read-only. List or filter the user's pantry/fridge/freezer inventory. Dietary tags follow the
        household vocabulary: gluten_free, contains_gluten, may_contain_gluten, contains_pork, pork_free,
        dairy_free, vegetarian, vegan, nut_free. "Safe for everyone" = tag gluten_free + exclude_tag contains_pork."""
        params = {
            "q": q, "location": location, "tag": tag, "exclude_tag": exclude_tag,
            "expiring_within_days": expiring_within_days, "in_stock": in_stock, "sort": sort, "limit": limit, "offset": offset,
        }
        return _listing(await api.request("GET", "/pantry", params=params))

    @mcp.tool(annotations=READ)
    async def get_pantry_item(id: Annotated[str, Field(description="Pantry item id (UUID)")]) -> dict:
        """Read-only. Get one pantry item by id."""
        return await api.request("GET", f"/pantry/{_id(id, 'pantry item')}")

    @mcp.tool(annotations=CREATE)
    async def create_pantry_item(
        name: Annotated[str, Field(min_length=1, max_length=200)],
        qty: Annotated[float, Field(ge=0, description="Quantity (default 1)")] = 1,
        unit: Annotated[str | None, Field(description="e.g. g, ml, can, bag, each")] = None,
        location: Annotated[str | None, Field(description="e.g. pantry, fridge, freezer")] = None,
        expiry_estimate: IsoDate = None,
        dietary_tags: Tags = None,
        notes: str | None = None,
    ) -> dict:
        """WRITES DATA: adds a new item to the user's pantry inventory. Returns the created item with its id."""
        body = {"name": name, "qty": qty, "unit": unit, "location": location, "expiry_estimate": expiry_estimate,
                "dietary_tags": dietary_tags or [], "notes": notes}
        return await api.request("POST", "/pantry", json={k: v for k, v in body.items() if v is not None})

    @mcp.tool(annotations=UPDATE)
    async def update_pantry_item(
        id: Annotated[str, Field(description="Pantry item id (UUID)")],
        name: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
        qty: Annotated[float | None, Field(ge=0, description="New absolute quantity (use adjust_pantry_quantity for +/-)")] = None,
        unit: str | None = None,
        location: str | None = None,
        expiry_estimate: IsoDate = None,
        dietary_tags: Annotated[list[str] | None, Field(description="Replaces the whole tag list")] = None,
        notes: str | None = None,
        clear: Annotated[
            list[Literal["unit", "location", "expiry_estimate", "notes"]] | None,
            Field(description="Fields to erase (set to empty)"),
        ] = None,
    ) -> dict:
        """WRITES DATA: changes an existing pantry item. Only the fields you pass are changed; list
        fields in `clear` to erase them. Returns the updated item."""
        body = _changes({"name": name, "qty": qty, "unit": unit, "location": location, "expiry_estimate": expiry_estimate,
                         "dietary_tags": dietary_tags, "notes": notes}, clear)
        return await api.request("PATCH", f"/pantry/{_id(id, 'pantry item')}", json=body)

    @mcp.tool(annotations=ADJUST)
    async def adjust_pantry_quantity(
        id: Annotated[str, Field(description="Pantry item id (UUID)")],
        delta: Annotated[float, Field(description="Amount to add (positive) or use up (negative), e.g. -2 for 'used 2 eggs'")],
    ) -> dict:
        """WRITES DATA: adds to or subtracts from a pantry item's quantity (clamped at 0; the item is kept).
        Use this for "used 2", "bought 3 more". Returns the updated item."""
        return await api.request("POST", f"/pantry/{_id(id, 'pantry item')}/adjust", json={"delta": delta})

    @mcp.tool(annotations=DELETE)
    async def delete_pantry_item(id: Annotated[str, Field(description="Pantry item id (UUID)")]) -> dict:
        """DELETES DATA PERMANENTLY: removes a pantry item. To record it as used up, prefer
        update_pantry_item with qty=0 or adjust_pantry_quantity."""
        await api.request("DELETE", f"/pantry/{_id(id, 'pantry item')}")
        return {"deleted": id}

    # -----------------------------------------------------------------------
    # Schedule
    # -----------------------------------------------------------------------

    @mcp.tool(annotations=READ)
    async def list_schedule(
        from_time: Annotated[
            str | None, Field(description="Only events still running at or after this time (ISO with timezone)")
        ] = None,
        to_time: Annotated[str | None, Field(description="Only events starting at or before this time (ISO with timezone)")] = None,
        category: Annotated[str | None, Field(description="e.g. work, family, health")] = None,
        limit: Limit = 100,
        offset: Offset = 0,
    ) -> dict:
        """Read-only. List the user's calendar/schedule events sorted by start time, optionally within a
        time window. Recurring events are returned once (their RRULE is not expanded)."""
        params = {"from": from_time, "to": to_time, "category": category, "limit": limit, "offset": offset}
        return _listing(await api.request("GET", "/schedule", params=params))

    @mcp.tool(annotations=READ)
    async def get_schedule_event(id: Annotated[str, Field(description="Event id (UUID)")]) -> dict:
        """Read-only. Get one schedule event by id."""
        return await api.request("GET", f"/schedule/{_id(id, 'event')}")

    @mcp.tool(annotations=CREATE)
    async def create_schedule_event(
        title: Annotated[str, Field(min_length=1, max_length=200)],
        starts_at: Annotated[str, Field(description="Start with timezone, e.g. 2026-10-01T09:00:00-06:00")],
        ends_at: IsoDateTime = None,
        all_day: bool = False,
        location: str | None = None,
        category: Annotated[str | None, Field(description="e.g. work, family, health")] = None,
        recurrence_rule: Annotated[str | None, Field(description="RFC 5545 RRULE, e.g. FREQ=WEEKLY;BYDAY=MO")] = None,
        notes: str | None = None,
    ) -> dict:
        """WRITES DATA: adds an event to the user's schedule. Times must include a timezone.
        Returns the created event with its id."""
        body = {"title": title, "starts_at": starts_at, "ends_at": ends_at, "all_day": all_day, "location": location,
                "category": category, "recurrence_rule": recurrence_rule, "notes": notes, "source": "mcp"}
        return await api.request("POST", "/schedule", json={k: v for k, v in body.items() if v is not None})

    @mcp.tool(annotations=UPDATE)
    async def update_schedule_event(
        id: Annotated[str, Field(description="Event id (UUID)")],
        title: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
        starts_at: IsoDateTime = None,
        ends_at: IsoDateTime = None,
        all_day: bool | None = None,
        location: str | None = None,
        category: str | None = None,
        recurrence_rule: str | None = None,
        notes: str | None = None,
        clear: Annotated[
            list[Literal["ends_at", "location", "category", "recurrence_rule", "notes"]] | None,
            Field(description="Fields to erase (set to empty)"),
        ] = None,
    ) -> dict:
        """WRITES DATA: changes an existing schedule event (e.g. reschedule, rename). Only the fields you
        pass are changed; list fields in `clear` to erase them. Returns the updated event."""
        body = _changes({"title": title, "starts_at": starts_at, "ends_at": ends_at, "all_day": all_day,
                         "location": location, "category": category, "recurrence_rule": recurrence_rule, "notes": notes}, clear)
        return await api.request("PATCH", f"/schedule/{_id(id, 'event')}", json=body)

    @mcp.tool(annotations=DELETE)
    async def delete_schedule_event(id: Annotated[str, Field(description="Event id (UUID)")]) -> dict:
        """DELETES DATA PERMANENTLY: removes an event from the user's schedule."""
        await api.request("DELETE", f"/schedule/{_id(id, 'event')}")
        return {"deleted": id}

    # -----------------------------------------------------------------------
    # Habits
    # -----------------------------------------------------------------------

    @mcp.tool(annotations=READ)
    async def list_habits(
        active: Annotated[bool | None, Field(description="true: only active habits, false: only retired ones")] = None,
        limit: Limit = 100,
        offset: Offset = 0,
    ) -> dict:
        """Read-only. List the habits the user tracks (definitions: name, weekly target, unit, active).
        For this week's progress, use get_context with include=["habits"]."""
        return _listing(await api.request("GET", "/habits", params={"active": active, "limit": limit, "offset": offset}))

    @mcp.tool(annotations=READ)
    async def get_habit(id: Annotated[str, Field(description="Habit id (UUID)")]) -> dict:
        """Read-only. Get one habit definition by id."""
        return await api.request("GET", f"/habits/{_id(id, 'habit')}")

    @mcp.tool(annotations=CREATE)
    async def create_habit(
        name: Annotated[str, Field(min_length=1, max_length=200, description="Unique habit name")],
        description: str | None = None,
        target_per_week: Annotated[int | None, Field(ge=1, le=7, description="Times per week, 1-7")] = None,
        unit: Annotated[str | None, Field(description="For measured habits, e.g. min, pages")] = None,
        active: bool = True,
    ) -> dict:
        """WRITES DATA: starts tracking a new habit. Names are unique. Returns the created habit with its id."""
        body = {"name": name, "description": description, "target_per_week": target_per_week, "unit": unit, "active": active}
        return await api.request("POST", "/habits", json={k: v for k, v in body.items() if v is not None})

    @mcp.tool(annotations=UPDATE)
    async def update_habit(
        id: Annotated[str, Field(description="Habit id (UUID)")],
        name: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
        description: str | None = None,
        target_per_week: Annotated[int | None, Field(ge=1, le=7)] = None,
        unit: str | None = None,
        active: Annotated[bool | None, Field(description="false retires the habit without deleting its history")] = None,
        clear: Annotated[
            list[Literal["description", "target_per_week", "unit"]] | None,
            Field(description="Fields to erase (set to empty)"),
        ] = None,
    ) -> dict:
        """WRITES DATA: changes a habit definition (rename, new target, retire with active=false).
        Only the fields you pass are changed. Returns the updated habit."""
        body = _changes({"name": name, "description": description, "target_per_week": target_per_week,
                         "unit": unit, "active": active}, clear)
        return await api.request("PATCH", f"/habits/{_id(id, 'habit')}", json=body)

    @mcp.tool(annotations=DELETE)
    async def delete_habit(id: Annotated[str, Field(description="Habit id (UUID)")]) -> dict:
        """DELETES DATA PERMANENTLY: removes a habit AND all of its check-in history. To stop tracking
        but keep history, prefer update_habit with active=false."""
        await api.request("DELETE", f"/habits/{_id(id, 'habit')}")
        return {"deleted": id}

    # -----------------------------------------------------------------------
    # Contacts
    # -----------------------------------------------------------------------

    @mcp.tool(annotations=READ)
    async def list_contacts(
        q: Annotated[str | None, Field(description="Name contains (case-insensitive)")] = None,
        tag: Annotated[list[str] | None, Field(description="Contact must have ALL of these tags")] = None,
        limit: Limit = 100,
        offset: Offset = 0,
    ) -> dict:
        """Read-only. List or find people in the user's contacts (family, friends, work), including
        relationship, email, phone, birthday, dietary needs for guests, tags and linked vault note."""
        return _listing(await api.request("GET", "/contacts", params={"q": q, "tag": tag, "limit": limit, "offset": offset}))

    @mcp.tool(annotations=READ)
    async def get_contact(id: Annotated[str, Field(description="Contact id (UUID)")]) -> dict:
        """Read-only. Get one contact by id."""
        return await api.request("GET", f"/contacts/{_id(id, 'contact')}")

    @mcp.tool(annotations=CREATE)
    async def create_contact(
        name: Annotated[str, Field(min_length=1, max_length=200)],
        relationship: Annotated[str | None, Field(description="e.g. family, friend, work")] = None,
        email: str | None = None,
        phone: str | None = None,
        birthday: IsoDate = None,
        dietary_tags: Tags = None,
        tags: Tags = None,
        notes: str | None = None,
        vault_path: Annotated[str | None, Field(description="Linked vault note, e.g. people/jane-doe.md")] = None,
    ) -> dict:
        """WRITES DATA: adds a person to the user's contacts. Returns the created contact with its id."""
        body = {"name": name, "relationship": relationship, "email": email, "phone": phone, "birthday": birthday,
                "dietary_tags": dietary_tags or [], "tags": tags or [], "notes": notes, "vault_path": vault_path}
        return await api.request("POST", "/contacts", json={k: v for k, v in body.items() if v is not None})

    @mcp.tool(annotations=UPDATE)
    async def update_contact(
        id: Annotated[str, Field(description="Contact id (UUID)")],
        name: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
        relationship: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        birthday: IsoDate = None,
        dietary_tags: Annotated[list[str] | None, Field(description="Replaces the whole list")] = None,
        tags: Annotated[list[str] | None, Field(description="Replaces the whole list")] = None,
        notes: str | None = None,
        vault_path: str | None = None,
        clear: Annotated[
            list[Literal["relationship", "email", "phone", "birthday", "notes", "vault_path"]] | None,
            Field(description="Fields to erase (set to empty)"),
        ] = None,
    ) -> dict:
        """WRITES DATA: changes an existing contact. Only the fields you pass are changed; list fields in
        `clear` to erase them. Returns the updated contact."""
        body = _changes({"name": name, "relationship": relationship, "email": email, "phone": phone, "birthday": birthday,
                         "dietary_tags": dietary_tags, "tags": tags, "notes": notes, "vault_path": vault_path}, clear)
        return await api.request("PATCH", f"/contacts/{_id(id, 'contact')}", json=body)

    @mcp.tool(annotations=DELETE)
    async def delete_contact(id: Annotated[str, Field(description="Contact id (UUID)")]) -> dict:
        """DELETES DATA PERMANENTLY: removes a contact."""
        await api.request("DELETE", f"/contacts/{_id(id, 'contact')}")
        return {"deleted": id}

    # -----------------------------------------------------------------------
    # Notes (the API's notes table, not the vault)
    # -----------------------------------------------------------------------

    @mcp.tool(annotations=READ)
    async def list_notes(
        q: Annotated[str | None, Field(description="Title or body contains this text (case-insensitive, exact words)")] = None,
        tag: Annotated[list[str] | None, Field(description="Note must have ALL of these tags")] = None,
        source: Annotated[str | None, Field(description="Where the note came from, e.g. chat, hermes, mcp")] = None,
        limit: Limit = 100,
        offset: Offset = 0,
    ) -> dict:
        """Read-only. List short notes saved through the API (quick facts, reminders, things to remember),
        newest first. These are separate from the Obsidian vault; to search the vault by meaning use
        search_knowledge."""
        return _listing(await api.request("GET", "/notes", params={"q": q, "tag": tag, "source": source, "limit": limit, "offset": offset}))

    @mcp.tool(annotations=READ)
    async def get_note(id: Annotated[str, Field(description="Note id (UUID)")]) -> dict:
        """Read-only. Get one saved note by id."""
        return await api.request("GET", f"/notes/{_id(id, 'note')}")

    @mcp.tool(annotations=CREATE)
    async def create_note(
        body: Annotated[str, Field(min_length=1, description="The note text")],
        title: Annotated[str | None, Field(max_length=200)] = None,
        tags: Tags = None,
    ) -> dict:
        """WRITES DATA: saves a new note for the user (a fact, reminder or anything worth remembering).
        Stored with source "mcp". Returns the created note with its id."""
        payload = {"body": body, "title": title, "tags": tags or [], "source": "mcp"}
        return await api.request("POST", "/notes", json={k: v for k, v in payload.items() if v is not None})

    @mcp.tool(annotations=UPDATE)
    async def update_note(
        id: Annotated[str, Field(description="Note id (UUID)")],
        body: Annotated[str | None, Field(min_length=1)] = None,
        title: Annotated[str | None, Field(max_length=200)] = None,
        tags: Annotated[list[str] | None, Field(description="Replaces the whole tag list")] = None,
        clear: Annotated[list[Literal["title"]] | None, Field(description="Fields to erase (set to empty)")] = None,
    ) -> dict:
        """WRITES DATA: changes an existing saved note. Only the fields you pass are changed. Returns the updated note."""
        changes = _changes({"body": body, "title": title, "tags": tags}, clear)
        return await api.request("PATCH", f"/notes/{_id(id, 'note')}", json=changes)

    @mcp.tool(annotations=DELETE)
    async def delete_note(id: Annotated[str, Field(description="Note id (UUID)")]) -> dict:
        """DELETES DATA PERMANENTLY: removes a saved note."""
        await api.request("DELETE", f"/notes/{_id(id, 'note')}")
        return {"deleted": id}

    return mcp


def main() -> None:
    # stdout carries the MCP protocol: all logging must go to stderr.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Clients like Claude Desktop don't set a working directory, so read .env by absolute path.
    settings = Settings(_env_file=REPO_ROOT / ".env")
    if not settings.api_key:
        log.warning("API_KEY is not set in %s; requests will fail if the API requires a key", REPO_ROOT / ".env")
    log.info("Personal API MCP server starting (API at %s)", settings.api_url)
    build_server(ApiClient(settings.api_url, settings.api_key)).run("stdio")


if __name__ == "__main__":
    main()
