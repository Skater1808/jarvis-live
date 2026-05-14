"""
Git Synchronization Module for JARVIS Auto-Update System

Handles checking for repository changes and syncing with remote Git repository.
"""

import os
import subprocess
import json
import time
from datetime import datetime

# #region agent log
def _agent_dbg(location, message, data, hypothesis_id=""):
    try:
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _p = os.path.join(_root, "debug-629b8b.log")
        with open(_p, "a", encoding="utf-8") as _f:
            _f.write(
                json.dumps(
                    {
                        "sessionId": "629b8b",
                        "timestamp": int(time.time() * 1000),
                        "location": location,
                        "message": message,
                        "data": data,
                        "hypothesisId": hypothesis_id,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass


# #endregion

class GitSync:
    """Handles Git repository synchronization and change detection."""
    
    def __init__(self, repo_path = None):
        """Initialize Git sync with repository path."""
        if repo_path is None:
            repo_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        self.repo_path = repo_path
        self.git_dir = os.path.join(repo_path, '.git')
        
        # Check if this is a Git repository
        self.is_git_repo = os.path.exists(self.git_dir)
        
        # State tracking file
        self.state_file = os.path.join(repo_path, 'git_sync_state.json')
    
    def is_available(self) -> bool:
        """Check if Git synchronization is available."""
        return self.is_git_repo and self._has_git_command()
    
    def _has_git_command(self) -> bool:
        """Check if Git command is available."""
        try:
            subprocess.run(['git', '--version'], capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
    
    def _run_git_command(self, args, capture_output = True):
        """Run a Git command in the repository directory."""
        try:
            # Set encoding to handle special characters properly
            result = subprocess.run(
                ['git'] + args,
                cwd=self.repo_path,
                capture_output=capture_output,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=30
            )
            return result
        except subprocess.TimeoutExpired:
            raise Exception("Git command timed out")
        except FileNotFoundError:
            raise Exception("Git command not found")
    
    def get_current_commit(self):
        """Get the current commit hash."""
        if not self.is_available():
            return None
        
        try:
            result = self._run_git_command(['rev-parse', 'HEAD'])
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as e:
            print(f"[git_sync] Error getting current commit: {e}")
        
        return None
    
    def get_remote_commit(self, remote = 'origin', branch = 'main'):
        """Get the latest commit from remote repository."""
        if not self.is_available():
            return None
        
        try:
            # Fetch latest changes from remote
            self._run_git_command(['fetch', remote, branch])
            
            # Get the commit hash of the remote branch
            result = self._run_git_command(['rev-parse', f'{remote}/{branch}'])
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as e:
            print(f"[git_sync] Error getting remote commit: {e}")
        
        return None
    
    def has_changes(self, remote = 'origin', branch = 'main'):
        """Check if there are changes between local and remote."""
        if not self.is_available():
            return False
        
        current_commit = self.get_current_commit()
        remote_commit = self.get_remote_commit(remote, branch)
        
        if not current_commit or not remote_commit:
            return False
        
        return current_commit != remote_commit
    
    def get_changed_files(self, remote = 'origin', branch = 'main'):
        """Get list of changed files between local and remote."""
        if not self.is_available():
            return []
        
        try:
            # Get diff between current HEAD and remote
            result = self._run_git_command(['diff', '--name-only', f'HEAD...{remote}/{branch}'])
            if result.returncode == 0:
                files = [f.strip() for f in result.stdout.split('\n') if f.strip()]
                return files
        except Exception as e:
            print(f"[git_sync] Error getting changed files: {e}")
        
        return []
    
    def get_commit_info(self, commit_hash = None):
        """Get information about a specific commit."""
        if not self.is_available():
            return None
        
        if not commit_hash:
            commit_hash = 'HEAD'
        
        try:
            # Get commit details
            result = self._run_git_command([
                'show', '--format=%H|%an|%ad|%s', 
                '--date=iso', 
                commit_hash
            ])
            
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                if lines:
                    first_line = lines[0]
                    parts = first_line.split('|')
                    if len(parts) >= 4:
                        return {
                            'hash': parts[0],
                            'author': parts[1],
                            'date': parts[2],
                            'message': parts[3]
                        }
        except Exception as e:
            print(f"[git_sync] Error getting commit info: {e}")
        
        return None
    
    def get_commit_history(self, limit = 10):
        """Get commit history."""
        if not self.is_available():
            return []
        
        try:
            result = self._run_git_command([
                'log', '--format=%H|%an|%ad|%s',
                '--date=iso',
                f'-{limit}'
            ])
            
            if result.returncode == 0:
                commits = []
                for line in result.stdout.strip().split('\n'):
                    if line.strip():
                        parts = line.split('|')
                        if len(parts) >= 4:
                            commits.append({
                                'hash': parts[0],
                                'author': parts[1],
                                'date': parts[2],
                                'message': parts[3]
                            })
                return commits
        except Exception as e:
            print(f"[git_sync] Error getting commit history: {e}")
        
        return []
    
    def pull_changes(self, remote = 'origin', branch = 'main'):
        """Pull changes from remote repository."""
        if not self.is_available():
            return False
        
        try:
            # Stash any local changes first
            stash_result = self._run_git_command(['stash', 'push', '-m', 'Auto-update stash'])
            # #region agent log
            _had_real_stash = (
                stash_result.returncode == 0
                and stash_result.stdout
                and "No local changes" not in stash_result.stdout
            )
            _agent_dbg(
                "git_sync.py:pull_changes",
                "after_stash",
                {
                    "stash_rc": stash_result.returncode,
                    "had_real_stash": _had_real_stash,
                    "stash_stdout_has_no_local": (
                        "No local changes" in (stash_result.stdout or "")
                    ),
                },
                "H6",
            )
            # #endregion

            # Pull changes
            pull_result = self._run_git_command(['pull', remote, branch])
            
            if pull_result.returncode == 0:
                print(f"[git_sync] Successfully pulled changes from {remote}/{branch}")
                
                # Try to restore stashed changes if any
                if stash_result.returncode == 0 and 'No local changes' not in stash_result.stdout:
                    self._run_git_command(['stash', 'pop'])
                # #region agent log
                _agent_dbg(
                    "git_sync.py:pull_changes",
                    "pull_ok",
                    {"popped_after_success": _had_real_stash},
                    "H6",
                )
                # #endregion

                return True
            else:
                print(f"[git_sync] Pull failed: {pull_result.stderr}")
                # #region agent log
                _restored_on_fail = False
                if _had_real_stash:
                    _pop_r = self._run_git_command(["stash", "pop"])
                    _restored_on_fail = _pop_r.returncode == 0
                _agent_dbg(
                    "git_sync.py:pull_changes",
                    "pull_failed_after_restore_attempt",
                    {
                        "pull_rc": pull_result.returncode,
                        "had_real_stash": _had_real_stash,
                        "restored_stash_on_pull_fail": _restored_on_fail,
                    },
                    "H6",
                )
                # #endregion

                return False
                
        except Exception as e:
            print(f"[git_sync] Error pulling changes: {e}")
            return False
    
    def save_sync_state(self, state):
        """Save synchronization state to file."""
        try:
            state['last_sync'] = datetime.now().isoformat()
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"[git_sync] Error saving sync state: {e}")
    
    def load_sync_state(self):
        """Load synchronization state from file."""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"[git_sync] Error loading sync state: {e}")
        
        return {}
    
    def get_sync_status(self):
        """Get comprehensive sync status."""
        if not self.is_available():
            return {
                'available': False,
                'error': 'Git not available or not a Git repository'
            }
        
        try:
            current_commit = self.get_current_commit()
            remote_commit = self.get_remote_commit()
            has_changes = self.has_changes()
            changed_files = self.get_changed_files() if has_changes else []
            
            current_info = self.get_commit_info(current_commit) if current_commit else None
            remote_info = self.get_commit_info(remote_commit) if remote_commit else None
            
            status = {
                'available': True,
                'current_commit': current_commit,
                'remote_commit': remote_commit,
                'has_changes': has_changes,
                'changed_files': changed_files,
                'changed_files_count': len(changed_files),
                'current_info': current_info,
                'remote_info': remote_info,
                'last_sync': self.load_sync_state().get('last_sync')
            }
            
            return status
            
        except Exception as e:
            return {
                'available': False,
                'error': str(e)
            }
    
    def format_changes_summary(self, changed_files):
        """Format a summary of changed files."""
        if not changed_files:
            return "Keine Änderungen gefunden."
        
        summary = f"Geänderte Dateien ({len(changed_files)}):\n"
        
        # Group files by directory
        directories = {}
        for file in changed_files:
            dir_name = os.path.dirname(file)
            if dir_name not in directories:
                directories[dir_name] = []
            directories[dir_name].append(os.path.basename(file))
        
        for directory, files in sorted(directories.items()):
            if not directory:
                directory = "Root"
            summary += f"\n📁 {directory}/\n"
            for file in sorted(files)[:5]:  # Show max 5 files per directory
                summary += f"  • {file}\n"
            if len(files) > 5:
                summary += f"  • ... und {len(files) - 5} weitere Dateien\n"
        
        return summary
