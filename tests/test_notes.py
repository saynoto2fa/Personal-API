MISSING = "00000000-0000-0000-0000-000000000000"


def _create(client, **fields):
    body = {"body": "Something", **fields}
    r = client.post("/notes", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_create_and_get(client):
    note = _create(client, title="Plumber", body="  Call about the sink ", tags=["Home"], source="chat")
    assert note["body"] == "Call about the sink"
    assert note["tags"] == ["home"]

    r = client.get(f"/notes/{note['id']}")
    assert r.status_code == 200
    assert r.json() == note


def test_validation(client):
    assert client.post("/notes", json={}).status_code == 422
    assert client.post("/notes", json={"body": "   "}).status_code == 422


def test_patch(client):
    note = _create(client, title="Draft", body="v1")
    r = client.patch(f"/notes/{note['id']}", json={"body": "v2", "title": None})
    assert r.status_code == 200
    assert r.json()["body"] == "v2"
    assert r.json()["title"] is None

    assert client.patch(f"/notes/{note['id']}", json={"body": None}).status_code == 422
    assert client.patch(f"/notes/{MISSING}", json={"body": "x"}).status_code == 404


def test_delete(client):
    note = _create(client)
    assert client.delete(f"/notes/{note['id']}").status_code == 204
    assert client.get(f"/notes/{note['id']}").status_code == 404
    assert client.delete(f"/notes/{note['id']}").status_code == 404


def test_list(client):
    _create(client, title="Groceries", body="eggs, rice", tags=["home"], source="chat")
    _create(client, body="Quarterly review prep", tags=["work"], source="hermes")
    _create(client, title="Garden", body="plant garlic", tags=["home", "outdoor"])

    def bodies(**params):
        r = client.get("/notes", params=params)
        assert r.status_code == 200, r.text
        return [n["body"] for n in r.json()]

    assert bodies() == ["plant garlic", "Quarterly review prep", "eggs, rice"]  # newest first
    assert bodies(q="groc") == ["eggs, rice"]  # matches title
    assert bodies(q="GARLIC") == ["plant garlic"]
    assert bodies(tag="home") == ["plant garlic", "eggs, rice"]
    assert bodies(source="hermes") == ["Quarterly review prep"]
