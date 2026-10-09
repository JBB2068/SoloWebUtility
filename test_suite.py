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
        cls._auth_backup = None
        if os.path.exists(version_mgr.VERSION_FILE):
            with open(version_mgr.VERSION_FILE, "r") as f:
                cls._version_backup = f.read()
        if os.path.exists(auth.AUTH_FILE):
            with open(auth.AUTH_FILE, "r") as f:
                cls._auth_backup = f.read()

    @classmethod
    def tearDownClass(cls):
        if cls._version_backup is not None:
            with open(version_mgr.VERSION_FILE, "w") as f:
                f.write(cls._version_backup)
        if cls._auth_backup is not None:
            with open(auth.AUTH_FILE, "w") as f:
                f.write(cls._auth_backup)

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
        solo_apj = os.path.join(firmware_dir, "arducopter_solo.apj")
        green_apj = os.path.join(firmware_dir, "arducopter_green.apj")
        tar_gz = os.path.join(firmware_dir, "3dr-solo.tar.gz")
        tar_md5 = os.path.join(firmware_dir, "3dr-solo.tar.gz.md5")
        ctrl_tar_gz = os.path.join(firmware_dir, "3dr-controller.tar.gz")
        ctrl_tar_md5 = os.path.join(firmware_dir, "3dr-controller.tar.gz.md5")

        self.assertTrue(os.path.exists(solo_apj), "arducopter_solo.apj missing")
        self.assertTrue(os.path.exists(green_apj), "arducopter_green.apj missing")
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

        # Verify tar.gz sizes
        tar_sz = os.path.getsize(tar_gz)
        self.assertEqual(tar_sz, 75983897)
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
        self.assertIn("speed_sport", preset_ids)
        self.assertIn("speed_cinematic", preset_ids)
        self.assertIn("bait_drop", preset_ids)
        self.assertIn("lipo_default", preset_ids)
        self.assertIn("liion_default", preset_ids)

        # Parameter Manager search
        results = param_mgr.mgr.search_params("GPS")
        self.assertTrue(len(results) > 0)
        gps_type_found = any(p["name"] == "GPS_TYPE" for p in results)
        self.assertTrue(gps_type_found)

        # Parameter update
        ok, msg = param_mgr.mgr.set_param("WPNAV_SPEED", 1500)
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
        sample_iwlist = (
            "Cell 01 - Address: 00:11:22:33:44:55\n"
            "          Channel:6\n"
            "          Quality=60/70  Signal level=-50 dBm\n"
            "          Encryption key:on\n"
            "          ESSID:\"Home_2.4G\"\n"
            "          IE: IEEE 802.11i/WPA2 Version 1\n"
            "Cell 02 - Address: AA:BB:CC:DD:EE:FF\n"
            "          Channel:1\n"
            "          Quality=40/70  Signal level=-75 dBm\n"
            "          Encryption key:off\n"
            "          ESSID:\"Guest_Open\"\n"
        )
        wifi_mgr._ssh = lambda cmd, host="10.1.1.1": (0, sample_iwlist if "scan" in cmd else 'ssid="SoloLink_Test"')
        wifi_mgr._ssh_paramiko = lambda cmd, host="10.1.1.1", timeout=30: (0, "OK")
        orig_ctrl_reach = wifi_mgr._ctrl_reachable
        wifi_mgr._ctrl_reachable = lambda: True
        try:
            wifi_status = wifi_mgr.get_wifi_status()
            self.assertIn("mode", wifi_status)
            self.assertIn("connected_ssid", wifi_status)

            # Test scanning
            scan_res = wifi_mgr.scan_wifi_networks()
            self.assertTrue(scan_res.get("success"))
            self.assertEqual(scan_res.get("count"), 2)
            net_names = [n["ssid"] for n in scan_res.get("networks", [])]
            self.assertIn("Home_2.4G", net_names)
            self.assertIn("Guest_Open", net_names)

            # Test connect_home_wifi deployment command generation
            ok, msg = wifi_mgr.connect_home_wifi("Home_2.4G", "SecretPassword")
            self.assertTrue(ok)
            self.assertIn("Home_2.4G", msg)
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
        self.assertEqual(mock_mav3.sent_commands[0][0], calib_mgr.MAV_CMD_DO_START_MAG_CAL)

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
            while time.time() - t0 < 5.0:
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
        self.assertEqual(ver, "4.7.1")

        # Verify quick check against custom build API
        chk = arducopter_builder.update_engine.check_version_quick()
        self.assertTrue(chk.get("success"))
        self.assertEqual(chk.get("local_version"), "4.7.1")
        self.assertEqual(chk.get("online_version"), "4.7.1")
        self.assertFalse(chk.get("update_available"))

        # Verify clean install zip resolution
        zip_solo = flash_mgr.resolve_clean_install_zip("solo")
        zip_green = flash_mgr.resolve_clean_install_zip("green")
        self.assertTrue(os.path.exists(zip_solo))
        self.assertTrue(os.path.exists(zip_green))
        self.assertIn("Clean Install", zip_solo)
        self.assertIn("GREEN CUBE", zip_green)


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(TestSoloWebUtility)
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful():
        sys.exit(1)
