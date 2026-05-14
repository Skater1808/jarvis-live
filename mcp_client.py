"""MCP Client Manager for Jarvis
Connects to external MCP servers and exposes their tools to Gemini.
"""

import asyncio
import json
import os
import subprocess
import sys
from typing import Optional, Dict, List, Any
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent


# ── Dependency Management ─────────────────────────────────────────────────
_installed_check_cache: Dict[str, bool] = {}


def _command_exists(command: str) -> bool:
    """Check if a command exists in PATH."""
    if command in _installed_check_cache:
        return _installed_check_cache[command]

    try:
        result = subprocess.run(
            ["where" if sys.platform == "win32" else "which", command],
            capture_output=True,
            text=True,
            timeout=5
        )
        exists = result.returncode == 0 and result.stdout.strip()
        _installed_check_cache[command] = exists
        return exists
    except Exception:
        _installed_check_cache[command] = False
        return False


def _get_uvx_path() -> Optional[str]:
    """Get the full path to uvx executable."""
    # Try common locations
    if sys.platform == "win32":
        scripts_dir = os.path.join(os.path.dirname(sys.executable), "Scripts")
        uvx_path = os.path.join(scripts_dir, "uvx.exe")
        if os.path.exists(uvx_path):
            return uvx_path
        # Also check user site-packages
        import site
        user_scripts = os.path.join(site.getusersitepackages().replace("site-packages", ""), "Scripts")
        uvx_path = os.path.join(user_scripts, "uvx.exe")
        if os.path.exists(uvx_path):
            return uvx_path
    return None


def _install_uv() -> bool:
    """Automatically install uv if not present."""
    print("[mcp] uv nicht gefunden. Installiere automatisch...", flush=True)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "uv"],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            print("[mcp] uv erfolgreich installiert!", flush=True)

            # Check if uvx is now available directly
            if _command_exists("uvx"):
                _installed_check_cache["uv"] = True
                _installed_check_cache["uvx"] = True
                return True

            # Try to find uvx in Python Scripts directory
            uvx_path = _get_uvx_path()
            if uvx_path:
                print(f"[mcp] uvx gefunden unter: {uvx_path}", flush=True)
                _installed_check_cache["uv"] = True
                _installed_check_cache["uvx"] = True
                _installed_check_cache["uvx_path"] = uvx_path
                return True

            print("[mcp] WARNUNG: uv installiert, aber uvx nicht im PATH", flush=True)
            return False
        else:
            print(f"[mcp] Fehler bei uv Installation: {result.stderr}", flush=True)
            return False
    except Exception as e:
        print(f"[mcp] Konnte uv nicht installieren: {e}", flush=True)
        return False


def _check_and_install_deps(command: str) -> bool:
    """Check if command exists, try to install if missing."""
    if command in ("npx", "npm"):
        if not _command_exists("npx"):
            print("[mcp] WARNUNG: npx nicht gefunden. Node.js scheint nicht installiert zu sein.", flush=True)
            print("[mcp] Bitte installiere Node.js von https://nodejs.org/", flush=True)
            return False
        return True

    if command == "uvx":
        if not _command_exists("uvx"):
            # Try to install uv automatically
            return _install_uv()
        return True

    return _command_exists(command)


class MCPServerConnection:
    """Manages a single MCP server connection."""

    def __init__(self, name: str, command: str, args: List[str], env: Optional[Dict[str, str]] = None):
        self.name = name
        self.command = command
        self.args = args
        self.env = env or {}
        self.session: Optional[ClientSession] = None
        self._exit_stack: Optional[AsyncExitStack] = None
        self._tools: List[Dict[str, Any]] = []

    async def connect(self) -> bool:
        """Connect to the MCP server via stdio."""
        try:
            server_params = StdioServerParameters(
                command=self.command,
                args=self.args,
                env={**os.environ, **self.env}
            )

            self._exit_stack = AsyncExitStack()
            read_stream, write_stream = await self._exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            self.session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )

            await self.session.initialize()
            print(f"[mcp] Connected to server: {self.name}", flush=True)

            # Cache available tools
            await self._refresh_tools()
            return True

        except Exception as e:
            print(f"[mcp] Failed to connect to {self.name}: {e}", flush=True)
            return False

    def _clean_schema(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Convert JSON Schema to Gemini-compatible format."""
        if not schema or not isinstance(schema, dict):
            return {"type": "OBJECT", "properties": {}}

        # Map JSON Schema types to Gemini types
        type_mapping = {
            "string": "STRING",
            "integer": "INTEGER",
            "number": "NUMBER",
            "boolean": "BOOLEAN",
            "array": "ARRAY",
            "object": "OBJECT",
            "null": "STRING"  # Gemini doesn't support null
        }

        # Fields that Gemini doesn't accept
        invalid_fields = {"$schema", "$id", "additionalProperties", "definitions", "$defs",
                         "default", "examples", "pattern", "minLength", "maxLength",
                         "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                         "multipleOf", "minItems", "maxItems", "uniqueItems"}

        cleaned = {}

        # Handle type conversion
        if "type" in schema:
            json_type = schema["type"]
            if isinstance(json_type, list):
                # Take first non-null type from list
                json_type = next((t for t in json_type if t != "null"), "string")
            cleaned["type"] = type_mapping.get(json_type, "STRING")
        else:
            cleaned["type"] = "OBJECT"

        # Clean properties
        if "properties" in schema and isinstance(schema["properties"], dict):
            cleaned["properties"] = {}
            for prop_name, prop_schema in schema["properties"].items():
                if isinstance(prop_schema, dict):
                    cleaned_prop = self._clean_schema(prop_schema)
                    # Ensure every property has a valid type
                    if "type" not in cleaned_prop:
                        cleaned_prop["type"] = "STRING"
                    cleaned["properties"][prop_name] = cleaned_prop

        # Clean items for arrays
        if "items" in schema and isinstance(schema["items"], dict):
            cleaned["items"] = self._clean_schema(schema["items"])

        # Keep description if present
        if "description" in schema:
            cleaned["description"] = schema["description"]

        # Keep enum if present
        if "enum" in schema:
            cleaned["enum"] = schema["enum"]

        # Keep required if present
        if "required" in schema:
            cleaned["required"] = schema["required"]

        return cleaned

    async def _refresh_tools(self):
        """Refresh the list of available tools from this server."""
        if not self.session:
            return

        try:
            tools_result = await self.session.list_tools()
            self._tools = []
            for tool in tools_result.tools:
                # Convert MCP tool schema to Gemini function declaration format
                cleaned_schema = self._clean_schema(tool.inputSchema)
                func_decl = {
                    "name": f"{self.name}__{tool.name}",  # Prefix with server name
                    "description": f"[{self.name}] {tool.description or tool.name}",
                    "parameters": cleaned_schema
                }
                self._tools.append(func_decl)

            print(f"[mcp] {self.name}: {len(self._tools)} tools available", flush=True)

        except Exception as e:
            print(f"[mcp] Error listing tools from {self.name}: {e}", flush=True)

    def get_tool_declarations(self) -> List[Dict[str, Any]]:
        """Return function declarations for all tools on this server."""
        return self._tools

    async def call_tool(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Call a tool on this server."""
        if not self.session:
            return f"Error: Not connected to {self.name}"

        # Remove server name prefix to get actual tool name
        prefix = f"{self.name}__"
        actual_name = tool_name[len(prefix):] if tool_name.startswith(prefix) else tool_name

        try:
            result = await self.session.call_tool(actual_name, arguments=args)

            # Extract text content from result
            texts = []
            for content in result.content:
                if isinstance(content, TextContent):
                    texts.append(content.text)

            return "\n".join(texts) if texts else str(result.content)

        except Exception as e:
            return f"Error calling {actual_name}: {e}"

    async def disconnect(self):
        """Disconnect from the MCP server."""
        if self._exit_stack:
            await self._exit_stack.aclose()
            self._exit_stack = None
            self.session = None
            print(f"[mcp] Disconnected from {self.name}", flush=True)


class MCPClientManager:
    """Manages multiple MCP server connections."""

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or os.path.join(
            os.path.dirname(__file__), "mcp_servers.json"
        )
        self.servers: Dict[str, MCPServerConnection] = {}
        self._connected = False

    def _load_config(self) -> Dict[str, Any]:
        """Load MCP server configuration from JSON file."""
        if not os.path.exists(self.config_path):
            return {"servers": []}

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[mcp] Error loading config: {e}", flush=True)
            return {"servers": []}

    def _resolve_command(self, command: str) -> str:
        """Resolve command to full path if cached (e.g., uvx)."""
        if command == "uvx" and "uvx_path" in _installed_check_cache:
            return _installed_check_cache["uvx_path"]
        return command

    async def connect_all(self):
        """Connect to all configured MCP servers."""
        config = self._load_config()
        servers_config = config.get("servers", [])

        if not servers_config:
            print("[mcp] No MCP servers configured. Create mcp_servers.json to add servers.", flush=True)
            return

        for server_conf in servers_config:
            name = server_conf.get("name", "unknown")
            command = server_conf.get("command")
            args = server_conf.get("args", [])
            env = server_conf.get("env", {})

            if not command:
                print(f"[mcp] Skipping {name}: no command specified", flush=True)
                continue

            # Check and auto-install dependencies
            if not _check_and_install_deps(command):
                print(f"[mcp] Skipping {name}: Abhaengigkeit '{command}' nicht verfuegbar", flush=True)
                continue

            # Resolve command to full path if available (e.g., uvx on Windows)
            resolved_command = self._resolve_command(command)

            conn = MCPServerConnection(name, resolved_command, args, env)
            if await conn.connect():
                self.servers[name] = conn

        self._connected = True
        total_tools = sum(len(s.get_tool_declarations()) for s in self.servers.values())
        print(f"[mcp] Total: {len(self.servers)} servers, {total_tools} tools", flush=True)

    def get_all_tool_declarations(self) -> List[Dict[str, Any]]:
        """Get function declarations for all tools from all servers."""
        declarations = []
        for server in self.servers.values():
            declarations.extend(server.get_tool_declarations())
        return declarations

    async def execute_tool(self, full_tool_name: str, args: Dict[str, Any]) -> str:
        """Execute a tool on the appropriate server."""
        # Find which server owns this tool
        for server_name, server in self.servers.items():
            prefix = f"{server_name}__"
            if full_tool_name.startswith(prefix):
                return await server.call_tool(full_tool_name, args)

        return f"Error: Unknown MCP tool: {full_tool_name}"

    async def disconnect_all(self):
        """Disconnect from all MCP servers with error handling."""
        for server_name, server in list(self.servers.items()):
            try:
                await server.disconnect()
            except Exception as e:
                print(f"[mcp] Error disconnecting from {server_name}: {e}", flush=True)
        self.servers.clear()
        self._connected = False

    async def cleanup(self):
        """Cleanup all resources - alias for disconnect_all."""
        await self.disconnect_all()


# Global manager instance
_mcp_manager: Optional[MCPClientManager] = None


def get_mcp_manager() -> MCPClientManager:
    """Get or create the global MCP manager instance."""
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPClientManager()
    return _mcp_manager


async def initialize_mcp():
    """Initialize all MCP connections. Call this on server startup."""
    manager = get_mcp_manager()
    await manager.connect_all()


def get_mcp_tools() -> List[Dict[str, Any]]:
    """Get all MCP tool declarations for Gemini."""
    manager = get_mcp_manager()
    return manager.get_all_tool_declarations()


async def execute_mcp_tool(tool_name: str, args: Dict[str, Any]) -> str:
    """Execute an MCP tool."""
    manager = get_mcp_manager()
    return await manager.execute_tool(tool_name, args)


def is_mcp_tool(tool_name: str) -> bool:
    """Check if a tool name is from an MCP server."""
    manager = get_mcp_manager()
    for server_name in manager.servers.keys():
        if tool_name.startswith(f"{server_name}__"):
            return True
    return False


async def cleanup():
    """Global cleanup function for lifespan shutdown."""
    global _mcp_manager
    if _mcp_manager is not None:
        try:
            await _mcp_manager.cleanup()
        except Exception as e:
            # Ignore cleanup errors to prevent shutdown issues
            print(f"[mcp] Cleanup error (ignoring): {e}", flush=True)
        finally:
            _mcp_manager = None
