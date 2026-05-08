# Jarvis Skill System

Modulares Skill-System. Skills werden als **ZIP-Dateien** einfach in den Ordner geworfen.

## So funktioniert's

1. ZIP-Datei in `skills/` Ordner werfen
2. Jarvis neu starten
3. Fertig!

```
skills/
├── diagnose.zip             # ← Einfach reinwerfen!
├── weather_skill.zip
└── mein_custom_skill.zip
```

## ZIP-Struktur

**Minimale Struktur:**

```
dein_skill.zip
dein_skill.py         # Muss im Root liegen!
    └── class Skill(BaseSkill): ...
```

**Mit Unterordner:**

```
dein_skill.zip
└── dein_skill/
    ├── __init__.py
    └── dein_skill.py     # Wird automatisch gefunden
```

**Wichtig:** Die Datei muss `*_skill.py` heißen und eine `Skill` Klasse enthalten.

## Neue Skill erstellen

1. Erstelle eine Datei `skills/dein_skillname_skill.py`
2. Erstelle die `Skill` Klasse, die von `BaseSkill` erbt
3. Definiere `name`, `description`, `version`, `author`
4. Implementiere `_setup_tools()` um Tools zu registrieren

### Beispiel-Template

```python
"""
Mein Skill für Jarvis
"""
from typing import Dict, Any
from . import BaseSkill


class Skill(BaseSkill):
    name = "meinskill"  # Wird als Prefix verwendet: meinskill__toolname
    description = "Was macht dieser Skill?"
    version = "1.0.0"
    author = "Dein Name"
    
    def _setup_tools(self):
        # Tool registrieren
        self.register_tool(
            name="mein_tool",  # Wird zu: meinskill__mein_tool
            description="Was macht dieses Tool?",
            parameters={
                "param1": {
                    "type": "string",
                    "description": "Beschreibung des Parameters"
                }
            },
            handler=self._mein_tool_handler
        )
    
    async def _mein_tool_handler(self, param1: str) -> str:
        # Tool-Logik hier
        return f"Ergebnis: {param1}"
```

### Tool-Namen

Skills verwenden das Format: `skillname__toolname`

Beispiel: `calculator__calculate` oder `system__get_battery_status`

### Parameter-Typen

- `string`, `integer`, `number`, `boolean`
- `enum`: `["option1", "option2"]`
- Arrays und Objekte möglich (siehe Gemini Function Calling Schema)

### Handler-Typen

- **Async Handler**: `async def _handler(self, ...)` - für I/O-Operationen
- **Sync Handler**: `def _handler(self, ...)` - für einfache Berechnungen

### Config-Optionen

In `config.json` kannst du Skills konfigurieren:

```json
{
  "skills": {
    "calculator": {
      "enabled": true
    },
    "system": {
      "enabled": true,
      "custom_setting": "value"
    }
  }
}
```

Zugriff im Skill: `self.config.get("custom_setting", "default")`

## Aktivierung

Skills werden beim Server-Start automatisch geladen:

```
[jarvis] Skills loaded: 9 tools from 2 skills
  - calculator v1.0.0: 3 tools
  - system v1.0.0: 6 tools
```

## Testen

Nach dem Neustart des Servers kannst du direkt sagen:
- "Jarvis, rechne 2 + 2 mal 3"
- "Wie ist mein PC?"
- "Konvertiere 25 Celsius zu Fahrenheit"
