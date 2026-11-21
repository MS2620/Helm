from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from tests.test_auth_and_threads import make_test_settings


def parse_sse_events(raw_body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in raw_body.strip().split("\n\n"):
        if not block.strip():
            continue
        event_name = "message"
        data_dict: dict[str, Any] = {}
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line[len("event: ") :].strip()
            elif line.startswith("data: "):
                data_dict = json.loads(line[len("data: ") :])
        events.append((event_name, data_dict))
    return events


@pytest.mark.asyncio
async def test_deepseek_direct_answer_and_model_lock_header(tmp_path: Path) -> None:
    captured_headers: dict[str, str] = {}

    def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.cheaperinference.com":
            captured_headers.update(dict(request.headers))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "The capital of France is Paris.",
                            }
                        }
                    ]
                },
            )
        return httpx.Response(404)

    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    settings = make_test_settings(tmp_path)
    app = create_app(settings=settings, http_client=mock_http)
    await app.state.db.init()
    headers = {"Authorization": "Bearer test-secret-token-123"}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        t_resp = await client.post("/v1/threads", json={}, headers=headers)
        tid = t_resp.json()["id"]

        resp = await client.post(
            f"/v1/threads/{tid}/messages",
            json={"content": "What is the capital of France?", "model_override": "auto"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert captured_headers.get("x-ci-model-lock") == "true"

        events = parse_sse_events(resp.text)
        done_events = [d for name, d in events if name == "done"]
        assert len(done_events) == 1
        assert done_events[0]["model"] == "deepseek"
        assert done_events[0]["escalated"] is False
        assert "Paris" in done_events[0]["full_text"]


@pytest.mark.asyncio
async def test_escalation_from_deepseek_to_claude(tmp_path: Path) -> None:
    def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.cheaperinference.com":
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call_esc_1",
                                        "type": "function",
                                        "function": {
                                            "name": "escalate_to_model",
                                            "arguments": json.dumps(
                                                {
                                                    "target": "claude",
                                                    "reason": "Complex Swift concurrency architecture question",
                                                }
                                            ),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        if request.url.host == "api.anthropic.com":
            return httpx.Response(
                200,
                json={
                    "content": [
                        {
                            "type": "text",
                            "text": "Here is the complete Swift actor isolation pattern.",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    settings = make_test_settings(tmp_path)
    app = create_app(settings=settings, http_client=mock_http)
    await app.state.db.init()
    headers = {"Authorization": "Bearer test-secret-token-123"}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        t_resp = await client.post("/v1/threads", json={}, headers=headers)
        tid = t_resp.json()["id"]

        resp = await client.post(
            f"/v1/threads/{tid}/messages",
            json={"content": "Design a lock-free Swift actor pipeline", "model_override": "auto"},
            headers=headers,
        )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)
        routing_events = [d for name, d in events if name == "routing"]
        assert any(r["model"] == "claude" and r["escalated"] is True for r in routing_events)

        done = next(d for name, d in events if name == "done")
        assert done["model"] == "claude"
        assert done["escalated"] is True
        assert "Swift actor isolation" in done["full_text"]


@pytest.mark.asyncio
async def test_manual_override_gemini(tmp_path: Path) -> None:
    deepseek_called = False

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal deepseek_called
        if request.url.host == "api.cheaperinference.com":
            deepseek_called = True
            return httpx.Response(500)
        if request.url.host == "generativelanguage.googleapis.com":
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": "Answered directly by Gemini 2.5 Pro."}]
                            }
                        }
                    ]
                },
            )
        return httpx.Response(404)

    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    settings = make_test_settings(tmp_path)
    app = create_app(settings=settings, http_client=mock_http)
    await app.state.db.init()
    headers = {"Authorization": "Bearer test-secret-token-123"}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        t_resp = await client.post("/v1/threads", json={}, headers=headers)
        tid = t_resp.json()["id"]

        resp = await client.post(
            f"/v1/threads/{tid}/messages",
            json={"content": "Summarize quantum error correction", "model_override": "gemini"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert deepseek_called is False
        events = parse_sse_events(resp.text)
        done = next(d for name, d in events if name == "done")
        assert done["model"] == "gemini"
        assert "Gemini 2.5 Pro" in done["full_text"]


@pytest.mark.asyncio
async def test_safe_vs_sensitive_home_assistant_actions_and_face_id_confirm(
    tmp_path: Path,
) -> None:
    ha_calls: list[tuple[str, str, dict[str, Any]]] = []
    llm_turn = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal llm_turn
        if request.url.host == "ha.test":
            if request.method == "GET" and request.url.path == "/api/states":
                # Return empty states here so this test exercises the AI tool-calling path
                return httpx.Response(200, json=[])
            body = json.loads(request.content.decode("utf-8")) if request.content else {}
            ha_calls.append((request.method, request.url.path, body))
            return httpx.Response(200, json=[{"entity_id": body.get("entity_id")}])

        if request.url.host == "api.cheaperinference.com":
            llm_turn += 1
            req_json = json.loads(request.content.decode("utf-8"))
            last_msg = req_json["messages"][-1]

            # Turn 1: Safe light turn_on
            if last_msg["role"] == "user" and "living room light" in last_msg["content"]:
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "",
                                    "tool_calls": [
                                        {
                                            "id": "call_light_1",
                                            "type": "function",
                                            "function": {
                                                "name": "ha_call_service",
                                                "arguments": json.dumps(
                                                    {
                                                        "domain": "light",
                                                        "service": "turn_on",
                                                        "entity_id": "light.living_room",
                                                        "service_data": {"brightness_pct": 80},
                                                    }
                                                ),
                                            },
                                        }
                                    ],
                                }
                            }
                        ]
                    },
                )

            # Turn 2: Sensitive lock.unlock
            if last_msg["role"] == "user" and "unlock the front door" in last_msg["content"].lower():
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "",
                                    "tool_calls": [
                                        {
                                            "id": "call_lock_1",
                                            "type": "function",
                                            "function": {
                                                "name": "ha_call_service",
                                                "arguments": json.dumps(
                                                    {
                                                        "domain": "lock",
                                                        "service": "unlock",
                                                        "entity_id": "lock.front_door",
                                                    }
                                                ),
                                            },
                                        }
                                    ],
                                }
                            }
                        ]
                    },
                )

            # Tool result follow-up
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Processed your Home Assistant command.",
                            }
                        }
                    ]
                },
            )

        if request.url.host == "api.elevenlabs.io":
            return httpx.Response(200, content=b"FAKE_MP3_AUDIO_BYTES")

        return httpx.Response(404)

    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    settings = make_test_settings(tmp_path)
    app = create_app(settings=settings, http_client=mock_http)
    await app.state.db.init()
    headers = {"Authorization": "Bearer test-secret-token-123"}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        t_resp = await client.post("/v1/threads", json={}, headers=headers)
        tid = t_resp.json()["id"]

        # 1. Safe command executes immediately
        r1 = await client.post(
            f"/v1/threads/{tid}/messages",
            json={"content": "Turn on the living room light"},
            headers=headers,
        )
        assert r1.status_code == 200
        assert len(ha_calls) == 1
        assert ha_calls[0][1] == "/api/services/light/turn_on"
        assert ha_calls[0][2]["entity_id"] == "light.living_room"

        # 2. Sensitive command (lock.unlock) is blocked by safety gate
        r2 = await client.post(
            f"/v1/threads/{tid}/messages",
            json={"content": "Unlock the front door"},
            headers=headers,
        )
        assert r2.status_code == 200
        # Still only 1 HA call because lock.unlock was intercepted!
        assert len(ha_calls) == 1

        events = parse_sse_events(r2.text)
        action_events = [d for name, d in events if name == "action_required"]
        assert len(action_events) == 1
        pending_action = action_events[0]
        action_id = pending_action["id"]
        assert pending_action["domain"] == "lock"
        assert pending_action["service"] == "unlock"
        assert pending_action["entity_id"] == "lock.front_door"

        # 3. User approves with Face ID on iPhone -> calls /confirm
        confirm_resp = await client.post(
            f"/v1/actions/{action_id}/confirm",
            headers=headers,
        )
        assert confirm_resp.status_code == 200
        assert confirm_resp.json()["action"]["status"] == "executed"
        # Now the HA lock/unlock call was executed!
        assert len(ha_calls) == 2
        assert ha_calls[1][1] == "/api/services/lock/unlock"
        assert ha_calls[1][2]["entity_id"] == "lock.front_door"

        # 4. Re-confirming an already executed action returns 409 Conflict
        again_resp = await client.post(
            f"/v1/actions/{action_id}/confirm",
            headers=headers,
        )
        assert again_resp.status_code == 409

        # 5. Test expired action rejection (410 Gone)
        expired = await app.state.db.create_pending_action(
            thread_id=tid,
            domain="cover",
            service="open_cover",
            entity_id="cover.garage_door",
            service_data={},
            summary="Cover: Open Cover → cover.garage_door",
            ttl_seconds=-10,
        )
        exp_resp = await client.post(
            f"/v1/actions/{expired['id']}/confirm",
            headers=headers,
        )
        assert exp_resp.status_code == 410

        # 6. Test ElevenLabs TTS streaming endpoint
        tts_resp = await client.post(
            "/v1/tts",
            json={"text": "Front door unlocked."},
            headers=headers,
        )
        assert tts_resp.status_code == 200
        assert tts_resp.content == b"FAKE_MP3_AUDIO_BYTES"


@pytest.mark.asyncio
async def test_instant_home_assistant_fast_path_skips_ai(tmp_path: Path) -> None:
    ai_calls = 0
    ha_service_calls: list[tuple[str, dict[str, Any]]] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal ai_calls
        if request.url.host in (
            "api.cheaperinference.com",
            "api.anthropic.com",
            "generativelanguage.googleapis.com",
        ):
            ai_calls += 1
            return httpx.Response(500)

        if request.url.host == "ha.test":
            if request.method == "GET" and request.url.path == "/api/states":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "entity_id": "light.living_room",
                            "state": "off",
                            "attributes": {"friendly_name": "Living Room Light"},
                        },
                        {
                            "entity_id": "lock.front_door",
                            "state": "locked",
                            "attributes": {"friendly_name": "Front Door"},
                        },
                    ],
                )
            body = json.loads(request.content.decode("utf-8")) if request.content else {}
            ha_service_calls.append((request.url.path, body))
            return httpx.Response(200, json=[])

        return httpx.Response(404)

    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    settings = make_test_settings(tmp_path)
    app = create_app(settings=settings, http_client=mock_http)
    await app.state.db.init()
    headers = {"Authorization": "Bearer test-secret-token-123"}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        t_resp = await client.post("/v1/threads", json={}, headers=headers)
        tid = t_resp.json()["id"]

        # 1. Safe HA command skips AI completely (0 LLM calls)
        r1 = await client.post(
            f"/v1/threads/{tid}/messages",
            json={"content": "Turn on the living room light to 75%"},
            headers=headers,
        )
        assert r1.status_code == 200
        assert ai_calls == 0
        assert len(ha_service_calls) == 1
        assert ha_service_calls[0][0] == "/api/services/light/turn_on"
        assert ha_service_calls[0][1] == {
            "entity_id": "light.living_room",
            "brightness_pct": 75,
        }
        events1 = parse_sse_events(r1.text)
        done1 = next(d for name, d in events1 if name == "done")
        assert done1["model"] == "ha_direct"

        # 2. Sensitive HA command also skips AI (0 LLM calls) and still queues Face ID approval!
        r2 = await client.post(
            f"/v1/threads/{tid}/messages",
            json={"content": "Unlock the front door"},
            headers=headers,
        )
        assert r2.status_code == 200
        assert ai_calls == 0
        assert len(ha_service_calls) == 1  # Not executed yet until Face ID confirm
        events2 = parse_sse_events(r2.text)
        action_events = [d for name, d in events2 if name == "action_required"]
        assert len(action_events) == 1
        assert action_events[0]["entity_id"] == "lock.front_door"

