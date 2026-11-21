from __future__ import annotations

from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import ElevenLabsConfig, ModelSlotConfig, Settings
from app.main import create_app


def make_test_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_api_token="test-secret-token-123",
        database_path=str(tmp_path / "test_assistant.db"),
        ha_base_url="http://ha.test/api",
        ha_token="ha-supervisor-token",
        deepseek=ModelSlotConfig(
            provider_type="openai_compatible",
            base_url="https://api.cheaperinference.com/v1",
            api_key="ci-key-123",
            model_id="deepseek-v4.1-flash",
            model_lock=True,
        ),
        claude=ModelSlotConfig(
            provider_type="anthropic",
            base_url="https://api.anthropic.com",
            api_key="anth-key-123",
            model_id="claude-sonnet-4-20250514",
            model_lock=False,
        ),
        gemini=ModelSlotConfig(
            provider_type="gemini",
            base_url="https://generativelanguage.googleapis.com",
            api_key="gem-key-123",
            model_id="gemini-2.5-pro",
            model_lock=False,
        ),
        elevenlabs=ElevenLabsConfig(
            api_key="el-key-123",
            voice_id="voice-abc",
            model_id="eleven_flash_v2_5",
        ),
    )


@pytest.mark.asyncio
async def test_health_and_bearer_auth(tmp_path: Path) -> None:
    settings = make_test_settings(tmp_path)
    app = create_app(settings=settings)
    await app.state.db.init()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        # /health is unauthenticated
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

        # Missing bearer token -> 401
        r = await client.get("/v1/threads")
        assert r.status_code == 401

        # Wrong bearer token -> 401
        r = await client.get(
            "/v1/threads",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert r.status_code == 401

        # Valid bearer token -> 200
        r = await client.get(
            "/v1/config/models",
            headers={"Authorization": "Bearer test-secret-token-123"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["models"]["deepseek"]["configured"] is True
        assert body["models"]["claude"]["configured"] is True
        assert body["models"]["gemini"]["configured"] is True
        assert body["tts_configured"] is True


@pytest.mark.asyncio
async def test_threads_crud(tmp_path: Path) -> None:
    settings = make_test_settings(tmp_path)
    app = create_app(settings=settings)
    await app.state.db.init()
    headers = {"Authorization": "Bearer test-secret-token-123"}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        # Create thread
        r = await client.post("/v1/threads", json={"title": "Smart Home"}, headers=headers)
        assert r.status_code == 201
        thread = r.json()
        tid = thread["id"]
        assert thread["title"] == "Smart Home"

        # List threads
        r = await client.get("/v1/threads", headers=headers)
        assert r.status_code == 200
        assert len(r.json()["threads"]) == 1

        # Rename thread
        r = await client.patch(
            f"/v1/threads/{tid}",
            json={"title": "Downstairs Controls"},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["title"] == "Downstairs Controls"

        # Get thread details
        r = await client.get(f"/v1/threads/{tid}", headers=headers)
        assert r.status_code == 200
        assert r.json()["thread"]["title"] == "Downstairs Controls"

        # Delete thread
        r = await client.delete(f"/v1/threads/{tid}", headers=headers)
        assert r.status_code == 200
        assert r.json()["deleted"] is True

        # Verify gone
        r = await client.get(f"/v1/threads/{tid}", headers=headers)
        assert r.status_code == 404
