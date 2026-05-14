"""SQLite-Log für ausgehende SIP-Anrufe.

Wird vom SIP-Client als ``on_history``-Callback aufgerufen, sobald ein
Anruf beendet (oder fehlgeschlagen) ist. Strukturell analog zu
``memory.py``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import aiosqlite


DB_PATH = Path(__file__).parent / "jarvis_calls.db"


def init_database() -> None:
    """Legt die SQLite-Tabelle für die Anruf-Historie an (sync, beim Start)."""

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                call_id TEXT,
                contact_name TEXT,
                target TEXT NOT NULL,
                state TEXT NOT NULL,
                error TEXT,
                started_at REAL NOT NULL,
                ended_at REAL,
                duration_seconds REAL,
                created_at TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_calls_started ON calls(started_at)"
        )
        conn.commit()
    finally:
        conn.close()
    print("[calls] Database initialized", flush=True)


async def record_call(record: Dict[str, Any]) -> None:
    """Schreibt einen Anruf in die Historie. Wird vom SIP-Client aufgerufen."""

    started_at = float(record.get("started_at") or 0.0)
    ended_at_raw = record.get("ended_at")
    ended_at = float(ended_at_raw) if ended_at_raw is not None else None
    duration_raw = record.get("duration_seconds")
    duration = float(duration_raw) if duration_raw is not None else None
    async with aiosqlite.connect(str(DB_PATH)) as conn:
        await conn.execute(
            """
            INSERT INTO calls (
                call_id, contact_name, target, state, error,
                started_at, ended_at, duration_seconds, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("call_id", ""),
                record.get("contact_name", ""),
                record.get("target", ""),
                record.get("state", ""),
                record.get("error", ""),
                started_at,
                ended_at,
                duration,
                datetime.utcnow().isoformat(timespec="seconds") + "Z",
            ),
        )
        await conn.commit()


async def list_recent_calls(limit: int = 10) -> List[Dict[str, Any]]:
    """Liest die letzten ``limit`` Anrufe (neueste zuerst)."""

    limit = max(1, min(int(limit), 100))
    async with aiosqlite.connect(str(DB_PATH)) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM calls ORDER BY started_at DESC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]


def format_calls_for_voice(calls: List[Dict[str, Any]]) -> str:
    """Bildet eine sprachfreundliche Zusammenfassung der Anruf-Historie."""

    if not calls:
        return "Keine Anrufe im Verlauf."
    lines: List[str] = []
    for call in calls:
        when = ""
        started = call.get("started_at")
        if started:
            try:
                when = datetime.fromtimestamp(float(started)).strftime("%d.%m. %H:%M")
            except Exception:
                when = ""
        target = call.get("contact_name") or call.get("target", "")
        duration = call.get("duration_seconds")
        if duration:
            dur = f" ({int(round(float(duration)))}s)"
        else:
            dur = ""
        state = call.get("state", "")
        lines.append(f"{when} {target} – {state}{dur}".strip())
    return "\n".join(lines)
