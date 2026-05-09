"""
JARVIS Auto-Update System

Provides automatic update functionality including:
- Version checking via GitHub Releases API
- Git repository synchronization
- Secure download and verification
- Backup and rollback mechanisms
- Safe installation with health checks
"""

from .version_manager import VersionManager
from .downloader import UpdateDownloader
from .backup import BackupManager
from .installer import UpdateInstaller
from .git_sync import GitSync

__all__ = ['VersionManager', 'UpdateDownloader', 'BackupManager', 'UpdateInstaller', 'GitSync']
