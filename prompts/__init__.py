"""Prompt-Module für Jarvis.

Enthält wiederverwendbare System-Prompts wie z. B. die Dev-Personas
(Reviewer, Debugger, Tech Writer, Security).
"""

from .dev_personas import (
    DEFAULT_DEV_PERSONAS,
    DEV_PERSONAS_MASTER_PROMPT,
    build_persona_prompt,
    get_active_persona,
    list_personas,
    load_personas_config,
    set_active_persona,
)

__all__ = [
    "DEFAULT_DEV_PERSONAS",
    "DEV_PERSONAS_MASTER_PROMPT",
    "build_persona_prompt",
    "get_active_persona",
    "list_personas",
    "load_personas_config",
    "set_active_persona",
]
