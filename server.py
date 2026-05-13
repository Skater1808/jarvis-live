"""
Jarvis V3 — Gemini Live Edition
Real-time audio: Mic -> Gemini Live API -> Speaker
No text conversion, no ElevenLabs — 100% native voice.
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Optional

from google import genai
import httpx
import websockets
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# ── MCP Client ────────────────────────────────────────────────────────────
import mcp_client

# ── Skill System ───────────────────────────────────────────────────────────
import skills
from skills import skill_registry

# ── Memory System ─────────────────────────────────────────────────────────
import memory
from memory import (
    init_database,
    get_facts_for_prompt,
    get_conversation_context,
    remember_fact,
    save_conversation,
    generate_summary,
)

# ── Quick Notes System ────────────────────────────────────────────────────
import quick_notes
from quick_notes import add_quick_note

# ── Dev-Personas ──────────────────────────────────────────────────────────
import prompts as jarvis_prompts
from prompts import (
    build_persona_prompt,
    get_active_persona,
    list_personas,
    set_active_persona,
)

# ── Config ─────────────────────────────────────────────────────────────
# Für PyInstaller-Bundle: Config liegt im Ordner der .exe, nicht im _MEIPASS
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# Fallback: Wenn config.json nicht existiert, nutze config.example.json
if not os.path.exists(CONFIG_PATH):
    CONFIG_PATH = os.path.join(BASE_DIR, "config.example.json")
    print(f"[INFO] config.json nicht gefunden, nutze config.example.json stattdessen")
    if not os.path.exists(CONFIG_PATH):
        print(f"[ERROR] Weder config.json noch config.example.json gefunden in: {BASE_DIR}")
        print(f"[ERROR] Bitte erstelle eine config.json im gleichen Ordner wie die Anwendung")
        sys.exit(1)

with open(CONFIG_PATH) as f:
    config = json.load(f)

GEMINI_API_KEY = config["gemini_api_key"]
USER_NAME      = config.get("user_name",    "Emil Carstensen")
USER_ADDRESS   = config.get("user_address", "Sir")
CITY           = config.get("city",         "Bremen")
TASKS_FILE     = config.get("obsidian_inbox_path", "")
JARVIS_VOICE   = config.get("jarvis_voice", "Charon")
# Available voices: Puck | Charon | Kore | Fenrir | Aoede
TELEGRAM_CONFIG = config.get("telegram", {}) or {}

# Dev-Personas aus Config laden (Reviewer / Debugger / Tech Writer / Security)
_persona_state = jarvis_prompts.configure(config)
if _persona_state.get("active"):
    print(
        f"[jarvis] Dev-Persona aktiv: {_persona_state['active']}",
        flush=True,
    )

GEMINI_LIVE_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta."
    f"GenerativeService.BidiGenerateContent?key={GEMINI_API_KEY}"
)

# Vision model client for screenshot descriptions (non-live)
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# MCP tool declarations (populated on startup)
MCP_TOOL_DECLARATIONS = []


async def initialize_servers():
    """Initialize all servers including MCP and Skills."""
    global MCP_TOOL_DECLARATIONS
    
    # Initialize MCP
    await mcp_client.initialize_mcp()
    MCP_TOOL_DECLARATIONS = mcp_client.get_mcp_tools()
    if MCP_TOOL_DECLARATIONS:
        print(f"[jarvis] MCP tools loaded: {len(MCP_TOOL_DECLARATIONS)}", flush=True)
    
    # Load Skills
    try:
        await skill_registry.load_all_skills(config)
        skill_count = len(skill_registry.get_all_tool_declarations())
        if skill_count > 0:
            print(f"[jarvis] Skills loaded: {skill_count} tools from {len(skill_registry._skills)} skills", flush=True)
            # Print skill info
            for info in skill_registry.get_skill_info():
                print(f"  - {info['name']} v{info['version']}: {info['tool_count']} tools", flush=True)
    except Exception as e:
        print(f"[jarvis] WARNING: Could not load skills: {e}", flush=True)


# ── FastAPI App with Lifespan ────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup and shutdown events."""
    # Startup
    await initialize_servers()
    app.state.telegram_bridge = None
    telegram_enabled = bool(TELEGRAM_CONFIG.get("enabled", False))
    telegram_token = (TELEGRAM_CONFIG.get("bot_token") or "").strip()
    if telegram_enabled:
        if TelegramBridge is None:
            print(
                f"[telegram] Bridge kann nicht geladen werden: {TELEGRAM_IMPORT_ERROR}",
                flush=True,
            )
        elif not telegram_token:
            print("[telegram] Telegram aktiviert, aber bot_token fehlt.", flush=True)
        else:
            try:
                bridge = TelegramBridge(config, process_text_prompt_for_jarvis)
                await bridge.start()
                app.state.telegram_bridge = bridge
            except Exception as e:
                print(f"[telegram] Bridge-Start fehlgeschlagen: {e}", flush=True)
    yield
    # Shutdown
    bridge = getattr(app.state, "telegram_bridge", None)
    if bridge is not None:
        try:
            await bridge.stop()
        except Exception as e:
            print(f"[telegram] Fehler beim Bridge-Stop: {e}", flush=True)
    await mcp_client.cleanup()
    skill_registry.cleanup()

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="frontend"), name="static")

http = httpx.AsyncClient(timeout=30)

import browser_tools
import screen_capture
try:
    from telegram_bridge import TelegramBridge
except Exception as telegram_import_error:
    TelegramBridge = None
    TELEGRAM_IMPORT_ERROR: Optional[Exception] = telegram_import_error
else:
    TELEGRAM_IMPORT_ERROR = None


# ── Weather / Tasks ─────────────────────────────────────────────────────
WEATHER_INFO = None
TASKS_INFO   = []

def _fetch_weather():
    import urllib.request
    try:
        req  = urllib.request.Request(
            f"https://wttr.in/{CITY}?format=j1",
            headers={"User-Agent": "curl"},
        )
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        c    = data["current_condition"][0]
        return {
            "temp":        c["temp_C"],
            "feels_like":  c["FeelsLikeC"],
            "description": c["weatherDesc"][0]["value"],
            "humidity":    c["humidity"],
            "wind_kmh":    c["windspeedKmph"],
        }
    except Exception:
        return None

def _fetch_tasks():
    if not TASKS_FILE:
        return []
    try:
        with open(os.path.join(TASKS_FILE, "Tasks.md"), encoding="utf-8") as f:
            lines = f.readlines()
        return [
            l.strip().replace("- [ ]", "").strip()
            for l in lines if l.strip().startswith("- [ ]")
        ]
    except Exception:
        return []

def refresh_data():
    global WEATHER_INFO, TASKS_INFO
    WEATHER_INFO = _fetch_weather()
    TASKS_INFO   = _fetch_tasks()
    print(f"[jarvis] Wetter: {WEATHER_INFO}", flush=True)
    print(f"[jarvis] Tasks : {len(TASKS_INFO)} geladen", flush=True)

refresh_data()


# ── System Prompt ────────────────────────────────────────────────────────
async def build_system_prompt(user_query: str = "") -> str:
    weather = ""
    if WEATHER_INFO:
        w = WEATHER_INFO
        weather = (
            f"\nWetter {CITY}: {w['temp']}C, "
            f"gefuehlt {w['feels_like']}C, {w['description']}"
        )
    tasks = ""
    if TASKS_INFO:
        tasks = (
            f"\nOffene Aufgaben ({len(TASKS_INFO)}): "
            + ", ".join(TASKS_INFO[:5])
        )
    
    # Load memory information
    memory_facts = await get_facts_for_prompt(user_query, limit=5)
    memory_context = await get_conversation_context(limit=3)
    
    memory_section = ""
    if memory_facts or memory_context:
        memory_section = "\n=== GEDAECHTNIS ==="
        if memory_facts:
            memory_section += f"\nWichtige Fakten:\n{memory_facts}"
        if memory_context:
            memory_section += f"\n\nLetzte Gespräche:\n{memory_context}"
        memory_section += "\nNutze diese Informationen natuerlich, ohne explizit zu sagen 'laut meiner Datenbank...'"

    persona_block = build_persona_prompt()
    persona_section = f"\n\n=== DEV-PERSONA ===\n{persona_block}" if persona_block else ""

    return (
        f"Du bist Jarvis, der KI-Assistent von {USER_NAME}. "
        f"Du sprichst ausschliesslich Deutsch. "
        f"{USER_NAME} wird mit {USER_ADDRESS} angesprochen und gesiezt. "
        f"Dein Ton ist charmant, witzig, eloquent mit leichtem britischem Understatement. "
        f"Du bist ein loyaler Begleiter wie ein Butler mit Persoenlichkeit - nicht zu steif, nicht zu locker. "
        f"Nutze trockenen Humor und gelegentliche Ironie, aber bleibe stets hilfsbereit. "
        f"Kurze Antworten, maximal 3 Saetze. Kein Markdown. "
        f"Aktuelle Zeit: {datetime.now().strftime('%H:%M')}. "
        f"=== DATEN ==={weather}{tasks}{memory_section}{persona_section} === "
        f"WICHTIG: Nutze Tools NUR wenn der Nutzer sie EXPLIZIT anfordert oder es offensichtlich noetig ist. "
        f"- 'suche nach X' oder 'google X' -> search_web mit EXAKT diesem X. "
        f"- 'oeffne URL' -> open_url. "
        f"- 'screenshot' oder 'was siehst du' -> take_screenshot. "
        f"- 'news' oder 'nachrichten' -> get_news. "
        f"- 'Was ist X?' / 'Erklaer mir Y' -> search_wiki fuer Fakten aus Wikipedia. "
        f"- 'merke dir...' -> remember_fact fuer Langzeitgedaechtnis. "
        f"- 'Notiere...' / 'Zu den Notizen:' -> add_quick_note fuer schnelle Datei-Notizen. "
        f"- 'rechne...' / 'wie viel ist...' -> calculator__calculate. "
        f"- 'konvertiere...' -> calculator__convert_units. "
        f"- 'system info' / 'wie ist mein pc' -> system__get_resource_usage. "
        f"- MCP tools (z.B. 'filesystem__read_file') -> fuer Dateisystem, Datenbanken, etc. "
        f"- 'wechsle zu <Rolle>' / 'Persona <Rolle>' / 'als <Rolle>' -> switch_dev_persona "
        f"(Rollen: reviewer, debugger, tech_writer, security). "
        f"Antworte sonst NORMAL per Sprache, ohne Tools zu benutzen!"
    )


# ── Tool Declarations ────────────────────────────────────────────────────
FUNCTION_DECLARATIONS = [
    {
        "name": "search_web",
        "description": "Sucht im Internet nach Informationen",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Suchbegriff"}
            },
            "required": ["query"],
        },
    },
    {
        "name": "open_url",
        "description": "Oeffnet eine URL im Browser",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "url": {"type": "STRING", "description": "Vollstaendige URL"}
            },
            "required": ["url"],
        },
    },
    {
        "name": "take_screenshot",
        "description": "Screenshot des Bildschirms machen und beschreiben",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "get_news",
        "description": "Aktuelle Weltnachrichten abrufen",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "remember_fact",
        "description": "Speichert eine persoenliche Information fuer das Langzeitgedaechtnis",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": "Kategorie: preference, date, habit, project, negative_experience",
                    "enum": ["preference", "date", "habit", "project", "negative_experience"]
                },
                "fact_text": {
                    "type": "STRING",
                    "description": "Die zu merkende Information"
                },
                "context": {
                    "type": "STRING",
                    "description": "Zusatzkontext oder Original-Satz"
                }
            },
            "required": ["category", "fact_text"],
        },
    },
    {
        "name": "add_quick_note",
        "description": "Speichert eine schnelle Notiz in eine Datei (nicht fuer Langzeitgedaechtnis). Verwenden fuer: 'Notiere:', 'Merke dir:', 'Zu den Notizen:'",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "note_text": {
                    "type": "STRING",
                    "description": "Der komplette Inhalt der Notiz"
                }
            },
            "required": ["note_text"],
        },
    },
    {
        "name": "switch_dev_persona",
        "description": (
            "Wechselt die aktive Jarvis-Dev-Persona (z. B. 'reviewer', "
            "'debugger', 'tech_writer', 'security'). 'none' oder leer "
            "deaktiviert die Persona. 'list' gibt nur die verfuegbaren "
            "Personas zurueck, ohne zu wechseln."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "persona": {
                    "type": "STRING",
                    "description": (
                        "Schluessel der gewuenschten Persona aus "
                        "config.dev_personas.personas, oder 'none' / 'list'."
                    )
                }
            },
            "required": ["persona"],
        },
    },
    {
        "name": "search_wiki",
        "description": "Durchsucht Wikipedia, Fandom und Arch Wiki nach Informationen. Nutze fuer: 'Was ist X?', 'Erklaer mir Y', 'Wiki-Suche nach Z'",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "Der Suchbegriff oder das Thema"
                },
                "wiki_source": {
                    "type": "STRING",
                    "description": "Bevorzugte Quelle: 'wikipedia', 'fandom', 'arch', oder 'auto'",
                    "enum": ["wikipedia", "fandom", "arch", "auto"]
                }
            },
            "required": ["query"],
        },
    },
]


# ── Tool Execution ───────────────────────────────────────────────────────
async def execute_tool(name: str, args: dict) -> str:
    print(f"  [tool] {name}({args})", flush=True)
    try:
        if name == "search_web":
            result = await browser_tools.search_and_read(args.get("query", ""))
            if "error" not in result:
                return (
                    f"Seite: {result.get('title', '')}\n"
                    f"{result.get('content', '')[:1800]}"
                )
            return "Suche fehlgeschlagen."

        elif name == "open_url":
            await browser_tools.open_url(args.get("url", ""))
            return f"Geoeffnet: {args.get('url', '')}"

        elif name == "take_screenshot":
            return await screen_capture.describe_screen_gemini(gemini_client)

        elif name == "get_news":
            return await browser_tools.fetch_news()

        elif name == "remember_fact":
            success = await remember_fact(
                args.get("category", "preference"),
                args.get("fact_text", ""),
                args.get("context", "")
            )
            return "Gespeichert." if success else "Konnte nicht speichern."

        elif name == "add_quick_note":
            from quick_notes import add_quick_note
            return await add_quick_note(args.get("note_text", ""), config)

        elif name == "switch_dev_persona":
            requested = (args.get("persona") or "").strip()
            available = list_personas()
            available_str = ", ".join(
                f"{p['key']} ({p['name']})" for p in available
            ) or "keine"
            if not requested or requested.lower() == "list":
                current = get_active_persona()
                current_name = current["name"] if current else "keine"
                return (
                    f"Aktive Persona: {current_name}. "
                    f"Verfuegbar: {available_str}."
                )
            if requested.lower() in {"none", "off", "aus", "keine"}:
                set_active_persona(None)
                return "Dev-Persona deaktiviert."
            try:
                active = set_active_persona(requested)
            except KeyError:
                return (
                    f"Unbekannte Persona '{requested}'. "
                    f"Verfuegbar: {available_str}."
                )
            name_str = active["name"] if active else requested
            return f"Persona gewechselt zu {name_str}."

        elif name == "search_wiki":
            try:
                from wiki_tools import search_wiki
                result = await search_wiki(
                    args.get("query", ""),
                    args.get("wiki_source", "auto"),
                    config
                )
                if "error" in result:
                    return f"Sir, {result['error']}. {result.get('fallback_suggestion', '')}"
                source_info = f" (Quelle: {result['source']})" if not result.get('from_cache') else ""
                return f"{result['title']}{source_info}:\n{result['extract']}"
            except Exception as wiki_err:
                return f"Sir, die Wiki-Suche ist momentan nicht verfügbar: {wiki_err}"

        # MCP tools (prefixed with server name)
        elif mcp_client.is_mcp_tool(name):
            return await mcp_client.execute_mcp_tool(name, args)

        # Skill tools (prefixed with skill name)
        elif skill_registry.is_skill_tool(name):
            return await skill_registry.execute_tool(name, args)

    except Exception as e:
        return f"Fehler: {e}"
    return "Unbekannte Funktion."


# ── Gemini Live Setup Message ────────────────────────────────────────────
def build_setup_msg(system_prompt: str, response_modalities: Optional[list[str]] = None) -> str:
    # Combine built-in tools with MCP tools and Skills
    skill_tools = skill_registry.get_all_tool_declarations()
    all_tools = FUNCTION_DECLARATIONS + MCP_TOOL_DECLARATIONS + skill_tools
    if response_modalities is None:
        response_modalities = ["AUDIO"]

    generation_config: dict[str, Any] = {
        "response_modalities": response_modalities
    }
    if "AUDIO" in response_modalities:
        generation_config["speech_config"] = {
            "voice_config": {
                "prebuilt_voice_config": {
                    "voice_name": JARVIS_VOICE
                }
            }
        }

    return json.dumps({
        "setup": {
            "model": "models/gemini-2.5-flash-native-audio-preview-09-2025",
            "generation_config": generation_config,
            "system_instruction": {
                "parts": [{"text": system_prompt}]
            },
            "tools": [{"function_declarations": all_tools}],
        }
    })


def _extract_text_from_server_content(msg: dict) -> str:
    server_content = msg.get("serverContent", {})
    model_turn = server_content.get("modelTurn", {})
    parts = model_turn.get("parts", [])
    chunks = []
    for part in parts:
        text = part.get("text")
        if text:
            chunks.append(text)
    return "".join(chunks).strip()


async def _process_text_prompt_fallback(user_text: str, system_prompt: str) -> str:
    """Fallback text generation when Live WebSocket text mode is unavailable."""
    prompt = (
        f"{system_prompt}\n\n"
        f"Nutzeranfrage: {user_text}\n\n"
        "Antworte als Jarvis gemaess den Regeln oben."
    )
    try:
        response = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model="gemini-2.5-flash",
            contents=prompt,
        )
        text = (getattr(response, "text", "") or "").strip()
        if text:
            return text
    except Exception as e:
        print(f"[jarvis] Text-Fallback fehlgeschlagen: {e}", flush=True)
    return "Ich konnte gerade keine Antwort erzeugen. Bitte versuchen Sie es erneut."


async def process_text_prompt_for_jarvis(
    user_text: str,
    source_meta: Optional[dict[str, Any]] = None,
) -> str:
    """Text-only prompt path for Telegram and other remote inputs."""
    if not user_text or not user_text.strip():
        return "Bitte senden Sie eine gueltige Nachricht."

    source = (source_meta or {}).get("source", "external")
    print(f"[jarvis] Textanfrage von {source}: {user_text[:120]}", flush=True)
    refresh_data()
    system_prompt = await build_system_prompt(user_text)
    collected_text_parts: list[str] = []

    try:
        async with websockets.connect(
            GEMINI_LIVE_URL,
            additional_headers={"Content-Type": "application/json"},
            max_size=10 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=60,
        ) as gemini_ws:
            await gemini_ws.send(build_setup_msg(system_prompt, response_modalities=["TEXT"]))
            raw = await asyncio.wait_for(gemini_ws.recv(), timeout=10)
            setup_resp = json.loads(raw)
            if "setupComplete" not in setup_resp:
                print(f"[jarvis] Unerwartete Setup-Antwort (Textpfad): {setup_resp}", flush=True)

            await gemini_ws.send(json.dumps({
                "client_content": {
                    "turns": [{
                        "role": "user",
                        "parts": [{"text": user_text}],
                    }],
                    "turn_complete": True,
                }
            }))

            for _ in range(20):
                raw_msg = await asyncio.wait_for(gemini_ws.recv(), timeout=20)
                msg = json.loads(raw_msg)

                if "toolCall" in msg:
                    calls = msg["toolCall"].get("functionCalls", [])
                    responses = []
                    for call in calls:
                        result = await execute_tool(call["name"], call.get("args", {}))
                        responses.append({
                            "id": call["id"],
                            "response": {"result": result},
                        })
                    await gemini_ws.send(json.dumps({
                        "tool_response": {
                            "function_responses": responses
                        }
                    }))
                    continue

                if "serverContent" in msg:
                    text_piece = _extract_text_from_server_content(msg)
                    if text_piece:
                        collected_text_parts.append(text_piece)
                    if msg["serverContent"].get("turnComplete"):
                        break

            reply_text = "\n".join(part for part in collected_text_parts if part).strip()
            if not reply_text:
                reply_text = (
                    "Ich konnte gerade keine vollstaendige Textantwort erzeugen. "
                    "Bitte versuchen Sie es erneut."
                )
    except asyncio.TimeoutError:
        reply_text = "Zeitueberschreitung bei der Gemini-Verbindung."
    except Exception as e:
        print(f"[jarvis] Textpfad-Fehler: {e}", flush=True)
        print("[jarvis] Wechsle auf Text-Fallback ohne Live-Session.", flush=True)
        reply_text = await _process_text_prompt_fallback(user_text, system_prompt)

    try:
        summary = await generate_summary(user_text, reply_text, gemini_client)
        await save_conversation(user_text, reply_text, summary, gemini_client)
    except Exception as mem_err:
        print(f"[memory] Konnte Konversation nicht speichern: {mem_err}", flush=True)

    return reply_text


# ── WebSocket endpoint ───────────────────────────────────────────────────
@app.websocket("/ws")
async def ws_endpoint(browser_ws: WebSocket):
    await browser_ws.accept()
    cid = id(browser_ws)
    print(f"[ws] Client {cid} verbunden", flush=True)

    refresh_data()

    try:
        async with websockets.connect(
            GEMINI_LIVE_URL,
            additional_headers={"Content-Type": "application/json"},
            max_size=10 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=60,
        ) as gemini_ws:

            # Handshake - load system prompt with memory (empty query for general facts)
            system_prompt = await build_system_prompt("")
            await gemini_ws.send(build_setup_msg(system_prompt))
            raw = await asyncio.wait_for(gemini_ws.recv(), timeout=10)
            resp = json.loads(raw)
            if "setupComplete" in resp:
                print("[gemini] Setup OK", flush=True)
            else:
                print(f"[gemini] Setup-Antwort: {resp}", flush=True)

            # Greeting trigger
            await gemini_ws.send(json.dumps({
                "client_content": {
                    "turns": [{
                        "role":  "user",
                        "parts": [{"text": "Jarvis activate"}],
                    }],
                    "turn_complete": True,
                }
            }))
            await browser_ws.send_json({"type": "status", "text": "Jarvis aktiv — ich hoere zu..."})

            # ── browser → Gemini ──────────────────────────────────────────
            async def browser_to_gemini():
                try:
                    while True:
                        msg = await browser_ws.receive_json()
                        if msg.get("type") == "audio":
                            await gemini_ws.send(json.dumps({
                                "realtime_input": {
                                    "media_chunks": [{
                                        "mime_type": "audio/pcm;rate=16000",
                                        "data":      msg["data"],
                                    }]
                                }
                            }))
                        elif msg.get("type") == "turn_complete_request":
                            # 3 seconds of silence detected - signal turn complete
                            await gemini_ws.send(json.dumps({
                                "client_content": {
                                    "turn_complete": True
                                }
                            }))
                            print("[vad] 3s silence detected, triggering response", flush=True)
                        elif msg.get("type") == "tool_call":
                            # Direct tool call from frontend (for update functionality)
                            tool_name = msg.get("tool")
                            args = msg.get("args", {})
                            try:
                                result = await execute_tool(tool_name, args)
                                await browser_ws.send_json({
                                    "type": "tool_response",
                                    "tool_name": tool_name,
                                    "result": result
                                })
                            except Exception as e:
                                await browser_ws.send_json({
                                    "type": "tool_response",
                                    "tool_name": tool_name,
                                    "result": f"Fehler: {str(e)}"
                                })
                except Exception as e:
                    if "disconnect message" not in str(e).lower():
                        print(f"[ws] browser_to_gemini error: {e}", flush=True)

            # ── Gemini → browser ──────────────────────────────────────────
            async def gemini_to_browser():
                try:
                    async for raw_msg in gemini_ws:
                        msg = json.loads(raw_msg)

                        # Audio chunks
                        if "serverContent" in msg:
                            sc = msg["serverContent"]
                            for part in sc.get("modelTurn", {}).get("parts", []):
                                if "inlineData" in part:
                                    await browser_ws.send_json({
                                        "type": "audio",
                                        "data": part["inlineData"]["data"],
                                    })
                            if sc.get("turnComplete"):
                                await browser_ws.send_json({"type": "turn_complete"})
                            if sc.get("interrupted"):
                                await browser_ws.send_json({"type": "interrupted"})

                        # Tool calls
                        elif "toolCall" in msg:
                            calls     = msg["toolCall"].get("functionCalls", [])
                            responses = []
                            for call in calls:
                                result = await execute_tool(
                                    call["name"], call.get("args", {})
                                )
                                responses.append({
                                    "id":       call["id"],
                                    "response": {"result": result},
                                })
                            await gemini_ws.send(json.dumps({
                                "tool_response": {
                                    "function_responses": responses
                                }
                            }))
                except Exception as e:
                    if "disconnect message" not in str(e).lower():
                        print(f"[ws] gemini_to_browser error: {e}", flush=True)

            t1 = asyncio.create_task(browser_to_gemini())
            t2 = asyncio.create_task(gemini_to_browser())
            done, pending = await asyncio.wait(
                [t1, t2], return_when=asyncio.FIRST_EXCEPTION
            )
            for t in pending:
                t.cancel()

    except asyncio.CancelledError:
        # Normal when connection closes
        pass
    except WebSocketDisconnect:
        print(f"[ws] Client {cid} getrennt", flush=True)
    except Exception as e:
        print(f"[ws] Fehler: {e}", flush=True)
        try:
            await browser_ws.send_json({"type": "error", "text": str(e)})
        except Exception:
            pass


# ── Static & Entry ───────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse("frontend/index.html")


if __name__ == "__main__":
    import uvicorn
    print(f"\n{'='*58}")
    print(f"  JARVIS V3  —  Gemini Live Edition")
    print(f"  http://localhost:8340")
    print(f"  Nutzer : {USER_NAME} ({USER_ADDRESS})")
    print(f"  Stadt  : {CITY}")
    print(f"  Stimme : {JARVIS_VOICE}")
    print(f"  Modell : gemini-2.5-flash-native-audio-preview-09-2025")
    print(f"  Kein ElevenLabs benoetigt!")
    print(f"{'='*58}\n")
    uvicorn.run(app, host="127.0.0.1", port=8340, log_level="warning")
