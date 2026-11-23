#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Push changed Solo Web Utility files to the Solo (10.1.1.10) -- runs on the PC, not on the Solo.

Compares every app file (the same list deploy_to_drone.py installs: top-level .py files,
version.json, modules/, templates/, static/, bin/) with the copy on the Solo by MD5, then:
  1. shows what changed and asks once (skip the question with --yes),
  2. backs up each Solo file it replaces to <file>.bak_push,
  3. uploads the changed files and checks their MD5 on the Solo,
  4. restarts the web app only if a Python file changed (pages and the Viewer APK need no restart).
No firmware, no baking, nothing else on the Solo. Uses deploy_to_drone.py's login and upload code.

  python tools\\push_to_solo.py           (or double-click PUSH_TO_SOLO.bat)
  python tools\\push_to_solo.py --dry-run  only list what differs
  python tools\\push_to_solo.py --yes      no question
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import deploy_to_drone as dd   # noqa: E402

TARGET = dd.REMOTE_DIR          # /log/SoloWebUtility2


def remote_md5s(client, paths):
    """{remote path: md5} for the files that exist on the Solo (batched: command line length)."""
    out = {}
    for i in range(0, len(paths), 40):
        chunk = paths[i:i + 40]
        _, text = dd.ssh_exec(client, "md5sum %s 2>/dev/null || true" % " ".join("'%s'" % p for p in chunk))
        for line in text.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and len(parts[0]) == 32:
                out[parts[1].lstrip("*")] = parts[0].lower()
    return out


def restart_web_app(client):
    app_entry = "%s/solo_web_app.py" % TARGET
    dd.ssh_exec(client, "for p in $(ps | grep -E 'solo_web_app|SoloWebUtility2/app.py' | grep -v grep | awk '{print $1}'); do kill $p 2>/dev/null; done")
    time.sleep(3.0)                                  # init respawns it (inittab SWU entry)
    _, ps = dd.ssh_exec(client, "ps | grep solo_web_app | grep -v grep || true")
    if not ps.strip():
        dd.ssh_exec(client, "nohup /usr/bin/python %s 80 </dev/null >>/log/solowebutility.log 2>&1 &" % app_entry, timeout=5.0)
        time.sleep(2.0)
        _, ps = dd.ssh_exec(client, "ps | grep solo_web_app | grep -v grep || true")
    if ps.strip():
        print("[OK] Web app restarted.")
        return True
    print("[!] The web app is not running. Last log lines:")
    _, log = dd.ssh_exec(client, "tail -n 15 /log/solowebutility.log 2>/dev/null || true")
    print(log)
    return False


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    yes = "--yes" in args
    print("=" * 60)
    print(" Push changed files to the Solo (%s) -- v%s" % (dd.SOLO_IP, dd.get_installer_version()))
    print("=" * 60)

    items = [(rel, remote) for rel, remote in dd.build_app_file_list(TARGET)
             if os.path.isfile(os.path.join(ROOT, rel))]
    print("[*] %d app files on this PC." % len(items))

    if not dd.test_connection(dd.SOLO_IP, dd.PORT):
        print("[!] Cannot reach the Solo at %s. Connect this PC to the SoloLink Wi-Fi and try again." % dd.SOLO_IP)
        return 1
    try:
        import paramiko
    except ImportError:
        print("[!] paramiko is required on this PC: pip install paramiko")
        return 1
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    for attempt in range(3):
        try:
            client.connect(dd.SOLO_IP, port=dd.PORT, username=dd.USER, password=dd.PASSWORD,
                           timeout=15.0, look_for_keys=False, allow_agent=False)
            break
        except Exception as e:
            if attempt == 2:
                print("[!] SSH login failed: %s" % e)
                return 1
            time.sleep(1.5)
    client.get_transport().set_keepalive(15)

    try:
        theirs = remote_md5s(client, [remote for _, remote in items])
        changed = []
        for rel, remote in items:
            mine = dd.calc_file_md5(os.path.join(ROOT, rel))
            if theirs.get(remote) != mine:
                changed.append((rel, remote, mine, remote in theirs))
        if not changed:
            print("[OK] The Solo already has every file. Nothing to do.")
            return 0
        print("\n[*] %d file(s) differ:" % len(changed))
        for rel, _, _, exists in changed:
            print("    %s%s" % (rel, "" if exists else "   (new on the Solo)"))
        needs_restart = any(rel.endswith(".py") for rel, _, _, _ in changed)
        print("\n    Web app restart afterwards: %s" % ("YES (a .py file changed)" if needs_restart else "no"))
        if dry:
            return 0
        if not yes:
            ans = input("\nCopy these to the Solo? [y/N] ").strip().lower()
            if ans not in ("y", "yes"):
                print("Nothing copied.")
                return 0

        dd.ssh_exec(client, "mount -o remount,rw /log 2>/dev/null || true")
        sftp = None
        try:
            sftp = client.open_sftp()
        except Exception:
            sftp = None                              # upload_app_file falls back to base64/cat
        failed = []
        for rel, remote, mine, exists in changed:
            print("\n[*] %s" % rel)
            if exists:
                dd.ssh_exec(client, "cp -p '%s' '%s.bak_push'" % (remote, remote))
            if not dd.upload_app_file(client, sftp, os.path.join(ROOT, rel), remote):
                failed.append(rel)
                continue
            if remote_md5s(client, [remote]).get(remote) != mine:
                print("    [!] MD5 on the Solo does not match.")
                failed.append(rel)
        if sftp is not None:
            try:
                sftp.close()
            except Exception:
                pass
        dd.ssh_exec(client, "sync")

        if failed:
            print("\n[!] %d file(s) failed: %s" % (len(failed), ", ".join(failed)))
            print("    The old versions are kept as <file>.bak_push on the Solo. Web app NOT restarted.")
            return 1
        print("\n[OK] %d file(s) copied and checked. Backups: <file>.bak_push on the Solo." % len(changed))
        if needs_restart:
            print("[*] Restarting the web app...")
            if not restart_web_app(client):
                return 1
        print("\nDone. Pull to refresh the Cockpit on the phone.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    rc = main()
    sys.exit(rc)
