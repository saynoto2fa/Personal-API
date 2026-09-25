from datetime import date, timedelta

import pytest

from app.config import get_settings


def _create(client, **fields):
    body = {"name": "Item", **fields}
    r = client.post("/pantry", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_create_and_get(client):
    item = _create(
        client,
        name="  Rice pasta ",
        qty=2,
        unit="bag",
        location="pantry",
        expiry_estimate="2027-01-01",
        dietary_tags=["Gluten-Free", "pork free", "gluten_free"],
    )
    assert item["name"] == "Rice pasta"
    assert item["qty"] == 2
    assert item["dietary_tags"] == ["gluten_free", "pork_free"]

    r = client.get(f"/pantry/{item['id']}")
    assert r.status_code == 200
    assert r.json() == item


def test_create_defaults_and_validation(client):
    item = _create(client, name="Salt")
    assert item["qty"] == 1
    assert item["dietary_tags"] == []

    assert client.post("/pantry", json={"name": "   "}).status_code == 422
    assert client.post("/pantry", json={"name": "Eggs", "qty": -1}).status_code == 422
    assert client.post("/pantry", json={}).status_code == 422


def test_get_missing_returns_404(client):
    r = client.get("/pantry/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_patch_partial_update(client):
    item = _create(client, name="Milk", qty=1, unit="l", notes="oat")
    r = client.patch(f"/pantry/{item['id']}", json={"qty": 0.5, "notes": None})
    assert r.status_code == 200
    body = r.json()
    assert body["qty"] == 0.5
    assert body["notes"] is None
    assert body["unit"] == "l"  # untouched
    assert body["updated_at"] >= item["updated_at"]

    assert client.patch(f"/pantry/{item['id']}", json={"name": None}).status_code == 422
    assert client.patch("/pantry/00000000-0000-0000-0000-000000000000", json={"qty": 1}).status_code == 404


def test_adjust_qty_clamps_at_zero(client):
    item = _create(client, name="Eggs", qty=6, unit="each")
    r = client.post(f"/pantry/{item['id']}/adjust", json={"delta": -2})
    assert r.json()["qty"] == 4
    r = client.post(f"/pantry/{item['id']}/adjust", json={"delta": -10})
    assert r.json()["qty"] == 0
    r = client.post(f"/pantry/{item['id']}/adjust", json={"delta": 0.25})
    assert r.json()["qty"] == 0.25


def test_delete(client):
    item = _create(client, name="Old bread")
    assert client.delete(f"/pantry/{item['id']}").status_code == 204
    assert client.get(f"/pantry/{item['id']}").status_code == 404
    assert client.delete(f"/pantry/{item['id']}").status_code == 404


def test_list_filters(client):
    today = date.today()
    _create(client, name="GF bread", location="Freezer", dietary_tags=["gluten_free", "pork_free"],
            expiry_estimate=str(today + timedelta(days=30)))
    _create(client, name="Bacon", location="fridge", dietary_tags=["gluten_free", "contains_pork"],
            expiry_estimate=str(today + timedelta(days=3)))
    _create(client, name="Wheat flour", location="pantry", dietary_tags=["contains_gluten"], qty=0)
    _create(client, name="Yogurt", location="fridge", expiry_estimate=str(today - timedelta(days=1)))

    def names(**params):
        r = client.get("/pantry", params=params)
        assert r.status_code == 200, r.text
        return [i["name"] for i in r.json()]

    assert names() == ["Bacon", "GF bread", "Wheat flour", "Yogurt"]
    assert names(q="bread") == ["GF bread"]
    assert names(location="FRIDGE") == ["Bacon", "Yogurt"]
    assert names(tag="gluten_free") == ["Bacon", "GF bread"]
    # Safe for everyone in the household: gluten-free AND not pork.
    assert names(tag="gluten-free", exclude_tag="contains_pork") == ["GF bread"]
    assert names(exclude_tag=["contains_pork", "contains_gluten"]) == ["GF bread", "Yogurt"]
    assert names(expiring_within_days=7) == ["Bacon", "Yogurt"]
    assert names(sort="expiry") == ["Yogurt", "Bacon", "GF bread", "Wheat flour"]
    assert names(in_stock=False) == ["Wheat flour"]
    assert "Wheat flour" not in names(in_stock=True)
    assert names(limit=2, offset=1) == ["GF bread", "Wheat flour"]


def test_api_key_required_when_configured(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "api_key", "s3cret")
    assert client.get("/pantry").status_code == 401
    assert client.get("/pantry", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/pantry", headers={"X-API-Key": "s3cret"}).status_code == 200
    assert client.get("/health").status_code == 200  # health stays open


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert "0001_core_schema" in r.json()["migrations"]
