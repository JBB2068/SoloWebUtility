#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
3DR Solo Controller Wi-Fi & Internet Management - v1.0.25
Directly converted from the proven setup-wifi.bat / solo_wifi.py utility.
Compatible with Python 2.7 and Python 3.x.
"""

import os
import sys
import subprocess
import socket
import time
import re

import flash_mgr

CONTROLLER_IP = "10.1.1.1"
SSH_USER = "root"
SSH_OPTS = "-o StrictHostKeyChecking=no -o ConnectTimeout=10"


def _ssh(cmd, host=CONTROLLER_IP):
    """Execute a command on the Controller via SSH. Returns (rc, output)."""
    full = 'ssh %s %s@%s "%s"' % (SSH_OPTS, SSH_USER, host, cmd.replace('"', '\\"'))
    return flash_mgr._run_with_ssh_auth(full, host=host, timeout=20)


def _ssh_paramiko(cmd, host=CONTROLLER_IP, timeout=30):
    """
    Execute a command on the Controller using Paramiko directly.
    Bypasses the shell/cmd.exe wrapper -- safe for multi-line scripts and
    heredocs that break under Windows cmd.exe quoting.
    Returns (rc, output_str).
    """
    try:
        import paramiko
    except ImportError:
        return _ssh(cmd, host=host)
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            host, port=22, username=SSH_USER,
            password=flash_mgr.SOLO_ROOT_PASSWORD,
            timeout=12.0, look_for_keys=False, allow_agent=False
        )
        stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        stdin.close()
        out = stdout.read()
        err = stderr.read()
        rc = stdout.channel.recv_exit_status()
        client.close()
        if sys.version_info[0] >= 3:
            out = out.decode('utf-8', 'ignore')
            err = err.decode('utf-8', 'ignore')
        return rc, out + err
    except Exception as e:
        return 1, str(e)



def _ctrl_reachable():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.2)
        s.connect((CONTROLLER_IP, 22))
        s.close()
        return True
    except Exception:
        return False


def ensure_copter_internet_route():
    """
    Ensure the Copter has a default gateway via Controller (10.1.1.1)
    and public DNS servers (8.8.8.8, 1.1.1.1, 10.1.1.1) configured in /etc/resolv.conf.
    When the Controller cycles hostapd, Linux drops all wlan0 routes on the Copter.
    This function restores the default gateway and DNS so the Copter can access the internet.
    """
    if sys.platform == "win32":
        return True

    # 1. Ensure default gateway 10.1.1.1 on Copter
    try:
        p = subprocess.Popen("ip route show 2>/dev/null || route -n", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _ = p.communicate()
        if sys.version_info[0] >= 3:
            out = out.decode("utf-8", "ignore")
        if "10.1.1.1" not in out and "default" not in out:
            os.system("route add default gw 10.1.1.1 dev wlan0 2>/dev/null || ip route add default via 10.1.1.1 2>/dev/null || true")
    except Exception:
        pass

    # 2. Ensure /etc/resolv.conf has working DNS (Google, Cloudflare, Controller local dnsmasq)
    try:
        resolv_path = "/etc/resolv.conf"
        content = ""
        if os.path.exists(resolv_path):
            with open(resolv_path, "r") as rf:
                content = rf.read()
        if "8.8.8.8" not in content and "1.1.1.1" not in content:
            with open(resolv_path, "a") as af:
                af.write("\nnameserver 8.8.8.8\nnameserver 1.1.1.1\nnameserver 10.1.1.1\n")
    except Exception:
        pass

    return True


def get_wifi_status():
    """Return current Wi-Fi status from the controller."""
    # Ensure Copter default route and DNS are set
    ensure_copter_internet_route()

    if not _ctrl_reachable():
        return {
            "connected_ssid": "SoloLink_AP",
            "mode": "Access Point (SoloLink)",
            "ip_address": CONTROLLER_IP,
            "internet_connected": False,
            "bidirectional_enabled": False,
            "ip_forwarding": True,
            "controller_online": False
        }

    # Combined single-shot SSH query to reduce Wi-Fi link traffic and latency
    batch_cmd = (
        "cat /tmp/wpa_client.conf /etc/wpa_client.conf 2>/dev/null | grep 'ssid=' || true; echo '===SEC==='; "
        "ip -4 addr show dev wlan0 2>/dev/null | grep 'inet ' || true; echo '===SEC==='; "
        "(ping -c 2 -W 2 8.8.8.8 >/dev/null 2>&1 || ping -c 2 -W 2 1.1.1.1 >/dev/null 2>&1) && echo PING_OK || echo PING_FAIL; echo '===SEC==='; "
        "cat /proc/sys/net/ipv4/ip_forward 2>/dev/null || echo 0; echo '===SEC==='; "
        "iptables -t nat -L -n 2>/dev/null || true; echo '===SEC==='; "
        "tail -n 12 /tmp/wifi_setup.log 2>/dev/null || true"
    )
    _, batch_out = _ssh(batch_cmd)
    parts = batch_out.split('===SEC===')

    conf_out = parts[0] if len(parts) > 0 else ""
    addr_out = parts[1] if len(parts) > 1 else ""
    ping_out = parts[2] if len(parts) > 2 else ""
    fwd_out  = parts[3] if len(parts) > 3 else ""
    nat_out  = parts[4] if len(parts) > 4 else ""
    log_out  = parts[5].strip() if len(parts) > 5 else ""

    sta_ssid = ""
    m = re.search(r'ssid="([^"]+)"', conf_out)
    if m:
        sta_ssid = m.group(1).strip()

    ip_match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", addr_out)
    ctrl_home_ip = ip_match.group(1) if ip_match else ""

    internet_online = "PING_OK" in ping_out

    # Also test internet from Copter directly if Controller ping didn't confirm
    if not internet_online:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.2)
            s.connect(("8.8.8.8", 53))
            s.close()
            internet_online = True
        except Exception:
            pass

    ip_forward = fwd_out.strip() == "1"
    bidirectional = ip_forward and "MASQUERADE" in nat_out

    mode = "Access Point (SoloLink)"
    ssid_display = "SoloLink_AP"
    if ctrl_home_ip and internet_online:
        mode = "Home Wi-Fi (Connected)"
        ssid_display = sta_ssid or "Home Wi-Fi"
    elif sta_ssid:
        if "FAILED" in log_out or "Connection failed" in log_out:
            mode = "Connection Failed"
            ssid_display = "%s (Failed - Check Password)" % sta_ssid
        else:
            mode = "Connecting..."
            ssid_display = "%s (Connecting...)" % sta_ssid

    return {
        "connected_ssid": ssid_display,
        "mode": mode,
        "ip_address": ctrl_home_ip or CONTROLLER_IP,
        "internet_connected": internet_online,
        "bidirectional_enabled": bidirectional,
        "ip_forwarding": ip_forward,
        "controller_online": True,
        "setup_log": log_out
    }


def _parse_iwlist(output):
    """Parse iwlist wlan0 scan output into structured network objects."""
    networks = []
    seen = {}
    cells = output.split("Cell ")
    for block in cells[1:]:
        m_ssid = re.search(r'ESSID:\"([^\"]*)\"', block)
        if not m_ssid:
            continue
        ssid = m_ssid.group(1).strip()
        # Skip hidden/blank networks
        if not ssid or ssid.startswith("\\x00") or ssid == "\\x00" * len(ssid):
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

        if ssid in seen:
            if signal > seen[ssid]["signal"]:
                seen[ssid]["signal"] = signal
                seen[ssid]["quality"] = quality
        else:
            entry = {
                "ssid": ssid,
                "signal": signal,
                "quality": quality,
                "channel": channel,
                "security": sec
            }
            seen[ssid] = entry
            networks.append(entry)

    networks.sort(key=lambda x: x.get("signal", -100), reverse=True)
    return networks


def _parse_iw(output):
    """Parse 'iw dev wlan0 scan' output."""
    networks = []
    seen = {}
    blocks = output.split("BSS ")
    for block in blocks[1:]:
        m_ssid = re.search(r'SSID:\s*([^\r\n]+)', block)
        if not m_ssid:
            continue
        ssid = m_ssid.group(1).strip()
        if not ssid or ssid.startswith("\\x00"):
            continue

        m_sig = re.search(r'signal:\s*(-?\d+\.?\d*)', block)
        signal = int(float(m_sig.group(1))) if m_sig else -75

        m_freq = re.search(r'freq:\s*(\d+)', block)
        channel = 0
        if m_freq:
            freq = int(m_freq.group(1))
            if 2412 <= freq <= 2472:
                channel = (freq - 2407) // 5

        sec = "Open"
        if "RSN:" in block:
            sec = "WPA2"
        elif "WPA:" in block:
            sec = "WPA"
        elif "Privacy" in block:
            sec = "WEP"

        if ssid in seen:
            if signal > seen[ssid]["signal"]:
                seen[ssid]["signal"] = signal
        else:
            entry = {
                "ssid": ssid,
                "signal": signal,
                "quality": max(10, min(100, 2 * (signal + 100))),
                "channel": channel,
                "security": sec
            }
            seen[ssid] = entry
            networks.append(entry)

    networks.sort(key=lambda x: x.get("signal", -100), reverse=True)
    return networks


def _parse_netsh(output):
    """Parse Windows netsh wlan show networks output for development."""
    networks = []
    seen = {}
    cur_ssid = None
    cur_auth = "WPA2"
    for line in output.splitlines():
        line = line.strip()
        m_ssid = re.match(r'SSID\s+\d+\s*:\s*(.*)', line)
        if m_ssid:
            cur_ssid = m_ssid.group(1).strip()
            cur_auth = "WPA2"
            continue
        m_auth = re.match(r'Authentication\s*:\s*(.*)', line)
        if m_auth:
            auth_val = m_auth.group(1).strip()
            cur_auth = "Open" if "Open" in auth_val else "WPA2"
            continue
        m_sig = re.match(r'Signal\s*:\s*(\d+)%', line)
        if m_sig and cur_ssid:
            qual = int(m_sig.group(1))
            sig_dbm = int((qual / 2.0) - 100)
            if cur_ssid not in seen:
                entry = {
                    "ssid": cur_ssid,
                    "signal": sig_dbm,
                    "quality": qual,
                    "channel": 6,
                    "security": cur_auth
                }
                seen[cur_ssid] = entry
                networks.append(entry)
            cur_ssid = None

    networks.sort(key=lambda x: x.get("signal", -100), reverse=True)
    return networks


def _parse_wpa_cli(output):
    """Parse 'wpa_cli scan_results' tab-separated table."""
    networks = []
    seen = {}
    lines = output.strip().splitlines()
    for line in lines:
        parts = line.split('\t')
        if len(parts) >= 5:
            freq_str = parts[1].strip()
            sig_str = parts[2].strip()
            flags = parts[3].strip()
            ssid = parts[4].strip()

            if not ssid or ssid.startswith('\\x00'):
                continue

            try:
                freq = int(freq_str)
                channel = 14 if freq == 2484 else (freq - 2407) // 5 if 2412 <= freq <= 2484 else 0
            except Exception:
                channel = 0

            try:
                signal = int(sig_str)
            except Exception:
                signal = -70

            sec = "Open"
            if "WPA2" in flags:
                sec = "WPA2"
            elif "WPA" in flags:
                sec = "WPA"
            elif "WEP" in flags:
                sec = "WEP"

            quality = max(10, min(100, 2 * (signal + 100)))

            if ssid in seen:
                if signal > seen[ssid]["signal"]:
                    seen[ssid]["signal"] = signal
                    seen[ssid]["quality"] = quality
            else:
                entry = {
                    "ssid": ssid,
                    "signal": signal,
                    "quality": quality,
                    "channel": channel,
                    "security": sec
                }
                seen[ssid] = entry
                networks.append(entry)

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

        quality = max(10, min(100, 2 * (signal + 100)))

        if ssid in seen:
            if signal > seen[ssid]["signal"]:
                seen[ssid]["signal"] = signal
                seen[ssid]["quality"] = quality
        else:
            entry = {
                "ssid": ssid,
                "signal": signal,
                "quality": quality,
                "channel": channel,
                "security": sec
            }
            seen[ssid] = entry
            networks.append(entry)

    networks.sort(key=lambda x: x.get("signal", -100), reverse=True)
    return networks


def _parse_scan_output(text):
    """Auto-detect format and parse wireless scan output."""
    if not text or not text.strip():
        return []
    if "bssid / frequency" in text.lower() or "\t" in text:
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
    if "BSS " in text:
        nets = _parse_iw(text)
        if nets:
            return nets
    if "SSID " in text and "Signal" in text:
        nets = _parse_netsh(text)
        if nets:
            return nets
    return []


def scan_wifi_networks():
    """
    Scan for visible 2.4 GHz Wi-Fi networks in range.
    Queries both Controller and Copter radios using Broadcom wl, wpa_cli, and iwlist.
    """
    seen = {}
    networks = []

    def _merge(new_nets):
        for n in new_nets:
            ssid = n.get("ssid")
            if not ssid:
                continue
            if ssid not in seen:
                seen[ssid] = n
                networks.append(n)
            else:
                if n.get("signal", -100) > seen[ssid].get("signal", -100):
                    seen[ssid]["signal"] = n["signal"]
                    seen[ssid]["quality"] = n.get("quality", seen[ssid].get("quality", 50))

    # 1. Try Controller via SSH (Broadcom wl hardware sweep first, then wpa_cli, then iwlist)
    if _ctrl_reachable():
        ctrl_scan_cmd = (
            "wl scan 2>/dev/null; "
            "for i in 1 2 3 4; do "
            "  sleep 0.8; "
            "  res=$(wl scanresults 2>/dev/null); "
            "  if [ -n \"$res\" ]; then echo \"$res\"; exit 0; fi; "
            "done; "
            "wpa_cli -i wlan0 scan >/dev/null 2>&1; "
            "for i in 1 2 3; do "
            "  sleep 1; "
            "  res=$(wpa_cli -i wlan0 scan_results 2>/dev/null); "
            "  if [ $(echo \"$res\" | wc -l) -gt 1 ]; then echo \"$res\"; exit 0; fi; "
            "done; "
            "iwlist wlan0 scan 2>/dev/null || iw dev wlan0 scan 2>/dev/null || true"
        )
        _, scan_out = _ssh(ctrl_scan_cmd)
        _merge(_parse_scan_output(scan_out))

    # 2. Try Copter Linux locally (wpa_cli client interface scan, then wl, then iwlist)
    if sys.platform.startswith("linux"):
        try:
            copter_scan_cmd = (
                "wpa_cli scan >/dev/null 2>&1 || wpa_cli -i wlan0 scan >/dev/null 2>&1; "
                "for i in 1 2 3; do "
                "  sleep 1; "
                "  res=$(wpa_cli scan_results 2>/dev/null || wpa_cli -i wlan0 scan_results 2>/dev/null); "
                "  if [ $(echo \"$res\" | wc -l) -gt 1 ]; then echo \"$res\"; exit 0; fi; "
                "done; "
                "wl scan 2>/dev/null; "
                "for i in 1 2 3; do "
                "  sleep 0.8; "
                "  res=$(wl scanresults 2>/dev/null); "
                "  if [ -n \"$res\" ]; then echo \"$res\"; exit 0; fi; "
                "done; "
                "iwlist wlan0 scan 2>/dev/null || true"
            )
            p = subprocess.Popen(
                copter_scan_cmd, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            out, _ = p.communicate()
            if sys.version_info[0] >= 3:
                out = out.decode("utf-8", "ignore")
            _merge(_parse_scan_output(out))
        except Exception:
            pass

    # 3. Development / Windows fallback
    if not networks and sys.platform == "win32":
        try:
            p = subprocess.Popen(
                "netsh wlan show networks mode=bssid",
                shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            out, _ = p.communicate()
            if sys.version_info[0] >= 3:
                out = out.decode("utf-8", "ignore")
            _merge(_parse_netsh(out))
        except Exception:
            pass

    networks.sort(key=lambda x: x.get("signal", -100), reverse=True)

    return {
        "success": True,
        "count": len(networks),
        "networks": networks,
        "message": ("Found %d visible 2.4 GHz network(s)." % len(networks)) if networks else "No 2.4 GHz broadcast Wi-Fi networks found."
    }


def disconnect_on_armed():
    """
    Safety interlock: Immediately disconnect controller from home Wi-Fi
    when the vehicle is armed. Restores default SoloLink AP mode.
    """
    if not _ctrl_reachable():
        return
    try:
        cmds = (
            "killall wpa_supplicant 2>/dev/null || true; "
            "killall udhcpc 2>/dev/null || true; "
            "rm -f /tmp/wpa_client.conf /etc/wpa_client.conf /usr/bin/connect-home-wifi; "
            "iptables -t nat -F 2>/dev/null || true; "
            "iptables -F FORWARD 2>/dev/null || true; "
            "echo 0 > /proc/sys/net/ipv4/ip_forward 2>/dev/null || true"
        )
        _ssh(cmds)
    except Exception:
        pass


def connect_home_wifi(ssid, password):
    """
    Connect Controller to Home Wi-Fi using the exact proven v1.0.58 engine.
    Volatile mode (RAM only, non-persistent). Disconnects if vehicle is armed.
    """
    if not ssid:
        return False, "SSID is required."

    try:
        import cockpit_mgr
        if cockpit_mgr.is_vehicle_armed():
            return False, "SAFETY INTERLOCK: Cannot connect to home Wi-Fi while vehicle is ARMED!"
    except Exception:
        pass

    if not _ctrl_reachable():
        return False, "Controller (10.1.1.1) is not reachable over SSH."

    clean_ssid = ssid.strip()
    clean_pwd = password.strip() if password else ""
    psk_block = ('psk="%s"' % clean_pwd.replace('"', '\\"')) if clean_pwd else 'key_mgmt=NONE'

    deploy_cmd = (
        "# Clean up any legacy boot persistence so connection remains strictly volatile\n"
        "rm -f /etc/init.d/solo-home-wifi /etc/rc5.d/S99solo-home-wifi /etc/rc3.d/S99solo-home-wifi /etc/wpa_client.conf\n"
        "if [ -f /etc/rc.local ]; then sed -i '/connect-home-wifi/d' /etc/rc.local 2>/dev/null || true; fi\n"
        "\n"
        "# Deploy volatile config into RAM (/tmp) - erased on reboot\n"
        "cat << 'NET' > /tmp/wpa_client.conf\n"
        "ctrl_interface=/var/run/wpa_supplicant\n"
        "update_config=1\n"
        "\n"
        "network={\n"
        '    ssid="%s"\n'
        "    %s\n"
        "}\n"
        "NET\n"
        "chmod 600 /tmp/wpa_client.conf\n"
        "\n"
        "cat << 'SCRIPT' > /usr/bin/connect-home-wifi\n"
        "#!/bin/sh\n"
        "# Volatile connection script - will not auto-run on reboot\n"
        "if [ ! -f /tmp/wpa_client.conf ]; then exit 0; fi\n"
        "killall wpa_supplicant 2>/dev/null || true\n"
        "killall udhcpc 2>/dev/null || true\n"
        "for i in 1 2 3 4 5 6 7 8 9 10; do\n"
        "    if ip link show wlan0 >/dev/null 2>&1; then break; fi\n"
        "    sleep 1\n"
        "done\n"
        "/etc/init.d/hostapd stop 2>/dev/null || true\n"
        "sleep 1\n"
        "if [ -f /etc/dnsmasq.conf ]; then\n"
        "    sed -i.bak 's/dhcp-option=3.*/dhcp-option=3,10.1.1.1/g' /etc/dnsmasq.conf 2>/dev/null || true\n"
        "    sed -i.bak 's/dhcp-option=6.*/dhcp-option=6,8.8.8.8,1.1.1.1/g' /etc/dnsmasq.conf 2>/dev/null || true\n"
        "    /etc/init.d/dnsmasq restart 2>/dev/null || true\n"
        "fi\n"
        "echo 1 > /proc/sys/net/ipv4/ip_forward\n"
        "wpa_supplicant -i wlan0 -c /tmp/wpa_client.conf -B\n"
        "sleep 3\n"
        "for attempt in 1 2 3 4 5 6 7 8 9 10; do\n"
        "    if wpa_cli -i wlan0 status 2>/dev/null | grep -q 'wpa_state=COMPLETED'; then\n"
        "        break\n"
        "    fi\n"
        "    sleep 1\n"
        "done\n"
        "DHCP_OK=0\n"
        "if udhcpc -i wlan0 -n -t 15 -T 3 -q; then\n"
        "    DHCP_OK=1\n"
        "fi\n"
        "/etc/init.d/hostapd start 2>/dev/null || true\n"
        'if [ "$DHCP_OK" -eq 1 ]; then\n'
        "    insmod /lib/modules/3.10.17-rt12-*/kernel/net/ipv4/netfilter/iptable_filter.ko 2>/dev/null || true\n"
        "    insmod /lib/modules/3.10.17-rt12-*/kernel/net/ipv4/netfilter/iptable_nat.ko 2>/dev/null || true\n"
        "    insmod /lib/modules/3.10.17-rt12-*/kernel/net/ipv4/netfilter/nf_nat.ko 2>/dev/null || true\n"
        "    iptables -t nat -D POSTROUTING -o wlan0 -j MASQUERADE 2>/dev/null || true\n"
        "    iptables -t nat -D POSTROUTING -o wlan0-ap -j MASQUERADE 2>/dev/null || true\n"
        "    iptables -D FORWARD -i wlan0 -o wlan0-ap -j ACCEPT 2>/dev/null || true\n"
        "    iptables -D FORWARD -i wlan0-ap -o wlan0 -j ACCEPT 2>/dev/null || true\n"
        "    iptables -t nat -A POSTROUTING -o wlan0 -j MASQUERADE 2>/dev/null || true\n"
        "    iptables -t nat -A POSTROUTING -o wlan0-ap -j MASQUERADE 2>/dev/null || true\n"
        "    iptables -A FORWARD -i wlan0 -o wlan0-ap -j ACCEPT 2>/dev/null || true\n"
        "    iptables -A FORWARD -i wlan0-ap -o wlan0 -j ACCEPT 2>/dev/null || true\n"
        "fi\n"
        "SCRIPT\n"
        "chmod 755 /usr/bin/connect-home-wifi\n"
        "nohup /usr/bin/connect-home-wifi >/tmp/wifi_setup.log 2>&1 &\n"
    ) % (clean_ssid, psk_block)

    rc, out = _ssh_paramiko(deploy_cmd, timeout=60)
    if rc != 0:
        rc, out = _ssh(deploy_cmd)
        if rc != 0:
            return False, "Failed to deploy Wi-Fi configuration script to Controller: %s" % out

    # Ensure Copter routes internet traffic to Controller 10.1.1.1 and has DNS
    ensure_copter_internet_route()

    return True, "Connecting Controller to '%s'... Associating and requesting DHCP lease (takes ~10s)." % clean_ssid


def set_bidirectional(enable=True):
    """
    Enable or disable bi-directional routing (IP forwarding + NAT masquerade)
    on the Controller for wlan0 home interface.
    """
    if not _ctrl_reachable():
        return False, "Controller not reachable."

    if enable:
        cmds = [
            "echo 1 > /proc/sys/net/ipv4/ip_forward",
            "insmod /lib/modules/3.10.17-rt12-*/kernel/net/ipv4/netfilter/iptable_filter.ko 2>/dev/null || true",
            "iptables -t nat -D POSTROUTING -o wlan0 -j MASQUERADE 2>/dev/null || true",
            "iptables -t nat -D POSTROUTING -o wlan0-ap -j MASQUERADE 2>/dev/null || true",
            "iptables -D FORWARD -i wlan0 -o wlan0-ap -j ACCEPT 2>/dev/null || true",
            "iptables -D FORWARD -i wlan0-ap -o wlan0 -j ACCEPT 2>/dev/null || true",
            "iptables -t nat -A POSTROUTING -o wlan0 -j MASQUERADE 2>/dev/null || true",
            "iptables -t nat -A POSTROUTING -o wlan0-ap -j MASQUERADE 2>/dev/null || true",
            "iptables -A FORWARD -i wlan0 -o wlan0-ap -j ACCEPT 2>/dev/null || true",
            "iptables -A FORWARD -i wlan0-ap -o wlan0 -j ACCEPT 2>/dev/null || true",
            "grep -q 'ip_forward=1' /etc/sysctl.conf 2>/dev/null || echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf"
        ]
        msg = "Bi-directional routing enabled. Home LAN can now reach Solo subnet (10.1.1.0/24)."
    else:
        cmds = [
            "echo 0 > /proc/sys/net/ipv4/ip_forward",
            "iptables -t nat -F 2>/dev/null || true",
            "iptables -F FORWARD 2>/dev/null || true",
            "sed -i '/ip_forward=1/d' /etc/sysctl.conf 2>/dev/null || true"
        ]
        msg = "Bi-directional routing disabled. Firewall restored."

    for cmd in cmds:
        _ssh(cmd)
    return True, msg


def reset_to_default_ap():
    """
    Restore Controller Wi-Fi to factory SoloLink Access Point mode.
    Removes home Wi-Fi config and restarts hostapd AP.
    """
    if not _ctrl_reachable():
        return True, "Controller not currently reachable — it may already be in AP mode."

    cmds = [
        "killall wpa_supplicant 2>/dev/null || true",
        "killall udhcpc 2>/dev/null || true",
        "rm -f /tmp/wpa_client.conf /etc/wpa_client.conf /usr/bin/connect-home-wifi",
        "rm -f /etc/init.d/solo-home-wifi /etc/rc5.d/S99solo-home-wifi /etc/rc3.d/S99solo-home-wifi",
        "sed -i '/connect-home-wifi/d' /etc/rc.local 2>/dev/null || true",
        "iptables -t nat -F 2>/dev/null || true",
        "iptables -F FORWARD 2>/dev/null || true",
        "/etc/init.d/hostapd restart 2>/dev/null || true",
        "/etc/init.d/dnsmasq restart 2>/dev/null || true"
    ]
    for cmd in cmds:
        _ssh(cmd)

    return True, "Controller Wi-Fi reset to default SoloLink Access Point mode (10.1.1.1)."
