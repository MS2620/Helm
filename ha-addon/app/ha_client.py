from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


class HomeAssistantClient:
    """Async client for Home Assistant Core API (via Supervisor or direct URL)."""

    def __init__(
        self,
        base_url: str = "http://supervisor/core/api",
        token: str = "",
        http_client: httpx.AsyncClient | None = None,
        cache_ttl_seconds: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._external_client = http_client
        self._cache_ttl = cache_ttl_seconds
        self._states_cache: tuple[float, list[dict[str, Any]]] | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if self._external_client is not None:
            resp = await self._external_client.request(
                method,
                url,
                headers=self._headers(),
                params=params,
                json=json_body,
                timeout=20.0,
            )
            resp.raise_for_status()
            return resp.json() if resp.content else {}

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.request(
                method,
                url,
                headers=self._headers(),
                params=params,
                json=json_body,
            )
            resp.raise_for_status()
            return resp.json() if resp.content else {}

    async def _fetch_all_states(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        now = time.monotonic()
        if (
            not force_refresh
            and self._states_cache is not None
            and (now - self._states_cache[0]) < self._cache_ttl
        ):
            return self._states_cache[1]

        data = await self._request("GET", "/states")
        states = data if isinstance(data, list) else []
        self._states_cache = (now, states)
        return states

    async def search_entities(
        self,
        query: str = "",
        domain: str = "",
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Search Home Assistant entities by entity_id, friendly_name, or domain."""
        states = await self._fetch_all_states()
        q = (query or "").strip().lower()
        dom = (domain or "").strip().lower()
        matches: list[dict[str, Any]] = []

        for item in states:
            entity_id: str = item.get("entity_id", "")
            item_domain = entity_id.split(".", 1)[0].lower() if "." in entity_id else ""
            if dom and item_domain != dom:
                continue

            attrs = item.get("attributes") or {}
            friendly_name = str(attrs.get("friendly_name", ""))
            haystack = f"{entity_id} {friendly_name}".lower()
            if q and q not in haystack:
                continue

            matches.append(
                {
                    "entity_id": entity_id,
                    "domain": item_domain,
                    "friendly_name": friendly_name or entity_id,
                    "state": item.get("state"),
                    "device_class": attrs.get("device_class"),
                    "unit_of_measurement": attrs.get("unit_of_measurement"),
                }
            )
            if len(matches) >= max(1, min(limit, 100)):
                break

        return matches

    async def get_states(self, entity_ids: list[str]) -> list[dict[str, Any]]:
        """Get live state and attributes for specific entity_ids."""
        results: list[dict[str, Any]] = []
        for eid in entity_ids:
            clean_id = eid.strip()
            if not clean_id:
                continue
            try:
                data = await self._request("GET", f"/states/{clean_id}")
                results.append(
                    {
                        "entity_id": data.get("entity_id", clean_id),
                        "state": data.get("state"),
                        "attributes": data.get("attributes", {}),
                        "last_changed": data.get("last_changed"),
                        "last_updated": data.get("last_updated"),
                    }
                )
            except httpx.HTTPStatusError as exc:
                results.append(
                    {
                        "entity_id": clean_id,
                        "error": f"HTTP {exc.response.status_code}",
                    }
                )
        return results

    async def get_calendar_events(
        self,
        entity_id: str,
        start_iso: str | None = None,
        end_iso: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch calendar events from a calendar.<name> entity."""
        now = datetime.now(timezone.utc)
        start = start_iso or now.isoformat()
        end = end_iso or (now + timedelta(days=7)).isoformat()
        data = await self._request(
            "GET",
            f"/calendars/{entity_id.strip()}",
            params={"start": start, "end": end},
        )
        return data if isinstance(data, list) else []

    async def list_services(self, domain: str = "") -> list[dict[str, Any]]:
        """List available Home Assistant services (optionally filtered by domain)."""
        data = await self._request("GET", "/services")
        if not isinstance(data, list):
            return []
        dom = (domain or "").strip().lower()
        results: list[dict[str, Any]] = []
        for entry in data:
            entry_domain = str(entry.get("domain", "")).lower()
            if dom and entry_domain != dom:
                continue
            services_dict = entry.get("services") or {}
            service_summaries = {}
            for svc_name, svc_meta in services_dict.items():
                fields = list((svc_meta.get("fields") or {}).keys())
                service_summaries[svc_name] = {
                    "name": svc_meta.get("name", svc_name),
                    "description": svc_meta.get("description", ""),
                    "fields": fields,
                }
            results.append({"domain": entry_domain, "services": service_summaries})
        return results

    async def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str | list[str] | None = None,
        service_data: dict[str, Any] | None = None,
    ) -> Any:
        """Call a Home Assistant service (e.g. light.turn_on, script.turn_on, scene.turn_on)."""
        payload: dict[str, Any] = dict(service_data or {})
        if entity_id:
            payload["entity_id"] = entity_id
        self._states_cache = None  # Invalidate cached states after mutation
        return await self._request(
            "POST",
            f"/services/{domain.strip()}/{service.strip()}",
            json_body=payload,
        )
