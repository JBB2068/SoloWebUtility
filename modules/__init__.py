"""
Solo Web Utility Modules
Modern modular architecture for 3DR Solo & Open Solo.
"""

from modules.auth import auth_bp
from modules.version import version_bp
from modules.parameters import parameters_bp
from modules.controller import controller_bp
from modules.firmware import firmware_bp
from modules.video import video_bp
from modules.telemetry import telemetry_bp
from modules.wifi import wifi_bp
from modules.pairing import pairing_bp
from modules.calibration import calibration_bp
from modules.legacy_server import start_legacy_server, get_legacy_server

__all__ = [
    "auth_bp",
    "version_bp",
    "parameters_bp",
    "controller_bp",
    "firmware_bp",
    "video_bp",
    "telemetry_bp",
    "wifi_bp",
    "pairing_bp",
    "calibration_bp",
    "start_legacy_server",
    "get_legacy_server",
]
