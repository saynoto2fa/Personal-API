from datetime import date, timedelta

MISSING = "00000000-0000-0000-0000-000000000000"


def _create(client, **fields):
    body = {"name": "Person", **fields}
    r = client.post("/contacts", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_create_and_get(client):
    contact = _create(
        client,
        name=" Jane Doe ",
        relationship="friend",
        email="jane@example.com",
        birthday="1990-05-04",
        dietary_tags=["Gluten-Free"],
        tags=["Book Club", "book-club"],
        vault_path="people/jane-doe.md",
    )
    assert contact["name"] == "Jane Doe"
    assert contact["dietary_tags"] == ["gluten_free"]
    assert contact["tags"] == ["book_club"]

    r = client.get(f"/contacts/{contact['id']}")
    assert r.status_code == 200
    assert r.json() == contact


def test_validation(client):
    assert client.post("/contacts", json={}).status_code == 422
    assert client.post("/contacts", json={"name": " "}).status_code == 422
    assert client.post("/contacts", json={"name": "x", "email": "not-an-email"}).status_code == 422
    tomorrow = str(date.today() + timedelta(days=1))
    assert client.post("/contacts", json={"name": "x", "birthday": tomorrow}).status_code == 422


def test_patch(client):
    contact = _create(client, name="Bob", phone="555-0100", tags=["work"])
    r = client.patch(f"/contacts/{contact['id']}", json={"phone": None, "tags": ["Work", "Golf"]})
    assert r.status_code == 200
    assert r.json()["phone"] is None
    assert r.json()["tags"] == ["golf", "work"]
    assert r.json()["name"] == "Bob"

    assert client.patch(f"/contacts/{contact['id']}", json={"tags": None}).status_code == 422
    assert client.patch(f"/contacts/{MISSING}", json={"name": "x"}).status_code == 404


def test_delete(client):
    contact = _create(client)
    assert client.delete(f"/contacts/{contact['id']}").status_code == 204
    assert client.get(f"/contacts/{contact['id']}").status_code == 404
    assert client.delete(f"/contacts/{contact['id']}").status_code == 404


def test_list(client):
    _create(client, name="zoe", tags=["family"])
    _create(client, name="Adam", tags=["family", "local"])
    _create(client, name="Maria", tags=["work"])

    def names(**params):
        r = client.get("/contacts", params=params)
        assert r.status_code == 200, r.text
        return [c["name"] for c in r.json()]

    assert names() == ["Adam", "Maria", "zoe"]
    assert names(q="AR") == ["Maria"]
    assert names(tag="family") == ["Adam", "zoe"]
    assert names(tag=["family", "local"]) == ["Adam"]
