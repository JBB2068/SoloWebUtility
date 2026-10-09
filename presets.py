#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ArduPilot Parameter Presets for 3DR Solo / Open Solo latest ArduCopter stable version.
Contains calibrated configurations for Here GPS, Landing Gear, Speed modes,
Bait drop, and LiPo / Li-ion Battery management.
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
        "description": "Configures landing gear servo output on SERVO7, manual toggle on RC7, and automatic deployment in RTL and LAND modes.",
        "icon": "🛬",
        "params": {
            "LGR_ENABLE": 1,          # Enable Landing Gear feature
            "LGR_OPTIONS": 3,         # Auto-deploy on RTL and Land modes
            "LGR_SERVO_DEPLOY": 1000, # Deploy PWM value
            "LGR_SERVO_RTRACT": 2000, # Retract PWM value
            "SERVO7_FUNCTION": 29,    # 29: Landing Gear output on Servo 7
            "RC7_OPTION": 29          # 29: Landing Gear switch on RC7
        }
    },
    "speed_sport": {
        "id": "speed_sport",
        "name": "Speed Control: Sport Mode (High Velocity)",
        "category": "Flight Performance",
        "description": "Unlocks maximum agility with 15 m/s waypoint cruise, 12 m/s loiter speed, faster vertical climb/descent rates, and 35° lean angle.",
        "icon": "⚡",
        "params": {
            "WPNAV_SPEED": 1500,      # 15.0 m/s horizontal waypoint speed
            "WPNAV_SPEED_UP": 400,    # 4.0 m/s max climb rate
            "WPNAV_SPEED_DN": 300,    # 3.0 m/s max descent rate
            "LOIT_SPEED": 1200,       # 12.0 m/s max loiter speed
            "LOIT_ACC_MAX": 350,      # 3.5 m/s^2 acceleration
            "LOIT_BRK_ACCEL": 250,    # 2.5 m/s^2 braking acceleration
            "ANGLE_MAX": 3500,        # 35.0 deg maximum pitch/roll lean angle
            "PILOT_SPEED_UP": 250,    # 2.5 m/s pilot climb
            "PILOT_SPEED_DN": 200     # 2.0 m/s pilot descent
        }
    },
    "speed_cinematic": {
        "id": "speed_cinematic",
        "name": "Speed Control: Cinematic Mode (Smooth)",
        "category": "Flight Performance",
        "description": "Smooth gentle accelerations and dampened braking optimized for ultra-smooth video recording and stabilized photography.",
        "icon": "🎬",
        "params": {
            "WPNAV_SPEED": 500,       # 5.0 m/s smooth cruise
            "WPNAV_SPEED_UP": 180,    # 1.8 m/s climb
            "WPNAV_SPEED_DN": 120,    # 1.2 m/s descent
            "LOIT_SPEED": 500,        # 5.0 m/s loiter speed
            "LOIT_ACC_MAX": 150,      # Gentle acceleration
            "LOIT_BRK_ACCEL": 120,    # Gentle braking
            "LOIT_BRK_DELAY": 0.4,    # Smooth braking deceleration delay
            "ANGLE_MAX": 2000         # 20.0 deg max lean angle
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
    }
}


def get_all_presets():
    """Return list of all available preset configurations."""
    return list(PRESETS.values())


def get_preset(preset_id):
    """Return specific preset by ID."""
    return PRESETS.get(preset_id)
