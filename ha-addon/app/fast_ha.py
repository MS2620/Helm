from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.database import Database
from app.ha_client import HomeAssistantClient
from app.safety import format_action_summary, is_sensitive_action

CONTROLLABLE_DOMAINS = {
    "light",
    "switch",
    "fan",
    "Input_boolean",
    "input_boolean",
    "scene",
    "script",
    "automation",
    "media_player",
    "climate",
    "lock",
    "cover",
    "alarm_control_panel",
    "vacuum",
    "humidifier",
    "water_heater",
}


@dataclass
class FastHAResult:
    handled: bool
    reply_text: str = ""
    pending_action: dict[str, Any] | None = None
    tool_events: list[dict[str, Any]] | None = None


def _normalize_text(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^\w\s%°.]", " ", s)
    s = re.sub(r"\b(the|my|please|can you|could you| right now|for me|a|an)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _singularize(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _normalize_tokens(s: str) -> list[str]:
    norm = _normalize_text(s)
    return [_singularize(tok) for tok in norm.split() if tok]


def _match_entities(
    target_phrase: str,
    states: list[dict[str, Any]],
    preferred_domains: set[str] | None = None,
) -> list[dict[str, Any]]:
    target_tokens = _normalize_tokens(target_phrase)
    if not target_tokens:
        return []
    target_joined = " ".join(target_tokens)

    scored: list[tuple[int, dict[str, Any]]] = []
    for item in states:
        entity_id: str = item.get("entity_id", "")
        if "." not in entity_id:
            continue
        domain, object_id = entity_id.split(".", 1)
        domain = domain.lower()

        if preferred_domains and domain not in preferred_domains:
            continue

        attrs = item.get("attributes") or {}
        friendly = str(attrs.get("friendly_name") or object_id.replace("_", " "))
        friendly_tokens = _normalize_tokens(friendly)
        object_tokens = _normalize_tokens(object_id.replace("_", " "))
        friendly_joined = " ".join(friendly_tokens)
        object_joined = " ".join(object_tokens)

        score = 0
        if target_phrase.strip().lower() == entity_id.lower():
            score = 1000
        elif target_joined == friendly_joined or target_joined == object_joined:
            score = 900
        elif all(t in friendly_tokens for t in target_tokens):
            # Prefer tighter matches with fewer extra tokens
            score = 750 - max(0, len(friendly_tokens) - len(target_tokens)) * 20
        elif all(t in object_tokens for t in target_tokens):
            score = 700 - max(0, len(object_tokens) - len(target_tokens)) * 20
        elif target_joined in friendly_joined or friendly_joined in target_joined:
            if len(target_joined) >= 4 and len(friendly_joined) >= 4:
                score = 550

        if score > 0:
            if preferred_domains and domain in preferred_domains:
                score += 50
            scored.append((score, item))

    if not scored:
        return []

    scored.sort(key=lambda pair: pair[0], reverse=True)
    top_score = scored[0][0]
    if top_score < 550:
        return []

    # Return the top match (or multiple entities if they tied at the exact same name/room score)
    top_matches = [item for sc, item in scored if sc >= top_score - 10]
    return top_matches[:4]


async def try_fast_ha_command(
    user_content: str,
    ha_client: HomeAssistantClient,
    db: Database,
    settings: Settings,
    thread_id: str,
) -> FastHAResult:
    """Attempt to resolve simple, unambiguous Home Assistant commands in <150ms without calling an LLM."""
    text = user_content.strip()
    if not text or len(text) > 120:
        return FastHAResult(handled=False)

    # Do not fast-path compound or analytical requests
    lower = text.lower()
    if any(
        kw in lower
        for kw in (
            " and ",
            " then ",
            " because ",
            " if ",
            " unless ",
            "script to",
            "write ",
            "explain ",
            "code ",
            "why ",
            "how do i",
        )
    ):
        return FastHAResult(handled=False)

    clean = _normalize_text(text)

    # Pattern 1: Turn on / off / toggle (+ optional brightness %)
    m_power = re.match(
        r"^(?:(turn|switch)\s+(on|off)|(toggle)|(enable|disable))\s+(.+?)(?:\s+(?:to|at)\s+(\d{1,3})\s*%?)?$",
        clean,
    )
    # Pattern 2: Set brightness % ("set office light to 60%", "dim living room light to 30%")
    m_bright = re.match(
        r"^(?:set|dim|brighten)\s+(.+?)(?:\s+brightness)?\s+(?:to|at)\s+(\d{1,3})\s*%$",
        clean,
    )
    # Pattern 3: Set temperature ("set thermostat to 21", "set bedroom ac to 72 degrees")
    m_temp = re.match(
        r"^set\s+(.+?)(?:\s+temperature|\s+temp)?\s+(?:to|at)\s+(\d{1,3}(?:\.\d+)?)\s*(?:degrees|degree|°|c|f)?$",
        clean,
    )
    # Pattern 4: Lock / Unlock
    m_lock = re.match(r"^(lock|unlock)\s+(.+)$", clean)
    # Pattern 5: Open / Close cover/garage/blinds
    m_cover = re.match(r"^(open|close)\s+(.+)$", clean)
    # Pattern 6: Activate / run scene or script
    m_scene = re.match(r"^(?:activate|run|start|trigger)\s+(.+)$", clean)
    # Pattern 7: Status / sensor query ("what is the temperature of bedroom", "is front door locked")
    m_query = re.match(
        r"^(?:what\s+is|whats|what\s+s|check|status\s+of|state\s+of|temperature\s+of|is)\s+(.+?)(?:\s+(on|off|locked|unlocked|open|closed|status|state|temperature|humidity))?$",
        clean,
    )

    if not any((m_power, m_bright, m_temp, m_lock, m_cover, m_scene, m_query)):
        return FastHAResult(handled=False)

    try:
        states = await ha_client._fetch_all_states()
    except Exception:
        return FastHAResult(handled=False)

    if not states:
        return FastHAResult(handled=False)

    # Resolve intent -> (domain, service, target_phrase, service_data, preferred_domains)
    intent_type = "service"
    service = ""
    target_phrase = ""
    service_data: dict[str, Any] = {}
    preferred_domains: set[str] | None = None

    if m_power:
        _, on_off, toggle_kw, en_dis, target_phrase, pct_str = m_power.groups()
        if toggle_kw:
            service = "toggle"
        elif on_off == "off" or en_dis == "disable":
            service = "turn_off"
        else:
            service = "turn_on"
        if pct_str and service == "turn_on":
            service_data["brightness_pct"] = max(0, min(100, int(pct_str)))
            preferred_domains = {"light"}
        else:
            preferred_domains = {
                "light",
                "switch",
                "fan",
                "input_boolean",
                "scene",
                "script",
                "automation",
                "media_player",
                "climate",
                "humidifier",
            }
    elif m_bright:
        target_phrase, pct_str = m_bright.groups()
        pct = max(0, min(100, int(pct_str)))
        service = "turn_on" if pct > 0 else "turn_off"
        if pct > 0:
            service_data["brightness_pct"] = pct
        preferred_domains = {"light"}
    elif m_temp:
        target_phrase, temp_str = m_temp.groups()
        service = "set_temperature"
        service_data["temperature"] = float(temp_str)
        preferred_domains = {"climate", "water_heater"}
    elif m_lock:
        verb, target_phrase = m_lock.groups()
        service = "lock" if verb == "lock" else "unlock"
        preferred_domains = {"lock"}
    elif m_cover:
        verb, target_phrase = m_cover.groups()
        service = "open_cover" if verb == "open" else "close_cover"
        preferred_domains = {"cover"}
    elif m_scene:
        target_phrase = m_scene.group(1)
        service = "turn_on"
        preferred_domains = {"scene", "script", "automation"}
    elif m_query:
        intent_type = "query"
        target_phrase = m_query.group(1)
        preferred_domains = None

    matches = _match_entities(target_phrase, states, preferred_domains=preferred_domains)
    if not matches and preferred_domains is not None:
        # Fallback without domain restriction in case entity is under another domain
        matches = _match_entities(target_phrase, states, preferred_domains=CONTROLLABLE_DOMAINS)
    if not matches:
        return FastHAResult(handled=False)

    if intent_type == "query":
        summaries = []
        for item in matches:
            eid = item.get("entity_id", "")
            attrs = item.get("attributes") or {}
            fname = attrs.get("friendly_name") or eid
            st = item.get("state", "unknown")
            unit = attrs.get("unit_of_measurement") or ""
            unit_str = f" {unit}" if unit else ""
            summaries.append(f"**{fname}** is currently **{st}{unit_str}**")
        return FastHAResult(
            handled=True,
            reply_text=". ".join(summaries) + ".",
            tool_events=[
                {
                    "tool": "ha_get_states",
                    "status": "completed",
                    "arguments": {"entity_ids": [m.get("entity_id") for m in matches]},
                }
            ],
        )

    # Execute service call on matched entity/entities (grouped by domain)
    first_eid = matches[0].get("entity_id", "")
    domain = first_eid.split(".", 1)[0].lower()
    same_domain_matches = [
        m for m in matches if m.get("entity_id", "").split(".", 1)[0].lower() == domain
    ]
    entity_ids = [m["entity_id"] for m in same_domain_matches]
    friendly_names = [
        str((m.get("attributes") or {}).get("friendly_name") or m["entity_id"])
        for m in same_domain_matches
    ]
    entity_arg: str | list[str] = entity_ids[0] if len(entity_ids) == 1 else entity_ids
    entity_str = ", ".join(entity_ids)

    # Adjust service if domain is lock/cover and user said "turn on/off"
    if domain == "lock" and service in ("turn_on", "turn_off"):
        service = "lock" if service == "turn_on" else "unlock"
    elif domain == "cover" and service in ("turn_on", "turn_off"):
        service = "open_cover" if service == "turn_on" else "close_cover"

    # Enforce Face ID Safety Gate!
    if is_sensitive_action(domain, entity_ids, settings):
        summary = format_action_summary(domain, service, entity_ids, service_data)
        pending = await db.create_pending_action(
            thread_id=thread_id,
            domain=domain,
            service=service,
            entity_id=entity_str,
            service_data=service_data,
            summary=summary,
            ttl_seconds=settings.action_ttl_seconds,
        )
        names_joined = ", ".join(friendly_names)
        clean_verb = service.replace("_cover", "").replace("_", " ")
        return FastHAResult(
            handled=True,
            reply_text=f"Ready to **{clean_verb} {names_joined}**. Please confirm with Face ID below.",
            pending_action=pending,
            tool_events=[
                {
                    "tool": "ha_call_service",
                    "status": "pending_confirmation",
                    "action": pending,
                }
            ],
        )

    await ha_client.call_service(
        domain=domain,
        service=service,
        entity_id=entity_arg,
        service_data=service_data,
    )
    names_joined = ", ".join(friendly_names)
    verb_past = {
        "turn_on": "Turned on",
        "turn_off": "Turned off",
        "toggle": "Toggled",
        "open_cover": "Opened",
        "close_cover": "Closed",
        "lock": "Locked",
        "unlock": "Unlocked",
        "set_temperature": "Set temperature for",
    }.get(service, f"Executed {service} on")

    extra_detail = ""
    if "brightness_pct" in service_data:
        extra_detail = f" at {service_data['brightness_pct']}%"
    elif "temperature" in service_data:
        extra_detail = f" to {service_data['temperature']}°"

    return FastHAResult(
        handled=True,
        reply_text=f"{verb_past} **{names_joined}**{extra_detail}.",
        tool_events=[
            {
                "tool": "ha_call_service",
                "status": "completed",
                "arguments": {
                    "domain": domain,
                    "service": service,
                    "entity_id": entity_arg,
                    "service_data": service_data,
                },
            }
        ],
    )
