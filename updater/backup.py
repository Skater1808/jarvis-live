"""
Backup Manager for JARVIS Auto-Update System

Handles creating and managing backups of the current installation.
"""

import os
import json
import zipfile
import shutil
from datetime import datetime
from pathlib import Path

class BackupManager:
    """Manages backup creation and cleanup for updates."""
    
    def __init__(self, base_dir = None, backup_count = 5):
        """Initialize backup manager."""
        if base_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        self.base_dir = base_dir
        self.backup_dir = os.path.join(base_dir, "backups")
        self.backup_count = backup_count
        
        # Ensure backup directory exists
        os.makedirs(self.backup_dir, exist_ok=True)
    
    def create_backup(self):
        """
        Create a complete backup of the current installation.
        
        Returns:
            Path to backup file or None if failed
        """
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            backup_filename = f"backup_{timestamp}.zip"
            backup_path = os.path.join(self.backup_dir, backup_filename)
            
            print(f"[updater] Creating backup: {backup_filename}")
            
            # Create ZIP backup
            with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                # Add all files except exclusions
                for root, dirs, files in os.walk(self.base_dir):
                    # Skip backup directory itself
                    if self.backup_dir in root:
                        continue
                    
                    # Skip temp directories and cache
                    dirs[:] = [d for d in dirs if not d.startswith('.') and 
                              d not in ['__pycache__', 'node_modules', 'temp', 'tmp']]
                    
                    for file in files:
                        if file.startswith('.') or file.endswith('.tmp'):
                            continue
                        
                        file_path = os.path.join(root, file)
                        # Calculate relative path from base_dir
                        rel_path = os.path.relpath(file_path, self.base_dir)
                        
                        try:
                            zf.write(file_path, rel_path)
                        except Exception as e:
                            print(f"[updater] Warning: Could not backup {file_path}: {e}")
            
            # Create separate config backup
            self._backup_config(timestamp)
            
            # Verify backup was created
            if os.path.exists(backup_path) and os.path.getsize(backup_path) > 0:
                print(f"[updater] Backup created successfully: {backup_path}")
                self._cleanup_old_backups()
                return backup_path
            else:
                print("[updater] Backup creation failed - empty or missing file")
                return None
                
        except Exception as e:
            print(f"[updater] Error creating backup: {e}")
            return None
    
    def _backup_config(self, timestamp: str) -> None:
        """Create separate backup of config files."""
        try:
            config_backup_dir = os.path.join(self.backup_dir, "configs")
            os.makedirs(config_backup_dir, exist_ok=True)
            
            config_backup_file = os.path.join(config_backup_dir, f"config_{timestamp}.json")
            
            # Backup main config
            config_path = os.path.join(self.base_dir, "config.json")
            if os.path.exists(config_path):
                shutil.copy2(config_path, config_backup_file)
                print(f"[updater] Config backed up: {config_backup_file}")
            
            # Backup example config
            example_config_path = os.path.join(self.base_dir, "config.example.json")
            if os.path.exists(example_config_path):
                example_backup_file = os.path.join(config_backup_dir, f"config.example_{timestamp}.json")
                shutil.copy2(example_config_path, example_backup_file)
                
        except Exception as e:
            print(f"[updater] Error backing up config: {e}")
    
    def _cleanup_old_backups(self) -> None:
        """Remove old backups, keeping only the most recent ones."""
        try:
            # Get all backup files
            backup_files = []
            for file in os.listdir(self.backup_dir):
                if file.startswith("backup_") and file.endswith(".zip"):
                    file_path = os.path.join(self.backup_dir, file)
                    backup_files.append((file_path, os.path.getctime(file_path)))
            
            # Sort by creation time (newest first)
            backup_files.sort(key=lambda x: x[1], reverse=True)
            
            # Remove excess backups
            if len(backup_files) > self.backup_count:
                for file_path, _ in backup_files[self.backup_count:]:
                    try:
                        os.remove(file_path)
                        print(f"[updater] Removed old backup: {os.path.basename(file_path)}")
                    except Exception as e:
                        print(f"[updater] Error removing old backup {file_path}: {e}")
            
            # Also cleanup old config backups
            self._cleanup_old_config_backups()
            
        except Exception as e:
            print(f"[updater] Error cleaning up old backups: {e}")
    
    def _cleanup_old_config_backups(self) -> None:
        """Remove old config backups, keeping only recent ones."""
        try:
            config_backup_dir = os.path.join(self.backup_dir, "configs")
            if not os.path.exists(config_backup_dir):
                return
            
            config_files = []
            for file in os.listdir(config_backup_dir):
                if file.startswith("config_") and file.endswith(".json"):
                    file_path = os.path.join(config_backup_dir, file)
                    config_files.append((file_path, os.path.getctime(file_path)))
            
            # Sort by creation time (newest first)
            config_files.sort(key=lambda x: x[1], reverse=True)
            
            # Keep only the most recent config backups (same count as main backups)
            if len(config_files) > self.backup_count:
                for file_path, _ in config_files[self.backup_count:]:
                    try:
                        os.remove(file_path)
                        print(f"[updater] Removed old config backup: {os.path.basename(file_path)}")
                    except Exception as e:
                        print(f"[updater] Error removing old config backup {file_path}: {e}")
                        
        except Exception as e:
            print(f"[updater] Error cleaning up old config backups: {e}")
    
    def get_latest_backup(self):
        """Get the most recent backup file path."""
        try:
            backup_files = []
            for file in os.listdir(self.backup_dir):
                if file.startswith("backup_") and file.endswith(".zip"):
                    file_path = os.path.join(self.backup_dir, file)
                    backup_files.append((file_path, os.path.getctime(file_path)))
            
            if not backup_files:
                return None
            
            # Return the most recent backup
            latest_backup = max(backup_files, key=lambda x: x[1])
            return latest_backup[0]
            
        except Exception as e:
            print(f"[updater] Error getting latest backup: {e}")
            return None
    
    def restore_backup(self, backup_path = None):
        """
        Restore from a backup file.
        
        Args:
            backup_path: Path to backup file (uses latest if None)
            
        Returns:
            True if restore successful, False otherwise
        """
        try:
            if backup_path is None:
                backup_path = self.get_latest_backup()
                if not backup_path:
                    print("[updater] No backup found for restore")
                    return False
            
            if not os.path.exists(backup_path):
                print(f"[updater] Backup file not found: {backup_path}")
                return False
            
            print(f"[updater] Restoring from backup: {os.path.basename(backup_path)}")
            
            # Create temporary directory for extraction
            import tempfile
            with tempfile.TemporaryDirectory() as temp_dir:
                # Extract backup to temp directory
                with zipfile.ZipFile(backup_path, 'r') as zf:
                    zf.extractall(temp_dir)
                
                # Move files from temp to base directory
                for item in os.listdir(temp_dir):
                    source_path = os.path.join(temp_dir, item)
                    dest_path = os.path.join(self.base_dir, item)
                    
                    # Remove existing destination if it's a directory
                    if os.path.exists(dest_path) and os.path.isdir(dest_path):
                        shutil.rmtree(dest_path)
                    elif os.path.exists(dest_path):
                        os.remove(dest_path)
                    
                    shutil.move(source_path, dest_path)
            
            # Restore config separately if available
            self._restore_config()
            
            print("[updater] Backup restored successfully")
            return True
            
        except Exception as e:
            print(f"[updater] Error restoring backup: {e}")
            return False
    
    def _restore_config(self) -> None:
        """Restore config from the most recent config backup."""
        try:
            config_backup_dir = os.path.join(self.backup_dir, "configs")
            if not os.path.exists(config_backup_dir):
                return
            
            # Find most recent config backup
            config_files = []
            for file in os.listdir(config_backup_dir):
                if file.startswith("config_") and file.endswith(".json"):
                    file_path = os.path.join(config_backup_dir, file)
                    config_files.append((file_path, os.path.getctime(file_path)))
            
            if not config_files:
                return
            
            latest_config = max(config_files, key=lambda x: x[1])[0]
            config_dest = os.path.join(self.base_dir, "config.json")
            
            shutil.copy2(latest_config, config_dest)
            print(f"[updater] Config restored from: {os.path.basename(latest_config)}")
            
        except Exception as e:
            print(f"[updater] Error restoring config: {e}")
    
    def list_backups(self):
        """List all available backups with metadata."""
        try:
            backups = []
            for file in os.listdir(self.backup_dir):
                if file.startswith("backup_") and file.endswith(".zip"):
                    file_path = os.path.join(self.backup_dir, file)
                    stat = os.stat(file_path)
                    
                    backups.append({
                        "filename": file,
                        "path": file_path,
                        "size": stat.st_size,
                        "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
                    })
            
            # Sort by creation time (newest first)
            backups.sort(key=lambda x: x["created"], reverse=True)
            return backups
            
        except Exception as e:
            print(f"[updater] Error listing backups: {e}")
            return []
    
    def get_backup_info(self):
        """Get backup system information."""
        try:
            backups = self.list_backups()
            total_size = sum(b["size"] for b in backups)
            
            return {
                "backup_count": len(backups),
                "total_size_mb": round(total_size / (1024 * 1024), 2),
                "max_backups": self.backup_count,
                "backup_dir": self.backup_dir,
                "latest_backup": backups[0] if backups else None
            }
            
        except Exception as e:
            print(f"[updater] Error getting backup info: {e}")
            return {"error": str(e)}
