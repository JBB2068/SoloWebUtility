#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Compatibility alias -- there is exactly ONE calibration manager: ../calib_mgr.py.

This file used to be a full byte-for-byte copy of calib_mgr.py that nothing
imported. It is now a re-export so the two can never drift out of sync.
"""
from __future__ import absolute_import

import os
import sys

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from calib_mgr import *  # noqa: F401,F403
from calib_mgr import calib_engine, CalibrationManager  # noqa: F401
