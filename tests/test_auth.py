import pytest

from app.config import get_settings

PROTECTED = ["/pantry", "/schedule", "/habits", "/contacts", "/notes"]


@pytest.mark.parametrize("path", PROTECTED)
def test_every_resource_requires_api_key(client, monkeypatch, path):
    monkeypatch.setattr(get_settings(), "api_key", "s3cret")
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post(path, json={}).status_code == 401  # rejected before body validation
    assert client.get(path, headers={"X-API-Key": "s3cret"}).status_code == 200
