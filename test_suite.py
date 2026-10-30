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
        self.assertLess(fm_src.find("self._full_install_preflight()"), fm_src.find("--load /firmware/arducopter.apj"))

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
                  "LOIT_ANG_MAX", "ATC_INPUT_TC"]
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
        self.assertIsNone(re.search(r'\bf"|\bf\'', src))

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
        retry, skips a current gimbal unless forced, installs the fixed 3DR tools with LF endings and a
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
        cm = open(os.path.join(gdir, "cube_mount.py")).read()
        for needle in ("serial0 = int(sys.argv[3]) if len(sys.argv) > 3 else None", "SERIAL0_PROTOCOL now",
                       "[CUBE_FAILED] SERIAL0_PROTOCOL not accepted", "[CUBE_DONE]"):
            self.assertIn(needle, cm)
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
                def __init__(self, probes, flashes=(True,), armed=False, mnt=2, fail_mount=None, serial0=1,
                             fail_serial0=None):
                    self.probes, self.flashes = list(probes), list(flashes)
                    self.armed, self.mnt, self.fail_mount, self.calls = armed, mnt, fail_mount, []
                    self.serial0, self.fail_serial0 = serial0, fail_serial0

                def cube_info(self):
                    # the firmware must already be in place and checked before the Cube is touched
                    fw = os.path.join(tmp, "fw", "gimbal_firmware_1.3.6.ax")
                    self.fw_ready = os.path.isfile(fw) and gu._file_md5(fw) == gu.FW_MD5
                    return {"armed": self.armed, "mnt1_type": self.mnt, "serial0": self.serial0}

                def set_mount(self, t, settle, serial0=None):
                    self.calls.append(("mount", t) if serial0 is None else ("mount", t, serial0))
                    if t == self.fail_mount:
                        return gu.MOUNT_FAILED
                    self.mnt = t
                    if serial0 is not None:
                        if serial0 == self.fail_serial0:
                            return gu.MOUNT_SERIAL0_FAILED
                        self.serial0 = serial0
                    return gu.MOUNT_DONE

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

            def run(fake, force=False):
                for n in ("st.json", "state.json", "AXON"):
                    if os.path.exists(os.path.join(tmp, n)):
                        os.remove(os.path.join(tmp, n))
                st = gu.Status(force=force)
                return gu.run_update(fake, st, force), st, fake

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
            self.assertEqual(f.calls, [("probe",), ("mount", 2), ("mount", 0), ("probe",), ("flash",), ("mount", 2, 2)])
            self.assertEqual(f.serial0, 2)                 # MAVLink 2 on the Cube's USB port afterwards, always
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
            # already current: no flash unless forced; MNT1_TYPE still restored
            r, st, f = run(Fake([none, cur]))
            self.assertEqual(r, "current")
            self.assertNotIn(("flash",), f.calls)
            self.assertEqual(f.calls[-1], ("mount", 2, 2))
            r, st, f = run(Fake([none, cur]), force=True)
            self.assertEqual(r, "updated")
            self.assertIn(("flash",), f.calls)
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
            self.assertEqual((f.calls[-1], f.mnt, f.serial0), (("mount", 2, 2), 2, 2))
            # SERIAL0_PROTOCOL=2 before the run: set to 1 (MNT1_TYPE kept) before the gimbal check, 2 at the end
            r, st, f = run(Fake([none, old], serial0=2))
            self.assertEqual(r, "updated")
            self.assertEqual(f.calls[:2], [("mount", 2, 1), ("probe",)])
            self.assertEqual((f.calls[-1], f.serial0), (("mount", 2, 2), 2))
            self.assertIn("SERIAL0_PROTOCOL = 2", "\n".join(st.data["lines"]))
            # ... also on the bootloader path (step 2 -> step 5)
            r, st, f = run(Fake([boot], serial0=2))
            self.assertEqual(f.calls, [("mount", 2, 1), ("probe",), ("flash",), ("mount", 2, 2)])
            # the Cube refuses SERIAL0_PROTOCOL=1: stops, MNT1_TYPE and SERIAL0_PROTOCOL=2 still restored
            r, st, f = run(Fake([none, old], serial0=2, fail_serial0=1))
            self.assertEqual(r, "failed")
            self.assertNotIn(("probe",), f.calls)
            self.assertEqual(f.calls[-1], ("mount", 2, 2))
            self.assertIn("SERIAL0_PROTOCOL=1", st.data["message"])
            # restore with SERIAL0_PROTOCOL refused: MNT1_TYPE back, reported, note cleared (no reboot loop)
            r, st, f = run(Fake([none, old], fail_serial0=2))
            self.assertEqual(r, "failed")
            self.assertEqual(f.mnt, 2)
            self.assertIn("SERIAL0_PROTOCOL could NOT be set to 2", st.data["message"])
            self.assertFalse(os.path.exists(os.path.join(tmp, "state.json")))
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
            fk = Fake([], mnt=0, serial0=1)
            self.assertEqual(gu.run_restore(fk, gu.Status(mode="restore")), "restored")
            self.assertEqual((fk.mnt, fk.serial0, os.path.exists(os.path.join(tmp, "state.json"))), (2, 2, False))
            self.assertEqual(fk.calls, [("mount", 2, 2)])

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
            for el_id in ("gimbalUpdateCard", "btnStartGimbalUpdate", "chkGimbalForce", "gimbalTerminal",
                          "btnInstallGimbalBootUpdater", "btnRestoreGimbalBootUpdater", "chkGimbalBootUpdaterEnable"):
                self.assertIn('id="%s"' % el_id, html)
            self.assertIn('id="btnInstallGimbalBootUpdater" style="display: none;"', html)   # hidden by default
        self.assertIn("'enable_install'", swa)
        js = open(os.path.join(root, "static", "app.js"), encoding="utf-8").read()
        self.assertIn("/api/gimbal/start", js)
        self.assertIn("refreshGimbalStatus();", js)
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
