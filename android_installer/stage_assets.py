"""Stage the files the phone installer copies to the Solo, plus assets/manifest.txt.

Uses the same file list as the Windows installer (release/Solo Web Utility v1.2.2.spec),
so the .exe and the .apk always install exactly the same files.
Usage: python stage_assets.py <assets_dir>
"""
import hashlib
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(HERE, "..", "release", "Solo Web Utility v1.2.2.spec")


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    out = os.path.abspath(sys.argv[1])
    g = {"SPECPATH": os.path.dirname(os.path.abspath(SPEC))}
    with open(SPEC) as f:
        exec(f.read().split("a = Analysis(")[0], g)   # collects g["datas"], fails on missing files
    shutil.rmtree(out, ignore_errors=True)
    lines = []
    for src, dst in g["datas"]:
        dst = dst.replace("\\", "/")
        rel = os.path.basename(src) if dst in (".", "") else dst + "/" + os.path.basename(src)
        kind = "fw" if rel.startswith("firmware/") else "app"
        target = os.path.join(out, "swu", *rel.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(src, target)
        lines.append("%s\t%d\t%s\t%s" % (kind, os.path.getsize(src), md5(src), rel))
    lines.sort(key=lambda l: (l.split("\t")[0] != "app", l.split("\t")[3]))
    with open(os.path.join(out, "manifest.txt"), "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("Staged %d files (%d app, %d firmware)" % (
        len(lines), sum(1 for l in lines if l.startswith("app")), sum(1 for l in lines if l.startswith("fw"))))


if __name__ == "__main__":
    main()
