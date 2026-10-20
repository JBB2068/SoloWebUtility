#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Video Pipeline Module
Manages the 3DR Solo HDMI live video pipeline:
- TCP keepalive handshake with Solo Controller on port 5502
- UDP RTP receiver on port 5600
- RFC 6184 H.264 RTP depacketizer to Annex-B NAL units
- Low-latency HTTP H.264 chunk streaming to browser JMuxer player
"""

import os
import sys
import time
import socket
import select
import struct
import threading
from webcore import Blueprint, jsonify, Response, stream_with_context

try:
    import queue
except ImportError:
    import Queue as queue

SOLO_CONTROLLER_IP = "10.1.1.1"
SOLO_VIDEO_TCP_PORT = 5502
SOLO_VIDEO_UDP_PORT = 5600


def parse_rtp_h264(data, fu_buffer):
    """RFC 6184 H.264 RTP depacketizer for raw Annex-B NAL units."""
    if len(data) < 12:
        return [], fu_buffer
    payload = data[12:]
    if not payload:
        return [], fu_buffer

    nals = []
    nal_header = ord(payload[0:1]) if sys.version_info[0] < 3 else payload[0]
    nal_type = nal_header & 0x1F

    if 1 <= nal_type <= 23:
        nals.append(b'\x00\x00\x00\x01' + payload)
        fu_buffer = b''
    elif nal_type == 24:
        idx = 1
        while idx + 2 <= len(payload):
            nsize = struct.unpack('>H', payload[idx:idx+2])[0]
            idx += 2
            if idx + nsize <= len(payload):
                nals.append(b'\x00\x00\x00\x01' + payload[idx:idx+nsize])
            idx += nsize
        fu_buffer = b''
    elif nal_type == 28:
        if len(payload) < 2:
            return [], fu_buffer
        fu_header = ord(payload[1:2]) if sys.version_info[0] < 3 else payload[1]
        start_bit = bool(fu_header & 0x80)
        end_bit = bool(fu_header & 0x40)
        orig_type = fu_header & 0x1F
        reconstructed_nal_byte = struct.pack('B', (nal_header & 0xE0) | orig_type)

        if start_bit:
            fu_buffer = b'\x00\x00\x00\x01' + reconstructed_nal_byte + payload[2:]
        else:
            if fu_buffer:
                fu_buffer += payload[2:]

        if end_bit and fu_buffer:
            nals.append(fu_buffer)
            fu_buffer = b''

    return nals, fu_buffer


class SoloVideoManager(threading.Thread):
    """
    Manages the 3DR Solo HDMI live video pipeline.
    1. Keeps a TCP keepalive to 10.1.1.1:5502 to register client IP for video.
    2. Binds UDP port 5600 for incoming RTP H.264 video packets.
    3. Depacketizes RTP packets into Annex-B H.264 NAL units (SPS, PPS, IDR, slices).
    4. Caches SPS/PPS parameter sets and fans out live H.264 stream to browser clients.
    """
    def __init__(self, controller_ip=SOLO_CONTROLLER_IP, tcp_port=SOLO_VIDEO_TCP_PORT, udp_port=SOLO_VIDEO_UDP_PORT):
        super(SoloVideoManager, self).__init__()
        self.daemon = True
        self.controller_ip = controller_ip
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.running = False

        self.lock = threading.Lock()
        self.subscribers = []
        self.sps = None
        self.pps = None
        self.force_reconnect_flag = False

        # Diagnostics
        self.handshake_connected = False
        self.streaming_active = False
        self.packet_count = 0
        self.bytes_received = 0
        self.last_packet_time = 0.0
        self.kbps = 0.0
        self._rate_calc_time = time.time()
        self._rate_calc_bytes = 0

    def subscribe(self):
        """Register a new HTTP/browser consumer for live H.264 NAL units."""
        q = queue.Queue(maxsize=80)
        q.needs_idr = True
        with self.lock:
            if self.sps:
                q.put(self.sps)
            if self.pps:
                q.put(self.pps)
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        """Unregister an HTTP/browser consumer."""
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def broadcast_nal(self, nal_data):
        """Distribute a completed NAL unit to all active browser subscriber queues."""
        nal_header = ord(nal_data[4:5]) if sys.version_info[0] < 3 else nal_data[4]
        nal_type = nal_header & 0x1F

        with self.lock:
            if nal_type == 7:
                self.sps = nal_data
            elif nal_type == 8:
                self.pps = nal_data

            for q in list(self.subscribers):
                try:
                    data_to_send = nal_data
                    if getattr(q, 'needs_idr', False) and (nal_type == 1 or nal_type == 5):
                        if sys.version_info[0] < 3:
                            spoofed_header = chr((nal_header & 0xE0) | 5)
                            data_to_send = nal_data[:4] + spoofed_header + nal_data[5:]
                        else:
                            spoofed_header = bytes([(nal_header & 0xE0) | 5])
                            data_to_send = nal_data[:4] + spoofed_header + nal_data[5:]
                        q.needs_idr = False

                    if q.full():
                        try:
                            q.get_nowait()
                        except queue.Empty:
                            pass
                    q.put_nowait(data_to_send)
                except Exception:
                    pass

    def get_status(self):
        """Return video diagnostics dictionary."""
        now = time.time()
        is_active = (now - self.last_packet_time < 2.5) if self.last_packet_time > 0 else False
        return {
            "handshake_connected": self.handshake_connected,
            "streaming_active": is_active,
            "packet_count": self.packet_count,
            "kbps": round(self.kbps, 1),
            "subscribers": len(self.subscribers),
            "last_packet_sec_ago": round(now - self.last_packet_time, 1) if self.last_packet_time > 0 else None
        }

    def _handshake_worker(self):
        """Background worker keeping the TCP handshake alive to 10.1.1.1:5502."""
        while self.running:
            sock = None
            if len(self.subscribers) == 0:
                self.handshake_connected = False
                time.sleep(1.0)
                continue

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(4.0)
                sock.connect((self.controller_ip, self.tcp_port))
                self.handshake_connected = True
                last_ping = 0.0

                while self.running:
                    if len(self.subscribers) == 0:
                        break

                    now = time.time()
                    if now - last_ping >= 2.0:
                        try:
                            # 4-byte length prefix (serves as heartbeat to app_server.py)
                            sock.sendall(struct.pack('!I', 4))
                            last_ping = now
                        except Exception:
                            break

                    r, _, _ = select.select([sock], [], [], 1.0)
                    if self.force_reconnect_flag:
                        self.force_reconnect_flag = False
                        break

                    if r:
                        try:
                            data = sock.recv(1024)
                            if not data:
                                break
                        except Exception:
                            break
            except Exception:
                self.handshake_connected = False
                time.sleep(1.0)
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass

    def run(self):
        self.running = True

        hs_thread = threading.Thread(target=self._handshake_worker)
        hs_thread.daemon = True
        hs_thread.start()

        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except Exception:
            pass
        try:
            udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1048576)
        except Exception:
            pass
        if hasattr(socket, 'SIO_UDP_CONNRESET'):
            try:
                udp_sock.ioctl(socket.SIO_UDP_CONNRESET, False)
            except Exception:
                pass

        try:
            udp_sock.bind(('0.0.0.0', self.udp_port))
            udp_sock.settimeout(1.0)
        except Exception as e:
            print("[!] Could not bind UDP %d for video: %s" % (self.udp_port, e))
            return

        fu_buffer = b''

        while self.running:
            try:
                data, addr = udp_sock.recvfrom(65536)
            except socket.timeout:
                continue
            except (socket.error, OSError):
                time.sleep(0.01)
                continue
            except Exception:
                time.sleep(0.01)
                continue

            now = time.time()
            self.packet_count += 1
            self.bytes_received += len(data)
            self.last_packet_time = now
            self._rate_calc_bytes += len(data)

            if now - self._rate_calc_time >= 1.0:
                elapsed = now - self._rate_calc_time
                self.kbps = (self._rate_calc_bytes * 8.0) / (elapsed * 1024.0)
                self._rate_calc_bytes = 0
                self._rate_calc_time = now

            nals, fu_buffer = parse_rtp_h264(data, fu_buffer)
            for nal in nals:
                self.broadcast_nal(nal)

        try:
            udp_sock.close()
        except Exception:
            pass


# Global singleton
video_manager = SoloVideoManager()


def start_video_service():
    """Start the video manager thread if not already running."""
    if not video_manager.is_alive():
        video_manager.start()


# ---------------------------------------------------------------------------
# Flask Blueprint
# ---------------------------------------------------------------------------
video_bp = Blueprint("video", __name__)


@video_bp.route("/api/video/status", methods=["GET"])
def api_video_status():
    return jsonify(video_manager.get_status())


@video_bp.route("/api/video/restart", methods=["POST"])
def api_video_restart():
    video_manager.force_reconnect_flag = True
    return jsonify({"success": True, "message": "Video stream reconnecting..."})


@video_bp.route("/api/video/h264", methods=["GET"])
def api_video_h264():
    def generate():
        q = video_manager.subscribe()
        try:
            while True:
                try:
                    chunk = q.get(timeout=2.0)
                    yield chunk
                except queue.Empty:
                    continue
        except GeneratorExit:
            pass
        finally:
            video_manager.unsubscribe(q)

    resp = Response(stream_with_context(generate()), mimetype='video/mp4; codecs="avc1.42E01E"')
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Connection"] = "close"
    return resp
