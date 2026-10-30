#!/bin/bash
# Solo Web Utility v1.2.3 -- Mac installer.
#
# Installs Solo Web Utility on a 3DR Solo over SoloLink Wi-Fi with the ssh that ships
# with macOS: same files, target folders, /etc/inittab respawn entry and firmware
# staging in /log/firmware as the Windows (.exe) and Android (.apk) installers.
# Mac port of android_installer/src/com/sololink/webutility/SoloInstaller.java.
#
# Packed by mac_installer/build_mac.py as "Install Solo Web Utility.command", next to
# files/manifest.txt and files/swu/ (staged by android_installer/stage_assets.py).
# Plain bash 3.2 (the version macOS ships); also runs on Linux.

set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
FILES="$HERE/files"
MANIFEST="$FILES/manifest.txt"

SOLO_HOST="${SOLO_HOST:-10.1.1.10}"
SOLO_PORT="${SOLO_PORT:-22}"
CTRL_HOST="${CTRL_HOST:-10.1.1.1}"
CTRL_PORT="${CTRL_PORT:-22}"
SSH_USER="root"
PASSWORD="TjSDBkAu"
LOG_FW_DIR="/log/firmware"
INITTAB_ENTRY='SWU:345:respawn:/usr/bin/python /usr/bin/SoloWebUtility2/solo_web_app.py >>/log/solowebutility.log 2>&1'
CHUNK_MB=4          # big files go up in pieces of this size so progress shows

# ------------------------------------------------------------------ output

say()  { printf '%s\n' "$*"; }
ok()   { printf '[OK] %s\n' "$*"; }
fail() { printf '\n[!] %s\n' "$*"; finish 1; }

human_size() {
    awk -v n="$1" 'BEGIN { if (n >= 1048576) printf "%.1f MB", n / 1048576;
                           else if (n >= 1024) printf "%.1f KB", n / 1024; else printf "%d B", n }'
}

finish() {
    local rc=$1
    close_master
    rm -rf "$WORK" 2>/dev/null
    say ""
    if [ "$rc" -eq 0 ]; then
        say "Done. Solo Web Utility is running on the Solo: open http://$SOLO_HOST in a browser"
        say "(password: sololink) on this Mac, a phone or a PC joined to SoloLink."
        say "You can delete this installer folder."
        command -v open >/dev/null 2>&1 && open "http://$SOLO_HOST" >/dev/null 2>&1
    else
        say "Installation did not finish. See the messages above, then run the installer again."
    fi
    if [ -t 1 ] && [ -z "${SWU_NO_PAUSE:-}" ]; then
        say ""
        read -r -p "Press Return to close this window. " _ < /dev/tty
    fi
    exit "$rc"
}

# ------------------------------------------------------------------ ssh setup

WORK="$(mktemp -d "${TMPDIR:-/tmp}/swu.XXXXXX")" || { say "[!] Cannot create a temp folder."; exit 1; }
CM="$WORK/cm"
trap 'finish 1' INT TERM

# The Solo only takes a password; ssh reads it from this helper instead of the keyboard.
printf '#!/bin/sh\necho %s\n' "'$PASSWORD'" > "$WORK/askpass"
chmod 700 "$WORK/askpass"
export SSH_ASKPASS="$WORK/askpass" SSH_ASKPASS_REQUIRE=force DISPLAY="${DISPLAY:-:0}"

SSH_OPTS=(
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR
    -o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive
    -o NumberOfPasswordPrompts=1 -o ConnectTimeout=15
    -o ServerAliveInterval=15 -o ServerAliveCountMax=4
)

# Open Solo runs an older OpenSSH: also allow its algorithms, where this ssh still has them
allow_legacy() {    # ssh option, ssh -Q query, algorithms...
    local opt=$1 query=$2 have list="" a
    shift 2
    have="$(ssh -Q "$query" 2>/dev/null)"
    for a in "$@"; do
        printf '%s\n' "$have" | grep -qx "$a" && list="$list,$a"
    done
    [ -n "$list" ] && SSH_OPTS+=(-o "$opt=+${list#,}")
}
allow_legacy KexAlgorithms kex diffie-hellman-group14-sha1 diffie-hellman-group-exchange-sha1 diffie-hellman-group1-sha1
allow_legacy HostKeyAlgorithms key ssh-rsa ssh-dss
allow_legacy Ciphers cipher aes128-cbc 3des-cbc

# One connection to the Solo, reused by every command and upload below
open_master() {
    local a
    for a in 1 2 3; do
        ssh "${SSH_OPTS[@]}" -p "$SOLO_PORT" -o ControlMaster=yes -o ControlPath="$CM" \
            -o ControlPersist=yes -f -N "$SSH_USER@$SOLO_HOST" </dev/null && return 0
        [ "$a" -lt 3 ] && sleep 2
    done
    return 1
}

close_master() {
    [ -S "$CM" ] && ssh -o ControlPath="$CM" -O exit "$SSH_USER@$SOLO_HOST" >/dev/null 2>&1
}

# rsh CMD -- runs CMD on the Solo, prints its output, returns its exit code
rsh() {
    ssh "${SSH_OPTS[@]}" -p "$SOLO_PORT" -o ControlPath="$CM" "$SSH_USER@$SOLO_HOST" "cd /; $1"
}

# Single-quotes a string for the Solo's shell
q() {
    printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

# put LOCAL REMOTE -- copies a file to the Solo over the open connection
put() {
    local src=$1 dst=$2 size chunks i
    size=$(wc -c < "$src" | tr -d ' ')
    chunks=$(( (size + CHUNK_MB * 1048576 - 1) / (CHUNK_MB * 1048576) ))
    if [ "$chunks" -le 2 ]; then
        rsh "cat > $(q "$dst")" < "$src"
        return
    fi
    rsh ": > $(q "$dst")" </dev/null || return 1
    i=0
    while [ "$i" -lt "$chunks" ]; do
        dd if="$src" bs=1048576 skip=$(( i * CHUNK_MB )) count="$CHUNK_MB" 2>/dev/null \
            | rsh "cat >> $(q "$dst")" || return 1
        i=$(( i + 1 ))
        printf '\r      %d%%  ' $(( 100 * i / chunks ))
    done
    printf '\r      done   \n'
}

md5_local() {
    if command -v md5 >/dev/null 2>&1; then md5 -q "$1"; else md5sum "$1" | awk '{print $1}'; fi
}

# bad_md5s SUMS_FILE PREFIX KIND -- lists manifest entries of KIND whose md5sum output differs
bad_md5s() {
    awk -F'\t' -v pfx="$2" -v kind="$3" '
        NR == FNR { p = substr($0, 35); sub(/^\*/, "", p); got[p] = substr($0, 1, 32); next }
        $1 == kind {
            path = (kind == "fw") ? pfx substr($4, 9) : pfx $4
            if (got[path] != $3) print $4 ": MD5 on the Solo " (got[path] == "" ? "missing" : got[path]) ", expected " $3
        }' "$1" "$MANIFEST"
}

# ------------------------------------------------------------------ install

say "=========================================================="
say "  Solo Web Utility v1.2.3 -- Mac installer"
say "=========================================================="
say "1. Turn on the Solo and the Controller."
say "2. Join the SoloLink_... Wi-Fi on this Mac (it has no internet; that is normal)."
say "3. Keep this window open until it finishes (about 15-25 minutes)."
say ""

[ -f "$MANIFEST" ] || fail "files/manifest.txt is missing. Unzip the whole installer folder and run it from there."
command -v ssh >/dev/null 2>&1 || fail "ssh was not found on this computer."

APP_COUNT=$(awk -F'\t' '$1 == "app"' "$MANIFEST" | wc -l | tr -d ' ')
FW_COUNT=$(awk -F'\t' '$1 == "fw"' "$MANIFEST" | wc -l | tr -d ' ')
say "Solo Web Utility installer: $APP_COUNT app files, $FW_COUNT firmware files."

# 0. The golden image in this folder must be intact
want=$(awk '{print tolower($1); exit}' "$FILES/swu/firmware/3dr-solo.tar.gz.md5" 2>/dev/null)
listed=$(awk -F'\t' '$4 == "firmware/3dr-solo.tar.gz" {print $3}' "$MANIFEST")
[ -f "$FILES/swu/firmware/3dr-solo.tar.gz" ] || fail "This installer is missing the golden image (3dr-solo.tar.gz)."
say "Checking the packaged firmware..."
got=$(md5_local "$FILES/swu/firmware/3dr-solo.tar.gz")
if [ "$got" != "$want" ] || [ "$got" != "$listed" ]; then
    fail "The golden image in this folder is damaged (MD5 $got, expected $want). Download the installer again."
fi
ok "Packaged golden image verified (MD5 $got)."

# 1. Connect
if [ "$SOLO_HOST" = "10.1.1.10" ] && command -v ifconfig >/dev/null 2>&1 \
        && ! ifconfig 2>/dev/null | grep -q 'inet 10\.1\.1\.'; then
    say "This Mac has no SoloLink (10.1.1.x) address yet. Join the SoloLink_... Wi-Fi if the connection fails."
fi
say "Connecting to the Solo ($SOLO_HOST)..."
if ! open_master; then
    fail "Cannot connect to the Solo at $SOLO_HOST. Join the SoloLink Wi-Fi and turn the Solo on.
    (If ssh asked for a password, type $PASSWORD.)"
fi
ok "Connected to the Solo ($SOLO_HOST)."
say "Solo storage:"
rsh "df -h / /log 2>/dev/null || df -h" </dev/null | sed 's/^/    /'

# 2. Stop the running server, clean stale update folders
say "Preparing the Solo..."
rsh "mount -o remount,rw / 2>/dev/null || true
     rm -rf /tmp/updates /log/updates 2>/dev/null || true
     fuser -k 80/tcp 2>/dev/null || true
     for p in \$(ps | grep -E 'solo_web_app|SoloWebUtility2/app.py' | grep -v grep | awk '{print \$1}'); do kill -9 \$p 2>/dev/null; done
     true" </dev/null

if rsh "touch /log/.rw_test 2>/dev/null && rm -f /log/.rw_test 2>/dev/null" </dev/null; then
    TARGET=/log/SoloWebUtility2
else
    rsh "mount -o remount,rw /dev/mmcblk0p4 /log 2>/dev/null || mount -o remount,rw /log 2>/dev/null || true" </dev/null
    if rsh "touch /log/.rw_test 2>/dev/null && rm -f /log/.rw_test 2>/dev/null" </dev/null; then
        TARGET=/log/SoloWebUtility2
    else
        TARGET=/home/root/SoloWebUtility2
    fi
fi
if [ "$TARGET" = /log/SoloWebUtility2 ]; then
    rsh "mkdir -p /log/SoloWebUtility2/bin /log/SoloWebUtility2/static /log/SoloWebUtility2/templates /log/SoloWebUtility2/modules /log/firmware 2>/dev/null || true
         chmod -R 755 /log/SoloWebUtility2 2>/dev/null || true
         ln -sfn /log/firmware /log/SoloWebUtility2/firmware 2>/dev/null || true
         rm -rf /home/root/SoloWebUtility2 2>/dev/null; ln -sfn /log/SoloWebUtility2 /home/root/SoloWebUtility2 2>/dev/null || true
         rm -rf /usr/bin/SoloWebUtility2 2>/dev/null; ln -sfn /log/SoloWebUtility2 /usr/bin/SoloWebUtility2 2>/dev/null || true" </dev/null
else
    rsh "rm -rf /home/root/SoloWebUtility2 2>/dev/null
         mkdir -p /home/root/SoloWebUtility2/bin /home/root/SoloWebUtility2/static /home/root/SoloWebUtility2/templates /home/root/SoloWebUtility2/modules /home/root/SoloWebUtility2/firmware 2>/dev/null || true
         chmod -R 755 /home/root/SoloWebUtility2 2>/dev/null || true
         ln -sfn /log/firmware /home/root/SoloWebUtility2/firmware 2>/dev/null || true" </dev/null
fi
say "Installation folder: $TARGET"

# 3. App files
say "Installing Solo Web Utility files..."
dirs=$(awk -F'\t' -v t="$TARGET" '$1 == "app" { d = t "/" $4; sub(/\/[^\/]*$/, "", d); if (!(d in s)) { s[d]; print d } }' "$MANIFEST")
mk=""
while IFS= read -r d; do mk="$mk $(q "$d")"; done <<EOF
$dirs
EOF
rsh "mkdir -p $mk 2>/dev/null || true" </dev/null

i=0
names=""
while IFS=$'\t' read -r kind size sum rel; do
    [ "$kind" = app ] || continue
    i=$((i + 1))
    say "  [$i/$APP_COUNT] $rel ($(human_size "$size"))"
    put "$FILES/swu/$rel" "$TARGET/$rel" || fail "Copying $rel to the Solo failed."
    names="$names $(q "$rel")"
done < "$MANIFEST"

rsh "cd $(q "$TARGET") && md5sum $names 2>/dev/null" </dev/null > "$WORK/app.md5"
bad_md5s "$WORK/app.md5" "" app > "$WORK/app.bad"
if [ -s "$WORK/app.bad" ]; then
    sed 's/^/[!] /' "$WORK/app.bad"
    fail "$(wc -l < "$WORK/app.bad" | tr -d ' ') app file(s) did not arrive intact."
fi
ok "All $APP_COUNT app files installed and verified."

rsh "chmod -R 755 $(q "$TARGET") 2>/dev/null || true
     chmod +x $TARGET/*.py $TARGET/bin/*.py 2>/dev/null || true" </dev/null

# Let the copter's web server manage the Controller without a password prompt
rsh "test -f /home/root/.ssh/id_rsa || ssh-keygen -t rsa -N '' -f /home/root/.ssh/id_rsa >/dev/null 2>&1" </dev/null
KEY=$(rsh "cat /home/root/.ssh/id_rsa.pub 2>/dev/null" </dev/null | tr -d '\r\n')
case "$KEY" in
    *ssh-rsa*)
        QK=$(q "$KEY")
        if ssh "${SSH_OPTS[@]}" -p "$CTRL_PORT" -o ConnectTimeout=5 "$SSH_USER@$CTRL_HOST" \
                "cd / && mkdir -p /home/root/.ssh /root/.ssh && chmod 700 /home/root/.ssh /root/.ssh
                 grep -q $QK /home/root/.ssh/authorized_keys 2>/dev/null || echo $QK >> /home/root/.ssh/authorized_keys
                 grep -q $QK /root/.ssh/authorized_keys 2>/dev/null || echo $QK >> /root/.ssh/authorized_keys
                 chmod 600 /home/root/.ssh/authorized_keys /root/.ssh/authorized_keys 2>/dev/null || true" </dev/null; then
            ok "Solo's SSH key authorized on the Controller."
        else
            say "Controller not reachable yet; the Solo will use its password fallback."
        fi
        ;;
esac

# 4. Firmware
say "Copying firmware to the Solo..."
rsh "rm -f $LOG_FW_DIR/arducopter_solo.apj $LOG_FW_DIR/arducopter_green.apj /firmware/arducopter_solo.apj /firmware/arducopter_green.apj 2>/dev/null || true" </dev/null
dirs=$(awk -F'\t' -v t="$LOG_FW_DIR" '$1 == "fw" { d = t substr($4, 9); sub(/\/[^\/]*$/, "", d); if (!(d in s)) { s[d]; print d } }' "$MANIFEST")
mk="$(q "$LOG_FW_DIR")"
while IFS= read -r d; do mk="$mk $(q "$d")"; done <<EOF
$dirs
EOF
rsh "mkdir -p $mk 2>/dev/null || true" </dev/null

i=0
names=""
while IFS=$'\t' read -r kind size sum rel; do
    [ "$kind" = fw ] || continue
    i=$((i + 1))
    remote="$LOG_FW_DIR${rel#firmware}"
    say "  [$i/$FW_COUNT] $rel ($(human_size "$size"))"
    names="$names $(q "$remote")"
    have=$(rsh "md5sum $(q "$remote") 2>/dev/null" </dev/null | awk '{print $1}')
    if [ "$have" = "$sum" ]; then
        say "      already on the Solo, unchanged"
        continue
    fi
    rsh "rm -f $(q "$remote") 2>/dev/null || true" </dev/null
    put "$FILES/swu/$rel" "$remote" || fail "Copying $rel to the Solo failed."
done < "$MANIFEST"

rsh "md5sum $names 2>/dev/null" </dev/null > "$WORK/fw.md5"
bad_md5s "$WORK/fw.md5" "$LOG_FW_DIR" fw > "$WORK/fw.bad"
if [ -s "$WORK/fw.bad" ]; then
    sed 's/^/[!] /' "$WORK/fw.bad"
    fail "$(wc -l < "$WORK/fw.bad" | tr -d ' ') firmware file(s) on the Solo are damaged."
fi
ok "All $FW_COUNT firmware files on the Solo verified."

rsh "mkdir -p /usr/bin/SoloWebUtility2 /log/SoloWebUtility2 /home/root/SoloWebUtility2 2>/dev/null || true
     ln -sfn /log/firmware /usr/bin/SoloWebUtility2/firmware 2>/dev/null || true
     ln -sfn /log/firmware /log/SoloWebUtility2/firmware 2>/dev/null || true
     ln -sfn /log/firmware /home/root/SoloWebUtility2/firmware 2>/dev/null || true" </dev/null

# 5. Start on boot (inittab respawn) and start now
say "Starting Solo Web Utility..."
rsh "rm -f /etc/init.d/solowebutility /etc/rc5.d/S99solowebutility 2>/dev/null || true" </dev/null
if rsh "grep -q -e SoloWebUtility -e solo_web_app /etc/rc.local 2>/dev/null" </dev/null; then
    rsh "grep -v -e SoloWebUtility -e solo_web_app /etc/rc.local > /tmp/rc.local.swu
         cat /tmp/rc.local.swu > /etc/rc.local && rm -f /tmp/rc.local.swu && chmod 755 /etc/rc.local" </dev/null
    ok "Removed old start-up lines from /etc/rc.local."
fi
if rsh "grep -q -e 'SWU:345:respawn' -e SoloWebUtility /etc/inittab 2>/dev/null" </dev/null; then
    ok "/etc/inittab already starts Solo Web Utility."
else
    rsh "printf '\n# SoloWebUtility2 continuous supervision\n%s\n' $(q "$INITTAB_ENTRY") >> /etc/inittab
         chmod 644 /etc/inittab 2>/dev/null || true" </dev/null
    ok "Solo Web Utility now starts automatically (/etc/inittab)."
fi

rsh "telinit q 2>/dev/null || kill -HUP 1 2>/dev/null || true
     fuser -k 80/tcp 2>/dev/null || true
     for p in \$(ps | grep -E 'solo_web_app|SoloWebUtility2' | grep -v grep | awk '{print \$1}'); do kill -9 \$p 2>/dev/null; done
     sleep 1
     mkdir -p /log /tmp 2>/dev/null; touch /log/solowebutility.log 2>/dev/null || true
     ln -sfn /log/solowebutility.log /tmp/solo_web.log 2>/dev/null; ln -sfn /log/solowebutility.log /log/solo_web.log 2>/dev/null
     ln -sfn /log/solowebutility.log /tmp/solo_web_server.log 2>/dev/null; ln -sfn /log/solowebutility.log /log/solo_web_server.log 2>/dev/null
     telinit q 2>/dev/null || true" </dev/null
sleep 2
PS_OUT=$(rsh "ps | grep -E 'solo_web_app|SoloWebUtility2' | grep -v grep || true" </dev/null)
if [ -z "$PS_OUT" ]; then
    rsh "nohup /usr/bin/python $TARGET/solo_web_app.py 80 </dev/null >>/log/solowebutility.log 2>&1 &" </dev/null
    sleep 2
    PS_OUT=$(rsh "ps | grep -E 'solo_web_app|SoloWebUtility2' | grep -v grep || true" </dev/null)
fi
if [ -z "$PS_OUT" ]; then
    say "[!] The web server did not start. Last log lines:"
    rsh "tail -n 15 /log/solowebutility.log 2>/dev/null || true" </dev/null | sed 's/^/    /'
    finish 1
fi
ok "Web server running: $(printf '%s\n' "$PS_OUT" | head -n 1 | sed 's/^ *//')"
finish 0
