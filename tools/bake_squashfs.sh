#!/bin/bash
# bake_squashfs.sh -- update and verify the SoloWebUtility2 golden copter image.
#
# firmware/3dr-solo.tar.gz IS the golden image (Open Solo 4.0.0 + SoloWebUtility2
# baked into the squashfs rootfs). `build` updates /usr/bin/SoloWebUtility2 inside
# it IN PLACE; everything else in the image is kept exactly as it is and checked
# against tools/stock_reference (a record of the pristine Open Solo 4.0.0 image).
#
# MUST run as root on a native Linux filesystem (inside WSL: `wsl -u root`).
# Never unpack the rootfs onto a Windows drive (/mnt/c ...): NTFS/drvfs drops
# execute and setuid bits, cannot hold device nodes, and is case-insensitive,
# which once produced an image whose /sbin/init was not executable.
#
# Usage:
#   bake_squashfs.sh build    GOLDEN_TAR APP_STAGING_DIR REF_DIR
#   bake_squashfs.sh verify   GOLDEN_TAR REF_DIR
#   bake_squashfs.sh mkref    STOCK_TAR  REF_DIR      (one-time, from pristine stock)
#
# Verification (build always runs it before replacing GOLDEN_TAR):
#   * container members = stock members; uImage/dtb/u-boot byte-identical to stock
#   * squashfs is gzip, 128K blocks
#   * every stock rootfs entry present and unchanged: type, mode incl. setuid,
#     numeric owner, file size, device numbers, symlink target
#     (directory sizes and /etc/inittab size are allowed to differ)
#   * the only new entries are under /usr/bin/SoloWebUtility2, all root-owned
#   * the only missing entries are REMOVED_ENTRIES (stock ArduCopter 4.0.1), which must be absent
#   * /etc/inittab = stock inittab + exactly one SWU respawn line, LF only
#   * app files in the image are byte-identical to the staged app (build only)
#   * GOLDEN_TAR.md5 is exactly "<md5>  3dr-solo.tar.gz\n"

set -euo pipefail
export LC_ALL=C

SQFS=3dr-solo-imx6solo-3dr-1080p.squashfs
SWU_LINE='SWU:345:respawn:/usr/bin/python /usr/bin/SoloWebUtility2/solo_web_app.py >>/log/solowebutility.log 2>&1'
SWU_COMMENT='# SoloWebUtility2 web server on port 80 (respawned by init)'
APP_REL=usr/bin/SoloWebUtility2
# Stock files deliberately removed from the image. /firmware/arducopter.apj is stock
# ArduCopter 4.0.1: Open Solo's boot script (pixhawk.py) flashes any .apj in /firmware
# to the Cube on the first boot after an update, which would overwrite the ArduCopter
# version Solo Web Utility installs. Without it, the Cube keeps the utility's version.
REMOVED_ENTRIES="/firmware/arducopter.apj"

die() { echo "[BAKE ERROR] $*" >&2; exit 1; }
log() { echo "    $*"; }

[ "$(id -u)" = "0" ] || die "must run as root (use: wsl -u root ...)"
command -v unsquashfs >/dev/null && command -v mksquashfs >/dev/null \
    || die "squashfs-tools not installed (in WSL: apt-get install -y squashfs-tools)"

WORK=$(mktemp -d /tmp/solo_bake.XXXXXX)
trap 'rm -rf "$WORK"' EXIT
case "$WORK" in /mnt/*) die "work dir $WORK is on a Windows drive";; esac
FSTYPE=$(stat -f -c %T "$WORK")
case "$FSTYPE" in v9fs|9p|drvfs|fuseblk|ntfs*|vfat|msdos) die "work dir is on $FSTYPE, need a Linux filesystem";; esac

# One line per rootfs entry: path <TAB> mode <TAB> uid/gid <TAB> detail
#   detail = file size | "major,minor" | symlink target | "" for directories
sqfs_manifest() {
    unsquashfs -n -lln "$1" 2>/dev/null | awk '
    {
        perm=$1; own=$2; i=index($0, "squashfs-root"); if (i == 0) next
        rest=substr($0, i + 13); if (rest == "") rest="/"
        t=substr(perm, 1, 1); detail=""
        if (t == "-") detail=$3
        else if (t == "c" || t == "b") { detail=$3 $4; gsub(/ /, "", detail) }
        else if (t == "l") { j=index(rest, " -> "); detail=substr(rest, j + 4); rest=substr(rest, 1, j - 1) }
        print rest "\t" perm "\t" own "\t" detail
    }' | sort
}

# Read a reference file, tolerating CRLF added by Windows editors/git
ref() { tr -d '\r' < "$REF/$1"; }

need_ref() {
    for f in rootfs.manifest inittab boot.md5 members.txt; do
        [ -s "$REF/$f" ] || die "missing stock reference file $REF/$f"
    done
}

verify_image() {   # tar  [staged_app_dir]
    local tar=$1 app=${2:-} d="$WORK/verify"
    rm -rf "$d"; mkdir -p "$d/c"
    need_ref

    # 1. container: exactly the stock members
    [ "$(tar -tzf "$tar" | sort)" = "$(ref members.txt | sort)" ] \
        || die "container members differ from stock: $(tar -tzf "$tar" | tr '\n' ' ')"
    tar -xzf "$tar" -C "$d/c"

    # 2. boot files byte-identical to stock
    (cd "$d/c" && ref boot.md5 | md5sum -c --quiet -) >/dev/null 2>&1 \
        || die "uImage / dtb / u-boot.imx differ from stock"

    # 3. squashfs format
    unsquashfs -s "$d/c/$SQFS" | grep -q "Compression gzip" || die "squashfs is not gzip"
    unsquashfs -s "$d/c/$SQFS" | grep -q "Block size 131072" || die "squashfs block size is not 131072"

    # 4. rootfs entries vs stock
    sqfs_manifest "$d/c/$SQFS" > "$d/new.lst"
    [ -s "$d/new.lst" ] || die "could not list the squashfs (unsquashfs needs -lln, squashfs-tools >= 4.4)"
    ref rootfs.manifest > "$d/stock.lst"
    awk -F'\t' -v app="/$APP_REL" -v removed="$REMOVED_ENTRIES" '
        BEGIN { n = split(removed, r, " "); for (i = 1; i <= n; i++) gone[r[i]] = 1 }
        NR == FNR { s[$1] = $2 "\t" $3 "\t" $4; st[$1] = substr($2, 1, 1); next }
        {
            seen[$1] = 1
            if ($1 in gone) { print "MUST BE REMOVED: " $1; bad++; next }
            if (!($1 in s)) {
                if ($1 != app && index($1, app "/") != 1) { print "UNEXPECTED NEW ENTRY: " $1; bad++ }
                else if ($3 != "0/0") { print "APP ENTRY NOT ROOT-OWNED: " $1 " " $3; bad++ }
                next
            }
            cur = $2 "\t" $3 "\t" $4; want = s[$1]
            if (st[$1] == "d" || $1 == "/etc/inittab") { split(want, a, "\t"); want = a[1] "\t" a[2]; cur = $2 "\t" $3 }
            if (cur != want) { print "CHANGED: " $1 "  stock[" want "]  image[" cur "]"; bad++ }
        }
        END {
            for (p in s) if (!(p in seen) && !(p in gone)) { print "MISSING: " p; bad++ }
            exit (bad > 0)
        }' "$d/stock.lst" "$d/new.lst" > "$d/diff.txt" || {
            head -40 "$d/diff.txt" >&2
            die "rootfs differs from stock in $(wc -l < "$d/diff.txt") place(s) (first 40 above)"
        }
    grep -q "^/$APP_REL/solo_web_app.py	-rwxr-xr-x	0/0	" "$d/new.lst" || die "solo_web_app.py missing or not 0755 root"

    # 5. inittab = stock + one SWU line
    unsquashfs -n -q -d "$d/r" "$d/c/$SQFS" etc/inittab >/dev/null
    grep -q $'\r' "$d/r/etc/inittab" && die "inittab contains CR characters"
    [ "$(grep -c '^SWU:' "$d/r/etc/inittab")" = "1" ] || die "inittab must contain exactly one SWU line"
    grep -qxF "$SWU_LINE" "$d/r/etc/inittab" || die "inittab SWU line is wrong"
    diff <(grep -v -e '^SWU:' -e '^# SoloWebUtility2' "$d/r/etc/inittab" | sed '/^$/d') \
         <(ref inittab | sed '/^$/d') >/dev/null || die "inittab differs from stock beyond the SWU line"

    # 6. app content = staged app (build only)
    if [ -n "$app" ]; then
        rm -rf "$d/r"
        unsquashfs -n -q -d "$d/r" "$d/c/$SQFS" "$APP_REL" >/dev/null
        diff <(cd "$app" && find . -type f -exec md5sum {} + | sort -k2) \
             <(cd "$d/r/$APP_REL" && find . -type f -exec md5sum {} + | sort -k2) >/dev/null \
            || die "app files inside the image differ from the staged app"
    fi

    # 7. md5 file: exactly "<md5>  3dr-solo.tar.gz\n"
    local want_md5 name=3dr-solo.tar.gz
    want_md5=$(md5sum "$tar" | cut -d' ' -f1)
    [ -f "$tar.md5" ] || die "missing $tar.md5"
    [ "$(cat "$tar.md5")" = "$want_md5  $name" ] || die "$tar.md5 content is wrong"
    [ "$(wc -c < "$tar.md5")" = "$(( ${#want_md5} + 2 + ${#name} + 1 ))" ] || die "$tar.md5 has the wrong line ending"

    local n_app
    n_app=$(grep -c "^/$APP_REL/" "$d/new.lst" || true)
    log "Verified: rootfs = stock - stock ArduCopter 4.0.1 + $n_app SoloWebUtility2 entries + SWU inittab line; boot files = stock; md5 $want_md5"
}

build_image() {   # golden_tar app_dir
    local golden=$1 app=$2
    [ -f "$golden" ] || die "golden image not found: $golden"
    [ -f "$app/solo_web_app.py" ] || die "app staging dir has no solo_web_app.py: $app"
    need_ref

    log "Unpacking golden image (as root, on $FSTYPE)..."
    mkdir -p "$WORK/boot"
    tar -xpzf "$golden" -C "$WORK/boot"
    [ -f "$WORK/boot/$SQFS" ] || die "$SQFS missing from $golden"
    unsquashfs -n -q -d "$WORK/root" "$WORK/boot/$SQFS" >/dev/null || die "unsquashfs failed"

    log "Replacing /$APP_REL in place..."
    rm -rf "$WORK/root/$APP_REL"
    mkdir -p "$WORK/root/$APP_REL"
    cp -r --no-preserve=all "$app/." "$WORK/root/$APP_REL/"
    find "$WORK/root/$APP_REL" -type f \( -name '*.py' -o -name '*.sh' -o -name '*.json' -o -name '*.html' \
        -o -name '*.css' -o -name '*.js' -o -name '*.txt' -o -name '*.param' -o -name '*.conf' -o -name '*.yaml' \) \
        -exec sed -i 's/\r$//' {} +
    chown -R 0:0 "$WORK/root/$APP_REL"
    find "$WORK/root/$APP_REL" -type d -exec chmod 0755 {} +
    find "$WORK/root/$APP_REL" -type f -exec chmod 0644 {} +
    find "$WORK/root/$APP_REL" -type f -name '*.py' -exec chmod 0755 {} +
    # keep a copy of exactly what went in, for the content check
    rm -rf "$WORK/app_in"; cp -a "$WORK/root/$APP_REL" "$WORK/app_in"

    local e
    for e in $REMOVED_ENTRIES; do
        log "Removing stock $e (Open Solo would flash it to the Cube on first boot)..."
        rm -f "$WORK/root$e"
    done

    log "Ensuring exactly one SWU respawn line in /etc/inittab..."
    local it="$WORK/root/etc/inittab"
    sed -i 's/\r$//' "$it"
    grep -v -e '^SWU:' -e '^# SoloWebUtility2' "$it" \
        | awk '{ l[NR] = $0 } END { n = NR; while (n > 0 && l[n] == "") n--; for (i = 1; i <= n; i++) print l[i] }' > "$it.new"
    printf '\n%s\n%s\n' "$SWU_COMMENT" "$SWU_LINE" >> "$it.new"
    cat "$it.new" > "$it"; rm -f "$it.new"

    log "Repacking squashfs (gzip, 128K blocks, original ownership)..."
    mksquashfs "$WORK/root" "$WORK/new.sqfs" -comp gzip -b 131072 -noappend -no-xattrs -no-progress -quiet >/dev/null \
        || die "mksquashfs failed"
    mv -f "$WORK/new.sqfs" "$WORK/boot/$SQFS"
    chmod 0644 "$WORK/boot/$SQFS"; chown 0:0 "$WORK/boot/$SQFS"
    touch -r "$WORK/boot/uImage" "$WORK/boot/$SQFS"

    log "Repacking container (stock member order)..."
    mkdir -p "$WORK/out"
    # shellcheck disable=SC2046
    tar --numeric-owner -C "$WORK/boot" -czf "$WORK/out/3dr-solo.tar.gz" $(ref members.txt)
    (cd "$WORK/out" && md5sum 3dr-solo.tar.gz > 3dr-solo.tar.gz.md5)

    verify_image "$WORK/out/3dr-solo.tar.gz" "$WORK/app_in"

    # replace the golden image only after verification passed
    cp -f "$WORK/out/3dr-solo.tar.gz" "$golden.tmp"
    cp -f "$WORK/out/3dr-solo.tar.gz.md5" "$golden.md5.tmp"
    mv -f "$golden.tmp" "$golden"
    mv -f "$golden.md5.tmp" "$golden.md5"
    log "Updated $golden in place ($(stat -c %s "$golden") bytes) and $golden.md5"
}

make_ref() {   # stock_tar ref_dir   -- one-time, from the pristine Open Solo tarball
    local stock=$1
    mkdir -p "$REF" "$WORK/s"
    tar -tzf "$stock" > "$REF/members.txt"
    tar -xzf "$stock" -C "$WORK/s"
    unsquashfs -n -q -d "$WORK/s/r" "$WORK/s/$SQFS" etc/inittab >/dev/null
    [ ! -e "$WORK/s/r/$APP_REL" ] || die "not a stock image"
    grep -q '^SWU:' "$WORK/s/r/etc/inittab" && die "not a stock image (inittab has SWU line)"
    cp "$WORK/s/r/etc/inittab" "$REF/inittab"
    (cd "$WORK/s" && md5sum $(grep -v "^$SQFS\$" "$REF/members.txt") </dev/null) > "$REF/boot.md5"
    sqfs_manifest "$WORK/s/$SQFS" > "$REF/rootfs.manifest"
    [ -s "$REF/rootfs.manifest" ] || die "could not list the squashfs (unsquashfs needs -lln, squashfs-tools >= 4.4)"
    grep -q "^/$APP_REL" "$REF/rootfs.manifest" && die "not a stock image"
    log "Wrote stock reference to $REF ($(wc -l < "$REF/rootfs.manifest") rootfs entries)"
}

absdir() { mkdir -p "$1" && (cd "$1" && pwd); }
abspath() { echo "$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; }

case "${1:-}" in
    build)  [ $# -eq 4 ] || die "usage: $0 build GOLDEN_TAR APP_STAGING_DIR REF_DIR"
            REF=$(absdir "$4"); build_image "$(abspath "$2")" "$(absdir "$3")" ;;
    verify) [ $# -eq 3 ] || die "usage: $0 verify GOLDEN_TAR REF_DIR"
            REF=$(absdir "$3"); verify_image "$(abspath "$2")" ;;
    mkref)  [ $# -eq 3 ] || die "usage: $0 mkref STOCK_TAR REF_DIR"
            REF=$(absdir "$3"); make_ref "$(abspath "$2")" ;;
    *) die "usage: $0 build GOLDEN_TAR APP_DIR REF_DIR | verify GOLDEN_TAR REF_DIR | mkref STOCK_TAR REF_DIR" ;;
esac
