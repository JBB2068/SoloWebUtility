#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
3DR Solo Controller Wi-Fi & Internet Management - v2.0.0

Runs on the copter (Python 2.7.3, stdlib only) and drives the Artoo
controller (10.1.1.1) over SSH.  Controller facts this module is built on
(Open Solo 4.0.0):

  * One Atheros ath9k radio (mac80211, 2.4 GHz).  ``wlan0`` is the station
    interface, ``wlan0-ap`` the SoloLink access point (hostapd).  ath9k can
    only be on ONE channel, so hostapd is paused while wlan0 associates and
    restarted afterwards; its init script then moves the AP onto the home
    network's channel ("hostapdconfig.py --channel wlan0").
  * Tools: iw (supports "scan flush"), iwlist, wpa_supplicant 2.4, wpa_cli,
    BusyBox 1.21 udhcpc, hostapd, iptables 1.4.19, python2.7.
    No dhcpcd/dhclient, no crda, no base64 applet, no Broadcom "wl".

All controller-side work is done by a small shell helper (CONTROLLER_SCRIPT,
pushed to /tmp/swu-wifi.sh on every connect/scan/reset) that writes
human-readable progress plus machine-readable "STATUS: ..." lines to
/tmp/wifi_setup.log.  get_wifi_status() turns those into the JSON the UI shows.

Compatible with Python 2.7 and Python 3.x (PC test runs).
"""

import os
import sys
import subprocess
import socket
import time
import re
import base64
import binascii
import hashlib
import hmac
import struct
import threading

import flash_mgr

PY3 = sys.version_info[0] >= 3

CONTROLLER_IP = "10.1.1.1"
SSH_USER = "root"
SSH_OPTS = "-o StrictHostKeyChecking=no -o ConnectTimeout=10"

STA_IFACE = "wlan0"
AP_IFACE = "wlan0-ap"
REMOTE_SCRIPT = "/tmp/swu-wifi.sh"
REMOTE_CONF = "/tmp/wpa_client.conf"
REMOTE_SSID = "/tmp/home_wifi.ssid"
REMOTE_LOG = "/tmp/wifi_setup.log"
CTRL_DIR = "/var/run/wpa_supplicant"
DEFAULT_COUNTRY = "US"

SCAN_TIMEOUT = 45       # two blocking sweeps + retries, worst case
CONNECT_TIMEOUT = 30    # only pushes files and launches the detached helper


# ---------------------------------------------------------------------------
# SSH transport
# ---------------------------------------------------------------------------
def _shq(s):
    """Quote *s* for a POSIX shell: wrap in single quotes, escape embedded ones.

    Nothing inside single quotes is expanded by the copter's local shell, so
    the controller receives the command byte-for-byte ($, `, \\ and " in an
    SSID or password used to be mangled by the old double-quote wrapping).
    """
    return "'" + s.replace("'", "'\\''") + "'"


def _ssh(cmd, host=CONTROLLER_IP, timeout=20):
    """Execute a command on the Controller via SSH. Returns (rc, output)."""
    full = "ssh %s %s@%s %s" % (SSH_OPTS, SSH_USER, host, _shq(cmd))
    rc, out = flash_mgr._run_with_ssh_auth(full, host=host, timeout=timeout)
    if PY3 and isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    return rc, (out or "").replace("\r", "")


def _ssh_paramiko(cmd, host=CONTROLLER_IP, timeout=30):
    """Execute command on Controller via SSH (name kept for existing callers)."""
    return _ssh(cmd, host=host, timeout=timeout)


def _log(msg):
    """One line to the web server's log (/log/solowebutility.log)."""
    try:
        sys.stdout.write("[%s] [wifi] %s\n" % (time.strftime("%H:%M:%S"), msg))
        sys.stdout.flush()
    except Exception:
        pass


def _ctrl_reachable():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.2)
        s.connect((CONTROLLER_IP, 22))
        s.close()
        return True
    except Exception:
        return False


def _to_bytes(s):
    if s is None:
        return b""
    if isinstance(s, bytes):
        return s
    return s.encode("utf-8")


def _to_text(b):
    """bytes/str -> text that json.dumps accepts on Python 2 and 3."""
    if b is None:
        return u""
    if isinstance(b, bytes):
        return b.decode("utf-8", "replace")
    return b


def _push_file_cmd(path, data, mode="600"):
    """Shell snippet that recreates *path* on the controller with exact bytes.

    The payload travels as base64 (only [A-Za-z0-9+/=]) and is decoded by the
    controller's python2.7, because BusyBox there has no base64 applet.
    """
    b64 = base64.b64encode(_to_bytes(data))
    if PY3:
        b64 = b64.decode("ascii")
    return ("umask 077; python -c \"import base64,sys;"
            "open(sys.argv[1],'wb').write(base64.b64decode(sys.argv[2]))\" %s %s"
            " && chmod %s %s") % (path, b64, mode, path)


def _has_line(out, marker):
    """True only if `marker` is printed as a line of its own. Shell errors can echo
    the command text (which contains 'echo MARKER'), so a substring test is not enough."""
    return re.search(r"^%s\s*$" % re.escape(marker), out or "", re.M) is not None


def _join_cmds(parts):
    """Join shell steps with newlines. '; ' after a step ending in '&' gives '&;',
    a syntax error that makes the remote shell reject the whole command."""
    return "\n".join(parts)


def _sections(out, names):
    """Split helper output on ===NAME=== markers -> {name: text}."""
    found = {}
    pattern = r"^===(%s)===\s*$" % "|".join(names)
    parts = re.split(pattern, out, flags=re.M)
    # parts = [preamble, name1, body1, name2, body2, ...]
    for i in range(1, len(parts) - 1, 2):
        found[parts[i]] = parts[i + 1]
    return found


# ---------------------------------------------------------------------------
# WPA credentials
# ---------------------------------------------------------------------------
def _pbkdf2_sha1(password, salt, iterations, dklen):
    """PBKDF2-HMAC-SHA1. Python 2.7.3 on the Solo has no hashlib.pbkdf2_hmac."""
    if hasattr(hashlib, "pbkdf2_hmac"):
        return hashlib.pbkdf2_hmac("sha1", password, salt, iterations, dklen)
    mac = hmac.new(password, None, hashlib.sha1)

    def prf(data):
        h = mac.copy()
        h.update(data)
        return h.digest()

    out = b""
    block = 1
    while len(out) < dklen:
        u = prf(salt + struct.pack(">I", block))
        acc = int(binascii.hexlify(u), 16)
        for _ in range(iterations - 1):
            u = prf(u)
            acc ^= int(binascii.hexlify(u), 16)
        out += binascii.unhexlify(("%040x" % acc).encode("ascii"))
        block += 1
    return out[:dklen]


def wpa_psk_hex(ssid, passphrase):
    """The 256-bit WPA PSK exactly as wpa_passphrase computes it (64 hex chars)."""
    raw = _pbkdf2_sha1(_to_bytes(passphrase), _to_bytes(ssid), 4096, 32)
    hx = binascii.hexlify(raw)
    return hx.decode("ascii") if PY3 else hx


def _validate_credentials(ssid, password):
    """Returns an error string, or None if the pair is usable."""
    sb = _to_bytes(ssid)
    if not sb:
        return "SSID is required."
    if len(sb) > 32:
        return "SSID is longer than 32 bytes."
    if password:
        pb = _to_bytes(password)
        if len(pb) == 64 and re.match(r"^[0-9a-fA-F]{64}$", _to_text(pb)):
            return None
        if len(pb) < 8 or len(pb) > 63:
            return ("WPA passwords are 8-63 characters (or a 64-digit hex key); "
                    "got %d. WEP networks are not supported." % len(pb))
    return None


def build_wpa_config(ssid, password, country=DEFAULT_COUNTRY):
    """wpa_supplicant.conf for one network, immune to quoting problems.

    * ``ssid=<hex>``  - any byte sequence (quotes, backslashes, UTF-8) is exact.
    * ``psk=<64 hex>``- precomputed PSK; the passphrase never appears in a
      config file or a command line.
    * ``scan_ssid=1`` - directed probe requests, so hidden SSIDs also work.
    * ``country=``    - regulatory alpha2 passed to the kernel via nl80211.
    """
    sb = _to_bytes(ssid)
    ssid_hex = binascii.hexlify(sb)
    if PY3:
        ssid_hex = ssid_hex.decode("ascii")
    lines = [
        "ctrl_interface=%s" % CTRL_DIR,
        "ctrl_interface_group=0",
        "country=%s" % country,
        "ap_scan=1",
        "fast_reauth=1",
        "",
        "network={",
        "\tssid=%s" % ssid_hex,
        "\tscan_ssid=1",
    ]
    if password:
        pt = _to_text(password)
        if len(pt) == 64 and re.match(r"^[0-9a-fA-F]{64}$", pt):
            psk = pt.lower()
        else:
            psk = wpa_psk_hex(sb, password)
        lines += ["\tkey_mgmt=WPA-PSK", "\tpsk=%s" % psk]
    else:
        lines += ["\tkey_mgmt=NONE"]
    lines += ["}", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Controller helper (pushed to /tmp/swu-wifi.sh; BusyBox ash compatible)
# ---------------------------------------------------------------------------
CONTROLLER_SCRIPT = r'''#!/bin/sh
# swu-wifi.sh - SoloWebUtility2 controller Wi-Fi helper (generated by wifi_mgr.py)
# Runs on the Artoo controller (BusyBox 1.21 ash, ath9k, wpa_supplicant 2.4).
#   swu-wifi.sh connect   join the network in /tmp/wpa_client.conf (run detached)
#   swu-wifi.sh scan      active sweep on wlan0, prints ===IW=== / ===WPA=== sections
#   swu-wifi.sh reset     drop home Wi-Fi, restore SoloLink AP defaults
#   swu-wifi.sh station-off   drop home Wi-Fi only (AP untouched; arm interlock)
# Progress goes to /tmp/wifi_setup.log; machine-readable lines start "STATUS: ".

IFACE=wlan0
AP_IFACE=wlan0-ap
CONF=/tmp/wpa_client.conf
SSIDF=/tmp/home_wifi.ssid
LOG=/tmp/wifi_setup.log
CTRL_DIR=/var/run/wpa_supplicant
# Stock SoloLink station mode: /etc/network/interfaces "iface wlan0 inet dhcp"
# with wpa-conf /etc/wpa_supplicant.conf, enabled by StationEnable=True in
# /etc/sololink.conf. Its if-pre-up.d hook starts wpa_supplicant (nl80211) and
# busybox ifup runs udhcpc. configinit checks the .conf.md5 files at boot.
STA_CONF=/etc/wpa_supplicant.conf
STA_ORIG=/mnt/rootfs.ro/etc/wpa_supplicant.orig
SOLO_CONF=/etc/sololink.conf
WPA_PID=/var/run/wpa_supplicant.wlan0.pid
DHCP_PID=/var/run/udhcpc.wlan0.pid
DNSMASQ=/etc/dnsmasq.conf
DNSMASQ_ORIG=/etc/dnsmasq.conf.solo-orig
ASSOC_TIMEOUT=30

log() { echo "$(date '+%H:%M:%S') $*" >> $LOG; }
status() { log "STATUS: $*"; }
# Step markers parsed by wifi_mgr.get_wifi_status(): "STEP <id> RUN|OK|FAIL <detail>"
CUR_STEP=""
step() { CUR_STEP=$1; log "STEP $1 RUN"; }
step_ok() { log "STEP $CUR_STEP OK $*"; }
step_fail() { log "STEP $CUR_STEP FAIL $*"; }

# PIDs of process $1 that were started for $IFACE (never touches wlan0-ap users)
iface_pids() {
    for p in $(pidof $1 2>/dev/null); do
        if tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | grep -qE -e "-i ?$IFACE( |$)"; then
            echo $p
        fi
    done
}

kill_iface_procs() {  # $1 = signal
    for p in $(iface_pids wpa_supplicant) $(iface_pids udhcpc); do
        kill -$1 $p 2>/dev/null
    done
}

# Stop wpa_supplicant/udhcpc on wlan0 and wait until they are really gone, then
# clear every piece of state they leave behind (control socket, pid files,
# addresses, routes). A new wpa_supplicant cannot bind its control socket while
# a stale /var/run/wpa_supplicant/wlan0 exists, which is the classic cause of
# "connect works once, then never again".
station_down() {
    ifdown $IFACE >/dev/null 2>&1           # stock hooks: stops udhcpc + wpa_supplicant
    wpa_cli -p $CTRL_DIR -i $IFACE terminate >/dev/null 2>&1
    kill_iface_procs TERM
    n=0
    while [ -n "$(iface_pids wpa_supplicant)$(iface_pids udhcpc)" ]; do
        n=$((n+1))
        if [ $n -ge 5 ]; then
            log "station processes ignored SIGTERM; sending SIGKILL"
            kill_iface_procs KILL
            sleep 1
            break
        fi
        sleep 1
    done
    rm -f $CTRL_DIR/$IFACE $WPA_PID $DHCP_PID /var/run/swu_wpa_supplicant.pid /var/run/swu_udhcpc.pid
    ip addr flush dev $IFACE 2>/dev/null
    while ip route del default dev $IFACE 2>/dev/null; do :; done
    ip link set $IFACE down 2>/dev/null
}

hostapd_running() { pidof hostapd >/dev/null 2>&1; }

hostapd_start() {
    # The init script runs "hostapdconfig.py --channel wlan0": when wlan0 is
    # associated the AP is moved to the home network's channel (ath9k can only
    # be on one channel), otherwise channel 0 (ACS) is restored.
    /etc/init.d/hostapd start >/dev/null 2>&1
    n=0
    while ! hostapd_running && [ $n -lt 5 ]; do sleep 1; n=$((n+1)); done
    if hostapd_running; then
        log "SoloLink AP is up ($(grep '^channel=' /etc/hostapd.conf 2>/dev/null))"
        return 0
    fi
    log "hostapd did not start"
    return 1
}

nat_on() {
    modprobe iptable_filter 2>/dev/null
    modprobe iptable_nat 2>/dev/null
    echo 1 > /proc/sys/net/ipv4/ip_forward
    # Strict reverse-path filtering drops forwarded replies on a multi-homed
    # box (wlan0 + wlan0-ap on one radio); switch it off for all interfaces.
    for f in /proc/sys/net/ipv4/conf/*/rp_filter; do echo 0 > $f 2>/dev/null; done
    # NAT the Solo subnet out of the home link, ahead of any other rule.
    while iptables -t nat -D POSTROUTING -o $IFACE -j MASQUERADE 2>/dev/null; do :; done
    iptables -t nat -I POSTROUTING 1 -o $IFACE -j MASQUERADE
    # Stock catch-all rule from /etc/network/interfaces; keep it present.
    iptables -t nat -C POSTROUTING -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -j MASQUERADE
    # Forwarding rules go to the top of FORWARD so no earlier DROP/REJECT wins.
    while iptables -D FORWARD -i $AP_IFACE -o $IFACE -j ACCEPT 2>/dev/null; do :; done
    while iptables -D FORWARD -i $IFACE -o $AP_IFACE -j ACCEPT 2>/dev/null; do :; done
    iptables -I FORWARD 1 -i $IFACE -o $AP_IFACE -j ACCEPT
    iptables -I FORWARD 1 -i $AP_IFACE -o $IFACE -j ACCEPT
    log "NAT: $(iptables -t nat -S POSTROUTING 2>/dev/null | tr '\n' ';')"
    log "FORWARD: $(iptables -S FORWARD 2>/dev/null | sed -n 1,4p | tr '\n' ';')"
}

nat_off() {
    # Remove only rules this tool adds; the stock MASQUERADE rule and
    # ip_forward=1 belong to SoloLink and stay.
    while iptables -D FORWARD -i $AP_IFACE -o $IFACE -j ACCEPT 2>/dev/null; do :; done
    while iptables -D FORWARD -i $IFACE -o $AP_IFACE -j ACCEPT 2>/dev/null; do :; done
    while iptables -D FORWARD -i $IFACE -o $AP_IFACE -p tcp --syn -j DROP 2>/dev/null; do :; done
    while iptables -D FORWARD -i $IFACE -o $AP_IFACE -p icmp --icmp-type echo-request -j DROP 2>/dev/null; do :; done
    # legacy rules from older versions of this tool
    while iptables -t nat -D POSTROUTING -o $IFACE -j MASQUERADE 2>/dev/null; do :; done
    while iptables -t nat -D POSTROUTING -o $AP_IFACE -j MASQUERADE 2>/dev/null; do :; done
    iptables -t nat -C POSTROUTING -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -j MASQUERADE 2>/dev/null
    echo 1 > /proc/sys/net/ipv4/ip_forward
}

dnsmasq_gateway_on() {
    [ -f $DNSMASQ ] || return 0
    [ -f $DNSMASQ_ORIG ] || cp $DNSMASQ $DNSMASQ_ORIG
    sed -i -e 's/^dhcp-option=3.*/dhcp-option=3,10.1.1.1/' -e 's/^dhcp-option=6.*/dhcp-option=6,8.8.8.8,1.1.1.1/' $DNSMASQ
    /etc/init.d/dnsmasq restart >/dev/null 2>&1
}

dnsmasq_gateway_off() {
    if [ -f $DNSMASQ_ORIG ]; then
        cp $DNSMASQ_ORIG $DNSMASQ && rm -f $DNSMASQ_ORIG
        /etc/init.d/dnsmasq restart >/dev/null 2>&1
    fi
}

# backup_config / md5sum_config exactly as /usr/bin/sololink_config does them,
# so configinit accepts the file at the next boot instead of restoring it.
cfg_backup() { cp $1.conf $1.back && md5sum $1.back > $1.back.md5; sync; }
cfg_md5() { md5sum $1.conf > $1.conf.md5; sync; rm -f $1.back $1.back.md5; }

station_enabled() { grep -qi "^[[:space:]]*StationEnable[[:space:]]*=[[:space:]]*True" $SOLO_CONF 2>/dev/null; }

set_station_enable() {   # True|False
    if command -v sololink_config >/dev/null 2>&1; then
        sololink_config --set-config StationEnable $1 >/dev/null 2>&1 && return 0
    fi
    cfg_backup /etc/sololink
    sed -i "s/^[[:space:]]*StationEnable[[:space:]]*=.*/StationEnable=$1/" $SOLO_CONF
    cfg_md5 /etc/sololink
}

station_config_on() {
    cfg_backup /etc/wpa_supplicant
    cat $CONF > $STA_CONF && chmod 600 $STA_CONF
    cfg_md5 /etc/wpa_supplicant
    set_station_enable True
    # More DHCP attempts than busybox ifup's default 3, also at boot.
    grep -q "udhcpc_opts" /etc/network/interfaces 2>/dev/null || \
        sed -i '/^iface wlan0 inet dhcp/a\	udhcpc_opts -t 10 -T 3' /etc/network/interfaces
}

station_config_off() {
    station_enabled && set_station_enable False
    if grep -q "network={" $STA_CONF 2>/dev/null && [ -f $STA_ORIG ]; then
        cfg_backup /etc/wpa_supplicant
        cp $STA_ORIG $STA_CONF
        cfg_md5 /etc/wpa_supplicant
    fi
}

remove_legacy() {
    rm -f /usr/bin/connect-home-wifi /etc/wpa_client.conf /etc/init.d/solo-home-wifi \
          /etc/rc5.d/S99solo-home-wifi /etc/rc3.d/S99solo-home-wifi
    [ -f /etc/rc.local ] && sed -i '/connect-home-wifi/d' /etc/rc.local 2>/dev/null
}

# fail <reason> <detail>: report the failure and always bring SoloLink back
fail() {
    [ -n "$CUR_STEP" ] && step_fail "$2"
    status "FAILED reason=$1 detail=$2"
    station_down
    station_config_off
    nat_off
    dnsmasq_gateway_off
    hostapd_running || hostapd_start
    exit 1
}

# IPv4 address of $IFACE, by three independent methods (the first that answers wins).
iface_ip() {
    a=$(ip -4 addr show dev $IFACE 2>/dev/null | sed -n 's/.*inet \([0-9.]*\).*/\1/p' | sed -n 1p)
    [ -n "$a" ] || a=$(ip addr show $IFACE 2>/dev/null | sed -n 's/.*inet \([0-9.]*\).*/\1/p' | sed -n 1p)
    [ -n "$a" ] || a=$(ifconfig $IFACE 2>/dev/null | sed -n 's/.*inet addr:\([0-9.]*\).*/\1/p' | sed -n 1p)
    echo "$a"
}

# Raw state for the log when no address is found after a lease.
log_addr_state() {
    log "diag ip addr: $(ip addr show $IFACE 2>&1 | tr '\n' '|')"
    log "diag ifconfig: $(ifconfig $IFACE 2>&1 | tr '\n' '|')"
    log "diag udhcpc: $(ps | grep '[u]dhcpc' | tr '\n' '|')"
    log "diag routes: $(ip route 2>&1 | tr '\n' '|')"
}

wpa_state() {
    wpa_cli -p $CTRL_DIR -i $IFACE status 2>/dev/null | sed -n 's/^wpa_state=//p'
}

ssid_in_scan() {
    # column 5+ of scan_results is the SSID; exact match against the plain SSID
    wpa_cli -p $CTRL_DIR -i $IFACE scan_results 2>/dev/null | cut -f5- | tr -d '\r' | grep -qxF -f $SSIDF
}

do_connect() {
    [ -f $CONF ] || { status "FAILED reason=no_config detail=missing_$CONF"; exit 1; }
    sleep 1   # let the SSH session that launched us return before the AP drops
    remove_legacy
    [ -e /sys/class/net/$IFACE ] || { status "FAILED reason=no_interface detail=$IFACE"; exit 1; }

    step teardown
    log "Tearing down previous station state on $IFACE"
    station_down
    step_ok "wpa_supplicant/udhcpc stopped, addresses and routes cleared"

    # ath9k (single channel): the station can only join a network on another
    # channel while the AP is not holding the radio. SoloLink drops briefly.
    step ap_pause
    if hostapd_running; then
        log "Stopping SoloLink AP (hostapd) while associating"
        /etc/init.d/hostapd stop >/dev/null 2>&1
        n=0; while hostapd_running && [ $n -lt 5 ]; do sleep 1; n=$((n+1)); done
        step_ok "hostapd stopped"
    else
        step_ok "AP was not running"
    fi

    step supplicant

    # Regulatory domain: use the controller's configured country (US by default).
    CC=$(sed -n 's/^country_code=//p' /etc/hostapd.conf 2>/dev/null | sed -n 1p)
    [ -n "$CC" ] || CC=US
    REG=$(iw reg get 2>/dev/null | sed -n 's/^country \([A-Z0-9]*\).*/\1/p' | sed -n 1p)
    if [ "$REG" != "$CC" ]; then
        iw reg set $CC 2>/dev/null
        sleep 1
        REG=$(iw reg get 2>/dev/null | sed -n 's/^country \([A-Z0-9]*\).*/\1/p' | sed -n 1p)
    fi
    log "Regulatory domain: wanted $CC, kernel reports ${REG:-unknown} (ath9k EEPROM limits still apply)"

    log "Saving network to $STA_CONF and setting StationEnable=True (built-in station mode)"
    station_config_on
    station_enabled || fail station_mode "could_not_set_StationEnable=True_in_$SOLO_CONF"
    mkdir -p $CTRL_DIR

    # Built-in bring-up: if-pre-up.d/wpa-supplicant starts wpa_supplicant -D nl80211,
    # then busybox ifup runs udhcpc. Run it in the background so association can be
    # watched (and a wrong password reported) while ifup waits for DHCP.
    log "ifup $IFACE (stock SoloLink hooks)"
    ( ifup -f $IFACE >>$LOG 2>&1; echo "ifup $IFACE exit=$?" >> $LOG ) &
    IFUP_PID=$!
    n=0
    while [ ! -S $CTRL_DIR/$IFACE ] && [ $n -lt 20 ]; do usleep 250000; n=$((n+1)); done
    [ -S $CTRL_DIR/$IFACE ] || fail wpa_start "ifup_did_not_start_wpa_supplicant_(no_$CTRL_DIR/$IFACE)"
    # wpa_supplicant has read the config; switch the saved settings straight back off
    # so a Controller reboot (e.g. at the flying field) always comes up as plain SoloLink.
    station_config_off
    log "StationEnable=False again: this connection lasts until reboot or reset"
    step_ok "ifup $IFACE started wpa_supplicant (nl80211), regulatory ${REG:-unknown}"
    wpa_cli -p $CTRL_DIR -i $IFACE scan >/dev/null 2>&1

    step associate

    # Poll every 250 ms so short-lived states (4WAY_HANDSHAKE lasts well under
    # a second on a wrong key) are not missed.
    t=0; last=""; handshakes=0; assoc_tries=0; associated=0
    while [ $t -lt $((ASSOC_TIMEOUT*4)) ]; do
        st=$(wpa_state)
        if [ "$st" != "$last" ]; then
            log "wpa_state: ${st:-none}"
            case "$st" in
                4WAY_HANDSHAKE|GROUP_HANDSHAKE) handshakes=$((handshakes+1)); associated=1 ;;
                ASSOCIATED) associated=1 ;;
                ASSOCIATING) assoc_tries=$((assoc_tries+1)) ;;
            esac
            last=$st
        fi
        [ "$st" = "COMPLETED" ] && break
        # wpa_supplicant temporarily disables a network after an auth failure
        # (WRONG_KEY / CONN_FAILED); classify immediately instead of waiting.
        if [ $((t % 4)) -eq 0 ] && wpa_cli -p $CTRL_DIR -i $IFACE list_networks 2>/dev/null | grep -q 'TEMP-DISABLED'; then
            if [ $associated -eq 1 ] && grep -q '^[[:space:]]*psk=' $CONF; then
                fail wrong_password "key_rejected_during_4-way_handshake"
            else
                fail assoc_rejected "AP_rejected_authentication/association"
            fi
        fi
        [ -z "$(iface_pids wpa_supplicant)" ] && fail wpa_exited "wpa_supplicant_exited"
        usleep 250000
        t=$((t+1))
    done

    if [ "$last" != "COMPLETED" ]; then
        if [ $associated -eq 1 ] && grep -q '^[[:space:]]*psk=' $CONF; then
            fail wrong_password "4-way_handshake_never_completed"
        elif [ $assoc_tries -gt 0 ]; then
            fail assoc_rejected "AP_did_not_accept_association_(WPA3-only/MAC_filter/band_steering?)"
        elif ! ssid_in_scan; then
            fail not_found "SSID_not_seen_on_2.4GHz"
        else
            fail timeout "no_association_after_${ASSOC_TIMEOUT}s_state_${last:-none}"
        fi
    fi

    WS=$(wpa_cli -p $CTRL_DIR -i $IFACE status 2>/dev/null)
    BSSID=$(echo "$WS" | sed -n 's/^bssid=//p')
    FREQ=$(echo "$WS" | sed -n 's/^freq=//p')
    status "ASSOCIATED bssid=$BSSID freq=$FREQ"
    step_ok "bssid=$BSSID freq=${FREQ}MHz"

    step dhcp

    # DHCP only on a clean, associated link: 10 discovers, 3 s apart. No -q,
    # so udhcpc forks to the background and keeps renewing the lease; -n makes
    # it exit non-zero if no lease is offered.
    log "Waiting for the DHCP lease from ifup's udhcpc"
    # ifup's udhcpc runs -t 10 -T 3 (~30 s); never start a second client beside it.
    n=0; IP=""
    while [ $n -lt 90 ]; do
        IP=$(iface_ip)
        [ -n "$IP" ] && break
        kill -0 $IFUP_PID 2>/dev/null || break      # ifup finished without a lease
        usleep 500000; n=$((n+1))
    done
    if [ -z "$IP" ] && kill -0 $IFUP_PID 2>/dev/null; then
        log "ifup still has no lease after 45 s; stopping it"
        kill $IFUP_PID 2>/dev/null
        for p in $(iface_pids udhcpc); do kill $p 2>/dev/null; done
        sleep 1
    fi
    if [ -z "$IP" ]; then
        log "ifup gave no lease; retrying: udhcpc -n -t 10 -T 3 (no -q, keeps renewing)"
        if ! udhcpc -i $IFACE -n -t 10 -T 3 -A 3 -p $DHCP_PID >>$LOG 2>&1; then
            fail dhcp_failed "no_DHCP_offer_from_router"
        fi
    fi
    # The lease's bound script adds the address; give it a moment before deciding.
    n=0; IP=$(iface_ip)
    while [ -z "$IP" ] && [ $n -lt 10 ]; do sleep 1; n=$((n+1)); IP=$(iface_ip); done
    [ -n "$IP" ] || log_addr_state
    GW=$(ip route show default dev $IFACE 2>/dev/null | sed -n 's/.*via \([0-9.]*\).*/\1/p' | sed -n 1p)
    [ -n "$IP" ] || fail dhcp_failed "lease_reported_but_no_address"
    case "$IP" in
        10.1.1.*) fail subnet_conflict "home_network_uses_10.1.1.0/24_same_as_SoloLink" ;;
    esac
    log "Lease: ip=$IP gateway=${GW:-none}"
    step_ok "ip=$IP gateway=${GW:-none}"

    step nat
    nat_on
    dnsmasq_gateway_on
    step_ok "MASQUERADE -o $IFACE, FORWARD $AP_IFACE<->$IFACE, rp_filter=0"

    step ap_resume
    if ! hostapd_start; then
        # Never leave the copter stranded: give up the home link and retry the AP.
        step_fail "hostapd failed on ${FREQ}MHz; home link dropped"
        status "FAILED reason=ap_restart detail=hostapd_failed_on_channel_${FREQ}MHz"
        CUR_STEP=""
        station_down; station_config_off; nat_off; dnsmasq_gateway_off; hostapd_start
        exit 1
    fi
    step_ok "$(grep '^channel=' /etc/hostapd.conf 2>/dev/null)"

    step internet
    NET=no
    if ping -c 2 -W 3 8.8.8.8 >/dev/null 2>&1 || ping -c 2 -W 3 1.1.1.1 >/dev/null 2>&1; then NET=yes; fi
    if [ "$NET" = yes ]; then step_ok "8.8.8.8 / 1.1.1.1 reachable"; else step_fail "no reply from 8.8.8.8 or 1.1.1.1 (router has no internet?)"; fi
    status "CONNECTED ip=$IP gw=${GW:-none} freq=$FREQ internet=$NET"
    exit 0
}

do_reset() {
    : > $LOG
    log "Reset to SoloLink AP requested"
    station_down
    station_config_off
    nat_off
    dnsmasq_gateway_off
    remove_legacy
    rm -f $CONF $SSIDF
    /etc/init.d/hostapd stop >/dev/null 2>&1
    sleep 1
    hostapd_start
    status "RESET"
}

do_station_off() {
    log "Home Wi-Fi station stopped (arm interlock); SoloLink AP left on its current channel"
    station_down
    station_config_off
    nat_off
    rm -f $CONF
    status "DISCONNECTED reason=armed"
}

do_scan() {
    was_up=0
    ip link show $IFACE 2>/dev/null | grep -q '<[^>]*UP' && was_up=1
    if [ $was_up -eq 0 ]; then
        ip link set $IFACE up 2>/dev/null
        sleep 1
    fi
    # One sweep: flush mac80211's BSS cache and block until the radio has
    # actively probed every allowed channel. Each sweep takes the only radio off
    # SoloLink's channel briefly (the copter beeps on link loss), so no second pass.
    ok=0
    for try in 1 2 3 4 5; do
        if iw dev $IFACE scan flush >/dev/null 2>/tmp/swu_scan.err; then ok=1; break; fi
        sleep 1    # -EBUSY: wpa_supplicant or the AP is mid-scan
    done
    if [ $ok -eq 1 ]; then
        echo "===IW==="
        iw dev $IFACE scan dump 2>/dev/null
    elif wpa_cli -p $CTRL_DIR -i $IFACE ping 2>/dev/null | grep -q PONG; then
        wpa_cli -p $CTRL_DIR -i $IFACE scan >/dev/null 2>&1
        prev=-1; i=0
        while [ $i -lt 10 ]; do
            sleep 1; i=$((i+1))
            n=$(wpa_cli -p $CTRL_DIR -i $IFACE scan_results 2>/dev/null | wc -l)
            [ $i -ge 4 ] && [ "$n" = "$prev" ] && break
            prev=$n
        done
        echo "===WPA==="
        wpa_cli -p $CTRL_DIR -i $IFACE scan_results 2>/dev/null
    else
        echo "===IWLIST==="
        iwlist $IFACE scan 2>/dev/null
    fi
    echo "===ERR==="
    cat /tmp/swu_scan.err 2>/dev/null
    echo "===OWN==="
    sed -n 's/^ssid=//p' /etc/hostapd.conf 2>/dev/null
    [ $was_up -eq 0 ] && [ -z "$(iface_pids wpa_supplicant)" ] && ip link set $IFACE down 2>/dev/null
    exit 0
}

case "$1" in
    connect) do_connect ;;
    reset) do_reset ;;
    station-off) do_station_off ;;
    scan) do_scan ;;
    *) echo "usage: $0 connect|scan|reset|station-off"; exit 2 ;;
esac
'''
# A CRLF checkout must never reach BusyBox ash as \r-terminated lines.
CONTROLLER_SCRIPT = CONTROLLER_SCRIPT.replace("\r\n", "\n")


# ---------------------------------------------------------------------------
# Copter-side route/DNS
# ---------------------------------------------------------------------------
COPTER_IFACE = "wlan0"
COPTER_SUBNET_PREFIX = "10.1.1."
RESOLV_CONF = "/etc/resolv.conf"
PUBLIC_DNS = ("8.8.8.8", "1.1.1.1")
RESOLV_TEXT = ("# Written by SoloWebUtility2 (internet via Controller 10.1.1.1)\n"
               "nameserver 8.8.8.8\nnameserver 1.1.1.1\nnameserver 10.1.1.1\n")


def _run_local(cmd):
    """Run a shell command on the copter -> (rc, output text)."""
    try:
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = p.communicate()
        if PY3:
            out = out.decode("utf-8", "ignore")
        return p.returncode, out or ""
    except Exception as e:
        return 1, str(e)


def _copter_on_sololink():
    """True only on the copter itself (wlan0 holds a 10.1.1.x address).
    Keeps PC/dev runs from rewriting the host's routes or DNS."""
    rc, out = _run_local("ip -4 addr show dev %s 2>/dev/null" % COPTER_IFACE)
    return bool(re.search(r"inet\s+%s\d+" % re.escape(COPTER_SUBNET_PREFIX), out))


def _default_route_ok():
    rc, out = _run_local("ip route show default 2>/dev/null")
    return bool(re.search(r"^default via %s(\s|$)" % re.escape(CONTROLLER_IP), out, re.M))


def _ensure_default_route():
    """Point the copter's default route at the Controller.

    The old check looked for the text "10.1.1.1" in 'ip route' output, but the
    copter's own address 10.1.1.10 always contains that text, so the route was
    never added. 'replace' is idempotent and also fixes a wrong/stale default."""
    if _default_route_ok():
        return True
    _run_local("ip route replace default via %s dev %s 2>/dev/null || "
               "route add default gw %s dev %s 2>/dev/null" % (CONTROLLER_IP, COPTER_IFACE,
                                                               CONTROLLER_IP, COPTER_IFACE))
    return _default_route_ok()


def _resolv_ok(text):
    servers = re.findall(r"^\s*nameserver\s+(\S+)", text or "", re.M)
    return bool(servers) and servers[0] in PUBLIC_DNS


def _ensure_resolv_conf(path=None):
    """Make the first nameservers public resolvers (8.8.8.8 / 1.1.1.1).

    * Symlink (e.g. -> /var/run/resolv.conf): write the real target.
    * Normal file: write a temp file in the same directory and rename it over
      (atomic, never leaves a half-written resolv.conf).
    * Read-only filesystem: bind-mount a tmpfs copy over the path.
    Returns True when the active resolv.conf starts with a public resolver."""
    path = path or RESOLV_CONF
    try:
        with open(path, "r") as f:
            if _resolv_ok(f.read()):
                return True
    except (IOError, OSError):
        pass

    target = os.path.realpath(path)
    try:
        d = os.path.dirname(target) or "."
        if not os.path.isdir(d):
            os.makedirs(d)
        tmp = os.path.join(d, ".resolv.conf.swu.%d" % os.getpid())
        with open(tmp, "w") as f:
            f.write(RESOLV_TEXT)
        os.chmod(tmp, 0o644)
        os.rename(tmp, target)
        return True
    except (IOError, OSError):
        try:
            os.remove(tmp)
        except Exception:
            pass

    # Read-only (or otherwise unwritable) location: shadow it with a RAM copy.
    try:
        shadow = "/tmp/resolv.conf.swu"
        with open(shadow, "w") as f:
            f.write(RESOLV_TEXT)
        if os.path.exists(path):
            _run_local("umount %s 2>/dev/null; mount --bind %s %s" % (path, shadow, path))
        with open(path, "r") as f:
            return _resolv_ok(f.read())
    except Exception:
        return False


def ensure_copter_internet_route():
    """
    Give the copter (10.1.1.10) a working path to the internet through the
    Controller: default route via 10.1.1.1 and public DNS in /etc/resolv.conf.
    hostapd restarts on the Controller drop the copter's wlan0 routes, so this
    is called before every status poll and every update/builder request.
    Safe no-op anywhere except on the copter itself. Returns True when the
    default route via the Controller is in place.
    """
    if not _copter_on_sololink():
        return True
    route_ok = _ensure_default_route()
    _ensure_resolv_conf()
    return route_ok


def _tcp_ok(host, port=53, timeout=1.5):
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sk.settimeout(timeout)
        sk.connect((host, port))
        sk.close()
        return True
    except Exception:
        return False


def copter_network_diagnostics():
    """Repair and report the copter's own path to the internet.
    -> {"applicable", "route_ok", "dns_ok", "internet", "detail"}"""
    if not _copter_on_sololink():
        return {"applicable": False, "route_ok": False, "dns_ok": False,
                "internet": False, "detail": "not running on the copter"}
    route_ok = _ensure_default_route()
    dns_ok = _ensure_resolv_conf()
    internet = route_ok and (_tcp_ok("8.8.8.8") or _tcp_ok("1.1.1.1"))
    if internet:
        detail = "default via 10.1.1.1, DNS 8.8.8.8/1.1.1.1, 8.8.8.8 reachable"
    elif not route_ok:
        detail = "could not set default route via 10.1.1.1"
    else:
        detail = "route %s, DNS %s, but 8.8.8.8/1.1.1.1 not reachable through the Controller" % (
            "ok" if route_ok else "missing", "ok" if dns_ok else "not writable")
    return {"applicable": True, "route_ok": route_ok, "dns_ok": dns_ok,
            "internet": internet, "detail": detail}


def controller_has_internet(timeout=3):
    """True if the Controller itself can reach the internet (ping 8.8.8.8 /
    1.1.1.1). Lets callers fall back to proxying requests through it."""
    if not _ctrl_reachable():
        return False
    rc, out = _ssh("ping -c 1 -W %d 8.8.8.8 >/dev/null 2>&1 || ping -c 1 -W %d 1.1.1.1 >/dev/null 2>&1; "
                   "echo RC=$?" % (timeout, timeout), timeout=timeout * 2 + 15)
    return _has_line(out, "RC=0")


# ---------------------------------------------------------------------------
# Scan parsers
# ---------------------------------------------------------------------------
_ESC_RE = re.compile(r"\\x([0-9a-fA-F]{2})")


def _unescape_ssid(s):
    """iw / wpa_cli print non-printable SSID bytes as \\xNN; restore them."""
    s = s.strip("\r\n")
    if "\\x" not in s:
        if not PY3 and isinstance(s, bytes):
            return s.decode("utf-8", "replace")
        return s
    raw = _ESC_RE.sub(lambda m: chr(int(m.group(1), 16)), s)
    if PY3:
        return raw.encode("latin-1").decode("utf-8", "replace")
    return raw.decode("utf-8", "replace")


def _is_hidden(ssid):
    return (not ssid) or ssid.startswith("\\x00") or set(ssid) == set(["\x00"])


def _chan_from_freq(freq):
    if freq == 2484:
        return 14
    if 2412 <= freq <= 2472:
        return (freq - 2407) // 5
    if 5000 <= freq <= 5900:
        return (freq - 5000) // 5
    return 0


def _merge_entry(networks, seen, entry):
    ssid = entry["ssid"]
    if ssid in seen:
        if entry["signal"] > seen[ssid]["signal"]:
            seen[ssid].update({"signal": entry["signal"], "quality": entry["quality"],
                               "channel": entry["channel"]})
    else:
        seen[ssid] = entry
        networks.append(entry)


def _quality(signal):
    return max(10, min(100, 2 * (signal + 100)))


def _parse_iwlist(output):
    """Parse iwlist wlan0 scan output into structured network objects."""
    networks = []
    seen = {}
    cells = output.split("Cell ")
    for block in cells[1:]:
        m_ssid = re.search(r'ESSID:\"([^\"]*)\"', block)
        if not m_ssid:
            continue
        ssid = _unescape_ssid(m_ssid.group(1))
        if _is_hidden(ssid.strip()):
            continue

        m_sig = re.search(r'Signal level[=:](-?\d+)', block)
        signal = int(m_sig.group(1)) if m_sig else -75

        m_qual = re.search(r'Quality=(\d+)/(\d+)', block)
        quality = int(int(m_qual.group(1)) * 100 / int(m_qual.group(2))) if m_qual else 50

        m_ch = re.search(r'Channel:?(\d+)', block)
        channel = int(m_ch.group(1)) if m_ch else 0

        sec = "Open"
        if "WPA2" in block or "IEEE 802.11i" in block:
            sec = "WPA2"
        elif "WPA" in block:
            sec = "WPA"
        elif "Encryption key:on" in block:
            sec = "WEP"

        _merge_entry(networks, seen, {"ssid": ssid, "signal": signal, "quality": quality,
                                      "channel": channel, "security": sec})

    networks.sort(key=lambda x: x.get("signal", -100), reverse=True)
    return networks


_IW_BSS_RE = re.compile(r"^BSS ([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})", re.M)


def _iw_security(block):
    """Security label from an 'iw scan' BSS block (RSN/WPA IEs)."""
    rsn = re.search(r"^\s*RSN:(.*?)(?=^\s*[A-Z][A-Za-z ]+:|\Z)", block, re.M | re.S)
    wpa = re.search(r"^\s*WPA:(.*?)(?=^\s*[A-Z][A-Za-z ]+:|\Z)", block, re.M | re.S)
    ie = rsn.group(1) if rsn else (wpa.group(1) if wpa else "")
    m_auth = re.search(r"Authentication suites:\s*([^\n]*)", ie)
    suites = m_auth.group(1).split() if m_auth else []
    if rsn or wpa:
        if "IEEE" in suites or "802.1X" in " ".join(suites):
            return "WPA2-Enterprise"
        if "SAE" in suites and "PSK" not in suites:
            return "WPA3"
        if "SAE" in suites:
            return "WPA2/WPA3"
        return "WPA2" if rsn else "WPA"
    if re.search(r"capability:.*\bPrivacy\b", block):
        return "WEP"
    return "Open"


def _parse_iw(output):
    """Parse 'iw dev wlan0 scan [dump]' output.

    Blocks are split on real 'BSS xx:xx:xx:xx:xx:xx' headers (iw also prints
    'BSS Load:' lines), and an empty 'SSID:' line is treated as hidden instead
    of swallowing the next line.
    """
    networks = []
    seen = {}
    output = output.replace("\r", "")
    starts = [m.start() for m in _IW_BSS_RE.finditer(output)]
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(output)
        block = output[start:end]
        m_ssid = re.search(r"^\s*SSID: ?(.*)$", block, re.M)
        if not m_ssid:
            continue
        ssid = _unescape_ssid(m_ssid.group(1))
        if _is_hidden(ssid.strip()):
            continue

        m_sig = re.search(r"signal:\s*(-?\d+\.?\d*)", block)
        signal = int(float(m_sig.group(1))) if m_sig else -75
        m_freq = re.search(r"freq:\s*(\d+)", block)
        channel = _chan_from_freq(int(m_freq.group(1))) if m_freq else 0

        _merge_entry(networks, seen, {"ssid": ssid, "signal": signal, "quality": _quality(signal),
                                      "channel": channel, "security": _iw_security(block)})

    networks.sort(key=lambda x: x.get("signal", -100), reverse=True)
    return networks


def _wpa_flags_security(flags):
    if "EAP" in flags:
        return "WPA2-Enterprise"
    if "SAE" in flags and "PSK" not in flags:
        return "WPA3"
    if "SAE" in flags:
        return "WPA2/WPA3"
    if "WPA2" in flags or "RSN" in flags:
        return "WPA2"
    if "WPA" in flags:
        return "WPA"
    if "WEP" in flags:
        return "WEP"
    return "Open"


def _parse_wpa_cli(output):
    """Parse 'wpa_cli scan_results' tab-separated table."""
    networks = []
    seen = {}
    for line in output.replace("\r", "").splitlines():
        parts = line.split("\t", 4)
        if len(parts) < 5 or not re.match(r"^[0-9a-fA-F:]{17}$", parts[0].strip()):
            continue
        ssid = _unescape_ssid(parts[4])
        if _is_hidden(ssid.strip()):
            continue
        try:
            channel = _chan_from_freq(int(parts[1].strip()))
        except Exception:
            channel = 0
        try:
            signal = int(parts[2].strip())
        except Exception:
            signal = -70
        _merge_entry(networks, seen, {"ssid": ssid, "signal": signal, "quality": _quality(signal),
                                      "channel": channel,
                                      "security": _wpa_flags_security(parts[3])})

    networks.sort(key=lambda x: x.get("signal", -100), reverse=True)
    return networks


def _parse_wl(output):
    """Parse Broadcom 'wl scanresults' output."""
    networks = []
    seen = {}
    blocks = output.split("BSSID:")
    for block in blocks[1:]:
        m_ssid = re.search(r'SSID:\s*\"([^\"]*)\"', block)
        if not m_ssid:
            m_ssid = re.search(r'SSID:\s*([^\r\n]+)', block)
        if not m_ssid:
            continue
        ssid = m_ssid.group(1).strip().strip('"')
        if not ssid or ssid.startswith('\\x00'):
            continue

        m_rssi = re.search(r'RSSI:\s*(-?\d+)', block)
        signal = int(m_rssi.group(1)) if m_rssi else -70

        m_ch = re.search(r'Channel:\s*(\d+)', block)
        channel = int(m_ch.group(1)) if m_ch else 0

        sec = "Open"
        if "WPA2" in block:
            sec = "WPA2"
        elif "WPA" in block:
            sec = "WPA"
        elif "WEP" in block or "Privacy" in block:
            sec = "WEP"

        _merge_entry(networks, seen, {"ssid": ssid, "signal": signal, "quality": _quality(signal),
                                      "channel": channel, "security": sec})

    networks.sort(key=lambda x: x.get("signal", -100), reverse=True)
    return networks


def _parse_scan_output(text):
    """Auto-detect format and parse wireless scan output."""
    if not text or not text.strip():
        return []
    if _IW_BSS_RE.search(text):
        nets = _parse_iw(text)
        if nets:
            return nets
    if "bssid / frequency" in text.lower() or re.search(r"^[0-9a-fA-F:]{17}\t", text, re.M):
        nets = _parse_wpa_cli(text)
        if nets:
            return nets
    if "BSSID:" in text and "RSSI:" in text:
        nets = _parse_wl(text)
        if nets:
            return nets
    if "Cell " in text:
        nets = _parse_iwlist(text)
        if nets:
            return nets
    return []


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------
# Security of each SSID seen in the most recent scan (the web server is a
# long-running process, so this survives between the Scan and Connect clicks).
_LAST_SCAN = {}
SUPPORTED_SECURITY = ("Open", "WPA", "WPA2", "WPA2/WPA3")
UNSUPPORTED_TEXT = {
    "WPA3": "WPA3-only: the Controller's wpa_supplicant 2.4 cannot join it. "
            "Switch the router/hotspot to WPA2/WPA3 mixed (transition) mode.",
    "WPA2-Enterprise": "Enterprise (802.1X) login is not supported; use a WPA2-Personal network.",
    "WEP": "WEP is not supported; use a WPA2-Personal network.",
}

SCAN_CACHE_SECONDS = 30
_scan_cache = {"t": 0.0, "result": None}


def scan_wifi_networks():
    """
    Active 2.4 GHz sweep on the controller's station radio (wlan0).

    The helper runs "iw dev wlan0 scan flush" (empties the kernel BSS cache and
    blocks until every allowed channel has been probed) followed by a second
    "iw dev wlan0 scan" and prints "scan dump", so the very first scan returns
    what is on the air now instead of a cached list holding only SoloLink.
    The copter radio is no longer queried: it is associated to SoloLink, and
    its wpa_supplicant cache was the source of the stale single-entry list.
    """
    # Re-use a recent result: every real scan blips the SoloLink link.
    cached = _scan_cache["result"]
    if cached is not None and time.time() - _scan_cache["t"] < SCAN_CACHE_SECONDS:
        return cached

    if not _ctrl_reachable():
        return {"success": False, "count": 0, "networks": [],
                "message": "Controller (10.1.1.1) is not reachable; cannot scan."}

    cmd = "%s && sh %s scan" % (_push_file_cmd(REMOTE_SCRIPT, CONTROLLER_SCRIPT, "755"), REMOTE_SCRIPT)
    rc, out = _ssh(cmd, timeout=SCAN_TIMEOUT)
    sec = _sections(out, ["IW", "WPA", "IWLIST", "ERR", "OWN"])
    _log("scan: rc=%s sections=%s" % (rc, ",".join(sorted(sec.keys())) or "none"))
    if not sec:
        _log("scan output: %s" % " | ".join(out.strip().splitlines()[-6:])[:400])

    if "IW" in sec:
        networks = _parse_iw(sec["IW"])
    elif "WPA" in sec:
        networks = _parse_wpa_cli(sec["WPA"])
    elif "IWLIST" in sec:
        networks = _parse_iwlist(sec["IWLIST"])
    else:
        networks = _parse_scan_output(out)   # older helper / unexpected output

    own = (sec.get("OWN") or "").strip()
    networks = [n for n in networks
                if n.get("ssid") and n["ssid"] != own and n.get("channel", 0) <= 14]
    networks.sort(key=lambda x: x.get("signal", -100), reverse=True)
    for n in networks:
        _LAST_SCAN[n["ssid"]] = n.get("security", "")
        n["supported"] = n.get("security") in SUPPORTED_SECURITY
        if not n["supported"]:
            n["note"] = UNSUPPORTED_TEXT.get(n.get("security"), "Not supported by the Controller")

    if networks:
        msg = "Found %d visible 2.4 GHz network(s)." % len(networks)
    else:
        err = (sec.get("ERR") or "").strip().splitlines()
        msg = "No 2.4 GHz broadcast Wi-Fi networks found."
        if err:
            msg += " (radio: %s)" % err[-1][:120]
    result = {"success": True, "count": len(networks), "networks": networks, "message": msg}
    if networks:
        _scan_cache["t"], _scan_cache["result"] = time.time(), result
    return result


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
FAILURE_TEXT = {
    "wrong_password": "Wrong Wi-Fi password: '%(ssid)s' rejected the key during the WPA handshake.",
    "assoc_rejected": ("'%(ssid)s' refused the connection. The controller supports WPA/WPA2-Personal "
                       "or open networks on 2.4 GHz; WPA3-only, MAC filtering or band steering will block it."),
    "not_found": ("'%(ssid)s' was not seen on 2.4 GHz. The controller radio is 2.4 GHz only "
                  "(channels 1-11): check the router broadcasts a 2.4 GHz network and is in range."),
    "timeout": "No association with '%(ssid)s' within 30 seconds (%(detail)s).",
    "station_mode": "Could not turn on the Controller's built-in station mode (%(detail)s).",
    "wpa_start": "The Controller's built-in station mode did not start wpa_supplicant (%(detail)s).",
    "dhcp_failed": "Joined '%(ssid)s' but the router did not hand out an IP address (DHCP).",
    "subnet_conflict": ("'%(ssid)s' uses the 10.1.1.x subnet, the same as SoloLink. "
                        "Change the router's LAN subnet (e.g. 192.168.x.x)."),
    "ap_restart": "Joined '%(ssid)s' but SoloLink could not restart on its channel; home Wi-Fi was dropped.",
}


CONNECT_STEPS = [
    ("prepare", "Send settings to the Controller"),
    ("teardown", "Reset the Controller's station interface (wlan0)"),
    ("ap_pause", "Pause SoloLink AP (one radio, one channel)"),
    ("supplicant", "Save network and start built-in station mode (ifup wlan0)"),
    ("associate", "Join the network and check the password"),
    ("dhcp", "Get an IP address from the router (DHCP)"),
    ("nat", "Share the connection with Solo (NAT + forwarding)"),
    ("ap_resume", "Restart SoloLink AP on the home channel"),
    ("internet", "Controller internet check"),
    ("copter", "Copter route, DNS and internet"),
]


def _parse_steps(log_text, copter=None, run_finished=False):
    """'STEP <id> RUN|OK|FAIL <detail>' lines -> ordered list of step dicts.
    state: pending | running | ok | failed | skipped."""
    seen = {}
    for line in log_text.splitlines():
        m = re.search(r"STEP (\w+) (RUN|OK|FAIL) ?(.*)$", line)
        if m:
            seen[m.group(1)] = (m.group(2), m.group(3).strip())
    steps, failed = [], False
    for sid, label in CONNECT_STEPS:
        state, detail = "pending", ""
        if sid in seen:
            mark, detail = seen[sid]
            state = {"RUN": "running", "OK": "ok", "FAIL": "failed"}[mark]
            if mark == "FAIL":
                detail = detail.replace("_", " ")
        if sid == "copter" and copter is not None:
            if not copter.get("applicable"):
                state, detail = "skipped", copter.get("detail", "")
            else:
                state = "ok" if copter.get("internet") else "failed"
                detail = copter.get("detail", "")
        if failed and state == "pending":
            state = "skipped"
        if state == "running" and run_finished:
            state = "failed"
        if state == "failed" and sid != "internet":
            failed = True
        steps.append({"id": sid, "label": label, "state": state, "detail": detail})
    return steps


def _parse_status_line(log_text):
    """Last 'STATUS: WORD k=v ...' line -> (word, {k: v})."""
    word, fields = "", {}
    for line in log_text.splitlines():
        m = re.search(r"STATUS: (\w+)(.*)$", line)
        if m:
            word = m.group(1)
            fields = dict(re.findall(r"(\w+)=(\S+)", m.group(2)))
    return word, fields


def describe_failure(reason, detail, ssid):
    tmpl = FAILURE_TEXT.get(reason)
    detail_txt = (detail or "").replace("_", " ")
    if tmpl:
        return tmpl % {"ssid": ssid or "home Wi-Fi", "detail": detail_txt}
    return "Controller Wi-Fi error (%s): %s" % (reason or "unknown", detail_txt or "see setup log")


def get_wifi_status():
    """Controller Wi-Fi status plus a per-step connection checklist ("steps").
    When the Controller has a home address, the copter's own route/DNS are
    repaired and its internet access is tested as the final step."""

    if not _ctrl_reachable():
        return {
            "connected_ssid": "SoloLink_AP",
            "mode": "Access Point (SoloLink)",
            "ip_address": CONTROLLER_IP,
            "internet_connected": False,
            "bidirectional_enabled": False,
            "ip_forwarding": True,
            "controller_online": False,
            "connection_state": "unknown",
            # NB: never "error" - the page's apiRequest() treats a truthy "error" key as a failed request.
            "failure": "Controller (10.1.1.1) is not answering on SSH. Is it on and paired?",
            "warning": "",
            "steps": [],
            "copter_internet": False,
            "copter_detail": "",
            "setup_log": "",
        }

    batch_cmd = (
        "echo '===SSID==='; cat %(ssidf)s 2>/dev/null; echo; "
        "echo '===ADDR==='; ip addr show %(sta)s 2>/dev/null | grep 'inet '; "
        "echo '===PING==='; if ip addr show %(sta)s 2>/dev/null | grep -q 'inet '; then "
        "(ping -c 1 -W 2 8.8.8.8 >/dev/null 2>&1 || ping -c 1 -W 2 1.1.1.1 >/dev/null 2>&1) "
        "&& echo PING_OK || echo PING_FAIL; else echo NO_ADDR; fi; "
        "echo '===FWD==='; cat /proc/sys/net/ipv4/ip_forward 2>/dev/null; "
        "echo '===IPT==='; iptables -S FORWARD 2>/dev/null; "
        "echo '===LOG==='; tail -n 25 %(log)s 2>/dev/null; "
        "echo '===WPA==='; wpa_cli -p %(ctrl)s -i %(sta)s status 2>/dev/null | grep -E '^(wpa_state|ssid|freq)='; "
        "echo '===END==='"
    ) % {"ssidf": REMOTE_SSID, "sta": STA_IFACE, "log": REMOTE_LOG, "ctrl": CTRL_DIR}
    _, out = _ssh(batch_cmd)
    sec = _sections(out, ["SSID", "ADDR", "PING", "FWD", "IPT", "LOG", "WPA", "END"])
    ssid_out = sec.get("SSID", "")
    addr_out = sec.get("ADDR", "")
    ping_out = sec.get("PING", "")
    fwd_out = sec.get("FWD", "")
    ipt_out = sec.get("IPT", "")
    log_out = sec.get("LOG", "").strip()
    wpa_out = sec.get("WPA", "")

    # SSID file holds the plain SSID; skip any ssh banner/password prompt lines.
    sta_ssid = u""
    ssid_lines = [l for l in ssid_out.splitlines() if l.strip()]
    if ssid_lines:
        sta_ssid = _to_text(ssid_lines[-1])
    else:
        m = re.search(r"^ssid=(.+)$", wpa_out, re.M)
        if m:
            sta_ssid = _unescape_ssid(m.group(1))

    ip_match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", addr_out)
    ctrl_home_ip = ip_match.group(1) if ip_match else ""
    internet_online = "PING_OK" in ping_out

    ip_forward = fwd_out.strip().endswith("1")
    blocked = any(("-i %s -o %s" % (STA_IFACE, AP_IFACE)) in l and "-j DROP" in l
                  for l in ipt_out.splitlines())
    bidirectional = bool(ctrl_home_ip) and ip_forward and not blocked

    word, fields = _parse_status_line(log_out)
    m_state = re.search(r"^wpa_state=(\S+)", wpa_out, re.M)
    wpa_state = m_state.group(1) if m_state else ""

    copter = None
    if ctrl_home_ip:
        copter = copter_network_diagnostics()
        if copter.get("internet"):
            internet_online = True
    steps = _parse_steps(log_out, copter, run_finished=(word in ("FAILED", "CONNECTED")))

    error = ""
    warning = ""
    if ctrl_home_ip:
        state = "connected"
        if copter and copter.get("applicable") and not copter.get("internet"):
            warning = "Controller is on '%s' but the copter cannot reach the internet: %s" % (
                sta_ssid or "home Wi-Fi", copter.get("detail"))
        elif "internet=no" in log_out and not internet_online:
            warning = "Connected to '%s', but the router itself has no internet access." % (
                sta_ssid or "home Wi-Fi")
        mode = "Home Wi-Fi (Connected)" if internet_online else "Home Wi-Fi (No Internet)"
        ssid_display = sta_ssid or "Home Wi-Fi"
    elif word == "FAILED":
        state = "failed"
        error = describe_failure(fields.get("reason"), fields.get("detail"), sta_ssid)
        mode = "Connection Failed"
        ssid_display = u"%s (Failed)" % (sta_ssid or "Home Wi-Fi")
    elif word in ("CONNECTING", "ASSOCIATED") or wpa_state in (
            "SCANNING", "AUTHENTICATING", "ASSOCIATING", "ASSOCIATED", "4WAY_HANDSHAKE", "GROUP_HANDSHAKE"):
        state = "connecting"
        mode = "Connecting..."
        ssid_display = u"%s (Connecting...)" % (sta_ssid or "Home Wi-Fi")
    elif word == "DISCONNECTED":
        state = "idle"
        mode = "Access Point (SoloLink)"
        ssid_display = "SoloLink_AP"
        warning = "Home Wi-Fi was disconnected automatically because the vehicle armed."
    elif word == "CONNECTED":
        state = "failed"
        error = "The home Wi-Fi link to '%s' was lost (no address on wlan0)." % (sta_ssid or "home Wi-Fi")
        mode = "Connection Lost"
        ssid_display = u"%s (Lost)" % (sta_ssid or "Home Wi-Fi")
    else:
        state = "idle"
        mode = "Access Point (SoloLink)"
        ssid_display = "SoloLink_AP"

    _note_station(state in ("connected", "connecting"))
    return {
        "connected_ssid": ssid_display,
        "mode": mode,
        "ip_address": ctrl_home_ip or CONTROLLER_IP,
        "internet_connected": internet_online,
        "bidirectional_enabled": bidirectional,
        "ip_forwarding": ip_forward,
        "controller_online": True,
        "connection_state": state,
        "wpa_state": wpa_state,
        "failure": error,
        "warning": warning,
        "steps": steps,
        "copter_internet": bool(copter and copter.get("internet")),
        "copter_detail": (copter or {}).get("detail", ""),
        "setup_log": log_out,
    }


# ---------------------------------------------------------------------------
# Connect / disconnect
# ---------------------------------------------------------------------------
# True while the Controller may be on home Wi-Fi. Starts True because the web
# server can restart while the Controller is still connected; the first arming
# then sends one harmless station-off.
_STATION_MAYBE_ACTIVE = True
_interlock_lock = threading.Lock()
_interlock_thread = None


def _note_station(active):
    global _STATION_MAYBE_ACTIVE
    _STATION_MAYBE_ACTIVE = bool(active)


def disconnect_on_armed():
    """
    Safety interlock: drop the controller's home Wi-Fi station when the vehicle
    is armed. The SoloLink AP is deliberately NOT restarted (that would cut the
    copter link in flight); it simply stays on its current channel.
    Returns True when the Controller confirmed the station is off.
    """
    if not _ctrl_reachable():
        return False
    try:
        cmd = "%s && sh %s station-off; echo STATION_OFF_RC=$?" % (
            _push_file_cmd(REMOTE_SCRIPT, CONTROLLER_SCRIPT, "755"), REMOTE_SCRIPT)
        rc, out = _ssh(cmd, timeout=30)
        ok = _has_line(out, "STATION_OFF_RC=0")
        _log("arm interlock station-off: rc=%s confirmed=%s" % (rc, ok))
        if ok:
            _note_station(False)
            return True
    except Exception:
        pass
    return False


def _default_is_armed():
    import cockpit_mgr
    return cockpit_mgr.is_vehicle_armed()


def _interlock_loop(is_armed_fn, interval, retry_s):
    was_armed = False
    last_try = 0.0
    while True:
        try:
            armed = bool(is_armed_fn())
        except Exception:
            armed = False
        now = time.time()
        if armed and _STATION_MAYBE_ACTIVE and (not was_armed or now - last_try >= retry_s):
            last_try = now
            ok = disconnect_on_armed()
            sys.stdout.write("[wifi] vehicle armed: home Wi-Fi station %s\n" % (
                "dropped" if ok else "drop FAILED, retrying in %ds" % retry_s))
            sys.stdout.flush()
        was_armed = armed
        time.sleep(interval)


def start_arm_interlock(is_armed_fn=None, interval=1.0, retry_s=10):
    """Background thread: when the vehicle arms while the Controller may be on
    home Wi-Fi, drop the station link (retrying until confirmed). Idempotent."""
    global _interlock_thread
    with _interlock_lock:
        if _interlock_thread is not None and _interlock_thread.is_alive():
            return _interlock_thread
        t = threading.Thread(target=_interlock_loop,
                             args=(is_armed_fn or _default_is_armed, interval, retry_s))
        t.daemon = True
        t.start()
        _interlock_thread = t
        return t


def _preflight(ssid, password, security=None):
    """Reject combinations that can only fail, before SoloLink is interrupted.
    Returns (error_or_None, password_to_use)."""
    sec = security or _LAST_SCAN.get(ssid)
    if sec in UNSUPPORTED_TEXT:
        return "'%s' cannot be used: %s" % (ssid, UNSUPPORTED_TEXT[sec]), password
    if sec == "Open":
        return None, ""                         # open network: ignore any typed password
    if sec in SUPPORTED_SECURITY and not password:
        return "'%s' is secured (%s). Enter its Wi-Fi password." % (ssid, sec), password
    return None, password


def connect_home_wifi(ssid, password, security=None):
    """
    Connect the Controller's wlan0 to a home network using SoloLink's built-in
    station mode (StationEnable=True + /etc/wpa_supplicant.conf + ifup wlan0).
    The saved settings are switched back off as soon as wpa_supplicant has
    started, so the link lasts only until the Controller reboots; arming the
    vehicle also drops it (start_arm_interlock). The work runs detached on the controller
    because SoloLink drops while the radio retunes; poll get_wifi_status()
    for the outcome ("connection_state" / "failure" / "steps").
    """
    ssid = (ssid or "").strip()
    password = password or ""
    _log("connect requested: ssid=%r security=%r password_len=%d" % (ssid, security or _LAST_SCAN.get(ssid), len(password)))
    err, password = _preflight(ssid, password, security)
    if err:
        _log("connect refused (pre-flight): %s" % err)
        return False, err
    err = _validate_credentials(ssid, password)
    if err:
        _log("connect refused (credentials): %s" % err)
        return False, err

    try:
        import cockpit_mgr
        if cockpit_mgr.is_vehicle_armed():
            _log("connect refused: telemetry reports the vehicle ARMED")
            return False, "SAFETY INTERLOCK: Cannot connect to home Wi-Fi while vehicle is ARMED!"
    except Exception:
        pass

    if not _ctrl_reachable():
        _log("connect refused: 10.1.1.1:22 not reachable")
        return False, "Controller (10.1.1.1) is not reachable over SSH."

    try:
        conf = build_wpa_config(ssid, password)
    except Exception as e:
        return False, "Could not build the Wi-Fi configuration: %s" % e

    deploy_cmd = _join_cmds([
        _push_file_cmd(REMOTE_SCRIPT, CONTROLLER_SCRIPT, "755") + " || { echo DEPLOY_FAILED; exit 1; }",
        _push_file_cmd(REMOTE_CONF, conf, "600") + " || { echo DEPLOY_FAILED; exit 1; }",
        _push_file_cmd(REMOTE_SSID, _to_bytes(ssid) + b"\n", "600") + " || { echo DEPLOY_FAILED; exit 1; }",
        # fresh log first, so the UI never shows a previous attempt's FAILED
        "{ echo \"$(date '+%%H:%%M:%%S') STATUS: CONNECTING\"; "
        "echo \"$(date '+%%H:%%M:%%S') STEP prepare OK settings and helper sent\"; } > %s" % REMOTE_LOG,
        "test -s %s && test -x %s || { echo DEPLOY_FAILED; exit 1; }" % (REMOTE_CONF, REMOTE_SCRIPT),
        "setsid sh %s connect </dev/null >/dev/null 2>&1 &" % REMOTE_SCRIPT,
        "echo LAUNCHED",
    ])
    rc, out = _ssh(deploy_cmd, timeout=CONNECT_TIMEOUT)
    launched = _has_line(out, "LAUNCHED")
    _log("connect deploy over SSH: rc=%s launched=%s" % (rc, launched))
    if not launched:
        _log("connect deploy output: %s" % " | ".join(out.strip().splitlines()[-6:])[:400])
        tail = " ".join(out.strip().splitlines()[-3:])[:200]
        return False, "Failed to start the Wi-Fi helper on the Controller (rc=%s): %s" % (rc, tail or "no output")

    _note_station(True)
    ensure_copter_internet_route()
    return True, ("Connecting Controller to '%s' using its built-in station mode... SoloLink drops "
                  "for ~15-40 s while the radio joins the home network. The connection lasts until "
                  "the Controller reboots, you reset to default, or the vehicle arms." % ssid)


def set_bidirectional(enable=True):
    """
    Allow (enable) or block (disable) connections initiated from the home LAN
    into the Solo subnet (10.1.1.0/24). Outbound internet for the copter is
    unaffected: the stock MASQUERADE rule and ip_forward=1 are left alone.
    (The kernel has no conntrack match module, so "disable" drops new inbound
    TCP connections and pings rather than all inbound packets.)
    """
    if not _ctrl_reachable():
        return False, "Controller not reachable."

    fwd_in = "-i %s -o %s" % (STA_IFACE, AP_IFACE)
    fwd_out = "-i %s -o %s" % (AP_IFACE, STA_IFACE)
    drop_syn = "FORWARD %s -p tcp --syn -j DROP" % fwd_in
    drop_ping = "FORWARD %s -p icmp --icmp-type echo-request -j DROP" % fwd_in
    if enable:
        cmd = _join_cmds([
            "modprobe iptable_filter 2>/dev/null",
            "echo 1 > /proc/sys/net/ipv4/ip_forward",
            "while iptables -D %s 2>/dev/null; do :; done" % drop_syn,
            "while iptables -D %s 2>/dev/null; do :; done" % drop_ping,
            "while iptables -D FORWARD %s -j ACCEPT 2>/dev/null; do :; done" % fwd_out,
            "while iptables -D FORWARD %s -j ACCEPT 2>/dev/null; do :; done" % fwd_in,
            "iptables -I FORWARD 1 %s -j ACCEPT" % fwd_in,
            "iptables -I FORWARD 1 %s -j ACCEPT" % fwd_out,
            "echo DONE",
        ])
        msg = "Bi-directional routing enabled. Home LAN can now reach Solo subnet (10.1.1.0/24)."
    else:
        cmd = _join_cmds([
            "modprobe iptable_filter 2>/dev/null",
            "while iptables -D FORWARD %s -j ACCEPT 2>/dev/null; do :; done" % fwd_in,
            "iptables -C %s 2>/dev/null || iptables -I %s" % (drop_syn, drop_syn),
            "iptables -C %s 2>/dev/null || iptables -I %s" % (drop_ping, drop_ping),
            "echo DONE",
        ])
        msg = "Bi-directional routing disabled. New connections from the home LAN are blocked."

    rc, out = _ssh(cmd)
    if not _has_line(out, "DONE"):
        return False, "Could not update the Controller firewall: %s" % out.strip()[-200:]
    return True, msg


def reset_to_default_ap():
    """
    Restore Controller Wi-Fi to factory SoloLink Access Point mode.
    Tears down the home station cleanly, removes only this tool's firewall
    rules, restores dnsmasq and restarts hostapd on its default channel.
    """
    if not _ctrl_reachable():
        return True, "Controller not currently reachable - it may already be in AP mode."

    cmd = _join_cmds([
        _push_file_cmd(REMOTE_SCRIPT, CONTROLLER_SCRIPT, "755") + " || { echo DEPLOY_FAILED; exit 1; }",
        "setsid sh %s reset </dev/null >/dev/null 2>&1 &" % REMOTE_SCRIPT,
        "echo LAUNCHED",
    ])
    rc, out = _ssh(cmd, timeout=CONNECT_TIMEOUT)
    if not _has_line(out, "LAUNCHED"):
        return False, "Could not start the reset on the Controller: %s" % out.strip()[-200:]
    _note_station(False)
    return True, ("Controller Wi-Fi resetting to default SoloLink Access Point mode (10.1.1.1). "
                  "SoloLink drops for a few seconds while hostapd restarts.")
