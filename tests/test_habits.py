MISSING = "00000000-0000-0000-0000-000000000000"


def _create(client, **fields):
    body = {"name": "Habit", **fields}
    r = client.post("/habits", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_create_and_get(client):
    habit = _create(client, name=" Read ", description="Before bed", target_per_week=5, unit="pages")
    assert habit["name"] == "Read"
    assert habit["active"] is True
    assert habit["target_per_week"] == 5

    r = client.get(f"/habits/{habit['id']}")
    assert r.status_code == 200
    assert r.json() == habit


def test_validation(client):
    assert client.post("/habits", json={}).status_code == 422
    assert client.post("/habits", json={"name": "  "}).status_code == 422
    assert client.post("/habits", json={"name": "x", "target_per_week": 0}).status_code == 422
    assert client.post("/habits", json={"name": "x", "target_per_week": 8}).status_code == 422


def test_duplicate_name_conflicts(client):
    first = _create(client, name="Walk")
    assert client.post("/habits", json={"name": "Walk"}).status_code == 409
    other = _create(client, name="Stretch")
    assert client.patch(f"/habits/{other['id']}", json={"name": "Walk"}).status_code == 409
    assert client.get(f"/habits/{first['id']}").json()["name"] == "Walk"


def test_patch(client):
    habit = _create(client, name="Meditate", unit="min")
    r = client.patch(f"/habits/{habit['id']}", json={"active": False, "unit": None})
    assert r.status_code == 200
    assert r.json()["active"] is False
    assert r.json()["unit"] is None
    assert r.json()["name"] == "Meditate"

    assert client.patch(f"/habits/{habit['id']}", json={"active": None}).status_code == 422
    assert client.patch(f"/habits/{MISSING}", json={"active": True}).status_code == 404


def test_delete(client):
    habit = _create(client)
    assert client.delete(f"/habits/{habit['id']}").status_code == 204
    assert client.get(f"/habits/{habit['id']}").status_code == 404
    assert client.delete(f"/habits/{habit['id']}").status_code == 404


def test_list(client):
    _create(client, name="walk")
    _create(client, name="Read")
    _create(client, name="Journal", active=False)

    def names(**params):
        r = client.get("/habits", params=params)
        assert r.status_code == 200, r.text
        return [h["name"] for h in r.json()]

    assert names() == ["Journal", "Read", "walk"]
    assert names(active=True) == ["Read", "walk"]
    assert names(active=False) == ["Journal"]
