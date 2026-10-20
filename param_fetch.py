#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
param_fetch.py - Backward-compatibility proxy for param_mgr.py.
Re-exports all MAVFTP and parameter fetching constants and functions from param_mgr.
"""

from param_mgr import (
    FTP_PARAM_PATH,
    OP_TERMINATE_SESSION,
    OP_RESET_SESSIONS,
    OP_OPEN_FILE_RO,
    OP_READ_FILE,
    OP_ACK,
    OP_NAK,
    NAK_EOF,
    FTP_HDR_FMT,
    FTP_HDR_LEN,
    FTP_PAYLOAD_LEN,
    FTP_MAX_DATA,
    PCK_MAGIC,
    PCK_MAGIC_DEFAULTS,
    PCK_TYPES,
    FtpError,
    decode_param_pck,
    MavFtp,
    fetch_via_mavftp,
    fetch_via_param_messages,
    fetch_all_params,
    ParamFetchProgress,
    fetch_progress,
)

__all__ = [
    "FTP_PARAM_PATH",
    "OP_TERMINATE_SESSION",
    "OP_RESET_SESSIONS",
    "OP_OPEN_FILE_RO",
    "OP_READ_FILE",
    "OP_ACK",
    "OP_NAK",
    "NAK_EOF",
    "FTP_HDR_FMT",
    "FTP_HDR_LEN",
    "FTP_PAYLOAD_LEN",
    "FTP_MAX_DATA",
    "PCK_MAGIC",
    "PCK_MAGIC_DEFAULTS",
    "PCK_TYPES",
    "FtpError",
    "decode_param_pck",
    "MavFtp",
    "fetch_via_mavftp",
    "fetch_via_param_messages",
    "fetch_all_params",
    "ParamFetchProgress",
    "fetch_progress",
]
