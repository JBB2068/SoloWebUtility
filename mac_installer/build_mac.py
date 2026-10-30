"""Build "Solo Web Utility v1.2.2 (Mac).zip": the Mac installer for Solo Web Utility.

Packs install_solo_web_utility.command (as "Install Solo Web Utility.command") with the
files staged by android_installer/stage_assets.py, so the Mac installer copies exactly
the same files as the .exe and the .apk. Runs on Windows, macOS or Linux (Python 3).
Output: ../release/dist/Solo Web Utility v1.2.2 (Mac).zip
"""
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STAGE = os.path.join(ROOT, "android_installer", "stage_assets.py")
DIST = os.path.join(ROOT, "release", "dist")
ZIP_NAME = "Solo Web Utility v1.2.2 (Mac).zip"
TOP = "Solo Web Utility v1.2.2 Installer"
SCRIPT_NAME = "Install Solo Web Utility.command"
NO_COMPRESS = (".gz", ".zip", ".png", ".jpg", ".jpeg", ".apj")


def add_bytes(z, name, data, mode):
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.create_system = 3                      # Unix, so Archive Utility keeps the mode bits
    info.external_attr = (0o100000 | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    z.writestr(info, data)


def add_file(z, name, path):
    info = zipfile.ZipInfo.from_file(path, name)
    info.create_system = 3
    info.external_attr = (0o100000 | 0o644) << 16
    stored = path.lower().endswith(NO_COMPRESS)
    info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    with open(path, "rb") as src, z.open(info, "w") as dst:
        shutil.copyfileobj(src, dst, 1 << 20)


def main():
    print("=" * 58)
    print("  Building " + ZIP_NAME)
    print("=" * 58)
    work = tempfile.mkdtemp(prefix="swu_mac_")
    try:
        files_dir = os.path.join(work, "files")
        print("[1/3] Staging the files to install (same list as the Windows installer)...")
        subprocess.check_call([sys.executable, STAGE, files_dir])

        with open(os.path.join(HERE, "install_solo_web_utility.command"), "rb") as f:
            script = f.read().replace(b"\r\n", b"\n")     # a CRLF checkout would break bash
        with open(os.path.join(HERE, "README.txt"), "rb") as f:
            readme = f.read().replace(b"\r\n", b"\n")

        os.makedirs(DIST, exist_ok=True)
        out = os.path.join(DIST, ZIP_NAME)
        tmp_out = out + ".part"
        print("[2/3] Packing %s..." % ZIP_NAME)
        count = 0
        with zipfile.ZipFile(tmp_out, "w", allowZip64=True) as z:
            add_bytes(z, TOP + "/" + SCRIPT_NAME, script, 0o755)
            add_bytes(z, TOP + "/README.txt", readme, 0o644)
            for cur, dirs, names in os.walk(files_dir):
                dirs.sort()
                for n in sorted(names):
                    p = os.path.join(cur, n)
                    rel = os.path.relpath(p, work).replace(os.sep, "/")
                    add_file(z, TOP + "/" + rel, p)
                    count += 1
        os.replace(tmp_out, out)

        print("[3/3] Checking the zip...")
        with open(os.path.join(files_dir, "manifest.txt")) as f:
            wanted = {TOP + "/files/swu/" + l.rstrip("\n").split("\t")[3] for l in f if l.strip()}
        with zipfile.ZipFile(out) as z:
            have = set(z.namelist())
            bad = z.testzip()
        missing = sorted(wanted - have)
        if missing or bad or TOP + "/" + SCRIPT_NAME not in have:
            raise SystemExit("Zip check failed. Missing: %s  Damaged: %s" % (", ".join(missing[:10]) or "-", bad or "-"))
        print("    %d files packed." % count)
        print("=" * 58)
        print("[OK] Built %s (%.1f MB)" % (out, os.path.getsize(out) / 1048576.0))
        print("=" * 58)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
