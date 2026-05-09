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
        self.github_repo = "Skater1808/jarvis-live"
        self.github_api_url = f"https://api.github.com/repos/{self.github_repo}/releases"
        
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
