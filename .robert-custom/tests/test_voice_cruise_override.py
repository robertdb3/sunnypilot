"""Tests for the schema-free selfdrived side of confirmed cruise targets."""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent / "sunnypilot"
VOICE_OVERRIDE = REPO / "openpilot/sunnypilot/selfdrive/car/intelligent_cruise_button_management/voice_override.py"
if not VOICE_OVERRIDE.is_file():
  raise unittest.SkipTest("speed command stage is not active")
spec = importlib.util.spec_from_file_location(
  "voice_override", VOICE_OVERRIDE)
voice = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = voice
spec.loader.exec_module(voice)


class Clock:
  def __init__(self):
    self.now = 100.0

  def __call__(self):
    return self.now


class FakeSocket:
  def __init__(self):
    self.messages = []
    self.sent = []

  def queue(self, **message):
    self.messages.append(json.dumps(message).encode())

  def recv(self, size):
    if not self.messages:
      raise BlockingIOError
    return self.messages.pop(0)

  def sendto(self, payload, path):
    self.sent.append((json.loads(payload), path))


class TestVoiceCruiseOverride(unittest.TestCase):
  def setUp(self):
    self.clock = Clock()
    self.override = voice.VoiceCruiseOverride(bind_socket=False, clock=self.clock)
    self.override.sock = FakeSocket()

  def update(self, current=55, ready=True, **kwargs):
    return self.override.update(current, 20, 90, ready, True, True, True, **kwargs)

  def set_target(self, target=60, current=55, command_id="abc"):
    self.override.sock.queue(type="set_target", id=command_id, target_mph=target, current_mph=current)

  def test_accepts_valid_target_and_holds_when_reached(self):
    self.set_target()
    self.assertEqual(self.update(), 60)
    self.assertEqual(self.update(current=60), 60)
    self.assertEqual(self.override.outcome, "holding")

  def test_physical_button_clears_immediately(self):
    self.set_target()
    self.update()
    self.override.sock.queue(type="physical_button")
    self.assertIsNone(self.update())
    self.assertEqual(self.override.reason, "physical cruise button")

  def test_disengagement_and_override_clear(self):
    self.set_target()
    self.update()
    self.assertIsNone(self.update(ready=False))
    self.set_target()
    self.update()
    self.assertIsNone(self.update(driver_override=True))

  def test_resume_assist_clears(self):
    self.set_target()
    self.update()
    self.override.sock.queue(type="resume_assist")
    self.assertIsNone(self.update())

  def test_no_progress_and_overall_timeouts(self):
    self.set_target()
    self.update()
    self.clock.now += voice.PROGRESS_TIMEOUT + 0.1
    self.assertIsNone(self.update())
    self.assertEqual(self.override.reason, "no cluster progress")

  def test_cluster_progress_extends_only_progress_deadline(self):
    self.set_target(target=70)
    self.update()
    self.clock.now += 2.0
    self.assertEqual(self.update(current=60), 70)
    self.clock.now += 2.0
    self.assertEqual(self.update(current=65), 70)
    self.clock.now += voice.COMMAND_TIMEOUT
    self.assertIsNone(self.update(current=65))

  def test_more_than_four_synthetic_taps_aborts(self):
    self.set_target(target=75)
    self.update()
    for _ in range(5):
      self.override.sock.queue(type="synthetic_tap")
    self.assertIsNone(self.update())
    self.assertEqual(self.override.reason, "synthetic tap limit")

  def test_rejects_changed_or_non_five_target(self):
    self.set_target(target=58)
    self.assertIsNone(self.update())
    self.assertEqual(self.override.outcome, "rejected")
    self.set_target(target=60, current=50)
    self.assertIsNone(self.update(current=55))


if __name__ == "__main__":
  unittest.main(verbosity=2)
