#!/usr/bin/env python3
"""micd/soundd must outlast the boot-time window where the audio device can't be opened.

    SUNNYPILOT=<built tree> python tests/test_audio_stream_retry.py

On the first drive after a boot both crashed with `get_stream failed after retry` (10 x 3s) and
selfdrived blocked engagement for the whole drive. See runbook symptom 17. Each failed open also logs
a snapshot of every audio layer (0018), because PortAudio's own error doesn't say which one refused.
That showed /dev/snd stays root-only until udev gets past AGNOS's slow 99-gpio.rules (~40-50s), so
launch_chffrplus.sh now sets the sound nodes' group itself, like it already does for the GPU (0019).
"""
import ast
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
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


class TestAudioOpenDiagnostics(unittest.TestCase):
  KEYS = {"uptime", "dmesg_audio", "groups", "cards", "card0_state", "node_controlC0", "alsa_default", "alsa_hw:0,0",
          "portaudio_devices", "portaudio_default", "dev_snd_holders"}

  def setUp(self):
    self.micd = MICD
    self.micd.cloudlog.reset_mock()
    patcher = mock.patch.object(UTILS.time, "sleep")
    patcher.start()
    self.addCleanup(patcher.stop)
    self.mic = self.micd.Mic.__new__(self.micd.Mic)
    self.mic.callback = None

  def test_every_failed_open_logs_a_snapshot(self):
    # FlakySoundDevice has no query_devices and this host has no sound card, so most probes fail.
    sd = FlakySoundDevice(failures=3)
    self.assertEqual(self.mic.get_stream(sd), "stream")
    events = self.micd.cloudlog.event.call_args_list
    self.assertEqual(len(events), 3)
    for call in events:
      self.assertEqual(call.args, ("micd audio open diagnostics",))
      self.assertTrue(call.kwargs["error"])
      self.assertLessEqual(self.KEYS | {"node_pcmC0D0c"}, set(call.kwargs))

  def test_a_failing_probe_is_recorded_not_raised(self):
    with mock.patch.object(self.micd.os, "getgroups", side_effect=PermissionError("nope")):
      diag = self.micd.audio_open_diagnostics(FlakySoundDevice(0), capture=True)
    self.assertEqual(diag["groups"], "PermissionError: nope")

  def test_only_probes_its_own_direction(self):
    # Opening the other direction's PCM could collide with the other daemon's retry.
    capture = self.micd.audio_open_diagnostics(FlakySoundDevice(0), capture=True)
    playback = self.micd.audio_open_diagnostics(FlakySoundDevice(0), capture=False)
    self.assertIn("node_pcmC0D0c", capture)
    self.assertNotIn("node_pcmC0D0p", capture)
    self.assertIn("node_pcmC0D0p", playback)
    self.assertNotIn("node_pcmC0D0c", playback)

  def test_dmesg_collapses_repeated_open_noise(self):
    out = "\n".join([
      "[    9.373120] sdm845-asoc-snd soc:sound-tavil: Sound card sdm845-tavil-snd-card registered",
      "[   40.005296] R0: [cds_mc_thread] wlan: [38919:I :HDD] hdd_local_unsafe_channel_updated",
      "[   50.000001] __q6asm_cmd: DSP returned error[ADSP_EFAILED] opcode 68557",
      "[   50.000002] usb 1-1: new device",
      "[   53.000001] __q6asm_cmd: DSP returned error[ADSP_EFAILED] opcode 68557",
    ])
    with mock.patch.object(self.micd.subprocess, "run", return_value=types.SimpleNamespace(stdout=out)):
      lines = self.micd._dmesg_audio()
    self.assertEqual(lines, [
      "[    9.373120] sdm845-asoc-snd soc:sound-tavil: Sound card sdm845-tavil-snd-card registered",
      "[   53.000001] __q6asm_cmd: DSP returned error[ADSP_EFAILED] opcode 68557 (x2)",
    ])


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
    self.assertIn("cloudlog.event", calls)
    self.assertIn("micd.audio_open_diagnostics", calls)
    self.assertIsInstance(handlers[0].body[-1], ast.Raise)

  def test_diagnostics_probe_playback(self):
    diag = next(c for c in ast.walk(self.fn) if isinstance(c, ast.Call) and ast.unparse(c.func) == "micd.audio_open_diagnostics")
    self.assertEqual({k.arg: ast.unparse(k.value) for k in diag.keywords}, {"capture": "False"})


class TestLaunchFixesSndPermissions(unittest.TestCase):
  """Runs the real agnos_init lines against a temp dir, with sudo and sleep stubbed."""
  def setUp(self):
    self.launch = (ROOT.parent / "launch_chffrplus.sh").read_text()
    m = re.search(r"\n( *# Same for sound:.*?-exec chmod 660 \{\} \+)\n", self.launch, re.DOTALL)
    self.assertIsNotNone(m, "sound permission block missing from launch_chffrplus.sh")
    self.block = m.group(1)

  def run_block(self, with_card):
    with tempfile.TemporaryDirectory() as d:
      snd = Path(d, "snd")
      snd.mkdir()
      if with_card:
        (snd / "controlC0").touch()
      log = Path(d, "log")
      script = (f'sleep() {{ echo sleep >> "{log}"; }}\n'
                f'sudo() {{ echo "sudo $*" >> "{log}"; }}\n' + self.block.replace("/dev/snd", str(snd)))
      subprocess.run(["bash", "-c", script], check=True)
      return [l.replace(str(snd), "/dev/snd") for l in log.read_text().splitlines()] if log.exists() else []

  def test_inside_agnos_init_after_gpu_fix(self):
    body = self.launch[self.launch.index("function agnos_init"):self.launch.index("# Check if AGNOS update is required")]
    self.assertIn(self.block, body)
    self.assertLess(body.index("chgrp gpu"), body.index("# Same for sound:"))

  def test_sets_group_and_mode_on_char_nodes_only(self):
    # by-path is a directory; 660 on it would make it untraversable.
    self.assertEqual(self.run_block(with_card=True),
                     ["sudo find /dev/snd -maxdepth 1 -type c -exec chgrp audio {} + -exec chmod 660 {} +"])

  def test_waits_at_most_ten_seconds_for_the_card(self):
    log = self.run_block(with_card=False)
    self.assertEqual(log.count("sleep"), 100)
    self.assertEqual(log[-1][:9], "sudo find")

  def test_script_parses(self):
    subprocess.run(["bash", "-n", str(ROOT.parent / "launch_chffrplus.sh")], check=True)


if __name__ == "__main__":
  unittest.main(verbosity=2)
