"""
Version Manager for JARVIS Auto-Update System

Handles version tracking, GitHub Releases API integration, and semantic versioning.
"""

import json
import os
import sys
import asyncio
from datetime import datetime
import aiohttp
from packaging import version
from .git_sync import GitSync

class VersionManager:
    """Manages version checking and GitHub Releases API integration."""
    
    def __init__(self, base_dir = None):
        """Initialize version manager."""
        if base_dir is None:
            if getattr(sys, 'frozen', False):
                base_dir = os.path.dirname(sys.executable)
            else:
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        self.base_dir = base_dir
        self.version_file = os.path.join(base_dir, "version.json")
        self.github_repo = "Skater1808/gemini-live-jarvis-German"
        self.github_api_url = f"https://api.github.com/repos/{self.github_repo}/releases"
        
        # Initialize Git sync
        self.git_sync = GitSync(base_dir)
        
    def get_current_version(self) -> str:
        """Get current version from version.json or return default."""
        try:
            if os.path.exists(self.version_file):
                with open(self.version_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get("version", "1.0.0")
            else:
                # Create default version file
                default_version = {"version": "1.0.0", "updated_at": datetime.now().isoformat()}
                self.save_current_version(default_version["version"])
                return default_version["version"]
        except Exception as e:
            print(f"[updater] Error reading version file: {e}")
            return "1.0.0"
    
    def save_current_version(self, version_str: str) -> None:
        """Save current version to version.json."""
        try:
            data = {
                "version": version_str,
                "updated_at": datetime.now().isoformat()
            }
            with open(self.version_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[updater] Error saving version file: {e}")
    
    async def check_github_releases(self, channel = "stable"):
        """Check GitHub Releases API for new versions."""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "JARVIS-Updater/1.0"
                }
                
                async with session.get(self.github_api_url, headers=headers) as response:
                    if response.status != 200:
                        print(f"[updater] GitHub API error: {response.status}")
                        return None
                    
                    releases = await response.json()
                    
                    # Filter releases based on channel
                    if channel == "stable":
                        # Filter out pre-releases for stable channel
                        releases = [r for r in releases if not r.get("prerelease", False)]
                    elif channel == "beta":
                        # Include pre-releases for beta channel
                        releases = releases  # All releases
                    
                    if not releases:
                        return None
                    
                    # Get latest release
                    latest_release = releases[0]
                    
                    # Extract version from tag (remove 'v' prefix if present)
                    tag_name = latest_release.get("tag_name", "")
                    if tag_name.startswith('v'):
                        tag_name = tag_name[1:]
                    
                    return {
                        "version": tag_name,
                        "name": latest_release.get("name", ""),
                        "body": latest_release.get("body", ""),
                        "published_at": latest_release.get("published_at"),
                        "prerelease": latest_release.get("prerelease", False),
                        "download_url": self._get_download_url(latest_release),
                        "assets": latest_release.get("assets", [])
                    }
                    
        except Exception as e:
            print(f"[updater] Error checking GitHub releases: {e}")
            return None
    
    def _get_download_url(self, release):
        """Extract download URL from release assets."""
        try:
            assets = release.get("assets", [])
            for asset in assets:
                name = asset.get("name", "").lower()
                # Look for ZIP file containing the source code
                if name.endswith(".zip") and "source" in name:
                    return asset.get("browser_download_url")
            
            # Fallback: try to get source zip from zipball_url
            return release.get("zipball_url")
        except Exception as e:
            print(f"[updater] Error extracting download URL: {e}")
            return None
    
    def is_newer_version(self, current, latest):
        """Compare semantic versions to check if latest is newer."""
        try:
            current_ver = version.parse(current)
            latest_ver = version.parse(latest)
            return latest_ver > current_ver
        except Exception as e:
            print(f"[updater] Error comparing versions {current} vs {latest}: {e}")
            return False
    
    def is_major_version_change(self, current, latest):
        """Check if this is a major version change (breaking change)."""
        try:
            current_ver = version.parse(current)
            latest_ver = version.parse(latest)
            return latest_ver.major > current_ver.major
        except Exception as e:
            print(f"[updater] Error checking major version change: {e}")
            return False
    
    async def check_for_updates(self, channel = "stable"):
        """
        Check for updates and return (update_available, release_info).
        
        Returns:
            Tuple of (bool, Dict) where bool indicates if update is available
            and Dict contains release information if available.
        """
        try:
            current_version = self.get_current_version()
            release_info = await self.check_github_releases(channel)
            
            if not release_info:
                return False, None
            
            latest_version = release_info["version"]
            
            if self.is_newer_version(current_version, latest_version):
                release_info["current_version"] = current_version
                release_info["is_major_change"] = self.is_major_version_change(current_version, latest_version)
                return True, release_info
            
            return False, None
            
        except Exception as e:
            print(f"[updater] Error checking for updates: {e}")
            return False, None
    
    def format_changelog(self, release_info):
        """Format release body into a readable changelog."""
        try:
            body = release_info.get("body", "")
            if not body:
                return "Keine Änderungen verfügbar."
            
            # Clean up markdown formatting for better readability
            lines = body.split('\n')
            formatted_lines = []
            
            for line in lines:
                line = line.strip()
                if line and not line.startswith('```'):
                    # Convert markdown headers to plain text
                    if line.startswith('#'):
                        line = line.replace('#', '').strip()
                    formatted_lines.append(line)
            
            return '\n'.join(formatted_lines[:20])  # Limit to first 20 lines
            
        except Exception as e:
            print(f"[updater] Error formatting changelog: {e}")
            return "Fehler beim Formatieren der Änderungen."
    
    async def check_git_changes(self, remote = 'origin', branch = 'main'):
        """
        Check for Git repository changes.
        
        Returns:
            Tuple of (has_changes, sync_info) where sync_info contains details about changes
        """
        try:
            if not self.git_sync.is_available():
                return False, {"error": "Git nicht verfügbar"}
            
            sync_status = self.git_sync.get_sync_status()
            
            if not sync_status.get('available', False):
                return False, sync_status
            
            has_changes = sync_status.get('has_changes', False)
            
            if has_changes:
                # Get additional info about changes
                changed_files = sync_status.get('changed_files', [])
                remote_info = sync_status.get('remote_info', {})
                
                sync_info = {
                    'has_changes': True,
                    'changed_files': changed_files,
                    'changed_files_count': len(changed_files),
                    'remote_commit': sync_status.get('remote_commit'),
                    'current_commit': sync_status.get('current_commit'),
                    'remote_info': remote_info,
                    'changes_summary': self.git_sync.format_changes_summary(changed_files)
                }
                
                return True, sync_info
            else:
                return False, {'has_changes': False}
                
        except Exception as e:
            print(f"[updater] Error checking Git changes: {e}")
            return False, {"error": str(e)}
    
    async def sync_git_changes(self, remote = 'origin', branch = 'main'):
        """
        Synchronize Git repository changes.
        
        Returns:
            Tuple of (success, sync_info) with details about the sync operation
        """
        try:
            if not self.git_sync.is_available():
                return False, {"error": "Git nicht verfügbar"}
            
            # Check if there are changes first
            has_changes, sync_info = await self.check_git_changes(remote, branch)
            
            if not has_changes:
                return True, {"message": "Keine Änderungen zum Synchronisieren"}
            
            # Pull changes
            success = self.git_sync.pull_changes(remote, branch)
            
            if success:
                # Save sync state
                self.git_sync.save_sync_state({
                    'last_sync_commit': sync_info.get('remote_commit'),
                    'sync_type': 'git_pull'
                })
                
                return True, {
                    "message": "Git-Änderungen erfolgreich synchronisiert",
                    "files_updated": sync_info.get('changed_files_count', 0),
                    "commit": sync_info.get('remote_commit')
                }
            else:
                return False, {"error": "Git Pull fehlgeschlagen"}
                
        except Exception as e:
            print(f"[updater] Error syncing Git changes: {e}")
            return False, {"error": str(e)}
    
    async def check_all_updates(self, channel = "stable", check_git = True):
        """
        Check for both GitHub releases and Git changes.
        
        Returns:
            Dict with information about available updates from both sources
        """
        result = {
            'github_release': None,
            'git_changes': None,
            'has_updates': False
        }
        
        # Check GitHub releases
        try:
            github_available, github_info = await self.check_for_updates(channel)
            if github_available:
                result['github_release'] = github_info
                result['has_updates'] = True
        except Exception as e:
            print(f"[updater] Error checking GitHub releases: {e}")
        
        # Check Git changes
        if check_git:
            try:
                git_has_changes, git_info = await self.check_git_changes()
                if git_has_changes:
                    result['git_changes'] = git_info
                    result['has_updates'] = True
            except Exception as e:
                print(f"[updater] Error checking Git changes: {e}")
        
        return result
