"""SQLite 저장소. 세션 상태와 대화 기록을 저장한다."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone


class Store:
    def __init__(self, path: str = "seoa.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
            """
        )
        self.conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_session(self, state: dict) -> str:
        sid = uuid.uuid4().hex
        now = self._now()
        self.conn.execute(
            "INSERT INTO sessions (id, state, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (sid, json.dumps(state, ensure_ascii=False), now, now),
        )
        self.conn.commit()
        return sid

    def get_state(self, sid: str) -> dict | None:
        row = self.conn.execute("SELECT state FROM sessions WHERE id = ?", (sid,)).fetchone()
        return json.loads(row["state"]) if row else None

    def save_state(self, sid: str, state: dict) -> None:
        self.conn.execute(
            "UPDATE sessions SET state = ?, updated_at = ? WHERE id = ?",
            (json.dumps(state, ensure_ascii=False), self._now(), sid),
        )
        self.conn.commit()

    def add_message(self, sid: str, role: str, content: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (sid, role, content, self._now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def messages_after(self, sid: str, after_id: int = 0) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, role, content FROM messages WHERE session_id = ? AND id > ? ORDER BY id",
            (sid, after_id),
        ).fetchall()
        return [dict(r) for r in rows]
