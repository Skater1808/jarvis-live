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

# ── SIP / VoIP ----------──────────────────────────────────────────────────
import call_history
from sip_client import SIPClient, SIPConfig

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

# ── SIP / VoIP ─────────────────────────────────────────────────────────
SIP_CONFIG = SIPConfig.from_config(config)
sip_client: Optional[SIPClient] = None

# ── Dev-Personas ─────────────────────────────────────────────────────────
# Personas werden in config.json definiert (siehe config.example.json).
# Jede Persona kann Prompt-Overlay und optional eine eigene Stimme setzen.
# Wechsel via Tool `switch_persona` (wirkt ab der naechsten Session/Anfrage)
# oder manuell durch Anpassen von `active_persona` in config.json + Neustart.
PERSONAS: dict[str, dict[str, Any]] = config.get("personas", {}) or {}
ACTIVE_PERSONA: str = config.get("active_persona", "default") or "default"


def _get_persona(name: str) -> dict[str, Any]:
    """Hole Persona-Definition; gibt leeres Dict zurueck, wenn unbekannt."""
    persona = PERSONAS.get(name)
    if isinstance(persona, dict):
        return persona
    return {}


def get_active_persona_prompt() -> str:
    """Zusatz-Prompt der aktuell aktiven Persona (kann leer sein)."""
    return str(_get_persona(ACTIVE_PERSONA).get("prompt", "") or "").strip()


def get_active_voice() -> str:
    """Stimme der aktiven Persona, sonst globale Standardstimme."""
    persona_voice = _get_persona(ACTIVE_PERSONA).get("voice")
    if isinstance(persona_voice, str) and persona_voice.strip():
        return persona_voice.strip()
    return JARVIS_VOICE

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

    if PERSONAS:
        active_name = _get_persona(ACTIVE_PERSONA).get("name", ACTIVE_PERSONA)
        print(
            f"[jarvis] Personas geladen: {len(PERSONAS)} "
            f"(aktiv: {ACTIVE_PERSONA} / {active_name})",
            flush=True,
        )

    # SIP / VoIP
    global sip_client
    if SIP_CONFIG.enabled:
        try:
            call_history.init_database()
            sip_client = SIPClient(SIP_CONFIG, on_history=call_history.record_call)
            await sip_client.start()
        except Exception as e:
            print(f"[jarvis] WARNUNG: SIP-Init fehlgeschlagen: {e}", flush=True)
            sip_client = None
    else:
        print("[jarvis] SIP deaktiviert (config.sip.enabled=false)", flush=True)

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
    if sip_client is not None:
        try:
            await sip_client.stop()
        except Exception as e:
            print(f"[sip] Fehler beim Stop: {e}", flush=True)
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

    persona_prompt = get_active_persona_prompt()
    persona_section = f" {persona_prompt} " if persona_prompt else ""

    return (
        f"Du bist Jarvis, der KI-Assistent von {USER_NAME}. "
        f"Du sprichst ausschliesslich Deutsch. "
        f"{USER_NAME} wird mit {USER_ADDRESS} angesprochen und gesiezt. "
        f"Dein Ton ist charmant, witzig, eloquent mit leichtem britischem Understatement. "
        f"Du bist ein loyaler Begleiter wie ein Butler mit Persoenlichkeit - nicht zu steif, nicht zu locker. "
        f"Nutze trockenen Humor und gelegentliche Ironie, aber bleibe stets hilfsbereit. "
        f"Kurze Antworten, maximal 3 Saetze. Kein Markdown. "
        f"Aktuelle Zeit: {datetime.now().strftime('%H:%M')}. "
        f"{persona_section}"
        f"=== DATEN ==={weather}{tasks}{memory_section} === "
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
        f"- 'welche Personas/Rollen hast du?' -> list_personas. "
        f"- 'wechsle zur X-Persona' / 'sei jetzt X' / 'wir debuggen jetzt' / 'mach jetzt Security-Review' -> switch_persona mit passendem Schluessel (reviewer, debugger, tech_writer, security, default). "
        f"- 'rufe X an' / 'telefoniere mit Y' -> make_call mit contact_name (Lookup in config.contacts) oder direkter phone_number. "
        f"- 'leg auf' / 'beende den Anruf' -> hangup_call. "
        f"- 'bist du eingeloggt?' / 'laeuft ein Anruf?' -> call_status. "
        f"- 'zeige meine Anrufe' / 'letzte Telefonate' -> list_recent_calls. "
        f"- MCP tools (z.B. 'filesystem__read_file') -> fuer Dateisystem, Datenbanken, etc. "
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
    {
        "name": "list_personas",
        "description": "Listet die in config.json definierten Dev-Personas (z.B. reviewer, debugger, tech_writer, security) und zeigt die aktive Persona an. Nutze bei Fragen wie 'welche Personas hast du?' oder 'welche Rollen kannst du?'.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "switch_persona",
        "description": "Wechselt die aktive Dev-Persona (z.B. 'reviewer', 'debugger', 'tech_writer', 'security', 'default'). Aenderung wirkt fuer Telegram sofort und fuer die Voice-Session ab der naechsten Verbindung/Reload. Nutze bei 'wechsle zur Reviewer-Persona', 'sei jetzt Security-Auditor', 'wir debuggen jetzt', 'zurueck zum Butler'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "persona": {
                    "type": "STRING",
                    "description": "Schluessel der Ziel-Persona aus config.json (z.B. reviewer, debugger, tech_writer, security, default)."
                },
                "persist": {
                    "type": "BOOLEAN",
                    "description": "Wenn true, wird die Aenderung in config.json gespeichert (sonst nur im Speicher bis Neustart). Default: false."
                }
            },
            "required": ["persona"],
        },
    },
    {
        "name": "make_call",
        "description": "Telefonanruf via SIP/VoIP taetigen. Nutze fuer: 'rufe X an', 'telefoniere mit Y', 'ruf bei Z an'. Kontakte werden aus config.contacts geladen (Fuzzy-Match auf Namen). Alternativ direkte Nummer ueber 'phone_number' moeglich.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "contact_name": {
                    "type": "STRING",
                    "description": "Name des Kontakts aus config.contacts (z.B. 'Mama', 'Papa', 'Arbeit'). Case-insensitive mit Fuzzy-Match."
                },
                "phone_number": {
                    "type": "STRING",
                    "description": "Direkte Telefonnummer oder SIP-URI (z.B. '+49123456789' oder 'sip:alice@example.com'). Wird verwendet wenn contact_name nicht gefunden wird."
                }
            },
            "required": [],
        },
    },
    {
        "name": "hangup_call",
        "description": "Beendet den aktuell laufenden Telefonanruf. Nutze fuer: 'leg auf', 'beende den Anruf', 'haeng auf'.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "call_status",
        "description": "Status des SIP-Clients und des aktuellen Anrufs (registriert? aktiver Anruf?). Nutze fuer: 'bist du eingeloggt?', 'laeuft gerade ein Anruf?'.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "list_recent_calls",
        "description": "Zeigt die letzten Telefonanrufe aus der Historie. Nutze fuer: 'zeige meine Anrufe', 'letzte Telefonate', 'Anruf-Historie'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "limit": {
                    "type": "INTEGER",
                    "description": "Anzahl der anzuzeigenden Anrufe (1-100, Default 10)."
                }
            },
            "required": [],
        },
    },
]


# ── Persona Helpers (Tools) ──────────────────────────────────────────────
def _list_personas_text() -> str:
    """Menschenlesbare Zusammenfassung der konfigurierten Personas."""
    if not PERSONAS:
        return "Keine Personas in config.json definiert."
    lines = [f"Aktive Persona: {ACTIVE_PERSONA}", "Verfuegbare Personas:"]
    for key, persona in PERSONAS.items():
        if not isinstance(persona, dict):
            continue
        name = persona.get("name", key)
        desc = persona.get("description", "")
        marker = " (aktiv)" if key == ACTIVE_PERSONA else ""
        lines.append(f"- {key}: {name}{marker} - {desc}".rstrip(" -"))
    return "\n".join(lines)


def _switch_persona(persona_key: str, persist: bool = False) -> str:
    """Wechselt die aktive Persona im Speicher; optional persistent in config.json."""
    global ACTIVE_PERSONA
    key = (persona_key or "").strip()
    if not key:
        return "Bitte gib einen Persona-Schluessel an (z.B. reviewer, debugger, tech_writer, security)."
    if key not in PERSONAS:
        available = ", ".join(PERSONAS.keys()) or "(keine)"
        return f"Persona '{key}' unbekannt. Verfuegbar: {available}."

    ACTIVE_PERSONA = key
    config["active_persona"] = key
    persona = _get_persona(key)
    display = persona.get("name", key)

    if persist:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as cfg_file:
                json.dump(config, cfg_file, ensure_ascii=False, indent=2)
            note = " Persistiert in config.json."
        except Exception as exc:
            note = f" Konnte nicht in config.json schreiben: {exc}"
    else:
        note = " (nur fuer diese Laufzeit; fuer dauerhaft 'persist=true' setzen oder config.json editieren)"

    return (
        f"Persona gewechselt zu '{display}' ({key})."
        f" Aenderung gilt sofort fuer Telegram-Anfragen;"
        f" fuer die Voice-Session ab der naechsten Verbindung/Reload.{note}"
    )


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

        elif name == "make_call":
            if sip_client is None:
                return (
                    "SIP ist deaktiviert. Setzen Sie 'sip.enabled' auf true "
                    "in config.json und starten Sie neu, Sir."
                )
            result = await sip_client.make_call(
                contact_name=args.get("contact_name", "") or "",
                phone_number=args.get("phone_number", "") or "",
            )
            if result.get("ok"):
                who = result.get("contact_name") or result.get("target", "")
                return f"Anruf zu {who} aufgebaut. Status: {result.get('state')}."
            return f"Anruf fehlgeschlagen: {result.get('error', 'unbekannt')}."

        elif name == "hangup_call":
            if sip_client is None:
                return "SIP ist deaktiviert, Sir."
            result = await sip_client.hangup()
            if result.get("ok"):
                return "Anruf beendet."
            return result.get("error", "Konnte Anruf nicht beenden.")

        elif name == "call_status":
            if sip_client is None:
                return "SIP ist deaktiviert (config.sip.enabled=false)."
            status = sip_client.get_status()
            registered = "ja" if status.get("registered") else "nein"
            active = status.get("active_call")
            if active:
                target = active.get("contact_name") or active.get("target", "")
                return (
                    f"Registriert: {registered}. Aktiver Anruf mit {target} "
                    f"({active.get('state')})."
                )
            contacts = status.get("contacts") or []
            ctx = (
                f" Bekannte Kontakte: {', '.join(contacts)}." if contacts else ""
            )
            return f"Registriert: {registered}. Kein aktiver Anruf.{ctx}"

        elif name == "list_recent_calls":
            try:
                limit = int(args.get("limit") or 10)
            except (TypeError, ValueError):
                limit = 10
            calls = await call_history.list_recent_calls(limit=limit)
            return call_history.format_calls_for_voice(calls)

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

        elif name == "list_personas":
            return _list_personas_text()

        elif name == "switch_persona":
            return _switch_persona(
                args.get("persona", ""),
                bool(args.get("persist", False)),
            )

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
                    "voice_name": get_active_voice()
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
    
    # Show Telegram messages in terminal
    if source == "telegram":
        username = (source_meta or {}).get("username", "Unknown")
        print(f"\n[telegram] 📩 Nachricht von @{username}: {user_text}", flush=True)
    else:
        print(f"[jarvis] Textanfrage von {source}: {user_text[:120]}", flush=True)
    
    refresh_data()
    system_prompt = await build_system_prompt(user_text)
    collected_text_parts: list[str] = []

    try:
        # Use direct text model instead of Live API to avoid WebSocket errors
        reply_text = await _process_text_prompt_fallback(user_text, system_prompt + " Antowrte kurz wie ein telegramm user.")
    except Exception as e:
        print(f"[jarvis] Text-Modell Fehler: {e}", flush=True)
        reply_text = "Ich konnte gerade keine Antwort erzeugen. Bitte versuchen Sie es erneut."

    try:
        summary = await generate_summary(user_text, reply_text, gemini_client)
        await save_conversation(user_text, reply_text, summary, gemini_client)
    except Exception as mem_err:
        print(f"[memory] Konnte Konversation nicht speichern: {mem_err}", flush=True)

    # Show Jarvis response in terminal for Telegram messages
    source = (source_meta or {}).get("source", "external")
    if source == "telegram":
        username = (source_meta or {}).get("username", "Unknown")
        print(f"[telegram] 🤖 Jarvis an @{username}: {reply_text}\n", flush=True)

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
