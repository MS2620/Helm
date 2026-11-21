from __future__ import annotations

import json
from typing import Any, AsyncGenerator

from app.config import Settings
from app.database import Database
from app.ha_client import HomeAssistantClient
from app.providers import LLMProviderClient, ToolCall
from app.safety import format_action_summary, is_sensitive_action, normalize_entity_ids

HA_TOOLS: list[dict[str, Any]] = [
    {
        "name": "ha_search_entities",
        "description": "Search Home Assistant entities by name, entity_id, or domain (e.g. light, switch, lock, climate, sensor, script, scene, calendar).",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keyword matching entity_id or friendly_name (e.g. 'living room', 'front door').",
                },
                "domain": {
                    "type": "string",
                    "description": "Optional domain filter (e.g. 'light', 'lock', 'climate', 'cover', 'sensor', 'scene', 'script').",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max entities to return (default 25).",
                },
            },
        },
    },
    {
        "name": "ha_get_states",
        "description": "Get live state and attributes for one or more Home Assistant entity_ids.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of exact entity_ids (e.g. ['light.kitchen', 'climate.thermostat']).",
                }
            },
            "required": ["entity_ids"],
        },
    },
    {
        "name": "ha_get_calendar_events",
        "description": "Fetch upcoming events from a Home Assistant calendar entity.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "Calendar entity_id (e.g. 'calendar.personal').",
                },
                "start_iso": {
                    "type": "string",
                    "description": "Optional ISO 8601 start timestamp.",
                },
                "end_iso": {
                    "type": "string",
                    "description": "Optional ISO 8601 end timestamp.",
                },
            },
            "required": ["entity_id"],
        },
    },
    {
        "name": "ha_list_services",
        "description": "List available Home Assistant services and their accepted fields for a given domain.",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Domain to inspect (e.g. 'light', 'climate', 'lock', 'cover', 'script', 'scene').",
                }
            },
        },
    },
    {
        "name": "ha_call_service",
        "description": "Execute a Home Assistant service to control a device, scene, script, or automation. Sensitive domains (locks, alarms, garage covers) automatically queue a Face ID confirmation card on the user's iPhone.",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Service domain (e.g. 'light', 'switch', 'lock', 'cover', 'climate', 'scene', 'script').",
                },
                "service": {
                    "type": "string",
                    "description": "Service name (e.g. 'turn_on', 'turn_off', 'unlock', 'lock', 'open_cover', 'set_temperature').",
                },
                "entity_id": {
                    "type": "string",
                    "description": "Target entity_id (or comma-separated entity_ids), e.g. 'light.living_room' or 'lock.front_door'.",
                },
                "service_data": {
                    "type": "object",
                    "description": "Optional extra service parameters (e.g. {'brightness_pct': 80} or {'temperature': 21}).",
                },
            },
            "required": ["domain", "service"],
        },
    },
]

ESCALATE_TOOL: dict[str, Any] = {
    "name": "escalate_to_model",
    "description": "Escalate this user request from DeepSeek v4.1-Flash to Claude or Gemini when specialist frontier reasoning, complex coding, or deep synthesis is required.",
    "parameters": {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "enum": ["claude", "gemini"],
                "description": "Target model ('claude' or 'gemini').",
            },
            "reason": {
                "type": "string",
                "description": "Short explanation of why this request is being escalated.",
            },
        },
        "required": ["target", "reason"],
    },
}


def format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class RouterEngine:
    """Orchestrates fast triage with DeepSeek v4.1-Flash, escalation to Claude/Gemini, and HA tool execution."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        ha_client: HomeAssistantClient,
        llm_client: LLMProviderClient,
    ) -> None:
        self.settings = settings
        self.db = db
        self.ha_client = ha_client
        self.llm_client = llm_client

    async def process_user_message(
        self,
        thread_id: str,
        user_content: str,
        model_override: str = "auto",
    ) -> AsyncGenerator[str, None]:
        thread = await self.db.get_thread(thread_id)
        if not thread:
            yield format_sse("error", {"message": "Thread not found."})
            return

        prior_messages = await self.db.list_messages(thread_id, limit=20)
        is_first_message = len(prior_messages) == 0

        await self.db.add_message(
            thread_id=thread_id,
            role="user",
            content=user_content,
        )

        if is_first_message and thread["title"] in ("New Chat", ""):
            short_title = user_content.strip().replace("\n", " ")[:48]
            if len(user_content.strip()) > 48:
                short_title += "…"
            if short_title:
                await self.db.update_thread_title(thread_id, short_title)

        conversation: list[dict[str, Any]] = [
            {"role": m["role"], "content": m["content"]}
            for m in prior_messages
            if m["role"] in ("user", "assistant")
        ]
        conversation.append({"role": "user", "content": user_content})

        choice = (model_override or "auto").strip().lower()
        active_model = "deepseek" if choice == "auto" else choice
        if active_model not in ("deepseek", "claude", "gemini"):
            active_model = "deepseek"

        escalated = False
        routing_reason = (
            "Manual model selection" if choice != "auto" else "Handled by DeepSeek v4.1-Flash"
        )

        yield format_sse(
            "routing",
            {
                "model": active_model,
                "escalated": False,
                "reason": routing_reason,
            },
        )

        tool_events: list[dict[str, Any]] = []
        final_text = ""

        try:
            for _step in range(6):
                slot_cfg = self.settings.get_slot(active_model)
                allow_escalation = choice == "auto" and active_model == "deepseek" and not escalated
                tools = list(HA_TOOLS) + ([ESCALATE_TOOL] if allow_escalation else [])
                system_prompt = self._build_system_prompt(active_model, allow_escalation)

                turn = await self.llm_client.complete_turn(
                    slot_config=slot_cfg,
                    system_prompt=system_prompt,
                    messages=conversation,
                    tools=tools,
                )

                if not turn.tool_calls:
                    final_text = turn.text or "Done."
                    break

                # Check if DeepSeek asked to escalate to Claude or Gemini
                escalation_call = next(
                    (tc for tc in turn.tool_calls if tc.name == "escalate_to_model"),
                    None,
                )
                if escalation_call is not None:
                    target = str(escalation_call.arguments.get("target", "claude")).lower()
                    if target not in ("claude", "gemini"):
                        target = "claude"
                    routing_reason = str(
                        escalation_call.arguments.get("reason")
                        or f"Escalated to {target.title()}"
                    )
                    active_model = target
                    escalated = True
                    yield format_sse(
                        "routing",
                        {
                            "model": active_model,
                            "escalated": True,
                            "reason": routing_reason,
                        },
                    )
                    continue

                # Otherwise execute Home Assistant tools
                conversation.append(
                    {
                        "role": "assistant",
                        "content": turn.text or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "name": tc.name,
                                "arguments": tc.arguments,
                            }
                            for tc in turn.tool_calls
                        ],
                    }
                )

                for tc in turn.tool_calls:
                    yield format_sse(
                        "tool_call",
                        {
                            "tool": tc.name,
                            "status": "running",
                            "arguments": tc.arguments,
                        },
                    )
                    tool_output, emitted_action = await self._execute_ha_tool(
                        thread_id=thread_id,
                        tool_call=tc,
                    )
                    if emitted_action is not None:
                        tool_events.append(
                            {
                                "tool": tc.name,
                                "status": "pending_confirmation",
                                "action": emitted_action,
                            }
                        )
                        yield format_sse("action_required", emitted_action)
                    else:
                        tool_events.append(
                            {
                                "tool": tc.name,
                                "status": "completed",
                                "arguments": tc.arguments,
                            }
                        )
                        yield format_sse(
                            "tool_call",
                            {
                                "tool": tc.name,
                                "status": "completed",
                            },
                        )

                    conversation.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": tc.name,
                            "content": json.dumps(tool_output),
                        }
                    )
            else:
                if not final_text:
                    final_text = "Completed the requested actions."

        except Exception as exc:
            yield format_sse("error", {"message": str(exc)})
            return

        # Stream the final text in responsive chunks for smooth iOS rendering
        for chunk in self._chunk_text(final_text):
            yield format_sse("delta", {"text": chunk})

        saved_msg = await self.db.add_message(
            thread_id=thread_id,
            role="assistant",
            content=final_text,
            model_used=active_model,
            routing_reason=routing_reason,
            tool_events=tool_events,
        )

        yield format_sse(
            "done",
            {
                "message_id": saved_msg["id"],
                "thread_id": thread_id,
                "model": active_model,
                "escalated": escalated,
                "routing_reason": routing_reason,
                "full_text": final_text,
                "tool_events": tool_events,
            },
        )

    def _build_system_prompt(self, active_model: str, allow_escalation: bool) -> str:
        ha_instructions = (
            "\n\nHome Assistant Guidelines:\n"
            "- Use `ha_search_entities` first if you do not know the exact `entity_id`.\n"
            "- Use `ha_get_states` to inspect current states or attributes.\n"
            "- Use `ha_call_service` to control devices, scenes, or scripts.\n"
            "- If a tool returns `PENDING_FACE_ID_APPROVAL`, clearly tell the user that the action "
            "is ready and waiting for their Face ID confirmation card on screen."
        )
        if active_model == "deepseek" and allow_escalation:
            return f"{self.settings.routing_prompt}{ha_instructions}"
        return (
            f"You are {active_model.title()}, a helpful AI assistant with full live access to the user's "
            f"Home Assistant smart home.{ha_instructions}"
        )

    async def _execute_ha_tool(
        self,
        thread_id: str,
        tool_call: ToolCall,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        name = tool_call.name
        args = tool_call.arguments or {}

        try:
            if name == "ha_search_entities":
                matches = await self.ha_client.search_entities(
                    query=str(args.get("query") or ""),
                    domain=str(args.get("domain") or ""),
                    limit=int(args.get("limit") or 25),
                )
                return {"entities": matches}, None

            if name == "ha_get_states":
                raw_ids = args.get("entity_ids") or []
                entity_ids = normalize_entity_ids(raw_ids)
                states = await self.ha_client.get_states(entity_ids)
                return {"states": states}, None

            if name == "ha_get_calendar_events":
                events = await self.ha_client.get_calendar_events(
                    entity_id=str(args.get("entity_id") or ""),
                    start_iso=args.get("start_iso"),
                    end_iso=args.get("end_iso"),
                )
                return {"events": events}, None

            if name == "ha_list_services":
                services = await self.ha_client.list_services(
                    domain=str(args.get("domain") or ""),
                )
                return {"services": services}, None

            if name == "ha_call_service":
                domain = str(args.get("domain") or "").strip().lower()
                service = str(args.get("service") or "").strip().lower()
                entity_id = args.get("entity_id")
                service_data = dict(args.get("service_data") or {})

                if is_sensitive_action(domain, entity_id, self.settings):
                    entity_str = ", ".join(normalize_entity_ids(entity_id))
                    summary = format_action_summary(domain, service, entity_id, service_data)
                    pending = await self.db.create_pending_action(
                        thread_id=thread_id,
                        domain=domain,
                        service=service,
                        entity_id=entity_str,
                        service_data=service_data,
                        summary=summary,
                        ttl_seconds=self.settings.action_ttl_seconds,
                    )
                    return (
                        {
                            "status": "PENDING_FACE_ID_APPROVAL",
                            "action_id": pending["id"],
                            "summary": summary,
                            "message": (
                                "This sensitive Home Assistant action requires Face ID confirmation "
                                "on the user's iPhone before execution."
                            ),
                        },
                        pending,
                    )

                result = await self.ha_client.call_service(
                    domain=domain,
                    service=service,
                    entity_id=entity_id,
                    service_data=service_data,
                )
                return {"status": "executed", "result": result}, None

            return {"error": f"Unknown tool: {name}"}, None

        except Exception as exc:
            return {"error": str(exc)}, None

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 32) -> list[str]:
        if not text:
            return []
        return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
