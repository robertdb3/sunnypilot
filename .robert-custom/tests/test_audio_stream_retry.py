#!/usr/bin/env python3
"""micd/soundd must outlast the boot-time window where the audio device can't be opened.

    SUNNYPILOT=<built tree> python tests/test_audio_stream_retry.py

On the first drive after a boot both crashed with `get_stream failed after retry` (10 x 3s) and
selfdrived blocked engagement for the whole drive. See runbook symptom 17.
"""
import ast
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(os.environ.get("SUNNYPILOT", Path(__file__).resolve().parent.parent / "sunnypilot")) / "openpilot"


def load(name: str, path: Path):
  spec = importlib.util.spec_from_file_location(name, path)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def load_micd():
  # patch.dict restores sys.modules on exit, which would also unload a first-time numpy import
  # and break every later test in the same discover run.
  import numpy  # noqa: F401
  # Only retry() is needed from common.utils; its zstandard import is irrelevant here.
  with mock.patch.dict(sys.modules, {"zstandard": sys.modules.get("zstandard", types.ModuleType("zstandard"))}):
    utils = load("utils_under_test", ROOT / "common/utils.py")
  stubs = {
    "openpilot.cereal": types.SimpleNamespace(messaging=None),
    "openpilot.common.realtime": types.SimpleNamespace(Ratekeeper=None),
    "openpilot.common.utils": utils,
    "openpilot.common.swaglog": types.SimpleNamespace(cloudlog=mock.MagicMock()),
  }
  with mock.patch.dict(sys.modules, stubs):
    micd = load("micd_under_test", ROOT / "system/micd.py")
  return micd, utils


MICD, UTILS = load_micd()


class FlakySoundDevice:
  """Raises like PortAudio for the first `failures` opens."""
  def __init__(self, failures):
    self.failures = failures
    self.opens = 0

  def _terminate(self):
    pass

  def _initialize(self):
    pass

  def InputStream(self, **kwargs):
    self.opens += 1
    if self.opens <= self.failures:
      raise RuntimeError("Error opening InputStream: Device unavailable [PaErrorCode -9985]")
    return "stream"


class TestMicdStreamRetry(unittest.TestCase):
  def setUp(self):
    self.micd = MICD
    self.micd.cloudlog.reset_mock()
    patcher = mock.patch.object(UTILS.time, "sleep")
    patcher.start()
    self.addCleanup(patcher.stop)
    self.mic = self.micd.Mic.__new__(self.micd.Mic)
    self.mic.callback = None

  def test_window_covers_the_observed_boot_delay(self):
    # Observed: unopenable from onroad start until ~30s later. Keep a wide margin.
    self.assertGreaterEqual(self.micd.STREAM_OPEN_ATTEMPTS * self.micd.STREAM_OPEN_DELAY, 90)

  def test_survives_more_failures_than_the_old_ten_attempts(self):
    sd = FlakySoundDevice(failures=15)
    self.assertEqual(self.mic.get_stream(sd), "stream")
    self.assertEqual(sd.opens, 16)

  def test_each_failure_is_logged_with_its_cause(self):
    sd = FlakySoundDevice(failures=3)
    self.mic.get_stream(sd)
    self.assertEqual(self.micd.cloudlog.exception.call_count, 3)

  def test_still_gives_up_eventually(self):
    sd = FlakySoundDevice(failures=10_000)
    with self.assertRaisesRegex(Exception, "get_stream failed after retry"):
      self.mic.get_stream(sd)
    self.assertEqual(sd.opens, self.micd.STREAM_OPEN_ATTEMPTS)


class TestSounddUsesSharedWindow(unittest.TestCase):
  def setUp(self):
    tree = ast.parse((ROOT / "selfdrive/ui/soundd.py").read_text())
    self.fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "get_stream")

  def test_retry_uses_micd_constants(self):
    (dec,) = self.fn.decorator_list
    kwargs = {k.arg: ast.unparse(k.value) for k in dec.keywords}
    self.assertEqual(kwargs, {"attempts": "micd.STREAM_OPEN_ATTEMPTS", "delay": "micd.STREAM_OPEN_DELAY"})

  def test_failures_are_logged(self):
    handlers = [n for n in ast.walk(self.fn) if isinstance(n, ast.ExceptHandler)]
    self.assertEqual(len(handlers), 1)
    calls = {ast.unparse(c.func) for c in ast.walk(handlers[0]) if isinstance(c, ast.Call)}
    self.assertIn("cloudlog.exception", calls)
    self.assertIsInstance(handlers[0].body[-1], ast.Raise)


if __name__ == "__main__":
  unittest.main(verbosity=2)
