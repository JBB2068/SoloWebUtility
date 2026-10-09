#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
3DR Solo Web Utility - Password Management CLI Tool
Usage:
    python change_password.py
    python change_password.py <new_password>
    python change_password.py --reset
"""

import sys
import getpass
import auth


def main():
    print("=" * 60)
    print("[*] 3DR SOLO WEB UTILITY - PASSWORD MANAGEMENT TOOL")
    print("=" * 60)

    if len(sys.argv) > 1:
        arg = sys.argv[1].strip()
        if arg in ["--reset", "-r", "reset"]:
            success, msg = auth.force_set_password(auth.DEFAULT_PASSWORD)
            if success:
                print("[OK] Password reset to default: '%s'" % auth.DEFAULT_PASSWORD)
            else:
                print("[!] Error resetting password: %s" % msg)
            return
        elif not arg.startswith("-"):
            new_pwd = arg
            success, msg = auth.force_set_password(new_pwd)
            if success:
                print("[OK] Password successfully changed!")
            else:
                print("[!] Error: %s" % msg)
            return

    # Interactive Mode
    auth_data = auth.get_auth_data()
    is_default = auth_data.get("is_default", False)
    if is_default:
        print("[!] Current password is set to default ('sololink').")
    
    try:
        if sys.version_info[0] >= 3:
            get_input = input
        else:
            get_input = raw_input
    except Exception:
        get_input = input

    try:
        new_pwd = getpass.getpass("Enter New Web Utility Password: ")
        if not new_pwd:
            print("[!] Password cannot be empty.")
            return
        
        confirm_pwd = getpass.getpass("Confirm New Web Utility Password: ")
        if new_pwd != confirm_pwd:
            print("[!] Error: Passwords do not match.")
            return

        success, msg = auth.force_set_password(new_pwd)
        if success:
            print("\n[✓] Password successfully updated!")
            print("[*] You can now log into the web portal with your new password.")
        else:
            print("\n[!] Failed to update password: %s" % msg)
    except (KeyboardInterrupt, EOFError):
        print("\nOperation cancelled.")


if __name__ == "__main__":
    main()
