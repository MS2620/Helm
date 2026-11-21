from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    async def init(self) -> None:
        parent = Path(self.db_path).parent
        parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    model_used TEXT,
                    routing_reason TEXT,
                    tool_events_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_messages_thread_created
                    ON messages(thread_id, created_at);

                CREATE TABLE IF NOT EXISTS pending_actions (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                    message_id TEXT,
                    domain TEXT NOT NULL,
                    service TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    service_data_json TEXT NOT NULL DEFAULT '{}',
                    summary TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_pending_actions_thread
                    ON pending_actions(thread_id, created_at);
                """
            )
            db.commit()

    async def create_thread(self, title: str = "New Chat") -> dict[str, Any]:
        thread_id = str(uuid.uuid4())
        now = utc_now_iso()
        with closing(self._connect()) as db:
            db.execute(
                "INSERT INTO threads (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (thread_id, title, now, now),
            )
            db.commit()
        return {
            "id": thread_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
        }

    async def list_threads(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            cursor = db.execute(
                "SELECT id, title, created_at, updated_at FROM threads ORDER BY updated_at DESC"
            )
            return [dict(r) for r in cursor.fetchall()]

    async def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as db:
            cursor = db.execute(
                "SELECT id, title, created_at, updated_at FROM threads WHERE id = ?",
                (thread_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    async def update_thread_title(self, thread_id: str, title: str) -> dict[str, Any] | None:
        now = utc_now_iso()
        with closing(self._connect()) as db:
            db.execute(
                "UPDATE threads SET title = ?, updated_at = ? WHERE id = ?",
                (title, now, thread_id),
            )
            db.commit()
        return await self.get_thread(thread_id)

    async def delete_thread(self, thread_id: str) -> bool:
        with closing(self._connect()) as db:
            cursor = db.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
            db.commit()
            return cursor.rowcount > 0

    async def add_message(
        self,
        thread_id: str,
        role: str,
        content: str,
        model_used: str | None = None,
        routing_reason: str | None = None,
        tool_events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        msg_id = str(uuid.uuid4())
        now = utc_now_iso()
        tool_json = json.dumps(tool_events or [])
        with closing(self._connect()) as db:
            db.execute(
                """
                INSERT INTO messages
                (id, thread_id, role, content, model_used, routing_reason, tool_events_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (msg_id, thread_id, role, content, model_used, routing_reason, tool_json, now),
            )
            db.execute(
                "UPDATE threads SET updated_at = ? WHERE id = ?",
                (now, thread_id),
            )
            db.commit()
        return {
            "id": msg_id,
            "thread_id": thread_id,
            "role": role,
            "content": content,
            "model_used": model_used,
            "routing_reason": routing_reason,
            "tool_events": tool_events or [],
            "created_at": now,
        }

    async def list_messages(self, thread_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            cursor = db.execute(
                """
                SELECT id, thread_id, role, content, model_used, routing_reason, tool_events_json, created_at
                FROM messages
                WHERE thread_id = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (thread_id, limit),
            )
            result = []
            for r in cursor.fetchall():
                d = dict(r)
                d["tool_events"] = json.loads(d.pop("tool_events_json", "[]"))
                result.append(d)
            return result

    async def create_pending_action(
        self,
        thread_id: str,
        domain: str,
        service: str,
        entity_id: str,
        service_data: dict[str, Any],
        summary: str,
        ttl_seconds: int = 120,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        action_id = str(uuid.uuid4())
        now_dt = datetime.now(timezone.utc)
        expires_dt = now_dt + timedelta(seconds=ttl_seconds)
        now_iso = now_dt.isoformat()
        expires_iso = expires_dt.isoformat()
        data_json = json.dumps(service_data or {})

        with closing(self._connect()) as db:
            db.execute(
                """
                INSERT INTO pending_actions
                (id, thread_id, message_id, domain, service, entity_id, service_data_json, summary, status, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    action_id,
                    thread_id,
                    message_id,
                    domain,
                    service,
                    entity_id,
                    data_json,
                    summary,
                    now_iso,
                    expires_iso,
                ),
            )
            db.commit()

        return {
            "id": action_id,
            "thread_id": thread_id,
            "message_id": message_id,
            "domain": domain,
            "service": service,
            "entity_id": entity_id,
            "service_data": service_data or {},
            "summary": summary,
            "status": "pending",
            "created_at": now_iso,
            "expires_at": expires_iso,
        }

    async def get_pending_action(self, action_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as db:
            cursor = db.execute(
                "SELECT * FROM pending_actions WHERE id = ?",
                (action_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._deserialize_action(dict(row))

    async def list_pending_actions(self, thread_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            cursor = db.execute(
                "SELECT * FROM pending_actions WHERE thread_id = ? ORDER BY created_at ASC",
                (thread_id,),
            )
            return [self._deserialize_action(dict(r)) for r in cursor.fetchall()]

    async def update_pending_action_status(
        self,
        action_id: str,
        status: str,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        result_json = json.dumps(result) if result is not None else None
        with closing(self._connect()) as db:
            db.execute(
                "UPDATE pending_actions SET status = ?, result_json = ? WHERE id = ?",
                (status, result_json, action_id),
            )
            db.commit()
        return await self.get_pending_action(action_id)

    @staticmethod
    def _deserialize_action(d: dict[str, Any]) -> dict[str, Any]:
        d["service_data"] = json.loads(d.pop("service_data_json", "{}"))
        res_raw = d.pop("result_json", None)
        d["result"] = json.loads(res_raw) if res_raw else None
        return d
