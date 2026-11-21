from fastapi.testclient import TestClient

from copilot import api

client = TestClient(api.app)


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_categorize_offline_uses_rules(monkeypatch):
    monkeypatch.setattr(api, "has_credentials", lambda: False)
    r = client.post("/categorize", json=[
        {"id": "x", "date": "2026-10-01", "description": "JUMBO 12", "amount": -5}])
    assert r.status_code == 200 and r.json()[0]["category"] == "Groceries"


def test_categorize_validates_input():
    assert client.post("/categorize", json=[{"id": "x"}]).status_code == 422
    assert client.post("/categorize", json=[]).status_code == 422


def test_help_search():
    assert client.get("/help/search", params={"q": "lost card"}).json()[0]["source"] == "cards > Lost or stolen card"


def test_chat_without_key_is_503(monkeypatch):
    monkeypatch.setattr(api, "has_credentials", lambda: False)
    assert client.post("/chat", json={"message": "hi"}).status_code == 503
