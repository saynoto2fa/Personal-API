MISSING = "00000000-0000-0000-0000-000000000000"


def _create(client, **fields):
    body = {"title": "Event", "starts_at": "2026-10-01T09:00:00Z", **fields}
    r = client.post("/schedule", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_create_and_get(client):
    event = _create(
        client,
        title="  Dentist ",
        starts_at="2026-10-01T09:00:00-06:00",
        ends_at="2026-10-01T10:00:00-06:00",
        location="Main St",
        category="health",
        recurrence_rule="FREQ=YEARLY",
    )
    assert event["title"] == "Dentist"
    assert event["all_day"] is False
    assert event["recurrence_rule"] == "FREQ=YEARLY"

    r = client.get(f"/schedule/{event['id']}")
    assert r.status_code == 200
    assert r.json() == event


def test_validation(client):
    assert client.post("/schedule", json={"title": "x"}).status_code == 422  # starts_at required
    assert client.post("/schedule", json={"title": "  ", "starts_at": "2026-10-01T09:00:00Z"}).status_code == 422
    # naive datetimes are ambiguous, so they are rejected
    assert client.post("/schedule", json={"title": "x", "starts_at": "2026-10-01T09:00:00"}).status_code == 422
    r = client.post(
        "/schedule", json={"title": "x", "starts_at": "2026-10-01T09:00:00Z", "ends_at": "2026-10-01T08:00:00Z"}
    )
    assert r.status_code == 422
    r = client.post("/schedule", json={"title": "x", "starts_at": "2026-10-01T09:00:00Z", "recurrence_rule": "weekly"})
    assert r.status_code == 422


def test_duplicate_external_id_conflicts(client):
    _create(client, source="google_calendar", external_id="abc")
    r = client.post(
        "/schedule",
        json={"title": "Dup", "starts_at": "2026-10-01T09:00:00Z", "source": "google_calendar", "external_id": "abc"},
    )
    assert r.status_code == 409
    _create(client, source="manual", external_id="abc")  # different source is fine


def test_patch(client):
    event = _create(client, ends_at="2026-10-01T10:00:00Z", notes="bring card")
    r = client.patch(f"/schedule/{event['id']}", json={"title": "Moved", "notes": None})
    assert r.status_code == 200
    assert r.json()["title"] == "Moved"
    assert r.json()["notes"] is None
    assert r.json()["ends_at"] == event["ends_at"]

    # moving the start past the existing end is rejected
    r = client.patch(f"/schedule/{event['id']}", json={"starts_at": "2026-10-01T11:00:00Z"})
    assert r.status_code == 422
    assert client.patch(f"/schedule/{event['id']}", json={"title": None}).status_code == 422
    assert client.patch(f"/schedule/{MISSING}", json={"title": "x"}).status_code == 404


def test_delete(client):
    event = _create(client)
    assert client.delete(f"/schedule/{event['id']}").status_code == 204
    assert client.get(f"/schedule/{event['id']}").status_code == 404
    assert client.delete(f"/schedule/{event['id']}").status_code == 404


def test_list(client):
    _create(client, title="Late", starts_at="2026-10-03T09:00:00Z", category="Work")
    _create(client, title="Early", starts_at="2026-10-01T09:00:00Z", ends_at="2026-10-02T12:00:00Z")
    _create(client, title="Middle", starts_at="2026-10-02T09:00:00Z")

    def titles(**params):
        r = client.get("/schedule", params=params)
        assert r.status_code == 200, r.text
        return [e["title"] for e in r.json()]

    assert titles() == ["Early", "Middle", "Late"]
    assert titles(**{"from": "2026-10-02T10:00:00Z"}) == ["Early", "Late"]  # Early is still running
    assert titles(to="2026-10-02T09:00:00Z") == ["Early", "Middle"]
    assert titles(category="work") == ["Late"]
    assert titles(limit=1, offset=1) == ["Middle"]
