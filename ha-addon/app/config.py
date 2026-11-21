from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ProviderType = Literal["openai_compatible", "anthropic", "gemini"]

DEFAULT_ROUTING_PROMPT = (
    "You are DeepSeek v4.1-Flash acting as the primary fast assistant and smart router. "
    "Handle simple questions, casual conversation, quick calculations, and ALL Home Assistant "
    "device queries or service calls directly using the provided Home Assistant tools. "
    "Call escalate_to_model ONLY when the user request clearly benefits from a frontier specialist:\n"
    "- target='claude': complex coding, software architecture, nuanced creative/long-form writing, or deep multi-step logic.\n"
    "- target='gemini': broad research synthesis, large-context analysis, or multimodal/fact-heavy exploration.\n"
    "Never escalate simple Home Assistant commands or everyday questions."
)


class ModelSlotConfig(BaseModel):
    provider_type: ProviderType = "openai_compatible"
    base_url: str = "https://api.cheaperinference.com/v1"
    api_key: str = ""
    model_id: str = "deepseek-v4.1-flash"
    model_lock: bool = False


class ElevenLabsConfig(BaseModel):
    api_key: str = ""
    voice_id: str = "JBFqnCBsd6RMkjVDRZzb"
    model_id: str = "eleven_flash_v2_5"


class Settings(BaseModel):
    app_api_token: str = Field(default="change-me-to-a-long-random-secret")
    database_path: str = Field(default="/data/assistant.db")
    ha_base_url: str = Field(default="http://supervisor/core/api")
    ha_token: str = Field(default="")

    deepseek: ModelSlotConfig = Field(
        default_factory=lambda: ModelSlotConfig(
            provider_type="openai_compatible",
            base_url="https://api.cheaperinference.com/v1",
            api_key="",
            model_id="deepseek-v4.1-flash",
            model_lock=True,
        )
    )
    claude: ModelSlotConfig = Field(
        default_factory=lambda: ModelSlotConfig(
            provider_type="anthropic",
            base_url="https://api.anthropic.com",
            api_key="",
            model_id="claude-sonnet-4-20250514",
            model_lock=False,
        )
    )
    gemini: ModelSlotConfig = Field(
        default_factory=lambda: ModelSlotConfig(
            provider_type="gemini",
            base_url="https://generativelanguage.googleapis.com",
            api_key="",
            model_id="gemini-2.5-pro",
            model_lock=False,
        )
    )

    routing_prompt: str = Field(default=DEFAULT_ROUTING_PROMPT)
    sensitive_domains: list[str] = Field(
        default_factory=lambda: ["lock", "alarm_control_panel", "cover"]
    )
    sensitive_entities: list[str] = Field(default_factory=list)
    action_ttl_seconds: int = Field(default=120)
    elevenlabs: ElevenLabsConfig = Field(default_factory=ElevenLabsConfig)

    def get_slot(self, name: str) -> ModelSlotConfig:
        key = name.lower()
        if key == "deepseek":
            return self.deepseek
        if key == "claude":
            return self.claude
        if key == "gemini":
            return self.gemini
        raise ValueError(f"Unknown model slot: {name}")


def load_settings(options_path: str | Path | None = None) -> Settings:
    """Load settings from HAOS /data/options.json with env var fallbacks."""
    candidate_path = Path(
        options_path or os.environ.get("HA_OPTIONS_PATH", "/data/options.json")
    )
    raw: dict = {}
    if candidate_path.exists():
        try:
            raw = json.loads(candidate_path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}

    if "DATABASE_PATH" in os.environ:
        raw["database_path"] = os.environ["DATABASE_PATH"]
    elif not Path("/data").exists() and "database_path" not in raw:
        raw["database_path"] = "./assistant.db"

    if "APP_API_TOKEN" in os.environ:
        raw["app_api_token"] = os.environ["APP_API_TOKEN"]

    ha_token = (
        os.environ.get("HA_TOKEN")
        or os.environ.get("SUPERVISOR_TOKEN")
        or raw.get("ha_token", "")
    )
    raw["ha_token"] = ha_token

    if "HA_BASE_URL" in os.environ:
        raw["ha_base_url"] = os.environ["HA_BASE_URL"]

    for slot in ("deepseek", "claude", "gemini"):
        slot_upper = slot.upper()
        slot_dict = dict(raw.get(slot) or {})
        if f"{slot_upper}_API_KEY" in os.environ:
            slot_dict["api_key"] = os.environ[f"{slot_upper}_API_KEY"]
        if f"{slot_upper}_BASE_URL" in os.environ:
            slot_dict["base_url"] = os.environ[f"{slot_upper}_BASE_URL"]
        if f"{slot_upper}_MODEL_ID" in os.environ:
            slot_dict["model_id"] = os.environ[f"{slot_upper}_MODEL_ID"]
        if f"{slot_upper}_PROVIDER_TYPE" in os.environ:
            slot_dict["provider_type"] = os.environ[f"{slot_upper}_PROVIDER_TYPE"]
        if slot_dict:
            raw[slot] = slot_dict

    el_dict = dict(raw.get("elevenlabs") or {})
    if "ELEVENLABS_API_KEY" in os.environ:
        el_dict["api_key"] = os.environ["ELEVENLABS_API_KEY"]
    if "ELEVENLABS_VOICE_ID" in os.environ:
        el_dict["voice_id"] = os.environ["ELEVENLABS_VOICE_ID"]
    if "ELEVENLABS_MODEL_ID" in os.environ:
        el_dict["model_id"] = os.environ["ELEVENLABS_MODEL_ID"]
    if el_dict:
        raw["elevenlabs"] = el_dict

    return Settings.model_validate(raw)
