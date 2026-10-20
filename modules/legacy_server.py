#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Legacy SoloLink TCP App Server Module
Runs the legacy 3DR Solo TCP socket loop in an isolated background thread.
Compatible with Python 2.7 & Python 3.x on both Linux and Windows.
"""

import os
import sys
import time
import socket
import struct
import logging
import logging.config
import threading

# Python 2 & 3 configparser compatibility
try:
    import configparser
except ImportError:
    import ConfigParser as configparser

# Setup safe logger
logger = logging.getLogger("sololink_app_server")

# Fallback for /usr/bin/app_connected_msg on Solo copter
try:
    if os.path.isdir("/usr/bin") and "/usr/bin" not in sys.path:
        sys.path.append("/usr/bin")
    import app_connected_msg
except ImportError:
    class MockAppConnectedMsg(object):
        @staticmethod
        def send_connected():
            logger.info("[Mock] app_connected_msg: send_connected()")

        @staticmethod
        def send_disconnected():
            logger.info("[Mock] app_connected_msg: send_disconnected()")

    app_connected_msg = MockAppConnectedMsg()

DEFAULT_SOLO_CONF = "/etc/sololink.conf"
DEFAULT_APP_SERVER_PORT = 5501  # Default to 5501 so it never conflicts with Flask web UI on 5000
DEFAULT_APP_ADDRESS_FILE = "/tmp/app_address.txt" if os.path.exists("/tmp") else os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_address.txt")


def setup_sololink_logging(conf_path=DEFAULT_SOLO_CONF):
    """Safely configure logging using fileConfig if valid, or basicConfig fallback."""
    if os.path.exists(conf_path):
        try:
            # Check if file has [loggers] section required by fileConfig
            cfg = configparser.ConfigParser()
            cfg.read(conf_path)
            if cfg.has_section("loggers") and cfg.has_section("handlers") and cfg.has_section("formatters"):
                logging.config.fileConfig(conf_path, disable_existing_loggers=False)
                return
        except Exception as e:
            logger.debug("Could not use fileConfig on %s: %s", conf_path, e)

    # Standard fallback logging
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )


def load_app_server_config(conf_path=DEFAULT_SOLO_CONF, flask_port=5000):
    """Load configuration items safely with robust defaults."""
    app_server_port = DEFAULT_APP_SERVER_PORT
    app_address_file = DEFAULT_APP_ADDRESS_FILE

    if os.path.exists(conf_path):
        config = configparser.ConfigParser()
        try:
            config.read(conf_path)
            if config.has_section("solo"):
                if config.has_option("solo", "appServerPort"):
                    cfg_port = config.getint("solo", "appServerPort")
                    # Avoid port collision with Flask web UI on port 5000
                    if cfg_port == flask_port:
                        logger.warning(
                            "appServerPort in %s is %d, which conflicts with Flask web UI on port %d. Using port %d instead.",
                            conf_path, cfg_port, flask_port, DEFAULT_APP_SERVER_PORT
                        )
                        app_server_port = DEFAULT_APP_SERVER_PORT
                    else:
                        app_server_port = cfg_port

                if config.has_option("solo", "appAddressFile"):
                    app_address_file = config.get("solo", "appAddressFile")
        except Exception as ex:
            logger.warning("Error reading config from %s: %s. Using default settings.", conf_path, ex)

    return app_server_port, app_address_file


def get_request(sock):
    """
    Get one request from socket.
    A request is a 32-bit big-endian byte count followed by opaque data.
    Works transparently with Python 2 (str) and Python 3 (bytes).
    """
    pkt = b""
    while len(pkt) < 4:
        try:
            b = sock.recv(1)
        except socket.error as se:
            logger.info("socket.error: %s", str(se))
            return None
        except socket.timeout as st:
            logger.info("socket.timeout: %s", str(st))
            return None
        if not b:
            return None
        pkt += b

    (pkt_len,) = struct.unpack("!I", pkt)
    logger.debug("packet length %d", pkt_len)

    # Minimum valid packet length is 4 bytes
    if pkt_len < 4 or pkt_len > 65536:
        logger.warning("Invalid packet length: %d", pkt_len)
        return None

    while len(pkt) < pkt_len:
        try:
            b = sock.recv(1)
        except socket.error as se:
            logger.info("socket.error: %s", str(se))
            return None
        except socket.timeout as st:
            logger.info("socket.timeout: %s", str(st))
            return None
        if not b:
            return None
        pkt += b

    return pkt


class LegacyTcpServerThread(threading.Thread):
    """
    Background thread running the legacy SoloLink TCP socket server.
    Ensures that the accept() loop does not block Flask or freeze port 5000.
    """

    def __init__(self, port=None, address_file=None, conf_path=DEFAULT_SOLO_CONF, flask_port=5000):
        super(LegacyTcpServerThread, self).__init__()
        self.daemon = True
        self.name = "SoloLink-Legacy-TCP-Server"
        self.conf_path = conf_path
        self.flask_port = flask_port

        cfg_port, cfg_file = load_app_server_config(conf_path, flask_port)
        self.port = port if port is not None else cfg_port
        self.address_file = address_file if address_file is not None else cfg_file

        self.running = False
        self.listen_sock = None
        self.connected_client_ip = None
        self.packet_count = 0
        self.last_active_time = 0.0

    def set_app_ip(self, app_ip):
        try:
            dir_name = os.path.dirname(self.address_file)
            if dir_name and not os.path.exists(dir_name):
                os.makedirs(dir_name)
            with open(self.address_file, "w") as f:
                f.write(app_ip + "\n")
        except Exception as e:
            logger.warning("Failed to write app IP to %s: %s", self.address_file, e)

    def unset_app_ip(self):
        try:
            if os.path.exists(self.address_file):
                os.unlink(self.address_file)
        except Exception as e:
            logger.debug("Failed to remove %s: %s", self.address_file, e)

    def get_status(self):
        """Return diagnostic status dictionary."""
        return {
            "running": self.running,
            "port": self.port,
            "address_file": self.address_file,
            "connected_client_ip": self.connected_client_ip,
            "packet_count": self.packet_count,
            "last_active": self.last_active_time
        }

    def stop(self):
        """Stop the TCP server cleanly."""
        self.running = False
        if self.listen_sock:
            try:
                self.listen_sock.close()
            except Exception:
                pass

    def run(self):
        setup_sololink_logging(self.conf_path)
        logger.info("Starting legacy TCP socket loop on port %d...", self.port)

        try:
            self.listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            # Platform-safe TCP keepalive options
            try:
                self.listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except Exception:
                pass

            if hasattr(socket, "TCP_KEEPIDLE"):
                try:
                    self.listen_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 1)
                except Exception:
                    pass

            if hasattr(socket, "TCP_KEEPINTVL"):
                try:
                    self.listen_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
                except Exception:
                    pass

            if hasattr(socket, "TCP_KEEPCNT"):
                try:
                    self.listen_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)
                except Exception:
                    pass

            self.listen_sock.settimeout(2.0)
            self.listen_sock.bind(("", self.port))
            self.listen_sock.listen(1)
            self.running = True
            logger.info("Legacy TCP server listening on port %d (Background Thread)", self.port)

        except Exception as e:
            logger.error("Failed to bind legacy TCP server on port %d: %s", self.port, e)
            self.running = False
            return

        while self.running:
            try:
                sock, address = self.listen_sock.accept()
            except socket.timeout:
                continue
            except (socket.error, OSError):
                if not self.running:
                    break
                time.sleep(0.1)
                continue

            self.connected_client_ip = address[0]
            logger.info("SoloLink app connection from %s", str(address))

            self.set_app_ip(address[0])
            logger.info("App IP recorded as %s", address[0])

            try:
                app_connected_msg.send_connected()
            except Exception as e:
                logger.warning("Error calling send_connected: %s", e)

            # Process client packets
            sock.settimeout(5.0)
            while self.running:
                pkt = get_request(sock)
                if not pkt:
                    # Remote end closed connection
                    break
                self.packet_count += 1
                self.last_active_time = time.time()
                hex_repr = [hex(b) if isinstance(b, int) else hex(ord(b)) for b in pkt]
                logger.debug("Received request (%d bytes): %s", len(pkt), str(hex_repr))

            try:
                app_connected_msg.send_disconnected()
            except Exception as e:
                logger.warning("Error calling send_disconnected: %s", e)

            self.unset_app_ip()
            self.connected_client_ip = None
            logger.info("Closing connection with %s", str(address))

            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass

        if self.listen_sock:
            try:
                self.listen_sock.close()
            except Exception:
                pass
        self.running = False
        logger.info("Legacy TCP server stopped.")


# Singleton instance
_legacy_server_instance = None
_legacy_server_lock = threading.Lock()


def get_legacy_server():
    """Return the global legacy TCP server thread instance."""
    global _legacy_server_instance
    with _legacy_server_lock:
        if _legacy_server_instance is None:
            _legacy_server_instance = LegacyTcpServerThread()
        return _legacy_server_instance


def start_legacy_server(port=None, flask_port=5000):
    """Start legacy TCP socket server thread if not already running."""
    global _legacy_server_instance
    with _legacy_server_lock:
        if _legacy_server_instance is None:
            _legacy_server_instance = LegacyTcpServerThread(port=port, flask_port=flask_port)
        if not _legacy_server_instance.is_alive():
            _legacy_server_instance.start()
        return _legacy_server_instance
