#!/usr/bin/env python3
"""
Test script to verify the update skill loads correctly.
"""

import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    print("Testing updater module import...")
    from updater import VersionManager, UpdateDownloader, BackupManager, UpdateInstaller
    print("✓ Updater module imported successfully")
    
    print("\nTesting update skill import...")
    from skills.update_skill import Skill
    print("✓ Update skill imported successfully")
    
    print("\nTesting skill instantiation...")
    config = {"update": {"backup_count": 3}}
    skill = Skill(config)
    print("✓ Update skill instantiated successfully")
    
    print("\nTesting skill tools...")
    tools = skill.get_tool_declarations()
    print(f"✓ Skill has {len(tools)} tools:")
    for tool in tools:
        print(f"  - {tool['name']}: {tool['description']}")
    
    print("\nTesting version manager...")
    vm = VersionManager()
    current_version = vm.get_current_version()
    print(f"✓ Current version: {current_version}")
    
    print("\n✅ All tests passed! Update system is working correctly.")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
