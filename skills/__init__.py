"""
Jarvis Skill System - Modular skill binding like Claude

Skills are self-contained modules that can register tools dynamically.
Each skill has metadata and can be enabled/disabled via config.

Usage:
    from skills import skill_registry
    
    # Load all skills
    await skill_registry.load_all_skills()
    
    # Get all tool declarations
    tools = skill_registry.get_all_tool_declarations()
    
    # Execute a tool
    result = await skill_registry.execute_tool("skillname__toolname", args)
"""

import os
import sys
import json
import zipfile
import tempfile
import shutil
import importlib
import importlib.util
from typing import Dict, List, Any, Callable, Optional
from pathlib import Path

# Skill registry singleton
class SkillRegistry:
    def __init__(self):
        self._skills: Dict[str, 'BaseSkill'] = {}
        self._tool_map: Dict[str, Callable] = {}
        self._declarations: List[Dict] = []
        self._config: Dict = {}
        self._temp_dirs: List[str] = []  # Track temp dirs for cleanup
    
    def register_skill(self, skill: 'BaseSkill') -> None:
        """Register a skill instance."""
        skill_name = skill.name
        self._skills[skill_name] = skill
        
        # Register all tools from this skill
        for declaration in skill.get_tool_declarations():
            # Create a clean copy for Gemini (only allowed fields)
            clean_declaration = {
                "name": f"{skill_name}__{declaration['name']}",
                "description": declaration.get("description", ""),
                "parameters": declaration.get("parameters", {"type": "object", "properties": {}})
            }
            self._declarations.append(clean_declaration)
            self._tool_map[clean_declaration['name']] = skill
        
        print(f"[skills] Registered: {skill_name} v{skill.version} by {skill.author}")
    
    def get_all_tool_declarations(self) -> List[Dict]:
        """Get all tool declarations from all registered skills."""
        return self._declarations
    
    async def execute_tool(self, tool_name: str, args: Dict) -> Any:
        """Execute a tool by name."""
        if tool_name not in self._tool_map:
            return f"[ERROR] Unknown tool: {tool_name}"
        
        skill = self._tool_map[tool_name]
        try:
            return await skill.execute_tool(tool_name, args)
        except Exception as e:
            return f"[ERROR] {tool_name} failed: {str(e)}"
    
    async def load_all_skills(self, config: Optional[Dict] = None) -> None:
        """Discover and load all skills from the skills directory (.py and .zip)."""
        self._config = config or {}
        
        skills_dir = Path(__file__).parent
        
        # 1. Load regular .py skill files
        for file_path in skills_dir.glob("*_skill.py"):
            if file_path.name.startswith("__"):
                continue
            
            await self._load_skill_from_file(file_path)
        
        # 2. Load skills from .zip files
        for zip_path in skills_dir.glob("*.zip"):
            if zip_path.name.startswith("__"):
                continue
            
            await self._load_skill_from_zip(zip_path)
    
    async def _load_skill_from_file(self, file_path: Path) -> None:
        """Load a skill from a .py file."""
        skill_name = file_path.stem
        
        # Check if skill is enabled in config (default: enabled)
        skill_config = self._config.get("skills", {}).get(skill_name, {})
        if skill_config.get("enabled", True) is False:
            print(f"[skills] Skipping disabled skill: {skill_name}")
            return
        
        try:
            # Load the module
            spec = importlib.util.spec_from_file_location(skill_name, file_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[skill_name] = module
            spec.loader.exec_module(module)
            
            # Find and instantiate the skill class
            if hasattr(module, "Skill"):
                skill_instance = module.Skill(skill_config)
                self.register_skill(skill_instance)
            else:
                print(f"[skills] WARNING: {skill_name} has no 'Skill' class")
                
        except Exception as e:
            print(f"[skills] ERROR loading {skill_name}: {e}")
    
    async def _load_skill_from_zip(self, zip_path: Path) -> None:
        """Load a skill from a .zip file."""
        skill_name = zip_path.stem
        
        # Check if skill is enabled in config (default: enabled)
        skill_config = self._config.get("skills", {}).get(skill_name, {})
        if skill_config.get("enabled", True) is False:
            print(f"[skills] Skipping disabled skill: {skill_name}")
            return
        
        try:
            # Create temp directory for extraction
            temp_dir = tempfile.mkdtemp(prefix=f"skill_{skill_name}_")
            self._temp_dirs.append(temp_dir)
            
            # Extract ZIP
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(temp_dir)
            
            # Find the skill .py file in the extracted content
            skill_file = None
            
            # Look for *_skill.py in root or subdirectories
            for py_file in Path(temp_dir).rglob("*_skill.py"):
                skill_file = py_file
                break
            
            if not skill_file:
                print(f"[skills] ERROR: No *_skill.py found in {zip_path.name}")
                return
            
            # Load the module from the extracted file
            module_name = f"zip_skill_{skill_name}"
            spec = importlib.util.spec_from_file_location(module_name, skill_file)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            
            # Find and instantiate the skill class
            if hasattr(module, "Skill"):
                skill_instance = module.Skill(skill_config)
                self.register_skill(skill_instance)
                print(f"[skills] Loaded from ZIP: {zip_path.name}")
            else:
                print(f"[skills] WARNING: {skill_name} (ZIP) has no 'Skill' class")
                
        except Exception as e:
            print(f"[skills] ERROR loading ZIP {zip_path.name}: {e}")
    
    def cleanup(self):
        """Clean up temporary directories created for ZIP skills."""
        for temp_dir in self._temp_dirs:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except:
                pass
        self._temp_dirs.clear()
    
    def get_skill_info(self) -> List[Dict]:
        """Get info about all registered skills."""
        return [
            {
                "name": name,
                "version": skill.version,
                "description": skill.description,
                "author": skill.author,
                "tool_count": len(skill.get_tool_declarations())
            }
            for name, skill in self._skills.items()
        ]
    
    def is_skill_tool(self, tool_name: str) -> bool:
        """Check if a tool name belongs to a skill."""
        return tool_name in self._tool_map


# Global registry instance
skill_registry = SkillRegistry()


class BaseSkill:
    """
    Base class for all Jarvis skills.
    
    Subclasses must define:
    - name: str
    - description: str
    - version: str
    - author: str
    - _tools: Dict[str, Callable]
    """
    
    name: str = "base"
    description: str = "Base skill"
    version: str = "1.0.0"
    author: str = "Unknown"
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self._tools: Dict[str, Callable] = {}
        self._declarations: List[Dict] = []
        self._setup_tools()
    
    def _setup_tools(self):
        """Override this to register tools."""
        pass
    
    def register_tool(self, name: str, description: str, 
                      parameters: Dict, handler: Callable):
        """Register a tool with its declaration and handler."""
        declaration = {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": parameters,
                "required": list(parameters.keys())
            }
        }
        self._declarations.append(declaration)
        self._tools[name] = handler
    
    def get_tool_declarations(self) -> List[Dict]:
        """Get all tool declarations for this skill (Gemini-compatible format)."""
        # Only return allowed fields for Gemini API
        allowed_fields = {"name", "description", "parameters"}
        return [
            {k: v for k, v in decl.items() if k in allowed_fields}
            for decl in self._declarations
        ]
    
    async def execute_tool(self, full_tool_name: str, args: Dict) -> Any:
        """Execute a tool by its full name (skill__tool)."""
        # Extract original tool name from full name
        parts = full_tool_name.split("__", 1)
        if len(parts) != 2:
            return f"[ERROR] Invalid tool name: {full_tool_name}"
        
        _, tool_name = parts
        
        if tool_name not in self._tools:
            return f"[ERROR] Tool not found: {tool_name}"
        
        handler = self._tools[tool_name]
        
        # Support both async and sync handlers
        if asyncio.iscoroutinefunction(handler):
            return await handler(**args)
        else:
            return handler(**args)


import asyncio

__all__ = ['skill_registry', 'BaseSkill']
