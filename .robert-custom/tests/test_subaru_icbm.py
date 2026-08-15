"""Tests for preglobal Subaru ICBM button selection.

    python3 tests/test_subaru_icbm.py

Stubs the opendbc structs the module imports so this runs on plain python3 with no compiled
dependencies, matching the other suites in this repo.

The centrepiece is TestNoOscillation. The shared ICBM controller stops on crossing rather than
proximity, which with this car's coarse step would hunt between two set speeds forever; the
Subaru interface only presses when a press strictly reduces the error, and that has to stay true.
"""
import os
import sys
import types
import unittest

REPO = os.path.join(os.path.dirname(__file__), "..", "sunnypilot")

class _SendButtonState:
  none, increase, decrease = 0, 1, 2


def _install_stubs():
  car = types.ModuleType("opendbc.car")
  car.DT_CTRL = 0.01

  class _ICBMStruct:
    SendButtonState = _SendButtonState

  class _Structs:
    IntelligentCruiseButtonManagement = _ICBMStruct

  car.structs = _Structs
  sys.modules["opendbc.car"] = car

  base = types.ModuleType("opendbc.sunnypilot.car.intelligent_cruise_button_management_interface_base")

  class _Base:
    def __init__(self, CP, CP_SP):
      self.CP, self.CP_SP = CP, CP_SP
      self.frame = 0
      self.button_frame = 0
      self.last_button_frame = 0

  base.IntelligentCruiseButtonManagementInterfaceBase = _Base
  sys.modules["opendbc.sunnypilot.car.intelligent_cruise_button_management_interface_base"] = base


_install_stubs()

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
  "subaru_icbm", os.path.join(REPO, "opendbc_repo", "opendbc", "sunnypilot", "car", "subaru", "icbm.py"))
icbm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(icbm)


class _CS:
  """Minimal CarState: engaged ACC and the relayed camera button.

  The set speed no longer comes from here -- it arrives on the ICBM struct in display units.
  """
  def __init__(self, set_mph=55, enabled=True, cruise_button=0):
    self.out = types.SimpleNamespace(cruiseState=types.SimpleNamespace(enabled=enabled))
    self.cruise_button = cruise_button


class _CCSP:
  """vTarget and vCruiseCluster are display units (mph here), whole numbers, never m/s."""
  def __init__(self, send, target_mph=55, cluster_mph=55, fine=True):
    self.intelligentCruiseButtonManagement = types.SimpleNamespace(
      sendButton=send, vTarget=float(target_mph), vCruiseCluster=float(cluster_mph),
      fineStepEnabled=fine)


def _iface():
  return icbm.IntelligentCruiseButtonManagementInterface(CP=None, CP_SP=None)


def _settle(target_mph, start_mph, max_presses=40):
  """Drive the loop the way the car would: press, observe the snap, repeat.

  Returns (final set speed, number of presses). Loops far past any sane press count so an
  oscillation shows up as hitting the cap rather than hanging.
  """
  i = _iface()
  cluster = start_mph
  frame = 5_000
  presses = 0

  for _ in range(max_presses):
    send = (_SendButtonState.increase if target_mph > cluster
            else _SendButtonState.decrease if target_mph < cluster
            else _SendButtonState.none)
    btn = i.button_for(_CCSP(send, target_mph, cluster), _CS(cluster), frame)
    if btn == icbm.BUTTON_NONE:
      break

    # A fine action repeats the shallow code across one bounded hold. Consume all of its CAN
    # slots, then model the ECU's single resulting 1-unit change; do not count every frame as a
    # separate tap.
    if i.hold_button != icbm.BUTTON_NONE:
      while i.hold_button != icbm.BUTTON_NONE:
        frame += icbm.ES_DISTANCE_FRAME_STEP
        i.button_for(_CCSP(send, target_mph, cluster), _CS(cluster), frame)
      cluster += 1 if send == _SendButtonState.increase else -1
    elif btn == icbm.BUTTON_SET_COARSE:
      cluster = icbm.coarse_down(cluster)
    elif btn == icbm.BUTTON_RES_COARSE:
      cluster = icbm.coarse_up(cluster)
    presses += 1
    frame += 200   # past the confirmation wait

  return cluster, presses


class TestSnapModel(unittest.TestCase):
  def test_snaps_to_multiple_of_five(self):
    self.assertEqual(icbm.coarse_down(63), 60)
    self.assertEqual(icbm.coarse_up(63), 65)
    self.assertEqual(icbm.coarse_down(58), 55)

  def test_on_a_multiple_it_steps_a_whole_five(self):
    self.assertEqual(icbm.coarse_down(60), 55)
    self.assertEqual(icbm.coarse_up(60), 65)


class TestNoOscillation(unittest.TestCase):
  """Regression for the hunting bug: 43 -> 55 used to cycle 53/58 forever."""

  def test_converges_from_below(self):
    final, presses = _settle(target_mph=55, start_mph=43)
    self.assertEqual(final, 55)
    self.assertLess(presses, 6)

  def test_converges_from_above(self):
    final, presses = _settle(target_mph=55, start_mph=72)
    self.assertEqual(final, 55)
    self.assertLess(presses, 6)

  def test_settles_and_stops_when_target_is_unreachable(self):
    """With coarse-only steps an odd target cannot be hit exactly; it must stop at the nearest
    reachable value rather than hunt around it."""
    icbm.FINE_STEP_ENABLED = False
    try:
      final, presses = _settle(target_mph=58, start_mph=72)
      self.assertEqual(final, 60)          # 60 is 2 away; 55 would be 3
      self.assertLess(presses, 6)
    finally:
      icbm.FINE_STEP_ENABLED = True

  def test_already_on_target_sends_nothing(self):
    i = _iface()
    self.assertEqual(i.button_for(_CCSP(_SendButtonState.increase, 55, 55), _CS(55), 5000),
                     icbm.BUTTON_NONE)

  def test_never_presses_away_from_target(self):
    """Even if the shared state machine asks for the wrong direction, decline it."""
    i = _iface()
    # sitting at 55 with target 55, but told to increase -- 60 is worse, so refuse
    self.assertEqual(i.button_for(_CCSP(_SendButtonState.increase, 55, 55), _CS(55), 5000),
                     icbm.BUTTON_NONE)


class TestConfirmationWait(unittest.TestCase):
  def test_holds_off_until_the_set_speed_moves(self):
    i = _iface()
    self.assertEqual(i.button_for(_CCSP(_SendButtonState.decrease, 55, 72), _CS(72), 1000),
                     icbm.BUTTON_SET_COARSE)
    # cluster has not updated yet: no second press
    self.assertEqual(i.button_for(_CCSP(_SendButtonState.decrease, 55, 72), _CS(72), 1010),
                     icbm.BUTTON_NONE)
    # now it moves -- next press allowed
    self.assertEqual(i.button_for(_CCSP(_SendButtonState.decrease, 55, 70), _CS(70), 1020),
                     icbm.BUTTON_SET_COARSE)

  def test_times_out_if_the_press_never_lands(self):
    i = _iface()
    i.button_for(_CCSP(_SendButtonState.decrease, 55, 72), _CS(72), 1000)
    stuck = 1000 + int(icbm.PRESS_CONFIRM_TIMEOUT / 0.01) + 1
    self.assertEqual(i.button_for(_CCSP(_SendButtonState.decrease, 55, 72), _CS(72), stuck),
                     icbm.BUTTON_SET_COARSE)


class TestGuards(unittest.TestCase):
  def test_never_presses_when_acc_disengaged(self):
    cs = _CS(72, enabled=False)
    self.assertEqual(i_btn := _iface().button_for(_CCSP(_SendButtonState.decrease, 55, 72), cs, 5000),
                     icbm.BUTTON_NONE, i_btn)

  def test_yields_while_driver_is_pressing(self):
    """Preglobal publishes no cruise buttonEvents, so the shared controller's driver check is
    dead here and this guard is the only thing keeping us off the bus."""
    cs = _CS(72, cruise_button=4)
    self.assertEqual(_iface().button_for(_CCSP(_SendButtonState.decrease, 55, 72), cs, 5000),
                     icbm.BUTTON_NONE)

  def test_cooldown_after_driver_releases(self):
    i = _iface()
    i.button_for(_CCSP(_SendButtonState.decrease, 55, 72), _CS(72, cruise_button=4), 5000)
    self.assertEqual(i.button_for(_CCSP(_SendButtonState.decrease, 55, 72), _CS(72), 5050),
                     icbm.BUTTON_NONE)
    self.assertEqual(i.button_for(_CCSP(_SendButtonState.decrease, 55, 72), _CS(72), 5000 + 101),
                     icbm.BUTTON_SET_COARSE)

  def test_no_target_sends_nothing(self):
    self.assertEqual(_iface().button_for(_CCSP(_SendButtonState.decrease, 0, 72), _CS(72), 5000),
                     icbm.BUTTON_NONE)

  def test_none_sends_nothing(self):
    self.assertEqual(_iface().button_for(_CCSP(_SendButtonState.none, 55, 72), _CS(72), 5000),
                     icbm.BUTTON_NONE)

  def test_declined_call_has_no_side_effect(self):
    i = _iface()
    i.button_for(_CCSP(_SendButtonState.none, 55, 72), _CS(72), 1000)
    self.assertEqual(i.button_for(_CCSP(_SendButtonState.decrease, 55, 72), _CS(72), 1001),
                     icbm.BUTTON_SET_COARSE)


class TestFineStep(unittest.TestCase):
  def test_measured_hold_constants_are_enabled(self):
    self.assertTrue(icbm.FINE_STEP_ENABLED)
    self.assertEqual(icbm.FINE_HOLD_SLOTS, 19)

  def test_measured_hold_reaches_non_multiple_of_five(self):
    final, presses = _settle(target_mph=58, start_mph=72)
    self.assertEqual(final, 58)
    self.assertLess(presses, 8)

  def test_setting_off_retains_coarse_only_behavior(self):
    i = _iface()
    self.assertEqual(i.button_for(_CCSP(_SendButtonState.decrease, 58, 60, fine=False),
                                  _CS(60), 5000), icbm.BUTTON_NONE)

  def test_fine_repeats_shallow_code_for_bounded_slots(self):
    icbm.FINE_STEP_ENABLED = True
    icbm.FINE_HOLD_SLOTS = 3
    try:
      i = _iface()
      cc = _CCSP(_SendButtonState.decrease, 58, 60)
      self.assertEqual(i.button_for(cc, _CS(60), 5000), icbm.BUTTON_SET_COARSE)
      self.assertEqual(i.button_for(cc, _CS(60), 5005), icbm.BUTTON_SET_COARSE)
      self.assertEqual(i.button_for(cc, _CS(60), 5010), icbm.BUTTON_SET_COARSE)
      self.assertEqual(i.button_for(cc, _CS(60), 5015), icbm.BUTTON_NONE)
    finally:
      icbm.FINE_STEP_ENABLED = True
      icbm.FINE_HOLD_SLOTS = 19

  def test_fine_uses_no_unobserved_deep_code(self):
    self.assertEqual(icbm.BUTTON_SET_FINE, 2)
    self.assertEqual(icbm.BUTTON_RES_FINE, 4)

  def test_interrupted_hold_is_abandoned(self):
    icbm.FINE_STEP_ENABLED = True
    icbm.FINE_HOLD_SLOTS = 4
    try:
      i = _iface()
      cc = _CCSP(_SendButtonState.decrease, 58, 60)
      self.assertEqual(i.button_for(cc, _CS(60), 5000), icbm.BUTTON_SET_COARSE)
      # Missing the expected 5005 slot simulates cancel/main winning ES_Distance priority.
      self.assertEqual(i.button_for(cc, _CS(60), 5010), icbm.BUTTON_NONE)
      self.assertEqual(i.hold_button, icbm.BUTTON_NONE)
    finally:
      icbm.FINE_STEP_ENABLED = True
      icbm.FINE_HOLD_SLOTS = 19

  def test_driver_press_abandons_hold_and_starts_cooldown(self):
    icbm.FINE_STEP_ENABLED = True
    icbm.FINE_HOLD_SLOTS = 4
    try:
      i = _iface()
      cc = _CCSP(_SendButtonState.decrease, 58, 60)
      i.button_for(cc, _CS(60), 5000)
      self.assertEqual(i.button_for(cc, _CS(60, cruise_button=4), 5005), icbm.BUTTON_NONE)
      self.assertEqual(i.hold_button, icbm.BUTTON_NONE)
      self.assertEqual(i.button_for(cc, _CS(60), 5010), icbm.BUTTON_NONE)
    finally:
      icbm.FINE_STEP_ENABLED = True
      icbm.FINE_HOLD_SLOTS = 19

  def test_observed_one_mph_change_releases_early(self):
    i = _iface()
    cc = _CCSP(_SendButtonState.decrease, 58, 60)
    self.assertEqual(i.button_for(cc, _CS(60), 5000), icbm.BUTTON_SET_COARSE)
    self.assertEqual(i.button_for(_CCSP(_SendButtonState.decrease, 58, 59), _CS(59), 5005),
                     icbm.BUTTON_NONE)
    self.assertEqual(i.hold_button, icbm.BUTTON_NONE)


if __name__ == "__main__":
  unittest.main(verbosity=2)
