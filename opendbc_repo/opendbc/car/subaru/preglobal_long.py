"""Blended longitudinal for the preglobal Outback.

EyeSight keeps driving. openpilot rebuilds each of EyeSight's longitudinal messages as it arrives and
may only add braking or remove throttle, never the reverse. The panda enforces the same bounds
(opendbc/safety/modes/subaru_preglobal.h), so EyeSight's ACC and AEB stay underneath as a floor.

The tables are fit to 41 minutes of EyeSight cruise on this car (the 2026-10-18 drives): the
acceleration that EyeSight's own requests produced, with road grade removed.
"""
import math

import numpy as np

from opendbc.car import ACCELERATION_DUE_TO_GRAVITY, DT_CTRL, structs
from opendbc.car.common.filter_simple import FirstOrderFilter

BRAKE_MAX = 600              # most braking openpilot may request; SUBARU_PG_MAX_BRAKE in panda safety
THROTTLE_ENGINE_BRAKE = 808  # EyeSight's own throttle while it brakes; SUBARU_PG_THROTTLE_ENGINE_BRAKE
THROTTLE_NO_LIMIT = 4095     # Cruise_Throttle is 12 bits, so this never lowers EyeSight's request

# brake lights for braking openpilot adds: EyeSight's own lights lag its braking by about a second,
# so turn them on promptly above this pressure (global Subaru's rule), and off once braking has faded
BRAKE_LIGHTS_ON = 70
BRAKE_LIGHTS_OFF = 20

# acceleration (m/s^2, grade removed) from a steady Cruise_Throttle with no braking, by speed (m/s).
# The first column, EyeSight's engine-braking throttle, is coasting.
SPEED_BP = [5., 15., 25., 35.]
THROTTLE_BP = [808, 1750, 2250, 2750, 3250, 3750]
THROTTLE_ACCEL = [
  [-0.26, -0.12, 0.29, 0.74, 1.06, 1.35],
  [-0.34, -0.20, 0.03, 0.27, 0.59, 0.90],
  [-0.48, -0.33, -0.16, 0.04, 0.26, 0.60],
  [-0.58, -0.42, -0.25, -0.08, 0.10, 0.33],
]

# deceleration beyond coasting (m/s^2) from Brake_Pressure. Pressures under ~25 do almost nothing.
BRAKE_DECEL_BP = [-3.51, -2.79, -2.48, -1.83, -1.34, -0.95, -0.65, -0.34, -0.13, -0.05]
BRAKE_PRESSURE = [761, 575, 449, 335, 246, 176, 126, 71, 36, 0]


def accel_to_commands(accel: float, v_ego: float) -> tuple[int, int]:
  """Brake_Pressure, and the Cruise_Throttle ceiling, for a grade-corrected acceleration request."""
  throttle_accel = [float(np.interp(v_ego, SPEED_BP, column)) for column in zip(*THROTTLE_ACCEL, strict=True)]
  coast = throttle_accel[0]

  if accel >= coast:
    if accel >= throttle_accel[-1]:
      return 0, THROTTLE_NO_LIMIT
    return 0, int(round(np.interp(accel, throttle_accel, THROTTLE_BP)))

  brake = int(round(np.interp(accel - coast, BRAKE_DECEL_BP, BRAKE_PRESSURE)))
  return min(brake, BRAKE_MAX), THROTTLE_ENGINE_BRAKE


class PreglobalBlend:
  def __init__(self):
    self.pitch = FirstOrderFilter(0., 0.5, DT_CTRL)
    self.active = False
    self.brake = 0
    self.throttle = THROTTLE_NO_LIMIT
    self.brake_lights = False

  def update(self, CC: structs.CarControl, v_ego: float) -> None:
    """openpilot's request for this frame. Call once per control frame, before rebuilding messages."""
    if len(CC.orientationNED) == 3:
      self.pitch.update(CC.orientationNED[1])

    self.active = CC.longActive
    if self.active:
      # the tables have grade removed, so add back what the slope will do to the car
      accel = CC.actuators.accel + ACCELERATION_DUE_TO_GRAVITY * math.sin(self.pitch.x)
      self.brake, self.throttle = accel_to_commands(accel, v_ego)
    else:
      self.brake, self.throttle = 0, THROTTLE_NO_LIMIT
      self.brake_lights = False

  def es_brake(self, msg: dict[str, float]) -> dict[str, float]:
    values = dict(msg)
    if not self.active:
      return values

    eyesight_brake = int(msg["Brake_Pressure"])
    brake = max(eyesight_brake, self.brake)
    if self.brake > eyesight_brake and brake >= BRAKE_LIGHTS_ON:
      self.brake_lights = True
    elif brake < BRAKE_LIGHTS_OFF:
      self.brake_lights = False

    values["Brake_Pressure"] = brake
    # EyeSight sets this exactly when Brake_Pressure is non-zero
    values["Cruise_Brake_Active"] = int(brake > 0)
    values["Cruise_Brake_Lights"] = int(bool(msg["Cruise_Brake_Lights"]) or self.brake_lights)
    return values

  def es_distance(self, msg: dict[str, float]) -> dict[str, float]:
    values = dict(msg)
    if not self.active:
      return values

    values["Cruise_Throttle"] = min(int(msg["Cruise_Throttle"]), self.throttle)
    values["Cruise_Brake_Active"] = int(bool(msg["Cruise_Brake_Active"]) or self.brake > 0)
    return values

  def es_status(self, msg: dict[str, float]) -> dict[str, float]:
    values = dict(msg)
    if not self.active:
      return values

    # EyeSight sets this exactly when ES_Brake's Brake_Pressure is non-zero. Cruise_RPM passes through.
    values["Brake"] = int(bool(msg["Brake"]) or self.brake > 0)
    return values
