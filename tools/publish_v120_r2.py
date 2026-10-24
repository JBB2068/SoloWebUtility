#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Upload v1.2.0 release binaries to Cloudflare R2 bucket open-solo-downloads.
"""
import os
import sys
import time
import subprocess

DIST_DIR = r"C:\Users\User\Documents\SoloWebUtility2\release\dist"
EXE_NAME = "Solo Web Utility v1.2.0 Installer.exe"
APK_NAME = "Solo Web Utility v1.2.0 Installer.apk"
EXE_FILE = os.path.join(DIST_DIR, EXE_NAME)
APK_FILE = os.path.join(DIST_DIR, APK_NAME)

# The site serves exactly these two objects; older download names are redirected
# to them by worker/index.js, so nothing else is uploaded.
UPLOADS = [
    {
        "key": EXE_NAME,
        "file": EXE_FILE,
        "content_type": "application/octet-stream",
        "disposition": 'attachment; filename="%s"' % EXE_NAME
    },
    {
        "key": APK_NAME,
        "file": APK_FILE,
        "content_type": "application/vnd.android.package-archive",
        "disposition": 'attachment; filename="%s"' % APK_NAME
    },
]

def upload_all():
    print("==================================================")
    print(" Uploading Solo Web Utility v1.2.0 to R2")
    print(" Bucket: open-solo-downloads")
    print("==================================================")
    
    total = len(UPLOADS)
    for idx, item in enumerate(UPLOADS, 1):
        key = item["key"]
        file_path = item["file"]
        content_type = item["content_type"]
        disposition = item["disposition"]
        
        if not os.path.exists(file_path):
            print("[ERROR] Source file not found: %s" % file_path)
            sys.exit(1)
            
        size_mb = os.path.getsize(file_path) / (1024.0 * 1024.0)
        target = "open-solo-downloads/%s" % key
        
        cmd = [
            "cmd.exe", "/c", "npx", "wrangler", "r2", "object", "put",
            target,
            "--file", file_path,
            "--content-type", content_type,
            "--content-disposition", disposition,
            "--remote"
        ]
        
        print("\n[%d/%d] Uploading '%s' (%.2f MB)..." % (idx, total, key, size_mb))
        
        success = False
        for attempt in range(1, 4):
            if attempt > 1:
                print("   Retry attempt %d/3..." % attempt)
            
            t0 = time.time()
            res = subprocess.call(cmd)
            elapsed = time.time() - t0
            
            if res == 0:
                print("[+] Successfully uploaded '%s' in %.1fs (%.2f MB)" % (key, elapsed, size_mb))
                success = True
                break
            else:
                print("[-] Failed attempt %d for '%s' (exit code %d)" % (attempt, key, res))
                time.sleep(3)
                
        if not success:
            print("[FATAL] Could not upload '%s' after 3 attempts." % key)
            sys.exit(1)
            
    print("\n==================================================")
    print("[+] All v1.2.0 binaries successfully uploaded to R2!")
    print("==================================================")

if __name__ == "__main__":
    upload_all()
