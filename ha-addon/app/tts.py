from __future__ import annotations

import re
from typing import AsyncGenerator

import httpx
from fastapi import HTTPException, status

from app.config import ElevenLabsConfig


def clean_text_for_speech(text: str) -> str:
    """Strip markdown formatting so ElevenLabs reads clean natural prose."""
    cleaned = re.sub(r"```[\s\S]*?```", " code block omitted. ", text)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"[*_~#]+", "", cleaned)
    cleaned = re.sub(r"→", " to ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


class ElevenLabsTTSClient:
    """Streams MP3 speech audio from ElevenLabs without exposing the API key to the iPhone."""

    def __init__(
        self,
        config: ElevenLabsConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._external_client = http_client

    async def synthesize_mp3_stream(
        self,
        text: str,
        voice_id: str | None = None,
    ) -> AsyncGenerator[bytes, None]:
        api_key = (self.config.api_key or "").strip()
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="ElevenLabs API key is not configured in the HAOS Add-on.",
            )

        clean_text = clean_text_for_speech(text or "")
        if not clean_text:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="TTS text must not be empty.",
            )

        vid = (voice_id or self.config.voice_id or "JBFqnCBsd6RMkjVDRZzb").strip()
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}/stream"
        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": clean_text,
            "model_id": self.config.model_id or "eleven_flash_v2_5",
        }
        params = {"output_format": "mp3_44100_128"}

        if self._external_client is not None:
            resp = await self._external_client.post(
                url,
                headers=headers,
                json=payload,
                params=params,
                timeout=30.0,
            )
            if resp.status_code >= 400:
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"ElevenLabs error: {resp.text}",
                )
            yield resp.content
            return

        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream(
                "POST",
                url,
                headers=headers,
                json=payload,
                params=params,
            ) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise HTTPException(
                        status_code=resp.status_code,
                        detail=f"ElevenLabs error: {body.decode('utf-8', errors='replace')}",
                    )
                async for chunk in resp.aiter_bytes(chunk_size=4096):
                    if chunk:
                        yield chunk
