from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status

from app.config import Settings
from app.database import Database
from app.ha_client import HomeAssistantClient


def normalize_entity_ids(entity_id: str | list[str] | None) -> list[str]:
    if not entity_id:
        return []
    if isinstance(entity_id, str):
        return [e.strip() for e in entity_id.split(",") if e.strip()]
    return [str(e).strip() for e in entity_id if str(e).strip()]


def is_sensitive_action(
    domain: str,
    entity_id: str | list[str] | None,
    settings: Settings,
) -> bool:
    """Check whether a Home Assistant service call requires Face ID confirmation."""
    clean_domain = (domain or "").strip().lower()
    sensitive_domains = {d.strip().lower() for d in settings.sensitive_domains if d.strip()}
    if clean_domain in sensitive_domains:
        return True

    sensitive_entities = {e.strip().lower() for e in settings.sensitive_entities if e.strip()}
    for eid in normalize_entity_ids(entity_id):
        eid_lower = eid.lower()
        if eid_lower in sensitive_entities:
            return True
        if "." in eid_lower and eid_lower.split(".", 1)[0] in sensitive_domains:
            return True

    return False


def format_action_summary(
    domain: str,
    service: str,
    entity_id: str | list[str] | None,
    service_data: dict[str, Any] | None = None,
) -> str:
    targets = ", ".join(normalize_entity_ids(entity_id)) or "system"
    clean_service = service.replace("_", " ").title()
    clean_domain = domain.replace("_", " ").title()
    extra = ""
    if service_data:
        filtered = {k: v for k, v in service_data.items() if k != "entity_id"}
        if filtered:
            pairs = ", ".join(f"{k}={v}" for k, v in filtered.items())
            extra = f" ({pairs})"
    return f"{clean_domain}: {clean_service} → {targets}{extra}"


async def confirm_pending_action(
    action_id: str,
    db: Database,
    ha_client: HomeAssistantClient,
) -> dict[str, Any]:
    action = await db.get_pending_action(action_id)
    if not action:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pending action not found.",
        )

    if action["status"] != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Action is already {action['status']}.",
        )

    expires_at = datetime.fromisoformat(action["expires_at"])
    now = datetime.now(timezone.utc)
    if now >= expires_at:
        await db.update_pending_action_status(action_id, "expired")
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Pending action has expired. Please request the action again.",
        )

    entity_list = normalize_entity_ids(action["entity_id"])
    target_entity: str | list[str] | None = (
        entity_list[0] if len(entity_list) == 1 else (entity_list or None)
    )

    ha_result = await ha_client.call_service(
        domain=action["domain"],
        service=action["service"],
        entity_id=target_entity,
        service_data=action.get("service_data") or {},
    )
    updated = await db.update_pending_action_status(
        action_id,
        "executed",
        result={"ha_response": ha_result},
    )
    await db.add_message(
        thread_id=action["thread_id"],
        role="assistant",
        content=f"Confirmed with Face ID and executed: **{action['summary']}**.",
        model_used="system",
        tool_events=[
            {
                "tool": "ha_call_service",
                "status": "executed_after_face_id",
                "action_id": action_id,
                "summary": action["summary"],
            }
        ],
    )
    return updated or action


async def cancel_pending_action(
    action_id: str,
    db: Database,
) -> dict[str, Any]:
    action = await db.get_pending_action(action_id)
    if not action:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pending action not found.",
        )

    if action["status"] != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Action is already {action['status']}.",
        )

    updated = await db.update_pending_action_status(action_id, "cancelled")
    await db.add_message(
        thread_id=action["thread_id"],
        role="assistant",
        content=f"Cancelled sensitive action: **{action['summary']}**.",
        model_used="system",
        tool_events=[
            {
                "tool": "ha_call_service",
                "status": "cancelled",
                "action_id": action_id,
                "summary": action["summary"],
            }
        ],
    )
    return updated or action
