from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth import verify_bearer_token
from app.config import Settings, load_settings
from app.database import Database
from app.ha_client import HomeAssistantClient
from app.providers import LLMProviderClient
from app.router_engine import RouterEngine
from app.safety import cancel_pending_action, confirm_pending_action
from app.tts import ElevenLabsTTSClient


class CreateThreadRequest(BaseModel):
    title: str = Field(default="New Chat")


class UpdateThreadRequest(BaseModel):
    title: str


class SendMessageRequest(BaseModel):
    content: str
    model_override: str = Field(default="auto")


class TTSRequest(BaseModel):
    text: str
    voice_id: str | None = None


def create_app(
    settings: Settings | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    resolved_settings = settings or load_settings()
    shared_http = http_client or httpx.AsyncClient(timeout=60.0)
    owns_http = http_client is None

    db = Database(resolved_settings.database_path)
    ha_client = HomeAssistantClient(
        base_url=resolved_settings.ha_base_url,
        token=resolved_settings.ha_token,
        http_client=shared_http,
    )
    llm_client = LLMProviderClient(http_client=shared_http)
    router_engine = RouterEngine(
        settings=resolved_settings,
        db=db,
        ha_client=ha_client,
        llm_client=llm_client,
    )
    tts_client = ElevenLabsTTSClient(
        config=resolved_settings.elevenlabs,
        http_client=shared_http,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await db.init()
        try:
            yield
        finally:
            if owns_http:
                await shared_http.aclose()

    app = FastAPI(
        title="Helm AI Router",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.db = db
    app.state.ha_client = ha_client
    app.state.llm_client = llm_client
    app.state.router_engine = router_engine
    app.state.tts_client = tts_client

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/v1/config/models", dependencies=[Depends(verify_bearer_token)])
    async def get_models_config(request: Request) -> dict[str, Any]:
        cfg: Settings = request.app.state.settings
        return {
            "models": {
                "deepseek": {
                    "label": "DeepSeek v4.1-Flash",
                    "model_id": cfg.deepseek.model_id,
                    "provider_type": cfg.deepseek.provider_type,
                    "configured": bool(cfg.deepseek.api_key),
                },
                "claude": {
                    "label": "Claude",
                    "model_id": cfg.claude.model_id,
                    "provider_type": cfg.claude.provider_type,
                    "configured": bool(cfg.claude.api_key),
                },
                "gemini": {
                    "label": "Gemini",
                    "model_id": cfg.gemini.model_id,
                    "provider_type": cfg.gemini.provider_type,
                    "configured": bool(cfg.gemini.api_key),
                },
            },
            "tts_configured": bool(cfg.elevenlabs.api_key),
            "sensitive_domains": cfg.sensitive_domains,
        }

    @app.get("/v1/threads", dependencies=[Depends(verify_bearer_token)])
    async def list_threads(request: Request) -> dict[str, Any]:
        threads = await request.app.state.db.list_threads()
        return {"threads": threads}

    @app.post(
        "/v1/threads",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(verify_bearer_token)],
    )
    async def create_thread(
        body: CreateThreadRequest,
        request: Request,
    ) -> dict[str, Any]:
        thread = await request.app.state.db.create_thread(title=body.title)
        return thread

    @app.get("/v1/threads/{thread_id}", dependencies=[Depends(verify_bearer_token)])
    async def get_thread(thread_id: str, request: Request) -> dict[str, Any]:
        database: Database = request.app.state.db
        thread = await database.get_thread(thread_id)
        if not thread:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Thread not found.",
            )
        messages = await database.list_messages(thread_id)
        pending_actions = await database.list_pending_actions(thread_id)
        return {
            "thread": thread,
            "messages": messages,
            "pending_actions": pending_actions,
        }

    @app.patch("/v1/threads/{thread_id}", dependencies=[Depends(verify_bearer_token)])
    async def update_thread(
        thread_id: str,
        body: UpdateThreadRequest,
        request: Request,
    ) -> dict[str, Any]:
        database: Database = request.app.state.db
        updated = await database.update_thread_title(thread_id, body.title)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Thread not found.",
            )
        return updated

    @app.delete("/v1/threads/{thread_id}", dependencies=[Depends(verify_bearer_token)])
    async def delete_thread(thread_id: str, request: Request) -> dict[str, Any]:
        database: Database = request.app.state.db
        deleted = await database.delete_thread(thread_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Thread not found.",
            )
        return {"deleted": True, "id": thread_id}

    @app.post(
        "/v1/threads/{thread_id}/messages",
        dependencies=[Depends(verify_bearer_token)],
    )
    async def send_message(
        thread_id: str,
        body: SendMessageRequest,
        request: Request,
    ) -> StreamingResponse:
        engine: RouterEngine = request.app.state.router_engine
        generator = engine.process_user_message(
            thread_id=thread_id,
            user_content=body.content,
            model_override=body.model_override,
        )
        return StreamingResponse(
            generator,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post(
        "/v1/actions/{action_id}/confirm",
        dependencies=[Depends(verify_bearer_token)],
    )
    async def confirm_action_endpoint(
        action_id: str,
        request: Request,
    ) -> dict[str, Any]:
        result = await confirm_pending_action(
            action_id=action_id,
            db=request.app.state.db,
            ha_client=request.app.state.ha_client,
        )
        return {"action": result}

    @app.post(
        "/v1/actions/{action_id}/cancel",
        dependencies=[Depends(verify_bearer_token)],
    )
    async def cancel_action_endpoint(
        action_id: str,
        request: Request,
    ) -> dict[str, Any]:
        result = await cancel_pending_action(
            action_id=action_id,
            db=request.app.state.db,
        )
        return {"action": result}

    @app.post("/v1/tts", dependencies=[Depends(verify_bearer_token)])
    async def synthesize_tts(
        body: TTSRequest,
        request: Request,
    ) -> StreamingResponse:
        tts: ElevenLabsTTSClient = request.app.state.tts_client
        # Verify configuration upfront before opening stream
        if not (tts.config.api_key or "").strip():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="ElevenLabs API key is not configured.",
            )
        stream = tts.synthesize_mp3_stream(text=body.text, voice_id=body.voice_id)
        return StreamingResponse(stream, media_type="audio/mpeg")

    return app


app = create_app()
