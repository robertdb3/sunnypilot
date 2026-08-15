"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

import math

from opendbc.car import structs, DT_CTRL
from opendbc.sunnypilot.car.intelligent_cruise_button_management_interface_base import \
  IntelligentCruiseButtonManagementInterfaceBase

SendButtonState = structs.IntelligentCruiseButtonManagement.SendButtonState

# ES_Distance->Cruise_Button, per the preglobal DBC:
#   1 = main, 2 = set shallow, 3 = set deep, 4 = resume shallow, 5 = resume deep
# On Subaru ACC, RES raises the set speed and SET lowers it.
#
# On this platform the SHORT tap is the COARSE action and the long hold is the fine one -- the
# opposite of what the "shallow / deep" naming suggests. A short tap does not add 5 either: it
# SNAPS to the next multiple of 5, so from 63 a tap down lands on 60, not 58.
BUTTON_NONE = 0
BUTTON_SET_COARSE = 2    # short tap down -> snap to next lower multiple of 5
BUTTON_SET_FINE = 3      # long hold down -> -1
BUTTON_RES_COARSE = 4    # short tap up   -> snap to next higher multiple of 5
BUTTON_RES_FINE = 5      # long hold up   -> +1

COARSE_SNAP = 5          # mph the coarse tap snaps to

# Both vTarget and vCruiseCluster arrive in the cluster's DISPLAY units already, rounded to whole
# numbers by the shared controller -- NOT in m/s. That is why nothing here converts: comparing a
# display-unit target against a m/s cluster speed would be off by the conversion factor. It also
# keeps this correct for a metric car, where both sides are simply kph instead.

# ES_Distance goes out every 5 frames (20 Hz), so a press occupies one 50 ms slot.
PRESS_CONFIRM_TIMEOUT = 1.2    # s to wait for the set speed to move before assuming the tap was lost
DRIVER_COOLDOWN = 1.0          # s to stay out of the way after the driver touches a button

# Whether a single frame of the fine code actually produces the 1 mph action, or whether the ECU
# decodes "long hold" from a sustained assertion, is unverified on the car. Coarse-only is a
# perfectly good shipping state: US limits are multiples of 5 and the coarse tap snaps to
# multiples of 5, so it lands exactly on the limit for any offset that is 0 or a multiple of 5.
# Flip this on once tools/probe_cruise_button.py shows the fine code working from a tap.
FINE_STEP_ENABLED = False


def coarse_down(v: int) -> int:
  return int(math.floor(v / COARSE_SNAP) * COARSE_SNAP) if v % COARSE_SNAP else v - COARSE_SNAP


def coarse_up(v: int) -> int:
  return int(math.ceil(v / COARSE_SNAP) * COARSE_SNAP) if v % COARSE_SNAP else v + COARSE_SNAP


class IntelligentCruiseButtonManagementInterface(IntelligentCruiseButtonManagementInterfaceBase):
  """ICBM for preglobal Subaru.

  Differs in shape from the Mazda/Chrysler/Hyundai ports: those append a dedicated button message
  to can_sends, but preglobal Subaru carries the button inside ES_Distance, which the
  carcontroller is already transmitting every 5 frames. So this returns a button code for the
  caller to inject into that message rather than a list of CanData.

  It also decides for itself whether to press at all. The shared controller stops on *crossing*
  rather than proximity (controller.py:91,96) and compares set speeds for exact equality, which
  with a coarse step oscillates forever: target 55 from 43 climbs 48-53-58, sees it overshot,
  drops to 53, sees it undershot, and repeats. So rather than obeying the requested direction
  blindly, only press when the press strictly reduces the error. That is self-limiting, needs no
  deadband, and settles on the closest reachable set speed.
  """

  def __init__(self, CP, CP_SP):
    super().__init__(CP, CP_SP)
    self.driver_active_frame = -10_000
    self.pending_since_frame: int | None = None
    self.cluster_at_press = 0

  def _candidates(self, v: int, increase: bool) -> list[tuple[int, int]]:
    """(predicted set speed, button) in preference order: coarse to close, fine to land."""
    out = [(coarse_up(v) if increase else coarse_down(v),
            BUTTON_RES_COARSE if increase else BUTTON_SET_COARSE)]
    if FINE_STEP_ENABLED:
      out.append((v + 1 if increase else v - 1,
                  BUTTON_RES_FINE if increase else BUTTON_SET_FINE))
    return out

  def button_for(self, CC_SP, CS, frame: int) -> int:
    """Return the Cruise_Button value to send this cycle, or BUTTON_NONE."""
    icbm = getattr(CC_SP, "intelligentCruiseButtonManagement", None)
    if icbm is None or icbm.sendButton == SendButtonState.none:
      return BUTTON_NONE

    # Only ever nudge a cruise system that is actually engaged.
    if not CS.out.cruiseState.enabled:
      return BUTTON_NONE

    # Preglobal Subaru publishes no cruise buttonEvents, so the shared controller's
    # "driver is pressing" check (controller.py:113) can never fire on this platform. Watch the
    # raw relayed button instead and yield the bus while the driver is adjusting.
    if CS.cruise_button != BUTTON_NONE:
      self.driver_active_frame = frame
      self.pending_since_frame = None
      return BUTTON_NONE

    if (frame - self.driver_active_frame) * DT_CTRL < DRIVER_COOLDOWN:
      return BUTTON_NONE

    cluster = round(getattr(icbm, "vCruiseCluster", 0.0))

    # Wait for the previous tap to land before sending another. Gating on observed movement
    # instead of a fixed interval makes overshoot structurally impossible rather than tuned away,
    # and it does not care how big the step turns out to be.
    if self.pending_since_frame is not None:
      moved = cluster != self.cluster_at_press
      timed_out = (frame - self.pending_since_frame) * DT_CTRL > PRESS_CONFIRM_TIMEOUT
      if not (moved or timed_out):
        return BUTTON_NONE
      self.pending_since_frame = None

    target = round(getattr(icbm, "vTarget", 0.0))
    if target <= 0 or cluster <= 0:
      return BUTTON_NONE

    increase = icbm.sendButton == SendButtonState.increase
    error = abs(target - cluster)

    for predicted, button in self._candidates(cluster, increase):
      if abs(target - predicted) < error:
        self.pending_since_frame = frame
        self.cluster_at_press = cluster
        self.button_frame += 1
        return button

    # Nothing available gets us closer -- we are as near as this car's steps allow. The shared
    # state machine will keep flapping increasing/decreasing underneath, which is harmless
    # because we simply do not act on it.
    return BUTTON_NONE
