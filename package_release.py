#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
3DR Solo Web Utility — Release Packaging Script
Generates clean distribution packages (Tester Package & AppOnly Lite) for release.
Excludes backup folders (e.g. v1_0_56_backup, *_backup), distribution folder,
build artifacts, VCS files, and nested .zip archives without deleting local workspace files.
"""

import os
import sys
import zipfile
import fnmatch
from version_mgr import get_version

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Global directories to ignore for ALL packages
IGNORE_DIRS = {
    'v1_0_56_backup',
    'distribution',
    'build',
    'dist',
    '__pycache__',
    '.system_generated',
    'scratch',
    '.git',
    '.wrangler',
    '.venv',
    '.idea',
    '.vscode',
    'build_temp',
    'res_compiled'
}

# Directories to exclude for AppOnly Lite package
LITE_EXCLUDE_DIRS = {
    'firmware',
    'docs'
}

# Specific files to exclude for AppOnly Lite package
LITE_EXCLUDE_FILES = {
    'deploy_to_solo.exe',
    'deploy_to_solo.spec',
    'sololink_viewer.apk',
    'sololink_viewer.apk.idsig'
}

def should_ignore_dir(dir_name):
    """Check if directory name matches ignore criteria."""
    if dir_name in IGNORE_DIRS:
        return True
    if dir_name.endswith('_backup') or 'backup' in dir_name.lower():
        return True
    return False

TESTER_EXCLUDE_FILES = {
    'arducoptercubegreen-solo-4.7.1.apj',
    'arducoptercubesolo-4.7.1.apj',
}

TESTER_EXCLUDE_PREFIXES = (
    'Open Solo 4.0.0 Clean Install',
    'index_temp',
    'cockpit_temp',
    'cockpit_mgr_temp'
)

def create_tester_package(version):
    """Create full SoloWebUtility_v<version>_Tester_Package.zip."""
    zip_name = "SoloWebUtility_v%s_Tester_Package.zip" % version
    zip_path = os.path.join(BASE_DIR, zip_name)
    
    print("[*] Building Tester Package: %s" % zip_name)
    file_count = 0
    
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(BASE_DIR):
            # Exclude ignored directories in-place so os.walk does not recurse into them
            dirs[:] = [d for d in dirs if not should_ignore_dir(d)]
            
            for f in files:
                # Do not zip existing zip files, temporary files, or compiled python files
                if f.endswith('.zip') or f.endswith('.apk') or f.endswith('.exe') or f.endswith('.pyc') or f.startswith('.~'):
                    continue

                # Exclude duplicate binaries or redundant firmware zip files
                if f in TESTER_EXCLUDE_FILES:
                    continue

                if f.startswith(TESTER_EXCLUDE_PREFIXES):
                    continue

                abs_file = os.path.join(root, f)
                rel_file = os.path.relpath(abs_file, BASE_DIR)

                # Skip the output zip itself if matching
                if abs_file == zip_path:
                    continue

                zf.write(abs_file, rel_file)
                file_count += 1
                
    size_mb = os.path.getsize(zip_path) / (1024.0 * 1024.0)
    print("[+] Created %s successfully (%d files, %.2f MB)" % (zip_name, file_count, size_mb))
    return zip_path, size_mb

def create_lite_package(version):
    """Create lightweight SoloWebUtility_v<version>_AppOnly_Lite.zip."""
    zip_name = "SoloWebUtility_v%s_AppOnly_Lite.zip" % version
    zip_path = os.path.join(BASE_DIR, zip_name)
    
    print("[*] Building AppOnly Lite Package: %s" % zip_name)
    file_count = 0
    
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(BASE_DIR):
            dirs[:] = [d for d in dirs if not should_ignore_dir(d) and d not in LITE_EXCLUDE_DIRS]
            
            for f in files:
                if f.endswith('.zip') or f.endswith('.apk') or f.endswith('.exe') or f.endswith('.pyc') or f.startswith('.~'):
                    continue
                if f in LITE_EXCLUDE_FILES:
                    continue
                
                abs_file = os.path.join(root, f)
                rel_file = os.path.relpath(abs_file, BASE_DIR)
                
                # Exclude top-level files that belong to backup/temp/test artifacts
                if root == BASE_DIR and (f.startswith('index_temp') or f.startswith('cockpit_temp') or f.startswith('cockpit_mgr_temp')):
                    continue

                if abs_file == zip_path:
                    continue
                
                zf.write(abs_file, rel_file)
                file_count += 1
                
    size_mb = os.path.getsize(zip_path) / (1024.0 * 1024.0)
    print("[+] Created %s successfully (%d files, %.2f MB)" % (zip_name, file_count, size_mb))
    return zip_path, size_mb

def package_all():
    ver = get_version()
    print("==================================================")
    print(" 3DR Solo Web Utility Release Packaging (v%s)" % ver)
    print("==================================================")
    t_path, t_size = create_tester_package(ver)
    l_path, l_size = create_lite_package(ver)
    print("--------------------------------------------------")
    print("Package Summary:")
    print("  1. %s (%.2f MB)" % (os.path.basename(t_path), t_size))
    print("  2. %s (%.2f MB)" % (os.path.basename(l_path), l_size))
    print("==================================================")

if __name__ == "__main__":
    package_all()
