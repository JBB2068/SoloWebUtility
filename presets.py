#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ArduPilot Parameter Presets for 3DR Solo / Open Solo latest ArduCopter stable version.
Contains calibrated configurations for Here GPS, Landing Gear, Speed modes,
Bait drop, and LiPo / Li-ion Battery management.

Parameter names and units are ArduCopter 4.7 (e.g. ATC_ANGLE_MAX in degrees, WP_SPD and
LOIT_SPEED_MS in m/s). param_mgr writes the pre-4.7 name with the right scale when the
Cube still runs an older ArduCopter (see param_mgr.PARAM_ALIASES_PRE_4_7).
"""

PRESETS = {
    "here_gps": {
        "id": "here_gps",
        "name": "Here 1 / Here 2 GPS Compass",
        "category": "Navigation",
        "description": "Configures compass orientation for Here 1 and Here 2 external GPS units mounted upright facing forward (COMPASS_ORIENT = 0).",
        "icon": "🧭",
        "params": {
            "COMPASS_ORIENT": 0       # 0: ROTATION_NONE (Here 1 / Here 2 upright, arrow forward)
        }
    },
    "stock_compass": {
        "id": "stock_compass",
        "name": "Stock Solo Leg Compass",
        "category": "Navigation",
        "description": "Restores factory compass orientation for the stock compass located in the Solo leg (COMPASS_ORIENT = 38).",
        "icon": "🧭",
        "params": {
            "COMPASS_ORIENT": 38      # 38: ROTATION_PITCH_180_YAW_270 (Stock Solo Leg Compass)
        }
    },
    "retract_gear": {
        "id": "retract_gear",
        "name": "Retractable Landing Gear",
        "category": "Airframe",
        "description": "Configures landing gear servo output on SERVO7 (deployed 1000 us, retracted 2000 us), manual toggle on RC7, retract after takeoff and deploy for landing.",
        "icon": "🛬",
        "params": {
            "LGR_ENABLE": 1,          # Enable Landing Gear feature
            "LGR_OPTIONS": 3,         # bit0 retract after takeoff, bit1 deploy during land
            "SERVO7_FUNCTION": 29,    # 29: Landing Gear output on Servo 7
            # ArduCopter 4.x has no LGR_SERVO_DEPLOY/RTRACT: deployed = SERVO7_MAX and
            # retracted = SERVO7_MIN, swapped by SERVO7_REVERSED -> deploy 1000, retract 2000
            "SERVO7_MIN": 1000,
            "SERVO7_MAX": 2000,
            "SERVO7_REVERSED": 1,
            "RC7_OPTION": 29          # 29: Landing Gear switch on RC7
        }
    },
    "speed_default": {
        "id": "speed_default",
        "name": "Flight Performance: Default (Solo Stock)",
        "category": "Flight Performance",
        "description": "Restores the Solo's stock ArduCopter 4.7 flight feel: 20° max lean, 360°/s² tilt rate, 5 m/s loiter speed, gentle acceleration and braking, 1.33 m/s climb and descent. Use this to undo the Fast preset.",
        "icon": "🎬",
        "params": {
            # Exactly the defaults of the CubeSolo / CubeGreen-solo ArduCopter 4.7 builds
            # (board defaults.parm + the defaults embedded in the firmware)
            "ATC_ANGLE_MAX": 20.0,    # deg max lean angle
            "LOIT_ANG_MAX": 30.0,     # deg loiter lean limit (capped by ATC_ANGLE_MAX)
            "ATC_INPUT_TC": 0.25,     # s stick response time (higher = softer)
            "ATC_ACC_R_MAX": 360.0,   # deg/s^2 how fast roll can change
            "ATC_ACC_P_MAX": 360.0,   # deg/s^2 how fast pitch can change
            "ATC_ACC_Y_MAX": 70.0,    # deg/s^2 how fast yaw can change
            "PSC_NE_JERK": 5.0,       # m/s^3 how fast Fly/Loiter builds up acceleration
            "LOIT_BRK_JRK_M": 5.0,    # m/s^3 how fast the Loiter brake comes on / releases
            "PHLD_BRK_RATE": 6.0,     # deg/s PosHold: how fast the brake lean builds and the stick lean unwinds
            "PHLD_BRK_ANGLE": 25.0,   # deg PosHold: maximum lean used for braking
            "LOIT_SPEED_MS": 5.0,     # m/s max loiter speed
            "LOIT_ACC_MAX_M": 2.0,    # m/s^2 loiter acceleration
            "LOIT_BRK_ACC_M": 1.75,   # m/s^2 braking when sticks are released
            "LOIT_BRK_DELAY": 0.25,   # s delay before braking
            "WP_SPD": 3.0,            # m/s waypoint (Auto mission) speed
            "WP_SPD_UP": 3.2,         # m/s waypoint climb
            "WP_SPD_DN": 2.4,         # m/s waypoint descent
            "PILOT_SPD_UP": 1.33,     # m/s pilot climb
            "PILOT_SPD_DN": 1.33      # m/s pilot descent
        }
    },
    "speed_fast": {
        "id": "speed_fast",
        "name": "Flight Performance: Fast",
        "category": "Flight Performance",
        "description": "Much more responsive than Default: 2.5x faster tilt (900°/s²), quicker yaw, crisper sticks, 40° lean, quick starts and stops in Fly/Loiter (8 m/s top speed) and Position Hold (12°/s, 40° brake), 4 m/s climb and 3 m/s descent. Fly it in open space first.",
        "icon": "⚡",
        "params": {
            "ATC_ANGLE_MAX": 40.0,    # deg max lean angle (Default 20)
            "LOIT_ANG_MAX": 40.0,     # deg loiter lean limit (Default 30, which would cap Fly mode)
            "ATC_INPUT_TC": 0.12,     # s crisper stick response (Default 0.25)
            "ATC_ACC_R_MAX": 900.0,   # deg/s^2 roll rate of change (Default 360; ArduPilot generic 1100)
            "ATC_ACC_P_MAX": 900.0,   # deg/s^2 pitch rate of change (Default 360; ArduPilot generic 1100)
            "ATC_ACC_Y_MAX": 180.0,   # deg/s^2 yaw rate of change (Default 70; ArduPilot generic 270)
            "PSC_NE_JERK": 10.0,      # m/s^3 Fly/Loiter acceleration build-up (Default 5)
            "LOIT_BRK_JRK_M": 20.0,   # m/s^3 Loiter brake comes on / releases faster (Default 5)
            "PHLD_BRK_RATE": 12.0,    # deg/s PosHold brake/stick-release rate, the maximum (Default 8)
            "PHLD_BRK_ANGLE": 40.0,   # deg PosHold maximum brake lean (Default 30)
            # Loiter speeds up / slows down with time constant LOIT_SPEED_MS / (g*tan(lean)):
            # 8 m/s at 40 deg = 0.97 s (Default 1.4 s; 15 m/s at 35 deg was 2.2 s, i.e. laggy)
            "LOIT_SPEED_MS": 8.0,     # m/s max loiter speed (Default 5)
            "LOIT_ACC_MAX_M": 5.0,    # m/s^2 loiter acceleration (Default 2.0)
            "LOIT_BRK_ACC_M": 2.5,    # m/s^2 braking (Default 1.75; 2.5 is the maximum)
            "LOIT_BRK_DELAY": 0.1,    # s delay before the Loiter brake starts (Default 0.25)
            "WP_SPD": 10.0,           # m/s waypoint and RTL speed (Default 3)
            "WP_SPD_UP": 4.0,         # m/s waypoint climb (Default 3.2)
            "WP_SPD_DN": 2.5,         # m/s waypoint descent (Default 2.4)
            "PILOT_SPD_UP": 4.0,      # m/s pilot climb (Default 2.5)
            "PILOT_SPD_DN": 3.0       # m/s pilot descent (Default follows climb, 2.5)
        }
    },
    "bait_drop": {
        "id": "bait_drop",
        "name": "Bait Drop / Payload Release Gripper",
        "category": "Payload",
        "description": "Configures ArduPilot Gripper / Servo release on Pin 18 (PWM8 / Servo 8) with RC8 controller trigger.",
        "icon": "🎣",
        "params": {
            "GRIP_ENABLE": 1,         # 1: Enable Gripper system
            "GRIP_TYPE": 1,           # 1: Servo-actuated Gripper
            "SERVO8_FUNCTION": 28,    # 28: Gripper PWM output on Servo 8
            "RC8_OPTION": 19,         # 19: Gripper toggle on RC8
            "GRIP_GRAB": 1000,        # Grab / Hold PWM
            "GRIP_RELEASE": 2000      # Open / Drop PWM
        }
    },
    "lipo_default": {
        "id": "lipo_default",
        "name": "Stock 4S LiPo Battery Parameters",
        "category": "Battery & Power",
        "description": "Standard factory 4S Lithium-Polymer failsafe thresholds (14.0V Low Failsafe RTL, 13.5V Critical Failsafe RTL, 5200mAh pack).",
        "icon": "🔋",
        "params": {
            "BATT_MONITOR": 5,        # 5: Solo SMBus Smart Battery
            "BATT_ARM_VOLT": 13.5,    # Min voltage to allow arming
            "BATT_LOW_VOLT": 14.0,    # Low battery RTL voltage (3.5V/cell)
            "BATT_CRT_VOLT": 13.5,    # Critical RTL/Land voltage (3.375V/cell)
            "BATT_LOW_MAH": 520,      # Low capacity threshold (10%)
            "BATT_CRT_MAH": 250,      # Critical capacity threshold (5%)
            "BATT_LOW_TIMER": 10,     # Time under voltage before RTL (10s)
            "BATT_FS_LOW_ACT": 2,     # 2: RTL
            "BATT_FS_CRT_ACT": 2,     # 2: RTL / Land
            "BATT_FS_VOLTSRC": 1,     # Raw voltage source
            "MOT_BAT_VOLT_MIN": 13.2, # Motor thrust compensation min voltage
            "MOT_BAT_VOLT_MAX": 16.8, # Motor thrust compensation max voltage
            "MOT_BAT_CURR_MAX": 40.0  # Max continuous current limit
        }
    },
    "liion_default": {
        "id": "liion_default",
        "name": "4S Li-ion Battery Parameters (Madhacker / High-Cap)",
        "category": "Battery & Power",
        "description": "Optimized discharge curves for Lithium-Ion packs (18650 / 21700), lowering voltage cutoffs down to 11.6V Low / 11.3V Critical with Smart RTL.",
        "icon": "⚡",
        "params": {
            "BATT_MONITOR": 5,        # 5: SMBus / BMSOne
            "BATT_ARM_VOLT": 13.8,    # Arming voltage requirement
            "BATT_LOW_VOLT": 11.6,    # 11.6V Low Failsafe (2.9V/cell for Li-ion)
            "BATT_CRT_VOLT": 11.3,    # 11.3V Critical Failsafe (2.82V/cell)
            "BATT_LOW_MAH": 0,        # Disabled mAh limit (capacity-independent)
            "BATT_CRT_MAH": 0,        # Disabled mAh limit
            "BATT_LOW_TIMER": 2,      # 2 second low voltage filter
            "BATT_FS_LOW_ACT": 3,     # 3: Smart RTL
            "BATT_FS_CRT_ACT": 3,     # 3: Smart RTL
            "BATT_FS_VOLTSRC": 1,
            "MOT_BAT_VOLT_MIN": 10.8, # Lower thrust compensation limit
            "MOT_BAT_VOLT_MAX": 16.8,
            "MOT_BAT_CURR_MAX": 40.0
        }
    },
    "liion_5s": {
        "id": "liion_5s",
        "name": "5S Li-ion Battery Parameters (BMSOne / Upgrade Energy 5S2P)",
        "category": "Battery & Power",
        "description": "For 5S Li-ion packs on a BMSOne, set from the Upgrade Energy 5S2P 8Ah specs (21.0V full, 13.5V minimum, 30A): 14.5V Low / 14.1V Critical with Smart RTL. Same per-cell thresholds as the 4S Li-ion preset.",
        "icon": "⚡",
        "params": {
            "BATT_MONITOR": 5,        # 5: SMBus / BMSOne
            "BATT_ARM_VOLT": 15.0,    # Arming voltage requirement (3.0V/cell)
            "BATT_LOW_VOLT": 14.5,    # Low Failsafe (2.9V/cell)
            "BATT_CRT_VOLT": 14.1,    # Critical Failsafe (2.82V/cell), above the pack's 13.5V minimum
            "BATT_LOW_MAH": 0,        # Disabled mAh limit (capacity-independent)
            "BATT_CRT_MAH": 0,        # Disabled mAh limit
            "BATT_LOW_TIMER": 2,      # 2 second low voltage filter
            "BATT_FS_LOW_ACT": 3,     # 3: Smart RTL
            "BATT_FS_CRT_ACT": 3,     # 3: Smart RTL
            "BATT_FS_VOLTSRC": 1,
            "MOT_BAT_VOLT_MIN": 13.5, # Thrust compensation down to the pack minimum (2.7V/cell)
            "MOT_BAT_VOLT_MAX": 21.0, # 5S full charge (4.2V/cell)
            "MOT_BAT_CURR_MAX": 30.0  # Pack's continuous current rating
        }
    }
}


def get_all_presets():
    """Return list of all available preset configurations."""
    return list(PRESETS.values())


def get_preset(preset_id):
    """Return specific preset by ID."""
    return PRESETS.get(preset_id)
