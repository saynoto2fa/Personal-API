"""MCP tools against a fake HTTP API (no database, server or Ollama needed)."""

import asyncio
import json

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from app.mcp_server import ApiClient, build_server

ITEM_ID = "3f2b8c1e-0000-4000-8000-000000000001"
EXPECTED_TOOLS = {
    "search_knowledge", "get_context",
    "list_pantry", "get_pantry_item", "create_pantry_item", "update_pantry_item", "adjust_pantry_quantity", "delete_pantry_item",
    "list_schedule", "get_schedule_event", "create_schedule_event", "update_schedule_event", "delete_schedule_event",
    "list_habits", "get_habit", "create_habit", "update_habit", "delete_habit",
    "list_habit_checkins", "create_habit_checkin", "delete_habit_checkin",
    "list_contacts", "get_contact", "create_contact", "update_contact", "delete_contact",
    "list_notes", "get_note", "create_note", "update_note", "delete_note",
}  # fmt: skip


class FakeApi:
    """Records requests and answers with canned responses."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.response = httpx.Response(200, json={"ok": True})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


@pytest.fixture
def api():
    return FakeApi()


@pytest.fixture
def server(api):
    return build_server(ApiClient("http://api.test", "s3cret", transport=httpx.MockTransport(api)))


def call(server, name, args=None):
    result = asyncio.run(server.call_tool(name, args or {}))
    assert not result.is_error, result
    return json.loads(result.content[0].text)


def call_error(server, name, args=None) -> str:
    with pytest.raises(ToolError) as e:
        asyncio.run(server.call_tool(name, args or {}))
    return str(e.value)


def test_one_tool_per_endpoint_with_clear_read_write_marking(server):
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert set(tools) == EXPECTED_TOOLS
    for name, tool in tools.items():
        desc, ann = tool.description, tool.annotations
        if name.startswith(("search_", "get_", "list_")):
            assert desc.startswith("Read-only."), name
            assert ann.read_only_hint is True, name
        elif name.startswith("delete_"):
            assert desc.startswith("DELETES DATA PERMANENTLY"), name
            assert ann.read_only_hint is False and ann.destructive_hint is True, name
        else:
            assert desc.startswith("WRITES DATA"), name
            assert ann.read_only_hint is False, name
    assert "by meaning" in tools["search_knowledge"].description.lower()
    assert "lightweight" in tools["get_context"].description


def test_api_key_is_sent_by_the_server_not_the_client(server, api):
    call(server, "search_knowledge", {"q": "tomatoes"})
    assert api.last.headers["X-API-Key"] == "s3cret"
    for tool in asyncio.run(server.list_tools()):
        schema = json.dumps(tool.input_schema if hasattr(tool, "input_schema") else tool.inputSchema).lower()
        assert "api_key" not in schema and "x-api-key" not in schema


def test_search_and_context_map_to_endpoints(server, api):
    call(server, "search_knowledge", {"q": "windows auditing", "limit": 3, "source": "vault"})
    assert api.last.method == "GET" and api.last.url.path == "/knowledge/search"
    assert dict(api.last.url.params) == {"q": "windows auditing", "limit": "3", "source": "vault"}

    call(server, "get_context", {})
    assert api.last.url.path == "/me/context"
    assert "include" not in api.last.url.params and "q" not in api.last.url.params

    call(server, "get_context", {"q": "tutor", "include": ["schedule", "habits"], "days": 3})
    assert api.last.url.params["include"] == "schedule,habits"
    assert api.last.url.params["q"] == "tutor" and api.last.url.params["days"] == "3"


def test_lists_are_wrapped_and_repeated_params_kept(server, api):
    api.response = httpx.Response(200, json=[{"name": "Rice"}, {"name": "Salt"}])
    out = call(server, "list_pantry", {"tag": ["gluten_free", "pork_free"], "exclude_tag": ["contains_pork"], "in_stock": True})
    assert out == {"count": 2, "items": [{"name": "Rice"}, {"name": "Salt"}]}
    assert api.last.url.params.get_list("tag") == ["gluten_free", "pork_free"]
    assert api.last.url.params["in_stock"] == "true"

    call(server, "list_schedule", {"from_time": "2026-10-01T00:00:00Z", "to_time": "2026-10-08T00:00:00Z"})
    assert api.last.url.params["from"] == "2026-10-01T00:00:00Z" and api.last.url.params["to"] == "2026-10-08T00:00:00Z"


def test_create_sends_only_given_fields(server, api):
    call(server, "create_note", {"body": "Call the plumber", "tags": ["home"]})
    assert api.last.method == "POST" and api.last.url.path == "/notes"
    assert json.loads(api.last.content) == {"body": "Call the plumber", "tags": ["home"], "source": "mcp"}


def test_update_is_partial_and_clear_sends_nulls(server, api):
    call(server, "update_pantry_item", {"id": ITEM_ID, "qty": 0.5, "clear": ["notes"]})
    assert api.last.method == "PATCH" and api.last.url.path == f"/pantry/{ITEM_ID}"
    assert json.loads(api.last.content) == {"qty": 0.5, "notes": None}

    sent = len(api.requests)
    assert "Nothing to update" in call_error(server, "update_contact", {"id": ITEM_ID})
    assert "both set and cleared" in call_error(server, "update_schedule_event", {"id": ITEM_ID, "location": "x", "clear": ["location"]})
    assert len(api.requests) == sent  # rejected before calling the API


def test_delete_and_adjust(server, api):
    api.response = httpx.Response(204)
    assert call(server, "delete_habit", {"id": ITEM_ID}) == {"deleted": ITEM_ID}
    assert api.last.method == "DELETE" and api.last.url.path == f"/habits/{ITEM_ID}"

    api.response = httpx.Response(200, json={"qty": 4})
    call(server, "adjust_pantry_quantity", {"id": ITEM_ID, "delta": -2})
    assert api.last.url.path == f"/pantry/{ITEM_ID}/adjust" and json.loads(api.last.content) == {"delta": -2}


def test_habit_checkin_tools(server, api):
    other_id = "3f2b8c1e-0000-4000-8000-000000000002"
    api.response = httpx.Response(201, json={"id": other_id})
    call(server, "create_habit_checkin", {"habit_id": ITEM_ID, "value": 20})
    assert api.last.method == "POST" and api.last.url.path == f"/habits/{ITEM_ID}/checkins"
    assert json.loads(api.last.content) == {"done": True, "value": 20}  # no date: the API uses today

    api.response = httpx.Response(200, json=[{"checkin_date": "2026-10-02"}])
    out = call(server, "list_habit_checkins", {"habit_id": ITEM_ID, "from_date": "2026-10-01", "to_date": "2026-10-07"})
    assert out["count"] == 1
    assert dict(api.last.url.params) == {"from": "2026-10-01", "to": "2026-10-07", "limit": "100", "offset": "0"}

    api.response = httpx.Response(204)
    assert call(server, "delete_habit_checkin", {"habit_id": ITEM_ID, "checkin_id": other_id}) == {"deleted": other_id}
    assert api.last.method == "DELETE" and api.last.url.path == f"/habits/{ITEM_ID}/checkins/{other_id}"

    api.response = httpx.Response(409, json={"detail": "This habit already has a check-in for 2026-10-02"})
    assert "Conflict: This habit already has a check-in" in call_error(server, "create_habit_checkin", {"habit_id": ITEM_ID})


def test_bad_ids_never_reach_the_api(server, api):
    msg = call_error(server, "get_note", {"id": "../../admin"})
    assert "not a valid note id" in msg
    assert api.requests == []


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(401, json={"detail": "Invalid or missing X-API-Key"}), "rejected the API key"),
        (httpx.Response(404, json={"detail": "Note not found"}), "Not found: Note not found"),
        (httpx.Response(409, json={"detail": "A habit with this name already exists"}), "Conflict: A habit"),
        (
            httpx.Response(422, json={"detail": [{"loc": ["body", "starts_at"], "msg": "Input should have timezone info"}]}),
            "Invalid input: starts_at: Input should have timezone info",
        ),
        (httpx.Response(503, json={"detail": "Knowledge search is unavailable: Ollama down"}), "Temporarily unavailable: Knowledge"),
        (httpx.Response(500, text="Internal Server Error\nTraceback (most recent call last): ..."), "internal error (HTTP 500)"),
    ],
)
def test_api_errors_become_readable_tool_errors(server, api, response, expected):
    api.response = response
    msg = call_error(server, "search_knowledge", {"q": "x"})
    assert expected in msg
    assert "Traceback" not in msg


def test_unreachable_api_gives_a_clear_message():
    def refuse(request):
        raise httpx.ConnectError("connection refused")

    server = build_server(ApiClient("http://127.0.0.1:9", "k", transport=httpx.MockTransport(refuse)))
    msg = call_error(server, "get_context")
    assert "not reachable" in msg and "API server" in msg
