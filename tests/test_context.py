from datetime import UTC, date, datetime, timedelta

from app.db import SessionLocal
from app.models.habits import HabitCheckin
from tests.test_knowledge import fake_embedder, indexed  # noqa: F401  (shared fixtures)

DETAIL = "pantry,schedule,habits"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _seed(client) -> dict:
    """Events, pantry items and habits around 'now'. Returns what the summary should say."""
    now = datetime.now(UTC)
    today = date.today()
    events = [
        ("Finished", now - timedelta(hours=3), now - timedelta(hours=2), False),
        ("Started, no end", now - timedelta(hours=2), None, False),  # a point in time that has passed
        ("Ongoing", now - timedelta(hours=1), now + timedelta(hours=1), False),
        ("All day today", now - timedelta(hours=2), None, True),
        ("Tomorrow", now + timedelta(days=1), None, False),
        ("Next month", now + timedelta(days=30), None, False),
    ]
    for title, start, end, all_day in events:
        body = {"title": title, "starts_at": _iso(start), "all_day": all_day}
        if end:
            body["ends_at"] = _iso(end)
        assert client.post("/schedule", json=body).status_code == 201

    # Same rule as the endpoint: an event overlaps today (server-local) if it hasn't ended by
    # local midnight and starts before the next one. Depends on the time of day, so compute it.
    midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = sum(
        1
        for _, start, end, all_day in events
        if (end or (start + timedelta(days=1) if all_day else start)) >= midnight and start < midnight + timedelta(days=1)
    )

    for name, qty, expiry in [("Milk", 1, 2), ("Yogurt", 1, -1), ("Old cheese", 0, -3), ("Rice", 2, 200), ("Salt", 1, None)]:
        body = {"name": name, "qty": qty}
        if expiry is not None:
            body["expiry_estimate"] = str(today + timedelta(days=expiry))
        assert client.post("/pantry", json=body).status_code == 201

    read = client.post("/habits", json={"name": "Read", "target_per_week": 5}).json()
    walk = client.post("/habits", json={"name": "Walk"}).json()
    client.post("/habits", json={"name": "Retired", "active": False})
    monday = today - timedelta(days=today.weekday())
    with SessionLocal() as s, s.begin():
        s.add(HabitCheckin(habit_id=read["id"], checkin_date=today, done=True))
        s.add(HabitCheckin(habit_id=read["id"], checkin_date=monday - timedelta(days=1), done=True))  # last week
        if monday != today:
            s.add(HabitCheckin(habit_id=read["id"], checkin_date=monday, done=True))
        s.add(HabitCheckin(habit_id=walk["id"], checkin_date=today, done=False))

    return {
        "schedule_count_today": today_count,
        "schedule_count_upcoming": 3,  # Ongoing, All day today, Tomorrow
        "pantry_total": 5,
        "pantry_out_of_stock": 1,
        "pantry_expiring_count": 2,  # Yogurt (expired, in stock) and Milk
        "habits_active": 2,
        "habits_done_today": 1,
        "habits_done_this_week": 1 if monday == today else 2,
    }


def test_context_validation(api_client, fake_embedder):  # noqa: F811
    assert api_client.get("/me/context", params={"q": "   "}).status_code == 422
    assert api_client.get("/me/context", params={"days": 0}).status_code == 422
    assert api_client.get("/me/context", params={"days": 32}).status_code == 422
    assert api_client.get("/me/context", params={"knowledge_limit": 21}).status_code == 422
    r = api_client.get("/me/context", params={"include": "pantry,weather"})
    assert r.status_code == 422 and "weather" in r.json()["detail"]
    r = api_client.get("/me/context", params={"include": "knowledge"})
    assert r.status_code == 422 and "needs q" in r.json()["detail"]
    assert fake_embedder.texts == []


def test_default_is_a_lightweight_summary(client, fake_embedder):  # noqa: F811
    expected = _seed(client)
    r = client.get("/me/context")
    assert r.status_code == 200, r.text
    ctx = r.json()
    assert ctx["include"] == []
    assert ctx["schedule"] is None and ctx["pantry"] is None and ctx["habits"] is None and ctx["knowledge"] is None
    summary = ctx["summary"]
    assert summary["next_event"]["title"] == "All day today"
    assert {k: summary[k] for k in expected} == expected
    assert fake_embedder.texts == []


def test_include_returns_full_detail_for_requested_sections(client, fake_embedder):  # noqa: F811
    _seed(client)
    today = date.today()

    r = client.get("/me/context", params={"include": " Schedule , pantry,habits,pantry"})
    assert r.status_code == 200, r.text
    ctx = r.json()
    assert ctx["include"] == ["pantry", "schedule", "habits"]
    # Sorted by start: the all-day event began 2h ago, "Ongoing" 1h ago.
    assert [e["title"] for e in ctx["schedule"]] == ["All day today", "Ongoing", "Tomorrow"]
    assert ctx["pantry"]["total_items"] == 5 and ctx["pantry"]["out_of_stock"] == 1
    assert [i["name"] for i in ctx["pantry"]["expiring"]] == ["Yogurt", "Milk"]
    habits = {h["name"]: h for h in ctx["habits"]}
    assert set(habits) == {"Read", "Walk"}
    assert habits["Read"]["done_today"] is True and habits["Read"]["target_per_week"] == 5
    monday = today - timedelta(days=today.weekday())
    assert habits["Read"]["done_this_week"] == (1 if monday == today else 2)
    assert habits["Walk"]["done_this_week"] == 0 and habits["Walk"]["done_today"] is False
    assert ctx["knowledge"] is None and ctx["warnings"] == []

    only = client.get("/me/context", params={"include": "pantry"}).json()
    assert only["pantry"] is not None and only["schedule"] is None and only["habits"] is None

    wider = client.get("/me/context", params={"include": "schedule", "days": 31}).json()
    assert [e["title"] for e in wider["schedule"]][-1] == "Next month"
    assert wider["summary"]["schedule_count_upcoming"] == 4


def test_context_knowledge_section_uses_topic(client, indexed, fake_embedder):  # noqa: F811
    r = client.get("/me/context", params={"q": "pasta recipe", "source": indexed.name, "knowledge_limit": 2})
    assert r.status_code == 200, r.text
    ctx = r.json()
    assert ctx["q"] == "pasta recipe"
    assert ctx["include"] == ["knowledge"]
    assert ctx["pantry"] is None  # a topic alone doesn't pull in the other sections
    assert len(ctx["knowledge"]) == 2
    assert ctx["knowledge"][0]["path"] == "food/pasta.md"
    assert fake_embedder.texts == ["search_query: pasta recipe"]

    both = client.get("/me/context", params={"q": "pasta", "include": "knowledge,habits", "source": indexed.name}).json()
    assert both["include"] == ["habits", "knowledge"] and both["habits"] == [] and both["knowledge"]


def test_context_still_answers_when_ollama_is_down(client, fake_embedder):  # noqa: F811
    fake_embedder.down = True
    r = client.get("/me/context", params={"q": "anything"})
    assert r.status_code == 200
    ctx = r.json()
    assert ctx["knowledge"] == []  # hybrid fell back to keyword-only; nothing indexed here
    assert len(ctx["warnings"]) == 1 and ctx["warnings"][0].startswith("knowledge: semantic search unavailable")
    assert ctx["summary"]["pantry_total"] == 0


def test_topic_filters_scope_detail_sections_but_not_the_summary(client, fake_embedder):  # noqa: F811
    now = datetime.now(UTC)
    today = date.today()
    for title, category in [("Standup", "Work"), ("Dentist", "health"), ("Review", "work"), ("Dinner", None)]:
        body = {"title": title, "starts_at": _iso(now + timedelta(hours=len(title))), "category": category}
        assert client.post("/schedule", json={k: v for k, v in body.items() if v}).status_code == 201
    soon = str(today + timedelta(days=2))
    for name, tags, location in [
        ("GF bread", ["gluten_free", "pork_free"], "freezer"),
        ("Bacon", ["gluten_free", "contains_pork"], "fridge"),
        ("Pasta", ["contains_gluten"], "pantry"),
        ("Yogurt", ["gluten_free"], "fridge"),
    ]:
        item = {"name": name, "dietary_tags": tags, "location": location, "expiry_estimate": soon}
        assert client.post("/pantry", json=item).status_code == 201

    r = client.get("/me/context", params={"schedule_category": "WORK"})
    assert r.status_code == 200, r.text
    ctx = r.json()
    assert ctx["include"] == ["schedule"]  # the filter brings its section
    assert ctx["filters"] == {"schedule_category": "WORK"}
    assert [e["title"] for e in ctx["schedule"]] == ["Review", "Standup"]  # sorted by start time
    assert ctx["pantry"] is None
    assert ctx["summary"]["schedule_count_upcoming"] == 4  # the summary is never filtered

    # "Safe for everyone": gluten-free and no pork, same semantics as GET /pantry?tag=&exclude_tag=
    ctx = client.get("/me/context", params={"pantry_tag": "Gluten-Free", "pantry_exclude_tag": "contains_pork"}).json()
    assert ctx["include"] == ["pantry"]
    assert ctx["filters"] == {"pantry_tag": ["gluten_free"], "pantry_exclude_tag": ["contains_pork"]}
    assert [i["name"] for i in ctx["pantry"]["expiring"]] == ["GF bread", "Yogurt"]
    assert ctx["pantry"]["total_items"] == 2
    assert ctx["summary"]["pantry_total"] == 4

    ctx = client.get("/me/context", params={"pantry_location": "Fridge", "include": "habits"}).json()
    assert ctx["include"] == ["pantry", "habits"]
    assert [i["name"] for i in ctx["pantry"]["expiring"]] == ["Bacon", "Yogurt"]

    plain = client.get("/me/context").json()  # the lean default is unchanged
    assert plain["include"] == [] and plain["filters"] == {} and plain["schedule"] is None and plain["pantry"] is None
