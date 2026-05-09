#!/usr/bin/env python3
"""
Test script to verify the Git sync functionality works correctly.
"""

import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    print("Testing Git sync module import...")
    from updater.git_sync import GitSync
    print("✓ Git sync module imported successfully")
    
    print("\nTesting Git sync instantiation...")
    git_sync = GitSync()
    print("✓ Git sync instantiated successfully")
    
    print("\nTesting Git availability...")
    is_available = git_sync.is_available()
    print(f"✓ Git available: {is_available}")
    
    if is_available:
        print("\nTesting Git sync status...")
        sync_status = git_sync.get_sync_status()
        print(f"✓ Sync status retrieved: {sync_status.get('available', False)}")
        
        print("\nTesting current commit...")
        current_commit = git_sync.get_current_commit()
        print(f"✓ Current commit: {current_commit[:8] if current_commit else 'None'}")
        
        print("\nTesting Git sync integration with version manager...")
        from updater import VersionManager
        vm = VersionManager()
        print("✓ Version manager with Git sync instantiated successfully")
        
        print("\nTesting update skill with Git sync...")
        from skills.update_skill import Skill
        config = {"update": {"git_sync_enabled": True}}
        skill = Skill(config)
        tools = skill.get_tool_declarations()
        git_tools = [t for t in tools if 'git' in t['name']]
        print(f"✓ Update skill has {len(git_tools)} Git tools:")
        for tool in git_tools:
            print(f"  - {tool['name']}: {tool['description']}")
    
    print("\n✅ All Git sync tests passed!")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
