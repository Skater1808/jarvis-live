"""
JARVIS Update Skill

Provides update management functionality through JARVIS skill system.
"""

import asyncio
import json
import os
import sys
from datetime import datetime

# Add parent directory to path for importing updater
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from updater import VersionManager, UpdateDownloader, BackupManager, UpdateInstaller
from skills import BaseSkill

class Skill(BaseSkill):
    """JARVIS Update Management Skill."""
    
    name = "update"
    description = "Auto-Update system for JARVIS with version checking, download, backup and rollback"
    version = "1.0.0"
    author = "JARVIS Team"
    
    def __init__(self, config = None):
        super().__init__(config)
        
        # Get base directory
        if getattr(sys, 'frozen', False):
            self.base_dir = os.path.dirname(sys.executable)
        else:
            self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        # Initialize components
        self.version_manager = VersionManager(self.base_dir)
        self.backup_manager = BackupManager(self.base_dir, 
                                          self.config.get("backup_count", 5))
        self.installer = UpdateInstaller(self.base_dir)
        self.installer.set_backup_manager(self.backup_manager)
        
        # Update status tracking
        self._update_status = {
            "checking": False,
            "downloading": False,
            "installing": False,
            "last_check": None,
            "available_update": None
        }
        
        self._setup_tools()
    
    def _setup_tools(self):
        """Register update management tools."""
        
        # Check for updates
        self.register_tool(
            "check",
            "Check for available JARVIS updates from GitHub releases",
            {
                "channel": {
                    "type": "STRING",
                    "description": "Update channel: 'stable' or 'beta'",
                    "enum": ["stable", "beta"]
                }
            },
            self._check_updates
        )
        
        # Install update
        self.register_tool(
            "install",
            "Install available JARVIS update",
            {
                "confirm": {
                    "type": "STRING", 
                    "description": "Type 'yes' to confirm installation",
                    "enum": ["yes"]
                }
            },
            self._install_update
        )
        
        # Rollback update
        self.register_tool(
            "rollback",
            "Rollback to previous backup if update failed",
            {},
            self._rollback_update
        )
        
        # Show update status
        self.register_tool(
            "status",
            "Show current update status and version information",
            {},
            self._show_status
        )
        
        # List backups
        self.register_tool(
            "list_backups",
            "List all available backups",
            {},
            self._list_backups
        )
        
        # Create manual backup
        self.register_tool(
            "backup",
            "Create a manual backup of current installation",
            {},
            self._create_backup
        )
        
        # Git sync tools
        self.register_tool(
            "git_check",
            "Check for Git repository changes",
            {
                "remote": {
                    "type": "STRING",
                    "description": "Git remote name (default: origin)",
                    "default": "origin"
                },
                "branch": {
                    "type": "STRING", 
                    "description": "Git branch name (default: main)",
                    "default": "main"
                }
            },
            self._git_check_changes
        )
        
        self.register_tool(
            "git_sync",
            "Synchronize Git repository changes",
            {
                "remote": {
                    "type": "STRING",
                    "description": "Git remote name (default: origin)",
                    "default": "origin"
                },
                "branch": {
                    "type": "STRING",
                    "description": "Git branch name (default: main)", 
                    "default": "main"
                },
                "confirm": {
                    "type": "STRING",
                    "description": "Type 'yes' to confirm synchronization",
                    "enum": ["yes"]
                }
            },
            self._git_sync_changes
        )
        
        self.register_tool(
            "git_status",
            "Show Git synchronization status",
            {},
            self._git_status
        )
    
    async def _check_updates(self, channel = "stable"):
        """Check for available updates."""
        try:
            if self._update_status["checking"]:
                return "Update check already in progress..."
            
            self._update_status["checking"] = True
            
            current_version = self.version_manager.get_current_version()
            update_available, release_info = await self.version_manager.check_for_updates(channel)
            
            self._update_status["checking"] = False
            self._update_status["last_check"] = datetime.now().isoformat()
            
            if update_available and release_info:
                self._update_status["available_update"] = release_info
                
                response = (
                    f"Update verfügbar!\n"
                    f"Aktuelle Version: {current_version}\n"
                    f"Neue Version: {release_info['version']}\n"
                    f"Veröffentlicht: {release_info.get('published_at', 'Unbekannt')}\n"
                )
                
                if release_info.get('is_major_change'):
                    response += f"\n⚠️ Major Version Update - Breaking Changes möglich!"
                
                # Add changelog
                changelog = self.version_manager.format_changelog(release_info)
                if changelog and changelog != "Keine Änderungen verfügbar.":
                    response += f"\n\nÄnderungen:\n{changelog[:500]}"
                    if len(changelog) > 500:
                        response += "...\n(gekürzt)"
                
                response += f"\n\nNutze 'update__install' mit confirm='yes' zum Installieren."
                return response
            else:
                self._update_status["available_update"] = None
                return f"Keine Updates verfügbar. Aktuelle Version: {current_version}"
                
        except Exception as e:
            self._update_status["checking"] = False
            return f"Fehler bei der Update-Prüfung: {e}"
    
    async def _install_update(self, confirm = "yes"):
        """Install available update."""
        try:
            if confirm != "yes":
                return "Bitte bestätige mit confirm='yes' zum Installieren."
            
            if self._update_status["installing"]:
                return "Installation bereits läuft..."
            
            # Check if update is available
            release_info = self._update_status.get("available_update")
            if not release_info:
                return "Kein Update verfügbar. Nutze 'update__check' zuerst."
            
            self._update_status["installing"] = True
            
            try:
                # Step 1: Create backup
                print("[update] Creating backup...")
                backup_path = self.backup_manager.create_backup()
                if not backup_path:
                    return "Backup fehlgeschlagen - Installation abgebrochen."
                
                # Step 2: Download update
                print("[update] Downloading update...")
                downloader = UpdateDownloader()
                
                progress_info = {"downloaded": 0, "total": 0}
                def progress_callback(downloaded, total):
                    progress_info["downloaded"] = downloaded
                    progress_info["total"] = total
                
                download_url = release_info.get("download_url")
                if not download_url:
                    return "Keine Download-URL gefunden."
                
                update_file = await downloader.download_and_verify(
                    download_url, 
                    progress_callback=progress_callback
                )
                
                if not update_file:
                    return "Download fehlgeschlagen."
                
                # Step 3: Install update
                print("[update] Installing update...")
                
                def install_progress(stage):
                    print(f"[update] {stage}")
                
                success = await self.installer.install_update(
                    update_file, 
                    progress_callback=install_progress
                )
                
                # Cleanup
                downloader.cleanup()
                
                if success:
                    # Update version file
                    self.version_manager.save_current_version(release_info["version"])
                    
                    # Clear update status
                    self._update_status["available_update"] = None
                    
                    return (
                        f"Update erfolgreich installiert!\n"
                        f"Neue Version: {release_info['version']}\n"
                        f"Backup erstellt: {os.path.basename(backup_path)}\n"
                        f"Bitte starte JARVIS neu."
                    )
                else:
                    return "Installation fehlgeschlagen - automatisches Rollback durchgeführt."
                    
            finally:
                self._update_status["installing"] = False
                
        except Exception as e:
            self._update_status["installing"] = False
            return f"Fehler bei der Installation: {e}"
    
    async def _rollback_update(self):
        """Rollback to previous backup."""
        try:
            if self.backup_manager.restore_backup():
                return "Rollback erfolgreich. Alte Version wiederhergestellt."
            else:
                return "Rollback fehlgeschlagen - kein Backup gefunden."
                
        except Exception as e:
            return f"Fehler beim Rollback: {e}"
    
    async def _show_status(self):
        """Show current update status."""
        try:
            current_version = self.version_manager.get_current_version()
            
            status = f"JARVIS Update Status\n"
            status += f"===================\n"
            status += f"Aktuelle Version: {current_version}\n"
            status += f"Basis-Verzeichnis: {self.base_dir}\n"
            
            # Last check
            if self._update_status["last_check"]:
                last_check = datetime.fromisoformat(self._update_status["last_check"])
                status += f"Letzte Prüfung: {last_check.strftime('%d.%m.%Y %H:%M')}\n"
            
            # Available update
            if self._update_status["available_update"]:
                release = self._update_status["available_update"]
                status += f"Verfügbares Update: {release['version']}\n"
                status += f"Veröffentlicht: {release.get('published_at', 'Unbekannt')}\n"
            else:
                status += "Verfügbares Update: Keines\n"
            
            # Current operations
            if self._update_status["checking"]:
                status += "Status: Prüfe auf Updates...\n"
            elif self._update_status["downloading"]:
                status += "Status: Lade Update herunter...\n"
            elif self._update_status["installing"]:
                status += "Status: Installiere Update...\n"
            else:
                status += "Status: Bereit\n"
            
            # Backup info
            backup_info = self.backup_manager.get_backup_info()
            if "error" not in backup_info:
                status += f"\nBackup-Info:\n"
                status += f"Anzahl Backups: {backup_info['backup_count']}/{backup_info['max_backups']}\n"
                status += f"Gesamtgröße: {backup_info['total_size_mb']} MB\n"
            
            return status
            
        except Exception as e:
            return f"Fehler beim Abrufen des Status: {e}"
    
    async def _list_backups(self):
        """List all available backups."""
        try:
            backups = self.backup_manager.list_backups()
            
            if not backups:
                return "Keine Backups verfügbar."
            
            response = f"Verfügbare Backups ({len(backups)}):\n"
            response += "=" * 40 + "\n"
            
            for i, backup in enumerate(backups[:10], 1):  # Show max 10
                size_mb = backup["size"] / (1024 * 1024)
                created = datetime.fromisoformat(backup["created"])
                
                response += f"{i}. {backup['filename']}\n"
                response += f"   Erstellt: {created.strftime('%d.%m.%Y %H:%M')}\n"
                response += f"   Größe: {size_mb:.1f} MB\n\n"
            
            if len(backups) > 10:
                response += f"... und {len(backups) - 10} weitere Backups\n"
            
            return response
            
        except Exception as e:
            return f"Fehler beim Auflisten der Backups: {e}"
    
    async def _create_backup(self):
        """Create a manual backup."""
        try:
            backup_path = self.backup_manager.create_backup()
            if backup_path:
                size_mb = os.path.getsize(backup_path) / (1024 * 1024)
                return f"Backup erstellt: {os.path.basename(backup_path)} ({size_mb:.1f} MB)"
            else:
                return "Backup-Erstellung fehlgeschlagen."
                
        except Exception as e:
            return f"Fehler beim Erstellen des Backups: {e}"
    
    async def _git_check_changes(self, remote = "origin", branch = "main"):
        """Check for Git repository changes."""
        try:
            has_changes, sync_info = await self.version_manager.check_git_changes(remote, branch)
            
            if not has_changes:
                if "error" in sync_info:
                    return f"Git-Prüfung fehlgeschlagen: {sync_info['error']}"
                else:
                    return "Keine Git-Änderungen gefunden. Repository ist aktuell."
            
            # Format response with changes info
            response = "Git-Änderungen gefunden!\n\n"
            response += f"Geänderte Dateien: {sync_info.get('changed_files_count', 0)}\n"
            
            remote_info = sync_info.get('remote_info', {})
            if remote_info:
                response += f"Remote-Commit: {remote_info.get('hash', 'Unknown')[:8]}\n"
                response += f"Autor: {remote_info.get('author', 'Unknown')}\n"
                response += f"Nachricht: {remote_info.get('message', 'No message')}\n"
            
            changes_summary = sync_info.get('changes_summary', '')
            if changes_summary and changes_summary != "Keine Änderungen gefunden.":
                response += f"\n{changes_summary}"
            
            response += f"\n\nNutze 'update__git_sync' mit confirm='yes' zum Synchronisieren."
            return response
            
        except Exception as e:
            return f"Fehler bei der Git-Prüfung: {e}"
    
    async def _git_sync_changes(self, remote = "origin", branch = "main", confirm = "yes"):
        """Synchronize Git repository changes."""
        try:
            if confirm != "yes":
                return "Bitte bestätige mit confirm='yes' zum Synchronisieren."
            
            # First check if there are changes
            has_changes, sync_info = await self.version_manager.check_git_changes(remote, branch)
            
            if not has_changes:
                if "error" in sync_info:
                    return f"Git-Synchronisation fehlgeschlagen: {sync_info['error']}"
                else:
                    return "Keine Änderungen zum Synchronisieren."
            
            # Create backup before syncing
            print("[update] Creating backup before Git sync...")
            backup_path = self.backup_manager.create_backup()
            if not backup_path:
                return "Backup fehlgeschlagen - Git-Synchronisation abgebrochen."
            
            # Perform sync
            success, result = await self.version_manager.sync_git_changes(remote, branch)
            
            if success:
                response = "Git-Synchronisation erfolgreich!\n\n"
                response += f"Backup erstellt: {os.path.basename(backup_path)}\n"
                response += f"Dateien aktualisiert: {result.get('files_updated', 0)}\n"
                response += f"Commit: {result.get('commit', 'Unknown')[:8]}\n"
                response += f"\nRepository ist jetzt aktuell."
                return response
            else:
                return f"Git-Synchronisation fehlgeschlagen: {result.get('error', 'Unknown error')}"
                
        except Exception as e:
            return f"Fehler bei der Git-Synchronisation: {e}"
    
    async def _git_status(self):
        """Show Git synchronization status."""
        try:
            sync_status = self.version_manager.git_sync.get_sync_status()
            
            if not sync_status.get('available', False):
                return f"Git-Status: {sync_status.get('error', 'Nicht verfügbar')}"
            
            response = "Git-Synchronisationsstatus\n"
            response += "========================\n"
            
            current_commit = sync_status.get('current_commit', '')
            remote_commit = sync_status.get('remote_commit', '')
            
            response += f"Aktueller Commit: {current_commit[:8] if current_commit else 'Unknown'}\n"
            response += f"Remote Commit: {remote_commit[:8] if remote_commit else 'Unknown'}\n"
            
            has_changes = sync_status.get('has_changes', False)
            response += f"Änderungen vorhanden: {'Ja' if has_changes else 'Nein'}\n"
            
            if has_changes:
                changed_count = sync_status.get('changed_files_count', 0)
                response += f"Geänderte Dateien: {changed_count}\n"
            
            last_sync = sync_status.get('last_sync')
            if last_sync:
                from datetime import datetime
                try:
                    sync_time = datetime.fromisoformat(last_sync.replace('Z', '+00:00'))
                    response += f"Letzte Synchronisation: {sync_time.strftime('%d.%m.%Y %H:%M')}\n"
                except:
                    response += f"Letzte Synchronisation: {last_sync}\n"
            
            # Add current branch info
            try:
                branch_result = self.version_manager.git_sync._run_git_command(['branch', '--show-current'])
                if branch_result.returncode == 0:
                    current_branch = branch_result.stdout.strip()
                    response += f"Aktueller Branch: {current_branch}\n"
            except:
                pass
            
            return response
            
        except Exception as e:
            return f"Fehler beim Abrufen des Git-Status: {e}"
    
    async def scheduled_check(self):
        """Background task for scheduled update checks."""
        try:
            if self.config.get("auto_check", True):
                # Check for both GitHub releases and Git changes
                all_updates = await self.version_manager.check_all_updates(
                    self.config.get("channel", "stable"),
                    check_git=self.config.get("git_sync_enabled", True)
                )
                
                if all_updates.get('has_updates', False):
                    print("[update] Updates available:")
                    if all_updates.get('github_release'):
                        print(f"  - GitHub Release: {all_updates['github_release']['version']}")
                    if all_updates.get('git_changes'):
                        print(f"  - Git Changes: {all_updates['git_changes']['changed_files_count']} files")
                
        except Exception as e:
            print(f"[update] Scheduled check error: {e}")
