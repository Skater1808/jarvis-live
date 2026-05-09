"""
Update Installer for JARVIS Auto-Update System

Handles safe installation of updates with health checks and rollback.
"""

import os
import sys
import json
import zipfile
import tempfile
import shutil
import asyncio
from pathlib import Path

class UpdateInstaller:
    """Handles installation of updates with safety checks."""
    
    def __init__(self, base_dir = None):
        """Initialize update installer."""
        if base_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        self.base_dir = base_dir
        self.backup_manager = None  # Will be set by caller
        
    def set_backup_manager(self, backup_manager):
        """Set backup manager instance for rollback functionality."""
        self.backup_manager = backup_manager
    
    async def install_update(self, update_file,
                           progress_callback = None):
        """
        Install update from downloaded ZIP file.
        
        Args:
            update_file: Path to the update ZIP file
            progress_callback: Callback for progress updates
            
        Returns:
            True if installation successful, False otherwise
        """
        try:
            if progress_callback:
                progress_callback("Starting installation...")
            
            # Create temporary directory for extraction
            with tempfile.TemporaryDirectory() as temp_dir:
                if progress_callback:
                    progress_callback("Extracting update...")
                
                # Extract update to temp directory
                if not self._extract_update(update_file, temp_dir):
                    return False
                
                # Find the actual source directory in the extracted content
                source_dir = self._find_source_directory(temp_dir)
                if not source_dir:
                    print("[updater] Could not find source directory in update")
                    return False
                
                if progress_callback:
                    progress_callback("Installing files...")
                
                # Install files
                if not self._install_files(source_dir, progress_callback):
                    return False
                
                if progress_callback:
                    progress_callback("Performing health check...")
                
                # Perform health check
                if not await self._health_check():
                    print("[updater] Health check failed, rolling back...")
                    if self.backup_manager:
                        self.backup_manager.restore_backup()
                    return False
                
                if progress_callback:
                    progress_callback("Installation complete!")
                
                print("[updater] Update installed successfully")
                return True
                
        except Exception as e:
            print(f"[updater] Error during installation: {e}")
            # Automatic rollback on error
            if self.backup_manager:
                print("[updater] Rolling back due to installation error...")
                self.backup_manager.restore_backup()
            return False
    
    def _extract_update(self, update_file, temp_dir):
        """Extract update ZIP to temporary directory."""
        try:
            with zipfile.ZipFile(update_file, 'r') as zf:
                zf.extractall(temp_dir)
            
            print(f"[updater] Update extracted to: {temp_dir}")
            return True
            
        except Exception as e:
            print(f"[updater] Error extracting update: {e}")
            return False
    
    def _find_source_directory(self, temp_dir):
        """Find the actual source directory in extracted content."""
        try:
            # GitHub's zipball_url creates a directory with format: {owner}-{repo}-{commit}
            # We need to find this directory
            
            for item in os.listdir(temp_dir):
                item_path = os.path.join(temp_dir, item)
                if os.path.isdir(item_path):
                    # Check if this looks like the main source directory
                    # It should contain key JARVIS files
                    key_files = ["server.py", "requirements.txt", "config.example.json"]
                    if any(os.path.exists(os.path.join(item_path, f)) for f in key_files):
                        print(f"[updater] Found source directory: {item}")
                        return item_path
            
            # If no subdirectory found, assume temp_dir is the source
            key_files = ["server.py", "requirements.txt", "config.example.json"]
            if any(os.path.exists(os.path.join(temp_dir, f)) for f in key_files):
                print("[updater] Using temp directory as source")
                return temp_dir
            
            return None
            
        except Exception as e:
            print(f"[updater] Error finding source directory: {e}")
            return None
    
    def _install_files(self, source_dir, 
                      progress_callback = None):
        """Install files from source directory to base directory."""
        try:
            # Files and directories to preserve (not overwrite)
            preserve_files = {"config.json"}
            preserve_dirs = {"backups", "skills"}  # Skills will be handled specially
            
            # First, handle skills directory specially (merge, don't replace)
            self._merge_skills_directory(source_dir)
            
            # Copy all other files and directories
            for item in os.listdir(source_dir):
                source_path = os.path.join(source_dir, item)
                dest_path = os.path.join(self.base_dir, item)
                
                # Skip preserved items
                if item in preserve_files:
                    print(f"[updater] Preserving existing file: {item}")
                    continue
                
                if item in preserve_dirs:
                    print(f"[updater] Preserving existing directory: {item}")
                    continue
                
                # Remove existing destination if it exists
                if os.path.exists(dest_path):
                    if os.path.isdir(dest_path):
                        shutil.rmtree(dest_path)
                    else:
                        os.remove(dest_path)
                
                # Copy the item
                if os.path.isdir(source_path):
                    shutil.copytree(source_path, dest_path)
                    print(f"[updater] Copied directory: {item}")
                else:
                    shutil.copy2(source_path, dest_path)
                    print(f"[updater] Copied file: {item}")
            
            return True
            
        except Exception as e:
            print(f"[updater] Error installing files: {e}")
            return False
    
    def _merge_skills_directory(self, source_dir):
        """Merge skills directory, adding new skills and updating existing ones."""
        try:
            source_skills = os.path.join(source_dir, "skills")
            dest_skills = os.path.join(self.base_dir, "skills")
            
            if not os.path.exists(source_skills):
                print("[updater] No skills directory in update")
                return
            
            # Ensure destination skills directory exists
            os.makedirs(dest_skills, exist_ok=True)
            
            # Copy each skill from source
            for skill_file in os.listdir(source_skills):
                source_skill_path = os.path.join(source_skills, skill_file)
                dest_skill_path = os.path.join(dest_skills, skill_file)
                
                # Remove existing skill if it exists
                if os.path.exists(dest_skill_path):
                    if os.path.isdir(dest_skill_path):
                        shutil.rmtree(dest_skill_path)
                    else:
                        os.remove(dest_skill_path)
                
                # Copy the skill
                if os.path.isdir(source_skill_path):
                    shutil.copytree(source_skill_path, dest_skill_path)
                else:
                    shutil.copy2(source_skill_path, dest_skill_path)
                
                print(f"[updater] Updated skill: {skill_file}")
            
            print("[updater] Skills directory merged successfully")
            
        except Exception as e:
            print(f"[updater] Error merging skills directory: {e}")
    
    async def _health_check(self):
        """Perform health check after installation."""
        try:
            print("[updater] Performing health check...")
            
            # Check if essential files exist
            essential_files = ["server.py", "requirements.txt"]
            for file in essential_files:
                if not os.path.exists(os.path.join(self.base_dir, file)):
                    print(f"[updater] Health check failed: Missing {file}")
                    return False
            
            # Check if version file exists and is readable
            version_file = os.path.join(self.base_dir, "version.json")
            if os.path.exists(version_file):
                try:
                    with open(version_file, 'r') as f:
                        json.load(f)  # Test if it's valid JSON
                except Exception as e:
                    print(f"[updater] Health check failed: Invalid version.json: {e}")
                    return False
            
            # Try to import main modules to check for syntax errors
            sys.path.insert(0, self.base_dir)
            try:
                import server
                print("[updater] Server module imports successfully")
            except Exception as e:
                print(f"[updater] Health check failed: Server module error: {e}")
                return False
            finally:
                if self.base_dir in sys.path:
                    sys.path.remove(self.base_dir)
            
            print("[updater] Health check passed")
            return True
            
        except Exception as e:
            print(f"[updater] Health check error: {e}")
            return False
    
    def rollback_update(self):
        """Rollback to the previous backup."""
        try:
            if not self.backup_manager:
                print("[updater] No backup manager available for rollback")
                return False
            
            print("[updater] Rolling back update...")
            return self.backup_manager.restore_backup()
            
        except Exception as e:
            print(f"[updater] Error during rollback: {e}")
            return False
    
    def get_installation_info(self):
        """Get information about current installation."""
        try:
            info = {
                "base_dir": self.base_dir,
                "essential_files": {},
                "version_info": None
            }
            
            # Check essential files
            essential_files = ["server.py", "requirements.txt", "config.example.json"]
            for file in essential_files:
                file_path = os.path.join(self.base_dir, file)
                info["essential_files"][file] = {
                    "exists": os.path.exists(file_path),
                    "size": os.path.getsize(file_path) if os.path.exists(file_path) else 0
                }
            
            # Get version info
            version_file = os.path.join(self.base_dir, "version.json")
            if os.path.exists(version_file):
                try:
                    with open(version_file, 'r') as f:
                        info["version_info"] = json.load(f)
                except Exception:
                    pass
            
            return info
            
        except Exception as e:
            print(f"[updater] Error getting installation info: {e}")
            return {"error": str(e)}
