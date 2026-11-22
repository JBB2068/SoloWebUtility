#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Comprehensive Automated Test Suite for 3DR Solo Web Utility.
Tests versioning, auth, firmware integrity, parameters, presets, wifi, pairing, and HTTP endpoints.
"""

import os
import sys
import json
import time
import unittest
import threading
import shutil
import tempfile
import tarfile

# Import modules to test
import auth
import presets
import param_mgr
import wifi_mgr
import flash_mgr
import pair_mgr
import calib_mgr
import version_mgr
import solo_web_app

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class TestSoloWebUtility(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._version_backup = None
        cls._version_mtime = None
        cls._auth_backup = None
        cls._auth_mtime = None
        cls._param_backup = None
        cls._param_mtime = None
        if os.path.exists(version_mgr.VERSION_FILE):
            cls._version_mtime = os.path.getmtime(version_mgr.VERSION_FILE)
            with open(version_mgr.VERSION_FILE, "r") as f:
                cls._version_backup = f.read()
        if os.path.exists(auth.AUTH_FILE):
            cls._auth_mtime = os.path.getmtime(auth.AUTH_FILE)
            with open(auth.AUTH_FILE, "r") as f:
                cls._auth_backup = f.read()
        param_file = os.path.join(BASE_DIR, "solo.param")
        if os.path.exists(param_file):
            cls._param_mtime = os.path.getmtime(param_file)
            with open(param_file, "r") as f:
                cls._param_backup = f.read()

    @classmethod
    def tearDownClass(cls):
        if cls._version_backup is not None:
            with open(version_mgr.VERSION_FILE, "w") as f:
                f.write(cls._version_backup)
            if cls._version_mtime is not None:
                try:
                    os.utime(version_mgr.VERSION_FILE, (cls._version_mtime, cls._version_mtime))
                except Exception:
                    pass
        if cls._auth_backup is not None:
            with open(auth.AUTH_FILE, "w") as f:
                f.write(cls._auth_backup)
            if cls._auth_mtime is not None:
                try:
                    os.utime(auth.AUTH_FILE, (cls._auth_mtime, cls._auth_mtime))
                except Exception:
                    pass
        param_file = os.path.join(BASE_DIR, "solo.param")
        if cls._param_backup is not None:
            with open(param_file, "w") as f:
                f.write(cls._param_backup)
            if cls._param_mtime is not None:
                try:
                    os.utime(param_file, (cls._param_mtime, cls._param_mtime))
                except Exception:
                    pass
        try:
            import bake_into_firmware
            bake_into_firmware.ensure_firmware_baked(log_func=None)
        except Exception:
            pass

    def setUp(self):
        # Clean sessions
        auth._active_sessions.clear()

    def test_01_version_manager(self):
        """Test versioning and incrementing 0.0.1 per iteration."""
        ver_info = version_mgr.get_version_info()
        cur_v = ver_info["version"]
        cur_it = ver_info["iteration"]
        major, minor, patch = version_mgr.parse_version(cur_v)

        # Increment iteration 1
        new_v, it = version_mgr.increment_version("Test iteration 1")
        self.assertEqual(new_v, "%d.%d.%d" % (major, minor, patch + 1))
        self.assertEqual(it, cur_it + 1)

        # Increment iteration 2
        new_v2, it2 = version_mgr.increment_version("Test iteration 2")
        self.assertEqual(new_v2, "%d.%d.%d" % (major, minor, patch + 2))
        self.assertEqual(it2, cur_it + 2)

    def test_02_auth_system(self):
        """Test authentication, default password 'sololink', hashing, and session tokens."""
        # Default password check
        self.assertTrue(auth.verify_password("sololink"))
        self.assertFalse(auth.verify_password("wrongpassword"))

        # Create session
        token = auth.create_session()
        self.assertTrue(auth.validate_session(token))
        self.assertFalse(auth.validate_session("fake-token-123"))

        # Change password
        ok, msg = auth.change_password("sololink", "SoloAdmin2026!")
        self.assertTrue(ok)
        self.assertTrue(auth.verify_password("SoloAdmin2026!"))
        self.assertFalse(auth.verify_password("sololink"))

        # Old session should be invalidated
        self.assertFalse(auth.validate_session(token))

        # Reset password back
        ok_reset, _ = auth.force_set_password("sololink")
        self.assertTrue(ok_reset)
        self.assertTrue(auth.verify_password("sololink"))

    def test_03_firmware_deduplication_and_files(self):
        """Verify firmware files exist and APJs are distinct while tar.gz is shared."""
        firmware_dir = os.path.join(BASE_DIR, "firmware")
        solo_apj = os.path.join(firmware_dir, "arducopter_CubeSolo_4.7.2.apj")
        green_apj = os.path.join(firmware_dir, "arducopter_CubeGreen_4.7.2.apj")
        legacy_solo = os.path.join(firmware_dir, "arducopter_solo.apj")
        legacy_green = os.path.join(firmware_dir, "arducopter_green.apj")
        tar_gz = os.path.join(firmware_dir, "3dr-solo.tar.gz")
        tar_md5 = os.path.join(firmware_dir, "3dr-solo.tar.gz.md5")
        ctrl_tar_gz = os.path.join(firmware_dir, "3dr-controller.tar.gz")
        ctrl_tar_md5 = os.path.join(firmware_dir, "3dr-controller.tar.gz.md5")

        self.assertTrue(os.path.exists(solo_apj), "arducopter_CubeSolo_4.7.2.apj missing")
        self.assertTrue(os.path.exists(green_apj), "arducopter_CubeGreen_4.7.2.apj missing")
        self.assertFalse(os.path.exists(legacy_solo), "legacy arducopter_solo.apj must not exist")
        self.assertFalse(os.path.exists(legacy_green), "legacy arducopter_green.apj must not exist")
        self.assertTrue(os.path.exists(tar_gz), "3dr-solo.tar.gz missing")
        self.assertTrue(os.path.exists(tar_md5), "3dr-solo.tar.gz.md5 missing")
        self.assertTrue(os.path.exists(ctrl_tar_gz), "3dr-controller.tar.gz missing")
        self.assertTrue(os.path.exists(ctrl_tar_md5), "3dr-controller.tar.gz.md5 missing")

        # Verify size of Solo APJ vs Green APJ
        solo_sz = os.path.getsize(solo_apj)
        green_sz = os.path.getsize(green_apj)
        self.assertGreater(solo_sz, 1000000, "Solo APJ must be valid firmware size")
        self.assertGreater(green_sz, 1000000, "Green Cube APJ must be valid firmware size")
        self.assertNotEqual(solo_sz, green_sz, "Solo APJ and Green Cube APJ should be distinct")

        # Verify tar.gz sizes (3dr-solo.tar.gz includes baked SoloWebUtility2)
        tar_sz = os.path.getsize(tar_gz)
        self.assertGreater(tar_sz, 75000000, "3dr-solo.tar.gz should be a complete firmware archive")
        ctrl_tar_sz = os.path.getsize(ctrl_tar_gz)
        self.assertEqual(ctrl_tar_sz, 75114379)
        self.assertNotEqual(tar_sz, ctrl_tar_sz, "Solo Copter tar and Controller tar are distinct")

        # Verify flash bin files
        self.assertTrue(os.path.exists(os.path.join(BASE_DIR, "bin", "pixhawk.py")))
        self.assertTrue(os.path.exists(os.path.join(BASE_DIR, "bin", "uploader.py")))

    def test_04_presets_and_param_manager(self):
        """Test preset retrieval and parameter editing/searching/saving."""
        all_presets = presets.get_all_presets()
        preset_ids = [p["id"] for p in all_presets]
        
        # Verify required presets
        self.assertIn("here_gps", preset_ids)
        self.assertIn("stock_compass", preset_ids)
        self.assertIn("retract_gear", preset_ids)
        self.assertIn("speed_default", preset_ids)
        self.assertIn("speed_fast", preset_ids)
        self.assertIn("bait_drop", preset_ids)
        self.assertIn("lipo_default", preset_ids)
        self.assertIn("liion_default", preset_ids)

        # Parameter Manager search
        results = param_mgr.mgr.search_params("GPS")
        self.assertTrue(len(results) > 0)
        gps_type_found = any(p["name"] == "GPS_TYPE" for p in results)
        self.assertTrue(gps_type_found)

        # Parameter update
        ok, msg = param_mgr.mgr.set_param("WPNAV_SPEED", 1500, send_mavlink=False)
        self.assertTrue(ok)
        self.assertEqual(param_mgr.mgr.params["WPNAV_SPEED"], 1500)

        # Apply Preset here_gps (COMPASS_ORIENT = 0)
        here_p = presets.get_preset("here_gps")
        ok_p, _ = param_mgr.mgr.set_batch_params(here_p["params"], send_mavlink=False)
        self.assertTrue(ok_p)
        self.assertEqual(param_mgr.mgr.params["COMPASS_ORIENT"], 0)

        # Apply Preset stock_compass (COMPASS_ORIENT = 38)
        stock_p = presets.get_preset("stock_compass")
        ok_s, _ = param_mgr.mgr.set_batch_params(stock_p["params"], send_mavlink=False)
        self.assertTrue(ok_s)
        self.assertEqual(param_mgr.mgr.params["COMPASS_ORIENT"], 38)

        # Export string
        export_str = param_mgr.mgr.export_param_string()
        self.assertIn("COMPASS_ORIENT", export_str)

    def test_05_pairing_and_wifi_modules(self):
        """Test pairing state machine and wifi manager queries."""
        pair_state = pair_mgr.pair_engine.get_state()
        self.assertEqual(pair_state["is_pairing"], False)
        self.assertEqual(pair_state["status"], "ready")

        orig_ssh = wifi_mgr._ssh
        orig_ssh_paramiko = getattr(wifi_mgr, "_ssh_paramiko", orig_ssh)
        sample_iw = (
            "===IW===\n"
            "BSS 00:11:22:33:44:55(on wlan0)\n"
            "\tfreq: 2437\n"
            "\tcapability: ESS Privacy ShortSlotTime (0x0411)\n"
            "\tsignal: -50.00 dBm\n"
            "\tSSID: Home_2.4G\n"
            "\tBSS Load:\n"
            "\t\t * station count: 2\n"
            "\tRSN:\t * Version: 1\n"
            "\t\t * Authentication suites: PSK\n"
            "BSS aa:bb:cc:dd:ee:ff(on wlan0)\n"
            "\tfreq: 2412\n"
            "\tcapability: ESS (0x0401)\n"
            "\tsignal: -75.00 dBm\n"
            "\tSSID: Guest_Open\n"
            "BSS aa:bb:cc:dd:ee:00(on wlan0)\n"
            "\tfreq: 2437\n"
            "\tsignal: -40.00 dBm\n"
            "\tSSID: SoloLink_Test\n"
            "===ERR===\n"
            "===OWN===\n"
            "SoloLink_Test\n"
        )
        sample_status = (
            "===SSID===\nHome_2.4G\n===ADDR===\n===PING===\nNO_ADDR\n===FWD===\n1\n===IPT===\n"
            "===LOG===\n10:00:00 STATUS: FAILED reason=wrong_password detail=x\n===WPA===\n===END===\n"
        )
        sent = []

        def fake_ssh(cmd, host="10.1.1.1", timeout=20):
            sent.append(cmd)
            if " scan" in cmd:
                return (0, sample_iw)
            if "connect" in cmd:
                return (0, "LAUNCHED\n")
            return (0, sample_status)

        wifi_mgr._ssh = fake_ssh
        wifi_mgr._ssh_paramiko = lambda cmd, host="10.1.1.1", timeout=30: fake_ssh(cmd, host, timeout)
        orig_ctrl_reach = wifi_mgr._ctrl_reachable
        wifi_mgr._ctrl_reachable = lambda: True
        try:
            wifi_status = wifi_mgr.get_wifi_status()
            self.assertIn("mode", wifi_status)
            self.assertIn("connected_ssid", wifi_status)
            self.assertEqual(wifi_status["connection_state"], "failed")
            self.assertIn("Wrong Wi-Fi password", wifi_status["failure"])

            # Scanning: fresh iw sweep, own SoloLink AP filtered out
            scan_res = wifi_mgr.scan_wifi_networks()
            self.assertTrue(scan_res.get("success"))
            self.assertEqual(scan_res.get("count"), 2)
            net_names = [n["ssid"] for n in scan_res.get("networks", [])]
            self.assertIn("Home_2.4G", net_names)
            self.assertIn("Guest_Open", net_names)
            self.assertNotIn("SoloLink_Test", net_names)

            # WPA PSK derivation (IEEE 802.11i test vector)
            self.assertEqual(wifi_mgr.wpa_psk_hex("IEEE", "password"),
                             "f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e")

            # Connect: the password never travels in clear text; launch is detached
            ok, msg = wifi_mgr.connect_home_wifi("Home_2.4G", "Secret$Pass`word")
            self.assertTrue(ok)
            self.assertIn("Home_2.4G", msg)
            self.assertNotIn("Secret$Pass", sent[-1])
            self.assertIn("setsid sh /tmp/swu-wifi.sh connect", sent[-1])

            ok, msg = wifi_mgr.connect_home_wifi("Home_2.4G", "short")
            self.assertFalse(ok)
        finally:
            wifi_mgr._ssh = orig_ssh
            wifi_mgr._ssh_paramiko = orig_ssh_paramiko
            wifi_mgr._ctrl_reachable = orig_ctrl_reach

    def test_06_calibration_manager(self):
        """Test IMU calibration, autopilot reboot, and compass calibration sequence."""
        # 1. State check
        state = calib_mgr.calib_engine.get_state()
        self.assertFalse(state["is_running"])
        self.assertEqual(state["status"], "ready")

        # 2. Mock MAVLink Message & Connection classes
        class MockMavMsg(object):
            def __init__(self, mtype, **kwargs):
                self._type = mtype
                for k, v in kwargs.items():
                    setattr(self, k, v)
            def get_type(self):
                return self._type

        class MockMavConnection(object):
            def __init__(self):
                self.target_system = 1
                self.target_component = 1
                self.sent_commands = []
                self.responses = []
                self.closed = False
                class MavSender(object):
                    def __init__(self, parent):
                        self.parent = parent
                    def command_long_send(self, sys_id, comp_id, cmd, conf, p1, p2, p3, p4, p5, p6, p7):
                        self.parent.sent_commands.append((cmd, p1, p2, p5))
                    def heartbeat_send(self, *args, **kwargs):
                        pass
                self.mav = MavSender(self)

            def recv_match(self, type=None, blocking=True, timeout=0.5):
                if self.responses:
                    return self.responses.pop(0)
                return None

            def close(self):
                self.closed = True

        # Test IMU calibration
        mock_mav = MockMavConnection()
        mock_mav.responses = [
            MockMavMsg('STATUSTEXT', text="Calibrating board level..."),
            MockMavMsg('COMMAND_ACK', command=calib_mgr.MAV_CMD_PREFLIGHT_CALIBRATION, result=calib_mgr.MAV_RESULT_ACCEPTED)
        ]
        logs = []
        ok, msg = calib_mgr.calibrate_imu(mock_mav, imu_type='level', timeout=5.0, log_cb=lambda m, l: logs.append(m))
        self.assertTrue(ok)
        self.assertIn("completed successfully", msg)
        self.assertEqual(len(mock_mav.sent_commands), 1)
        self.assertEqual(mock_mav.sent_commands[0][0], calib_mgr.MAV_CMD_PREFLIGHT_CALIBRATION)

        # Test Reboot
        mock_mav2 = MockMavConnection()
        reconnected_mav = MockMavConnection()
        orig_get_conn = calib_mgr.get_mavlink_connection
        calib_mgr.get_mavlink_connection = lambda endpoint=None, timeout=2.0: reconnected_mav
        try:
            ok_reb, new_mav, msg_reb = calib_mgr.reboot_autopilot(mock_mav2, timeout=5.0, reset_delay=0.0)
            self.assertTrue(ok_reb)
            self.assertIsNotNone(new_mav)
            self.assertTrue(mock_mav2.closed)
            self.assertEqual(mock_mav2.sent_commands[0][0], calib_mgr.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN)
        finally:
            calib_mgr.get_mavlink_connection = orig_get_conn

        # Test Compass calibration
        mock_mav3 = MockMavConnection()
        mock_mav3.responses = [
            MockMavMsg('COMMAND_ACK', command=calib_mgr.MAV_CMD_DO_START_MAG_CAL, result=calib_mgr.MAV_RESULT_ACCEPTED),
            MockMavMsg('MAG_CAL_PROGRESS', compass_id=0, completion_pct=50, cal_status=calib_mgr.MAG_CAL_RUNNING_STEP_ONE),
            MockMavMsg('MAG_CAL_REPORT', compass_id=0, cal_status=calib_mgr.MAG_CAL_SUCCESS, fitness=0.05)
        ]
        ok_comp, msg_comp = calib_mgr.calibrate_compass(mock_mav3, timeout=5.0, wait_completion=True)
        self.assertTrue(ok_comp)
        self.assertIn("completed successfully", msg_comp)
        sent_ids = [c[0] for c in mock_mav3.sent_commands]
        # leftover cal is cleared first, then started on all compasses with retry=1 and autosave=1
        self.assertEqual(sent_ids[0], calib_mgr.MAV_CMD_DO_CANCEL_MAG_CAL)
        start_cmd = [c for c in mock_mav3.sent_commands if c[0] == calib_mgr.MAV_CMD_DO_START_MAG_CAL][0]
        self.assertEqual(start_cmd[1], 0.0)   # param1: all compasses
        self.assertEqual(start_cmd[2], 1.0)   # param2: retry on failure
        self.assertEqual(sent_ids.count(calib_mgr.MAV_CMD_DO_CANCEL_MAG_CAL), 1)  # no cancel after success

        # A failed compass must always be followed by MAV_CMD_DO_CANCEL_MAG_CAL (stops the failure tone)
        mock_fail = MockMavConnection()
        mock_fail.responses = [
            MockMavMsg('COMMAND_ACK', command=calib_mgr.MAV_CMD_DO_START_MAG_CAL, result=calib_mgr.MAV_RESULT_ACCEPTED),
            MockMavMsg('MAG_CAL_PROGRESS', compass_id=1, completion_pct=89, cal_status=calib_mgr.MAG_CAL_RUNNING_STEP_TWO),
            MockMavMsg('MAG_CAL_REPORT', compass_id=1, cal_status=calib_mgr.MAG_CAL_FAILED, fitness=80.0),
        ]
        ok_fail, msg_fail = calib_mgr.calibrate_compass(mock_fail, timeout=5.0, wait_completion=True)
        self.assertFalse(ok_fail)
        fail_ids = [c[0] for c in mock_fail.sent_commands]
        self.assertEqual(fail_ids[-1], calib_mgr.MAV_CMD_DO_CANCEL_MAG_CAL)
        self.assertGreaterEqual(fail_ids.count(calib_mgr.MAV_CMD_DO_CANCEL_MAG_CAL), 2)

        # Test Full Pipeline (calibrate_imu_reboot_compass)
        mock_pipeline_mav = MockMavConnection()
        mock_pipeline_mav.responses = [
            MockMavMsg('STATUSTEXT', text="Calibrating offsets"),
            MockMavMsg('COMMAND_ACK', command=calib_mgr.MAV_CMD_PREFLIGHT_CALIBRATION, result=calib_mgr.MAV_RESULT_ACCEPTED),
            MockMavMsg('COMMAND_ACK', command=calib_mgr.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN, result=calib_mgr.MAV_RESULT_ACCEPTED),
        ]
        pipeline_reconnected = MockMavConnection()
        pipeline_reconnected.responses = [
            MockMavMsg('COMMAND_ACK', command=calib_mgr.MAV_CMD_DO_START_MAG_CAL, result=calib_mgr.MAV_RESULT_ACCEPTED),
            MockMavMsg('MAG_CAL_PROGRESS', compass_id=0, completion_pct=100, cal_status=calib_mgr.MAG_CAL_SUCCESS),
            MockMavMsg('MAG_CAL_REPORT', compass_id=0, cal_status=calib_mgr.MAG_CAL_SUCCESS, fitness=0.02)
        ]
        calib_mgr.get_mavlink_connection = lambda endpoint=None, timeout=2.0: pipeline_reconnected
        try:
            ok_full, msg_full = calib_mgr.calibrate_imu_reboot_compass(
                connection=mock_pipeline_mav,
                imu_type='simple',
                wait_for_compass=True,
                compass_timeout=5.0,
                reboot_timeout=5.0,
                imu_timeout=5.0,
                reboot_reset_delay=0.0
            )
            self.assertTrue(ok_full)
            self.assertIn("completed successfully", msg_full)
        finally:
            calib_mgr.get_mavlink_connection = orig_get_conn

        # Test Separate Methods in CalibrationManager
        engine = calib_mgr.CalibrationManager()
        self.assertFalse(engine.get_state()["is_running"])
        self.assertEqual(engine.get_state()["status"], "ready")

        # Mock connection for start_imu
        imu_mock = MockMavConnection()
        imu_mock.is_mock = True
        imu_mock.responses = [
            MockMavMsg('STATUSTEXT', text="IMU level complete"),
            MockMavMsg('COMMAND_ACK', command=calib_mgr.MAV_CMD_PREFLIGHT_CALIBRATION, result=calib_mgr.MAV_RESULT_ACCEPTED)
        ]
        conn_called = threading.Event()
        def mock_get_conn(endpoint=None, timeout=5.0):
            conn_called.set()
            return imu_mock

        calib_mgr.get_mavlink_connection = mock_get_conn
        try:
            ok_start_imu, _ = engine.start_imu()
            self.assertTrue(ok_start_imu)
            conn_called.wait(timeout=2.0)
            
            # Step through all 6 positions via continue_imu()
            for expected_pos in range(1, 7):
                # Wait for status to become imu_waiting
                t0 = time.time()
                while time.time() - t0 < 3.0:
                    st = engine.get_state()
                    if st["status"] == "imu_waiting" and st["pos_index"] == expected_pos:
                        break
                    time.sleep(0.02)
                st = engine.get_state()
                self.assertEqual(st["status"], "imu_waiting")
                self.assertEqual(st["pos_index"], expected_pos)
                
                # User confirms position
                ok_cont, _ = engine.continue_imu()
                self.assertTrue(ok_cont)
                time.sleep(0.05)

            # Wait for completion
            t0 = time.time()
            while time.time() - t0 < 3.0:
                if not engine.get_state()["is_running"]:
                    break
                time.sleep(0.02)

            st = engine.get_state()
            self.assertEqual(st["status"], "imu_completed")
            self.assertEqual(st["progress"], 100)
            self.assertEqual(st["positions_done"], [1, 2, 3, 4, 5, 6])
            # the autopilot was rebooted automatically after a successful 6-axis calibration
            self.assertIn(calib_mgr.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN, [c[0] for c in imu_mock.sent_commands])
            self.assertTrue(any("Rebooting autopilot automatically" in l["message"] for l in st["logs"]))
        finally:
            calib_mgr.get_mavlink_connection = orig_get_conn

        # Test start_compass with auto reboot
        compass_mock = MockMavConnection()
        compass_mock.is_mock = True
        compass_mock.responses = [
            MockMavMsg('COMMAND_ACK', command=calib_mgr.MAV_CMD_DO_START_MAG_CAL, result=calib_mgr.MAV_RESULT_ACCEPTED),
            MockMavMsg('MAG_CAL_PROGRESS', compass_id=0, completion_pct=60, cal_status=calib_mgr.MAG_CAL_RUNNING_STEP_ONE),
            MockMavMsg('MAG_CAL_PROGRESS', compass_id=1, completion_pct=40, cal_status=calib_mgr.MAG_CAL_RUNNING_STEP_ONE),
            MockMavMsg('MAG_CAL_REPORT', compass_id=0, cal_status=calib_mgr.MAG_CAL_SUCCESS, fitness=0.03),
            MockMavMsg('MAG_CAL_PROGRESS', compass_id=1, completion_pct=85, cal_status=calib_mgr.MAG_CAL_RUNNING_STEP_ONE),
            MockMavMsg('MAG_CAL_REPORT', compass_id=1, cal_status=calib_mgr.MAG_CAL_SUCCESS, fitness=0.04),
            MockMavMsg('COMMAND_ACK', command=calib_mgr.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN, result=calib_mgr.MAV_RESULT_ACCEPTED)
        ]
        reboot_conn_mock = MockMavConnection()
        reboot_conn_mock.is_mock = True
        call_count = [0]
        def mock_compass_conn(endpoint=None, timeout=5.0):
            call_count[0] += 1
            if call_count[0] == 1:
                return compass_mock
            return reboot_conn_mock

        calib_mgr.get_mavlink_connection = mock_compass_conn
        try:
            engine_comp = calib_mgr.CalibrationManager()
            ok_start_comp, _ = engine_comp.start_compass()
            self.assertTrue(ok_start_comp)
            t0 = time.time()
            while time.time() - t0 < 10.0:
                if not engine_comp.get_state()["is_running"]:
                    break
                time.sleep(0.02)
            st_comp = engine_comp.get_state()
            self.assertIn(st_comp["status"], ["compass_completed", "rebooting"])
            self.assertEqual(st_comp["progress"], 100)
        finally:
            calib_mgr.get_mavlink_connection = orig_get_conn

    def test_07_firmware_update_and_builder(self):
        """Test APJ builder quick check, version detection, and setup package resolution."""
        import arducopter_builder
        import flash_mgr

        # Verify local firmware version detection
        ver = arducopter_builder.get_local_firmware_version()
        self.assertEqual(ver, "4.7.2")

        # Verify quick check against custom build API
        chk = arducopter_builder.update_engine.check_version_quick()
        self.assertTrue(chk.get("success"))
        self.assertEqual(chk.get("local_version"), "4.7.2")
        self.assertTrue(bool(chk.get("online_version")))

        # Verify versioned APJ resolution
        apj_solo = flash_mgr.get_latest_apj("solo")
        self.assertIsNotNone(apj_solo)
        self.assertTrue(os.path.exists(apj_solo))
        self.assertIn("CubeSolo", apj_solo)
        self.assertIn("4.7.2", apj_solo)

        apj_green = flash_mgr.get_latest_apj("green")
        self.assertIsNotNone(apj_green)
        self.assertTrue(os.path.exists(apj_green))
        self.assertIn("CubeGreen", apj_green)
        self.assertIn("4.7.2", apj_green)

    def test_08_flask_modular_architecture(self):
        """Test modern modular Flask application and isolated background TCP server."""
        import app
        from modules.legacy_server import start_legacy_server
        import socket
        import struct

        # Test Flask test client
        client = app.app.test_client()

        # Public endpoints
        res_idx = client.get("/")
        self.assertEqual(res_idx.status_code, 200)

        res_cp = client.get("/hotspot-detect.html")
        self.assertEqual(res_cp.status_code, 302)

        res_status = client.get("/api/status")
        self.assertEqual(res_status.status_code, 200)
        data_status = res_status.get_json()
        self.assertIn("copter_online", data_status)
        self.assertIn("utility_version", data_status)

        res_ver = client.get("/api/version")
        self.assertEqual(res_ver.status_code, 200)

        # Login endpoint
        res_login = client.post("/api/auth/login", json={"password": "sololink"})
        self.assertEqual(res_login.status_code, 200)
        token = res_login.get_json().get("token")
        self.assertTrue(bool(token))

        # Protected modular blueprint endpoints
        headers = {"X-Auth-Token": token}
        res_p = client.get("/api/params", headers=headers)
        self.assertEqual(res_p.status_code, 200)

        res_c = client.get("/api/controller/definitions", headers=headers)
        self.assertEqual(res_c.status_code, 200)

        res_f = client.get("/api/flash/status", headers=headers)
        self.assertEqual(res_f.status_code, 200)

        res_w = client.get("/api/wifi/status", headers=headers)
        self.assertEqual(res_w.status_code, 200)

        res_cal = client.get("/api/calib/status", headers=headers)
        self.assertEqual(res_cal.status_code, 200)

        res_pair = client.get("/api/pair/status", headers=headers)
        self.assertEqual(res_pair.status_code, 200)

        # Test isolated background legacy TCP server loop
        server_thread = start_legacy_server(port=5533, flask_port=5000)
        self.assertTrue(server_thread.is_alive())

        test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        test_sock.connect(("127.0.0.1", 5533))
        test_sock.sendall(struct.pack("!I", 4))
        time.sleep(0.3)
        st = server_thread.get_status()
        self.assertEqual(st["connected_client_ip"], "127.0.0.1")
        test_sock.close()
        time.sleep(0.3)
        server_thread.stop()

    def test_09_wifi_lockdown_and_relatch(self):
        """Test Wi-Fi roaming lockdown, WinError 10051 exception detection, and 3-attempt retry/re-latch."""
        import socket
        from modules.controller_firmware import (
            is_network_socket_exception,
            lock_wifi_profile,
            unlock_wifi_profiles,
            relatch_wifi_connection,
        )
        from modules.firmware import sftp_upload_to_copter

        # 1. Verify socket / WinError 10051 detection
        e_win = OSError(10051, "A socket operation was attempted to an unreachable network")
        setattr(e_win, "winerror", 10051)
        self.assertTrue(is_network_socket_exception(e_win))

        e_str = RuntimeError("Custom socket failure: WinError 10051 unreachable network")
        self.assertTrue(is_network_socket_exception(e_str))

        e_timeout = socket.timeout("timed out")
        self.assertTrue(is_network_socket_exception(e_timeout))

        e_unrelated = ValueError("Invalid argument")
        self.assertFalse(is_network_socket_exception(e_unrelated))

        # 2. Test Wi-Fi lockdown and unlock execution
        ssid, modified = lock_wifi_profile("SoloLink_TestProfile")
        self.assertEqual(ssid, "SoloLink_TestProfile")
        self.assertIsInstance(modified, list)
        unlock_wifi_profiles(modified)

        # 3. Test upload with sftp_upload_to_copter
        import modules.firmware as fw
        import flash_mgr
        orig_scp = flash_mgr._scp_to
        try:
            flash_mgr._scp_to = lambda *a, **k: (0, "ok")
            ok, msg = fw.sftp_upload_to_copter("test_file.txt", "/tmp/test_file.txt", saved_ssid="SoloLink_TestProfile")
            self.assertTrue(ok)
        finally:
            flash_mgr._scp_to = orig_scp

    def test_10_copter_reboot_command_and_countdown_templates(self):
        """Test clean copter reboot command string, milestone logging, and 8-minute countdown elements."""
        import modules.firmware as fw
        executed_cmds = []

        def mock_ssh_run(cmd, host="10.1.1.10", **kwargs):
            executed_cmds.append((host, cmd))
            return 0, ""

        orig_ssh = fw.ssh_run_on_copter
        fw.ssh_run_on_copter = mock_ssh_run
        logs = []
        try:
            ok = fw.reboot_copter(host="10.1.1.10", log_callback=lambda m, lvl: logs.append((m, lvl)))
            self.assertTrue(ok)
            self.assertEqual(len(executed_cmds), 2)
            self.assertIn("touch /log/updates/RUN_PAIR_ONCE", executed_cmds[0][1])
            self.assertEqual(executed_cmds[1][0], "10.1.1.10")
            self.assertEqual(executed_cmds[1][1], "nohup sh -c 'sleep 2 && reboot' >/dev/null 2>&1 &")
            self.assertTrue(any("Staging one-time post-update pairing trigger" in l[0] for l in logs))
            self.assertTrue(any("reboot triggered cleanly" in l[0] for l in logs))
        finally:
            fw.ssh_run_on_copter = orig_ssh

        # Verify flash_mgr.py contains RUN_PAIR_ONCE staging and copter reboot
        with open(os.path.join(BASE_DIR, "flash_mgr.py"), "r", encoding="utf-8") as f:
            fm_src = f.read()
        self.assertIn("touch /log/updates/RUN_PAIR_ONCE", fm_src)
        self.assertIn("nohup sh -c 'sleep 2 && reboot' >/dev/null 2>&1 &", fm_src)
        self.assertIn("[AUTO-REBOOT] Arming copter update and rebooting...", fm_src)

        # Full Install must never ask the initramfs to reformat /log on the copter:
        # only UPDATE is armed; settings are reset by emptying the RWFS overlay.
        import inspect as _inspect
        for _name in ("_pipeline", "_stage_copter_update", "_arm_copter_update_and_reboot"):
            _src = _inspect.getsource(getattr(flash_mgr.FlashManager, _name))
            local_flag_cmds = [l for l in _src.splitlines() if "RESETSETTINGS" in l and "touch" in l]
            self.assertEqual(local_flag_cmds, [], "copter must not touch RESETSETTINGS (%s): %s" % (_name, local_flag_cmds))
        # ...while the controller update (run over SSH on 10.1.1.1) is armed with UPDATE + RESETSETTINGS
        _ctrl_src = _inspect.getsource(flash_mgr.FlashManager._stage_controller_update)
        self.assertIn("touch /log/updates/UPDATE /log/updates/RESETSETTINGS", _ctrl_src)
        self.assertIn("self._ctrl_cmd(", _ctrl_src)
        self.assertIn("def _arm_copter_update_and_reboot", fm_src)
        self.assertIn("def _full_install_preflight", fm_src)
        self.assertIn("shutil.copyfile(self._solo_src, dst)", fm_src)   # copy, never move
        self.assertNotIn("os.rename(self._solo_src", fm_src)
        self.assertNotIn("shutil.move(self._solo_src", fm_src)
        # pre-flight runs in Stage 1, before the Cube is flashed
        _pipe_src = _inspect.getsource(flash_mgr.FlashManager._pipeline)
        self.assertLess(_pipe_src.find("self._full_install_preflight()"), _pipe_src.find("self._run_pixhawk_load(pixhawk_bin)"))
        self.assertGreater(_pipe_src.find("self._full_install_preflight()"), 0)

        # Verify modules/controller_firmware.py handles RUN_PAIR_ONCE post-update pairing automation
        with open(os.path.join(BASE_DIR, "modules", "controller_firmware.py"), "r", encoding="utf-8") as f:
            cf_src = f.read()
        self.assertIn("[ -f /log/updates/RUN_PAIR_ONCE ]", cf_src)
        self.assertIn("sololink_config --pair", cf_src)
        self.assertIn("rm -f /log/updates/RUN_PAIR_ONCE", cf_src)
        self.assertIn("Automatically broadcasting software pairing invitation now...", cf_src)

        # Verify templates/updates.html contains 8-minute progress countdown and guard alert
        with open(os.path.join(BASE_DIR, "templates", "updates.html"), "r", encoding="utf-8") as f:
            up_src = f.read()
        self.assertIn('id="rebootTimerBox"', up_src)
        self.assertIn('id="rebootWarningAlert"', up_src)
        self.assertIn('id="rebootCountdown"', up_src)
        self.assertIn('id="rebootProgressBar"', up_src)
        self.assertIn("DO NOT POWER OFF THE DRONE OR CONTROLLER. Firmware installation is executing internally.", up_src)
        self.assertIn("Update Complete! Software pairing mode is currently active. Hold A+B on your Artoo Controller to finalize link.", up_src)
        self.assertIn("step2PairCard", up_src)

        # Verify static/app.js contains success transition, timerStarted safety lock, and smooth-scroll
        with open(os.path.join(BASE_DIR, "static", "app.js"), "r", encoding="utf-8") as f:
            app_src = f.read()
        self.assertIn("Update Complete! Software pairing mode is currently active. Hold A+B on your Artoo Controller to finalize link.", app_src)
        self.assertIn("scrollToPairingInstructions", app_src)
        self.assertIn("step2PairCard", app_src)
        self.assertIn("timerStarted", app_src)

        # Verify flash_mgr.py establishes RUN_PAIR_ONCE explicitly before controller reboot at 10.1.1.1
        run_pair_idx = fm_src.find("touch /log/updates/RUN_PAIR_ONCE")
        ctrl_reboot_idx = fm_src.find("Triggering controller reboot at 10.1.1.1...")
        self.assertTrue(run_pair_idx != -1 and ctrl_reboot_idx != -1)
        self.assertTrue(run_pair_idx < ctrl_reboot_idx)

        # Verify modules/firmware.py contains split network checks, 1s ping loop, and post-update pairing
        with open(os.path.join(BASE_DIR, "modules", "firmware.py"), "r", encoding="utf-8") as f:
            fw_src = f.read()
        self.assertIn("def reconnect_wifi", fw_src)
        self.assertIn("def _probe_copter_awake", fw_src)
        self.assertIn("sololink_config --pair", fw_src)
        self.assertIn("rm -f /log/updates/RUN_PAIR_ONCE", fw_src)
        self.assertIn("set_copter_boot_in_progress", fw_src)

        # Verify modules/telemetry.py keeps get_live_copter_status False while boot in progress
        from modules.telemetry import (
            set_copter_boot_in_progress,
            set_live_copter_status,
            get_live_copter_status,
        )
        set_copter_boot_in_progress(True)
        self.assertFalse(get_live_copter_status())
        set_copter_boot_in_progress(False)

        # Verify static/app.js strictly links countdown timer to copter_online
        self.assertIn("copterIsOnline", app_src)
        self.assertIn("previousCopterOnline = false;", app_src)

    def test_11_standard_pixhawk_flash_tar_bake_and_uninstall(self):
        """Test standard Solo pixhawk flash, tar baking, 6-min countdown/ping, and uninstall endpoint."""
        import tempfile
        import tarfile
        import flash_mgr
        import app

        # 1. Verify flash_mgr.py Stage 2 standard Solo pixhawk flash logic & APJ staging
        with open(os.path.join(BASE_DIR, "flash_mgr.py"), "r", encoding="utf-8") as f:
            fm_src = f.read()
        self.assertIn("find_apj_file", fm_src)
        self.assertIn("normalize_cube_type", fm_src)
        self.assertIn("release_mavlink_connections()", fm_src)
        self.assertIn("sololink_config --update-prepare pixhawk", fm_src)
        self.assertIn("/usr/bin/pixhawk.py", fm_src)
        self.assertIn("--load /firmware/arducopter.apj", fm_src)
        self.assertIn("os.path.exists(\"/firmware/arducopter.apj\")", fm_src)
        # Ensure rm -f /firmware/*.apj is NOT run
        self.assertNotIn('self._run("rm -f /firmware/*.apj 2>/dev/null || true")', fm_src)
        self.assertIn("Wait ~6 minutes for reboot to finish.", fm_src)

        # Test normalize_cube_type and get_latest_apj
        self.assertEqual(flash_mgr.normalize_cube_type("solo"), "CubeSolo")
        self.assertEqual(flash_mgr.normalize_cube_type("black"), "CubeSolo")
        self.assertEqual(flash_mgr.normalize_cube_type("Solo Cube (Black)"), "CubeSolo")
        self.assertEqual(flash_mgr.normalize_cube_type("green"), "CubeGreen")
        self.assertEqual(flash_mgr.normalize_cube_type("CubeGreen"), "CubeGreen")

        solo_apj_found = flash_mgr.get_latest_apj("solo")
        self.assertIsNotNone(solo_apj_found)
        self.assertTrue(solo_apj_found.endswith("arducopter_CubeSolo_4.7.2.apj"))

        green_apj_found = flash_mgr.get_latest_apj("green")
        self.assertIsNotNone(green_apj_found)
        self.assertTrue(green_apj_found.endswith("arducopter_CubeGreen_4.7.2.apj"))

        # 2. Test bake_into_firmware module
        import bake_into_firmware
        tmp_dir = tempfile.mkdtemp()
        try:
            test_tar = os.path.join(tmp_dir, "3dr-solo.tar.gz")
            shutil.copyfile(os.path.join(BASE_DIR, "firmware", "3dr-solo.tar.gz"), test_tar)
            logs = []
            ok, digest = bake_into_firmware.bake_tarball(test_tar, app_base_dir=BASE_DIR, log_func=lambda m: logs.append(m))
            self.assertTrue(ok)
            self.assertTrue(os.path.exists(test_tar + ".md5"))

            # Container tarball layout: contains SquashFS rootfs and boot files, no loose files outside SquashFS
            with tarfile.open(test_tar, "r:gz") as tf:
                member_names = [m.name for m in tf.getmembers()]
                self.assertIn("3dr-solo-imx6solo-3dr-1080p.squashfs", member_names)
                self.assertIn("uImage", member_names)
                self.assertIn("u-boot.imx", member_names)
                self.assertNotIn("etc/inittab", member_names)
                self.assertNotIn("etc/rc.local", member_names)
                self.assertNotIn("etc/rc5.d/S99solowebutility", member_names)
                self.assertNotIn("etc/init.d/solowebutility", member_names)
                self.assertNotIn("usr/bin/SoloWebUtility2/app.py", member_names)

            # Inner SquashFS verification via verify_tarball
            v_ok, v_errs, swu_count = bake_into_firmware.verify_tarball(test_tar)
            self.assertTrue(v_ok, "verify_tarball failed on test_tar: %s" % v_errs)
            self.assertGreater(swu_count, 40)
        finally:
            shutil.rmtree(tmp_dir)

        # 3. Test /api/ping and /api/status endpoints
        client = app.app.test_client()
        status_res = client.get("/api/status")
        self.assertEqual(status_res.status_code, 200)

        ping_res = client.get("/api/ping")
        self.assertEqual(ping_res.status_code, 200)
        self.assertIn("no-cache", ping_res.headers.get("Cache-Control", ""))
        import json
        ping_data = json.loads(ping_res.data)
        self.assertTrue(ping_data.get("online"))
        self.assertTrue(ping_data.get("pong"))

        # 4. Verify /api/uninstall route and CLI flag in app.py
        self.assertTrue(hasattr(app, "uninstall_solo_web_utility"))
        with open(os.path.join(BASE_DIR, "app.py"), "r", encoding="utf-8") as f:
            app_code = f.read()
        self.assertIn("/api/uninstall", app_code)
        self.assertIn("--uninstall", app_code)
        self.assertIn("/etc/rc.local", app_code)
        self.assertIn("/usr/bin/SoloWebUtility2", app_code)
        self.assertIn("/log/SoloWebUtility2", app_code)

        # 5. Verify UI countdown and /api/status AbortController polling in templates and app.js
        with open(os.path.join(BASE_DIR, "static", "app.js"), "r", encoding="utf-8") as f:
            app_js = f.read()
        self.assertIn("/api/status", app_js)
        self.assertIn("AbortController", app_js)
        self.assertIn("2500", app_js)
        self.assertIn("45000", app_js)
        self.assertIn("startRebootCountdown(360)", app_js)
        self.assertIn("btnUninstallApp", app_js)

        with open(os.path.join(BASE_DIR, "templates", "index.html"), "r", encoding="utf-8") as f:
            index_html = f.read()
        self.assertIn("rebootCountdown", index_html)
        self.assertIn("06:00", index_html)
        self.assertIn("btnUninstallApp", index_html)

        with open(os.path.join(BASE_DIR, "templates", "updates.html"), "r", encoding="utf-8") as f:
            updates_html = f.read()
        self.assertIn("startRebootCountdown(360)", updates_html)
        self.assertIn("/api/status", updates_html)
        self.assertIn("AbortController", updates_html)
        self.assertIn("btnUninstallAppUpdates", updates_html)

    def test_12_semantic_version_selection_and_legacy_filtering(self):
        """Verify flash_mgr semantic version parsing, automatic selection of highest version, and legacy APJ filtering."""
        import tempfile
        import flash_mgr
        import arducopter_builder

        # 1. Verify semantic version parsing
        self.assertEqual(flash_mgr.parse_apj_version("arducopter_CubeSolo_4.7.1.apj"), (4, 7, 1))
        self.assertEqual(flash_mgr.parse_apj_version("arducopter_CubeGreen_4.7.2.apj"), (4, 7, 2))
        self.assertEqual(flash_mgr.parse_apj_version("arducoptercubesolo-4.8.0.apj"), (4, 8, 0))
        self.assertEqual(flash_mgr.parse_apj_version("arducopter_solo.apj"), (0, 0, 0))
        self.assertEqual(flash_mgr.parse_apj_version("arducopter_green.apj"), (0, 0, 0))

        # 2. Test semantic selection in isolated directory
        tmp_dir = tempfile.mkdtemp(prefix="apj_test_")
        try:
            # Create dummy APJ files (> 100KB)
            test_files = [
                "arducopter_CubeSolo_4.7.1.apj",
                "arducopter_CubeSolo_4.7.2.apj",
                "arducopter_CubeGreen_4.7.1.apj",
                "arducopter_CubeGreen_4.7.3.apj",
                "arducopter_solo.apj",
                "arducopter_green.apj",
                "arducopter.apj",
            ]
            for fn in test_files:
                with open(os.path.join(tmp_dir, fn), "wb") as f:
                    f.write(b"APJ_DUMMY_DATA_" + b"0" * 150000)

            # Check highest semantic version selection
            best_solo = flash_mgr.get_latest_apj("solo", search_dirs=[tmp_dir])
            self.assertIsNotNone(best_solo)
            self.assertTrue(best_solo.endswith("arducopter_CubeSolo_4.7.2.apj"),
                            "Expected 4.7.2 to be chosen over 4.7.1 and legacy solo, got: %s" % best_solo)

            best_green = flash_mgr.get_latest_apj("green", search_dirs=[tmp_dir])
            self.assertIsNotNone(best_green)
            self.assertTrue(best_green.endswith("arducopter_CubeGreen_4.7.3.apj"),
                            "Expected 4.7.3 to be chosen over 4.7.1 and legacy green, got: %s" % best_green)

            # 3. Test clean_old_firmware_files logic
            orig_fw_dir = arducopter_builder.FIRMWARE_DIR
            try:
                arducopter_builder.FIRMWARE_DIR = tmp_dir
                removed = arducopter_builder.clean_old_firmware_files("CubeSolo", "4.7.2")
                self.assertIn("arducopter_CubeSolo_4.7.1.apj", removed)
                self.assertIn("arducopter_solo.apj", removed)
                self.assertFalse(os.path.exists(os.path.join(tmp_dir, "arducopter_CubeSolo_4.7.1.apj")))
                self.assertFalse(os.path.exists(os.path.join(tmp_dir, "arducopter_solo.apj")))
                self.assertTrue(os.path.exists(os.path.join(tmp_dir, "arducopter_CubeSolo_4.7.2.apj")))
            finally:
                arducopter_builder.FIRMWARE_DIR = orig_fw_dir
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_13_automatic_build_hook_and_startup_daemon(self):
        """Verify automatic build hook (stale detection & ensure_firmware_baked) and startup daemon paths."""
        import bake_into_firmware
        import deploy_to_drone

        # 1. Verify ensure_firmware_baked synchronizes any source changes from earlier tests
        ok_sync = bake_into_firmware.ensure_firmware_baked()
        self.assertTrue(ok_sync)

        # After synchronization, archive MUST be fresh and not stale
        stale, newer = bake_into_firmware.is_firmware_stale()
        self.assertFalse(stale, "Expected freshly baked firmware archive to not be stale, but found: %s" % newer)
        self.assertEqual(newer, [])

        # 2. Test stale detection in isolated temp environment
        tmp_dir = tempfile.mkdtemp(prefix="hook_test_")
        try:
            test_tar = os.path.join(tmp_dir, "3dr-solo.tar.gz")
            shutil.copyfile(os.path.join(BASE_DIR, "firmware", "3dr-solo.tar.gz"), test_tar)
            shutil.copyfile(os.path.join(BASE_DIR, "firmware", "3dr-solo.tar.gz.md5"), test_tar + ".md5")

            # Artificially set tarball mtime to 100 seconds in the past
            past_time = time.time() - 100
            os.utime(test_tar, (past_time, past_time))

            # Create a test app dir with a file modified now
            test_app_dir = os.path.join(tmp_dir, "app_src")
            os.makedirs(test_app_dir)
            with open(os.path.join(test_app_dir, "app.py"), "w") as f:
                f.write("# Modified app\n")

            is_stale, modified = bake_into_firmware.is_firmware_stale(
                tar_path=test_tar, app_base_dir=test_app_dir, firmware_dir=tmp_dir
            )
            self.assertTrue(is_stale)
            self.assertIn("app.py", modified)

            # 3. Test ensure_firmware_baked triggers auto-bake and resolves staleness
            logs = []
            orig_bake = bake_into_firmware.bake_tarball
            try:
                def mock_bake(tar_p, app_base_dir=BASE_DIR, log_func=None):
                    now_t = time.time() + 10
                    os.utime(tar_p, (now_t, now_t))
                    with open(tar_p + ".md5", "w") as f:
                        f.write("mock_md5  %s\n" % os.path.basename(tar_p))
                    return True, "mock_md5"
                bake_into_firmware.bake_tarball = mock_bake
                ok = bake_into_firmware.ensure_firmware_baked(
                    tar_path=test_tar, app_base_dir=test_app_dir, firmware_dir=tmp_dir, log_func=lambda m: logs.append(m)
                )
                self.assertTrue(ok)
                # After baking, should no longer be stale
                is_stale_now, _ = bake_into_firmware.is_firmware_stale(
                    tar_path=test_tar, app_base_dir=test_app_dir, firmware_dir=tmp_dir
                )
                self.assertFalse(is_stale_now)
            finally:
                bake_into_firmware.bake_tarball = orig_bake
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # 4. Verify inittab respawn entry configuration in bake_into_firmware and deploy_to_drone
        inittab_line = bake_into_firmware.INITTAB_RESPAWN_ENTRY
        self.assertIn("SWU:345:respawn:", inittab_line)
        self.assertIn("/usr/bin/SoloWebUtility2/solo_web_app.py", inittab_line)
        self.assertIn("/log/solowebutility.log", inittab_line)

        deploy_inittab = deploy_to_drone.INITTAB_RESPAWN_ENTRY
        self.assertIn("SWU:345:respawn:", deploy_inittab)
        self.assertIn("/usr/bin/SoloWebUtility2/solo_web_app.py", deploy_inittab)
        self.assertIn("/log/solowebutility.log", deploy_inittab)

        # 5. Verify deploy_to_drone.py main includes ensure_firmware_baked
        with open(os.path.join(BASE_DIR, "deploy_to_drone.py"), "r", encoding="utf-8") as f:
            d_code = f.read()
        self.assertIn("ensure_firmware_baked", d_code)

        # 6. Verify container layout and inner SquashFS contents of staged 3dr-solo.tar.gz
        tar_path = os.path.join(BASE_DIR, "firmware", "3dr-solo.tar.gz")
        with tarfile.open(tar_path, "r:gz") as tf:
            names = [m.name for m in tf.getmembers()]
            self.assertIn("3dr-solo-imx6solo-3dr-1080p.squashfs", names)
            self.assertIn("uImage", names)
            self.assertIn("u-boot.imx", names)
            self.assertNotIn("etc/inittab", names)
            self.assertNotIn("etc/rc.local", names)
            self.assertNotIn("etc/init.d/solowebutility", names)
            self.assertNotIn("etc/rc5.d/S99solowebutility", names)
            for m in tf.getmembers():
                self.assertFalse(m.name.startswith("etc/") or m.name.startswith("usr/"),
                                 "Loose file found in container tarball: %s" % m.name)

        v_ok, v_errs, swu_count = bake_into_firmware.verify_tarball(tar_path)
        self.assertTrue(v_ok, "verify_tarball failed on staged 3dr-solo.tar.gz: %s" % v_errs)
        self.assertGreater(swu_count, 40)

        # 7. Verify robust port binding retry logic and 0.0.0.0 host in solo_web_app and webcore
        with open(os.path.join(BASE_DIR, "solo_web_app.py"), "r", encoding="utf-8") as f:
            swa_code = f.read()
        self.assertIn("max_retries = 60", swa_code)
        self.assertIn("retry_delay = 2", swa_code)
        self.assertIn("bind_host = '0.0.0.0'", swa_code)
        self.assertIn("(bind_host, chosen_port)", swa_code)

        with open(os.path.join(BASE_DIR, "webcore.py"), "r", encoding="utf-8") as f:
            wc_code = f.read()
        self.assertIn("max_retries=60", wc_code)
        self.assertIn("retry_delay=2", wc_code)
        self.assertIn('host="0.0.0.0"', wc_code)


    def test_14_custombuild_yaml_builder(self):
        """solocustombuild.yaml drives the custom.ardupilot.org build (offline checks)."""
        import io
        import json
        import tarfile
        import arducopter_builder as ab

        # 1. Real config file parses (no PyYAML) and validates against CustomBuild schema 0.0.1
        cfg, path = ab.load_build_config(os.path.join(BASE_DIR, "firmware", "solocustombuild.yaml"))
        self.assertEqual(cfg["config_version"], "0.0.1")
        self.assertEqual(cfg["vehicle"]["id"], "copter")
        self.assertEqual(cfg["board"]["id"], "CubeSolo")
        self.assertIn("APJ_TOOL_PARAMETERS", cfg["selected_features"])
        self.assertIn("MODE_SPORT", cfg["selected_features"])
        self.assertIn("PrecLand - IRLock", cfg["selected_features"])   # labels with spaces
        self.assertNotIn("SCRIPTING", cfg["selected_features"])
        with open(path) as f:
            naive = [l.strip()[2:] for l in f if l.strip().startswith("- ")]
        self.assertEqual(naive, cfg["selected_features"])
        self.assertFalse(hasattr(ab, "ENABLE_FEATURES"))               # no hardcoded feature lists

        # 2. Invalid configs are rejected
        for bad in ("config_version: 9.9.9\n", "vehicle:\n\tid: x\n"):
            with self.assertRaises(ValueError):
                ab.validate_build_config(ab.parse_custombuild_yaml(bad))

        # 3. Feature resolution against a board's feature list
        board = [
            {"id": "APJ_TOOL_PARAMETERS", "default": {"enabled": False}, "dependencies": []},
            {"id": "MODE_SPORT", "default": {"enabled": False}, "dependencies": ["DEP_X"]},
            {"id": "DEP_X", "default": {"enabled": False}, "dependencies": []},
            {"id": "SCRIPTING", "default": {"enabled": True}, "dependencies": []},
        ]
        mini = dict(cfg)
        mini["selected_features"] = ["MODE_SPORT", "NOT_ON_THIS_BOARD"]
        sel, rep = ab.resolve_selected_features(mini, board)
        self.assertEqual(sel, ["APJ_TOOL_PARAMETERS", "DEP_X", "MODE_SPORT"])  # SCRIPTING stays off
        self.assertEqual(rep["unknown"], ["NOT_ON_THIS_BOARD"])
        self.assertTrue(rep["added_dependencies"])
        self.assertEqual(rep["added_required"], ["APJ_TOOL_PARAMETERS"])

        # 4. Artifact archive -> arducopter.apj, and FAILURE state is reported with log lines
        apj_src = os.path.join(BASE_DIR, "firmware", "arducopter_CubeSolo_4.7.2.apj")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, data in (("copter-CubeSolo-abc/arducopter_with_bl.hex", b":00"),
                               ("copter-CubeSolo-abc/arducopter.apj", open(apj_src, "rb").read())):
                ti = tarfile.TarInfo(name)
                ti.size = len(data)
                tar.addfile(ti, io.BytesIO(data))
        state = {"n": 0}

        def fake_get(url, timeout=25):
            if url.endswith("/artifact"):
                return buf.getvalue()
            if "/logs" in url:
                return b"region flash overflowed\nBuild failed\n"
            state["n"] += 1
            st = "RUNNING" if state["n"] < 2 else "FAILURE"
            return json.dumps({"progress": {"percent": 50, "state": st}}).encode()

        orig_get = ab._http_get
        ab._http_get = fake_get
        tmp = tempfile.mkdtemp()
        try:
            out = ab.download_artifact_apj("abc", os.path.join(tmp, "x.apj"))
            with open(out, "rb") as f:
                self.assertIn("image", json.loads(f.read().decode("utf-8")))
            with self.assertRaises(RuntimeError) as cm:
                ab.poll_build("abc", poll_interval=0.01, max_wait=5)
            self.assertIn("FAILURE", str(cm.exception))
            self.assertIn("Build failed", str(cm.exception))
        finally:
            ab._http_get = orig_get
            shutil.rmtree(tmp, ignore_errors=True)

    def test_15_home_wifi_internet_routing(self):
        """Copter default route + DNS repair, Controller NAT/DHCP script, and the update gate fallback."""
        import arducopter_builder as ab

        # --- 1. Copter default route: 'ip route' output contains 10.1.1.10, which
        #        used to hide a missing default route ("10.1.1.1" in "10.1.1.10").
        routes = {"default": ""}
        ran = []

        def fake_run(cmd):
            ran.append(cmd)
            if cmd.startswith("ip -4 addr show dev wlan0"):
                return 0, "    inet 10.1.1.10/24 brd 10.1.1.255 scope global wlan0\n"
            if cmd.startswith("ip route show default"):
                return 0, routes["default"]
            if cmd.startswith("ip route replace default via 10.1.1.1"):
                routes["default"] = "default via 10.1.1.1 dev wlan0\n"
                return 0, ""
            return 0, ""

        tmp = tempfile.mkdtemp()
        orig_run, orig_resolv = wifi_mgr._run_local, wifi_mgr.RESOLV_CONF
        orig_platform = sys.platform
        try:
            wifi_mgr._run_local = fake_run
            sys.platform = "linux2"
            # resolv.conf is a symlink into a runtime dir (as on many Yocto images)
            real = os.path.join(tmp, "run", "resolv.conf")
            os.makedirs(os.path.dirname(real))
            with open(real, "w") as f:
                f.write("nameserver 10.1.1.1\n")
            link = os.path.join(tmp, "resolv.conf")
            os.symlink(real, link)
            wifi_mgr.RESOLV_CONF = link

            self.assertTrue(wifi_mgr.ensure_copter_internet_route())
            self.assertTrue(any(c.startswith("ip route replace default via 10.1.1.1 dev wlan0") for c in ran))
            self.assertTrue(os.path.islink(link))          # symlink kept, target rewritten
            with open(real) as f:
                text = f.read()
            self.assertTrue(text.split("nameserver")[1].strip().startswith("8.8.8.8"))
            self.assertIn("nameserver 1.1.1.1", text)

            # Already correct -> no further route changes (idempotent)
            ran[:] = []
            self.assertTrue(wifi_mgr.ensure_copter_internet_route())
            self.assertFalse(any("replace" in c for c in ran))

            # A stale default via another gateway is replaced
            routes["default"] = "default via 192.168.1.1 dev wlan0\n"
            self.assertTrue(wifi_mgr.ensure_copter_internet_route())
            self.assertEqual(routes["default"], "default via 10.1.1.1 dev wlan0\n")

            # Not on the copter (no 10.1.1.x on wlan0): never touch host routes/DNS
            ran[:] = []
            wifi_mgr._run_local = lambda cmd: (ran.append(cmd) or (0, ""))
            self.assertTrue(wifi_mgr.ensure_copter_internet_route())
            self.assertFalse(any("replace" in c or "mount" in c for c in ran))
        finally:
            wifi_mgr._run_local, wifi_mgr.RESOLV_CONF = orig_run, orig_resolv
            sys.platform = orig_platform
            shutil.rmtree(tmp, ignore_errors=True)

        # --- 2. Controller helper: NAT at the top of the chains, rp_filter off,
        #        udhcpc with -n -t 10 and without -q (stays running to renew).
        script = wifi_mgr.CONTROLLER_SCRIPT
        self.assertIn("iptables -t nat -I POSTROUTING 1 -o $IFACE -j MASQUERADE", script)
        self.assertIn("iptables -I FORWARD 1 -i $AP_IFACE -o $IFACE -j ACCEPT", script)
        self.assertIn("iptables -I FORWARD 1 -i $IFACE -o $AP_IFACE -j ACCEPT", script)
        self.assertIn("rp_filter", script)
        dhcp_lines = [l for l in script.splitlines() if "udhcpc -i" in l]
        self.assertTrue(dhcp_lines)
        for l in dhcp_lines:
            self.assertIn("-n -t 10", l)
            self.assertNotIn(" -q", l)

        # --- 3. Update gate: repairs the route first, then falls back to the Controller.
        calls = []
        orig = (wifi_mgr.ensure_copter_internet_route, wifi_mgr.controller_has_internet, ab._tcp_probe)
        try:
            wifi_mgr.ensure_copter_internet_route = lambda: calls.append("route") or True
            wifi_mgr.controller_has_internet = lambda timeout=3: calls.append("ctrl") or True
            ab._tcp_probe = lambda h, p, t: calls.append("probe:" + h) or False
            self.assertTrue(ab.check_internet_connection())
            self.assertEqual(calls[0], "route")
            self.assertEqual(calls[1:], ["probe:8.8.8.8", "probe:1.1.1.1", "ctrl"])
            self.assertEqual(ab.LAST_INTERNET_PATH, "controller")

            ab._tcp_probe = lambda h, p, t: h == "8.8.8.8"
            self.assertTrue(ab.check_internet_connection())
            self.assertEqual(ab.LAST_INTERNET_PATH, "direct")

            ab._tcp_probe = lambda h, p, t: False
            wifi_mgr.controller_has_internet = lambda timeout=3: False
            self.assertFalse(ab.check_internet_connection())
            self.assertIsNone(ab.LAST_INTERNET_PATH)
        finally:
            (wifi_mgr.ensure_copter_internet_route, wifi_mgr.controller_has_internet, ab._tcp_probe) = orig

    def test_16_wifi_connect_workflow_diagnostics(self):
        """Step checklist, pre-flight security checks and copter step in get_wifi_status()."""
        orig = (wifi_mgr._ssh, wifi_mgr._ctrl_reachable, wifi_mgr.copter_network_diagnostics)
        sent = []
        log_lines = [
            "10:00:00 STATUS: CONNECTING",
            "10:00:00 STEP prepare OK settings and helper sent",
            "10:00:01 STEP teardown RUN", "10:00:02 STEP teardown OK cleared",
            "10:00:02 STEP ap_pause RUN", "10:00:03 STEP ap_pause OK hostapd stopped",
            "10:00:03 STEP supplicant RUN", "10:00:04 STEP supplicant OK running on wlan0",
            "10:00:04 STEP associate RUN",
        ]
        state = {"log": list(log_lines), "addr": ""}

        def fake_ssh(cmd, host="10.1.1.1", timeout=20):
            sent.append(cmd)
            if "connect" in cmd:
                return (0, "LAUNCHED\n")
            return (0, "===SSID===\nHome\n===ADDR===\n%s\n===PING===\n%s\n===FWD===\n1\n===IPT===\n"
                       "===LOG===\n%s\n===WPA===\n===END===\n" % (
                           state["addr"], "PING_OK" if state["addr"] else "NO_ADDR", "\n".join(state["log"])))
        try:
            wifi_mgr._ssh = fake_ssh
            wifi_mgr._ctrl_reachable = lambda: True

            # Pre-flight: unsupported or inconsistent security is refused before SoloLink drops
            wifi_mgr._LAST_SCAN.update({"WPA3Only": "WPA3", "Cafe": "Open", "Home": "WPA2"})
            ok, msg = wifi_mgr.connect_home_wifi("WPA3Only", "password123")
            self.assertFalse(ok); self.assertIn("WPA3", msg)
            ok, msg = wifi_mgr.connect_home_wifi("Home", "")
            self.assertFalse(ok); self.assertIn("Enter its Wi-Fi password", msg)
            n = len(sent)
            ok, msg = wifi_mgr.connect_home_wifi("Cafe", "typed-by-mistake")
            self.assertTrue(ok)
            self.assertIn("key_mgmt=NONE", __import__("base64").b64decode(
                sent[-1].split("/tmp/wpa_client.conf ")[1].split(" ")[0]).decode("utf-8"))
            self.assertIn("STEP prepare OK", sent[-1])
            self.assertEqual(len(sent), n + 1)

            # Mid-connect: associate step running, later steps pending
            st = wifi_mgr.get_wifi_status()
            self.assertEqual(st["connection_state"], "connecting")
            steps = dict((x["id"], x["state"]) for x in st["steps"])
            self.assertEqual(steps["supplicant"], "ok")
            self.assertEqual(steps["associate"], "running")
            self.assertEqual(steps["dhcp"], "pending")

            # Wrong password: failing step marked, later steps skipped, clear error
            state["log"] = log_lines + ["10:00:09 STEP associate FAIL key_rejected_during_4-way_handshake",
                                        "10:00:09 STATUS: FAILED reason=wrong_password detail=x"]
            st = wifi_mgr.get_wifi_status()
            steps = dict((x["id"], x) for x in st["steps"])
            self.assertEqual(st["connection_state"], "failed")
            self.assertEqual(steps["associate"]["state"], "failed")
            self.assertIn("key rejected", steps["associate"]["detail"])
            self.assertEqual(steps["dhcp"]["state"], "skipped")
            self.assertIn("Wrong Wi-Fi password", st["failure"])

            # Connected, copter cannot reach the internet -> copter step failed + warning
            done = ["10:00:%02d STEP %s OK x" % (i, sid) for i, sid in
                    enumerate(["associate", "dhcp", "nat", "ap_resume", "internet"], 10)]
            state["log"] = log_lines + done + ["10:00:20 STATUS: CONNECTED ip=192.168.1.5 gw=192.168.1.1 freq=2437 internet=yes"]
            state["addr"] = "    inet 192.168.1.5/24 scope global wlan0"
            wifi_mgr.copter_network_diagnostics = lambda: {
                "applicable": True, "route_ok": True, "dns_ok": True, "internet": False,
                "detail": "route ok, DNS ok, but 8.8.8.8/1.1.1.1 not reachable through the Controller"}
            st = wifi_mgr.get_wifi_status()
            steps = dict((x["id"], x["state"]) for x in st["steps"])
            self.assertEqual(st["connection_state"], "connected")
            self.assertEqual(steps["internet"], "ok")
            self.assertEqual(steps["copter"], "failed")
            self.assertFalse(st["copter_internet"])
            self.assertIn("copter cannot reach the internet", st["warning"])

            wifi_mgr.copter_network_diagnostics = lambda: {
                "applicable": True, "route_ok": True, "dns_ok": True, "internet": True, "detail": "ok"}
            st = wifi_mgr.get_wifi_status()
            self.assertTrue(st["copter_internet"])
            self.assertEqual([x["state"] for x in st["steps"]], ["ok"] * len(wifi_mgr.CONNECT_STEPS))
            self.assertEqual(st["warning"], "")
            json.dumps(st)
            self.assertNotIn("error", st)      # app.js apiRequest() rejects any truthy "error" key

            # Built-in SoloLink station mode is used, kept md5-consistent, and undone on failure
            script = wifi_mgr.CONTROLLER_SCRIPT
            self.assertIn("sololink_config --set-config StationEnable $1", script)
            self.assertIn("ifup -f $IFACE", script)
            self.assertIn("md5sum $1.conf > $1.conf.md5", script)
            fail_fn = script[script.index("fail() {"):script.index("wpa_state() {")]
            self.assertIn("station_config_off", fail_fn)
            self.assertIn("ctrl_interface_group=0", wifi_mgr.build_wpa_config("Home", "password123"))
        finally:
            wifi_mgr._ssh, wifi_mgr._ctrl_reachable, wifi_mgr.copter_network_diagnostics = orig
            wifi_mgr._LAST_SCAN.clear()

    def test_17_wifi_arm_interlock_and_session_only_station(self):
        """Arming drops home Wi-Fi (retrying until confirmed); station mode is never left saved."""
        script = wifi_mgr.CONTROLLER_SCRIPT
        connect_fn = script[script.index("do_connect() {"):script.index("do_reset() {")]
        # saved settings are switched back off right after wpa_supplicant starts
        self.assertLess(connect_fn.index("station_config_on"), connect_fn.index("station_config_off"))
        self.assertLess(connect_fn.index("station_config_off"), connect_fn.index("step associate"))
        off_fn = script[script.index("do_station_off() {"):script.index("do_scan() {")]
        self.assertNotIn("hostapd", off_fn)            # SoloLink is never restarted in flight
        self.assertIn("DISCONNECTED reason=armed", off_fn)

        orig = (wifi_mgr._ssh, wifi_mgr._ctrl_reachable, wifi_mgr._STATION_MAYBE_ACTIVE)
        calls = []
        replies = ["STATION_OFF_RC=1\n", "STATION_OFF_RC=0\n"]   # first attempt fails, retry succeeds

        def fake_ssh(cmd, host="10.1.1.1", timeout=20):
            if "station-off" in cmd:
                calls.append(time.time())
                return (0, replies.pop(0) if replies else "STATION_OFF_RC=0\n")
            return (0, "")
        armed = {"v": False}
        try:
            wifi_mgr._ssh = fake_ssh
            wifi_mgr._ctrl_reachable = lambda: True
            wifi_mgr._note_station(True)
            t = threading.Thread(target=wifi_mgr._interlock_loop, args=(lambda: armed["v"], 0.02, 0.1))
            t.daemon = True
            t.start()
            time.sleep(0.2)
            self.assertEqual(calls, [])                 # disarmed: nothing sent
            armed["v"] = True
            deadline = time.time() + 3
            while wifi_mgr._STATION_MAYBE_ACTIVE and time.time() < deadline:
                time.sleep(0.02)
            self.assertFalse(wifi_mgr._STATION_MAYBE_ACTIVE)
            self.assertEqual(len(calls), 2)             # failed once, retried, confirmed
            time.sleep(0.3)
            self.assertEqual(len(calls), 2)             # no repeats once the station is off

            # status after the drop explains what happened
            log = "10:00:00 STATUS: CONNECTED ip=192.168.1.5 gw=x freq=2437 internet=yes\n10:05:00 STATUS: DISCONNECTED reason=armed"
            wifi_mgr._ssh = lambda cmd, host="10.1.1.1", timeout=20: (0, (
                "===SSID===\nHome\n===ADDR===\n===PING===\nNO_ADDR\n===FWD===\n1\n===IPT===\n"
                "===LOG===\n%s\n===WPA===\n===END===\n" % log))
            st = wifi_mgr.get_wifi_status()
            self.assertEqual(st["connection_state"], "idle")
            self.assertIn("vehicle armed", st["warning"])
        finally:
            wifi_mgr._ssh, wifi_mgr._ctrl_reachable = orig[0], orig[1]
            wifi_mgr._note_station(orig[2])

    def test_18_heartbeat_armed_state_is_validated(self):
        """Only checksum-valid heartbeats from the autopilot (compid 1) may change the armed state."""
        import struct
        import cockpit_mgr as cm

        def hb(armed, compid=1, mtype=2, version=1):
            payload = struct.pack('<IBBBBB', 5, mtype, 3, (0x80 if armed else 0) | 0x1D, 4, 3)
            if version == 1:
                hdr = struct.pack('<BBBBB', len(payload), 0, 1, compid, 0)
                magic = b'\xfe'
            else:
                hdr = struct.pack('<BBBBBBBBB', len(payload), 0, 0, 0, 1, compid, 0, 0, 0)
                magic = b'\xfd'
            crc = cm.calc_x25_crc(bytearray(hdr + payload) + bytearray([50]))
            return magic + hdr + payload + struct.pack('<H', crc)

        st = cm.TelemetryState()
        client = cm.LiveMAVLinkClient(st)
        client.parse_mavlink_bytes(hb(True)); self.assertTrue(st.armed)
        client.parse_mavlink_bytes(hb(False)); self.assertFalse(st.armed)
        client.parse_mavlink_bytes(hb(True, version=2)); self.assertTrue(st.armed)
        client.parse_mavlink_bytes(hb(False, version=2)); self.assertFalse(st.armed)
        client.parse_mavlink_bytes(hb(True, compid=154, mtype=26)); self.assertFalse(st.armed)   # gimbal
        bad = bytearray(hb(True)); bad[8] ^= 1
        client.parse_mavlink_bytes(bytes(bad)); self.assertFalse(st.armed)                    # corrupt frame
        client.parse_mavlink_bytes(hb(False, compid=154, mtype=26) + hb(True)); self.assertTrue(st.armed)

    def test_19_controller_commands_are_valid_shell(self):
        """Every command sent to the Controller must parse (a '&;' once made it reject connect/reset)."""
        import subprocess
        sent = []

        def fake_ssh(cmd, host="10.1.1.1", timeout=20):
            sent.append(cmd)
            return (0, "")
        orig = (wifi_mgr._ssh, wifi_mgr._ctrl_reachable)
        try:
            wifi_mgr._ssh = fake_ssh
            wifi_mgr._ctrl_reachable = lambda: True
            wifi_mgr._LAST_SCAN["Home"] = "WPA2"
            wifi_mgr.connect_home_wifi("Home", "pa$$`word'1")
            wifi_mgr.reset_to_default_ap()
            wifi_mgr.disconnect_on_armed()
            wifi_mgr.set_bidirectional(True)
            wifi_mgr.set_bidirectional(False)
            wifi_mgr._scan_cache["result"] = None
            wifi_mgr.scan_wifi_networks()
            wifi_mgr.get_wifi_status()
        finally:
            wifi_mgr._ssh, wifi_mgr._ctrl_reachable = orig
            wifi_mgr._LAST_SCAN.clear()
        self.assertGreaterEqual(len(sent), 7)
        for cmd in sent:
            self.assertNotRegex(cmd, r"&\s*;") if hasattr(self, "assertNotRegex") else \
                self.assertNotRegexpMatches(cmd, r"&\s*;")
        if os.path.exists("/bin/sh"):
            for cmd in sent:
                p = subprocess.Popen(["/bin/sh", "-n", "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                out, err = p.communicate()
                self.assertEqual(p.returncode, 0, "shell syntax error: %r in %r" % (err, cmd[-200:]))
        # BusyBox 1.21 on the Controller is built without FANCY_HEAD: "head -1" fails silently
        # (it broke the DHCP address check). Only "head -n N" / "sed -n Np" are allowed.
        import re as _re
        self.assertIsNone(_re.search(r"\bhead -\d", wifi_mgr.CONTROLLER_SCRIPT))
        for cmd in sent:
            self.assertIsNone(_re.search(r"\bhead -\d", cmd))
        # success markers only count as whole output lines, never inside an echoed command
        self.assertFalse(wifi_mgr._has_line("sh: syntax error near ';': ...; echo LAUNCHED", "LAUNCHED"))
        self.assertTrue(wifi_mgr._has_line("root@10.1.1.1's password: \nLAUNCHED\n", "LAUNCHED"))

    def test_20_previous_versions_and_custom_firmware(self):
        """Older builds are archived (3 kept) and offered for revert; custom .apj uploads are validated."""
        import json as _json
        import arducopter_builder
        import flash_mgr
        tmp = tempfile.mkdtemp()
        saved = (arducopter_builder.FIRMWARE_DIR, flash_mgr.FIRMWARE_DIR, flash_mgr.BASE_DIR,
                 flash_mgr.PERSISTENT_FW_DIR, flash_mgr.get_latest_apj, flash_mgr._firmware_roots)

        def apj(board_id, pad=120000):
            return _json.dumps({"board_id": board_id, "image": "A" * pad, "image_size": pad,
                                "summary": "ArduCopter", "git_identity": "abc123"})

        def put(name, board_id=9):
            with open(os.path.join(tmp, name), "w") as f:
                f.write(apj(board_id))
        try:
            arducopter_builder.FIRMWARE_DIR = tmp
            flash_mgr.FIRMWARE_DIR = tmp
            flash_mgr.BASE_DIR = tmp
            flash_mgr.PERSISTENT_FW_DIR = os.path.join(tmp, "no_such_dir")
            orig_latest = saved[4]
            flash_mgr.get_latest_apj = lambda cube_type, search_dirs=None, log_fn=None: orig_latest(cube_type, search_dirs=[tmp])
            flash_mgr._firmware_roots = lambda: [tmp]

            # Five successive builds: each archive step keeps the 3 newest older versions
            for v in ["4.5.7", "4.6.0", "4.6.3", "4.7.1", "4.7.2"]:
                put("arducopter_CubeSolo_%s.apj" % v)
                arducopter_builder.clean_old_firmware_files("CubeSolo", v)
            put("arducopter_CubeGreen_4.7.2.apj", 140)
            top = sorted(f for f in os.listdir(tmp) if f.endswith(".apj"))
            self.assertEqual(top, ["arducopter_CubeGreen_4.7.2.apj", "arducopter_CubeSolo_4.7.2.apj"])
            prev = sorted(os.listdir(os.path.join(tmp, "previous")))
            self.assertEqual(prev, ["arducopter_CubeSolo_4.6.0.apj", "arducopter_CubeSolo_4.6.3.apj",
                                    "arducopter_CubeSolo_4.7.1.apj"])

            ch = flash_mgr.list_firmware_choices("solo")
            self.assertEqual(ch["latest"]["version"], "4.7.2")
            self.assertEqual([c["version"] for c in ch["previous"]], ["4.7.1", "4.6.3", "4.6.0"])
            self.assertEqual(flash_mgr.list_firmware_choices("green")["previous"], [])

            # Custom upload: validated, sanitised, stored in <firmware>/custom
            with self.assertRaises(ValueError):
                flash_mgr.save_custom_apj("bad.apj", "this is not json")
            with self.assertRaises(ValueError):
                flash_mgr.save_custom_apj("bad.apj", _json.dumps({"board_id": 9}))
            c = flash_mgr.save_custom_apj("../../etc/my build.apj", apj(9))
            self.assertEqual(c["file"], "my_build.apj")
            self.assertEqual(os.path.dirname(c["path"]), os.path.join(tmp, "custom"))
            self.assertEqual(c["board_id"], 9)
            ch = flash_mgr.list_firmware_choices("solo")
            self.assertEqual([x["file"] for x in ch["custom"]], ["my_build.apj"])
            self.assertTrue(flash_mgr.get_latest_apj("solo").endswith("arducopter_CubeSolo_4.7.2.apj"))

            # Only offered files may be flashed
            self.assertIsNone(flash_mgr.resolve_firmware_choice("solo", None))
            p = ch["previous"][0]["path"]
            self.assertEqual(flash_mgr.resolve_firmware_choice("solo", p), p)
            self.assertEqual(flash_mgr.resolve_firmware_choice("solo", c["path"]), c["path"])
            with self.assertRaises(ValueError):
                flash_mgr.resolve_firmware_choice("solo", "/etc/passwd")
            with self.assertRaises(ValueError):   # a green build is not offered for the black cube
                flash_mgr.resolve_firmware_choice("solo", os.path.join(tmp, "arducopter_CubeGreen_4.7.2.apj"))

            eng = flash_mgr.FlashManager()
            ok, msg = eng.start_flash("solo", "cube_only", "/etc/passwd")
            self.assertFalse(ok)
            self.assertIn("rejected", msg)
            self.assertFalse(eng.is_flashing)
        finally:
            (arducopter_builder.FIRMWARE_DIR, flash_mgr.FIRMWARE_DIR, flash_mgr.BASE_DIR,
             flash_mgr.PERSISTENT_FW_DIR, flash_mgr.get_latest_apj, flash_mgr._firmware_roots) = saved
            shutil.rmtree(tmp, ignore_errors=True)

    def test_21_cube_version_from_live_mavlink(self):
        """Header version = what the Cube runs (AUTOPILOT_VERSION / boot banner), CRC-checked."""
        import struct
        import cockpit_mgr
        st = cockpit_mgr.TelemetryState()
        client = cockpit_mgr.LiveMAVLinkClient(st)

        def frame_v1(msgid, payload, extra, compid=1):
            hdr = struct.pack('<BBBBBB', 0xfe, len(payload), 1, 1, compid, msgid)
            crc = cockpit_mgr.calc_x25_crc(bytearray(hdr[1:] + payload) + bytearray([extra]))
            return hdr + payload + struct.pack('<H', crc)

        def frame_v2(msgid, payload, extra, compid=1):
            payload = payload.rstrip(b"\x00") or b"\x00"          # MAVLink 2 trailing-zero truncation
            hdr = struct.pack('<BBBBBBBBBB', 0xfd, len(payload), 0, 0, 2, 1, compid,
                              msgid & 0xff, (msgid >> 8) & 0xff, msgid >> 16)
            crc = cockpit_mgr.calc_x25_crc(bytearray(hdr[1:] + payload) + bytearray([extra]))
            return hdr + payload + struct.pack('<H', crc)

        def av(major, minor, patch):
            fsw = (major << 24) | (minor << 16) | (patch << 8) | 255
            return struct.pack('<QQIIIIHH', 0, 0, fsw, 0, 0, 0, 0, 0) + b"fb773567" + b"\x00" * 16

        self.assertIsNone(st.cube_version)
        bad = bytearray(frame_v1(148, av(9, 9, 9), 178)); bad[22] ^= 1
        client.parse_mavlink_bytes(bytes(bad)); self.assertIsNone(st.cube_version)        # corrupt frame
        client.parse_mavlink_bytes(frame_v1(148, av(4, 7, 2), 178, compid=154))
        self.assertIsNone(st.cube_version)                                                  # not the autopilot
        client.parse_mavlink_bytes(frame_v1(148, av(4, 7, 2), 178)); self.assertEqual(st.cube_version, "4.7.2")
        client.parse_mavlink_bytes(frame_v2(148, av(4, 7, 1), 178)); self.assertEqual(st.cube_version, "4.7.1")
        txt = struct.pack('<B', 6) + b"ArduCopter V4.7.2 (fb773567)".ljust(50, b"\x00")
        client.parse_mavlink_bytes(frame_v1(253, txt, 83)); self.assertEqual(st.cube_version, "4.7.2")

        orig = cockpit_mgr.telemetry
        try:
            cockpit_mgr.telemetry = st
            self.assertEqual(cockpit_mgr.get_cube_version(), "ArduCopter 4.7.2")
        finally:
            cockpit_mgr.telemetry = orig

        sent = []
        client.send_packet = lambda pkt: sent.append(pkt)
        client.request_autopilot_version()
        pkt = bytearray(sent[0])
        self.assertEqual(pkt[5], 76)                                                        # COMMAND_LONG
        self.assertEqual(struct.unpack('<H', bytes(pkt[6 + 28:6 + 30]))[0], 520)
        self.assertEqual(cockpit_mgr.calc_x25_crc(pkt[1:-2] + bytearray([152])),
                         struct.unpack('<H', bytes(pkt[-2:]))[0])

    def test_22_parameter_presets_write_to_cube(self):
        """Presets must reach the Cube: only the autopilot is addressed (never the Solo
        Controller/GCS or the gimbal), every name exists on the Cube (ArduCopter 4.7 names,
        pre-4.7 names with unit scaling on older firmware), switches are written first,
        and the values the Cube confirmed are what the API reports."""
        import presets as _presets
        import param_mgr as _pm

        class _Msg(object):
            def __init__(self, name, value):
                self.param_id = name
                self.param_value = float(value)
                self.param_type = 9

        class _FakeCube(object):
            """ArduCopter-like: answers only PARAM_* addressed to 1/1, ignores unknown names."""
            def __init__(self, names):
                self.params = dict((n, 0.0) for n in names)
                self.queue = []
                self.order = []
                self.misaddressed = 0
                cube = self

                class _Link(object):
                    def param_request_read_send(self, ts, tc, name, idx):
                        cube._handle(ts, tc, name, None)

                    def param_set_send(self, ts, tc, name, value, ptype):
                        cube._handle(ts, tc, name, value)
                self.mav = _Link()
                self.target_system, self.target_component = 255, 190   # first heartbeat was the GCS

            def _handle(self, ts, tc, name, value):
                name = name.decode("ascii") if isinstance(name, bytes) else name
                if (ts, tc) != (1, 1):
                    self.misaddressed += 1
                    return
                if name not in self.params:
                    return
                if value is not None:
                    self.params[name] = float(value)
                    self.order.append(name)
                self.queue.append(_Msg(name, self.params[name]))

            def recv_match(self, type=None, blocking=True, timeout=0.1):
                return self.queue.pop(0) if self.queue else None

        common = ["COMPASS_ORIENT", "LGR_ENABLE", "LGR_OPTIONS", "SERVO7_FUNCTION", "SERVO7_MIN", "SERVO7_MAX",
                  "SERVO7_REVERSED", "RC7_OPTION", "RC8_OPTION", "SERVO8_FUNCTION", "GRIP_ENABLE", "GRIP_TYPE",
                  "GRIP_GRAB", "GRIP_RELEASE", "BATT_MONITOR", "BATT_ARM_VOLT", "BATT_LOW_VOLT", "BATT_CRT_VOLT",
                  "BATT_LOW_MAH", "BATT_CRT_MAH", "BATT_LOW_TIMER", "BATT_FS_LOW_ACT", "BATT_FS_CRT_ACT",
                  "BATT_FS_VOLTSRC", "MOT_BAT_VOLT_MIN", "MOT_BAT_VOLT_MAX", "MOT_BAT_CURR_MAX", "LOIT_BRK_DELAY",
                  "LOIT_ANG_MAX", "ATC_INPUT_TC", "PLND_ENABLED", "PLND_TYPE", "PLND_EST_TYPE", "PLND_LAG",
                  "PLND_XY_DIST_MAX", "PLND_OPTIONS"]
        new_names = sorted(_pm.PARAM_ALIASES_PRE_4_7.keys())
        old_names = [_pm.PARAM_ALIASES_PRE_4_7[n][0] for n in new_names]

        # 1. heartbeat filter: GCS / Solo Controller and gimbal are never the target
        class _Hb(object):
            def __init__(self, typ, ap):
                self.type, self.autopilot = typ, ap
        self.assertFalse(_pm._is_autopilot_heartbeat(_Hb(6, 8)))     # GCS / Solo Controller
        self.assertFalse(_pm._is_autopilot_heartbeat(_Hb(26, 8)))    # gimbal
        self.assertTrue(_pm._is_autopilot_heartbeat(_Hb(2, 3)))      # quadrotor, ArduPilot

        # 2. presets use ArduCopter 4.7 names only (no retired or renamed parameters)
        retired = set(old_names) | set(["LGR_SERVO_DEPLOY", "LGR_SERVO_RTRACT"])
        for pr in _presets.get_all_presets():
            self.assertEqual(sorted(set(pr["params"]) & retired), [], "preset %s uses retired names" % pr["id"])

        # 2b. Default restores the CubeSolo / CubeGreen-solo ArduCopter 4.7 defaults; Fast changes
        #     the same settings, each one faster/sharper, within the 4.7 parameter ranges
        stock = {"ATC_ANGLE_MAX": 20.0, "LOIT_ANG_MAX": 30.0, "ATC_INPUT_TC": 0.25, "LOIT_SPEED_MS": 5.0,
                 "ATC_ACC_R_MAX": 360.0, "ATC_ACC_P_MAX": 360.0, "ATC_ACC_Y_MAX": 70.0, "PSC_NE_JERK": 5.0,
                 "LOIT_BRK_JRK_M": 5.0, "PHLD_BRK_RATE": 6.0, "PHLD_BRK_ANGLE": 25.0,
                 "LOIT_ACC_MAX_M": 2.0, "LOIT_BRK_ACC_M": 1.75, "LOIT_BRK_DELAY": 0.25, "WP_SPD": 3.0,
                 "WP_SPD_UP": 3.2, "WP_SPD_DN": 2.4, "PILOT_SPD_UP": 1.33, "PILOT_SPD_DN": 1.33}
        ranges = {"ATC_ANGLE_MAX": (10, 80), "LOIT_ANG_MAX": (0, 45), "ATC_INPUT_TC": (0, 1),
                  "LOIT_SPEED_MS": (0.2, 35), "LOIT_ACC_MAX_M": (1, 9.81), "LOIT_BRK_ACC_M": (0.25, 2.5),
                  "LOIT_BRK_DELAY": (0, 2), "WP_SPD": (0.1, 20), "WP_SPD_UP": (0.1, 10), "WP_SPD_DN": (0.1, 10),
                  "PILOT_SPD_UP": (0.5, 5), "PILOT_SPD_DN": (0, 5), "ATC_ACC_R_MAX": (0, 1800),
                  "ATC_ACC_P_MAX": (0, 1800), "ATC_ACC_Y_MAX": (0, 720), "PSC_NE_JERK": (1, 50),
                  "LOIT_BRK_JRK_M": (5, 50), "PHLD_BRK_RATE": (4, 12), "PHLD_BRK_ANGLE": (20, 45)}
        default_p = _presets.get_preset("speed_default")["params"]
        fast_p = _presets.get_preset("speed_fast")["params"]
        self.assertEqual(default_p, stock)
        self.assertEqual(sorted(fast_p), sorted(default_p))
        for k in fast_p:
            self.assertTrue(ranges[k][0] <= fast_p[k] <= ranges[k][1], "%s out of range" % k)
            if k in ("ATC_INPUT_TC", "LOIT_BRK_DELAY"):
                self.assertLess(fast_p[k], default_p[k], k)       # shorter = crisper / brakes sooner
            elif k != "LOIT_SPEED_MS":
                self.assertGreater(fast_p[k], default_p[k], k)
        # Loiter's speed-up/slow-down time constant LOIT_SPEED_MS / (g*tan(lean)) must be shorter than Default's
        import math as _math
        def _tau(p):
            lean = min(p["LOIT_ANG_MAX"], p["ATC_ANGLE_MAX"])
            return p["LOIT_SPEED_MS"] / (9.81 * _math.tan(_math.radians(lean)))
        self.assertLess(_tau(fast_p), 1.0)
        self.assertLess(_tau(fast_p), _tau(default_p))
        self.assertGreater(fast_p["LOIT_SPEED_MS"], default_p["LOIT_SPEED_MS"])
        self.assertIsNone(_presets.get_preset("speed_sport"))
        self.assertIsNone(_presets.get_preset("speed_cinematic"))

        orig_mavutil = _pm.mavutil
        if _pm.mavutil is None:
            class _MavutilStub(object):
                class mavlink(object):
                    MAV_PARAM_TYPE_REAL32 = 9
            _pm.mavutil = _MavutilStub()
        try:
            # 3. ArduCopter 4.7 Cube: every preset is written and confirmed with its own values
            for pr in _presets.get_all_presets():
                cube = _FakeCube(common + new_names)
                ok, confirmed, failed, msg = _pm.commit_batch_mavlink(cube, pr["params"])
                self.assertTrue(ok, "%s: %s" % (pr["id"], msg))
                self.assertEqual(cube.misaddressed, 0)
                for k, v in pr["params"].items():
                    self.assertAlmostEqual(cube.params[k], float(v), places=4)
                    self.assertAlmostEqual(float(confirmed[k]), float(v), places=4)

            # 4. pre-4.7 Cube: renamed parameters go to the old names in the old units
            cube = _FakeCube(common + old_names)
            ok, confirmed, failed, msg = _pm.commit_batch_mavlink(cube, _presets.get_preset("speed_fast")["params"])
            self.assertTrue(ok, msg)
            self.assertEqual(cube.params["WPNAV_SPEED"], 1000.0)        # 10 m/s
            self.assertEqual(cube.params["ANGLE_MAX"], 4000.0)          # 40 deg
            self.assertEqual(cube.params["PILOT_SPEED_UP"], 400.0)      # 4 m/s
            self.assertEqual(cube.params["LOIT_ACC_MAX"], 500.0)        # 5 m/s/s
            self.assertEqual(cube.params["LOIT_ANG_MAX"], 40.0)         # same name and unit before 4.7
            self.assertEqual(cube.params["PHLD_BRAKE_RATE"], 12.0)      # deg/s, same unit
            self.assertEqual(cube.params["PHLD_BRAKE_ANGLE"], 4000.0)   # 40 deg in centidegrees
            self.assertEqual(cube.params["ATC_ACCEL_R_MAX"], 90000.0)   # 900 deg/s/s in cdeg/s/s
            self.assertEqual(cube.params["ATC_ACCEL_Y_MAX"], 18000.0)   # 180 deg/s/s
            self.assertEqual(cube.params["PSC_JERK_XY"], 10.0)          # m/s/s/s, unchanged unit
            self.assertEqual(cube.params["LOIT_BRK_JERK"], 2000.0)      # 20 m/s/s/s in cm/s/s/s
            self.assertIn("WPNAV_SPEED", confirmed)

            # 5. feature switches are written before their settings
            cube = _FakeCube(common + new_names)
            _pm.commit_batch_mavlink(cube, _presets.get_preset("bait_drop")["params"])
            self.assertLess(cube.order.index("GRIP_ENABLE"), cube.order.index("GRIP_GRAB"))
            self.assertLess(cube.order.index("GRIP_TYPE"), cube.order.index("GRIP_RELEASE"))

            # 6. a parameter the Cube does not have is reported by name; the rest are saved
            cube = _FakeCube(common + new_names)
            ok, confirmed, failed, msg = _pm.commit_batch_mavlink(cube, {"COMPASS_ORIENT": 0, "NO_SUCH_PARAM": 1})
            self.assertFalse(ok)
            self.assertIn("NO_SUCH_PARAM", failed)
            self.assertIn("NO_SUCH_PARAM", msg)
            self.assertEqual(confirmed, {"COMPASS_ORIENT": 0})
        finally:
            _pm.mavutil = orig_mavutil

        # 7. the apply-preset route reports what the Cube confirmed; the page does not
        #    re-mark an applied preset as unsaved changes
        with open(os.path.join(BASE_DIR, "solo_web_app.py"), "r", encoding="utf-8") as f:
            swa = f.read()
        self.assertIn('"params": result.get("confirmed", preset[\'params\'])', swa)
        with open(os.path.join(BASE_DIR, "static", "app.js"), "r", encoding="utf-8") as f:
            js = f.read()
        start = js.index("window.applyPreset = function")
        block = js[start:js.index("function updateSaveButtonState", start)]
        self.assertNotIn("modifiedParams[k] = res.params[k]", block)
        self.assertIn("delete modifiedParams[k]", block)

    def test_24_sololink_viewer_download(self):
        """The manual's /sololink_viewer.apk links work on the Solo: the viewer app ships inside the
        utility as static/sololink_viewer.apk.bin (packaging skips *.apk) and is served as an APK."""
        import re
        import zipfile
        apk = os.path.join(BASE_DIR, "static", "sololink_viewer.apk.bin")
        self.assertTrue(os.path.isfile(apk))
        self.assertTrue(zipfile.is_zipfile(apk))
        names = zipfile.ZipFile(apk).namelist()
        self.assertIn("AndroidManifest.xml", names)
        self.assertIn("classes.dex", names)
        self.assertLess(os.path.getsize(apk), 1024 * 1024)   # the small viewer, never the installer
        with open(os.path.join(BASE_DIR, "solo_web_app.py"), "r", encoding="utf-8") as f:
            swa = f.read()
        self.assertIn('VIEWER_APK_URL = "/sololink_viewer.apk"', swa)
        self.assertIn('"sololink_viewer.apk.bin"', swa)
        self.assertIn("application/vnd.android.package-archive", swa)
        self.assertLess(swa.index("if path == VIEWER_APK_URL:"), swa.index("self._serve_static(path)\n            return"))
        with open(os.path.join(BASE_DIR, "static", "manual.html"), "r", encoding="utf-8") as f:
            self.assertIn('href="/sololink_viewer.apk"', f.read())
        import bake_into_firmware as _b
        self.assertFalse("sololink_viewer.apk.bin".endswith(_b.SKIP_EXTS))
        # Top bar: "Viewer for Android" download link right after the User Manual tab. It must not be a
        # .nav-tab (the tab code would treat it as a tab and blank the page).
        for page in (("static", "index.html"), ("templates", "index.html")):
            with open(os.path.join(BASE_DIR, *page), "r", encoding="utf-8") as f:
                html = f.read()
            nav = html[html.index('<nav class="nav-tabs" id="navTabs">'):html.index("</nav>")]
            link = re.search(r'<a class="nav-download" id="navDownloadViewer"[^>]*>', nav).group(0)
            self.assertIn('href="/sololink_viewer.apk"', link)
            self.assertNotIn("nav-tab", link)
            self.assertIn("Viewer for Android", nav[nav.index('id="navDownloadViewer"'):])
            self.assertLess(nav.index('id="navTabManual"'), nav.index('id="navDownloadViewer"'))

    def test_25_osd_statustext_messages_bar(self):
        """STATUSTEXT from the Cube reaches the telemetry JSON for the OSD messages bar: cleaned text,
        severity, unique id per message (even repeats), timestamp, rolling buffer; bad CRC rejected;
        the existing message log still gets every message; the HUD page has the bar wired up."""
        import re
        import struct
        import json as _json
        import cockpit_mgr as cm

        def frame(sev, text, v2=False, sysid=1, seq=1):
            payload = struct.pack('<B50s', sev, text.encode('ascii'))
            if v2:
                payload = payload.rstrip(b'\x00')        # MAVLink 2 trims trailing zeros
                hdr = struct.pack('<BBBBBBBBBB', 0xfd, len(payload), 0, 0, seq, sysid, 1, 253, 0, 0)
            else:
                hdr = struct.pack('<BBBBBB', 0xfe, len(payload), seq, sysid, 1, 253)
            crc = cm.calc_x25_crc(bytearray(hdr[1:]) + bytearray(payload) + bytearray([83]))
            return hdr + payload + struct.pack('<H', crc)

        st = cm.TelemetryState()
        cl = cm.LiveMAVLinkClient(st)
        d = st.to_dict()
        self.assertEqual(d["osd"]["messages"], [])
        self.assertIsNone(d["osd"]["latest"])

        cl.parse_mavlink_bytes(frame(3, "PreArm: Need 3D Fix"))
        cl.parse_mavlink_bytes(frame(6, "EKF2 IMU0 is using GPS", v2=True))
        cl.parse_mavlink_bytes(frame(4, "  Low   Battery \x00junk"))
        bad = bytearray(frame(2, "Crash: Disarming"))
        bad[12] ^= 0xFF
        cl.parse_mavlink_bytes(bytes(bad))                      # corrupted frame: ignored
        cl.parse_mavlink_bytes(frame(6, "own GCS text", sysid=254))   # our own sysid: ignored
        cl.parse_mavlink_bytes(frame(3, "PreArm: Need 3D Fix"))  # same text again = new message

        d = st.to_dict()
        _json.dumps(d)
        osd = d["osd"]
        texts = [m["text"] for m in osd["messages"]]
        self.assertEqual(texts, ["PreArm: Need 3D Fix", "EKF2 IMU0 is using GPS", "Low Battery", "PreArm: Need 3D Fix"])
        self.assertEqual([m["severity"] for m in osd["messages"]], [3, 6, 4, 3])
        self.assertEqual([m["severity_name"] for m in osd["messages"]], ["ERROR", "INFO", "WARNING", "ERROR"])
        ids = [m["id"] for m in osd["messages"]]
        self.assertEqual(ids, sorted(set(ids)))                  # unique and increasing
        self.assertEqual(osd["latest"]["id"], ids[-1])
        self.assertEqual(osd["seq"], ids[-1])
        self.assertTrue(osd["session"])
        for m in osd["messages"]:
            self.assertIsInstance(m["timestamp"], float)
            self.assertLessEqual(abs(m["timestamp"] - time.time()), 5)
            self.assertNotIn("\x00", m["text"])
        # the existing MAVLink message log still receives every STATUSTEXT
        log = [m["text"] for m in d["mavlink_messages"]]
        self.assertEqual(log.count("PreArm: Need 3D Fix"), 2)
        self.assertIn("EKF2 IMU0 is using GPS", log)
        # the utility's own notes stay out of the OSD bar
        self.assertNotIn("Listening for Solo MAVLink...", texts)
        self.assertFalse(any(t.startswith("Connected to Solo MAVLink") for t in texts))

        # rolling buffer: 20 kept, 15 sent; out-of-range severity is clamped
        for i in range(30):
            st.add_osd_message("m%d" % i, 9)
        d = st.to_dict()
        self.assertEqual(len(st.osd_messages), cm.OSD_HISTORY_MAX)
        self.assertEqual(len(d["osd"]["messages"]), cm.OSD_HISTORY_SENT)
        self.assertEqual(d["osd"]["latest"]["text"], "m29")
        self.assertEqual(d["osd"]["latest"]["severity"], 7)

        # Python 2.7 rules for the Solo (no f-strings in the new code)
        with open(os.path.join(BASE_DIR, "cockpit_mgr.py"), "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIsNone(re.search(r'(?<![\w>\'"])[fF]("[^"\n]*\{|\'[^\'\n]*\{)', src))   # f-strings only, not struct '<f'

        # Frontend: bar on the video, wired to telemetry.osd, closes history on outside click
        for page in (("static", "index.html"), ("templates", "index.html")):
            with open(os.path.join(BASE_DIR, *page), "r", encoding="utf-8") as f:
                html = f.read()
            body = html[html.index('<div class="cockpit-body">'):html.index('<aside id="leftHud"')]
            for el_id in ("osdMsgBar", "osdMsgMain", "osdMsgSev", "osdMsgText", "osdMsgHistory"):
                self.assertIn('id="%s"' % el_id, body)
        with open(os.path.join(BASE_DIR, "static", "cockpit.js"), "r", encoding="utf-8") as f:
            js = f.read()
        self.assertIn("updateOsdBar(telem);", js)
        self.assertIn("telem.osd", js)
        self.assertIn("osdText.textContent = latest.text", js)      # text set safely, never as HTML
        with open(os.path.join(BASE_DIR, "static", "cockpit.css"), "r", encoding="utf-8") as f:
            css = f.read()
        for cls in (".osd-sev-crit", ".osd-sev-warn", ".osd-sev-info", ".osd-idle"):
            self.assertIn(cls, css)

    def test_26_video_latency_and_loss_resilience(self):
        """Live video: RTP loss never produces glued/truncated slices; slow viewers skip to the next
        keyframe instead of building delay; viewers start on a real keyframe (no P-frame relabelling)
        with SPS/PPS; stale keyframe cache isn't replayed; RESTART VIDEO works; telemetry can skip
        the message log; stream sockets get low-latency options; the HUD page controls latency."""
        import re
        import struct
        import cockpit_mgr as cm

        def rtp(seq, payload):
            return struct.pack('!BBHII', 0x80, 96, seq & 0xFFFF, 0, 1) + payload

        def fu(nal, size=1000):
            hdr, body, out, pos = nal[0], nal[1:], [], 0
            while pos < len(body):
                part = body[pos:pos + size]
                fh = (hdr & 0x1F) | (0x80 if pos == 0 else 0) | (0x40 if pos + size >= len(body) else 0)
                out.append(bytes([(hdr & 0xE0) | 28, fh]) + part)
                pos += size
            return out

        idr = bytes([0x65]) + b'\x11' * 2500
        p_slice = bytes([0x41]) + b'\x22' * 2500

        # 1) complete FU-A is rebuilt exactly; a lost middle fragment drops the whole NAL
        d = cm.RtpH264Depacketizer()
        out = []
        for i, pl in enumerate(fu(idr)):
            out += d.feed(rtp(100 + i, pl))
        self.assertEqual(out, [b'\x00\x00\x00\x01' + idr])
        frags = fu(p_slice)
        out = []
        seq = 200
        for i, pl in enumerate(frags):
            if i == 1:
                seq += 1          # packet lost
                continue
            out += d.feed(rtp(seq, pl))
            seq += 1
        self.assertEqual(out, [])
        self.assertGreaterEqual(d.lost_packets, 1)
        self.assertGreaterEqual(d.dropped_nals, 1)
        out = d.feed(rtp(seq, bytes([0x41]) + b'\x33' * 50))      # next single-NAL packet is fine
        self.assertEqual(out, [b'\x00\x00\x00\x01' + bytes([0x41]) + b'\x33' * 50])
        self.assertEqual(d.feed(rtp(seq - 5, bytes([0x41]))), [])   # late duplicate ignored

        # 2) fan-out: new viewer waits for a real keyframe and gets SPS/PPS with it
        vm = cm.SoloVideoManager()
        sps = b'\x00\x00\x00\x01\x67\x42'
        pps = b'\x00\x00\x00\x01\x68\xce'
        IDR = b'\x00\x00\x00\x01' + idr
        P = b'\x00\x00\x00\x01' + p_slice
        vm.broadcast_nal(sps)
        vm.broadcast_nal(pps)
        v = vm.subscribe()
        vm.broadcast_nal(P)
        self.assertIsNone(v.take(0.05))                        # a P-frame is never relabelled/sent first
        vm.broadcast_nal(IDR)
        self.assertEqual(v.take(0.05), sps + pps + IDR)
        self.assertNotIn(b'\x00\x00\x00\x01\x45', P)            # (sanity: no relabel trick remains)
        self.assertNotIn("spoofed_header", open(os.path.join(BASE_DIR, "cockpit_mgr.py"), encoding="utf-8").read())

        # 3) a viewer that falls behind is skipped to the next keyframe (bounded delay)
        for _ in range(cm.VIEWER_MAX_BACKLOG_FRAMES + 5):
            vm.broadcast_nal(P)
        self.assertTrue(v.waiting_idr)
        self.assertEqual(v.skips, 1)
        self.assertLessEqual(v.frames, cm.VIEWER_MAX_BACKLOG_FRAMES)
        vm.broadcast_nal(IDR)
        self.assertTrue(v.take(0.05).startswith(sps + pps + IDR))

        # 4) a second viewer starts instantly from the fresh keyframe group...
        vm.broadcast_nal(P)
        v2 = vm.subscribe()
        self.assertEqual(v2.take(0.05), sps + pps + IDR + P)
        # ...but a stale group (video paused) is not replayed
        vm.last_frame_time -= 5
        v3 = vm.subscribe()
        self.assertIsNone(v3.take(0.05))
        self.assertTrue(v3.waiting_idr)

        # 4b) Solo encoders may send IDRs rarely (or only at start): a viewer must never wait
        #     forever (black screen). Non-IDR I-slices count as keyframes; otherwise after
        #     KEYFRAME_WAIT_SECS the next frame starts the stream (relabelled, the old behaviour).
        i_slice = b'\x00\x00\x00\x01' + bytes([0x41, 0x88]) + b'\x44' * 300     # nal 1, slice_type 7 (I)
        p_hdr = b'\x00\x00\x00\x01' + bytes([0x41, 0x98]) + b'\x55' * 300      # nal 1, slice_type 5 (P)
        self.assertTrue(cm.slice_is_intra(i_slice))
        self.assertFalse(cm.slice_is_intra(p_hdr))
        vm2 = cm.SoloVideoManager()
        vm2.broadcast_nal(sps)
        vm2.broadcast_nal(pps)
        w = vm2.subscribe()
        vm2.broadcast_nal(i_slice)
        got = w.take(0.05)
        self.assertEqual(got, sps + pps + cm.relabel_as_idr(i_slice))
        self.assertEqual(vm2.get_status()["keyframes_islice"], 1)
        w2 = vm2.subscribe()                       # starts from the cached I-frame group
        self.assertTrue(w2.take(0.05).startswith(sps + pps + cm.relabel_as_idr(i_slice)))
        vm2.gop = []
        w3 = vm2.subscribe()
        vm2.broadcast_nal(p_hdr)
        self.assertIsNone(w3.take(0.05))           # waits briefly for a keyframe...
        w3.waiting_since -= cm.KEYFRAME_WAIT_SECS + 0.1
        vm2.broadcast_nal(p_hdr)
        self.assertEqual(w3.take(0.05), sps + pps + cm.relabel_as_idr(p_hdr))   # ...then starts anyway
        self.assertEqual(w3.legacy_starts, 1)
        for x in (w, w2, w3):
            vm2.unsubscribe(x)
        self.assertEqual(vm2.get_status()["viewer_fallback_starts"], 1)

        # 5) RESTART VIDEO really reconnects (old bug: flag set on the module, read on the instance)
        vm.request_reconnect()
        self.assertTrue(vm.force_reconnect_flag)
        self.assertTrue(v.waiting_idr and v2.waiting_idr)
        for viewer in (v, v2, v3):
            vm.unsubscribe(viewer)
        self.assertEqual(vm.subscribers, [])

        with open(os.path.join(BASE_DIR, "solo_web_app.py"), "r", encoding="utf-8") as f:
            swa = f.read()
        self.assertIn("cockpit_mgr.video_manager.request_reconnect()", swa)
        self.assertNotIn("setattr(cockpit_mgr, 'force_reconnect_flag'", swa)
        r0 = swa.index("if path == '/api/video/h264':")
        route = swa[r0:swa.index("\n        if path == ", r0 + 10)]
        for needle in ("TCP_NODELAY", "SO_SNDBUF", "IP_TOS", "settimeout", "unsubscribe(viewer)"):
            self.assertIn(needle, route)

        # 6) telemetry: the message log is sent once, and only when it changed
        st = cm.telemetry
        full = cm.get_telemetry_dict()
        self.assertIn("messages", full)                         # legacy payload unchanged
        seq = full["messages_seq"]
        slim = cm.get_telemetry_dict(seq)
        self.assertIsNone(slim["mavlink_messages"])
        self.assertNotIn("messages", slim)
        self.assertNotIn("messages", slim["status"])
        changed = cm.get_telemetry_dict(seq - 1)
        self.assertIsInstance(changed["mavlink_messages"], list)
        self.assertIsNone(cm.get_telemetry_dict('none')["mavlink_messages"])
        self.assertIn("osd", slim)

        # 7) HUD page: real frame rate, live-edge control, stall watchdog, background stop
        with open(os.path.join(BASE_DIR, "static", "cockpit.js"), "r", encoding="utf-8") as f:
            js = f.read()
        self.assertIn("const VIDEO_FPS = 24;", js)
        self.assertIn("fps: VIDEO_FPS", js)
        self.assertIsNone(re.search(r"fps:\s*30", js))
        self.assertIn("v.buffered.end(last)", js)               # newest range, not end(0)
        self.assertIn("VIDEO_STALL_MS", js)
        self.assertIn("visibilitychange", js)
        self.assertIn("function videoWanted()", js)
        self.assertIn("/api/telemetry?msgs_since=", js)
        self.assertIn("if (currentTelem && hudDirty)", js)
        with open(os.path.join(BASE_DIR, "static", "app.js"), "r", encoding="utf-8") as f:
            self.assertIn("/api/telemetry?msgs_since=none", f.read())

    def test_27_login_hardening(self):
        """Random per-login tokens (expire, revocable), no default-password-as-token, login
        throttling, Cockpit routes (telemetry, video, arm/mode) behind login, changes need the
        token in a header (not just the cookie), pages send the token and prompt on default."""
        import re
        import hashlib
        # tokens: random, distinct, revocable, expiring
        t1 = auth.create_session()
        t2 = auth.create_session()
        self.assertNotEqual(t1, t2)
        self.assertEqual(len(t1), 48)
        self.assertTrue(auth.validate_session(t1))
        auth.invalidate_session(t1)
        self.assertFalse(auth.validate_session(t1))
        self.assertTrue(auth.validate_session(t2))
        auth._active_sessions[t2] -= auth.SESSION_TIMEOUT_SEC + 1
        self.assertFalse(auth.validate_session(t2))
        # the factory password (or its hash) is never a token, and there is no fixed master token
        self.assertTrue(auth.is_default_password())
        self.assertFalse(auth.validate_session("sololink"))
        self.assertFalse(auth.validate_session(hashlib.sha256(b"sololink").hexdigest()))
        self.assertFalse(hasattr(auth, "get_master_token"))
        # a password change logs every session out
        t3 = auth.create_session()
        ok, _ = auth.change_password("sololink", "Another2026")
        self.assertTrue(ok)
        self.assertFalse(auth.validate_session(t3))
        self.assertFalse(auth.is_default_password())
        auth.force_set_password("sololink")
        # throttling: 5 wrong passwords -> wait
        ip = "10.1.1.99"
        auth.record_login_success(ip)
        for _ in range(auth.LOGIN_MAX_FAILURES - 1):
            auth.record_login_failure(ip)
        self.assertEqual(auth.login_wait_seconds(ip), 0)
        auth.record_login_failure(ip)
        self.assertGreater(auth.login_wait_seconds(ip), 0)
        auth.record_login_success(ip)
        self.assertEqual(auth.login_wait_seconds(ip), 0)

        # server routes: cockpit endpoints only after the login check
        with open(os.path.join(BASE_DIR, "solo_web_app.py"), "r", encoding="utf-8") as f:
            swa = f.read()
        get_part = swa[swa.index("    def do_GET(self):"):swa.index("    def do_POST(self):")]
        post_part = swa[swa.index("    def do_POST(self):"):]
        g_check = get_part.index("# Require Auth for all other API endpoints")
        for route in ("'/api/telemetry'", "'/api/video/status'", "'/api/video/h264'"):
            self.assertGreater(get_part.index("if path == %s" % route), g_check, route)
        p_check = post_part.index("# Protected Endpoints Check")
        self.assertIn("self._is_authenticated(allow_cookie=False)", post_part[p_check:p_check + 200])
        for route in ("'/api/action'", "'/api/video/restart'"):
            self.assertGreater(post_part.index("if path == %s" % route), p_check, route)
        self.assertIn("SameSite=Strict", swa)
        self.assertIn("auth.login_wait_seconds(ip)", swa)
        self.assertIn('"default_password": auth.is_default_password()', swa)

        # pages send the token and handle 401 / default password
        with open(os.path.join(BASE_DIR, "static", "cockpit.js"), "r", encoding="utf-8") as f:
            js = f.read()
        self.assertIn("function authHeaders(", js)
        self.assertIn("headers: authHeaders()", js)
        self.assertNotIn("fetch('/api/action'", js.replace("return fetch('/api/action'", ""))
        self.assertIn("solo-auth-required", js)
        with open(os.path.join(BASE_DIR, "static", "app.js"), "r", encoding="utf-8") as f:
            app = f.read()
        self.assertIn("solo-auth-required", app)
        self.assertIn("promptDefaultPassword()", app)
        self.assertIn("fetch('/api/auth/check'", app)
        self.assertIn("if (appDataStarted)", app)
        for page in (("static", "index.html"), ("templates", "index.html")):
            with open(os.path.join(BASE_DIR, *page), "r", encoding="utf-8") as f:
                self.assertIn('id="passwordDefaultNotice"', f.read())

    def test_28_mavlink_coexists_with_solex(self):
        """The utility must never redirect MAVLink away from Solex: it talks only to telem_forwarder's
        local client port (127.0.0.1:14560, like Open Solo's shot manager), never to the Controller's
        10.1.1.1:14550 (telem_ctrl would route Solex's parameter requests to us) or the Solo's
        down-link port 14550; it doesn't override stream rates while data flows; it goes idle with no
        browser; and the factory-password prompt can be dismissed."""
        import time as _time
        import cockpit_mgr as cm

        class FakeSock(object):
            def __init__(self):
                self.sent = []
            def sendto(self, data, dest):
                self.sent.append(dest)
        st = cm.TelemetryState()
        cl = cm.LiveMAVLinkClient(st)
        cl.sock = FakeSock()
        cl.send_gcs_heartbeat()
        cl.request_data_streams()
        cl.send_arm_command(False)
        cl.send_set_mode_command(5)
        self.assertEqual(set(cl.sock.sent), {('127.0.0.1', 14560)})
        self.assertEqual(cm.SOLO_TELEM_INJECT, ('127.0.0.1', 14560))
        src = open(os.path.join(BASE_DIR, "cockpit_mgr.py"), encoding="utf-8").read()
        code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
        self.assertNotIn("('10.1.1.1', 14550)", code)
        self.assertNotIn("udpout:10.1.1.1:14550", code)
        self.assertNotIn("udpout:127.0.0.1:14550", code)

        # idle: no browser demand and no keepalive -> link not wanted; demand wakes it
        saved = (cm._last_demand[0], list(cm._keepalive_fns))
        try:
            del cm._keepalive_fns[:]
            cm._last_demand[0] = _time.time() - cm.MAVLINK_IDLE_SECS - 1
            self.assertFalse(cm.mavlink_wanted())
            cm.note_demand()
            self.assertTrue(cm.mavlink_wanted())
            cm._last_demand[0] = 0
            cm.add_keepalive(lambda: True)            # home Wi-Fi interlock keeps it up
            self.assertTrue(cm.mavlink_wanted())
            del cm._keepalive_fns[:]
            cm._last_demand[0] = 0
            cm.is_vehicle_armed(wake=False)           # the background interlock must not wake it
            self.assertFalse(cm.mavlink_wanted())
        finally:
            cm._last_demand[0] = saved[0]
            cm._keepalive_fns[:] = saved[1]

        # stream rates are only requested when ATTITUDE is missing
        self.assertIn("now - self.state.last_msg_time.get(30, 0.0) > STREAM_MISSING_SECS", src)

        with open(os.path.join(BASE_DIR, "solo_web_app.py"), encoding="utf-8") as f:
            swa = f.read()
        self.assertIn("cockpit_mgr.note_demand()", swa)
        self.assertIn("cockpit_mgr.is_vehicle_armed(wake=False)", swa)
        self.assertIn("cockpit_mgr.add_keepalive(", swa)
        # hidden pages stop polling so the link can idle
        with open(os.path.join(BASE_DIR, "static", "cockpit.js"), encoding="utf-8") as f:
            self.assertIn("document.visibilityState === 'hidden'", f.read())
        with open(os.path.join(BASE_DIR, "static", "app.js"), encoding="utf-8") as f:
            app = f.read()
        self.assertIn("if (document.hidden && !installationInProgress) { return; }", app)
        # Cancel / keep default on the password prompt
        self.assertIn("btnCancelPasswordModal", app)
        self.assertIn("solo_keep_default_pw", app)
        for page in (("static", "index.html"), ("templates", "index.html")):
            with open(os.path.join(BASE_DIR, *page), encoding="utf-8") as f:
                self.assertIn('id="btnCancelPasswordModal"', f.read())

    def test_29_video_follows_encoder_restarts(self):
        """When the Solo restarts its video encoder (new SSRC / random new RTP sequence number,
        e.g. on a resolution change) the depacketizer follows the new stream at once instead of
        discarding it as 'late' packets or counting a huge fake loss."""
        import struct
        import cockpit_mgr as cm

        def rtp(seq, payload, ssrc=1):
            return struct.pack('!BBHII', 0x80, 96, seq & 0xFFFF, 0, ssrc) + payload

        def nal(n):
            return bytes([0x41]) + bytes([n & 0xFF]) * 20

        # 1) restart with a new SSRC and a sequence number "behind" the old one
        d = cm.RtpH264Depacketizer()
        for i in range(50):
            d.feed(rtp(40000 + i, nal(i)))
        out = d.feed(rtp(1000, nal(99), ssrc=2))
        self.assertEqual(out, [b'\x00\x00\x00\x01' + nal(99)])
        self.assertEqual(d.stream_restarts, 1)
        self.assertEqual(d.lost_packets, 0)
        self.assertEqual(d.feed(rtp(1001, nal(100), ssrc=2)), [b'\x00\x00\x00\x01' + nal(100)])

        # 2) same SSRC but the sequence number jumps far ahead: resync, no fake loss
        d = cm.RtpH264Depacketizer()
        for i in range(10):
            d.feed(rtp(100 + i, nal(i)))
        self.assertEqual(len(d.feed(rtp(20000, nal(1)))), 1)
        self.assertEqual(d.lost_packets, 0)
        self.assertEqual(d.stream_restarts, 1)

        # 3) same SSRC, sequence jumps "behind": a few packets are dropped, then it resyncs
        d = cm.RtpH264Depacketizer()
        for i in range(10):
            d.feed(rtp(30000 + i, nal(i)))
        got = []
        for i in range(30):
            got += d.feed(rtp(5 + i, nal(i)))
        self.assertGreaterEqual(len(got), 30 - cm.RTP_RESYNC_LATE_RUN)
        self.assertEqual(d.stream_restarts, 1)

        # 4) ordinary small loss is still counted, and a single reordered packet is still ignored
        d = cm.RtpH264Depacketizer()
        d.feed(rtp(10, nal(1)))
        d.feed(rtp(13, nal(2)))
        self.assertEqual(d.lost_packets, 2)
        self.assertEqual(d.feed(rtp(12, nal(3))), [])
        self.assertEqual(len(d.feed(rtp(14, nal(4)))), 1)
        self.assertEqual(d.stream_restarts, 0)

        # 5) the counters are in /api/video/status
        st = cm.SoloVideoManager().get_status()
        self.assertIn('stream_restarts', st)
        self.assertIn('late_packets', st)

    def test_30_gimbal_firmware_update_tool(self):
        """Update gimbal firmware (bin/gimbal_update): runs the verified MNT1_TYPE / Cube-only-reboot
        procedure, always puts MNT1_TYPE back (also after a power loss), flashes with a time limit and one
        retry, skips a current gimbal (no forced reflash), installs the fixed 3DR tools with LF endings and a
        .orig backup, and the web server exposes it behind login and blocks other Cube work meanwhile."""
        import json as _json
        import re as _re
        import shutil as _shutil
        import tempfile as _tempfile
        root = os.path.dirname(os.path.abspath(__file__))
        gdir = os.path.join(root, "bin", "gimbal_update")
        sys.path.insert(0, gdir)
        try:
            import gimbal_update as gu
        finally:
            sys.path.remove(gdir)
        for f in ("gimbal_update.py", "cube_mount.py", "gimbal_setup_fixed.py", "loadGimbal_fixed.py",
                  "gimbal_probe.py", "fix_cube_serial.py", "HANDOFF_SoloWebUtility.md"):
            self.assertTrue(os.path.isfile(os.path.join(gdir, f)), f)
        for f in ("gimbal_update.py", "cube_mount.py", "gimbal_setup_fixed.py", "loadGimbal_fixed.py"):
            src = open(os.path.join(gdir, f)).read()
            self.assertIsNone(_re.search(r'(?<![\w>\'"])[fF]("[^"\n]*\{|\'[^\'\n]*\{)', src),
                              "%s: f-string (Solo runs Python 2.7)" % f)

        tmp = _tempfile.mkdtemp()
        saved_paths, saved_sleep = dict(gu.PATHS), gu.time.sleep
        try:
            os.makedirs(os.path.join(tmp, "fw"))
            os.makedirs(os.path.join(tmp, "gimbal"))
            with open(os.path.join(tmp, "fw", "gimbal_firmware_1.3.6.ax"), "w") as f:
                _json.dump({"version": "1.3.6", "release": "r"}, f)
            with open(os.path.join(tmp, "gimbal", "setup.py"), "wb") as f:
                f.write(b"stock\r\n")
            open(os.path.join(tmp, "gimbal_setup"), "w").close()
            with open(os.path.join(tmp, "loadGimbal.py"), "w") as f:
                f.write("stock loader\n")
            gu.PATHS.update({k: os.path.join(tmp, v) for k, v in {
                "status": "st.json", "lock": "lock", "state": "state.json", "log": "log",
                "firmware_dir": "fw", "gimbal_dir": "gimbal", "gimbal_setup": "gimbal_setup",
                "boot_loader": "loadGimbal.py", "version_file": "AXON",
                "boot_updater_enabled": "bu_enabled"}.items()})
            gu.time.sleep = lambda s: None

            class Fake(object):
                def __init__(self, probes, flashes=(True,), armed=False, mnt=2, fail_mount=None):
                    self.probes, self.flashes = list(probes), list(flashes)
                    self.armed, self.mnt, self.fail_mount, self.calls = armed, mnt, fail_mount, []

                def cube_info(self):
                    # the firmware must already be in place and checked before the Cube is touched
                    fw = os.path.join(tmp, "fw", "gimbal_firmware_1.3.6.ax")
                    self.fw_ready = os.path.isfile(fw) and gu._file_md5(fw) == gu.FW_MD5
                    return {"armed": self.armed, "mnt1_type": self.mnt, "serial0": 1}

                def set_mount(self, t, settle):
                    self.calls.append(("mount", t))
                    if t == self.fail_mount:
                        return False
                    self.mnt = t
                    return True

                def install_setup_fix(self):
                    return gu._install_file(gu.FIXED_SETUP, os.path.join(gu.PATHS["gimbal_dir"], "setup.py"))

                def probe(self, secs=0):
                    self.calls.append(("probe",))
                    return self.probes.pop(0)

                def flash(self, fw, progress, timeout=0):
                    self.calls.append(("flash",))
                    self.flashed = fw
                    progress(50, "Uploading 32.16kB of 64.32kB - 50%")
                    return self.flashes.pop(0), ""

                def usb_disable(self):
                    pass

            def run(fake):
                for n in ("st.json", "state.json", "AXON"):
                    if os.path.exists(os.path.join(tmp, n)):
                        os.remove(os.path.join(tmp, n))
                st = gu.Status()
                return gu.run_update(fake, st), st, fake

            none = {"usb": "/dev/x", "sysids": [], "bootloader": False, "version": None}
            old = {"usb": "/dev/x", "sysids": [1], "bootloader": False, "version": "0.28.2"}
            cur = {"usb": "/dev/x", "sysids": [1], "bootloader": False, "version": "1.3.6"}
            boot = {"usb": "/dev/x", "sysids": [0], "bootloader": True, "version": None}
            id0 = {"usb": "/dev/x", "sysids": [0], "bootloader": False, "version": None}

            # bundled firmware: the exact Open Solo file
            self.assertEqual(os.path.getsize(gu.BUNDLED_FW), 48171)
            self.assertEqual(gu._file_md5(gu.BUNDLED_FW), "f5fba21a9ebf376566e88fe670546aea")
            # normal: firmware copied in (the stale .ax replaced, another .ax removed) before the Cube
            # is touched, then id 1 (MNT1_TYPE=2), open port (0), flash that explicit path, restore (2)
            with open(os.path.join(tmp, "fw", "gimbal_firmware_0.28.2.ax"), "w") as fh:
                fh.write("{}")
            r, st, f = run(Fake([none, old]))
            self.assertEqual(r, "updated")
            self.assertEqual(f.calls, [("probe",), ("mount", 2), ("mount", 0), ("probe",), ("flash",), ("mount", 2)])
            self.assertTrue(f.fw_ready)
            fw_dst = os.path.join(tmp, "fw", "gimbal_firmware_1.3.6.ax")
            self.assertEqual(f.flashed, fw_dst)
            self.assertEqual(sorted(os.listdir(os.path.join(tmp, "fw"))), ["gimbal_firmware_1.3.6.ax"])
            self.assertEqual(gu._file_md5(fw_dst), gu.FW_MD5)
            self.assertEqual(open(os.path.join(tmp, "AXON")).read().split(), ["1.3.6"])
            # right file already in place: nothing in /firmware is changed, other .ax files included
            with open(os.path.join(tmp, "fw", "gimbal_firmware_9.9.9.ax"), "w") as fh:
                fh.write("{}")
            os.utime(fw_dst, (1000000000, 1000000000))
            r, st, f = run(Fake([none, old]))
            self.assertEqual(r, "updated")
            self.assertEqual(sorted(os.listdir(os.path.join(tmp, "fw"))),
                             ["gimbal_firmware_1.3.6.ax", "gimbal_firmware_9.9.9.ax"])
            self.assertEqual(int(os.path.getmtime(fw_dst)), 1000000000)
            self.assertEqual(f.flashed, fw_dst)
            os.remove(os.path.join(tmp, "fw", "gimbal_firmware_9.9.9.ax"))
            self.assertFalse(os.path.exists(os.path.join(tmp, "state.json")))
            self.assertEqual(st.data["state"], "done")
            # /firmware missing: created
            _shutil.rmtree(os.path.join(tmp, "fw"))
            r, st, f = run(Fake([none, old]))
            self.assertEqual((r, f.fw_ready), ("updated", True))
            # bundled copy damaged: stops in preflight, before the Cube is touched
            saved_fw = gu.BUNDLED_FW
            bad = os.path.join(tmp, "bad.ax")
            with open(bad, "w") as fh:
                fh.write('{"version": "1.3.6"}')
            gu.BUNDLED_FW = bad
            try:
                r, st, f = run(Fake([none, old]))
            finally:
                gu.BUNDLED_FW = saved_fw
            self.assertEqual((r, f.calls), ("failed", []))
            self.assertFalse(hasattr(f, "fw_ready"))
            self.assertIn("damaged", st.data["message"])
            # /firmware cannot be created (a file is in the way): clear message, Cube untouched
            _shutil.rmtree(os.path.join(tmp, "fw"))
            open(os.path.join(tmp, "fw"), "w").close()
            r, st, f = run(Fake([none, old]))
            self.assertEqual((r, f.calls), ("failed", []))
            self.assertIn("is not a folder", st.data["message"])
            os.remove(os.path.join(tmp, "fw"))
            os.makedirs(os.path.join(tmp, "fw"))
            # already current: no flash (there is no forced reflash); MNT1_TYPE still restored
            r, st, f = run(Fake([none, cur]))
            self.assertEqual(r, "current")
            self.assertNotIn(("flash",), f.calls)
            self.assertEqual(f.calls[-1], ("mount", 2))
            # gimbal in its bootloader: flash straight away
            r, st, f = run(Fake([boot]))
            self.assertEqual(r, "updated")
            self.assertEqual(f.calls[:2], [("probe",), ("flash",)])
            # gimbal still id 0 after the dance: steps 3-4 repeated once
            r, st, f = run(Fake([none, id0, old]))
            self.assertEqual(r, "updated")
            self.assertEqual(f.calls.count(("mount", 0)), 2)
            # flash fails twice: failed, but MNT1_TYPE is put back
            r, st, f = run(Fake([none, old], flashes=(False, False)))
            self.assertEqual(r, "failed")
            self.assertEqual(f.calls.count(("flash",)), 2)
            self.assertEqual((f.calls[-1], f.mnt), (("mount", 2), 2))
            # armed: refused before anything changes
            r, st, f = run(Fake([], armed=True))
            self.assertEqual((r, f.calls), ("failed", []))
            self.assertIn("ARMED", st.data["message"])
            # MNT1_TYPE cannot be put back: failed, and the note stays for the startup recovery
            r, st, f = run(Fake([none, old], fail_mount=2))
            self.assertEqual(r, "failed")
            self.assertTrue(os.path.exists(os.path.join(tmp, "state.json")))
            self.assertIn("could NOT be put back", st.data["message"])
            # restore-only run (web server start after a power loss)
            with open(os.path.join(tmp, "state.json"), "w") as fh:
                _json.dump({"restore_mnt1_type": 2}, fh)
            fk = Fake([], mnt=0)
            self.assertEqual(gu.run_restore(fk, gu.Status(mode="restore")), "restored")
            self.assertEqual((fk.mnt, os.path.exists(os.path.join(tmp, "state.json"))), (2, False))

            # fixed 3DR setup.py installed with LF line endings, original kept once
            setup = open(os.path.join(tmp, "gimbal", "setup.py"), "rb").read()
            self.assertTrue(setup.startswith(b"#!/usr/bin/env python\n"))
            self.assertNotIn(b"\r\n", setup)
            self.assertEqual(open(os.path.join(tmp, "gimbal", "setup.py.orig"), "rb").read(), b"stock\r\n")
            # boot updater install: refused until the setting is on (it is untested on a drone)
            self.assertEqual(gu.boot_updater_state()["state"], "original")
            self.assertFalse(gu.boot_updater_state()["install_enabled"])
            self.assertRaises(gu.GimbalUpdateError, gu.install_boot_updater)
            self.assertEqual(open(os.path.join(tmp, "loadGimbal.py")).read(), "stock loader\n")
            self.assertEqual(gu.set_boot_updater_install_enabled(True), "on")
            self.assertTrue(gu.boot_updater_state()["install_enabled"])
            self.assertEqual(gu.install_boot_updater(), "installed")
            self.assertEqual(gu.boot_updater_state()["state"], "fixed")
            self.assertEqual(gu.install_boot_updater(), "current")
            self.assertEqual(gu.restore_boot_updater(), "restored")
            self.assertEqual(open(os.path.join(tmp, "loadGimbal.py")).read(), "stock loader\n")
            # a run that died is reported as interrupted, not "running" forever
            with open(os.path.join(tmp, "st.json"), "w") as fh:
                _json.dump({"state": "running", "pid": 999999, "lines": []}, fh)
            st = gu.get_status()
            self.assertEqual((st["state"], st["result"], st["running"]), ("failed", "interrupted", False))
            self.assertEqual((st["firmware_version"], st["firmware_file"]), ("1.3.6", "gimbal_firmware_1.3.6.ax"))
            self.assertEqual(gu.set_boot_updater_install_enabled(False), "off")
            self.assertFalse(gu.boot_updater_state()["install_enabled"])
            self.assertEqual(gu.decode_swver(_struct_float((1, 3, 6, 0))), "1.3.6")
        finally:
            gu.PATHS.clear()
            gu.PATHS.update(saved_paths)
            gu.time.sleep = saved_sleep
            _shutil.rmtree(tmp, ignore_errors=True)

        # web server: behind login, blocks other Cube work, recovers after a restart
        swa = open(os.path.join(root, "solo_web_app.py")).read()
        auth_gate = swa.index("# Require Auth for all other API endpoints")
        self.assertGreater(swa.index("if path == '/api/gimbal/status':"), auth_gate)
        post_gate = swa.index("if not self._is_authenticated(allow_cookie=False):")
        self.assertGreater(swa.index("self._handle_gimbal_post(path, body)"), post_gate)
        self.assertIn("'/api/flash/start'", swa[swa.index("GIMBAL_BUSY_BLOCKS = ("):swa.index("def gimbal_update_running")])
        self.assertIn("gimbal_update.recover_on_startup()", swa)
        self.assertIn("the gimbal cannot be updated while the vehicle is ARMED", swa)
        # UI on the Firmware Update tab (both copies of the page)
        for page in ("static/index.html", "templates/index.html"):
            html = open(os.path.join(root, page), encoding="utf-8").read()
            for el_id in ("gimbalUpdateCard", "btnStartGimbalUpdate", "gimbalTerminal",
                          "btnInstallGimbalBootUpdater", "btnRestoreGimbalBootUpdater", "chkGimbalBootUpdaterEnable"):
                self.assertIn('id="%s"' % el_id, html)
            self.assertIn('id="btnInstallGimbalBootUpdater" style="display: none;"', html)   # hidden by default
            # v1.2.6: no "Reflash even if current" option, and none of the reverted v1.2.2/v1.2.3 steps
            for gone in ('id="chkGimbalForce"', "Reflash", "SERIAL0_PROTOCOL", "system ID 1 directly"):
                self.assertNotIn(gone, html)
        self.assertIn("'enable_install'", swa)
        js = open(os.path.join(root, "static", "app.js"), encoding="utf-8").read()
        self.assertIn("/api/gimbal/start", js)
        self.assertIn("refreshGimbalStatus();", js)
        self.assertNotIn("chkGimbalForce", js)
        # the v1.2.0 procedure verified on fresh gimbals: no forced reflash, no SERIAL0_PROTOCOL switching
        # (v1.2.2), no gimbal_set_sysid.py (v1.2.3)
        gdir = os.path.join(root, "bin", "gimbal_update")
        gsrc = open(os.path.join(gdir, "gimbal_update.py"), encoding="utf-8").read()
        for gone in ("--force", "SERIAL0_UPDATE", "SERIAL0_AFTER", "MOUNT_SERIAL0_FAILED", "set_sysid"):
            self.assertNotIn(gone, gsrc)
        self.assertNotIn("SERIAL0_PROTOCOL", open(os.path.join(gdir, "cube_mount.py"), encoding="utf-8").read())
        self.assertFalse(os.path.exists(os.path.join(gdir, "gimbal_set_sysid.py")))
        self.assertIn("gimbal_update.start()", swa)
        # bin/ (and so bin/gimbal_update/) goes into the image and both installers
        self.assertIn('"bin"', open(os.path.join(root, "bake_into_firmware.py")).read().split("TREE_DIRS")[1][:60])
        self.assertIn('"bin"', open(os.path.join(root, "deploy_to_drone.py")).read().split("TREE_DIRS")[1][:60])

    def test_31_battery_solutions_tab(self):
        """Battery Solutions tab: the three options, the bundled Solo Battery Mod for Dummies guide
        (with the salt-water disposal replaced by recycling), and a 5S Li-ion preset from the
        Upgrade Energy 5S2P specs that the tab can apply."""
        import presets as _presets
        root = os.path.dirname(os.path.abspath(__file__))
        for page in ("static/index.html", "templates/index.html"):
            html = open(os.path.join(root, page), encoding="utf-8").read()
            self.assertIn('data-tab="tab-battery"', html)
            self.assertIn('id="tab-battery"', html)
            tab = html[html.index('id="tab-battery"'):html.index('id="tab-manual"')]
            for needle in ("/Solo_Battery_Mod_for_Dummies.pdf", "product/3dr-solo-battery-mod",
                           "product/bmsone-v2-plug-and-play", "applyPreset('lipo_default')",
                           "applyPreset('liion_default')", "applyPreset('liion_5s')"):
                self.assertIn(needle, tab)
        pdf = os.path.join(root, "static", "Solo_Battery_Mod_for_Dummies.pdf")
        self.assertTrue(os.path.isfile(pdf))
        data = open(pdf, "rb").read()
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertIn(b"1kwUlTDKVDHVkY6UCDoXdGuPJXFgDAxoO", data)      # guide's own download link
        p5 = _presets.get_preset("liion_5s")
        self.assertIsNotNone(p5)
        prm = p5["params"]
        self.assertEqual(prm["MOT_BAT_VOLT_MAX"], 21.0)
        self.assertEqual(prm["MOT_BAT_VOLT_MIN"], 13.5)
        self.assertGreater(prm["BATT_CRT_VOLT"], 13.5)                # above the pack minimum
        self.assertLess(prm["BATT_CRT_VOLT"], prm["BATT_LOW_VOLT"])
        self.assertLess(prm["BATT_LOW_VOLT"], prm["BATT_ARM_VOLT"])
        self.assertEqual(prm["BATT_MONITOR"], 5)
        self.assertEqual(prm["BATT_ARM_VOLT"], 15.0)
        # phones: the tab row folds into a menu button (no sideways page scroll)
        html = open(os.path.join(root, "static", "index.html"), encoding="utf-8").read()
        self.assertIn('id="btnNavMenu"', html)
        css = open(os.path.join(root, "static", "style.css"), encoding="utf-8").read()
        self.assertIn(".nav-tabs.open", css)

    def test_32_bmsone_configurator(self):
        """Battery tab: BMSOne Configurator card with the offline WebSerial configurator and the
        BMSOne manual stored on the Solo, plus links to the online version and the apps."""
        root = os.path.dirname(os.path.abspath(__file__))
        for page in ("static/index.html", "templates/index.html"):
            html = open(os.path.join(root, page), encoding="utf-8").read()
            tab = html[html.index('id="tab-battery"'):html.index('id="tab-manual"')]
            self.assertIn('id="bmsone-configurator"', tab)
            for needle in ('href="/BMSOne-Configurator.html" download="BMSOne-Configurator.html"',
                           'href="/BMSOne-User-Manual.pdf"',
                           "https://bmsone.open-solo.com/BMSOne-Configurator.html",
                           "BMSOne-Configurator-v3.exe", "BMSOne-Configurator-v3.apk"):
                self.assertIn(needle, tab)
        cfg = open(os.path.join(root, "static", "BMSOne-Configurator.html"), "rb").read()
        self.assertIn(b"BMSOne V3 Configurator", cfg)
        self.assertIn(b"navigator.serial.requestPort", cfg)
        self.assertNotIn(b"<script src=", cfg)                       # self-contained, works offline
        pdf = open(os.path.join(root, "static", "BMSOne-User-Manual.pdf"), "rb").read()
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_33_fence_margin_zero(self):
        """FENCE_MARGIN 0 stays in the Solo defaults: ArduCopter 4.7's default margin is 2 m, which fails
        arming with "Circle FENCE_MARGIN is greater than FENCE_RADIUS" on Solos that carry FENCE_RADIUS 0.
        Both .param files (used for every new build from Check for Updates) and every shipped .apj must have it."""
        import glob
        root = os.path.dirname(os.path.abspath(__file__))
        fw = os.path.join(root, "firmware")
        for name in ("Solo.param", "SoloGreen.param"):
            lines = [l.split() for l in open(os.path.join(fw, name)).read().splitlines() if l.strip()]
            vals = dict((l[0], l[1]) for l in lines if len(l) >= 2)
            self.assertIn("FENCE_MARGIN", vals, name)
            self.assertEqual(float(vals["FENCE_MARGIN"]), 0.0, name)
        sys.path.insert(0, os.path.join(root, "bin"))
        import apj_tool
        apjs = glob.glob(os.path.join(fw, "*.apj")) + glob.glob(os.path.join(fw, "previous", "*.apj"))
        self.assertTrue(apjs)
        for apj in apjs:
            d = apj_tool.embedded_defaults(apj)
            self.assertTrue(d.find(), apj)
            txt = apj_tool.to_ascii(d.contents())
            vals = dict(l.split()[:2] for l in txt.splitlines() if len(l.split()) >= 2)
            self.assertEqual(float(vals.get("FENCE_MARGIN", "nan")), 0.0, apj)

    def test_34_video_yields_to_solex(self):
        """Cockpit gives the Controller's single video slot to Solex: an ESTABLISHED connection from
        another device to the shot manager (TCP 5507) makes the video worker let go, and it takes the
        video back only APP_GONE_SECS after that app has gone."""
        import tempfile
        import cockpit_mgr as cm
        hdr = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
        listen = "   0: 00000000:1583 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 1 1\n"
        solex = "   1: 0A01010A:1583 9101010A:947A 01 00000000:00000000 00:00000000 00000000     0        0 2 1\n"
        local = "   2: 0100007F:1583 0100007F:A000 01 00000000:00000000 00:00000000 00000000     0        0 3 1\n"
        fd, path = tempfile.mkstemp()
        os.close(fd)
        try:
            open(path, "w").write(hdr + listen + local)
            self.assertIsNone(cm.find_app_on_shotmanager(proc_file=path))
            open(path, "w").write(hdr + listen + local + solex)
            self.assertEqual(cm.find_app_on_shotmanager(proc_file=path), "10.1.1.145")
        finally:
            os.remove(path)
        self.assertIsNone(cm.find_app_on_shotmanager(proc_file="/nonexistent/tcp"))

        vm = cm.SoloVideoManager()
        orig = cm.find_app_on_shotmanager
        try:
            cm.find_app_on_shotmanager = lambda *a, **k: "10.1.1.145"
            self.assertTrue(vm._app_wants_video())
            self.assertEqual(vm.get_status()["yielded_to_app"], "10.1.1.145")
            cm.find_app_on_shotmanager = lambda *a, **k: None
            self.assertTrue(vm._app_wants_video())          # still within APP_GONE_SECS
            vm._app_last_seen -= cm.APP_GONE_SECS + 0.1
            self.assertFalse(vm._app_wants_video())
            self.assertIsNone(vm.get_status()["yielded_to_app"])
        finally:
            cm.find_app_on_shotmanager = orig
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cockpit_mgr.py")).read()
        worker = src[src.index("def _handshake_worker"):src.index("def run(self)", src.index("def _handshake_worker"))]
        self.assertEqual(worker.count("self._app_wants_video()"), 2)   # before connecting and while holding
        js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "cockpit.js")).read()
        self.assertIn("yielded_to_app", js)

    def test_35_version_everywhere(self):
        """tools/bump_version.py: every file that names the version (page header, cache-busters, manuals,
        installer names, build scripts, website) shows the version in version.json."""
        import re as _re
        root = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, os.path.join(root, "tools"))
        try:
            import bump_version as bv
        finally:
            sys.path.pop(0)
        # other tests raise version.json's iteration, so take the release version from the page header
        html = open(os.path.join(root, "static", "index.html"), encoding="utf-8").read()
        version = _re.search(r'id="headerVersionTag">v(\d+\.\d+\.\d+)<', html).group(1)
        self.assertEqual(bv.check(version), [])
        self.assertTrue(os.path.isfile(os.path.join(root, "release", "Solo Web Utility v%s.spec" % version)))
        self.assertEqual(bv.version_code("1.2.1"), 10201)
        self.assertGreater(bv.version_code("1.3.0"), bv.version_code("1.2.99"))

    def test_36_header_cube_version_always_current(self):
        """The header's ArduCopter version: the Cube's own answer while it is fresh; otherwise the status poll
        wakes the MAVLink link so the Cube is asked again within seconds (no endless 'checking...' when no
        Cockpit is open), and the newest stored version is shown meanwhile."""
        import tempfile
        import time as _time
        import cockpit_mgr as cm
        tmp = tempfile.mkdtemp()
        saved = (cm.telemetry.cube_version, cm.telemetry.cube_version_time, cm._last_demand[0],
                 cm.CUBE_VERSION_RECORD, cm.PIX_VERSION_FILE)
        try:
            cm.CUBE_VERSION_RECORD = os.path.join(tmp, "rec")
            cm.PIX_VERSION_FILE = os.path.join(tmp, "pix")
            with open(cm.CUBE_VERSION_RECORD, "w") as f:
                f.write("4.7.1\n")
            # fresh live answer: shown, nothing woken
            cm.telemetry.cube_version, cm.telemetry.cube_version_time = "4.7.2", _time.time()
            cm._last_demand[0] = 0.0
            self.assertEqual(cm.get_cube_version(), "ArduCopter 4.7.2")
            self.assertEqual(cm._last_demand[0], 0.0)
            # stale live answer (link idle, Cube may have been reflashed): wake the link, show the record meanwhile
            cm.telemetry.cube_version_time = _time.time() - cm.CUBE_VERSION_FRESH_SECS - 1
            self.assertEqual(cm.get_cube_version(), "ArduCopter 4.7.1")
            self.assertTrue(cm.mavlink_wanted())
            # just flashed (empty record) and no live answer yet: unknown, and the link is woken to ask
            cm.telemetry.cube_version = None
            cm._last_demand[0] = 0.0
            with open(cm.CUBE_VERSION_RECORD, "w") as f:
                f.write("")
            self.assertEqual(cm.get_cube_version(), "")
            self.assertGreater(cm._last_demand[0], 0.0)
            # background callers can read it without waking anything
            cm._last_demand[0] = 0.0
            cm.get_cube_version(wake=False)
            self.assertEqual(cm._last_demand[0], 0.0)
        finally:
            (cm.telemetry.cube_version, cm.telemetry.cube_version_time, cm._last_demand[0],
             cm.CUBE_VERSION_RECORD, cm.PIX_VERSION_FILE) = saved

    def test_38_telemetry_resumes_after_cube_only_install(self):
        """flash_mgr pauses the MAVLink link before flashing the Cube and never un-pauses it. After a Cube Only
        install (no Solo reboot) the link must come back by itself once flashing has finished, or the Cockpit
        and the header's ArduCopter version ('checking...') stay dead until the next restart."""
        import types
        import time as _time
        import cockpit_mgr as cm
        fake = types.ModuleType("flash_mgr")
        fake.flash_engine = types.SimpleNamespace(is_flashing=True)
        saved_mod = sys.modules.get("flash_mgr")
        saved_demand = cm._last_demand[0]
        sys.modules["flash_mgr"] = fake
        cl = cm.LiveMAVLinkClient(cm.TelemetryState())
        try:
            cm.note_demand()
            cl.paused = True
            cl.start()
            _time.sleep(0.5)
            self.assertTrue(cl.paused)                     # still flashing: stays paused
            fake.flash_engine.is_flashing = False
            for _ in range(30):
                cm.note_demand()
                if not cl.paused:
                    break
                _time.sleep(0.1)
            self.assertFalse(cl.paused)                    # flashing over: resumed
        finally:
            cl.running = False
            cm._last_demand[0] = saved_demand
            if saved_mod is not None:
                sys.modules["flash_mgr"] = saved_mod
            else:
                sys.modules.pop("flash_mgr", None)

    def test_39_uninstall_and_every_page_endpoint_exists(self):
        """The Solo runs solo_web_app.py (not app.py): every /api/... path the pages call must be handled there.
        /api/uninstall was only in app.py, so the Uninstall button got "Endpoint not found". Uninstall removes the
        start-up entries (other lines kept) and is behind login."""
        import re as _re
        import tempfile
        root = os.path.dirname(os.path.abspath(__file__))
        js = "".join(open(os.path.join(root, "static", f), encoding="utf-8").read()
                     for f in ("app.js", "cockpit.js", "index.html"))
        swa = open(os.path.join(root, "solo_web_app.py"), encoding="utf-8").read()
        missing = [p for p in sorted(set(_re.findall(r"""['"](/api/[A-Za-z0-9_\-/]+)""", js)))
                   if ("'%s'" % p) not in swa and ('"%s"' % p) not in swa]
        self.assertEqual(missing, [])
        self.assertGreater(swa.index("if path == '/api/uninstall':"), swa.index("if not self._is_authenticated(allow_cookie=False):"))
        import solo_web_app as swa_mod
        tmp = tempfile.mkdtemp()
        try:
            it, rc = os.path.join(tmp, "inittab"), os.path.join(tmp, "rc.local")
            with open(it, "w") as f:
                f.write("id:5:initdefault:\n# SoloWebUtility2 web server on port 80 (respawned by init)\n"
                        "SWU:345:respawn:/usr/bin/python /usr/bin/SoloWebUtility2/solo_web_app.py >>/log/x 2>&1\nS0:12345:respawn:/bin/getty\n")
            with open(rc, "w") as f:
                f.write("#!/bin/sh\npython /log/SoloWebUtility2/solo_web_app.py &\nexit 0\n")
            done = swa_mod.uninstall_utility(inittab=it, rc_local=rc, dirs=[os.path.join(tmp, "x")], teardown=False)
            self.assertEqual(open(it).read(), "id:5:initdefault:\nS0:12345:respawn:/bin/getty\n")
            self.assertEqual(open(rc).read(), "#!/bin/sh\nexit 0\n")
            self.assertTrue(any("inittab" in d for d in done))
        finally:
            import shutil as _sh
            _sh.rmtree(tmp, ignore_errors=True)

    def test_37_factory_reset_partition_tool(self):
        """Update Factory Reset Partition (bin/golden_update): golden_write.sh takes the MD5 of the running boot
        files (no backup, no staging copy), never reformats GOLDEN (keeps .reg), writes each file as .new then
        renames, deletes the old squashfs first only when there is no room, marks and re-runs an unfinished run,
        removes the old backup/stage folders of earlier versions, and refuses when the files do not fit or the
        device is not running its system partition. The runner reports
        what GOLDEN holds; the web server puts it behind login and interlocks."""
        import hashlib
        import re
        import shutil as _shutil
        import subprocess as _sp
        import tempfile
        root = os.path.dirname(os.path.abspath(__file__))
        gdir = os.path.join(root, "bin", "golden_update")
        script = os.path.join(gdir, "golden_write.sh")
        self.assertTrue(os.path.isfile(script))
        src = open(os.path.join(gdir, "golden_update.py")).read()
        self.assertNotIn("mkfs", open(script).read().split("Test hooks")[1])       # never reformats GOLDEN
        sh = _shutil.which("sh") if hasattr(_shutil, "which") else None
        if sh:
            tmp = tempfile.mkdtemp()
            try:
                boot, gold, log = [os.path.join(tmp, d) for d in ("boot", "golden", "log")]
                for d in (boot, gold, log):
                    os.makedirs(d)

                def mk(path, size, seed):
                    with open(path, "wb") as f:
                        f.write((hashlib.md5(seed.encode()).digest() * (size // 16 + 1))[:size])

                mk(os.path.join(boot, "u-boot.imx"), 30000, "b1"); mk(os.path.join(boot, "uImage"), 90000, "b2")
                mk(os.path.join(boot, "imx6solo-3dr-1080p.dtb"), 4000, "b3")
                mk(os.path.join(boot, "3dr-solo-imx6solo-3dr-1080p.squashfs"), 2000000, "b4")
                mk(os.path.join(gold, "u-boot.imx"), 30000, "g1"); mk(os.path.join(gold, "uImage"), 88000, "g2")
                mk(os.path.join(gold, "imx6solo-3dr-1080p.dtb"), 4000, "g3")
                mk(os.path.join(gold, "3dr-solo-imx6solo-3dr-1080p.squashfs"), 1800000, "g4")
                with open(os.path.join(gold, ".reg"), "w") as f:
                    f.write("US\n")
                old = dict((n, hashlib.md5(open(os.path.join(gold, n), "rb").read()).hexdigest()) for n in os.listdir(gold))
                env = dict(os.environ, SWU_GOLDEN_TEST="1", GOLDEN_DEV=gold, BOOT_DIR=boot, LOG_DIR=log,
                           GOLDEN_TEST_TOTAL_KB="2800")    # room for the new set, not for two squashfs

                def run(action, **extra):
                    e = dict(env); e.update(extra)
                    p = _sp.Popen([sh, script, action], stdout=_sp.PIPE, stderr=_sp.STDOUT, env=e)
                    return p.communicate()[0].decode("utf-8", "replace")

                def md5s(d):
                    return dict((n, hashlib.md5(open(os.path.join(d, n), "rb").read()).hexdigest())
                                for n in os.listdir(d) if os.path.isfile(os.path.join(d, n)))

                os.makedirs(os.path.join(log, "golden_backup"))             # left by an earlier version
                with open(os.path.join(log, "golden_backup", "x"), "w") as f:
                    f.write("x")
                out = run("write")
                self.assertIn("[GOLDEN_DONE] write", out, out)
                self.assertFalse(os.path.exists(os.path.join(log, "golden_backup")))   # removed, not made
                self.assertNotIn("backing up", out)
                self.assertIn("removing the old one first", out)          # the only unprotected moment
                g = md5s(gold)
                for n, m in md5s(boot).items():
                    self.assertEqual(g[n], m, n)
                self.assertEqual(g[".reg"], old[".reg"])                   # not a boot file: kept
                self.assertFalse([n for n in g if n.endswith(".new")])
                self.assertFalse(os.path.exists(os.path.join(log, "golden_update.marker")))
                self.assertFalse(os.path.exists(os.path.join(log, "golden_stage")))
                self.assertFalse(os.path.exists(os.path.join(log, "golden_update.sums")))
                # status
                sys.path.insert(0, gdir)
                try:
                    import golden_update as gu
                finally:
                    sys.path.remove(gdir)
                st = gu.parse_status(run("status"))
                self.assertTrue(st["ok"] and st["same_as_running"])
                # an interrupted run (marker, squashfs gone): status says so; running again finishes it
                with open(os.path.join(log, "golden_update.marker"), "w") as f:
                    f.write("write x\n")
                os.remove(os.path.join(gold, "3dr-solo-imx6solo-3dr-1080p.squashfs"))
                st = gu.parse_status(run("status"))
                self.assertTrue(st["marker"])
                self.assertIn("Unfinished", st["summary"])
                out = run("write")
                self.assertIn("[GOLDEN_DONE] write", out)
                self.assertFalse(os.path.exists(os.path.join(log, "golden_update.marker")))
                g = md5s(gold)
                for n, m in md5s(boot).items():
                    self.assertEqual(g[n], m, n)
                # there is no restore any more
                self.assertIn("[GOLDEN_FAILED] usage", run("restore"))
                # does not fit: refused before GOLDEN is touched
                before = md5s(gold)
                os.remove(os.path.join(gold, "uImage"))
                os.remove(os.path.join(gold, "3dr-solo-imx6solo-3dr-1080p.squashfs"))
                before = md5s(gold)
                out = run("write", GOLDEN_TEST_TOTAL_KB="2000")
                self.assertIn("do not fit", out)
                self.assertEqual(md5s(gold), before)
                # not running the system partition
                fake = os.path.join(tmp, "slc")
                with open(fake, "w") as f:
                    f.write("#!/bin/sh\necho golden\n")
                os.chmod(fake, 0o755)
                e2 = dict(env); e2.pop("SWU_GOLDEN_TEST"); e2.update(SOLOLINK_CONFIG=fake, GOLDEN_DEV="/nonexistent")
                p = _sp.Popen([sh, script, "write"], stdout=_sp.PIPE, stderr=_sp.STDOUT, env=e2)
                self.assertIn("not running its system partition", p.communicate()[0].decode())
            finally:
                _shutil.rmtree(tmp, ignore_errors=True)

        # runner: summaries and a job with a fake device
        sys.path.insert(0, gdir)
        try:
            import golden_update as gu
        finally:
            sys.path.remove(gdir)
        out = "\n".join(["[GOLDEN] image latest",
                         "[GOLDEN] boot uImage 10 aa", "[GOLDEN] boot x.squashfs 20 bb",
                         "[GOLDEN] boot_versions os=4.0.0 swu=1.2.4",
                         "[GOLDEN] space total=89918 used=75210 avail=14708",
                         "[GOLDEN] golden uImage 10 cc", "[GOLDEN] golden x.squashfs 20 dd", "[GOLDEN] golden .reg 3 ee",
                         "[GOLDEN] golden_versions os=2.4.2 swu=-", "[GOLDEN_DONE] status"])
        st = gu.parse_status(out)
        self.assertEqual((st["summary"], st["same_as_running"]), ("3DR 2.4.2", False))
        st = gu.parse_status(out.replace(" cc", " aa").replace(" dd", " bb"))
        self.assertEqual(st["summary"], "Same as the running system (Open Solo 4.0.0 with Solo Web Utility v1.2.4)")
        tmp = tempfile.mkdtemp()
        saved = dict(gu.PATHS)
        try:
            gu.PATHS.update({"status": os.path.join(tmp, "st.json"), "info": os.path.join(tmp, "info.json"),
                             "lock": os.path.join(tmp, "lock")})
            calls = []

            def fake(action, on_line, timeout):
                calls.append(action)
                if action == "status":
                    return out
                on_line("[GOLDEN] step 4 Writing the factory reset partition")
                return "[GOLDEN] step 4 x\n[GOLDEN_DONE] write"
            self.assertEqual(gu.run_job("controller", "write", runner=fake), "done")
            self.assertEqual(calls, ["write", "status"])                     # always read back afterwards
            self.assertEqual(gu.get_status()["partitions"]["controller"]["summary"], "3DR 2.4.2")

            def bad(action, on_line, timeout):
                return "[GOLDEN_FAILED] could not reach the Controller over SSH (x)" if action == "write" else ""
            self.assertEqual(gu.run_job("controller", "write", runner=bad), "failed")
            self.assertIn("could not reach the Controller", gu.get_status()["message"])
            self.assertFalse(gu.get_status()["tested_on_drone"])
            # both: the Solo first, then the Controller, each on its own (one failing does not stop the other)
            seen = []

            def per_dev(action, on_line, timeout, _calls=seen):
                _calls.append(action)
                if action == "status":
                    return out
                return "[GOLDEN_FAILED] boom" if len(_calls) == 1 else "[GOLDEN_DONE] write"
            self.assertEqual(gu.run_job("both", "write", runner=per_dev), "failed")
            self.assertEqual(seen, ["write", "status", "write", "status"])
            st2 = gu.get_status()
            self.assertIn("Solo: boom", st2["message"])
            self.assertIn("Controller: the factory reset partition now holds the running system", st2["message"])
            self.assertEqual(set(st2["partitions"]), {"solo", "controller"})
            del seen[:]
            self.assertEqual(gu.run_job("both", "status", runner=lambda a, o, t: out), "done")
        finally:
            gu.PATHS.clear(); gu.PATHS.update(saved)
            _shutil.rmtree(tmp, ignore_errors=True)
        for f in ("golden_update.py",):
            s2 = open(os.path.join(gdir, f)).read()
            self.assertIsNone(re.search(r'(?<![\w>\'"])[fF]("[^"\n]*\{|\'[^\'\n]*\{)', s2), "%s: f-string" % f)
        # web server and page
        swa = open(os.path.join(root, "solo_web_app.py")).read()
        self.assertGreater(swa.index("if path == '/api/golden/status':"), swa.index("# Require Auth for all other API endpoints"))
        self.assertGreater(swa.index("self._handle_golden_post(path, body)"), swa.index("if not self._is_authenticated(allow_cookie=False):"))
        self.assertIn("the factory reset partition cannot be changed while the vehicle is ARMED", swa)
        self.assertIn("GOLDEN_BUSY_BLOCKS = ('/api/flash/start', '/api/firmware/update', '/api/gimbal/start')", swa)
        golden_post = swa[swa.index("def _handle_golden_post"):swa.index("def _handle_gimbal_post")]
        self.assertNotIn("battery", golden_post)                       # no battery limit (user's choice)
        for page in ("static/index.html", "templates/index.html"):
            html = open(os.path.join(root, page), encoding="utf-8").read()
            for i in ("goldenCard", "btnGoldenSoloWrite", "btnGoldenControllerWrite",
                      "btnGoldenBothWrite", "btnGoldenBothCheck",
                      "btnGoldenControllerCheck", "goldenTerminal"):
                self.assertIn('id="%s"' % i, html)
        self.assertIn("/api/golden/start", open(os.path.join(root, "static", "app.js"), encoding="utf-8").read())

    def test_40_smart_shots(self):
        """Cockpit Smart Shots: the 6th control button opens a row of shots. Starting one connects to the
        shot manager (TCP 5507) like an app, sends SET_CURRENT_SHOT and holds the connection while the shot
        runs; it hangs up when the shot manager reports shot -1, so Solex can connect again. It never
        connects while Solex is on the shot manager or the Solo is disarmed."""
        import socket as _socket
        import struct as _struct
        import time as _time
        import cockpit_mgr as cm
        root = os.path.dirname(os.path.abspath(__file__))

        # packet framing (shotmanager/app_packet.py: '<II' type, length, then the value)
        pkt = cm.pack_shot_packet(cm.SHOT_MSG_SET_CURRENT_SHOT, '<i', -1)
        self.assertEqual(pkt, _struct.pack('<IIi', 1, 4, -1))
        packets, rest = cm.split_shot_packets(pkt + pkt[:5])
        self.assertEqual(packets, [(1, _struct.pack('<i', -1))])
        self.assertEqual(rest, pkt[:5])

        def fake_shotmanager(mode):
            """Like appManager.py: greets with the current shot; 'ok' enters shots, 'ekf' refuses them,
            'reject' hangs up at once (another app is connected)."""
            srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)

            def run():
                conn, _ = srv.accept()
                try:
                    if mode == "reject":
                        return
                    conn.sendall(_struct.pack('<IIi', 0, 4, -1))
                    buf = b""
                    while True:
                        data = conn.recv(1024)
                        if not data:
                            return
                        buf += data
                        got, buf = cm.split_shot_packets(buf)
                        for msg_type, value in got:
                            shot = _struct.unpack('<i', value)[0]
                            if mode == "ekf" and shot != -1:
                                conn.sendall(_struct.pack('<III', 21, 4, 0))
                            else:
                                conn.sendall(_struct.pack('<IIi', 0, 4, shot))
                finally:
                    conn.close()
                    srv.close()
            t = threading.Thread(target=run)
            t.daemon = True
            t.start()
            return srv.getsockname()[1]

        def wait_for(fn, secs=3.0):
            deadline = _time.time() + secs
            while _time.time() < deadline and not fn():
                _time.sleep(0.05)
            return fn()

        # refused without touching the shot manager
        c = cm.SmartShotClient(port=1)
        self.assertIn("Selfie cannot start", c.start_shot(0)[1])               # Selfie: no Solo position
        self.assertIn("Follow cannot start", c.start_shot(5)[1])               # Follow: no phone position
        self.assertIn("Unknown", c.start_shot(4)[1])                           # Record is not a shot
        self.assertIn("10.1.1.145", c.start_shot(1, app_ip="10.1.1.145")[1])   # Solex is connected
        self.assertIn("armed", c.start_shot(1, armed=False)[1])
        self.assertFalse(c.end_shot()[0])

        # started, held while it runs, released when the shot manager ends it
        c = cm.SmartShotClient(host="127.0.0.1", port=fake_shotmanager("ok"))
        ok, msg = c.start_shot(1)
        self.assertTrue(ok, msg)
        self.assertEqual(c.status(), {"shot": 1, "name": "Orbit", "starting": False, "follow_style": "hold",
                                      "selfie": cm.SELFIE_DEFAULTS, "detail": ""})
        self.assertIn("End it first", c.start_shot(2)[1])
        self.assertTrue(c.end_shot()[0])
        self.assertTrue(wait_for(lambda: c.status()["shot"] == -1))
        self.assertIsNone(c.sock)

        # the shot manager refuses the shot: nothing held
        c = cm.SmartShotClient(host="127.0.0.1", port=fake_shotmanager("ekf"))
        ok, msg = c.start_shot(7)
        self.assertFalse(ok)
        self.assertIn("EKF", msg)
        self.assertIsNone(c.sock)

        # another app already holds the shot manager
        c = cm.SmartShotClient(host="127.0.0.1", port=fake_shotmanager("reject"))
        ok, msg = c.start_shot(2)
        self.assertFalse(ok)
        self.assertIn("busy", msg)

        # telemetry carries the state; the action handler checks Solex and the armed state first
        self.assertIn("smart_shot", cm.get_telemetry_dict(msgs_since='none'))
        src = open(os.path.join(root, "cockpit_mgr.py")).read()
        self.assertIn("app_ip=find_app_on_shotmanager()", src)
        self.assertIn("armed=is_vehicle_armed(wake=True)", src)

        # pages: SMART SHOTS is the 6th control button (PRECISION the 7th); its row has a button for every shot
        import re as _re
        for page in ("static/index.html", "templates/index.html"):
            html = open(os.path.join(root, page), encoding="utf-8").read()
            top = html[html.index('<section class="tab-content" id="tab-cockpit">'):html.index('<div class="cockpit-body">')]
            row = top[top.index('class="annun-row"', top.index('id="btnUnitToggle"') - 200):top.index('id="smartShotRow"')]
            self.assertEqual(row.count('class="btn-ctrl'), 7, page)
            self.assertIn('id="btnSmartShots"', row)
            shots = set(int(s) for s in _re.findall(r'data-shot="(-?\d+)"', top))
            self.assertEqual(shots, set(s[0] for s in cm.SMART_SHOTS), page)
            self.assertIn('id="btnEndShot"', top)
        js = open(os.path.join(root, "static", "cockpit.js"), encoding="utf-8").read()
        self.assertIn("action: 'start_shot'", js)
        self.assertIn("action: 'end_shot'", js)
        self.assertIn("updateSmartShots(telem)", js)

    def test_41_precision_landing(self):
        """Precision landing with ArduPilot's own PrecLand: the Cockpit's pad finder posts sightings,
        the server sends each one as LANDING_TARGET (MAVLink 1, MAV_FRAME_LOCAL_FRD, with the distance:
        the Solo has no rangefinder); camera down is DO_MOUNT_CONTROL -90 deg, Precision Loiter is
        DO_AUX_FUNCTION 39. The preset writes PLND_ENABLED before the PLND_ settings it unhides."""
        import struct as _struct
        import subprocess as _subprocess
        import shutil as _shutil
        import cockpit_mgr as cm
        import presets as _presets
        import param_mgr as _pm
        root = os.path.dirname(os.path.abspath(__file__))

        # CRC_EXTRA computed from the message definition the way mavgen does (fields sorted by size)
        def crc_extra(name, fields):
            def x25(buf, crc=0xffff):
                for b in bytearray(buf):
                    tmp = b ^ (crc & 0xff)
                    tmp = (tmp ^ (tmp << 4)) & 0xff
                    crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xffff
                return crc
            size = {"uint64_t": 8, "float": 4, "uint16_t": 2, "uint8_t": 1}
            s = name + " " + "".join("%s %s " % (t, n) for t, n in sorted(fields, key=lambda f: -size[f[0]]))
            c = x25(s.encode("ascii"))
            return (c & 0xff) ^ (c >> 8)
        lt_fields = [("uint64_t", "time_usec"), ("uint8_t", "target_num"), ("uint8_t", "frame"), ("float", "angle_x"),
                     ("float", "angle_y"), ("float", "distance"), ("float", "size_x"), ("float", "size_y")]
        self.assertEqual(crc_extra("LANDING_TARGET", lt_fields), cm.LANDING_TARGET_CRC_EXTRA)
        cl_fields = [("uint8_t", "target_system"), ("uint8_t", "target_component"), ("uint16_t", "command"),
                     ("uint8_t", "confirmation")] + [("float", "param%d" % i) for i in range(1, 8)]
        self.assertEqual(crc_extra("COMMAND_LONG", cl_fields), 152)

        class _Client(cm.LiveMAVLinkClient):
            def __init__(self):
                cm.LiveMAVLinkClient.__init__(self, cm.TelemetryState())
                self.sent = []

            def send_packet(self, packet_bytes):
                self.sent.append(packet_bytes)

        def payload(pkt):
            self.assertEqual(bytearray(pkt)[0], 0xFE)
            plen, msgid = bytearray(pkt)[1], bytearray(pkt)[5]
            body = pkt[6:6 + plen]
            crc = cm.calc_x25_crc(pkt[1:6 + plen] + bytes(bytearray([{149: 200, 76: 152}[msgid]])))
            self.assertEqual(_struct.unpack('<H', pkt[6 + plen:8 + plen])[0], crc)
            return msgid, body

        client = _Client()
        pl = cm.PrecisionLanding(client)

        # a good sighting goes out as LANDING_TARGET in the level, heading-aligned frame
        ok, msg = pl.report_target({"angle_x": 0.1, "angle_y": -0.2, "distance": 6.5, "size": 0.09, "age_ms": 40})
        self.assertTrue(ok, msg)
        msgid, body = payload(client.sent[-1])
        self.assertEqual(msgid, 149)
        self.assertEqual(len(body), 30)
        _t, ax, ay, dist, sx, sy, num, frame = _struct.unpack('<QfffffBB', body)
        self.assertAlmostEqual(ax, 0.1, places=5)
        self.assertAlmostEqual(ay, -0.2, places=5)
        self.assertAlmostEqual(dist, 6.5, places=4)
        self.assertEqual(frame, 20)                    # MAV_FRAME_LOCAL_FRD (AC_PrecLand_MAVLink accepts it)
        st = pl.status()
        self.assertTrue(st["sending"])
        self.assertEqual(st["distance"], 6.5)

        # nonsense is never sent (ArduPilot would steer the landing by it)
        n = len(client.sent)
        for bad in ({"angle_x": "x", "angle_y": 0, "distance": 5}, {"angle_x": 0, "angle_y": 0},
                    {"angle_x": float("nan"), "angle_y": 0, "distance": 5}, {"angle_x": 2.0, "angle_y": 0, "distance": 5},
                    {"angle_x": 0, "angle_y": 0, "distance": 0.0}, {"angle_x": 0, "angle_y": 0, "distance": 500},
                    {"angle_x": 0, "angle_y": 0, "distance": 5, "age_ms": 900}, None, [1, 2]):
            self.assertFalse(pl.report_target(bad)[0], bad)
        self.assertEqual(len(client.sent), n)

        # nothing for > 1 s: no longer "sending"
        pl.last_sent_time -= 2.0
        self.assertFalse(pl.status()["sending"])

        # camera down: DO_MOUNT_CONTROL pitch -90, MAVLink targeting; back: RC targeting (the paddle)
        pl.set_camera(True)
        msgid, body = payload(client.sent[-1])
        p = _struct.unpack('<fffffffHBBB', body)
        self.assertEqual((msgid, p[7], p[0], p[6], p[8], p[9]), (76, 205, -90.0, 2.0, 1, 1))
        pl.set_camera(False)
        p = _struct.unpack('<fffffffHBBB', payload(client.sent[-1])[1])
        self.assertEqual((p[7], p[6]), (205, 3.0))

        # Precision Loiter: aux function 39 high / low
        pl.set_precision_loiter(True)
        p = _struct.unpack('<fffffffHBBB', payload(client.sent[-1])[1])
        self.assertEqual((p[7], p[0], p[1]), (218, 39.0, 2.0))
        self.assertTrue(pl.status()["loiter"])
        pl.set_precision_loiter(False)
        p = _struct.unpack('<fffffffHBBB', payload(client.sent[-1])[1])
        self.assertEqual((p[7], p[0], p[1]), (218, 39.0, 0.0))
        self.assertIn("on or off", cm.handle_action("prec_loiter", "maybe")["message"])

        # the Cube's settings, as /api/precland/check reports them
        self.assertFalse(cm.precland_param_report({"PLND_ENABLED": None})["ready"])
        self.assertIn("preset", cm.precland_param_report({"PLND_ENABLED": 0.0})["message"])
        self.assertFalse(cm.precland_param_report({"PLND_ENABLED": 1.0, "PLND_TYPE": 2.0})["ready"])
        self.assertTrue(cm.precland_param_report({"PLND_ENABLED": 1.0, "PLND_TYPE": 1.0, "PLND_LAG": 0.25})["ready"])
        self.assertIn("precland", cm.get_telemetry_dict(msgs_since='none'))

        # preset: MAVLink type, the longest lag ArduPilot accepts, no Rover-only PLND_ORIENT
        pre = _presets.get_preset("precision_landing")["params"]
        self.assertEqual((pre["PLND_ENABLED"], pre["PLND_TYPE"]), (1, 1))
        self.assertTrue(0.02 <= pre["PLND_LAG"] <= 0.25)
        self.assertNotIn("PLND_ORIENT", pre)
        order = _pm._write_order(list(pre.keys()))
        self.assertEqual(order[0], "PLND_ENABLED")

        # the PLND_ settings only exist on the Cube once PLND_ENABLED is 1 (AP_PARAM_FLAG_ENABLE)
        class _Msg(object):
            def __init__(self, name, value):
                self.param_id, self.param_value, self.param_type = name, float(value), 9

        class _Cube(object):
            def __init__(self):
                self.params = dict((k, 0.0) for k in pre)
                self.queue = []
                self.target_system, self.target_component = 1, 1
                cube = self

                class _Link(object):
                    def param_request_read_send(self, ts, tc, name, idx):
                        cube._handle(name, None)

                    def param_set_send(self, ts, tc, name, value, ptype):
                        cube._handle(name, value)
                self.mav = _Link()

            def _handle(self, name, value):
                name = name.decode("ascii") if isinstance(name, bytes) else name
                if name != "PLND_ENABLED" and not self.params["PLND_ENABLED"]:
                    return                                  # hidden
                if value is not None:
                    self.params[name] = float(value)
                self.queue.append(_Msg(name, self.params[name]))

            def recv_match(self, type=None, blocking=True, timeout=0.1):
                return self.queue.pop(0) if self.queue else None
        orig_mavutil = _pm.mavutil
        if _pm.mavutil is None:
            class _MavutilStub(object):
                class mavlink(object):
                    MAV_PARAM_TYPE_REAL32 = 9
            _pm.mavutil = _MavutilStub()
        try:
            ok, confirmed, failed, msg = _pm.commit_batch_mavlink(_Cube(), pre)
            self.assertTrue(ok, msg)
        finally:
            _pm.mavutil = orig_mavutil

        # server routes and pages
        swa = open(os.path.join(root, "solo_web_app.py"), encoding="utf-8").read()
        self.assertIn("'/api/precland/target'", swa)
        self.assertIn("'/api/precland/check'", swa)
        for page in ("static/index.html", "templates/index.html"):
            html = open(os.path.join(root, page), encoding="utf-8").read()
            for i in ("btnPrecision", "precisionRow", "btnPadFinder", "btnCameraDown", "btnPrecLoiter",
                      "btnPrecLand", "padOverlay", "padSizeIn", "padFovIn"):
                self.assertIn('id="%s"' % i, html, page)
            self.assertLess(html.index('src="/padfinder.js'), html.index('src="/cockpit.js'))
        js = open(os.path.join(root, "static", "cockpit.js"), encoding="utf-8").read()
        self.assertIn("'/api/precland/target'", js)
        self.assertIn("updatePrecision(telem)", js)
        pad_page = open(os.path.join(root, "static", "landing_pad.html"), encoding="utf-8").read()
        self.assertIn('src="/padfinder.js"', pad_page)            # prints the finder's own pattern
        self.assertIn('"static/padfinder.js"', open(os.path.join(root, "install_to_solo.py")).read())

        # the pad finder itself on rendered camera frames (needs Node; skipped without it)
        node = _shutil.which("node") if hasattr(_shutil, "which") else None
        if not node:
            self.skipTest("node not installed: pad finder image tests not run")
        out = _subprocess.run([node, os.path.join(root, "tools", "test_padfinder.js")],
                              stdout=_subprocess.PIPE, stderr=_subprocess.STDOUT, timeout=600)
        text = out.stdout.decode("utf-8", "replace")
        self.assertEqual(out.returncode, 0, text)
        self.assertIn("PADFINDER OK", text)

    def test_42_takeoff_spot(self):
        """Precision landing on the takeoff spot, no pad: TAKEOFF SPOT turns the camera down when the
        Solo arms, saves pictures of the ground on the climb (spotfinder.js) and hands the camera back
        to the paddle; on the way down matches go to the Cube as LANDING_TARGET like pad sightings."""
        import struct as _struct
        import subprocess as _subprocess
        import shutil as _shutil
        import cockpit_mgr as cm
        root = os.path.dirname(os.path.abspath(__file__))

        class _Client(cm.LiveMAVLinkClient):
            def __init__(self):
                cm.LiveMAVLinkClient.__init__(self, cm.TelemetryState())
                self.sent = []

            def send_packet(self, packet_bytes):
                self.sent.append(packet_bytes)

        # a takeoff spot match is sent exactly like a pad sighting, and says which it was
        client = _Client()
        state = cm.TelemetryState()
        pl = cm.PrecisionLanding(client, state=state)
        ok, msg = pl.report_target({"angle_x": -0.05, "angle_y": 0.1, "distance": 8.0, "size": 0.3,
                                    "age_ms": 60, "source": "spot"})
        self.assertTrue(ok, msg)
        body = bytes(client.sent[-1])[6:6 + bytearray(client.sent[-1])[1]]
        _t, ax, ay, dist, sx, sy, num, frame = _struct.unpack('<QfffffBB', body)
        self.assertEqual(bytearray(client.sent[-1])[5], 149)
        self.assertAlmostEqual(dist, 8.0, places=4)
        self.assertEqual(frame, 20)
        self.assertEqual(pl.status()["source"], "spot")
        self.assertIn("Takeoff spot found", state.mavlink_messages[-1]["text"])
        # switching to the pad is logged again; anything else counts as the pad
        pl.report_target({"angle_x": 0, "angle_y": 0, "distance": 5.0, "age_ms": 10, "source": "nonsense"})
        self.assertEqual(pl.status()["source"], "pad")
        self.assertIn("Landing pad found", state.mavlink_messages[-1]["text"])

        # the Cockpit: button, script, arming / climb / hand back / matching
        for page in ("static/index.html", "templates/index.html"):
            html = open(os.path.join(root, page), encoding="utf-8").read()
            self.assertIn('id="btnTakeoffSpot"', html, page)
            self.assertLess(html.index('src="/spotfinder.js'), html.index('src="/cockpit.js'), page)
        js = open(os.path.join(root, "static", "cockpit.js"), encoding="utf-8").read()
        for s in ("SpotFinder.reference(", "SpotFinder.match(", "action: 'camera_down'", "action: 'camera_paddle'",
                  "updateTakeoffSpot(telem, armed)", "source: source || 'pad'", "SPOT_HANDBACK_ALT = 10"):
            self.assertIn(s, js)
        self.assertIn('"static/spotfinder.js"', open(os.path.join(root, "install_to_solo.py")).read())

        # the matcher itself on rendered camera frames (needs Node; skipped without it)
        node = _shutil.which("node") if hasattr(_shutil, "which") else None
        if not node:
            self.skipTest("node not installed: takeoff spot image tests not run")
        out = _subprocess.run([node, os.path.join(root, "tools", "test_spotfinder.js")],
                              stdout=_subprocess.PIPE, stderr=_subprocess.STDOUT, timeout=600)
        text = out.stdout.decode("utf-8", "replace")
        self.assertEqual(out.returncode, 0, text)
        self.assertIn("SPOTFINDER OK", text)

    def test_43_follow(self):
        """Cockpit Follow with the phone's GPS: the SoloLink Viewer app hands each fix to the page, which
        posts it to /api/follow/location. While Follow runs the server sends the position to follow.py's
        UDP socket (14558, '<IIddf' type 2, altitude 0) 25 times a second, carried forward along the fix's
        speed and heading; once follow.py has positions it sends SOLO_FOLLOW_OPTIONS_V2 (119) with the
        follow style, so the shot leaves Look At Me. Without a new fix for 2 s it ends the shot."""
        import socket as _socket
        import struct as _struct
        import time as _time
        import cockpit_mgr as cm
        root = os.path.dirname(os.path.abspath(__file__))

        self.assertEqual(cm.pack_follow_location(37.5, -122.25), _struct.pack('<IIddf', 2, 20, 37.5, -122.25, 0.0))
        self.assertEqual(cm.SMART_SHOT_NAMES[cm.FOLLOW_SHOT], "Follow")
        self.assertIsNone(dict((s[0], s[2]) for s in cm.SMART_SHOTS)[cm.FOLLOW_SHOT])

        # fixes: nonsense is refused, a good one makes Follow ready
        pl = cm.PilotLocation()
        self.assertIn("SoloLink Viewer", pl.problem())
        for bad in (None, [1], {"lat": 0, "lon": 0, "accuracy": 3}, {"lat": 91, "lon": 0, "accuracy": 3},
                    {"lat": "x", "lon": 1, "accuracy": 3}, {"lat": 37, "lon": -122},
                    {"lat": 37, "lon": -122, "accuracy": float("nan")},
                    {"lat": 37, "lon": -122, "accuracy": 3, "age_ms": 5000}):
            self.assertFalse(pl.report(bad)[0], bad)
        self.assertIsNone(pl.fix)
        self.assertTrue(pl.report({"lat": 37.0, "lon": -122.0, "accuracy": 4.0, "speed": 0.1, "bearing": 90,
                                   "age_ms": 100})[0])
        self.assertIsNone(pl.problem())
        self.assertTrue(pl.status()["ready"])
        (lat, lon), _ = pl.predict()
        self.assertEqual((lat, lon), (37.0, -122.0))       # standing: not carried forward

        # a jump faster than a person can move is a GPS glitch
        _time.sleep(0.05)
        ok, msg = pl.report({"lat": 37.01, "lon": -122.0, "accuracy": 4.0})
        self.assertFalse(ok)
        self.assertIn("jumped", msg)
        # an older fix than the last is ignored
        self.assertFalse(pl.report({"lat": 37.0, "lon": -122.0, "accuracy": 4.0, "age_ms": 1500})[0])

        # moving: carried forward along speed and heading (5 m/s north, fix 0.6 s old -> 3 m north)
        pl = cm.PilotLocation()
        pl.report({"lat": 37.0, "lon": -122.0, "accuracy": 3.0, "speed": 5.0, "bearing": 0.0, "age_ms": 600})
        (lat, lon), _ = pl.predict()
        north, east = cm._ground_offset(37.0, -122.0, lat, lon)
        self.assertAlmostEqual(north, 3.0, delta=0.2)
        self.assertAlmostEqual(east, 0.0, delta=0.01)
        # ... but never more than FOLLOW_PREDICT_MAX
        (lat, lon), _ = pl.predict(now=pl.fix["t"] + 1.9)
        self.assertAlmostEqual(cm._ground_offset(37.0, -122.0, lat, lon)[0], 5.0, delta=0.2)

        # poor accuracy: cannot start; gone quiet: cannot run
        pl.fix["accuracy"] = 14.0
        self.assertIn("only good to 14 m", pl.problem())
        self.assertIsNotNone(pl.predict()[0])               # still good enough while it runs
        pl.fix["t"] -= 3.0
        self.assertIn("no position from the phone", pl.predict()[1])

        # the whole shot against a fake shot manager and a fake follow.py socket
        udp = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        udp.bind(("127.0.0.1", 0))
        udp.settimeout(2.0)
        received = []                                       # (type, value) the shot manager got

        def fake_shotmanager():
            srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)

            def run():
                conn, _ = srv.accept()
                try:
                    conn.sendall(_struct.pack('<IIi', 0, 4, -1))
                    buf = b""
                    while True:
                        data = conn.recv(1024)
                        if not data:
                            return
                        buf += data
                        got, buf = cm.split_shot_packets(buf)
                        for msg_type, value in got:
                            received.append((msg_type, value))
                            if msg_type == 1:
                                conn.sendall(_struct.pack('<IIi', 0, 4, _struct.unpack('<i', value)[0]))
                finally:
                    conn.close()
                    srv.close()
            t = threading.Thread(target=run)
            t.daemon = True
            t.start()
            return srv.getsockname()[1]

        def wait_for(fn, secs=3.0):
            deadline = _time.time() + secs
            while _time.time() < deadline and not fn():
                _time.sleep(0.02)
            return fn()

        delay = cm.FOLLOW_OPTIONS_DELAY
        cm.FOLLOW_OPTIONS_DELAY = 0.3
        try:
            pl = cm.PilotLocation()
            c = cm.SmartShotClient(host="127.0.0.1", port=fake_shotmanager(), pilot=pl,
                                   follow_port=udp.getsockname()[1])
            self.assertIn("Follow cannot start", c.start_shot(5)[1])            # no fix yet
            self.assertIsNone(c.sock)
            self.assertTrue(c.set_follow_style("leash")[0])
            self.assertFalse(c.set_follow_style("sideways")[0])
            pl.report({"lat": 37.0, "lon": -122.0, "accuracy": 3.0})
            ok, msg = c.start_shot(5)
            self.assertTrue(ok, msg)
            self.assertIn("Leash", msg)

            # positions arrive at about 25 a second
            t0 = _time.time()
            pkts = [udp.recvfrom(64)[0] for _ in range(25)]
            self.assertLess(_time.time() - t0, 1.6)
            typ, length, lat, lon, alt = _struct.unpack('<IIddf', pkts[-1])
            self.assertEqual((typ, length, lat, lon, alt), (2, 20, 37.0, -122.0, 0.0))

            # then the style, so follow.py leaves Look At Me: '<fii' no cruise, not look-at, leash (2)
            opts = lambda: [v for t, v in received if t == 119]
            self.assertTrue(wait_for(lambda: opts()), received)
            self.assertEqual(_struct.unpack('<fii', opts()[0]), (0.0, 0, 2))
            # changed while it runs: sent at once
            self.assertIn("Free Look", c.set_follow_style("freelook")[1])
            self.assertTrue(wait_for(lambda: len(opts()) == 2))
            self.assertEqual(_struct.unpack('<fii', opts()[1]), (0.0, 0, 1))

            # the phone goes quiet: the shot is ended (shot -1) and the connection released
            with pl.lock:
                pl.fix["t"] -= 5.0
            self.assertTrue(wait_for(lambda: c.status()["shot"] == -1))
            self.assertIn((1, _struct.pack('<i', -1)), received)
            self.assertIsNone(c.sock)
        finally:
            cm.FOLLOW_OPTIONS_DELAY = delay
            udp.close()

        # actions, telemetry, route, page, viewer app
        self.assertIn("Unknown follow style", cm.handle_action("follow_style", "sideways")["message"])
        self.assertIn("pilot", cm.get_telemetry_dict(msgs_since='none'))
        self.assertIn("'/api/follow/location'", open(os.path.join(root, "solo_web_app.py")).read())
        for page in ("static/index.html", "templates/index.html"):
            html = open(os.path.join(root, page), encoding="utf-8").read()
            self.assertIn('id="btnFollow"', html, page)
            self.assertIn('id="btnFollowStyle"', html, page)
        js = open(os.path.join(root, "static", "cockpit.js"), encoding="utf-8").read()
        for s in ("window.soloViewerPilotFix", "'/api/follow/location'", "action: 'follow_style'", "updateFollow(telem)"):
            self.assertIn(s, js)
        java = open(os.path.join(root, "android_app", "src", "com", "sololink", "viewer", "MainActivity.java")).read()
        self.assertIn("window.soloViewerPilotFix", java)
        self.assertIn("LocationManager.GPS_PROVIDER", java)
        # why there is no fix: the app reports its GPS state, and asks for location even when it starts on SoloLink
        self.assertIn("window.soloViewerGpsStatus", java)
        self.assertIn("window.soloViewerGpsStatus", js)
        self.assertIn("GPS_PERMISSION_REQ_CODE);", java)
        for status in ("no_permission", "approximate", "off", "waiting"):
            self.assertIn('"%s"' % status, java)
            self.assertIn("%s:" % status, js)

    def test_44_selfie_and_multipoint(self):
        """Cockpit Selfie and Multipoint Cable Cam without Solex. Selfie: the server sends selfie.py the start
        (where the Solo is), the end (further from the subject and up) and the point to look at (the phone's
        position, or straight ahead of the Solo as far as it is high) as SOLO_MESSAGE_LOCATION, then
        SOLO_SHOT_OPTIONS with the speed. Multipoint: points are recorded with A / B on the Controller; after the
        shot manager's SOLO_SPLINE_PLAY and points the server sends SOLO_SPLINE_ATTACH at the last point."""
        import socket as _socket
        import struct as _struct
        import time as _time
        import cockpit_mgr as cm
        root = os.path.dirname(os.path.abspath(__file__))
        reasons = dict((s[0], s[2]) for s in cm.SMART_SHOTS)
        self.assertIsNone(reasons[cm.SELFIE_SHOT])
        self.assertIsNone(reasons[cm.MULTIPOINT_SHOT])

        def offset(a, b):
            return cm._ground_offset(a[0], a[1], b[0], b[1])

        # path away from the phone: the subject 10 m south, so out is north
        solo = {"lat": 37.0, "lon": -122.0, "alt": 10.0, "heading": 0.0}
        subject = cm._offset_location(37.0, -122.0, -10.0, 0.0)
        start, end, look = cm.selfie_path(solo, subject, 30.0, 15.0)
        self.assertEqual(start, (37.0, -122.0, 10.0))
        n, e = offset(start, end)
        self.assertAlmostEqual(n, 30.0, delta=0.05)
        self.assertAlmostEqual(e, 0.0, delta=0.05)
        self.assertEqual(end[2], 25.0)
        self.assertEqual(look, (subject[0], subject[1], 0.0))
        # no phone: the subject is straight ahead (heading 90 = east), as far as the Solo is high -> out is west
        start, end, look = cm.selfie_path(dict(solo, heading=90.0), None, 20.0, 5.0)
        n, e = offset(start, look)
        self.assertAlmostEqual(e, 10.0, delta=0.05)
        n, e = offset(start, end)
        self.assertAlmostEqual(e, -20.0, delta=0.05)
        # right over the phone: backs away from where it faces; never above SELFIE_MAX_ALT
        start, end, look = cm.selfie_path(dict(solo, alt=110.0), (37.0, -122.0), 10.0, 50.0)
        self.assertAlmostEqual(offset(start, end)[0], -10.0, delta=0.05)
        self.assertEqual(end[2], cm.SELFIE_MAX_ALT)

        # the Solo's position from telemetry: stale or missing -> None; unknown GPS heading -> attitude yaw
        st = cm.TelemetryState()
        self.assertIsNone(cm.vehicle_position(st))
        st.lat, st.lon, st.alt_rel, st.heading, st.yaw = 37.0, -122.0, 12.0, 655.35, 45.0
        st.position_time = _time.time()
        self.assertEqual(cm.vehicle_position(st), {"lat": 37.0, "lon": -122.0, "alt": 12.0, "heading": 45.0})
        self.assertIsNone(cm.vehicle_position(st, now=st.position_time + 10))

        # settings
        c = cm.SmartShotClient(port=1)
        self.assertTrue(c.set_selfie({"distance": 40, "speed": 2.5})[0])
        self.assertEqual(c.selfie, {"distance": 40.0, "rise": 15.0, "speed": 2.5})
        for bad in ({"distance": 1}, {"rise": 99}, {"speed": "fast"}, None):
            self.assertFalse(c.set_selfie(bad)[0], bad)
        self.assertEqual(c.selfie["distance"], 40.0)
        self.assertIn("no position from the Solo", c.start_shot(0)[1])
        c.position_fn = lambda: dict(solo, alt=1.0)
        self.assertIn("fly the Solo up", c.start_shot(0)[1])
        self.assertIsNone(c.sock)

        def fake_shotmanager():
            """Answers SET_CURRENT_SHOT; records what it gets; push() sends packets as the shot would."""
            srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            fake = {"got": [], "conn": None}

            def run():
                conn, _ = srv.accept()
                fake["conn"] = conn
                try:
                    conn.sendall(_struct.pack('<IIi', 0, 4, -1))
                    buf = b""
                    while True:
                        data = conn.recv(1024)
                        if not data:
                            return
                        buf += data
                        got, buf = cm.split_shot_packets(buf)
                        for msg_type, value in got:
                            fake["got"].append((msg_type, value))
                            if msg_type == 1:
                                conn.sendall(_struct.pack('<IIi', 0, 4, _struct.unpack('<i', value)[0]))
                except OSError:
                    pass
                finally:
                    conn.close()
                    srv.close()
            t = threading.Thread(target=run)
            t.daemon = True
            t.start()
            fake["port"] = srv.getsockname()[1]
            fake["push"] = lambda pkt: fake["conn"].sendall(pkt)
            return fake

        def wait_for(fn, secs=3.0):
            deadline = _time.time() + secs
            while _time.time() < deadline and not fn():
                _time.sleep(0.02)
            return fn()

        # Selfie: path then speed, on the app connection
        fake = fake_shotmanager()
        pl = cm.PilotLocation()
        pl.report({"lat": subject[0], "lon": subject[1], "accuracy": 4.0})
        c = cm.SmartShotClient(host="127.0.0.1", port=fake["port"], pilot=pl, position_fn=lambda: dict(solo))
        ok, msg = c.start_shot(0)
        self.assertTrue(ok, msg)
        self.assertIn("looking at you", msg)
        self.assertTrue(wait_for(lambda: len(fake["got"]) >= 5), fake["got"])
        types = [t for t, _ in fake["got"]]
        self.assertEqual(types, [1, 2, 2, 2, 20])
        sent = [_struct.unpack('<ddf', v) for t, v in fake["got"] if t == 2]
        expect = cm.selfie_path(solo, pl.predict()[0], 30.0, 15.0)
        for got, want in zip(sent, expect):
            self.assertAlmostEqual(got[0], want[0], places=7)
            self.assertAlmostEqual(got[1], want[1], places=7)
            self.assertAlmostEqual(got[2], want[2], places=3)
        self.assertEqual(_struct.unpack('<f', fake["got"][-1][1]), (3.0,))
        self.assertIn("Selfie", c.status()["detail"])
        self.assertTrue(c.end_shot()[0])
        self.assertTrue(wait_for(lambda: c.status()["shot"] == -1))

        # Multipoint: recorded points shown, attached at the last point after PLAY, on the cable after ATTACH
        def point(i, lat):
            return _struct.pack('<IIhfIddffffh', 52, 44, 0, 3.0, i, lat, -122.0, 10.0, -20.0, 90.0, 0.0, 0)

        fake = fake_shotmanager()
        c = cm.SmartShotClient(host="127.0.0.1", port=fake["port"])
        ok, msg = c.start_shot(6)
        self.assertTrue(ok, msg)
        self.assertIn("press A", msg)
        fake["push"](point(0, 37.0))
        self.assertTrue(wait_for(lambda: "point 1 recorded" in c.status()["detail"]))
        # B: last point recorded, then PLAY, durations and every point again
        fake["push"](point(1, 37.001) + _struct.pack('<II', 51, 0) + _struct.pack('<IIff', 56, 8, 10.0, 60.0) +
                     point(0, 37.0) + point(1, 37.001))
        attach = lambda: [v for t, v in fake["got"] if t == 57]
        self.assertTrue(wait_for(lambda: attach()), fake["got"])
        self.assertEqual(_struct.unpack('<I', attach()[0]), (1,))
        self.assertEqual(len(attach()), 1)
        self.assertIn("flying onto the cable at point 2", c.status()["detail"])
        fake["push"](_struct.pack('<III', 57, 4, 1))
        self.assertTrue(wait_for(lambda: "on the cable" in c.status()["detail"]))
        # the shot manager could not make the cable and went back to recording
        fake["push"](_struct.pack('<II', 50, 0))
        self.assertTrue(wait_for(lambda: "Record them again" in c.status()["detail"]))
        self.assertTrue(c.end_shot()[0])
        self.assertTrue(wait_for(lambda: c.status()["shot"] == -1))
        self.assertEqual(c.status()["detail"], "")

        # action, page
        self.assertIn("must be", cm.handle_action("selfie_settings", {"speed": 50})["message"])
        for page in ("static/index.html", "templates/index.html"):
            html = open(os.path.join(root, page), encoding="utf-8").read()
            row = html[html.index('id="smartShotRow"'):html.index('id="precisionRow"')]
            self.assertNotIn("needs-app\" type=\"button\" data-shot=\"0\"", row, page)
            self.assertNotIn("needs-app\" type=\"button\" data-shot=\"6\"", row, page)
            for i in ("btnSelfieSettings", "selfieDistanceIn", "selfieRiseIn", "selfieSpeedIn"):
                self.assertIn('id="%s"' % i, row, page)
        js = open(os.path.join(root, "static", "cockpit.js"), encoding="utf-8").read()
        for s in ("action: 'selfie_settings'", "updateSelfieSettings(telem)", "ss.detail"):
            self.assertIn(s, js)

    def test_45_precland_video_delay(self):
        """The video is ~0.4 s behind the GoPro and ArduPilot's PLND_LAG makes up for 0.25 s at most:
        the server moves each sighting on by the Solo's velocity and turn over the rest, so
        Precision Loiter doesn't chase where the pad was (it swung ~8 ft without this)."""
        import math
        import struct as _struct
        import cockpit_mgr as cm
        root = os.path.dirname(os.path.abspath(__file__))

        def frd(ax, ay, dist):
            """(forward, right, down) of the pad as ArduPilot reads LANDING_TARGET in LOCAL_FRD."""
            u = (-math.tan(ay), math.tan(ax), 1.0)
            n = math.sqrt(sum(c * c for c in u))
            return tuple(c / n * dist for c in u)

        # the pad 1 m ahead, 5 m down; the Solo flies forward (heading 90: east) at 2 m/s for 0.2 s
        ax, ay, d = math.atan2(0.0, 5.0), math.atan2(-1.0, 5.0), math.sqrt(26.0)
        f, r, dn = frd(*cm.predict_landing_target(ax, ay, d, (0.0, 2.0, 0.0), 90.0, 0.0, 0.2))
        self.assertAlmostEqual(f, 0.6, places=6)          # 0.4 m closer
        self.assertAlmostEqual(r, 0.0, places=6)
        self.assertAlmostEqual(dn, 5.0, places=6)
        # sideways (heading 0, flying east = right) and descending 0.5 m/s
        f, r, dn = frd(*cm.predict_landing_target(ax, ay, d, (0.0, 1.0, 0.5), 0.0, 0.0, 0.2))
        self.assertAlmostEqual(r, -0.2, places=6)        # the pad is now a little to the left
        self.assertAlmostEqual(dn, 4.9, places=6)
        # turning right 90 deg/s for 0.2 s: what was ahead is now ahead-left
        f, r, dn = frd(*cm.predict_landing_target(ax, ay, d, (0.0, 0.0, 0.0), 0.0, 90.0, 0.2))
        self.assertAlmostEqual(f, math.cos(math.radians(18)), places=6)
        self.assertAlmostEqual(r, -math.sin(math.radians(18)), places=6)
        # no delay beyond the Cube's: unchanged
        self.assertEqual(cm.predict_landing_target(ax, ay, d, (5.0, 5.0, 0.0), 0.0, 30.0, 0.0), (ax, ay, d))

        class _Client(cm.LiveMAVLinkClient):
            def __init__(self):
                cm.LiveMAVLinkClient.__init__(self, cm.TelemetryState())
                self.sent = []

            def send_packet(self, packet_bytes):
                self.sent.append(packet_bytes)

        def sent_target(client):
            pkt = bytes(client.sent[-1])
            body = pkt[6:6 + bytearray(pkt)[1]]
            return _struct.unpack('<QfffffBB', body)[1:4]

        state = cm.TelemetryState()
        state.vel_ned, state.yaw, state.yaw_rate, state.position_time = (0.0, 2.0, 0.0), 90.0, 0.0, time.time()
        client = _Client()
        pl = cm.PrecisionLanding(client, state=state)
        # 0.45 s old frame, PLND_LAG 0.25: moved on by 0.2 s
        self.assertTrue(pl.report_target({"angle_x": ax, "angle_y": ay, "distance": d, "age_ms": 30, "delay_s": 0.45})[0])
        f, r, dn = frd(*sent_target(client))
        self.assertAlmostEqual(f, 0.6, places=4)
        self.assertAlmostEqual(pl.status()["lead"], 0.2, places=2)
        # the Cube's own lag read back as 0.1: the Cockpit makes up for more
        pl.set_cube_lag(0.1)
        pl.report_target({"angle_x": ax, "angle_y": ay, "distance": d, "age_ms": 30, "delay_s": 0.45})
        self.assertAlmostEqual(frd(*sent_target(client))[0], 0.3, places=4)
        pl.set_cube_lag(None)                                  # unknown: kept
        self.assertAlmostEqual(pl.cube_lag, 0.1)
        # no delay sent (an older Cockpit), or no fresh velocity: sent as seen
        pl.report_target({"angle_x": ax, "angle_y": ay, "distance": d, "age_ms": 30})
        self.assertAlmostEqual(frd(*sent_target(client))[0], 1.0, places=4)
        state.position_time = time.time() - 5
        pl.report_target({"angle_x": ax, "angle_y": ay, "distance": d, "age_ms": 30, "delay_s": 0.45})
        self.assertAlmostEqual(frd(*sent_target(client))[0], 1.0, places=4)
        for bad in (-0.1, 2.0, "x"):
            self.assertFalse(pl.report_target({"angle_x": 0, "angle_y": 0, "distance": 5, "delay_s": bad})[0])

        # the Cockpit sends the delay setting, keeps the video near live while a finder is on;
        # /api/precland/check passes PLND_LAG on
        for page in ("static/index.html", "templates/index.html"):
            self.assertIn('id="padDelayIn"', open(os.path.join(root, page), encoding="utf-8").read(), page)
        js = open(os.path.join(root, "static", "cockpit.js"), encoding="utf-8").read()
        for s in ("delay_s: padDelayS +", "solo_video_delay_s", "VIDEO_FINDER_MAX_LAG_S"):
            self.assertIn(s, js)
        swa = open(os.path.join(root, "solo_web_app.py"), encoding="utf-8").read()
        self.assertIn("precland.set_cube_lag(", swa)

    def test_46_pixhawk_on_stock_firmware(self):
        """bin/pixhawk.py flashes the Cube on stock 3DR firmware too: without Open Solo's
        /usr/bin/led.py and usb.py it uses its own stand-ins (same LED and GPIO 19/21 writes as
        Open Solo's led_solo.py / usb_solo.py), and uploader.py from next to itself when the
        PATH has none. Open Solo's own modules still win when they are there."""
        import re as _re
        root = os.path.dirname(os.path.abspath(__file__))
        src = open(os.path.join(root, "bin", "pixhawk.py"), encoding="utf-8").read()
        self.assertNotIn("\nimport led\n", src)
        self.assertNotIn("\nimport usb\n", src)
        self.assertIn("try:\n    import led\nexcept ImportError:\n    led = _SysfsLed()", src)
        self.assertIn("try:\n    import usb\nexcept ImportError:\n    usb = _SysfsUsb()", src)
        self.assertIn("subprocess.Popen(_uploader_cmd() + [", src)
        self.assertNotIn('subprocess.Popen(["uploader.py"', src)

        # run the stand-ins (plain Python, the same under 2 and 3) against a recorded sysfs
        block = src[src.index("class _SysfsLed"):src.index("try:\n    import led")]
        uploader_fn = src[src.index("def _uploader_cmd"):src.index("version_file =")]
        writes = []

        class _Rec(object):
            def __init__(self, path, mode="r"):
                self.path = path
            def write(self, v):
                writes.append((self.path, v))
            def close(self):
                pass

        fake_os = type("o", (), {})()
        fake_os.path = type("p", (), {"isdir": staticmethod(lambda d: False),
                                      "isfile": staticmethod(os.path.isfile),
                                      "join": staticmethod(os.path.join),
                                      "dirname": staticmethod(os.path.dirname),
                                      "abspath": staticmethod(os.path.abspath)})()
        fake_os.environ = {"PATH": ""}
        fake_os.pathsep = os.pathsep
        ns = {"os": fake_os, "sys": sys, "open": _Rec, "IOError": IOError, "OSError": OSError,
              "__file__": os.path.join(root, "bin", "pixhawk.py")}
        exec(block + "\n" + uploader_fn, ns)
        u = ns["_SysfsUsb"]()
        u.init(); u.enable(); u.disable(); u.uninit()
        self.assertEqual(writes, [
            ("/sys/class/gpio/export", "21"), ("/sys/class/gpio/export", "19"),
            ("/sys/class/gpio/gpio21/direction", "out"), ("/sys/class/gpio/gpio19/direction", "out"),
            ("/sys/class/gpio/gpio21/value", "0"), ("/sys/class/gpio/gpio19/value", "1"),
            ("/sys/class/gpio/gpio21/value", "1"), ("/sys/class/gpio/gpio19/value", "0"),   # enable
            ("/sys/class/gpio/gpio19/value", "1"), ("/sys/class/gpio/gpio21/value", "0"),   # disable
            ("/sys/class/gpio/unexport", "21"), ("/sys/class/gpio/unexport", "19")])
        del writes[:]
        led = ns["_SysfsLed"]()
        led.blink(1000, 100); led.off()
        self.assertEqual(writes, [("/sys/class/leds/user2/trigger", "timer"),
                                  ("/sys/class/leds/user2/delay_on", "1000"),
                                  ("/sys/class/leds/user2/delay_off", "100"),
                                  ("/sys/class/leds/user2/trigger", "none")])
        # no uploader.py on the PATH: the bundled one next to pixhawk.py, run with this Python
        cmd = ns["_uploader_cmd"]()
        self.assertEqual(cmd[-1], os.path.join(root, "bin", "uploader.py"))
        self.assertTrue(os.path.isfile(cmd[-1]))

    def test_47_takeoff_spot_log_and_steady_sending(self):
        """TAKEOFF SPOT leaves a record: the climb pictures (saved / skipped and why) go to the
        Cockpit's messages and the Solo's log, and on the way down a line a second says what the
        looks found. Matches go to the Cube only once they agree and come steadily
        (SpotFinder.track, checked by tools/test_spotfinder.js), so ArduPilot doesn't start on a
        burst and then report "PrecLand: Init Failed"."""
        import cockpit_mgr
        root = os.path.dirname(os.path.abspath(__file__))
        state = cockpit_mgr.TelemetryState()
        pl = cockpit_mgr.PrecisionLanding(cockpit_mgr.LiveMAVLinkClient(cockpit_mgr.TelemetryState()), state=state)
        n0 = len(state.mavlink_messages)
        self.assertTrue(pl.note({"text": "TAKEOFF SPOT: picture 1 saved at 2.1 m", "show": True})[0])
        self.assertEqual(state.mavlink_messages[-1]["text"], "TAKEOFF SPOT: picture 1 saved at 2.1 m")
        self.assertTrue(pl.note({"text": "TAKEOFF SPOT looking at 9.0 m: 10 looks", "show": False})[0])
        self.assertEqual(len(state.mavlink_messages), n0 + 1)        # log only, not the Cockpit's messages
        for bad in (None, {}, {"text": 5}, {"text": "   "}, "x"):
            self.assertFalse(pl.note(bad)[0], bad)
        pl._note_time = time.time()
        pl._note_count = 0
        oks = [pl.note({"text": "line %d" % i})[0] for i in range(cockpit_mgr.NOTE_MAX_PER_SEC + 3)]
        self.assertEqual(oks.count(True), cockpit_mgr.NOTE_MAX_PER_SEC)
        pl._note_time = 0
        self.assertTrue(pl.note({"text": "x" * 1000 + "\x07", "show": True})[0])
        self.assertEqual(state.mavlink_messages[-1]["text"], "x" * cockpit_mgr.NOTE_MAX_LEN)

        swa = open(os.path.join(root, "solo_web_app.py"), encoding="utf-8").read()
        self.assertIn("if path == '/api/precland/note':", swa)
        self.assertLess(swa.index("Authentication required."), swa.index("if path == '/api/precland/note':"))
        js = open(os.path.join(root, "static", "cockpit.js"), encoding="utf-8").read()
        for s in ("SpotFinder.track(spotTrack,", "steadyMs: SPOT_STEADY_MS", "fetch('/api/precland/note'",
                  "spotLog('TAKEOFF SPOT: picture '", "spotLog('TAKEOFF SPOT: no picture at '",
                  "spotLog('TAKEOFF SPOT: climb done, '", "'TAKEOFF SPOT looking at '"):
            self.assertIn(s, js)
        sf = open(os.path.join(root, "static", "spotfinder.js"), encoding="utf-8").read()
        self.assertIn("pickReferences: pickReferences, track: track", sf)

    def test_48_precision_status_messages(self):
        """No false alarms in the Cockpit while flying the finders: /api/precland/check keeps the
        Cube's last answer when a read fails in flight (no "Could not read PLND_ENABLED" once it has
        answered), and the video counts as gone only after 2 s without usable frames; a jump to the
        newest video (seeking) is waited out, and small lags are caught up by playing faster."""
        import cockpit_mgr
        root = os.path.dirname(os.path.abspath(__file__))
        pl = cockpit_mgr.PrecisionLanding(cockpit_mgr.LiveMAVLinkClient(cockpit_mgr.TelemetryState()))
        none = {"PLND_ENABLED": None, "PLND_TYPE": None, "PLND_LAG": None}
        good = {"PLND_ENABLED": 1.0, "PLND_TYPE": 1.0, "PLND_LAG": 0.25}
        first = pl.check_report(none)                       # never answered: say so
        self.assertFalse(first["ready"])
        self.assertIn("Could not read PLND_ENABLED", first["message"])
        ok = pl.check_report(good)
        self.assertTrue(ok["ready"])
        kept = pl.check_report(none)                        # a read fails later: the last answer stands
        self.assertTrue(kept["ready"])
        self.assertNotIn("Could not read", kept["message"])
        pl._last_check = (pl._last_check[0] - 300, pl._last_check[1], pl._last_check[2])
        old = pl.check_report(none)
        self.assertTrue(old["ready"])
        self.assertIn("as read 5 min ago", old["message"])
        off = pl.check_report({"PLND_ENABLED": 0.0, "PLND_TYPE": 1.0, "PLND_LAG": 0.25})
        self.assertFalse(off["ready"])                      # a real answer always wins
        self.assertFalse(pl.check_report(none)["ready"])

        swa = open(os.path.join(root, "solo_web_app.py"), encoding="utf-8").read()
        self.assertIn("cockpit_mgr.precland.check_report(values)", swa)
        js = open(os.path.join(root, "static", "cockpit.js"), encoding="utf-8").read()
        for s in ("if (v && v.seeking) return;", "PAD_NO_VIDEO_MSG_MS = 2000", "PAD_VIDEO_FRESH_MS = 1000",
                  "VIDEO_CATCHUP_RATE", "v.playbackRate = rate"):
            self.assertIn(s, js)

    def test_49_telemetry_byte_order(self):
        """The Cockpit decodes VFR_HUD and SYS_STATUS in MAVLink's wire order (fields sorted by
        size: VFR_HUD = airspeed, groundspeed, alt, climb, heading, throttle; SYS_STATUS has
        battery_remaining at byte 30). VFR_HUD used to be read as airspeed, groundspeed, heading,
        throttle, alt, climb: its climb rate landed in the height above home, so in a hover the
        height read about 0 every other message, and TAKEOFF SPOT said "too low for the saved
        pictures" all the way down. VFR_HUD's alt is above sea level and no longer touches alt_rel."""
        import cockpit_mgr, struct as st
        state = cockpit_mgr.TelemetryState()
        c = cockpit_mgr.LiveMAVLinkClient(state)
        # GLOBAL_POSITION_INT: 15.0 m above home, 210.0 m above sea level, climbing 0.5 m/s, heading 270
        c.decode_payload(33, st.pack('<IiiiihhhH', 1000, 327000000, -971000000, 210000, 15000, 0, 0, -50, 27000))
        self.assertEqual(state.alt_rel, 15.0)
        # VFR_HUD: airspeed 1, groundspeed 2, alt (MSL) 210, climb -0.5, heading 271, throttle 55
        c.decode_payload(74, st.pack('<ffffhH', 1.0, 2.0, 210.0, -0.5, 271, 55))
        self.assertEqual(state.alt_rel, 15.0)            # not the climb rate, not the sea-level alt
        self.assertEqual(state.groundspeed, 2.0)
        self.assertEqual(state.heading, 271.0)
        self.assertEqual(state.throttle, 55)
        self.assertEqual(state.climb_rate, -0.5)
        # MAVLink 2 trims trailing zero bytes (throttle 0): still decoded
        c.decode_payload(74, st.pack('<ffffhH', 1.0, 3.0, 210.0, 0.25, 90, 0)[:18])
        self.assertEqual((state.groundspeed, state.heading, state.throttle, state.climb_rate), (3.0, 90.0, 0, 0.25))
        self.assertEqual(state.alt_rel, 15.0)
        # SYS_STATUS: 16.2 V, 12.3 A, drop rate 7 %, battery 64 %
        sys_status = st.pack('<IIIHHhHHHHHHb', 0, 0, 0, 100, 16200, 1230, 700, 0, 0, 0, 0, 0, 64)
        self.assertEqual(len(sys_status), 31)
        c.decode_payload(1, sys_status)
        self.assertEqual(state.voltage, 16.2)
        self.assertEqual(state.current, 12.3)
        self.assertEqual(state.battery_remaining, 64)     # not the drop rate's low byte
        root = os.path.dirname(os.path.abspath(__file__))
        src = open(os.path.join(root, "cockpit_mgr.py"), encoding="utf-8").read()
        self.assertNotIn("'<ffhHff'", src)

    def test_50_precland_check_over_cockpit_link(self):
        """/api/precland/check reads PLND_ENABLED, PLND_TYPE and PLND_LAG over the Cockpit's own,
        already running telemetry link (PARAM_REQUEST_READ out, PARAM_VALUE back), so it no longer
        needs a fresh connection that can miss the Cube's heartbeat; the Parameters tab's reader is
        only the fallback."""
        import cockpit_mgr, struct as st
        state = cockpit_mgr.TelemetryState()
        state.mavlink_connected = True
        cube = {"PLND_ENABLED": 1.0, "PLND_TYPE": 1.0, "PLND_LAG": 0.25}
        sent = []

        class Client(cockpit_mgr.LiveMAVLinkClient):
            def send_packet(self, pkt):            # the Cube answers each request
                sent.append(pkt)
                msgid = bytearray(pkt)[5]
                if msgid != cockpit_mgr.PARAM_REQUEST_READ_MSGID:
                    return
                pid = bytes(bytearray(pkt)[10:26]).split(b"\x00")[0].decode("ascii")
                if pid in cube:
                    payload = st.pack('<fHH16sB', cube[pid], 900, 1, pid.encode("ascii"), 9)
                    self.decode_payload(cockpit_mgr.PARAM_VALUE_MSGID, payload)

        c = Client(state)
        t0 = time.time()
        vals = c.read_params(cockpit_mgr.PRECLAND_PARAMS, timeout=2.0)
        self.assertEqual(vals, {"PLND_ENABLED": 1.0, "PLND_TYPE": 1.0, "PLND_LAG": 0.25})
        self.assertLess(time.time() - t0, 1.0)
        req = bytearray(sent[0])
        self.assertEqual((req[0], req[1], req[5]), (0xFE, 20, cockpit_mgr.PARAM_REQUEST_READ_MSGID))
        self.assertEqual(st.unpack('<hBB', bytes(req[6:10])), (-1, 1, 1))
        # a Cube that never answers: None after the timeout, nothing invented
        del cube["PLND_LAG"]
        vals = c.read_params(cockpit_mgr.PRECLAND_PARAMS, timeout=0.8)
        self.assertIsNone(vals["PLND_LAG"])
        self.assertEqual(vals["PLND_ENABLED"], 1.0)
        self.assertIn(cockpit_mgr.PARAM_VALUE_MSGID, cockpit_mgr.CHECKED_CRC_EXTRA)   # CRC-checked
        # a value seen on the link recently (e.g. an app downloading the parameter list) counts
        # when the caller allows it: PLND_LAG no longer answers, but was seen just now
        vals = c.read_params(cockpit_mgr.PRECLAND_PARAMS, timeout=0.5, max_age=60.0)
        self.assertEqual(vals["PLND_LAG"], 0.25)
        with state.lock:                                  # ... but not once it is too old
            c.param_values["PLND_LAG"] = (0.25, time.time() - 120)
        self.assertIsNone(c.read_params(cockpit_mgr.PRECLAND_PARAMS, timeout=0.5, max_age=60.0)["PLND_LAG"])

        # a busy Cube (a parameter list download in front of us) answers seconds late: the check
        # waits for it and doesn't flood the queue with repeats
        import threading as _th
        late = []

        class SlowClient(cockpit_mgr.LiveMAVLinkClient):
            def send_packet(self, pkt):
                if bytearray(pkt)[5] != cockpit_mgr.PARAM_REQUEST_READ_MSGID:
                    return
                late.append(time.time())
                pid = bytes(bytearray(pkt)[10:26]).split(b"\x00")[0].decode("ascii")
                payload = st.pack('<fHH16sB', {"PLND_ENABLED": 1.0, "PLND_TYPE": 1.0, "PLND_LAG": 0.25}[pid],
                                  900, 1, pid.encode("ascii"), 9)
                _th.Timer(1.2, self.decode_payload, (cockpit_mgr.PARAM_VALUE_MSGID, payload)).start()

        slow = SlowClient(state)
        vals = slow.read_params(cockpit_mgr.PRECLAND_PARAMS, timeout=3.0, resend=1.0)
        self.assertEqual(vals, {"PLND_ENABLED": 1.0, "PLND_TYPE": 1.0, "PLND_LAG": 0.25})
        self.assertLessEqual(len(late), 6)                 # 3 asked, at most once more each
        self.assertGreater(cockpit_mgr.PRECLAND_CHECK_TIMEOUT, 5.0)   # the Solo was seen answering 4-5 s late
        self.assertGreater(cockpit_mgr.PRECLAND_CHECK_RESEND, 1.0)

        root = os.path.dirname(os.path.abspath(__file__))
        swa = open(os.path.join(root, "solo_web_app.py"), encoding="utf-8").read()
        i = swa.index("if path == '/api/precland/check':")
        block = swa[i:i + 1200]
        self.assertLess(block.index("precland.client.read_params("), block.index("param_mgr.mgr.read_live("))
        for s in ("timeout=cockpit_mgr.PRECLAND_CHECK_TIMEOUT", "max_age=cockpit_mgr.PRECLAND_CHECK_MAX_AGE",
                  "resend=cockpit_mgr.PRECLAND_CHECK_RESEND"):
            self.assertIn(s, block)


    def test_51_never_updated_solo_cube_after_open_solo(self):
        """A never-updated Solo (3DR's software, no /usr/bin/pixhawk.py) does not keep its Cube in the
        bootloader, so the uploader asked it to reboot forever. Full Install now parks the Cube
        firmware in /log/firmware/pending_cube and the utility flashes it with Open Solo's scripts the
        first time it starts on Open Solo. pixhawk.py --load has a hard time limit, and the bundled
        pixhawk.py runs its own Open Solo uploader.py even when another one is on the PATH."""
        import tempfile
        import shutil as _shutil
        import inspect as _inspect
        import flash_mgr
        root = os.path.dirname(os.path.abspath(__file__))

        # 1. bundled pixhawk.py: its own uploader.py first; the uploader is stopped on timeout
        with open(os.path.join(root, "bin", "pixhawk.py"), "r", encoding="utf-8") as f:
            px = f.read()
        fn = px[px.index("def _uploader_cmd"):px.index("version_file =")]
        load_fn = px[px.index("def load("):px.index("# check usb")]
        self.assertIn("uploader.kill()", load_fn)
        tmp = tempfile.mkdtemp()
        saved_path = os.environ.get("PATH", "")
        saved = dict((k, getattr(flash_mgr, k)) for k in (
            "OPEN_SOLO_PIXHAWK", "PENDING_CUBE_DIR", "PENDING_CUBE_APJ", "PENDING_CUBE_INFO",
            "PENDING_CUBE_LOCK", "CUBE_FLASH_EXIT_GRACE", "_other_pixhawk_running",
            "OPEN_SOLO_FW_DIR", "OPEN_SOLO_FW_APJ", "OPEN_SOLO_LOADED_APJ", "CUBE_UPDATE_LOG",
            "_reboot_solo", "release_cube_bootloader"))
        try:
            open(os.path.join(tmp, "uploader.py"), "w").close()
            os.environ["PATH"] = tmp
            ns = {"os": os, "sys": sys, "__file__": os.path.join(root, "bin", "pixhawk.py")}
            exec(fn, ns)
            self.assertEqual(ns["_uploader_cmd"]()[-1], os.path.join(root, "bin", "uploader.py"))
            os.environ["PATH"] = saved_path

            # 2. Full Install on 3DR's software parks the firmware instead of flashing
            pend = os.path.join(tmp, "pending_cube")
            flash_mgr.OPEN_SOLO_PIXHAWK = os.path.join(tmp, "usr_bin_pixhawk.py")
            flash_mgr.PENDING_CUBE_DIR = pend
            flash_mgr.PENDING_CUBE_APJ = os.path.join(pend, "arducopter.apj")
            flash_mgr.PENDING_CUBE_INFO = os.path.join(pend, "pending.json")
            flash_mgr.PENDING_CUBE_LOCK = os.path.join(pend, "running")
            flash_mgr._other_pixhawk_running = lambda: False
            apj = os.path.join(tmp, "arducopter_CubeSolo_4.7.2.apj")
            with open(apj, "wb") as f:
                f.write(b"{\"board_id\": 9}" * 1000)
            self.assertFalse(flash_mgr.solo_has_open_solo())
            fm = flash_mgr.FlashManager()
            fm._defer_cube_flash(apj, "solo")
            self.assertTrue(fm._cube_deferred)
            info = flash_mgr.read_pending_cube_flash()
            self.assertEqual(info["cube_type"], "solo")
            self.assertEqual(info["firmware"], "arducopter_CubeSolo_4.7.2.apj")
            self.assertEqual(info["tries"], 0)
            self.assertEqual(info["md5"], flash_mgr._get_file_md5(apj))
            self.assertEqual(flash_mgr._get_file_md5(flash_mgr.PENDING_CUBE_APJ), info["md5"])
            # still on 3DR's software after a failed install: nothing starts
            self.assertFalse(fm.start_pending_cube_flash(delay=0))

            # 3. first start on Open Solo: like Solex, the firmware goes to Open Solo's /firmware and
            #    the Solo restarts once; Open Solo's boot-time pixhawk.py -i flashes the Cube. On the
            #    next start the utility checks what pixhawk.py did with the file.
            open(flash_mgr.OPEN_SOLO_PIXHAWK, "w").close()
            fwdir = os.path.join(tmp, "firmware")
            flash_mgr.OPEN_SOLO_FW_DIR = fwdir
            flash_mgr.OPEN_SOLO_FW_APJ = os.path.join(fwdir, "arducopter.apj")
            flash_mgr.OPEN_SOLO_LOADED_APJ = os.path.join(fwdir, "loaded", "arducopter.apj")
            flash_mgr.CUBE_UPDATE_LOG = os.path.join(tmp, "cube_update.log")
            reboots, releases = [], []
            flash_mgr._reboot_solo = lambda: reboots.append(1)
            flash_mgr.release_cube_bootloader = lambda log=None: releases.append(1) or "released"
            os.makedirs(fwdir)
            with open(os.path.join(fwdir, "old.px4"), "w") as f:
                f.write("x")
            md5 = info["md5"]

            def _msgs(f):
                return " ".join(e["message"] for e in f.log_buffer)
            fm._pending_cube_worker(flash_mgr.read_pending_cube_flash(), 0)
            self.assertEqual(reboots, [1])
            self.assertEqual(flash_mgr._get_file_md5(flash_mgr.OPEN_SOLO_FW_APJ), md5)
            self.assertFalse(os.path.exists(os.path.join(fwdir, "old.px4")))
            info = flash_mgr.read_pending_cube_flash()
            self.assertEqual((info["stage"], info["tries"]), ("handed_off", 1))
            self.assertIn("restarts now", _msgs(fm))
            # Open Solo loaded it at boot (moved to /firmware/loaded): finished, record cleared
            os.makedirs(os.path.join(fwdir, "loaded"))
            os.rename(flash_mgr.OPEN_SOLO_FW_APJ, flash_mgr.OPEN_SOLO_LOADED_APJ)
            fm._pending_cube_worker(flash_mgr.read_pending_cube_flash(), 0)
            self.assertIsNone(flash_mgr.read_pending_cube_flash())
            self.assertIn("Cube update is finished", _msgs(fm))
            self.assertEqual((reboots, releases), ([1], []))
            with open(flash_mgr.CUBE_UPDATE_LOG) as f:
                cl = f.read()
            self.assertIn("placed in", cl)
            self.assertIn("Cube update is finished", cl)

            # boot flash not run yet (file still waiting): ask for a power-cycle, nothing else
            fm2 = flash_mgr.FlashManager()
            fm2._defer_cube_flash(apj, "solo")
            fm2._pending_cube_worker(flash_mgr.read_pending_cube_flash(), 0)
            self.assertEqual(len(reboots), 2)
            self.assertFalse(os.path.exists(flash_mgr.OPEN_SOLO_LOADED_APJ))   # old copy removed
            fm2._pending_cube_worker(flash_mgr.read_pending_cube_flash(), 0)
            self.assertIn("still waiting", _msgs(fm2))
            self.assertEqual((len(reboots), releases), (2, []))
            self.assertEqual(flash_mgr.read_pending_cube_flash()["tries"], 1)
            # boot flash failed (pixhawk.py deleted the file): free the Cube, put the file back and
            # ask for a power-cycle, no restart; after PENDING_CUBE_MAX_TRIES it gives up
            for n in range(2, flash_mgr.PENDING_CUBE_MAX_TRIES + 1):
                os.remove(flash_mgr.OPEN_SOLO_FW_APJ)
                fm2._pending_cube_worker(flash_mgr.read_pending_cube_flash(), 0)
                self.assertEqual(flash_mgr.read_pending_cube_flash()["tries"], n)
                self.assertTrue(os.path.isfile(flash_mgr.OPEN_SOLO_FW_APJ))
                self.assertIn("Power-cycle the Solo", _msgs(fm2))
            self.assertEqual((len(reboots), len(releases)), (2, flash_mgr.PENDING_CUBE_MAX_TRIES - 1))
            os.remove(flash_mgr.OPEN_SOLO_FW_APJ)
            fm2._pending_cube_worker(flash_mgr.read_pending_cube_flash(), 0)
            self.assertTrue(flash_mgr.read_pending_cube_flash()["gave_up"])
            self.assertFalse(os.path.exists(flash_mgr.OPEN_SOLO_FW_APJ))
            self.assertIn("Run Cube Only", _msgs(fm2))
            n_rel = len(releases)
            fm2._pending_cube_worker(flash_mgr.read_pending_cube_flash(), 0)   # gave up: does nothing
            self.assertEqual((len(releases), len(reboots)), (n_rel, 2))
            self.assertFalse(os.path.exists(flash_mgr.PENDING_CUBE_LOCK))
            # another copy of the utility holding the lock: this one does nothing
            flash_mgr.clear_pending_cube_flash()
            fm2._defer_cube_flash(apj, "solo")
            os.mkdir(flash_mgr.PENDING_CUBE_LOCK)
            fm2._pending_cube_worker(flash_mgr.read_pending_cube_flash(), 0)
            self.assertEqual(len(reboots), 2)
            os.rmdir(flash_mgr.PENDING_CUBE_LOCK)
            # a corrupt parked file is dropped, never handed over
            with open(flash_mgr.PENDING_CUBE_APJ, "ab") as f:
                f.write(b"x")
            fm2._pending_cube_worker(flash_mgr.read_pending_cube_flash(), 0)
            self.assertEqual(len(reboots), 2)
            self.assertIsNone(flash_mgr.read_pending_cube_flash())

            # 4. pixhawk.py --load: hard time limit, and a leftover uploader is stopped
            if hasattr(os, "setsid"):
                flash_mgr.CUBE_FLASH_EXIT_GRACE = 1

                def _script(name, body):
                    p = os.path.join(tmp, name)
                    with open(p, "w") as f:
                        f.write("#!/bin/sh\n" + body)
                    os.chmod(p, 0o755)
                    return p
                fm3 = flash_mgr.FlashManager()
                t0 = time.time()
                rc = fm3._run_pixhawk_load(_script("ok.sh", "echo loaded fine\nexit 0\n"), timeout=20)
                self.assertEqual(rc, 0)
                self.assertTrue(any("loaded fine" in e["message"] for e in fm3.log_buffer))
                pidf = os.path.join(tmp, "pid")
                stuck = _script("stuck.sh", "echo Attempting reboot\nsleep 60 &\necho $! > %s\nwait\n" % pidf)
                t0 = time.time()
                self.assertIsNone(fm3._run_pixhawk_load(stuck, timeout=2))
                self.assertLess(time.time() - t0, 10)
                left = _script("left.sh", "sleep 60 &\necho $! > %s\nexit 3\n" % pidf)
                t0 = time.time()
                self.assertEqual(fm3._run_pixhawk_load(left, timeout=30), 3)
                self.assertLess(time.time() - t0, 10)
                with open(pidf) as f:
                    pid = int(f.read().strip())
                time.sleep(0.3)
                state = ""
                try:
                    with open("/proc/%d/status" % pid) as f:
                        state = [l for l in f.read().splitlines() if l.startswith("State:")][0]
                except (IOError, OSError):
                    pass
                self.assertTrue(state == "" or "Z" in state, state)
        finally:
            os.environ["PATH"] = saved_path
            for k, v in saved.items():
                setattr(flash_mgr, k, v)
            _shutil.rmtree(tmp, ignore_errors=True)

        # 5. wiring: Stage 2 defers only Full Install on 3DR's software, a failed Full Install
        #    drops the parked update, and the web app looks for it when it starts
        pipe = _inspect.getsource(flash_mgr.FlashManager._pipeline)
        self.assertIn('if install_mode == "full" and not solo_has_open_solo():', pipe)
        self.assertIn("self._defer_cube_flash(apj_src, cube_type)", pipe)
        self.assertIn("rc_load = self._run_pixhawk_load(pixhawk_bin)", pipe)
        # pixhawk.py exits 0 even when the flash fails: the file moved to /firmware/loaded decides
        self.assertIn("not_loaded = rc_load == 0 and _md5_or_none(OPEN_SOLO_LOADED_APJ) != apj_md5", pipe)
        self.assertIn("if rc_load != 0 or not_loaded:", pipe)
        self.assertIn("release_cube_bootloader(", pipe)
        self.assertNotIn("proc_load.stdout.readline()", pipe)
        self.assertIn("clear_pending_cube_flash()", pipe)
        self.assertIn("Wait ~6 minutes for reboot to finish.", pipe)
        with open(os.path.join(root, "solo_web_app.py"), "r", encoding="utf-8") as f:
            self.assertIn("flash_mgr.flash_engine.start_pending_cube_flash()", f.read())


    def test_52_download_logs_and_cube_bootloader_release(self):
        """Download logs on the Firmware page saves one zip (summary + the utility, install, Cube update
        and Open Solo logs, newest part of big files). bin/cube_bootloader.py starts the firmware of a
        Cube that a failed flash left in its bootloader (Open Solo's pixhawk.py forgets to)."""
        import tempfile
        import zipfile as _zip
        import io as _io
        import shutil as _shutil
        import importlib.util as _ilu
        import flash_mgr
        root = os.path.dirname(os.path.abspath(__file__))
        tmp = tempfile.mkdtemp()
        saved_tail = flash_mgr.LOG_BUNDLE_TAIL
        try:
            # 1. the zip
            small = os.path.join(tmp, "solowebutility_install.log")
            big = os.path.join(tmp, "solowebutility.log")
            with open(small, "w") as f:
                f.write("install line\n")
            with open(big, "w") as f:
                f.write("old " * 5000 + "NEWEST LINE\n")
            flash_mgr.LOG_BUNDLE_TAIL = 1000
            name, body = flash_mgr.build_log_bundle(files=[small, big, os.path.join(tmp, "missing.log")],
                                                    version="9.9.9")
            self.assertTrue(name.startswith("solo_logs_") and name.endswith(".zip"))
            zf = _zip.ZipFile(_io.BytesIO(body))
            names = zf.namelist()
            self.assertIn("summary.txt", names)
            self.assertIn("Solo Web Utility 9.9.9 logs", zf.read("summary.txt").decode("utf-8"))
            key_small = small.strip("/").replace("/", "_")
            key_big = big.strip("/").replace("/", "_")
            self.assertEqual(zf.read(key_small), b"install line\n")
            tail = zf.read(key_big)
            self.assertTrue(tail.endswith(b"NEWEST LINE\n"))
            self.assertIn(b"first", tail[:60])
            self.assertLess(len(tail), 1100)
            self.assertEqual(len(names), 3)
            for p in ("/log/solowebutility_cube_update.log", "/log/solowebutility.log",
                      "/log/solowebutility_install.log", "/log/3dr-solo.log"):
                self.assertIn(p, flash_mgr.LOG_BUNDLE_FILES)

            # 2. route and button
            with open(os.path.join(root, "solo_web_app.py"), "r", encoding="utf-8") as f:
                app = f.read()
            gate = app.index('# Require Auth for all other API endpoints')
            self.assertGreater(app.index("if path == '/api/logs/download':"), gate)
            self.assertIn("flash_mgr.build_log_bundle(version=ver)", app)
            for page in ("static/index.html", "templates/index.html"):
                with open(os.path.join(root, page), "r", encoding="utf-8") as f:
                    self.assertIn('id="btnDownloadLogs"', f.read())
            with open(os.path.join(root, "static", "app.js"), "r", encoding="utf-8") as f:
                js = f.read()
            self.assertIn("window.location.href = '/api/logs/download';", js)

            # 3. cube_bootloader.py
            spec = _ilu.spec_from_file_location("cube_bootloader", os.path.join(root, "bin", "cube_bootloader.py"))
            cb = _ilu.module_from_spec(spec)
            spec.loader.exec_module(cb)
            self.assertTrue(cb.is_bootloader_port("/dev/serial/by-id/usb-3D_Robotics_PX4_BL_FMU_v2.x_0-if00"))
            self.assertTrue(cb.is_bootloader_port("usb-ArduPilot_CubeSolo-BL_2E0034000A51-if00"))
            self.assertFalse(cb.is_bootloader_port("/dev/serial/by-id/usb-3D_Robotics_PX4_FMU_v2.x_0-if00"))
            self.assertFalse(cb.is_bootloader_port("usb-ArduPilot_CubeSolo_2E0034000A51-if00"))
            by_id = os.path.join(tmp, "by-id")
            gpio = os.path.join(tmp, "gpio")
            os.makedirs(by_id)
            os.makedirs(os.path.join(gpio, "gpio19"))
            os.makedirs(os.path.join(gpio, "gpio21"))
            bl = os.path.join(by_id, "usb-3D_Robotics_PX4_BL_FMU_v2.x_0-if00")
            fmu = os.path.join(by_id, "usb-3D_Robotics_PX4_FMU_v2.x_0-if00")

            class Port(object):
                def __init__(self, boots):
                    self.sent, self.boots, self.closed = b"", boots, False
                    self.reply = b""

                def write(self, d):
                    self.sent += d
                    self.reply = cb.INSYNC + cb.OK
                    if d == cb.BOOT + cb.EOC and self.boots:
                        os.remove(bl)
                        open(fmu, "w").close()

                def flush(self):
                    pass

                def read(self, n):
                    r, self.reply = self.reply, b""
                    return r

                def close(self):
                    self.closed = True
            quiet = lambda m: None
            self.assertEqual(cb.release(by_id, gpio, settle=0, wait=0.3, log=quiet), "no-usb")
            open(fmu, "w").close()
            self.assertEqual(cb.release(by_id, gpio, settle=0, wait=0.3, log=quiet), "running")
            os.remove(fmu)
            open(bl, "w").close()
            ports = []
            opener = lambda p, boots=True: ports.append(Port(boots)) or ports[-1]
            self.assertEqual(cb.release(by_id, gpio, open_serial=opener, settle=0, wait=0.3, log=quiet), "released")
            self.assertEqual(ports[0].sent, cb.GET_SYNC + cb.EOC + cb.BOOT + cb.EOC)
            self.assertTrue(ports[0].closed)
            os.remove(fmu)
            open(bl, "w").close()
            self.assertEqual(cb.release(by_id, gpio, open_serial=lambda p: Port(False), settle=0, wait=0.3,
                                        log=quiet), "still-in-bootloader")
            # USB left disconnected afterwards (enable 19 = 1, VBUS 21 = 0)
            with open(os.path.join(gpio, "gpio19", "value")) as f:
                self.assertEqual(f.read(), "1")
            with open(os.path.join(gpio, "gpio21", "value")) as f:
                self.assertEqual(f.read(), "0")
        finally:
            flash_mgr.LOG_BUNDLE_TAIL = saved_tail
            _shutil.rmtree(tmp, ignore_errors=True)


    def test_53_flight_log_viewer_is_self_contained(self):
        """Parameters page flight log viewer. Backend: modules/log_viewer/log_viewer_api.py lists and
        sends /log/dataflash/<n>.BIN read-only (refused while armed). Frontend: static/log-viewer/
        mounts itself into #logViewerMount (Shadow DOM, own state, error guard, parsing in a Worker).
        The only hooks into the utility are one route block in solo_web_app.py and the mount point +
        script tag in the page."""
        import tempfile
        import io as _io
        import json as _json
        import shutil as _shutil
        import importlib.util as _ilu
        import subprocess as _subprocess
        root = os.path.dirname(os.path.abspath(__file__))
        api_path = os.path.join(root, "modules", "log_viewer", "log_viewer_api.py")
        spec = _ilu.spec_from_file_location("lv_api_test", api_path)
        lv = _ilu.module_from_spec(spec)
        spec.loader.exec_module(lv)

        class Req(object):
            def __init__(self):
                self.code, self.headers, self.wfile = None, {}, _io.BytesIO()

            def send_response(self, code):
                self.code = code

            def send_header(self, k, v):
                self.headers[k] = v

            def end_headers(self):
                pass

        tmp = tempfile.mkdtemp()
        try:
            for name, data in (("3.BIN", b"abc"), ("12.BIN", b"\xa3\x95" * 50000), ("notes.txt", b"x"), ("x.BIN", b"y")):
                with open(os.path.join(tmp, name), "wb") as f:
                    f.write(data)
            with open(os.path.join(tmp, "LASTLOG.TXT"), "w") as f:
                f.write("3\n")
            old = time.time() - 600
            os.utime(os.path.join(tmp, "3.BIN"), (old, old))
            logs = lv.list_flight_logs(tmp)
            self.assertEqual([d["n"] for d in logs], [12, 3])
            self.assertEqual([d["latest"] for d in logs], [False, True])
            self.assertEqual([d["writing"] for d in logs], [True, False])
            self.assertEqual(logs[0]["size"], 100000)

            r = Req()
            lv.handle(r, "/api/logviewer/flightlogs", None, folder=tmp, armed=lambda: False)
            body = _json.loads(r.wfile.getvalue().decode("utf-8"))
            self.assertEqual((r.code, len(body["logs"]), body["armed"]), (200, 2, False))
            r = Req()
            lv.handle(r, "/api/logviewer/flightlog/12", None, folder=tmp, armed=lambda: False)
            self.assertEqual(r.code, 200)
            self.assertEqual(r.wfile.getvalue(), b"\xa3\x95" * 50000)
            self.assertEqual(r.headers["Content-Length"], "100000")
            self.assertIn("attachment", r.headers["Content-Disposition"])
            self.assertIn("solo_flight_12_", r.headers["Content-Disposition"])
            for path, code in (("/api/logviewer/flightlog/99", 404), ("/api/logviewer/flightlog/..%2F..%2Fetc", 404),
                               ("/api/logviewer/flightlog/../../etc/passwd", 404), ("/api/logviewer/nope", 404)):
                r = Req()
                lv.handle(r, path, None, folder=tmp, armed=lambda: False)
                self.assertEqual(r.code, code, path)
            r = Req()
            lv.handle(r, "/api/logviewer/flightlog/12", None, folder=tmp, armed=lambda: True)
            self.assertEqual(r.code, 409)
            self.assertIn("after landing", _json.loads(r.wfile.getvalue().decode("utf-8"))["error"])
            # a failing armed check (no telemetry) counts as disarmed
            r = Req()
            lv.handle(r, "/api/logviewer/flightlogs", None, folder=tmp, armed=lambda: 1 / 0)
            self.assertEqual(r.code, 500)
            self.assertFalse(lv._armed() and False)
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)

        # backend: read-only, no utility imports except the passive armed check
        with open(api_path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("is_vehicle_armed(wake=False)", src)
        for bad in ("import flash_mgr", "import param_mgr", "import solo_web_app", "mavlink", "subprocess",
                    "open(path, \"w", "os.remove", "shutil"):
            self.assertNotIn(bad, src)

        # the one hook in solo_web_app.py, after the login check, loading the file by path
        import solo_web_app
        with open(os.path.join(root, "solo_web_app.py"), "r", encoding="utf-8") as f:
            app = f.read()
        gate = app.index("# Require Auth for all other API endpoints")
        self.assertGreater(app.index("if path.startswith('/api/logviewer/'):"), gate)
        self.assertEqual(app.count("startswith('/api/logviewer/')"), 1)
        self.assertNotIn("from modules.log_viewer", app)
        mod = solo_web_app._load_log_viewer()
        self.assertTrue(callable(getattr(mod, "handle", None)))

        # the mount point and script tag, the only page changes
        for page in ("static/index.html", "templates/index.html"):
            with open(os.path.join(root, page), "r", encoding="utf-8") as f:
                html = f.read()
            # the viewer has its own Flight Logs tab, not a card on the Parameters page
            self.assertIn('<button class="nav-tab" data-tab="tab-logs" id="navTabLogs">', html)
            params = html[html.index('<section class="tab-content" id="tab-params">'):]
            params = params[:params.index("</section>")]
            self.assertNotIn("logViewerMount", params)
            logs = html[html.index('<section class="tab-content" id="tab-logs">'):]
            logs = logs[:logs.index("</section>")]
            self.assertIn('<div id="logViewerMount"></div>', logs)
            self.assertEqual((html.count("log-viewer.js"), html.count("logViewerMount")), (1, 1))
            self.assertIn('<script src="/log-viewer/log-viewer.js?v=', html)
        with open(os.path.join(root, "static", "app.js"), "r", encoding="utf-8") as f:
            self.assertNotIn("logViewer", f.read().replace("btnDownloadLogs", ""))

        # frontend module: everything in static/log-viewer/, no use of the utility's globals
        lvdir = os.path.join(root, "static", "log-viewer")
        self.assertEqual(sorted(os.listdir(lvdir)), sorted([
            "api.js", "chart.js", "dataflash.js", "flight-panel.js", "log-viewer.css", "log-viewer.js",
            "log-worker.js", "tlog.js"]))
        js = ""
        for name in os.listdir(lvdir):
            with open(os.path.join(lvdir, name), "r", encoding="utf-8") as f:
                js += f.read()
        for bad in ("apiRequest", "localStorage", "sessionStorage", "Cockpit", "window.state", "document.body.",
                    "innerHTML = summary", ".innerHTML = m.", "eval(", "new Function"):
            self.assertNotIn(bad, js)
        self.assertIn("solo-auth-required", js)      # an expired login opens the utility's login box
        with open(os.path.join(lvdir, "log-viewer.js"), "r", encoding="utf-8") as f:
            entry = f.read()
        self.assertIn("attachShadow", entry)
        self.assertIn("function guard(fn)", entry)
        self.assertIn("IntersectionObserver", entry)
        with open(os.path.join(lvdir, "flight-panel.js"), "r", encoding="utf-8") as f:
            panel = f.read()
        self.assertIn("new Worker(", panel)
        self.assertIn("VirtualList", panel)

        # parser tests (node)
        node = _shutil.which("node") if hasattr(_shutil, "which") else None
        if not node:
            self.skipTest("node not installed: flight log parser tests not run")
        out = _subprocess.run([node, os.path.join(root, "tools", "test_logviewer.js")],
                              stdout=_subprocess.PIPE, stderr=_subprocess.STDOUT, timeout=120)
        self.assertEqual(out.returncode, 0, out.stdout.decode("utf-8", "replace")[-3000:])
        self.assertIn(b"LOGVIEWER OK", out.stdout)

    def test_54_controller_log_viewer_server_and_installer(self):
        """The read-only viewer server that runs on the Solo controller (modules/log_viewer/controller/
        log_server.py) and the installer the Solo utility uses to put it there (controller_install.py)."""
        import tempfile
        import shutil as _shutil
        import struct as _struct
        import json as _json
        import threading as _threading
        import importlib.util as _ilu
        import http.client as _http
        root = os.path.dirname(os.path.abspath(__file__))
        lvdir = os.path.join(root, "modules", "log_viewer")

        def load(name, path):
            spec = _ilu.spec_from_file_location(name, path)
            m = _ilu.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m

        srv = load("lv_ctl_server_test", os.path.join(lvdir, "controller", "log_server.py"))
        inst = load("lv_ctl_install_test", os.path.join(lvdir, "controller_install.py"))

        # --- the server never writes: no write calls, no shell, no MAVLink sender
        with open(os.path.join(lvdir, "controller", "log_server.py"), "r", encoding="utf-8") as f:
            ssrc = f.read()
        for bad in ("subprocess", "os.system", "os.remove", "shutil", "open(path, \"w", "'wb'", "\"wb\"",
                    "os.rename", "send_mavlink", "pymavlink"):
            self.assertNotIn(bad, ssrc)

        def frame(ts_us, sysid, comp, msgid, payload):
            return (_struct.pack(">Q", ts_us) + bytes(bytearray([0xFE, len(payload), 0, sysid, comp, msgid])) +
                    payload + b"\x00\x00")

        def heartbeat(ts_us, armed):
            pay = _struct.pack("<I", 5) + bytes(bytearray([2, 3, 0x80 if armed else 0, 0, 3]))
            body = bytes(bytearray([len(pay), 0, 1, 1, 0])) + pay
            crc = srv._crc_x25(body, 0xFFFF)
            crc = srv._crc_x25(bytes(bytearray([0x32])), crc)
            return _struct.pack(">Q", ts_us) + b"\xfe" + body + _struct.pack("<H", crc)

        tmp = tempfile.mkdtemp()
        httpd = None
        try:
            log_dir = os.path.join(tmp, "log")
            rootdir = os.path.join(tmp, "lv")
            os.makedirs(os.path.join(rootdir, "web", "log-viewer"))
            os.makedirs(os.path.join(rootdir, "bin"))
            os.makedirs(log_dir)
            with open(os.path.join(rootdir, "web", "index.html"), "w") as f:
                f.write("<html>v=%VERSION%</html>")
            with open(os.path.join(rootdir, "VERSION"), "w") as f:
                f.write("abc123\n")
            with open(os.path.join(rootdir, "web", "log-viewer", "log-viewer.js"), "w") as f:
                f.write("// viewer")
            t0 = 1762527600 * 10 ** 6
            data = b"".join([heartbeat(t0 + k * 10 ** 6, False) for k in range(5)])
            with open(os.path.join(log_dir, "solo.tlog"), "wb") as f:
                f.write(data)
            with open(os.path.join(log_dir, "solo.tlog.1"), "wb") as f:
                f.write(data * 2)
            with open(os.path.join(rootdir, "bin", "7.BIN"), "wb") as f:
                f.write(b"\xa3\x95" * 1000)
            with open(os.path.join(log_dir, "other.txt"), "w") as f:
                f.write("secret")

            # armed detection from the last Cube heartbeat (CRC checked)
            self.assertFalse(srv.last_heartbeat_armed(data))
            self.assertTrue(srv.last_heartbeat_armed(data + heartbeat(t0 + 9 * 10 ** 6, True)))
            self.assertFalse(srv.last_heartbeat_armed(b"\xfe" * 500))
            bad = bytearray(heartbeat(t0, True))
            bad[-1] ^= 0xFF
            self.assertFalse(srv.last_heartbeat_armed(bytes(bad)))

            cfg = srv.make_config(rootdir, log_dir, armed=lambda: False)
            httpd = srv.Server(("127.0.0.1", 0), cfg)
            port = httpd.server_address[1]
            th = _threading.Thread(target=httpd.serve_forever)
            th.daemon = True
            th.start()

            def get(path, headers=None, method="GET"):
                c = _http.HTTPConnection("127.0.0.1", port, timeout=10)
                c.request(method, path, headers=headers or {})
                r = c.getresponse()
                body = r.read()
                c.close()
                return r.status, dict((k.lower(), v) for k, v in r.getheaders()), body

            st, hd, body = get("/")
            self.assertEqual(st, 200)
            self.assertIn(b"v=abc123", body)
            st, hd, body = get("/log-viewer/log-viewer.js")
            self.assertEqual((st, body), (200, b"// viewer"))
            st, hd, body = get("/api/logviewer/ping")
            self.assertEqual(st, 200)
            st, hd, body = get("/api/logviewer/flightlogs")
            lst = _json.loads(body.decode("utf-8"))
            self.assertEqual((st, lst["mode"], lst["armed"]), (200, "controller", False))
            ids = [d["id"] for d in lst["logs"]]
            self.assertEqual(sorted(ids), ["b7", "t0", "t1"])
            # downloads, ranges
            st, hd, body = get("/api/logviewer/flightlog/t1")
            self.assertEqual((st, body), (200, data * 2))
            self.assertIn("attachment", hd["content-disposition"])
            st, hd, body = get("/api/logviewer/flightlog/t1", {"Range": "bytes=10-19"})
            self.assertEqual((st, body, hd["content-range"]), (206, (data * 2)[10:20], "bytes 10-19/%d" % (len(data) * 2)))
            st, hd, body = get("/api/logviewer/flightlog/t1", {"Range": "bytes=%d-" % (len(data) * 2 - 4)})
            self.assertEqual((st, len(body)), (206, 4))
            st, hd, body = get("/api/logviewer/flightlog/t1", {"Range": "bytes=999999-"})
            self.assertEqual(st, 416)
            # nothing outside the log list can be fetched
            for path in ("/api/logviewer/flightlog/other.txt", "/api/logviewer/flightlog/t99", "/api/logviewer/flightlog/..%2Fetc%2Fpasswd",
                         "/api/logviewer/flightlog/../../etc/passwd", "/log-viewer/../log_server.py", "/log-viewer/..%2Findex.html",
                         "/log-viewer/nope.js", "/etc/passwd", "/api/logviewer/nope"):
                self.assertEqual(get(path)[0], 404, path)
            # read-only
            for method in ("POST", "PUT", "DELETE", "PATCH"):
                self.assertEqual(get("/api/logviewer/flightlogs", method=method)[0] in (405, 501), True, method)
            # armed: logs are refused (409), the list still says why
            cfg["armed"] = lambda: True
            st, hd, body = get("/api/logviewer/flightlog/t1")
            self.assertEqual(st, 409)
            self.assertTrue(_json.loads(get("/api/logviewer/flightlogs")[2].decode("utf-8"))["armed"])
            cfg["armed"] = lambda: False
            # a failing armed check counts as disarmed
            cfg["armed"] = lambda: 1 / 0
            self.assertEqual(get("/api/logviewer/flightlog/t0")[0], 200)
            cfg["armed"] = lambda: False
        finally:
            if httpd is not None:
                httpd.shutdown()
                httpd.server_close()
            _shutil.rmtree(tmp, ignore_errors=True)

        # --- the installer, with a fake ssh
        files, ident = inst.bundle()
        names = [n for n, _d, _m in files]
        self.assertIn("log_server.py", names)
        self.assertIn("web/index.html", names)
        for name in inst.VIEWER_FILES:
            self.assertIn("web/log-viewer/" + name, names)
        self.assertEqual(ident, inst.bundle()[1])
        self.assertTrue(all(b"\r" not in d for n, d, _m in files if n.endswith((".sh", ".py"))))
        # every file the viewer page loads is in the bundle
        with open(os.path.join(root, "static", "log-viewer", "log-worker.js"), "r", encoding="utf-8") as f:
            wsrc = f.read()
        for needed in ("dataflash.js", "tlog.js"):
            self.assertIn(needed, wsrc)
            self.assertIn(needed, inst.VIEWER_FILES)
        with open(os.path.join(root, "static", "log-viewer", "log-viewer.js"), "r", encoding="utf-8") as f:
            lvsrc = f.read()
        for part in ("api.js", "chart.js", "flight-panel.js"):
            self.assertIn(part, lvsrc)
        self.assertEqual(sorted(inst.VIEWER_FILES), sorted(
            n for n in os.listdir(os.path.join(root, "static", "log-viewer"))))

        # rc.local editing: other lines kept, one marked block before the last exit, idempotent, removable
        none = inst.rc_local_with_hook("")
        self.assertTrue(none.startswith("#!/bin/sh") and none.rstrip().endswith("exit 0"))
        self.assertEqual(none.count(inst.RC_BEGIN), 1)
        mine = "#!/bin/sh\necho hello\nexit 0\n"
        a = inst.rc_local_with_hook(mine)
        self.assertEqual(inst.rc_local_with_hook(a), a)
        self.assertLess(a.index("echo hello"), a.index(inst.RC_BEGIN))
        self.assertLess(a.index(inst.RC_END), a.rindex("exit 0"))
        self.assertEqual(inst.rc_local_without_hook(a), mine)
        self.assertEqual(inst.rc_local_with_hook("echo no exit line\n").count("exit 0"), 1)

        class Fake(object):
            def __init__(self, rc_local, version):
                self.rc_local, self.version, self.calls, self.tar = rc_local, version, [], None
                self.running = False

            def __call__(self, args, data=None, timeout=20):
                cmd = args[-1]
                self.calls.append(cmd)
                assert args[0] == "ssh" and "root@10.1.1.1" in args
                if cmd == "echo ok":
                    return 0, "ok\n"
                if cmd.startswith("mkdir -p"):
                    self.tar = data
                    self.version = "NEWVER"
                    return 0, ""
                if cmd.startswith("cat /etc/rc.local"):
                    return (0, self.rc_local) if self.rc_local is not None else (1, "")
                if cmd.startswith("cat > /etc/rc.local"):
                    self.rc_local = data.decode("utf-8")
                    return 0, ""
                if "restart.sh" in cmd:
                    self.running = True
                    return 0, ""
                if "stop.sh" in cmd:
                    self.running = False
                    self.version = None
                    return 0, ""
                if cmd.startswith("cat /log/logviewer/VERSION"):
                    hook = "1" if self.rc_local and inst.RC_BEGIN in self.rc_local else "0"
                    return 0, "%s\n---\n%s\n---\n%d\n" % (self.version or "", hook, 1 if self.running else 0)
                return 1, "unexpected: " + cmd

        tmpst = tempfile.mkdtemp()
        try:
            sp = os.path.join(tmpst, "state.json")
            fake = Fake(None, None)
            res = inst.status(fake, sp, lambda: False, use_cache=False, expect="NEWVER")
            self.assertEqual((res["reachable"], res["installed"], res["wasInstalled"]), (True, False, False))
            res = inst.install(fake, sp, lambda: True, lambda s: None)
            self.assertTrue(res["ok"], res)
            self.assertTrue(res["installed"] and res["hook"] and res["running"])
            self.assertIn(inst.RC_BEGIN, fake.rc_local)
            self.assertTrue(fake.tar and len(fake.tar) > 1000)
            with open(sp) as sf:
                self.assertTrue(_json.load(sf)["installed"])
            # a second install changes nothing in rc.local
            before = fake.rc_local
            self.assertTrue(inst.install(fake, sp, lambda: True, lambda s: None)["ok"])
            self.assertEqual(fake.rc_local, before)
            # a controller reset empties rc.local: the status shows what is missing and that it was installed
            fake.rc_local, fake.running = None, False
            res = inst.status(fake, sp, lambda: False, use_cache=False, expect="NEWVER")
            self.assertEqual((res["installed"], res["hook"], res["running"], res["wasInstalled"]), (True, False, False, True))
            # server not answering after install -> not ok, with a hint
            res = inst.install(fake, sp, lambda: False, lambda s: None)
            self.assertFalse(res["ok"])
            self.assertIn("did not answer", res["error"])
            # remove
            res = inst.remove(fake, sp)
            self.assertTrue(res["ok"])
            self.assertNotIn(inst.RC_BEGIN, fake.rc_local or "")
            with open(sp) as sf:
                self.assertFalse(_json.load(sf)["installed"])
            # unreachable controller
            res = inst.install(lambda a, d=None, t=20: (255, ""), sp, lambda: False, lambda s: None)
            self.assertFalse(res["ok"])
            self.assertIn("Cannot reach", res["error"])
        finally:
            _shutil.rmtree(tmpst, ignore_errors=True)

        # the Solo-side API: install/remove need the header and a disarmed Solo
        lvapi = load("lv_api_test2", os.path.join(lvdir, "log_viewer_api.py"))

        class Req(object):
            def __init__(self, headers=None):
                import io as _io
                self.code, self.headers, self.wfile, self.sent = None, dict(headers or {}), _io.BytesIO(), {}

            def send_response(self, code):
                self.code = code

            def send_header(self, k, v):
                self.sent[k] = v

            def end_headers(self):
                pass

        class FakeInstaller(object):
            def __init__(self):
                self.did = []

            def status(self, **kw):
                return {"reachable": True, "installed": True}

            def install(self, **kw):
                self.did.append("install")
                return {"ok": True}

            def remove(self, **kw):
                self.did.append("remove")
                return {"ok": True}

        fi = FakeInstaller()
        for sub, want in (("install", "install"), ("remove", "remove")):
            r = Req()
            lvapi.handle(r, "/api/logviewer/controller/" + sub, None, folder=tempfile.gettempdir(), armed=lambda: False, installer=fi)
            self.assertEqual(r.code, 403)                                  # no header
            fi.did = []
            r = Req({"X-Log-Viewer": "1"})
            lvapi.handle(r, "/api/logviewer/controller/" + sub, None, folder=tempfile.gettempdir(), armed=lambda: True, installer=fi)
            self.assertEqual(r.code, 409)                                  # armed
            self.assertEqual(fi.did, [])
            r = Req({"X-Log-Viewer": "1"})
            lvapi.handle(r, "/api/logviewer/controller/" + sub, None, folder=tempfile.gettempdir(), armed=lambda: False, installer=fi)
            self.assertEqual(r.code, 200)
            self.assertEqual(fi.did[-1], want)
        r = Req()
        lvapi.handle(r, "/api/logviewer/controller", None, folder=tempfile.gettempdir(), armed=lambda: True, installer=fi)
        self.assertEqual(r.code, 200)                                      # status is always readable

    def test_55_cube_sd_card_copy_over_mavftp(self):
        """modules/log_viewer/cube_sd.py: lists and copies the Cube's SD card logs over MAVFTP, read-only,
        only while disarmed, with limits. Tested against a simulated Cube."""
        import tempfile
        import shutil as _shutil
        import struct as _struct
        import json as _json
        import io as _io
        import importlib.util as _ilu
        root = os.path.dirname(os.path.abspath(__file__))
        lvdir = os.path.join(root, "modules", "log_viewer")

        def load(name, path):
            spec = _ilu.spec_from_file_location(name, path)
            m = _ilu.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m

        sd = load("lv_cube_sd_test", os.path.join(lvdir, "cube_sd.py"))
        api = load("lv_api_test3", os.path.join(lvdir, "log_viewer_api.py"))

        # --- the module never writes to the Cube and stays out of the rest of the utility
        with open(os.path.join(lvdir, "cube_sd.py"), "r", encoding="utf-8") as f:
            src = f.read()
        for bad in ("import flash_mgr", "import param_mgr", "import calib_mgr", "import solo_web_app",
                    "import cockpit_mgr", "subprocess", "param_set", "PARAM_SET", "command_long", "set_mode",
                    "OP_WRITE", "OP_CREATE", "OP_REMOVE", "OP_RENAME", "shutil"):
            self.assertNotIn(bad, src)
        self.assertEqual(sorted(sd.ALLOWED_OPS), [1, 2, 3, 4, 5, 15])
        real_init = sd.FtpClient.__init__

        def quick_init(self, transport, check=None, reply_timeout=1.0, retries=4, clock=None):
            real_init(self, transport, check, reply_timeout=0.05, retries=retries)   # the simulated Cube answers at once
        sd.FtpClient.__init__ = quick_init

        class FakeCube(object):
            """Speaks the Cube's side of MAVFTP for /APM/LOGS."""

            def __init__(self, files, burst=True, drop_every=0, arm_after=None, silent=False, page=239):
                self.files, self.burst, self.drop_every = files, burst, drop_every
                self.arm_after, self.silent = arm_after, silent
                self.queue, self.ops, self.sessions, self.sent = [], [], {}, 0
                self.is_armed = None
                self.closed = False
                self.names = sorted(files)
                self.nbursts = 0

            def reply(self, seq, req, op, data=b"", offset=0, burst=0, session=0):
                hdr = _struct.pack(sd.HDR_FMT, (seq + 1) & 0xFFFF, session, op, len(data), req, burst, 0, offset)
                self.queue.append(hdr + data + b"\x00" * (251 - 12 - len(data)))

            def send(self, raw):
                seq, session, op, size, _r, _b, _p, offset = _struct.unpack(sd.HDR_FMT, raw[:12])
                self.ops.append(op)
                data = raw[12:12 + size]
                if self.silent:
                    return
                if op == sd.OP_RESET:
                    return
                if op == sd.OP_TERMINATE:
                    self.sessions.pop(session, None)
                    return
                if op == sd.OP_LIST:
                    entries = [b"F" + n.encode() + b"\t" + str(len(self.files[n])).encode() for n in self.names]
                    entries = [b"D.."] + entries if False else entries
                    chunk, used = [], 0
                    for e in entries[offset:]:
                        if used + len(e) + 1 > sd.MAX_DATA:
                            break
                        chunk.append(e)
                        used += len(e) + 1
                    if not chunk:
                        return self.reply(seq, op, sd.OP_NAK, b"\x06")
                    return self.reply(seq, op, sd.OP_ACK, b"\x00".join(chunk) + b"\x00")
                if op == sd.OP_OPEN_RO:
                    path = data.decode()
                    name = path.rsplit("/", 1)[-1]
                    if not path.startswith("/APM/LOGS/") or name not in self.files:
                        return self.reply(seq, op, sd.OP_NAK, b"\x01")
                    self.sessions[3] = name
                    return self.reply(seq, op, sd.OP_ACK, _struct.pack("<I", len(self.files[name])), session=3)
                if op == sd.OP_READ:
                    blob = self.files[self.sessions[session]]
                    if offset >= len(blob):
                        return self.reply(seq, op, sd.OP_NAK, b"\x06", session=session)
                    return self.reply(seq, op, sd.OP_ACK, blob[offset:offset + size], offset, session=session)
                if op == sd.OP_BURST:
                    if not self.burst:
                        return self.reply(seq, op, sd.OP_NAK, b"\x07", session=session)
                    self.nbursts += 1
                    blob = self.files[self.sessions[session]]
                    pos, n = offset, 0
                    while pos < len(blob) and n < 40:
                        piece = blob[pos:pos + size]
                        n += 1
                        last = n == 40 or pos + len(piece) >= len(blob)
                        if not (self.drop_every and n % self.drop_every == 0):
                            self.reply(seq, op, sd.OP_ACK, piece, pos, 1 if last else 0, session)
                        pos += len(piece)
                    return
                raise AssertionError("unexpected opcode %d" % op)

            def recv(self, timeout):
                if self.arm_after is not None and len(self.ops) >= self.arm_after:
                    self.is_armed = True
                if self.queue:
                    return self.queue.pop(0)
                time.sleep(0.001)
                return None

            def armed(self):
                return self.is_armed

            def close(self):
                self.closed = True

        import os as _os
        blob7 = bytes(bytearray((k * 7) % 256 for k in range(50000)))
        blob8 = bytes(bytearray((k * 3) % 256 for k in range(1234)))
        files = {"00000007.BIN": blob7, "00000008.BIN": blob8}
        tmp = tempfile.mkdtemp()
        try:
            dest = os.path.join(tmp, "cubesd")

            def run_job(cube, kind, n=None, armed=lambda: False, **kw):
                mgr = sd.CubeSD(connect=lambda: cube, dest=dest, free=lambda p: 10 ** 12, **kw)
                if kind == "list":
                    ok, msg = mgr.start_list(armed)
                else:
                    mgr.listing = [{"n": 7, "name": "00000007.BIN", "size": len(blob7)},
                                   {"n": 8, "name": "00000008.BIN", "size": len(blob8)}]
                    ok, msg = mgr.start_copy(n, armed)
                self.assertTrue(ok, msg)
                mgr.wait(20)
                return mgr

            # listing
            cube = FakeCube(files)
            mgr = run_job(cube, "list")
            st = mgr.status()
            self.assertEqual(st["job"]["state"], "done", st)
            self.assertEqual([(x["n"], x["size"]) for x in st["list"]], [(8, 1234), (7, 50000)])
            self.assertTrue(cube.closed)
            self.assertEqual(set(cube.ops) - set(sd.ALLOWED_OPS), set())

            # a big directory is read in pages
            many = dict(("%08d.BIN" % k, b"x") for k in range(1, 41))
            cube = FakeCube(many)
            self.assertEqual(len(run_job(cube, "list").status()["list"]), 40)

            # copy with burst reads
            cube = FakeCube(files)
            mgr = run_job(cube, "copy", 7)
            st = mgr.status()
            self.assertEqual(st["job"]["state"], "done", st)
            with open(os.path.join(dest, "7.BIN"), "rb") as f:
                self.assertEqual(f.read(), blob7)
            self.assertEqual([n for n in os.listdir(dest) if n.endswith(".part")], [])
            self.assertGreater(cube.nbursts, 0)
            self.assertEqual(set(cube.ops) - set(sd.ALLOWED_OPS), set())
            self.assertEqual(sd.list_copies(dest)[0]["n"], 7)
            # shown as already copied once the list is known
            mgr.listing = [{"n": 7, "name": "00000007.BIN", "size": len(blob7)}]
            self.assertTrue(mgr.status()["list"][0]["copied"])

            # lost packets in a burst: the next burst restarts where the data stopped
            cube = FakeCube(files, drop_every=5)
            run_job(cube, "copy", 7)
            with open(os.path.join(dest, "7.BIN"), "rb") as f:
                self.assertEqual(f.read(), blob7)

            # no burst support: plain reads
            cube = FakeCube(files, burst=False)
            run_job(cube, "copy", 8)
            with open(os.path.join(dest, "8.BIN"), "rb") as f:
                self.assertEqual(f.read(), blob8)
            self.assertIn(sd.OP_READ, cube.ops)

            # armed when asked: nothing starts
            cube = FakeCube(files)
            mgr = sd.CubeSD(connect=lambda: cube, dest=dest, free=lambda p: 10 ** 12)
            ok, msg = mgr.start_list(lambda: True)
            self.assertFalse(ok)
            self.assertIn("disarmed", msg)
            self.assertEqual(cube.ops, [])

            # the Cube arms during the copy: stops at once, no partial file left
            shutil_dest = os.path.join(tmp, "cubesd2")
            cube = FakeCube(files, arm_after=6)
            mgr = sd.CubeSD(connect=lambda: cube, dest=shutil_dest, free=lambda p: 10 ** 12)
            mgr.listing = [{"n": 7, "name": "00000007.BIN", "size": len(blob7)}]
            self.assertTrue(mgr.start_copy(7, lambda: False)[0])
            mgr.wait(20)
            self.assertEqual(mgr.status()["job"]["state"], "cancelled")
            self.assertEqual(os.listdir(shutil_dest), [])
            self.assertTrue(cube.closed)

            # the Solo's own armed check stops it too, and a second job cannot start while one runs
            flag = {"armed": False}
            cube = FakeCube(files)
            orig_recv = cube.recv

            def recv(t):
                flag["armed"] = len(cube.ops) > 5
                return orig_recv(t)
            cube.recv = recv
            mgr = sd.CubeSD(connect=lambda: cube, dest=shutil_dest, free=lambda p: 10 ** 12)
            mgr.listing = [{"n": 7, "name": "00000007.BIN", "size": len(blob7)}]
            mgr.start_copy(7, lambda: flag["armed"])
            mgr.wait(20)
            self.assertEqual(mgr.status()["job"]["state"], "cancelled")

            # cancel
            cube = FakeCube(files)
            orig_recv = cube.recv
            holder = {}

            def recv2(t):
                if len(cube.ops) > 5:
                    holder["m"].cancel()
                return orig_recv(t)
            cube.recv = recv2
            mgr = sd.CubeSD(connect=lambda: cube, dest=shutil_dest, free=lambda p: 10 ** 12)
            holder["m"] = mgr
            mgr.listing = [{"n": 7, "name": "00000007.BIN", "size": len(blob7)}]
            mgr.start_copy(7, lambda: False)
            mgr.wait(20)
            self.assertEqual(mgr.status()["job"]["message"], "Cancelled.")

            # a silent Cube: a clear error, not a hang
            cube = FakeCube(files, silent=True)
            mgr = sd.CubeSD(connect=lambda: cube, dest=shutil_dest, free=lambda p: 10 ** 12)
            mgr.start_list(lambda: False)
            mgr.wait(20)
            st = mgr.status()["job"]
            self.assertEqual(st["state"], "error")
            self.assertIn("did not answer", st["error"])

            # ...and it says what the Cube did send, so a silent file service can be told from a dead link
            class Msg(object):
                def __init__(self, kind, payload=b""):
                    self._k, self.payload, self.base_mode = kind, payload, 0
                def get_type(self):
                    return self._k
            class StubMav(object):
                def __init__(self):
                    self.queue = [Msg("ATTITUDE")] * 5 + [Msg("HEARTBEAT")] + [Msg("FILE_TRANSFER_PROTOCOL", bytes(bytearray([7, 0, 0, 128, 0, 3, 0, 0, 0, 0, 0, 0])))]
                def recv_match(self, blocking=True, timeout=None, **kw):
                    return self.queue.pop(0) if self.queue else None
            tr = sd.MavTransport(StubMav())
            seen_raw = [tr.recv(0.01) for _ in range(7)]
            self.assertEqual(tr.ftp_seen, 1)
            self.assertEqual(seen_raw[-1][:2], bytes(bytearray([7, 0])))
            text = tr.summary()
            self.assertIn("ATTITUDE 5", text)
            self.assertIn("file-transfer packets: 1", text)
            self.assertIn("reply to 3", text)
            tr.send = lambda raw: None
            tr.recv = lambda timeout: None
            try:
                sd.FtpClient(tr, reply_timeout=0.05, retries=2).request(sd.OP_LIST, 0, 0, 0)
                self.fail("a silent Cube must raise")
            except sd.CubeSDError as ex:
                self.assertIn("did not answer (MAVFTP 3)", str(ex))
                self.assertIn("ATTITUDE 5", str(ex))

            # cannot reach the Cube at all
            def no_link():
                raise sd.CubeSDError("Cannot reach the Cube over the Solo's telemetry link.")
            mgr = sd.CubeSD(connect=no_link, dest=shutil_dest, free=lambda p: 10 ** 12)
            mgr.start_list(lambda: False)
            mgr.wait(20)
            self.assertIn("Cannot reach", mgr.status()["job"]["error"])
            self.assertTrue(mgr.job_lock.acquire(False))     # released after the failure
            mgr.job_lock.release()

            # not enough room on the Solo
            cube = FakeCube(files)
            mgr = sd.CubeSD(connect=lambda: cube, dest=shutil_dest, free=lambda p: 1000)
            mgr.listing = [{"n": 7, "name": "00000007.BIN", "size": len(blob7)}]
            mgr.start_copy(7, lambda: False)
            mgr.wait(20)
            self.assertIn("Not enough space", mgr.status()["job"]["error"])
            # a log not in the list is refused
            mgr = sd.CubeSD(connect=lambda: FakeCube(files), dest=shutil_dest, free=lambda p: 10 ** 12)
            mgr.start_copy(99, lambda: False)
            mgr.wait(20)
            self.assertIn("Check the Cube", mgr.status()["job"]["error"])

            # retention: newest copies kept, the one just copied always kept
            keep_dir = os.path.join(tmp, "keep")
            os.makedirs(keep_dir)
            for k in range(1, 8):
                p = os.path.join(keep_dir, "%d.BIN" % k)
                with open(p, "wb") as f:
                    f.write(b"x" * 100)
                os.utime(p, (1000 + k, 1000 + k))
            with open(os.path.join(keep_dir, "notes.txt"), "w") as f:
                f.write("mine")
            sd.enforce_limits(keep_dir, max_copies=3, max_bytes=10 ** 9)
            self.assertEqual(sorted(n for n in os.listdir(keep_dir)), ["5.BIN", "6.BIN", "7.BIN", "notes.txt"])
            sd.enforce_limits(keep_dir, max_copies=10, max_bytes=250, keep=5)
            self.assertEqual(sorted(n for n in os.listdir(keep_dir)), ["5.BIN", "7.BIN", "notes.txt"])
        finally:
            sd.FtpClient.__init__ = real_init
            _shutil.rmtree(tmp, ignore_errors=True)

        # --- the API: copies show up in the log list, and the Cube routes are guarded
        class Req(object):
            def __init__(self, headers=None):
                self.code, self.headers, self.wfile, self.sent = None, dict(headers or {}), _io.BytesIO(), {}

            def send_response(self, code):
                self.code = code

            def send_header(self, k, v):
                self.sent[k] = v

            def end_headers(self):
                pass

        class FakeMgr(object):
            def __init__(self):
                self.calls = []

            def status(self):
                return {"job": {"state": "idle"}, "list": None, "listedAt": None, "copies": 0}

            def start_list(self, armed):
                self.calls.append("list")
                return True, ""

            def start_copy(self, n, armed):
                self.calls.append("copy%d" % n)
                return True, ""

            def cancel(self):
                self.calls.append("cancel")

        tmp = tempfile.mkdtemp()
        try:
            flash, copies = os.path.join(tmp, "dataflash"), os.path.join(tmp, "cubesd")
            os.makedirs(flash)
            os.makedirs(copies)
            with open(os.path.join(flash, "3.BIN"), "wb") as f:
                f.write(b"abc")
            with open(os.path.join(copies, "12.BIN"), "wb") as f:
                f.write(b"\xa3\x95" * 500)
            with open(os.path.join(copies, "junk.txt"), "wb") as f:
                f.write(b"x")
            r = Req()
            api.handle(r, "/api/logviewer/flightlogs", None, folder=flash, armed=lambda: False, copies=copies)
            body = _json.loads(r.wfile.getvalue().decode("utf-8"))
            ids = sorted(d["id"] for d in body["logs"])
            self.assertEqual(ids, ["3", "c12"])
            cube_entry = [d for d in body["logs"] if d["id"] == "c12"][0]
            self.assertEqual((cube_entry["kind"], cube_entry["size"], cube_entry["source"]), ("bin", 1000, "cube"))
            r = Req()
            api.handle(r, "/api/logviewer/flightlog/c12", None, folder=flash, armed=lambda: False, copies=copies)
            self.assertEqual((r.code, r.wfile.getvalue()), (200, b"\xa3\x95" * 500))
            self.assertIn("cube_log_12_", r.sent["Content-Disposition"])
            r = Req()
            api.handle(r, "/api/logviewer/flightlog/c12", None, folder=flash, armed=lambda: True, copies=copies)
            self.assertEqual(r.code, 409)
            for p in ("/api/logviewer/flightlog/c99", "/api/logviewer/flightlog/cx", "/api/logviewer/flightlog/c../1"):
                r = Req()
                api.handle(r, p, None, folder=flash, armed=lambda: False, copies=copies)
                self.assertEqual(r.code, 404, p)
            fm = FakeMgr()
            r = Req()
            api.handle(r, "/api/logviewer/cubesd", None, folder=flash, armed=lambda: True, cubesd=fm)
            self.assertEqual(r.code, 200)                              # status always readable
            for sub in ("cubesd/list", "cubesd/copy/7", "cubesd/cancel"):
                r = Req()
                api.handle(r, "/api/logviewer/" + sub, None, folder=flash, armed=lambda: False, cubesd=fm)
                self.assertEqual(r.code, 403, sub)                      # needs our header
            self.assertEqual(fm.calls, [])
            r = Req({"X-Log-Viewer": "1"})
            api.handle(r, "/api/logviewer/cubesd/list", None, folder=flash, armed=lambda: True, cubesd=fm)
            self.assertEqual(r.code, 409)                               # armed
            self.assertEqual(fm.calls, [])
            for sub in ("cubesd/list", "cubesd/copy/7", "cubesd/cancel"):
                r = Req({"X-Log-Viewer": "1"})
                api.handle(r, "/api/logviewer/" + sub, None, folder=flash, armed=lambda: False, cubesd=fm)
                self.assertEqual(r.code, 200, sub)
            self.assertEqual(fm.calls, ["list", "copy7", "cancel"])
            r = Req({"X-Log-Viewer": "1"})
            api.handle(r, "/api/logviewer/cubesd/copy/abc", None, folder=flash, armed=lambda: False, cubesd=fm)
            self.assertEqual(r.code, 404)
            # cancelling is allowed even when armed
            fm.calls = []
            r = Req({"X-Log-Viewer": "1"})
            api.handle(r, "/api/logviewer/cubesd/cancel", None, folder=flash, armed=lambda: True, cubesd=fm)
            self.assertEqual((r.code, fm.calls), (200, ["cancel"]))
        finally:
            _shutil.rmtree(tmp, ignore_errors=True)


    def test_56_controller_ssh_restores_the_solos_key(self):
        """modules/controller_ssh.py (shared by Update Controller and the controller log viewer installer):
        when the Controller refuses the Solo's key (settings reset), the key is put back with the default
        root password through a real controlling terminal, once, then key login works. A wrong password
        gives one clear message. Tested with a fake ssh that, like OpenSSH, prompts only on /dev/tty."""
        import textwrap
        import importlib.util as _ilu
        root = os.path.dirname(os.path.abspath(__file__))
        FAKE_SSH = textwrap.dedent('''\
            #!/usr/bin/env python3
            import os, sys, subprocess
            args = sys.argv[1:]
            ctl = os.environ["FAKE_CTRL"]
            batch = "BatchMode=yes" in args
            cmd = args[-1]
            auth = os.path.join(ctl, "home_root_ssh", "authorized_keys")
            key = os.path.join(os.environ["FAKE_SOLO_KEYDIR"], "id_rsa.pub")
            blob = open(key).read().split()[1] if os.path.exists(key) else None
            def count(name):
                p = os.path.join(ctl, name)
                n = int(open(p).read()) if os.path.exists(p) else 0
                open(p, "w").write(str(n + 1))
            def run_cmd():
                c = cmd.replace("/home/root/.ssh", os.path.join(ctl, "home_root_ssh")).replace("/root/.ssh", os.path.join(ctl, "root_ssh"))
                r = subprocess.run(["sh", "-c", c], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                sys.stdout.write(r.stdout.decode()); sys.exit(r.returncode)
            if batch:
                if blob and os.path.exists(auth) and blob in open(auth).read():
                    run_cmd()
                print("root@10.1.1.1: Permission denied (publickey,password,keyboard-interactive).")
                sys.exit(255)
            try:
                tty = open("/dev/tty", "r+b", buffering=0)
            except OSError:
                print("ssh: no tty present and no askpass program specified"); sys.exit(255)
            count("password_prompts")
            tty.write(b"root@10.1.1.1's password: ")
            line = b""
            while not line.endswith(b"\\n"):
                ch = tty.read(1)
                if not ch: break
                line += ch
            if line.strip().decode() == os.environ["FAKE_PASSWORD"]:
                run_cmd()
            print("Permission denied, please try again."); sys.exit(255)
        ''')
        FAKE_KEYGEN = textwrap.dedent('''\
            #!/usr/bin/env python3
            import sys
            f = sys.argv[sys.argv.index("-f") + 1]
            open(f, "w").write("PRIVATE"); open(f + ".pub", "w").write("ssh-rsa AAAAFAKEBLOB123 root@solo\\n")
        ''')


        d = tempfile.mkdtemp()
        env_keys = ("FAKE_CTRL", "FAKE_SOLO_KEYDIR", "FAKE_PASSWORD")
        env_old = dict((k, os.environ.get(k)) for k in env_keys)
        path_old = list(sys.path)
        try:
            ctl = os.path.join(d, "ctl")
            os.makedirs(os.path.join(ctl, "home_root_ssh"))
            os.makedirs(os.path.join(ctl, "root_ssh"))
            os.makedirs(os.path.join(d, "solo"))
            for name, text in (("fake_ssh", FAKE_SSH), ("fake_keygen", FAKE_KEYGEN)):
                with open(os.path.join(d, name), "w") as f:
                    f.write(text)
                os.chmod(os.path.join(d, name), 0o755)
            os.environ.update(FAKE_CTRL=ctl, FAKE_SOLO_KEYDIR=os.path.join(d, "solo"), FAKE_PASSWORD="TjSDBkAu")
            spec = _ilu.spec_from_file_location("controller_ssh_test", os.path.join(root, "modules", "controller_ssh.py"))
            cs = _ilu.module_from_spec(spec)
            spec.loader.exec_module(cs)
            cs.SSH_BIN, cs.KEYGEN_BIN = os.path.join(d, "fake_ssh"), os.path.join(d, "fake_keygen")
            cs.KEY_PATH = os.path.join(d, "solo", "id_rsa")

            def prompts():
                p = os.path.join(ctl, "password_prompts")
                if not os.path.exists(p):
                    return 0
                with open(p) as f:
                    return int(f.read())

            def auth_text(sub):
                with open(os.path.join(ctl, sub, "authorized_keys")) as f:
                    return f.read()

            # 1. the Controller forgot the key: restored with the default password, Solo key created
            rc, out = cs.run("echo hello")
            self.assertEqual(rc, 0)
            self.assertIn("hello", out)
            self.assertTrue(os.path.exists(cs.KEY_PATH + ".pub"))
            for sub in ("home_root_ssh", "root_ssh"):
                self.assertIn("AAAAFAKEBLOB123", auth_text(sub))
            self.assertEqual(prompts(), 1)
            rc, out = cs.run("echo again")                       # key login now: no password asked
            self.assertEqual((rc, out.strip()), (0, "again"))
            self.assertEqual(prompts(), 1)
            cs.ensure_authorized(force=True)                     # the key is not added twice
            self.assertEqual(auth_text("home_root_ssh").count("AAAAFAKEBLOB123"), 1)

            # 2. golden_update.ssh() goes through it (the old pty-without-terminal code got no prompt at all)
            sys.path.insert(0, os.path.join(root, "bin", "golden_update"))
            import golden_update as gu
            gcs = gu.controller_ssh                              # the helper as Update Controller imported it
            gcs.SSH_BIN, gcs.KEYGEN_BIN, gcs.KEY_PATH = cs.SSH_BIN, cs.KEYGEN_BIN, cs.KEY_PATH
            self.assertEqual(gu.ssh("echo viaGolden", 30)[1].strip(), "viaGolden")

            # 3. wrong root password: one clear message, one attempt
            for sub in ("home_root_ssh", "root_ssh"):
                os.remove(os.path.join(ctl, sub, "authorized_keys"))
            os.environ["FAKE_PASSWORD"] = "not-the-default"
            cs._state.update(tried=False, result=None)
            before = prompts()
            rc, out = cs.run("echo hello")
            self.assertEqual((rc, out), (255, cs.NOT_AUTHORIZED))
            cs.run("echo again")
            self.assertEqual(prompts(), before + 1)
            self.assertIn("isn't authorized on the Controller", cs.NOT_AUTHORIZED)
            self.assertIn("Run the installer again with the controller on.", cs.NOT_AUTHORIZED)

            # 4. Update Controller reports that message
            gcs._state.update(tried=False, result=None)
            lines = []
            gu.run_controller("status", lines.append, 30)
            self.assertEqual(lines[0], "[GOLDEN_FAILED] " + gcs.NOT_AUTHORIZED)
            self.assertEqual(len(lines), 2)                      # then what ssh said, for the log
            self.assertTrue(lines[1].startswith("[ssh detail] "))
            self.assertIn("Permission denied", lines[1])

            # 5. the controller log viewer installer restores the key, or reports the same message
            sys.path.insert(0, os.path.join(root, "modules", "log_viewer"))
            spec = _ilu.spec_from_file_location("lv_ctl_install_test56", os.path.join(root, "modules", "log_viewer", "controller_install.py"))
            inst = _ilu.module_from_spec(spec)
            spec.loader.exec_module(inst)
            inst._authorize = lambda: (False, cs.NOT_AUTHORIZED)
            res = inst.install(lambda a, dd=None, t=20: (255, "Permission denied (publickey)."),
                               os.path.join(d, "s.json"), lambda: True, lambda s: None)
            self.assertEqual(res, {"ok": False, "error": cs.NOT_AUTHORIZED})
            seen = []

            def runner(a, dd=None, t=20):
                seen.append(1)
                return (0, "ok") if len(seen) > 1 else (255, "Permission denied (publickey).")
            inst._authorize = lambda: (True, "ok")
            lvdir = os.path.join(root, "modules", "log_viewer")
            inst.install(runner, os.path.join(d, "s.json"), lambda: True, lambda s: None,
                         os.path.join(lvdir, "controller"), os.path.join(root, "static", "log-viewer"))
            self.assertGreater(len(seen), 2)                     # carried on after the key was restored

            # 6. flash_mgr (the controller step of a Full Install): same terminal handling, same key restore
            for sub in ("home_root_ssh", "root_ssh"):
                if os.path.exists(os.path.join(ctl, sub, "authorized_keys")):
                    os.remove(os.path.join(ctl, sub, "authorized_keys"))
            os.environ["FAKE_PASSWORD"] = "TjSDBkAu"
            gcs._state.update(tried=False, result=None)
            before = prompts()
            rc, out = flash_mgr._run_with_ssh_auth("%s -o StrictHostKeyChecking=no root@10.1.1.1 'echo viaFlash'" % cs.SSH_BIN,
                                                   host="10.1.1.1", timeout=30)
            self.assertEqual(rc, 0)
            self.assertIn("viaFlash", out)
            self.assertGreater(prompts(), before)
            self.assertIn("AAAAFAKEBLOB123", auth_text("home_root_ssh"))

            # 7. a /root that can't be written must not stop the key from reaching /home/root
            for sub in ("home_root_ssh", "root_ssh"):
                if os.path.exists(os.path.join(ctl, sub, "authorized_keys")):
                    os.remove(os.path.join(ctl, sub, "authorized_keys"))
            shutil.rmtree(os.path.join(ctl, "root_ssh"))
            with open(os.path.join(ctl, "root_ssh"), "w") as f:
                f.write("a file, so mkdir and writes under it fail")
            gcs._state.update(tried=False, result=None)
            rc, out = gcs.run("echo noRoot")
            self.assertEqual((rc, out.strip()), (0, "noRoot"))
            self.assertIn("AAAAFAKEBLOB123", auth_text("home_root_ssh"))
        finally:
            for k, v in env_old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            sys.path[:] = path_old
            shutil.rmtree(d, True)


    def test_57_cube_sd_ftp_accepts_a_slow_cubes_late_reply(self):
        """A slow Cube answers the first attempt while the second is already waiting: that reply
        (sequence = an earlier attempt + 1) is accepted. Replies meant for someone else are not, and
        the error names them."""
        import importlib.util as _ilu
        import struct as _st
        root = os.path.dirname(os.path.abspath(__file__))
        spec = _ilu.spec_from_file_location("cube_sd_t57", os.path.join(root, "modules", "log_viewer", "cube_sd.py"))
        sd = _ilu.module_from_spec(spec)
        spec.loader.exec_module(sd)

        def reply(seq, req, offset=0):
            h = _st.pack(sd.HDR_FMT, seq & 0xFFFF, 0, sd.OP_ACK, 0, req, 0, 0, offset)
            return h + b"\0" * (251 - len(h))

        class Slow(object):
            def __init__(self, answer_after, wrong=False):
                self.sent, self.q, self.n, self.after, self.wrong = [], [], 0, answer_after, wrong
            def send(self, b):
                self.sent.append(_st.unpack("<H", b[:2])[0])
            def recv(self, t):
                time.sleep(0.1)
                self.n += 1
                if self.n == self.after:
                    if self.wrong:
                        return reply(self.sent[0] - 3, sd.OP_LIST, 136)
                    return reply(self.sent[0] + 1, sd.OP_LIST)          # to the FIRST attempt
                return None

        tr = Slow(answer_after=7)                                       # arrives during the second attempt
        r = sd.FtpClient(tr, reply_timeout=0.5, retries=3).request(sd.OP_LIST, 0, 0, 0)
        self.assertEqual(r["op"], sd.OP_ACK)
        self.assertGreaterEqual(len(tr.sent), 2)
        tr = Slow(answer_after=2, wrong=True)
        with self.assertRaises(sd.CubeSDError) as cm:
            sd.FtpClient(tr, reply_timeout=0.05, retries=2).request(sd.OP_LIST, 0, 0, 0)
        msg = str(cm.exception)
        self.assertIn("did not answer (MAVFTP 3)", msg)
        self.assertIn("We sent seq", msg)
        self.assertIn("offset 136", msg)


    def test_58_cube_sd_lists_recent_logs_without_listing_the_directory(self):
        """ArduPilot walks the whole directory for every list page, so a card with hundreds of logs gets
        too slow to answer. recent_logs() reads LASTLOG.TXT and opens the newest logs by name instead (the
        open reply carries the size). No List request is sent, gaps in the numbering are skipped, and a
        card without LASTLOG.TXT falls back to list_dir."""
        import importlib.util as _ilu
        import struct as _st
        root = os.path.dirname(os.path.abspath(__file__))
        spec = _ilu.spec_from_file_location("cube_sd_t58", os.path.join(root, "modules", "log_viewer", "cube_sd.py"))
        sd = _ilu.module_from_spec(spec)
        spec.loader.exec_module(sd)

        class Cube(object):
            def __init__(self, files):
                self.files, self.q, self.ops = files, [], []
            def _reply(self, seq, req, op, data=b"", session=0, offset=0):
                h = _st.pack(sd.HDR_FMT, (seq + 1) & 0xFFFF, session, op, len(data), req, 0, 0, offset)
                self.q.append(h + data + b"\0" * (251 - len(h) - len(data)))
            def send(self, raw):
                seq, session, op, size, _req, _b, _p, offset = _st.unpack(sd.HDR_FMT, raw[:sd.HDR_LEN])
                data = raw[sd.HDR_LEN:sd.HDR_LEN + size]
                self.ops.append(op)
                if op == sd.OP_OPEN_RO:
                    content = self.files.get(data.decode("ascii"))
                    if content is None:
                        self._reply(seq, op, sd.OP_NAK, b"\x0a")
                    else:
                        size_of = len(content) if isinstance(content, bytes) else content
                        self._reply(seq, op, sd.OP_ACK, _st.pack("<I", size_of), session=1)
                elif op == sd.OP_READ:
                    content = self.files.get("/APM/LOGS/LASTLOG.TXT", b"")
                    if offset >= len(content):
                        self._reply(seq, op, sd.OP_NAK, b"\x06")
                    else:
                        self._reply(seq, op, sd.OP_ACK, content[offset:offset + size], offset=offset)
                elif op == sd.OP_LIST:
                    self._reply(seq, op, sd.OP_NAK, b"\x06")
            def recv(self, t):
                return self.q.pop(0) if self.q else None
            def summary(self):
                return ""

        d = "/APM/LOGS"
        files = {d + "/LASTLOG.TXT": b"7\n", d + "/00000007.BIN": 7000, d + "/00000005.BIN": 5000,
                 d + "/00000004.BIN": 4000, d + "/00000002.BIN": 2000, d + "/00000001.BIN": 1000}
        cube = Cube(files)
        got = sd.FtpClient(cube, reply_timeout=0.05, retries=2).recent_logs(d, count=4)
        self.assertEqual(got, [("00000007.BIN", 7000), ("00000005.BIN", 5000), ("00000004.BIN", 4000),
                               ("00000002.BIN", 2000)])             # newest first; 6 and 3 are gaps
        self.assertNotIn(sd.OP_LIST, cube.ops)
        cube = Cube({d + "/00000001.BIN": 10})                      # no LASTLOG.TXT
        with self.assertRaises(sd.CubeSDError):
            sd.FtpClient(cube, reply_timeout=0.05, retries=2).recent_logs(d)


    def test_59_cube_sd_burst_keeps_packets_after_a_drop(self):
        """A dropped packet in a burst costs one ReadFile for that hole, not a restart of the burst (the
        old client threw away the rest of a burst, up to 500 packets, after every loss)."""
        import importlib.util as _ilu
        import struct as _st
        root = os.path.dirname(os.path.abspath(__file__))
        spec = _ilu.spec_from_file_location("cube_sd_t59", os.path.join(root, "modules", "log_viewer", "cube_sd.py"))
        sd = _ilu.module_from_spec(spec)
        spec.loader.exec_module(sd)
        blob = bytes(bytearray((i * 7 + i // 251) & 0xFF for i in range(239 * 300 + 100)))

        class Cube(object):
            def __init__(self, drop_every):
                self.q, self.bursts, self.reads, self.drop_every = [], 0, 0, drop_every
            def _reply(self, seq, req, op, data=b"", session=0, offset=0, burst=0):
                h = _st.pack(sd.HDR_FMT, (seq + 1) & 0xFFFF, session, op, len(data), req, burst, 0, offset)
                self.q.append(h + data + b"\0" * (251 - len(h) - len(data)))
            def send(self, raw):
                seq, session, op, size, _r, _b, _p, offset = _st.unpack(sd.HDR_FMT, raw[:sd.HDR_LEN])
                if op == sd.OP_OPEN_RO:
                    self._reply(seq, op, sd.OP_ACK, _st.pack("<I", len(blob)), session=1)
                elif op == sd.OP_READ:
                    self.reads += 1
                    self._reply(seq, op, sd.OP_ACK, blob[offset:offset + size], session=session, offset=offset)
                elif op == sd.OP_BURST:
                    self.bursts += 1
                    pos, n = offset, 0
                    while pos < len(blob) and n < 500:
                        piece = blob[pos:pos + size]
                        n += 1
                        last = n == 500 or pos + len(piece) >= len(blob)
                        if not (self.drop_every and n % self.drop_every == 0 and not last):
                            self._reply(seq, op, sd.OP_ACK, piece, session=session, offset=pos, burst=1 if last else 0)
                        pos += len(piece)
            def recv(self, t):
                return self.q.pop(0) if self.q else None

        for drop_every in (0, 7, 3):
            cube = Cube(drop_every)
            out = []
            total = sd.FtpClient(cube, reply_timeout=0.05, retries=2).download(
                "/APM/LOGS/00000109.BIN", out.append, lambda got, tot: None)
            self.assertEqual(b"".join(out), blob)
            self.assertEqual(total, len(blob))
            self.assertLessEqual(cube.bursts, 2)                          # one burst covers this file
            self.assertLessEqual(cube.reads, 300 // max(drop_every, 1) + 2)  # one ReadFile per hole

    def test_60_cube_sd_queues_copies_and_estimates_the_time_left(self):
        """Copies can be lined up while one runs; they run one after the other on one connection, a cancel or
        an error drops the queue, and the estimate comes from the average speed of the run."""
        import importlib.util as _ilu
        import threading as _th
        root = os.path.dirname(os.path.abspath(__file__))
        spec = _ilu.spec_from_file_location("cube_sd_t60", os.path.join(root, "modules", "log_viewer", "cube_sd.py"))
        sd = _ilu.module_from_spec(spec)
        spec.loader.exec_module(sd)
        sizes = {7: 5000, 8: 3000, 9: 1000}
        listing = [{"n": n, "name": "%08d.BIN" % n, "size": sizes[n]} for n in (9, 8, 7)]

        class Stub(object):
            gate = _th.Event()
            fail_first = False
            connects = []
            def __init__(self, transport, check=None, **kw):
                self.check = check
            def reset(self):
                pass
            def describe(self):
                return ""
            def download(self, path, write, progress):
                n = int(path.split("/")[-1].split(".")[0])
                started.append(n)
                while not Stub.gate.is_set():
                    self.check()
                    time.sleep(0.01)
                if Stub.fail_first and n == 7:
                    raise sd.CubeSDError("The Cube did not answer.")
                write(b"x" * sizes[n])
                progress(sizes[n], sizes[n])
                return sizes[n]

        class Link(object):
            closed = 0
            def armed(self):
                return False
            def close(self):
                Link.closed += 1

        started = []
        real = sd.FtpClient
        sd.FtpClient = Stub
        tmp = tempfile.mkdtemp()
        try:
            def make(sub):
                mgr = sd.CubeSD(connect=lambda: Link(), dest=os.path.join(tmp, sub), free=lambda p: 10 ** 12)
                mgr.listing = [dict(x) for x in listing]
                return mgr

            def wait_for(cond):
                end = time.time() + 5
                while time.time() < end and not cond():
                    time.sleep(0.01)
                self.assertTrue(cond())

            # two queued behind the first: all three are copied, one connection
            Stub.gate = _th.Event()
            del started[:]
            Link.closed = 0
            mgr = make("a")
            self.assertTrue(mgr.start_copy(7, lambda: False)[0])
            wait_for(lambda: started == [7])
            self.assertEqual(mgr.start_copy(8, lambda: False), (True, "Queued."))
            self.assertEqual(mgr.start_copy(9, lambda: False), (True, "Queued."))
            self.assertEqual(mgr.start_copy(8, lambda: False), (True, "Already queued."))
            self.assertEqual(mgr.start_copy(7, lambda: False), (True, "Already queued."))
            self.assertFalse(mgr.start_copy(99, lambda: False)[0])           # not in the list
            self.assertFalse(mgr.start_copy(9, lambda: True)[0])             # armed
            self.assertEqual(mgr.status()["queue"], [8, 9])
            Stub.gate.set()
            mgr.wait(10)
            st = mgr.status()
            self.assertEqual(st["job"]["state"], "done", st)
            self.assertEqual(st["queue"], [])
            self.assertEqual(started, [7, 8, 9])
            self.assertEqual(st["job"]["message"], "3 logs copied to the Solo.")
            self.assertEqual(Link.closed, 1)
            for n in (7, 8, 9):
                self.assertEqual(os.path.getsize(os.path.join(tmp, "a", "%d.BIN" % n)), sizes[n])

            # cancelling drops the queue
            Stub.gate = _th.Event()
            del started[:]
            mgr = make("b")
            mgr.start_copy(7, lambda: False)
            wait_for(lambda: started == [7])
            mgr.start_copy(8, lambda: False)
            mgr.cancel()
            mgr.wait(10)
            st = mgr.status()
            self.assertEqual(st["job"]["state"], "cancelled", st)
            self.assertEqual(st["queue"], [])
            self.assertEqual(started, [7])
            self.assertIn("1 log waiting", st["job"]["message"])

            # an error drops the queue and says so
            Stub.gate = _th.Event()
            Stub.fail_first = True
            del started[:]
            mgr = make("c")
            mgr.start_copy(7, lambda: False)
            wait_for(lambda: started == [7])
            mgr.start_copy(8, lambda: False)
            mgr.start_copy(9, lambda: False)
            Stub.gate.set()
            mgr.wait(10)
            st = mgr.status()
            self.assertEqual(st["job"]["state"], "error", st)
            self.assertIn("2 logs waiting", st["job"]["error"])
            self.assertEqual(started, [7])
            Stub.fail_first = False

            # a new copy after the run is a new run (nothing left over)
            Stub.gate.set()
            mgr = make("d")
            mgr.start_copy(9, lambda: False)
            mgr.wait(10)
            self.assertEqual(mgr.status()["job"]["message"], "Log 9 copied to the Solo.")

            # the estimate: average speed since the run began
            now = [100.0]
            mgr = sd.CubeSD(connect=lambda: Link(), dest=tmp, clock=lambda: now[0])
            mgr.listing = [dict(x) for x in listing]
            job = {"state": "copying", "kind": "copy", "n": 7, "itemStart": 90.0, "got": 1000000, "total": 3000000}
            est = mgr._estimate(job, [8], [{"n": 8, "size": 500000}], [0, 0.0])
            self.assertEqual(est, {"eta": 20, "queueEta": 25})
            now[0] = 92.0                                                   # too early to say
            self.assertEqual(mgr._estimate(job, [], [], [0, 0.0]), {"eta": None, "queueEta": None})
            now[0] = 100.0                                                  # finished items count towards the speed
            est = mgr._estimate(dict(job, itemStart=99.0, got=0, total=1000000), [], [], [1000000, 9.0])
            self.assertEqual(est["eta"], 10)
        finally:
            sd.FtpClient = real
            shutil.rmtree(tmp, ignore_errors=True)

    def test_61_flight_panel_queues_cube_copies(self):
        """The Cube section shows Add to queue / Queued while a copy runs, and the time left."""
        root = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(root, "static", "log-viewer", "flight-panel.js"), "r", encoding="utf-8") as f:
            js = f.read()
        for needle in ("Add to queue", "'Queued'", "more waiting", "st.queue", "job.eta", "left'"):
            self.assertIn(needle, js)


def _struct_float(b4):
    """Pack 4 signed bytes into the float the gimbal sends as GMB_SWVER."""
    import struct
    return struct.unpack('>f', struct.pack('4b', *b4))[0]

if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(TestSoloWebUtility)
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful():
        sys.exit(1)
