# Jarvis V3 — Gemini Live Voice Assistant

Ein deutscher KI-Assistent mit nativer Sprachausgabe. Keine Text-zu-Sprache-Konvertierung, keine externe TTS-API — 100% native Gemini Live Audio.

## Features

- **Rein Audio-basiert**: Spracheingabe → Gemini Live API → Sprachausgabe
- **Eingebaute Tools**: Websuche, Screenshots, URL-Öffnen, Nachrichten, Wiki-Suche
- **MCP Server Unterstützung**: Dateisystem, Zeit, Datenbanken und mehr via Model Context Protocol
- **Memory System**: Langzeitgedächtnis für Fakten und Gesprächskontext via SQLite
- **Quick Notes**: Sprachgesteuerte Notizen mit Kategorien
- **Wiki Integration**: Wikipedia, Fandom & Arch Wiki mit 24h Cache
- **Voice Activity Detection**: Automatische Antwort nach 3 Sekunden Stille
- **Deutsche Persönlichkeit**: Charmant, witzig, eloquent mit britischem Understatement
- **Multi-Modal**: Unterstützt Audio + Vision (Screenshots)
- **Echtzeit**: WebSocket-basierte Kommunikation mit Gemini Live

## Schnellstart

### 1. Repository klonen

```bash
# Repo klonen
git clone https://github.com/Skater1808/jarvis-live.git

# In das Verzeichnis wechseln
cd jarvis-live
```

### 2. Setup Wizard ausführen

```bash
# Setup startet automatisch:
# - Python & Chrome prüfen
# - Abhängigkeiten installieren
# - API-Key eintragen
# - Persönliche Einstellungen
python setup_jarvis.py
```

**Oder manuell konfigurieren:**

```bash
# Abhängigkeiten installieren
pip install -r requirements.txt

# Playwright Browser installieren
playwright install

# Config erstellen
cp config.example.json config.json
# config.json anpassen...
```

### 3. Server starten

```bash
python server.py
```

Dann öffne: **http://localhost:8340**

**Wichtig**: Auf den Orb klicken, um Audio zu aktivieren (Browser-Policy).

## MCP Server Konfiguration

Jarvis unterstützt MCP (Model Context Protocol) Server für erweiterte Funktionalität.

### Aktive Server

`mcp_servers.json` enthält die aktiven Server:

```json
{
  "servers": [
    {
      "name": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\Users\\DeinUser"],
      "env": {},
      "description": "Dateisystem-Zugriff"
    }
  ]
}
```

### Verfügbare MCP Server

`mcp_servers.example.json` enthält Beispiele für 12 beliebte Server:

| Server | Beschreibung | Installation |
|--------|--------------|--------------|
| `filesystem` | Dateien lesen/schreiben | Auto (npx) |
| `time` | Zeit/Datumsfunktionen | Auto (uvx) |
| `github` | GitHub API Zugriff | Auto (npx) + Token |
| `puppeteer` | Browser-Automation | Auto (npx) |
| `sqlite` | SQLite Datenbank | Auto (uvx) |
| `fetch` | HTTP Requests | Auto (uvx) |
| `brave-search` | Brave Web Search | Auto (npx) + API Key |
| `postgres` | PostgreSQL Datenbank | Auto (npx) |
| `google-maps` | Maps Geocoding | Auto (npx) + API Key |
| `slack` | Slack Integration | Auto (npx) + Token |
| `sentry` | Error Tracking | Auto (npx) + Token |
| `sequential-thinking` | Strukturiertes Denken | Auto (npx) |

**Alle Server werden automatisch installiert** — keine manuelle Installation nötig!

### Server aktivieren

1. Einträge aus `mcp_servers.example.json` kopieren
2. In `mcp_servers.json` einfügen
3. Server neu starten

## Architektur

```
┌─────────────┐     WebSocket        ┌──────────────┐
│   Browser   │ ◄──────────────────► │   Server     │
│  (Frontend) │    Audio/Control     │   (Python)   │
└─────────────┘                      └──────┬───────┘
       │                                  │
       │ WebSocket                        │ WebSocket
       │                                  │
┌──────▼─────────┐                  ┌───────▼─────────┐
│  AudioWorklet  │                  │   Gemini Live   │
│ (Mic/Playback) │                  │   API (Google)  │
└────────────────┘                  └─────────────────┘
```

### Komponenten

- **`server.py`**: FastAPI WebSocket-Server, Gemini-Kommunikation, Tool-Execution
- **`mcp_client.py`**: MCP Server Verwaltung, Tool-Discovery, Schema-Konvertierung
- **`browser_tools.py`**: Playwright-basierte Browser-Tools (Suche, Screenshots)
- **`frontend/main.js`**: WebSocket-Client, Audio-Capture/Playback, AudioWorklet

## Tools

### Eingebaute Tools

- `search_web(query)` — Websuche via DuckDuckGo
- `search_wiki(query, source)` — Wikipedia/Fandom/Arch Wiki Suche
- `open_url(url)` — URL im Browser öffnen
- `take_screenshot()` — Screenshot + Vision-Analyse
- `get_news()` — Aktuelle Nachrichten
- `remember_fact(category, fact)` — Fakt im Langzeitgedächtnis speichern
- `add_quick_note(note)` — Schnelle Notiz speichern

### Memory Features

Jarvis merkt sich Dinge automatisch:
- **Fakten**: "Merke dir: Ich bin Vegetarier" → Wird bei Essensvorschlägen berücksichtigt
- **Kontext**: Letzte Gespräche werden einbezogen
- **Datenbank**: `jarvis_memory.db` (SQLite)

### Wiki Integration

- **Wikipedia**: Allgemeines Wissen
- **Fandom**: Gaming, Filme, Serien
- **Arch Wiki**: Linux/Technik
- **Cache**: 24h SQLite-Cache für schnelle Antworten

### Voice Activity Detection (VAD)

- **Automatische Erkennung**: Jarvis antwortet nach 3 Sekunden Stille
- **Echo-Prevention**: Mikrofon stoppt automatisch wenn Jarvis spricht

### MCP Tools

MCP Server-Tools werden automatisch mit Präfix verfügbar:
- `filesystem__read_file(path)`
- `filesystem__write_file(path, content)`
- `time__get_current_time()`
- etc.

## Entwicklung

### Projektstruktur

```
jarvis-live/
├── server.py              # Hauptserver
├── mcp_client.py          # MCP Client Manager
├── browser_tools.py       # Browser-Automation
├── screen_capture.py      # Screenshot-Funktionen
├── memory.py              # Gedächtnis-System (SQLite)
├── wiki_tools.py          # Wikipedia/Fandom/Arch Integration
├── quick_notes.py         # Schnelle Notizen
├── config.json            # Konfiguration
├── config.example.json    # Beispiel-Konfiguration
├── mcp_servers.json       # Aktive MCP Server
├── mcp_servers.example.json  # Beispiel-Server
├── requirements.txt       # Python-Abhängigkeiten
├── setup_jarvis.py        # Setup Wizard
├── jarvis_memory.db       # Gedächtnis-Datenbank (automatisch erstellt)
├── jarvis_wiki_cache.db   # Wiki-Cache (automatisch erstellt)
└── frontend/
    ├── index.html         # UI
    ├── main.js            # Audio-Logik + VAD
    └── style.css          # Styling
```

### Wichtige Konfigurationen

| Datei | Zweck |
|-------|-------|
| `config.json` | API-Keys, Nutzerdaten, Voice |
| `mcp_servers.json` | Aktive MCP Server |
| `mcp_servers.example.json` | Verfügbare Server-Beispiele |

## Telegram-Bridge (BotFather)

Jarvis kann optional remote über Telegram gesteuert werden (Text + Voice Notes).

### Schritt-für-Schritt

1. `@BotFather` in Telegram öffnen
2. `/newbot` ausführen und Bot erstellen
3. Token kopieren
4. `config.json` anpassen:

```json
"telegram": {
  "enabled": true,
  "bot_token": "123456:ABCDEF...",
  "allowed_user_ids": [123456789],
  "allowed_chat_ids": [123456789],
  "voice_reply": false,
  "poll_interval_seconds": 1.0
}
```

5. User-ID/Chat-ID ermitteln:
   - Bot mit `/start` anschreiben
   - Server-Logs beobachten (`[telegram] ...`)
6. `allowed_user_ids` und `allowed_chat_ids` auf Ihre Werte setzen
7. Server neu starten

### Verfügbare Telegram-Befehle

- `/start` - Bridge prüfen
- `/help` - Hilfe anzeigen
- `/status` - Bot-Status
- `/note Einkaufsliste aktualisieren` - Quick Note speichern
- `/memory` - Memory-Kurzstatistik
- Normale Textnachrichten werden an Jarvis weitergeleitet
- Voice-Nachrichten (`.ogg`) werden transkribiert und dann als Prompt verarbeitet

### Sicherheit

- Bot-Token niemals committen
- `config.json` nicht ins Repository einchecken
- `.gitignore` aktiv lassen und vor Commits prüfen
- Allowlisten (`allowed_user_ids`, `allowed_chat_ids`) immer setzen

### Windows-Hinweis für Voice Notes

Für OGG->WAV-Konvertierung wird FFmpeg benötigt (durch `pydub` genutzt).  
Prüfen Sie nach Installation im Terminal:

```bash
ffmpeg -version
```

## Troubleshooting

### Kein Audio

- **Orb klicken** vor dem Sprechen (Browser-Policy)
- Konsole auf Fehler prüfen
- `audioCtxOut` wird bei ersten Chunk erstellt
- **VAD**: Mikrofon stoppt automatisch wenn Jarvis spricht (kein Echo)
- **Wiederverbindung**: Einfach "Jarvis" sagen oder Orb klicken

### MCP Server Fehler

- Automatische Installation prüfen: `[mcp] uv erfolgreich installiert!`
- Bei Fehlern: Server manuell testen mit `npx -y @modelcontextprotocol/server-XYZ`

### Gemini Verbindung

- API-Key in `config.json` prüfen
- Modell-Verfügbarkeit: `gemini-2.5-flash-native-audio-preview`

## Credits

Built by [Julian](https://skool.com/ki-automatisierung) with [Claude Code](https://claude.ai/code).

Modified by [Skater1808](https://github.com/Skater1808) with[ Kimi K2.5 in Windsurf](https://windsurf.com)

Inspired by Iron Man's J.A.R.V.I.S. — *"At your service, Sir."*

---

## License

MIT — use it, modify it, build on it. If you build something cool, let me know!
