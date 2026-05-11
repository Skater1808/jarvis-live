"""Jarvis Long-term Memory System
SQLite-based storage with Gemini-powered fact extraction.
"""

import asyncio
import json
import aiosqlite
from typing import List, Dict, Any, Optional
from datetime import datetime
from pathlib import Path

# Import Gemini client from server
from google import genai

DB_PATH = Path(__file__).parent / "jarvis_memory.db"


async def get_db_connection() -> aiosqlite.Connection:
    """Get an async database connection with row factory."""
    conn = await aiosqlite.connect(str(DB_PATH))
    conn.row_factory = aiosqlite.Row
    return conn


def init_database():
    """Initialize the SQLite database with required tables (sync version for startup)."""
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Conversations table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            user_message TEXT,
            jarvis_response TEXT,
            summary TEXT
        )
    """)
    
    # Facts table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            fact_text TEXT NOT NULL,
            context TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    
    # Create index for faster queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_facts_updated ON facts(updated_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_time ON conversations(timestamp)")
    
    conn.commit()
    conn.close()
    print("[memory] Database initialized successfully", flush=True)


# ── Fact Extraction with Gemini ───────────────────────────────────────────

async def extract_facts(text: str, gemini_client: genai.Client) -> List[Dict[str, str]]:
    """Extract personal facts from conversation using Gemini.
    
    Returns list of dicts with keys: category, fact_text, context
    """
    if not text or len(text.strip()) < 10:
        return []
    
    prompt = f"""Analysiere den folgenden Text und extrahiere alle wichtigen persönlichen Fakten über den Nutzer.

Kategorien:
- preference: Vorlieben, Geschmack, was der Nutzer mag/nicht mag
- date: Wichtige Daten, Geburtstage, Jahrestage, Termine
- habit: Gewohnheiten, Routinen, regelmäßige Aktivitäten
- project: Projekte, Ziele, laufende Arbeiten
- negative_experience: Negative Erfahrungen, Dinge die vermieden werden sollten

Gib das Ergebnis als JSON-Array zurück:
[{{"category": "preference", "fact_text": "Der Nutzer mag Kaffee", "context": "Originalsatz"}}]

Text: "{text}"

Antworte NUR mit dem JSON-Array, ohne Markdown-Formatierung oder Erklärungen."""

    try:
        response = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model="gemini-2.5-flash",
            contents=prompt
        )
        
        content = response.text.strip()
        
        # Clean up JSON if wrapped in markdown
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
        
        facts = json.loads(content)
        
        # Validate and clean facts
        valid_facts = []
        for fact in facts:
            if isinstance(fact, dict) and "fact_text" in fact:
                valid_facts.append({
                    "category": fact.get("category", "preference"),
                    "fact_text": fact["fact_text"],
                    "context": fact.get("context", "")
                })
        
        return valid_facts
        
    except json.JSONDecodeError as e:
        print(f"[memory] JSON parse error in extract_facts: {e}", flush=True)
        return []
    except Exception as e:
        print(f"[memory] Error extracting facts: {e}", flush=True)
        return []


async def generate_summary(user_message: str, jarvis_response: str, gemini_client: genai.Client) -> str:
    """Generate a summary of the conversation topic."""
    if not user_message:
        return ""
    
    prompt = f"""Fasse das folgende Gespräch in 2-3 Wörtern als Thema zusammen.

Nutzer: {user_message}
Jarvis: {jarvis_response}

Thema: """

    try:
        response = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model="gemini-2.5-flash",
            contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        print(f"[memory] Error generating summary: {e}", flush=True)
        return "Allgemeines Gespräch"


# ── Database Operations ───────────────────────────────────────────────────

async def save_conversation(
    user_message: str, 
    jarvis_response: str, 
    summary: str,
    gemini_client: genai.Client
) -> int:
    """Save a conversation and extract facts asynchronously."""
    conn = await get_db_connection()
    
    timestamp = datetime.now().isoformat()
    
    await conn.execute("""
        INSERT INTO conversations (timestamp, user_message, jarvis_response, summary)
        VALUES (?, ?, ?, ?)
    """, (timestamp, user_message, jarvis_response, summary))
    
    cursor = await conn.execute("SELECT last_insert_rowid()")
    row = await cursor.fetchone()
    conversation_id = row[0]
    
    await conn.commit()
    await conn.close()
    
    # Extract facts in background
    combined_text = f"Nutzer: {user_message}\nJarvis: {jarvis_response}"
    facts = await extract_facts(combined_text, gemini_client)
    
    if facts:
        await save_facts(facts)
        print(f"[memory] Extracted and saved {len(facts)} facts", flush=True)
    
    return conversation_id


async def save_facts(facts: List[Dict[str, str]]) -> int:
    """Save extracted facts to database."""
    if not facts:
        return 0
    
    conn = await get_db_connection()
    
    now = datetime.now().isoformat()
    count = 0
    
    for fact in facts:
        # Check if similar fact already exists
        cursor = await conn.execute("""
            SELECT id FROM facts 
            WHERE fact_text LIKE ? 
            LIMIT 1
        """, (f"%{fact['fact_text'][:50]}%",))
        
        existing = await cursor.fetchone()
        
        if existing:
            # Update existing fact
            await conn.execute("""
                UPDATE facts 
                SET updated_at = ?, context = ?
                WHERE id = ?
            """, (now, fact.get("context", ""), existing["id"]))
        else:
            # Insert new fact
            await conn.execute("""
                INSERT INTO facts (category, fact_text, context, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                fact["category"],
                fact["fact_text"],
                fact.get("context", ""),
                now,
                now
            ))
            count += 1
    
    await conn.commit()
    await conn.close()
    return count


async def remember_fact(category: str, fact_text: str, context: str = "") -> bool:
    """Manually save a fact."""
    if not fact_text.strip():
        return False
    
    valid_categories = ["preference", "date", "habit", "project", "negative_experience"]
    if category not in valid_categories:
        category = "preference"
    
    conn = await get_db_connection()
    
    now = datetime.now().isoformat()
    
    await conn.execute("""
        INSERT INTO facts (category, fact_text, context, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
    """, (category, fact_text, context, now, now))
    
    await conn.commit()
    await conn.close()
    return True


# ── Query Functions ─────────────────────────────────────────────────────────

async def get_relevant_facts(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Get facts relevant to the query using keyword matching."""
    if not query:
        # Return most recent facts
        conn = await get_db_connection()
        cursor = await conn.execute("""
            SELECT * FROM facts
            ORDER BY updated_at DESC
            LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()
        await conn.close()
        return [dict(row) for row in rows]
    
    # Simple keyword extraction (split by spaces, filter short words)
    keywords = [w.lower() for w in query.split() if len(w) > 3]
    
    if not keywords:
        keywords = [query.lower()]
    
    conn = await get_db_connection()
    
    # Build query with OR conditions for each keyword
    conditions = " OR ".join(["LOWER(fact_text) LIKE ? OR LOWER(context) LIKE ?" for _ in keywords])
    params = []
    for kw in keywords:
        params.extend([f"%{kw}%", f"%{kw}%"])
    
    sql = f"""
        SELECT * FROM facts
        WHERE {conditions}
        ORDER BY 
            CASE category
                WHEN 'preference' THEN 1
                WHEN 'negative_experience' THEN 2
                ELSE 3
            END,
            updated_at DESC
        LIMIT ?
    """
    params.append(limit)
    
    try:
        cursor = await conn.execute(sql, params)
        rows = await cursor.fetchall()
        
        # If no matches, fall back to recent facts
        if not rows:
            cursor = await conn.execute("""
                SELECT * FROM facts
                ORDER BY updated_at DESC
                LIMIT ?
            """, (limit,))
            rows = await cursor.fetchall()
        
        await conn.close()
        return [dict(row) for row in rows]
        
    except Exception as e:
        print(f"[memory] Error querying facts: {e}", flush=True)
        await conn.close()
        return []


async def get_conversation_context(limit: int = 5) -> str:
    """Get recent conversation summaries formatted for prompt."""
    conn = await get_db_connection()
    
    cursor = await conn.execute("""
        SELECT timestamp, summary 
        FROM conversations
        WHERE summary IS NOT NULL AND summary != ''
        ORDER BY timestamp DESC
        LIMIT ?
    """, (limit,))
    
    rows = await cursor.fetchall()
    await conn.close()
    
    if not rows:
        return ""
    
    context_parts = []
    for row in reversed(rows):  # Oldest first
        # Format timestamp nicely
        try:
            dt = datetime.fromisoformat(row["timestamp"])
            date_str = dt.strftime("%d.%m.%Y %H:%M")
        except:
            date_str = row["timestamp"][:16]
        
        context_parts.append(f"[{date_str}] {row['summary']}")
    
    return "\n".join(context_parts)


async def get_facts_for_prompt(query: str = "", limit: int = 5) -> str:
    """Get formatted facts string for system prompt."""
    facts = await get_relevant_facts(query, limit)
    
    if not facts:
        return ""
    
    fact_lines = []
    for f in facts:
        category_emojis = {
            "preference": "💡",
            "date": "📅", 
            "habit": "🔄",
            "project": "📁",
            "negative_experience": "⚠️"
        }
        emoji = category_emojis.get(f["category"], "📝")
        fact_lines.append(f"{emoji} {f['fact_text']}")
    
    return "\n".join(fact_lines)


# ── Statistics ────────────────────────────────────────────────────────────

async def get_memory_stats() -> Dict[str, int]:
    """Get memory statistics."""
    conn = await get_db_connection()
    
    cursor = await conn.execute("SELECT COUNT(*) as count FROM conversations")
    row = await cursor.fetchone()
    conversations_count = row["count"]
    
    cursor = await conn.execute("SELECT COUNT(*) as count FROM facts")
    row = await cursor.fetchone()
    facts_count = row["count"]
    
    cursor = await conn.execute("SELECT COUNT(*) as count FROM facts WHERE category = 'preference'")
    row = await cursor.fetchone()
    preferences_count = row["count"]
    
    await conn.close()
    
    return {
        "conversations": conversations_count,
        "facts": facts_count,
        "preferences": preferences_count
    }


# Initialize on import
init_database()
