"""
Test Skill for Jarvis - Validates the skill system functionality
"""

import asyncio
from typing import Dict, Any


class Skill:
    """Test skill that validates all skill system features."""
    
    name = "test"
    description = "Testet ob das Skill-System korrekt funktioniert"
    version = "1.0.0"
    author = "Jarvis System"
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self._tools: Dict[str, Any] = {}
        self._declarations: list = []
        self._setup_tools()
    
    def _setup_tools(self):
        """Register all test tools."""
        
        self._declarations.append({
            "name": "ping",
            "description": "Einfacher Ping-Test. Gibt 'Pong!' zurück wenn das Skill-System funktioniert.",
            "parameters": {"type": "object", "properties": {}}
        })
        self._tools["ping"] = self._ping
        
        self._declarations.append({
            "name": "echo",
            "description": "Testet String-Parameter. Gibt den eingegebenen Text zurück.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Text der zurückgegeben werden soll"}
                },
                "required": ["message"]
            }
        })
        self._tools["echo"] = self._echo
        
        self._declarations.append({
            "name": "math_test",
            "description": "Testet Number-Parameter. Addiert zwei Zahlen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "number", "description": "Erste Zahl"},
                    "b": {"type": "number", "description": "Zweite Zahl"}
                },
                "required": ["a", "b"]
            }
        })
        self._tools["math_test"] = self._math_test
        
        self._declarations.append({
            "name": "config_check",
            "description": "Testet ob Config korrekt übergeben wird. Zeigt die Skill-Konfiguration an.",
            "parameters": {"type": "object", "properties": {}}
        })
        self._tools["config_check"] = self._config_check
        
        self._declarations.append({
            "name": "async_test",
            "description": "Testet asynchrone Tool-Ausführung. Simuliert eine kurze Verzögerung.",
            "parameters": {
                "type": "object",
                "properties": {
                    "delay": {"type": "integer", "description": "Verzögerung in Sekunden (max 5)", "default": 1}
                }
            }
        })
        self._tools["async_test"] = self._async_test
        
        self._declarations.append({
            "name": "full_test",
            "description": "Führt alle Tests aus und gibt einen vollständigen Report.",
            "parameters": {"type": "object", "properties": {}}
        })
        self._tools["full_test"] = self._full_test
    
    def get_tool_declarations(self) -> list:
        """Get all tool declarations for this skill."""
        return self._declarations
    
    async def execute_tool(self, full_tool_name: str, args: Dict) -> Any:
        """Execute a tool by its full name."""
        parts = full_tool_name.split("__", 1)
        if len(parts) != 2:
            return f"[ERROR] Invalid tool name: {full_tool_name}"
        
        _, tool_name = parts
        
        if tool_name not in self._tools:
            return f"[ERROR] Tool not found: {tool_name}"
        
        handler = self._tools[tool_name]
        
        if asyncio.iscoroutinefunction(handler):
            return await handler(**args)
        else:
            return handler(**args)
    
    def _ping(self) -> str:
        """Simple ping test."""
        return "✅ Pong! Skill-System funktioniert."
    
    def _echo(self, message: str) -> str:
        """Echo test for string parameters."""
        return f"✅ Echo: '{message}' (Länge: {len(message)} Zeichen)"
    
    def _math_test(self, a: float, b: float) -> str:
        """Math test for number parameters."""
        result = a + b
        return f"✅ Mathe-Test: {a} + {b} = {result}"
    
    def _config_check(self) -> str:
        """Config access test."""
        if self.config:
            return f"✅ Config vorhanden: {len(self.config)} Einstellungen\n   Inhalt: {self.config}"
        else:
            return "✅ Config leer oder nicht gesetzt (Standard-Verhalten)"
    
    async def _async_test(self, delay: int = 1) -> str:
        """Async execution test."""
        actual_delay = min(delay, 5)  # Max 5 seconds
        await asyncio.sleep(actual_delay)
        return f"✅ Async-Test abgeschlossen nach {actual_delay} Sekunden"
    
    def _full_test(self) -> str:
        """Run all tests and return comprehensive report."""
        tests = []
        
        # Test 1: Ping
        try:
            ping_result = self._ping()
            tests.append(f"1. Ping: {ping_result}")
        except Exception as e:
            tests.append(f"1. Ping: ❌ FEHLER - {e}")
        
        # Test 2: Echo
        try:
            echo_result = self._echo("Hallo Test")
            tests.append(f"2. Echo: {echo_result}")
        except Exception as e:
            tests.append(f"2. Echo: ❌ FEHLER - {e}")
        
        # Test 3: Math
        try:
            math_result = self._math_test(10, 20)
            tests.append(f"3. Mathe: {math_result}")
        except Exception as e:
            tests.append(f"3. Mathe: ❌ FEHLER - {e}")
        
        # Test 4: Config
        try:
            config_result = self._config_check()
            tests.append(f"4. Config: {config_result}")
        except Exception as e:
            tests.append(f"4. Config: ❌ FEHLER - {e}")
        
        # Summary
        passed = sum(1 for t in tests if not "FEHLER" in t)
        total = len(tests)
        
        return "\n".join([
            "=" * 40,
            "🧪 SKILL-SYSTEM TEST-REPORT",
            "=" * 40,
            ""
        ] + tests + [
            "",
            "=" * 40,
            f"✅ {passed}/{total} Tests bestanden",
            "=" * 40
        ])
