#!/usr/bin/env python3
"""
Unit tests for PreglobalMainCruise, the driver-intent tracker added to
opendbc/sunnypilot/car/subaru/main_cruise.py.

The module under test has no compiled dependencies on purpose, so this runs
on a laptop without building opendbc. Point SUNNYPILOT at your checkout:

    SUNNYPILOT=~/projects/sunnypilot-lat-toggle/sunnypilot python3 tests/test_preglobal_main_cruise.py
"""

import os
import sys
import unittest
from pathlib import Path

DEFAULT_CHECKOUT = Path(__file__).resolve().parent.parent / "sunnypilot"
CHECKOUT = Path(os.environ.get("SUNNYPILOT", DEFAULT_CHECKOUT)).expanduser()
sys.path.insert(0, str(CHECKOUT / "opendbc_repo"))

from opendbc.sunnypilot.car.subaru.main_cruise import (  # noqa: E402
  PreglobalMainCruise,
  MAIN_CRUISE_PRESS_TIMEOUT_FRAMES,
)


class Sim:
  """Minimal stand-in for the preglobal branch of SubaruCarController.update().

  Models the car as: a mocked main button press flips ACC main state after `latency` frames.
  """

  def __init__(self, available=False, ready=True, latency=5, legacy=False):
    self.main_cruise = PreglobalMainCruise()
    self.cruise_button_prev = 0
    self.frame = 0
    self.available = available
    self.ready = ready
    self.latency = latency
    self.legacy = legacy       # True reproduces the pre-fix carcontroller logic
    self.pending = []          # queued main toggles, by frame they land
    self.mocked_presses = 0

  def step(self, pcm_cancel_cmd=False, driver_button=0):
    # car applies any button press that has landed
    for at in [f for f in self.pending if f <= self.frame]:
      self.available = not self.available
      self.pending.remove(at)

    # driver's physical main press toggles main directly
    if driver_button == 1:
      self.available = not self.available

    self.main_cruise.update(self.frame, self.available, driver_button == 1)

    if self.frame % 5 == 0:
      mocked_main_press = False
      if driver_button == 1:
        cruise_button = driver_button
      elif pcm_cancel_cmd:
        cruise_button = 1
        mocked_main_press = True
      elif (not self.available and self.ready) if self.legacy else \
           self.main_cruise.should_restore(self.available, self.ready):
        cruise_button = 1
        mocked_main_press = True
      else:
        cruise_button = driver_button

      if cruise_button == 1 and self.cruise_button_prev == 1:
        cruise_button = 0
        mocked_main_press = False
      self.cruise_button_prev = cruise_button

      if mocked_main_press:
        self.main_cruise.mocked_press(self.frame)
        self.mocked_presses += 1
        self.pending.append(self.frame + self.latency)

    self.frame += 1

  def run(self, frames, **kwargs):
    for _ in range(frames):
      self.step(**kwargs)


class TestPreglobalMainCruise(unittest.TestCase):

  def test_turns_main_on_at_startup(self):
    """Unchanged behavior: main off at boot gets turned on for the driver."""
    sim = Sim(available=False)
    sim.run(100)
    self.assertTrue(sim.available)

  def test_does_not_turn_main_on_before_ready(self):
    sim = Sim(available=False, ready=False)
    sim.run(100)
    self.assertFalse(sim.available)
    self.assertEqual(sim.mocked_presses, 0)

  def test_legacy_logic_reproduces_the_bug(self):
    """Pre-fix behavior: a driver main press is undone within ~0.1s.

    cruiseState.available is what MADS keys lateral off, so this is the reported
    'lane guidance blips off and comes right back' symptom.
    """
    sim = Sim(available=False, legacy=True)
    sim.run(100)
    self.assertTrue(sim.available)

    sim.step(driver_button=1)
    self.assertFalse(sim.available)

    sim.run(20)                   # 0.2 seconds later
    self.assertTrue(sim.available, "expected pre-fix logic to turn ACC main back on")

  def test_driver_main_off_latches_off(self):
    """The fix: a driver main press stays off instead of being re-pressed."""
    sim = Sim(available=False)
    sim.run(100)
    self.assertTrue(sim.available)

    sim.step(driver_button=1)     # driver presses ACC main off
    self.assertFalse(sim.available)

    presses_before = sim.mocked_presses
    sim.run(500)                  # 5 seconds
    self.assertFalse(sim.available, "openpilot turned ACC main back on behind the driver")
    self.assertEqual(sim.mocked_presses, presses_before)

  def test_driver_press_wins_over_simultaneous_pcm_cancel(self):
    """The driver's OFF transition itself can make controls request PCM cancel."""
    sim = Sim(available=False)
    sim.run(100)
    sim.step(driver_button=1, pcm_cancel_cmd=True)
    sim.run(500)
    self.assertFalse(sim.available)

  def test_driver_intent_survives_delayed_availability_transition(self):
    mc = PreglobalMainCruise()
    mc.update(0, True)
    mc.update(1, True, driver_main_pressed=True)
    mc.update(2, True, driver_main_pressed=False)
    mc.update(5, False)
    self.assertTrue(mc.driver_turned_off)

  def test_driver_can_turn_main_back_on(self):
    sim = Sim(available=False)
    sim.run(100)
    sim.step(driver_button=1)
    sim.run(500)
    self.assertFalse(sim.available)

    sim.step(driver_button=1)     # driver presses ACC main on again
    self.assertTrue(sim.available)
    sim.run(500)
    self.assertTrue(sim.available)
    self.assertFalse(sim.main_cruise.driver_turned_off)

  def test_toggles_repeatedly(self):
    """Latching both directions over several cycles: this is the lateral toggle."""
    sim = Sim(available=False)
    sim.run(100)
    for _ in range(5):
      sim.step(driver_button=1)
      sim.run(300)
      self.assertFalse(sim.available)
      sim.step(driver_button=1)
      sim.run(300)
      self.assertTrue(sim.available)

  def test_openpilot_cancel_restores_main(self):
    """Unchanged behavior: openpilot's own cancel presses main off, then back on."""
    sim = Sim(available=False)
    sim.run(100)
    self.assertTrue(sim.available)

    sim.step(pcm_cancel_cmd=True)
    sim.run(300)
    self.assertTrue(sim.available, "ACC main was not restored after openpilot cancelled")
    self.assertFalse(sim.main_cruise.driver_turned_off)

  def test_driver_off_after_cancel_window_still_latches(self):
    """A driver press after openpilot's cancel has settled is still respected."""
    sim = Sim(available=False)
    sim.run(100)
    sim.step(pcm_cancel_cmd=True)
    sim.run(300)
    self.assertTrue(sim.available)

    sim.step(driver_button=1)
    sim.run(500)
    self.assertFalse(sim.available)

  def test_in_flight_press_claims_the_transition(self):
    mc = PreglobalMainCruise()
    mc.update(0, True)
    mc.mocked_press(0)
    mc.update(1, False)
    self.assertFalse(mc.driver_turned_off)
    self.assertIsNone(mc.pending_press_frame, "landed press should be consumed")

  def test_press_times_out(self):
    """If a mocked press never lands, a later main off is the driver's."""
    mc = PreglobalMainCruise()
    mc.update(0, True)
    mc.mocked_press(0)
    for f in range(1, MAIN_CRUISE_PRESS_TIMEOUT_FRAMES + 1):
      mc.update(f, True)                            # main never moved
    mc.update(MAIN_CRUISE_PRESS_TIMEOUT_FRAMES + 1, False)
    self.assertTrue(mc.driver_turned_off)

  def test_one_press_is_consumed_once(self):
    """A single mocked press does not excuse a second, later main off."""
    mc = PreglobalMainCruise()
    mc.update(0, True)
    mc.mocked_press(0)
    mc.update(1, False)                             # our press landed
    mc.update(2, True)                              # driver turns main back on
    mc.update(3, False)                             # driver turns it off again
    self.assertTrue(mc.driver_turned_off)


if __name__ == "__main__":
  unittest.main(verbosity=2)
