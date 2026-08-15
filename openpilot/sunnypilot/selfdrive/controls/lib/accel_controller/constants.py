import math

import numpy as np

from openpilot.cereal import custom


AccelProfile = custom.LongitudinalPlanSP.AccelController.Profile
ACCEL_PROFILES = tuple(AccelProfile.schema.enumerants.values())

COMFORT_DECEL = {
  AccelProfile.eco: 0.25,
  AccelProfile.normal: 0.30,
  AccelProfile.sport: 0.35,
}

ACCEL_PROFILE_MAX_BP = [0.0, 3.0, 10.0, 25.0, 40.0]
ACCEL_PROFILE_MAX_V = {
  AccelProfile.eco: [1.56, 1.30, 0.72, 0.32, 0.24],
  AccelProfile.normal: [1.58, 1.51, 0.98, 0.53, 0.35],
  AccelProfile.sport: [2.00, 1.91, 1.16, 0.73, 0.47],
}

BRAKING_ACCEL_THRESHOLD = -0.11

LEAD_SAMPLE_FILTER_FRAMES = 5
LEAD_RELEASE_CONFIRM_TIME = 0.50
LEAD_DROPOUT_COAST_TIME = 1.50

SPEED_DEADBAND = 0.15

TARGET_RELEASE_SLEW = 8.75
LAUNCH_TARGET_HEADROOM = 3.0
LAUNCH_END_SPEED = 3.0

LEAD_RECOVERY_HEADROOM = 1.25
LEAD_RECOVERY_ACCEL_SLEW = 0.25
LEAD_RECOVERY_DECEL_RATE = 0.50

STOP_HOLD_EGO_SPEED = 0.30
STOP_HOLD_SPEED_FLOOR = 0.15
STOP_HOLD_EXIT_FRAMES = 4
STOP_HOLD_CREEP_DISTANCE = 0.30
STOP_HOLD_MAX_LEAD_DISTANCE = 30.0
DISTANCE_JUMP_CONFIRM_FRAMES = 3

STOP_GAP_RESERVE = 0.75

RADAR_STALE_TIMEOUT = 0.50
MAX_LEAD_ACCEL_TAU = 10.0
MIN_LEAD_SPEED = -1.0
VEGO_NOISE_TOLERANCE = 0.10
PARAM_READ_INTERVAL = 0.25
ACCEL_LIMIT_HORIZON_JERK = 1.0

MPC_DECEL_JERK_COST_MULTIPLIER = 1.05
MPC_DECEL_JERK_MAX_REQUIRED_DECEL = 0.80
MPC_DECEL_JERK_MAX_REQUIRED_DECEL_RATE = 0.35
MPC_DECEL_JERK_LONG_TREND_FRAMES = 6
MPC_DECEL_JERK_LONG_TREND_RATE = 0.02
MPC_DECEL_JERK_MAX_TARGET_REDUCTION = 9.0
MPC_DECEL_TREND_FRAMES = 4


def sanitize_profile(profile: int) -> int:
  return profile if profile in ACCEL_PROFILES else AccelProfile.normal


def profile_accel_max(profile: int, v_ego: float) -> float:
  if not math.isfinite(v_ego):
    return math.nan
  return float(np.interp(max(v_ego, 0.0), ACCEL_PROFILE_MAX_BP, ACCEL_PROFILE_MAX_V[sanitize_profile(profile)]))
