#!/bin/sh
# golden_write.sh -- update the factory reset (recovery, GOLDEN) partition of a 3DR Solo or Solo Controller.
# Runs ON the device (Open Solo, BusyBox/bash, POSIX sh only). Started by gimbal-style runner golden_update.py:
#   sh golden_write.sh status     what GOLDEN holds (files + MD5, versions, free space, backup, unfinished run)
#   sh golden_write.sh write      copy the boot files of the RUNNING system (/mnt/boot) onto GOLDEN
#   sh golden_write.sh restore    put the backed-up GOLDEN files back
#
# Unlike Open Solo's makeGolden.sh / sololink_config --make-golden (mkfs.vfat GOLDEN, then cp, no checks):
#   * GOLDEN is never reformatted; files that are not boot files (e.g. the Wi-Fi region file .reg) are kept
#   * the current GOLDEN files are backed up to /log/golden_backup first (MD5-checked)
#   * the new files are staged in /log and MD5-checked before GOLDEN is touched
#   * every file goes to GOLDEN as <name>.new, is synced and MD5-checked, then renamed into place; only when
#     GOLDEN has no room for a second copy is the old file deleted first (the only unprotected moment)
#   * a marker file in /log records an unfinished run, so it can be retried or the backup restored
#   * everything is re-checked from a read-only mount at the end
#   * no factory reset, no reboot, nothing else on the device is changed
# Output: progress lines "[GOLDEN] ...", then "[GOLDEN_DONE] <action>" or "[GOLDEN_FAILED] <reason>".
#
# Test hooks (the test suite sets these; never set on a device):
#   SWU_GOLDEN_TEST=1      GOLDEN_DEV is a plain folder (no mount/umount), capacity GOLDEN_TEST_TOTAL_KB
#   BOOT_DIR, LOG_DIR, SOLOLINK_CONFIG

ACTION="$1"
GOLDEN_DEV="${GOLDEN_DEV:-/dev/mmcblk0p1}"
BOOT_DIR="${BOOT_DIR:-/mnt/boot}"
LOG_DIR="${LOG_DIR:-/log}"
SOLOLINK_CONFIG="${SOLOLINK_CONFIG:-sololink_config}"
MNT="${GOLDEN_MNT:-/tmp/swu_golden}"
SQMNT="${MNT}_sq"
BACKUP="$LOG_DIR/golden_backup"
STAGE="$LOG_DIR/golden_stage"
MARK="$LOG_DIR/golden_update.marker"
MARGIN_KB=512

if [ -n "$SWU_GOLDEN_TEST" ]; then
    MNT="$GOLDEN_DEV"
fi

say() { echo "[GOLDEN] $*"; }
step() { echo "[GOLDEN] step $1 $2"; }

unmount_all() {
    if [ -z "$SWU_GOLDEN_TEST" ]; then
        sync
        umount "$SQMNT" 2>/dev/null
        umount "$MNT" 2>/dev/null
        rmdir "$SQMNT" "$MNT" 2>/dev/null
    fi
}

fail() {
    echo "[GOLDEN_FAILED] $*"
    unmount_all
    rm -rf "$STAGE" "$BACKUP.new" 2>/dev/null
    exit 1
}

md5of() { md5sum "$1" | awk '{print $1}'; }
sizeof() { ls -l "$1" | awk '{print $5}'; }
kb_of() { echo $(( ( $1 + 1023 ) / 1024 )); }

is_boot_file() {
    case "$1" in
        *.squashfs|*.dtb|uImage|u-boot.imx) return 0 ;;
    esac
    return 1
}

# ---- GOLDEN mount helpers ------------------------------------------------------------------------
golden_mounted_elsewhere() {
    [ -n "$SWU_GOLDEN_TEST" ] && return 1
    grep -q "^$GOLDEN_DEV " /proc/mounts
}

mount_golden() {   # mount_golden ro|rw
    if [ -n "$SWU_GOLDEN_TEST" ]; then
        [ -d "$MNT" ] || fail "test GOLDEN folder $MNT missing"
        return 0
    fi
    if golden_mounted_elsewhere; then
        # sololink_config --get-version golden can leave it mounted here; anything else is in use
        umount /mnt/golden-squashfs 2>/dev/null
        umount /mnt/golden 2>/dev/null
        rmdir /mnt/golden-squashfs /mnt/golden 2>/dev/null
        if golden_mounted_elsewhere; then
            fail "the factory reset partition is in use ($(grep "^$GOLDEN_DEV " /proc/mounts | awk '{print $2}'))"
        fi
    fi
    mkdir -p "$MNT" || fail "could not create $MNT"
    mount -t vfat -o "$1" "$GOLDEN_DEV" "$MNT" || fail "could not mount the factory reset partition ($1)"
}

umount_golden() {
    [ -n "$SWU_GOLDEN_TEST" ] && return 0
    sync
    umount "$MNT" || fail "could not unmount the factory reset partition"
    rmdir "$MNT" 2>/dev/null
}

golden_total_kb() {
    if [ -n "$SWU_GOLDEN_TEST" ]; then echo "$GOLDEN_TEST_TOTAL_KB"; return; fi
    df -k "$MNT" | tail -n 1 | awk '{print $2}'
}

golden_used_kb() {
    if [ -n "$SWU_GOLDEN_TEST" ]; then
        t=0
        for f in "$MNT"/* "$MNT"/.[!.]*; do [ -f "$f" ] && t=$(( t + $(kb_of $(sizeof "$f")) )); done
        echo "$t"; return
    fi
    df -k "$MNT" | tail -n 1 | awk '{print $3}'
}

golden_avail_kb() { echo $(( $(golden_total_kb) - $(golden_used_kb) )); }

golden_files() {   # every file on GOLDEN, dotfiles included (names only)
    for f in "$MNT"/* "$MNT"/.[!.]*; do
        [ -f "$f" ] && basename "$f"
    done
}

running_image() {
    if [ -n "$SWU_GOLDEN_TEST" ]; then echo "latest"; return; fi
    "$SOLOLINK_CONFIG" --get-image 2>/dev/null | head -n 1
}

# ---- versions inside a squashfs -------------------------------------------------------------------
squashfs_versions() {   # squashfs_versions <file>: prints "os=<VERSION line 1> swu=<utility version or ->"
    os="-"; swu="-"
    if [ -z "$SWU_GOLDEN_TEST" ]; then
        mkdir -p "$SQMNT"
        if mount -o ro "$1" "$SQMNT" 2>/dev/null; then
            [ -f "$SQMNT/VERSION" ] && os=$(head -n 1 "$SQMNT/VERSION")
            if [ -f "$SQMNT/usr/bin/SoloWebUtility2/version.json" ]; then
                swu=$(grep '"version"' "$SQMNT/usr/bin/SoloWebUtility2/version.json" | head -n 1 | sed 's/.*"version" *: *"\([^"]*\)".*/\1/')
            fi
            umount "$SQMNT"
        fi
        rmdir "$SQMNT" 2>/dev/null
    else
        os=$(cat "$1.os" 2>/dev/null || echo "-")
        swu=$(cat "$1.swu" 2>/dev/null || echo "-")
    fi
    echo "os=$os swu=$swu"
}

# ---- status ---------------------------------------------------------------------------------------
do_status() {
    say "image $(running_image)"
    [ -f "$MARK" ] && say "marker $(cat "$MARK")"
    [ -f "$BACKUP/MD5SUMS" ] && say "backup $(wc -l < "$BACKUP/MD5SUMS" | awk '{print $1}') files"
    for f in "$BOOT_DIR"/*; do
        [ -f "$f" ] || continue
        n=$(basename "$f")
        is_boot_file "$n" && say "boot $n $(sizeof "$f") $(md5of "$f")"
    done
    # versions of the running system (not by mounting its squashfs a second time)
    os=$(head -n 1 "${ROOT_DIR:-}/VERSION" 2>/dev/null); swu="-"
    vj="${ROOT_DIR:-}/usr/bin/SoloWebUtility2/version.json"
    [ -f "$vj" ] && swu=$(grep '"version"' "$vj" | head -n 1 | sed 's/.*"version" *: *"\([^"]*\)".*/\1/')
    say "boot_versions os=${os:--} swu=$swu"
    mount_golden ro
    say "space total=$(golden_total_kb) used=$(golden_used_kb) avail=$(golden_avail_kb)"
    for n in $(golden_files); do
        say "golden $n $(sizeof "$MNT/$n") $(md5of "$MNT/$n")"
    done
    for f in "$MNT"/*.squashfs; do [ -f "$f" ] && say "golden_versions $(squashfs_versions "$f")"; done
    umount_golden
    echo "[GOLDEN_DONE] status"
}

# ---- writing one file safely onto GOLDEN ----------------------------------------------------------
put_file() {   # put_file <source path> <name> <md5>
    src="$1"; name="$2"; want="$3"
    need=$(( $(kb_of $(sizeof "$src")) + 64 ))
    rm -f "$MNT/$name.new"
    if [ "$(golden_avail_kb)" -lt "$need" ]; then
        # no room for a second copy: remove old boot files that are not part of the new set, then this one
        for old in $(golden_files); do
            is_boot_file "$old" || continue
            grep -q "  $old\$" "$SET" && continue
            say "removing old $old to make room"
            rm -f "$MNT/$old"; sync
        done
        if [ "$(golden_avail_kb)" -lt "$need" ] && [ -f "$MNT/$name" ]; then
            say "no room for a second copy of $name: removing the old one first"
            rm -f "$MNT/$name"; sync
        fi
        [ "$(golden_avail_kb)" -lt "$need" ] && fail "not enough room on the factory reset partition for $name"
    fi
    say "writing $name ($(sizeof "$src") bytes)"
    cp "$src" "$MNT/$name.new" || fail "copying $name to the factory reset partition failed"
    sync
    got=$(md5of "$MNT/$name.new")
    [ "$got" = "$want" ] || fail "$name on the factory reset partition has MD5 $got, expected $want"
    mv -f "$MNT/$name.new" "$MNT/$name" || fail "could not rename $name.new"
    sync
    say "ok $name $want"
}

# write_set <dir with the files + MD5SUMS> <label>: puts every file of <dir>/MD5SUMS on GOLDEN
write_set() {
    dir="$1"; SET="$dir/MD5SUMS"
    [ -s "$SET" ] || fail "nothing to write ($SET missing)"
    mount_golden rw
    # capacity: the new set plus every file that stays must fit
    keep=0
    for n in $(golden_files); do
        grep -q "  $n\$" "$SET" && continue
        is_boot_file "$n" && continue
        case "$n" in *.new) continue ;; esac
        keep=$(( keep + $(kb_of $(sizeof "$MNT/$n")) + 32 ))
    done
    newkb=0
    while read sum name; do newkb=$(( newkb + $(kb_of $(sizeof "$dir/$name")) + 32 )); done < "$SET"
    total=$(golden_total_kb)
    say "space total=${total}KB, needed=$(( newkb + keep ))KB (new files ${newkb}KB, kept files ${keep}KB)"
    [ $(( newkb + keep + MARGIN_KB )) -le "$total" ] || fail "the files do not fit on the factory reset partition (${total}KB)"
    echo "$ACTION $(date) $(cat "$SET" | awk '{print $1}' | head -n 1)" > "$MARK"; sync
    for f in "$MNT"/*.new; do [ -f "$f" ] && rm -f "$f"; done      # leftovers of an interrupted run
    # small files first, the squashfs last
    while read sum name; do
        case "$name" in *.squashfs) ;; *) put_file "$dir/$name" "$name" "$sum" ;; esac
    done < "$SET"
    while read sum name; do
        case "$name" in *.squashfs) put_file "$dir/$name" "$name" "$sum" ;; esac
    done < "$SET"
    # old boot files that are not part of the new set (e.g. a squashfs with another name)
    for old in $(golden_files); do
        is_boot_file "$old" || continue
        grep -q "  $old\$" "$SET" && continue
        say "removing old $old"
        rm -f "$MNT/$old"
    done
    umount_golden
    # final check from a read-only mount
    mount_golden ro
    while read sum name; do
        [ -f "$MNT/$name" ] || fail "$name is missing after writing"
        got=$(md5of "$MNT/$name")
        [ "$got" = "$sum" ] || fail "final check: $name has MD5 $got, expected $sum"
    done < "$SET"
    for n in $(golden_files); do say "golden $n $(sizeof "$MNT/$n") $(md5of "$MNT/$n")"; done
    umount_golden
    rm -f "$MARK"; sync
}

# copy_checked <src> <dst dir>: copy one file and check its MD5 against the source
copy_checked() {
    want=$(md5of "$1")
    cp "$1" "$2/" || fail "copying $(basename "$1") to $2 failed"
    sync
    got=$(md5of "$2/$(basename "$1")")
    [ "$got" = "$want" ] || fail "copy of $(basename "$1") in $2 has MD5 $got, expected $want"
    echo "$want  $(basename "$1")"
}

need_log_space() {   # need_log_space <KB>
    avail=$(df -k "$LOG_DIR" | tail -n 1 | awk '{print $4}')
    [ "$avail" -ge "$1" ] || fail "not enough free space in $LOG_DIR (${avail}KB free, ${1}KB needed)"
}

do_write() {
    step 1 "Checks"
    [ "$(running_image)" = "latest" ] || fail "the device is not running its system partition (sololink_config --get-image: $(running_image))"
    n=0; bootkb=0
    for f in "$BOOT_DIR"/*; do
        [ -f "$f" ] || continue
        is_boot_file "$(basename "$f")" || continue
        n=$(( n + 1 )); bootkb=$(( bootkb + $(kb_of $(sizeof "$f")) ))
    done
    ls "$BOOT_DIR"/*.squashfs >/dev/null 2>&1 || fail "no squashfs in $BOOT_DIR"
    ls "$BOOT_DIR"/*.dtb >/dev/null 2>&1 || fail "no .dtb in $BOOT_DIR"
    [ -f "$BOOT_DIR/uImage" ] || fail "no uImage in $BOOT_DIR"
    [ -f "$BOOT_DIR/u-boot.imx" ] || fail "no u-boot.imx in $BOOT_DIR"
    say "running system: $n boot files, ${bootkb}KB"

    step 2 "Backing up the factory reset partition"
    if [ -f "$MARK" ] && [ -f "$BACKUP/MD5SUMS" ]; then
        say "an earlier run did not finish ($(cat "$MARK")): keeping the existing backup"
    else
        mount_golden ro
        usedkb=$(golden_used_kb)
        need_log_space $(( usedkb + bootkb + 4096 ))
        rm -rf "$BACKUP.new"; mkdir -p "$BACKUP.new" || fail "could not create $BACKUP.new"
        for n in $(golden_files); do
            say "backing up $n"
            copy_checked "$MNT/$n" "$BACKUP.new" >> "$BACKUP.new/MD5SUMS"
        done
        umount_golden
        rm -rf "$BACKUP"; mv "$BACKUP.new" "$BACKUP" || fail "could not keep the backup"
        sync
        say "backup in $BACKUP ($(wc -l < "$BACKUP/MD5SUMS" | awk '{print $1}') files)"
    fi

    step 3 "Staging the new files"
    need_log_space $(( bootkb + 2048 ))
    rm -rf "$STAGE"; mkdir -p "$STAGE" || fail "could not create $STAGE"
    for f in "$BOOT_DIR"/*; do
        [ -f "$f" ] || continue
        is_boot_file "$(basename "$f")" || continue
        say "staging $(basename "$f")"
        copy_checked "$f" "$STAGE" >> "$STAGE/MD5SUMS"
    done
    # the running files must still match what was staged
    while read sum name; do
        [ "$(md5of "$BOOT_DIR/$name")" = "$sum" ] || fail "$BOOT_DIR/$name changed while staging"
    done < "$STAGE/MD5SUMS"

    step 4 "Writing the factory reset partition"
    write_set "$STAGE"
    rm -rf "$STAGE"
    step 5 "Checked"
    echo "[GOLDEN_DONE] write"
}

do_restore() {
    step 1 "Checks"
    [ -f "$BACKUP/MD5SUMS" ] || fail "there is no backup in $BACKUP"
    while read sum name; do
        [ -f "$BACKUP/$name" ] || fail "the backup is incomplete ($name missing)"
        [ "$(md5of "$BACKUP/$name")" = "$sum" ] || fail "the backup of $name is damaged"
    done < "$BACKUP/MD5SUMS"
    say "backup checked ($(wc -l < "$BACKUP/MD5SUMS" | awk '{print $1}') files)"
    step 4 "Restoring the factory reset partition from the backup"
    write_set "$BACKUP"
    step 5 "Checked"
    echo "[GOLDEN_DONE] restore"
}

case "$ACTION" in
    status) do_status ;;
    write) do_write ;;
    restore) do_restore ;;
    *) echo "[GOLDEN_FAILED] usage: golden_write.sh status|write|restore"; exit 2 ;;
esac
exit 0
