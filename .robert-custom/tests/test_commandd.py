"""Focused tests for the loopback command service and fail-closed speed handshake."""
from __future__ import annotations

import http.client
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import threading
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent / "sunnypilot"
COMMANDD = REPO / "openpilot/sunnypilot/system/commandd.py"
if not COMMANDD.is_file():
  raise unittest.SkipTest("visual command stage is not active")


class FakeParams:
  def __init__(self):
    self.values = {"OnroadScreenOffBrightness": "0", "Scene3D": False, "MapPanel": False}

  def put(self, key, value):
    self.values[key] = value

  def put_bool(self, key, value):
    self.values[key] = bool(value)

  def get(self, key, return_default=False):
    return self.values.get(key, "0")

  def get_bool(self, key):
    return bool(self.values.get(key, False))


params_module = types.ModuleType("openpilot.common.params")
params_module.Params = FakeParams
sys.modules["openpilot.common.params"] = params_module

spec = importlib.util.spec_from_file_location(
  "custompilot_commandd", COMMANDD)
commandd = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = commandd
spec.loader.exec_module(commandd)


class Clock:
  def __init__(self):
    self.now = 100.0

  def __call__(self):
    return self.now


def telemetry(**updates):
  base = {
    "ready": True, "icbm_enabled": True, "acc_engaged": True, "imperial": True,
    "driver_override": False, "button_active": False, "current_mph": 55,
    "minimum_mph": 20, "maximum_mph": 90,
  }
  base.update(updates)
  return base


class TestConfiguration(unittest.TestCase):
  def test_requires_mode_0600_and_complete_values(self):
    with tempfile.TemporaryDirectory() as tmp:
      config_path = Path(tmp) / "commands.json"
      token_path = Path(tmp) / "token"
      config_path.write_text(json.dumps({"allowed_login": "owner@example.com",
                                         "visual_enabled": True, "speed_enabled": False}))
      token_path.write_text("x" * 44)
      os.chmod(config_path, 0o600)
      os.chmod(token_path, 0o600)
      config = commandd.Config.load(config_path, token_path)
      self.assertEqual(config.allowed_login, "owner@example.com")
      os.chmod(token_path, 0o644)
      with self.assertRaises(ValueError):
        commandd.Config.load(config_path, token_path)

  def test_audit_contains_only_normalized_fields(self):
    output = io.StringIO()
    with redirect_stdout(output):
      commandd.audit("speed_prepare", "rejected", 65, "target outside bounds")
    event = output.getvalue()
    self.assertIn('"target_mph":65', event)
    self.assertNotIn("Bearer", event)
    self.assertNotIn("confirmation_token", event)


class TestVisualMappings(unittest.TestCase):
  def setUp(self):
    self.params = FakeParams()
    self.visual = commandd.VisualController(self.params)

  def test_all_brightness_modes_and_steps(self):
    for requested, stored in (("auto", "0"), ("auto-dark", "1"), ("screen-off", "2"),
                              (5, "3"), (50, "12"), (100, "22")):
      self.visual.apply({"brightness": requested})
      self.assertEqual(self.params.values["OnroadScreenOffBrightness"], stored)

  def test_rejects_non_step_brightness(self):
    with self.assertRaises(commandd.CommandError):
      self.visual.apply({"brightness": 47})

  def test_view_and_map_are_independent(self):
    self.visual.apply({"view": "3d"})
    self.visual.apply({"map": "on"})
    self.assertTrue(self.params.values["Scene3D"])
    self.assertTrue(self.params.values["MapPanel"])
    self.visual.apply({"view": "camera"})
    self.assertTrue(self.params.values["MapPanel"])


class TestSpeedHandshake(unittest.TestCase):
  def setUp(self):
    self.clock = Clock()
    self.speed = commandd.SpeedCoordinator(bind_socket=False, clock=self.clock)
    self.speed.inject_telemetry(telemetry())

  def test_prepare_confirm_is_single_use(self):
    prepared = self.speed.prepare(65)
    confirmed = self.speed.confirm(prepared["confirmation_token"])
    self.assertEqual(confirmed["outcome"], "pending")
    with self.assertRaises(commandd.CommandError):
      self.speed.confirm(prepared["confirmation_token"])

  def test_token_expires(self):
    token = self.speed.prepare(60)["confirmation_token"]
    self.clock.now += commandd.CONFIRM_TTL + 0.1
    with self.assertRaisesRegex(commandd.CommandError, "expired"):
      self.speed.confirm(token)

  def test_rejects_non_five_target_and_large_delta(self):
    with self.assertRaisesRegex(commandd.CommandError, "ending"):
      self.speed.prepare(58)
    with self.assertRaisesRegex(commandd.CommandError, "20 mph"):
      self.speed.prepare(80)

  def test_rejects_bounds_and_unsafe_state(self):
    with self.assertRaisesRegex(commandd.CommandError, "bounds"):
      self.speed.prepare(95)
    for key in ("ready", "icbm_enabled", "acc_engaged", "imperial"):
      self.speed.inject_telemetry(telemetry(**{key: False}))
      with self.assertRaises(commandd.CommandError):
        self.speed.prepare(60)

  def test_confirmation_rechecks_cluster_and_readiness(self):
    token = self.speed.prepare(60)["confirmation_token"]
    self.speed.inject_telemetry(telemetry(current_mph=56))
    with self.assertRaisesRegex(commandd.CommandError, "changed"):
      self.speed.confirm(token)

  def test_stale_telemetry_fails_closed(self):
    self.clock.now += commandd.TELEMETRY_MAX_AGE + 0.1
    with self.assertRaisesRegex(commandd.CommandError, "stale"):
      self.speed.prepare(60)


class TestHTTPBoundary(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.token = "t" * 44
    config = commandd.Config("owner@example.com", True, False, cls.token)
    app = commandd.CommandApplication(commandd.VisualController(FakeParams()),
                                      commandd.SpeedCoordinator(bind_socket=False), lambda: config)
    cls.server = commandd.Server(("127.0.0.1", 0), commandd.Handler, app)
    threading.Thread(target=cls.server.serve_forever, daemon=True).start()
    cls.port = cls.server.server_address[1]

  @classmethod
  def tearDownClass(cls):
    cls.server.shutdown()
    cls.server.server_close()

  def request(self, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    payload = json.loads(response.read())
    conn.close()
    return response.status, payload

  @property
  def auth(self):
    return {"Tailscale-User-Login": "owner@example.com", "Authorization": f"Bearer {self.token}"}

  def test_missing_and_spoofed_identity_are_rejected(self):
    status, _ = self.request("GET", "/v1/status")
    self.assertEqual(status, 401)
    headers = {**self.auth, "Tailscale-User-Login": "attacker@example.com"}
    status, _ = self.request("GET", "/v1/status", headers=headers)
    self.assertEqual(status, 401)

  def test_wrong_bearer_is_rejected(self):
    headers = {**self.auth, "Authorization": "Bearer wrong"}
    status, _ = self.request("GET", "/v1/status", headers=headers)
    self.assertEqual(status, 401)

  def test_malformed_json_changes_nothing(self):
    params = self.server.app.visual.params
    before = dict(params.values)
    headers = {**self.auth, "Content-Type": "application/json"}
    status, _ = self.request("POST", "/v1/visual", body="{bad", headers=headers)
    self.assertEqual(status, 400)
    self.assertEqual(params.values, before)


if __name__ == "__main__":
  unittest.main(verbosity=2)
