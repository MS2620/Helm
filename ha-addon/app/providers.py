from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import ModelSlotConfig


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelTurnResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMProviderClient:
    """Unified async LLM client supporting OpenAI-compatible (CheaperInference), Anthropic, and Gemini."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._external_client = http_client

    async def complete_turn(
        self,
        slot_config: ModelSlotConfig,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelTurnResult:
        ptype = slot_config.provider_type
        if ptype == "openai_compatible":
            return await self._call_openai_compatible(slot_config, system_prompt, messages, tools)
        if ptype == "anthropic":
            return await self._call_anthropic(slot_config, system_prompt, messages, tools)
        if ptype == "gemini":
            return await self._call_gemini(slot_config, system_prompt, messages, tools)
        raise ValueError(f"Unsupported provider_type: {ptype}")

    async def _post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._external_client is not None:
            resp = await self._external_client.post(
                url,
                headers=headers,
                json=payload,
                params=params,
                timeout=60.0,
            )
            resp.raise_for_status()
            return resp.json()

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                url,
                headers=headers,
                json=payload,
                params=params,
            )
            resp.raise_for_status()
            return resp.json()

    async def _call_openai_compatible(
        self,
        cfg: ModelSlotConfig,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> ModelTurnResult:
        base = cfg.base_url.rstrip("/")
        url = f"{base}/chat/completions" if not base.endswith("/chat/completions") else base
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
        }
        if cfg.model_lock:
            headers["x-ci-model-lock"] = cfg.model_id

        oai_messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for m in messages:
            role = m["role"]
            if role == "tool":
                oai_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": m["tool_call_id"],
                        "name": m.get("name", ""),
                        "content": m["content"],
                    }
                )
            elif role == "assistant" and m.get("tool_calls"):
                oai_messages.append(
                    {
                        "role": "assistant",
                        "content": m.get("content") or "",
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": json.dumps(tc["arguments"]),
                                },
                            }
                            for tc in m["tool_calls"]
                        ],
                    }
                )
            else:
                oai_messages.append({"role": role, "content": m.get("content") or ""})

        payload: dict[str, Any] = {
            "model": cfg.model_id,
            "messages": oai_messages,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["parameters"],
                    },
                }
                for t in tools
            ]

        data = await self._post_json(url, headers, payload)
        choices = data.get("choices") or []
        if not choices:
            return ModelTurnResult()

        msg = choices[0].get("message") or {}
        text = msg.get("content") or ""
        parsed_calls: list[ToolCall] = []
        for raw_tc in msg.get("tool_calls") or []:
            fn = raw_tc.get("function") or {}
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except Exception:
                args = {}
            parsed_calls.append(
                ToolCall(
                    id=raw_tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                    name=fn.get("name") or "",
                    arguments=args,
                )
            )
        return ModelTurnResult(text=text, tool_calls=parsed_calls)

    async def _call_anthropic(
        self,
        cfg: ModelSlotConfig,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> ModelTurnResult:
        base = cfg.base_url.rstrip("/")
        if base.endswith("/v1/messages") or base.endswith("/messages"):
            url = base
        elif base.endswith("/v1"):
            url = f"{base}/messages"
        else:
            url = f"{base}/v1/messages"

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "x-api-key": cfg.api_key,
            "Authorization": f"Bearer {cfg.api_key}",
            "anthropic-version": "2023-06-01",
        }
        if cfg.model_lock:
            headers["x-ci-model-lock"] = cfg.model_id

        anth_messages: list[dict[str, Any]] = []
        for m in messages:
            role = m["role"]
            if role == "tool":
                anth_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m["tool_call_id"],
                                "content": m["content"],
                            }
                        ],
                    }
                )
            elif role == "assistant" and m.get("tool_calls"):
                blocks: list[dict[str, Any]] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m["tool_calls"]:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": tc["name"],
                            "input": tc["arguments"],
                        }
                    )
                anth_messages.append({"role": "assistant", "content": blocks})
            else:
                anth_messages.append({"role": role, "content": m.get("content") or ""})

        payload: dict[str, Any] = {
            "model": cfg.model_id,
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": anth_messages,
        }
        if tools:
            payload["tools"] = [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": t["parameters"],
                }
                for t in tools
            ]

        data = await self._post_json(url, headers, payload)
        text_parts: list[str] = []
        parsed_calls: list[ToolCall] = []
        for block in data.get("content") or []:
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text") or "")
            elif btype == "tool_use":
                parsed_calls.append(
                    ToolCall(
                        id=block.get("id") or f"toolu_{uuid.uuid4().hex[:8]}",
                        name=block.get("name") or "",
                        arguments=block.get("input") or {},
                    )
                )
        return ModelTurnResult(text="".join(text_parts), tool_calls=parsed_calls)

    async def _call_gemini(
        self,
        cfg: ModelSlotConfig,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> ModelTurnResult:
        base = cfg.base_url.rstrip("/")
        url = f"{base}/v1beta/models/{cfg.model_id}:generateContent"
        headers = {"Content-Type": "application/json"}
        params = {"key": cfg.api_key}

        contents: list[dict[str, Any]] = []
        for m in messages:
            role = m["role"]
            if role == "tool":
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": m.get("name", "tool"),
                                    "response": {"result": m["content"]},
                                }
                            }
                        ],
                    }
                )
            elif role == "assistant" and m.get("tool_calls"):
                parts: list[dict[str, Any]] = []
                if m.get("content"):
                    parts.append({"text": m["content"]})
                for tc in m["tool_calls"]:
                    parts.append(
                        {
                            "functionCall": {
                                "name": tc["name"],
                                "args": tc["arguments"],
                            }
                        }
                    )
                contents.append({"role": "model", "parts": parts})
            else:
                g_role = "model" if role == "assistant" else "user"
                contents.append({"role": g_role, "parts": [{"text": m.get("content") or ""}]})

        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
        }
        if tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t["name"],
                            "description": t["description"],
                            "parameters": t["parameters"],
                        }
                        for t in tools
                    ]
                }
            ]

        data = await self._post_json(url, headers, payload, params=params)
        candidates = data.get("candidates") or []
        if not candidates:
            return ModelTurnResult()

        parts = (candidates[0].get("content") or {}).get("parts") or []
        text_parts: list[str] = []
        parsed_calls: list[ToolCall] = []
        for part in parts:
            if "text" in part:
                text_parts.append(part["text"])
            if "functionCall" in part:
                fc = part["functionCall"]
                parsed_calls.append(
                    ToolCall(
                        id=f"gcall_{uuid.uuid4().hex[:8]}",
                        name=fc.get("name") or "",
                        arguments=fc.get("args") or {},
                    )
                )
        return ModelTurnResult(text="".join(text_parts), tool_calls=parsed_calls)
