"""
Update Downloader for JARVIS Auto-Update System

Handles secure download and verification of update files.
"""

import os
import hashlib
import asyncio
import aiohttp
import tempfile
from pathlib import Path

class UpdateDownloader:
    """Handles downloading and verification of update files."""
    
    def __init__(self, temp_dir = None):
        """Initialize downloader with temp directory."""
        if temp_dir is None:
            self.temp_dir = tempfile.mkdtemp(prefix="jarvis_update_")
        else:
            self.temp_dir = temp_dir
        
        os.makedirs(self.temp_dir, exist_ok=True)
    
    async def download_release(self, download_url, 
                             progress_callback = None,
                             max_retries = 3):
        """
        Download release ZIP file with progress tracking and retry mechanism.
        
        Args:
            download_url: URL to download from
            progress_callback: Callback for progress updates (downloaded, total)
            max_retries: Maximum number of retry attempts
            
        Returns:
            Path to downloaded file or None if failed
        """
        for attempt in range(max_retries):
            try:
                print(f"[updater] Download attempt {attempt + 1}/{max_retries}: {download_url}")
                
                async with aiohttp.ClientSession() as session:
                    headers = {
                        "User-Agent": "JARVIS-Updater/1.0"
                    }
                    
                    async with session.get(download_url, headers=headers) as response:
                        if response.status != 200:
                            print(f"[updater] Download failed: HTTP {response.status}")
                            if attempt == max_retries - 1:
                                return None
                            await asyncio.sleep(2 ** attempt)  # Exponential backoff
                            continue
                        
                        # Get file size for progress tracking
                        total_size = int(response.headers.get('content-length', 0))
                        downloaded = 0
                        
                        # Generate filename from URL
                        filename = self._get_filename_from_url(download_url)
                        file_path = os.path.join(self.temp_dir, filename)
                        
                        # Download file with progress tracking
                        with open(file_path, 'wb') as f:
                            async for chunk in response.content.iter_chunked(8192):
                                f.write(chunk)
                                downloaded += len(chunk)
                                
                                if progress_callback and total_size > 0:
                                    progress_callback(downloaded, total_size)
                        
                        print(f"[updater] Download completed: {file_path}")
                        return file_path
                        
            except Exception as e:
                print(f"[updater] Download error (attempt {attempt + 1}): {e}")
                if attempt == max_retries - 1:
                    return None
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
        
        return None
    
    def _get_filename_from_url(self, url: str) -> str:
        """Extract filename from download URL."""
        try:
            # Try to get filename from URL path
            if '/' in url:
                filename = url.split('/')[-1]
                if filename and '.' in filename:
                    return filename
            
            # Fallback: generate filename based on timestamp
            import time
            return f"jarvis_update_{int(time.time())}.zip"
            
        except Exception:
            import time
            return f"jarvis_update_{int(time.time())}.zip"
    
    def calculate_sha256(self, file_path):
        """Calculate SHA256 checksum of a file."""
        try:
            sha256_hash = hashlib.sha256()
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(chunk)
            return sha256_hash.hexdigest()
        except Exception as e:
            print(f"[updater] Error calculating SHA256: {e}")
            return ""
    
    def verify_checksum(self, file_path, expected_checksum):
        """Verify file checksum matches expected value."""
        try:
            if not expected_checksum:
                print("[updater] No checksum provided, skipping verification")
                return True
            
            actual_checksum = self.calculate_sha256(file_path)
            
            if actual_checksum.lower() == expected_checksum.lower():
                print("[updater] Checksum verification passed")
                return True
            else:
                print(f"[updater] Checksum mismatch: expected {expected_checksum}, got {actual_checksum}")
                return False
                
        except Exception as e:
            print(f"[updater] Error verifying checksum: {e}")
            return False
    
    def verify_zip_integrity(self, file_path):
        """Verify that the downloaded ZIP file is not corrupted."""
        try:
            import zipfile
            with zipfile.ZipFile(file_path, 'r') as zf:
                # Test the ZIP file integrity
                bad_file = zf.testzip()
                if bad_file:
                    print(f"[updater] ZIP file corrupted: {bad_file}")
                    return False
                
                # Check if ZIP contains expected files
                file_list = zf.namelist()
                if not file_list:
                    print("[updater] ZIP file is empty")
                    return False
                
                print(f"[updater] ZIP integrity verified: {len(file_list)} files")
                return True
                
        except Exception as e:
            print(f"[updater] Error verifying ZIP integrity: {e}")
            return False
    
    async def download_and_verify(self, download_url,
                                expected_checksum = None,
                                progress_callback = None):
        """
        Download and verify update file.
        
        Args:
            download_url: URL to download from
            expected_checksum: Expected SHA256 checksum (optional)
            progress_callback: Progress callback function
            
        Returns:
            Path to verified file or None if failed
        """
        try:
            # Download the file
            file_path = await self.download_release(download_url, progress_callback)
            if not file_path:
                return None
            
            # Verify checksum if provided
            if expected_checksum and not self.verify_checksum(file_path, expected_checksum):
                # Clean up corrupted file
                try:
                    os.remove(file_path)
                except:
                    pass
                return None
            
            # Verify ZIP integrity
            if not self.verify_zip_integrity(file_path):
                # Clean up corrupted file
                try:
                    os.remove(file_path)
                except:
                    pass
                return None
            
            print(f"[updater] Download and verification completed: {file_path}")
            return file_path
            
        except Exception as e:
            print(f"[updater] Error in download_and_verify: {e}")
            return None
    
    def cleanup(self):
        """Clean up temporary files."""
        try:
            import shutil
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir, ignore_errors=True)
                print(f"[updater] Cleaned up temp directory: {self.temp_dir}")
        except Exception as e:
            print(f"[updater] Error cleaning up: {e}")
    
    def get_temp_dir(self) -> str:
        """Get the temporary directory path."""
        return self.temp_dir
