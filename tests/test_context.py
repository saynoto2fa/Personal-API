from datetime import UTC, date, datetime, timedelta

from app.db import SessionLocal
from app.models.habits import HabitCheckin
from tests.test_knowledge import fake_embedder, indexed  # noqa: F401  (shared fixtures)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_context_validation(api_client, fake_embedder):  # noqa: F811
    assert api_client.get("/me/context", params={"q": "   "}).status_code == 422
    assert api_client.get("/me/context", params={"days": 0}).status_code == 422
    assert api_client.get("/me/context", params={"days": 32}).status_code == 422
    assert api_client.get("/me/context", params={"knowledge_limit": 21}).status_code == 422
    assert fake_embedder.texts == []


def test_context_snapshot(client, fake_embedder):  # noqa: F811
    now = datetime.now(UTC)
    today = date.today()

    def event(title, start, **kw):
        r = client.post("/schedule", json={"title": title, "starts_at": _iso(start), **kw})
        assert r.status_code == 201, r.text

    event("Finished", now - timedelta(hours=3), ends_at=_iso(now - timedelta(hours=2)))
    event("Started, no end", now - timedelta(hours=2))  # a point in time that has passed
    event("Ongoing", now - timedelta(hours=1), ends_at=_iso(now + timedelta(hours=1)))
    event("All day today", now - timedelta(hours=2), all_day=True)
    event("Tomorrow", now + timedelta(days=1))
    event("Next month", now + timedelta(days=30))

    def item(name, qty, expiry=None):
        body = {"name": name, "qty": qty}
        if expiry is not None:
            body["expiry_estimate"] = str(today + timedelta(days=expiry))
        assert client.post("/pantry", json=body).status_code == 201

    item("Milk", 1, expiry=2)
    item("Yogurt", 1, expiry=-1)  # already expired, still in stock: worth knowing
    item("Old cheese", 0, expiry=-3)  # used up: not "expiring"
    item("Rice", 2, expiry=200)
    item("Salt", 1)

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

    r = client.get("/me/context")
    assert r.status_code == 200, r.text
    ctx = r.json()

    assert ctx["today"] == str(today)
    assert ctx["days"] == 7 and ctx["q"] is None
    # Sorted by start: the all-day event began 2h ago, "Ongoing" 1h ago.
    assert [e["title"] for e in ctx["schedule"]] == ["All day today", "Ongoing", "Tomorrow"]

    assert ctx["pantry"]["total_items"] == 5
    assert ctx["pantry"]["out_of_stock"] == 1
    assert [i["name"] for i in ctx["pantry"]["expiring"]] == ["Yogurt", "Milk"]

    habits = {h["name"]: h for h in ctx["habits"]}
    assert set(habits) == {"Read", "Walk"}
    assert habits["Read"]["done_this_week"] == (1 if monday == today else 2)
    assert habits["Read"]["done_today"] is True
    assert habits["Read"]["target_per_week"] == 5
    assert habits["Walk"] == {**habits["Walk"], "done_this_week": 0, "done_today": False}

    assert ctx["knowledge"] == [] and ctx["warnings"] == []
    assert fake_embedder.texts == []  # no topic, no search

    wider = client.get("/me/context", params={"days": 31}).json()
    assert [e["title"] for e in wider["schedule"]][-1] == "Next month"


def test_context_knowledge_section_uses_topic(client, indexed, fake_embedder):  # noqa: F811
    r = client.get("/me/context", params={"q": "pasta recipe", "source": indexed.name, "knowledge_limit": 2})
    assert r.status_code == 200, r.text
    ctx = r.json()
    assert ctx["q"] == "pasta recipe"
    assert len(ctx["knowledge"]) == 2
    assert ctx["knowledge"][0]["path"] == "food/pasta.md"
    assert fake_embedder.texts == ["search_query: pasta recipe"]


def test_context_still_answers_when_ollama_is_down(client, fake_embedder):  # noqa: F811
    fake_embedder.down = True
    r = client.get("/me/context", params={"q": "anything"})
    assert r.status_code == 200
    ctx = r.json()
    assert ctx["knowledge"] == []
    assert len(ctx["warnings"]) == 1 and ctx["warnings"][0].startswith("knowledge: search unavailable")
