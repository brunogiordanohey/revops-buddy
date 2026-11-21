"""HTTP surface of the minimal server: health, join with presync, bearer token."""
from fastapi.testclient import TestClient

from revops_buddy.config import Settings
from revops_buddy.server import create_app

MEET = "https://meet.google.com/abc-defg-hij"


class FakeMeetingBaas:
    def __init__(self):
        self.created, self.left = [], []

    async def create_bot(self, **options):
        self.created.append(options)
        return "bot-1"

    async def leave(self, bot_id):
        self.left.append(bot_id)

    async def status(self, bot_id):
        return "in_waiting_room"

    async def aclose(self):
        pass


def make(tmp_path, **overrides):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db", public_base_url="https://example.test", **overrides)
    fake = FakeMeetingBaas()
    return create_app(settings, meetingbaas_client=fake), fake


def test_health_without_any_keys(tmp_path):
    app, _ = make(tmp_path)
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["ok"] is True
    assert set(body["providers"].values()) == {False}


def test_join_runs_presync_and_returns_bot_id(tmp_path):
    app, fake = make(tmp_path)
    calls = []
    original = app.state.presync

    async def spy():
        calls.append(True)
        return await original()

    app.state.presync = spy
    with TestClient(app) as client:
        response = client.post("/meetings", json={"meeting_url": MEET})
        assert response.status_code == 200 and response.json() == {"bot_id": "bot-1"}
        assert calls == [True]
        options = fake.created[0]
        assert options["output_url"].startswith("wss://example.test/audio/out?")
        assert options["input_url"].startswith("wss://example.test/audio/in?")
        assert client.post("/meetings", json={"meeting_url": MEET}).status_code == 409
        assert client.post("/meetings/bot-1/leave").json() == {"ok": True}
    assert fake.left == ["bot-1"]


def test_rejects_non_meet_urls(tmp_path):
    app, fake = make(tmp_path)
    with TestClient(app) as client:
        assert client.post("/meetings", json={"meeting_url": "https://evil.test/abc-defg-hij"}).status_code == 422
    assert fake.created == []


def test_api_token_required_when_set(tmp_path):
    app, fake = make(tmp_path, api_token="s3cret")
    with TestClient(app) as client:
        assert client.post("/meetings", json={"meeting_url": MEET}).status_code == 401
        ok = client.post("/meetings", json={"meeting_url": MEET}, headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200
    assert len(fake.created) == 1
