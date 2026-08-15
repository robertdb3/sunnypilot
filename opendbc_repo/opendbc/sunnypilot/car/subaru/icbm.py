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
BUTTON_SET_FINE = 2      # sustained shallow SET; duration selects the fine action
BUTTON_RES_COARSE = 4    # short tap up   -> snap to next higher multiple of 5
BUTTON_RES_FINE = 4      # sustained shallow RES; duration selects the fine action

COARSE_SNAP = 5          # mph the coarse tap snaps to

# Both vTarget and vCruiseCluster arrive in the cluster's DISPLAY units already, rounded to whole
# numbers by the shared controller -- NOT in m/s. That is why nothing here converts: comparing a
# display-unit target against a m/s cluster speed would be off by the conversion factor. It also
# keeps this correct for a metric car, where both sides are simply kph instead.

# ES_Distance goes out every 5 frames (20 Hz), so a press occupies one 50 ms slot.
PRESS_CONFIRM_TIMEOUT = 1.2    # s to wait for the set speed to move before assuming the tap was lost
DRIVER_COOLDOWN = 1.0          # s to stay out of the way after the driver touches a button
ES_DISTANCE_FRAME_STEP = 5

# The read-only raw-CAN probe proved that quick and held physical presses both use shallow codes
# 2/4; a hold is represented by repeating that code at 20 Hz. Six on-road trials changed the set
# speed once after 0.806-0.856 s. Nineteen slots assert through 0.90 s, while the state machine
# releases earlier when it observes the first change.
FINE_STEP_ENABLED = True
FINE_HOLD_SLOTS = 19


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
    self.hold_button = BUTTON_NONE
    self.hold_slots_remaining = 0
    self.hold_last_frame = 0
    self.hold_increase = False

  def _clear_hold(self) -> None:
    self.hold_button = BUTTON_NONE
    self.hold_slots_remaining = 0
    self.hold_last_frame = 0

  def _candidates(self, v: int, increase: bool) -> list[tuple[int, int, int]]:
    """(predicted set speed, button, slots) in preference order."""
    out = [(coarse_up(v) if increase else coarse_down(v),
            BUTTON_RES_COARSE if increase else BUTTON_SET_COARSE, 1)]
    if FINE_STEP_ENABLED and FINE_HOLD_SLOTS >= 2 and self.fine_step_enabled:
      out.append((v + 1 if increase else v - 1,
                  BUTTON_RES_FINE if increase else BUTTON_SET_FINE, FINE_HOLD_SLOTS))
    return out

  def button_for(self, CC_SP, CS, frame: int) -> int:
    """Return the Cruise_Button value to send this cycle, or BUTTON_NONE."""
    icbm = getattr(CC_SP, "intelligentCruiseButtonManagement", None)
    if icbm is None or icbm.sendButton == SendButtonState.none:
      self._clear_hold()
      return BUTTON_NONE

    self.fine_step_enabled = bool(getattr(icbm, "fineStepEnabled", False))

    # Only ever nudge a cruise system that is actually engaged.
    if not CS.out.cruiseState.enabled:
      self._clear_hold()
      return BUTTON_NONE

    # Preglobal Subaru publishes no cruise buttonEvents, so the shared controller's
    # "driver is pressing" check (controller.py:113) can never fire on this platform. Watch the
    # raw relayed button instead and yield the bus while the driver is adjusting.
    if CS.cruise_button != BUTTON_NONE:
      self.driver_active_frame = frame
      self.pending_since_frame = None
      self._clear_hold()
      return BUTTON_NONE

    if (frame - self.driver_active_frame) * DT_CTRL < DRIVER_COOLDOWN:
      return BUTTON_NONE

    cluster = round(getattr(icbm, "vCruiseCluster", 0.0))

    target = round(getattr(icbm, "vTarget", 0.0))
    if target <= 0 or cluster <= 0:
      self._clear_hold()
      return BUTTON_NONE

    increase = icbm.sendButton == SendButtonState.increase

    # A fine action is the same shallow code repeated in uninterrupted 50 ms slots. Abort on a
    # missed carcontroller call (for example cancel/main taking priority), a direction change,
    # target invalidation/crossing, or the first observed set-speed movement. Never resume a
    # partially transmitted hold after another message source has occupied ES_Distance.
    if self.hold_button != BUTTON_NONE:
      uninterrupted = frame - self.hold_last_frame == ES_DISTANCE_FRAME_STEP
      same_direction = increase == self.hold_increase
      still_toward_target = target > cluster if increase else target < cluster
      set_speed_unchanged = cluster == self.cluster_at_press
      if not (uninterrupted and same_direction and still_toward_target and set_speed_unchanged):
        self._clear_hold()
        return BUTTON_NONE

      if self.hold_slots_remaining > 0:
        self.hold_slots_remaining -= 1
        self.hold_last_frame = frame
        return self.hold_button

      self._clear_hold()
      self.pending_since_frame = frame
      return BUTTON_NONE

    # Wait for the previous tap to land before sending another. Gating on observed movement
    # instead of a fixed interval makes overshoot structurally impossible rather than tuned away,
    # and it does not care how big the step turns out to be.
    if self.pending_since_frame is not None:
      moved = cluster != self.cluster_at_press
      timed_out = (frame - self.pending_since_frame) * DT_CTRL > PRESS_CONFIRM_TIMEOUT
      if not (moved or timed_out):
        return BUTTON_NONE
      self.pending_since_frame = None

    error = abs(target - cluster)

    for predicted, button, slots in self._candidates(cluster, increase):
      if abs(target - predicted) < error:
        self.cluster_at_press = cluster
        self.button_frame += 1
        if slots > 1:
          self.hold_button = button
          self.hold_slots_remaining = slots - 1
          self.hold_last_frame = frame
          self.hold_increase = increase
        else:
          self.pending_since_frame = frame
        return button

    # Nothing available gets us closer -- we are as near as this car's steps allow. The shared
    # state machine will keep flapping increasing/decreasing underneath, which is harmless
    # because we simply do not act on it.
    return BUTTON_NONE
