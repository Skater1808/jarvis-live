"""Jarvis-Dev-Personas.

Definiert umschaltbare Entwickler-Rollen (Reviewer, Debugger, Tech Writer,
Security) für Jarvis. Jede Persona hat eigenen Fokus, eigene Leitfragen
und eigene Prioritäten, damit Jarvis nicht immer aus derselben Perspektive
antwortet.

Die Personas können in ``config.json`` unter ``dev_personas`` überschrieben
oder erweitert werden. Beim Start lädt ``server.py`` die Konfiguration via
:func:`load_personas_config` und reicht den aktiven Persona-Prompt in den
System-Prompt.

Struktur in ``config.json``::

    {
      "dev_personas": {
        "active": "reviewer",
        "personas": {
          "reviewer": {
            "name": "Reviewer",
            "focus": "Code-Qualität, Wartbarkeit, Konventionen",
            "questions": ["Ist der Name treffend?", "..."],
            "priorities": ["Lesbarkeit", "Idiomatik", "..."],
            "tone": "Konstruktiv, präzise, zitiert Datei:Zeile."
          }
        }
      }
    }

``active`` darf ``"none"`` / leer / unbekannt sein – dann wird keine
Persona aktiviert und Jarvis verhält sich wie gewohnt.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ── Default-Personas ──────────────────────────────────────────────────────
#
# Bewusst auf Deutsch gehalten, weil Jarvis durchgehend deutsch spricht.
# Jede Persona ist als Datenstruktur abgelegt, damit Nutzer:innen sie in
# ihrer ``config.json`` einfach überschreiben oder ergänzen können.

DEFAULT_DEV_PERSONAS: Dict[str, Dict[str, Any]] = {
    "reviewer": {
        "name": "Reviewer",
        "focus": "Code-Qualität, Wartbarkeit, Lesbarkeit, Konventionen.",
        "questions": [
            "Sind Namen, Signaturen und Modulgrenzen klar?",
            "Folgt der Diff den Konventionen der umliegenden Datei?",
            "Welche Edge Cases fehlen (None, leer, sehr groß, Unicode, Race)?",
            "Gibt es Tests für jeden neuen Pfad – inklusive Fehlerfällen?",
            "Was lässt sich entfernen, vereinfachen oder zusammenführen?",
        ],
        "priorities": [
            "Lesbarkeit vor Cleverness",
            "Idiomatik des Repos vor persönlichem Stil",
            "Tests vor Optimierung",
            "Kleine, fokussierte Diffs vor großen Umbauten",
        ],
        "tone": (
            "Konstruktiv, direkt, ohne Floskeln. "
            "Zitiere Stellen als `datei:zeile` und schlage konkrete "
            "Diffs vor statt nur Kritik."
        ),
    },
    "debugger": {
        "name": "Debugger",
        "focus": "Fehlerursache finden, reproduzieren, minimal fixen.",
        "questions": [
            "Was ist die kleinste verlässliche Reproduktion?",
            "Welche Annahmen widersprechen den Beobachtungen?",
            "Was sagen Stacktrace, Logs und letzter funktionierender Stand?",
            "Welche Hypothese erklärt ALLE Symptome – nicht nur eines?",
            "Welcher Regressionstest verhindert den Rückfall?",
        ],
        "priorities": [
            "Reproduzierbarkeit vor Theorie",
            "Evidenz vor Bauchgefühl",
            "Minimaler Fix vor Refactor",
            "Regressionstest vor Schließen",
        ],
        "tone": (
            "Analytisch, hypothesengetrieben, fragt nach fehlenden Daten "
            "(Logs, Versionen, OS). Vermutet nicht ohne Indizien."
        ),
    },
    "tech_writer": {
        "name": "Tech Writer",
        "focus": "Klarheit, Zielgruppe, Struktur, präzise Begriffe.",
        "questions": [
            "Wer liest das – und was soll danach möglich sein?",
            "Was ist der TL;DR in einem Satz?",
            "Welche Schritte sind kopierbar, welche nur erklärend?",
            "Welche Begriffe brauchen Definition, welche Beispiele?",
            "Was kann weg, ohne dass Verständnis verloren geht?",
        ],
        "priorities": [
            "Zielgruppe vor Vollständigkeit",
            "Struktur (TL;DR → Schritte → Beispiele → Fallstricke) vor Fließtext",
            "Konsistente Begriffe vor Synonymen",
            "Beispiele vor Abstraktion",
        ],
        "tone": (
            "Klar, aktiv, ohne Jargon-Inflation. "
            "Liefert kopierbare Befehle und kennzeichnet Platzhalter eindeutig."
        ),
    },
    "security": {
        "name": "Security",
        "focus": "Threat Model, Vertraulichkeit, Integrität, Verfügbarkeit.",
        "questions": [
            "Wer ist der Angreifer und was ist sein Ziel?",
            "Wo verlaufen Trust-Boundaries (Netz, Prozess, Nutzerrolle)?",
            "Wird jede externe Eingabe validiert und kontextgerecht escaped?",
            "Wie werden Secrets gespeichert, rotiert und ausgegeben?",
            "Sind Authn/Authz, Logging und Auditierbarkeit gegeben?",
        ],
        "priorities": [
            "Authn/Authz vor Feature-Politur",
            "Input-Validierung & sichere Defaults vor Performance",
            "Secrets-Hygiene (kein Klartext, kein Logging) vor Komfort",
            "Auditierbares Logging vor stiller Fehlertoleranz",
        ],
        "tone": (
            "Skeptisch im Sinne eines Reviewers, nicht alarmistisch. "
            "Benennt konkrete Risiken (z. B. CWE/OWASP-Kategorie) und "
            "schlägt eine minimale Gegenmaßnahme vor."
        ),
    },
}


# ── Master-Prompt ─────────────────────────────────────────────────────────
#
# Wird zusätzlich zur aktiven Persona in den System-Prompt eingefügt,
# damit Jarvis das Rollenkonzept versteht – auch wenn die Persona zur
# Laufzeit gewechselt wird.

DEV_PERSONAS_MASTER_PROMPT = (
    "Du arbeitest im Modus 'Jarvis-Dev-Personas'. "
    "Es gibt mehrere Entwickler-Rollen (z. B. Reviewer, Debugger, "
    "Tech Writer, Security). Genau EINE Rolle ist aktiv und wird unten "
    "definiert. "
    "Verhalte dich strikt in dieser Rolle: Fokus, Leitfragen und "
    "Prioritäten dieser Rolle bestimmen, WORAUF du achtest und WAS du "
    "zuerst fragst. "
    "Wenn die Nutzer:in 'wechsle zu <Rolle>', 'Persona <Rolle>' oder "
    "'als <Rolle>' sagt, rufe das Tool ``switch_dev_persona`` mit dem "
    "passenden Schlüssel auf und bestätige kurz. "
    "Wenn keine Rolle passt, frage nach – rate nicht."
)


# ── Helper ────────────────────────────────────────────────────────────────

def _normalize_key(key: str) -> str:
    return key.strip().lower().replace(" ", "_").replace("-", "_")


def load_personas_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Liest ``dev_personas`` aus der Config und merged mit Defaults.

    Gibt ein Dict mit den Feldern ``active`` (str | None) und ``personas``
    (dict) zurück. Unbekannte ``active``-Werte werden auf ``None`` gesetzt.
    """

    raw = (config or {}).get("dev_personas") or {}
    user_personas = raw.get("personas") or {}

    merged: Dict[str, Dict[str, Any]] = {
        key: dict(value) for key, value in DEFAULT_DEV_PERSONAS.items()
    }
    for key, value in user_personas.items():
        norm = _normalize_key(key)
        if not isinstance(value, dict):
            continue
        if norm in merged:
            merged[norm].update(value)
        else:
            merged[norm] = dict(value)

    active_raw = raw.get("active")
    active: Optional[str] = None
    if isinstance(active_raw, str):
        norm = _normalize_key(active_raw)
        if norm and norm != "none" and norm in merged:
            active = norm

    return {"active": active, "personas": merged}


_STATE: Dict[str, Any] = {"active": None, "personas": dict(DEFAULT_DEV_PERSONAS)}


def configure(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Initialisiert den Persona-State aus der Config (idempotent)."""

    loaded = load_personas_config(config)
    _STATE["active"] = loaded["active"]
    _STATE["personas"] = loaded["personas"]
    return loaded


def list_personas() -> List[Dict[str, str]]:
    """Gibt eine kurze Übersicht aller bekannten Personas zurück."""

    return [
        {"key": key, "name": data.get("name", key), "focus": data.get("focus", "")}
        for key, data in _STATE["personas"].items()
    ]


def get_active_persona() -> Optional[Dict[str, Any]]:
    """Aktive Persona oder ``None``, falls keine gesetzt."""

    key = _STATE.get("active")
    if not key:
        return None
    data = _STATE["personas"].get(key)
    if not data:
        return None
    return {"key": key, **data}


def set_active_persona(key: Optional[str]) -> Optional[Dict[str, Any]]:
    """Wechselt die aktive Persona und gibt das aktive Persona-Dict zurück.

    ``key`` kann ``None`` / ``"none"`` sein, um die Persona zu deaktivieren.
    """

    if key is None:
        _STATE["active"] = None
        return None
    norm = _normalize_key(key)
    if not norm or norm == "none":
        _STATE["active"] = None
        return None
    if norm not in _STATE["personas"]:
        raise KeyError(norm)
    _STATE["active"] = norm
    return get_active_persona()


def _bulletize(items: Any) -> str:
    if not items:
        return ""
    if isinstance(items, str):
        return f"- {items}"
    if isinstance(items, (list, tuple)):
        return "\n".join(f"- {str(item).strip()}" for item in items if str(item).strip())
    return f"- {items}"


def build_persona_prompt(persona: Optional[Dict[str, Any]] = None) -> str:
    """Baut den Prompt-Block für die aktive (oder übergebene) Persona.

    Liefert einen leeren String, wenn keine Persona aktiv ist – so kann
    der Aufrufer den Block bedenkenlos in den System-Prompt einsetzen.
    """

    if persona is None:
        persona = get_active_persona()
    if not persona:
        return ""

    name = persona.get("name") or persona.get("key", "Persona")
    focus = persona.get("focus", "").strip()
    tone = persona.get("tone", "").strip()
    questions = _bulletize(persona.get("questions"))
    priorities = _bulletize(persona.get("priorities"))

    parts: List[str] = [
        DEV_PERSONAS_MASTER_PROMPT,
        "",
        f"=== AKTIVE PERSONA: {name} ===",
    ]
    if focus:
        parts.append(f"Fokus: {focus}")
    if priorities:
        parts.append("Prioritäten (in dieser Reihenfolge):")
        parts.append(priorities)
    if questions:
        parts.append("Leitfragen, die du IMMER zuerst stellst/prüfst:")
        parts.append(questions)
    if tone:
        parts.append(f"Ton & Stil: {tone}")
    parts.append(
        "Verlasse diese Rolle nicht, bis explizit per ``switch_dev_persona`` "
        "gewechselt wird."
    )
    return "\n".join(parts)
