#!/usr/bin/env python3
"""Device-only checks against a REAL modelV2 message.

    PYTHONPATH=/data/openpilot /usr/local/venv/bin/python -m unittest test_device_real_modelv2 -v

Skips everywhere else, so `validate_candidate.sh` stays green on a laptop and in CI. That is not a
convenience -- it is the whole point. Trap 30 says a change that reads a cereal message must be
tested against a real reader, and symptom 14 established that **no off-device environment can build
one**: the prebuilt `msgq` extension is aarch64-Linux, so importing `cereal` fails on a laptop and
on an x86 CI runner alike. This file is where that obligation is actually discharged, and it can
only be discharged on the comma.

Two independent risks, one class each:

* `TestCapnpAccess` -- the capnp boundary. Patch 0011 shipped `model.laneLines[:4]`, which passed
  166 offline tests and crash-looped the UI on the first onroad frame, because a
  `_DynamicListReader` indexes but does not slice.
* `TestRealGeometry` -- the numpy code past that boundary. Every offline test feeds it synthetic
  shapes; this pushes the shipped functions over thousands of consecutive real frames, which is the
  only way the cross-frame smoothing logic gets exercised on real data.

Read-only: opens stored rlogs, publishes nothing, opens no window, touches no params.
"""

import glob
import os
import re
import unittest

REALDATA = "/data/media/0/realdata"


def _on_device() -> bool:
  if not os.path.isdir(REALDATA):
    return False
  try:
    import openpilot.tools.lib.logreader  # noqa: F401
    import numpy  # noqa: F401
  except Exception:
    return False
  return True


requires_device = unittest.skipUnless(
  _on_device(), "device-only: needs stored routes and a working cereal/msgq import")


def _segment_key(path: str):
  name = os.path.basename(os.path.dirname(path))
  m = re.search(r"--(\d+)$", name)
  return (name.rsplit("--", 1)[0], int(m.group(1)) if m else -1)


def _segments():
  return sorted(glob.glob(os.path.join(REALDATA, "*", "rlog.zst")), key=_segment_key)


def _first_model():
  """Newest-first, so we test against the current model generation's message shape."""
  from openpilot.tools.lib.logreader import _LogFileReader
  for fn in reversed(_segments()):
    try:
      for m in _LogFileReader(fn):
        if m.which() == "modelV2":
          return m.modelV2
    except Exception:
      continue
  return None


@requires_device
class TestCapnpAccess(unittest.TestCase):
  """The exact boundary that crash-looped the UI (symptom 14)."""

  @classmethod
  def setUpClass(cls):
    cls.model = _first_model()
    if cls.model is None:
      raise unittest.SkipTest("no modelV2 in any stored route")

  def test_lane_lines_is_a_reader_that_refuses_slicing(self):
    """Pins the defect itself. If this ever stops raising, the bug theory was wrong."""
    self.assertEqual(type(self.model.laneLines).__name__, "_DynamicListReader")
    with self.assertRaises(TypeError):
      _ = self.model.laneLines[:4]

  def test_other_model_lists_also_refuse_slicing(self):
    """Why the AST guard covers every model field, not just laneLines."""
    for name in ("roadEdges", "laneLineProbs", "leadsV3"):
      with self.subTest(field=name), self.assertRaises(TypeError):
        _ = getattr(self.model, name)[:2]

  def test_shipped_indexing_idiom_works(self):
    lanes = [self.model.laneLines[i] for i in range(min(4, len(self.model.laneLines)))]
    edges = [self.model.roadEdges[i] for i in range(min(2, len(self.model.roadEdges)))]
    self.assertEqual(len(lanes), 4)
    self.assertEqual(len(edges), 2)

  def test_the_other_capnp_accesses_in_the_same_block(self):
    """These were assumed safe by analogy with shipping code. Verify, do not assume."""
    import numpy as np
    self.assertEqual(np.asarray(self.model.laneLineProbs, dtype=np.float32).shape, (4,))
    for obj, label in ((self.model.position, "position"), (self.model.laneLines[0], "laneLines[0]")):
      for axis in ("x", "y", "z"):
        with self.subTest(obj=label, axis=axis):
          self.assertGreater(np.asarray(getattr(obj, axis), dtype=np.float32).size, 0)
    self.assertGreater(np.asarray(self.model.acceleration.x, dtype=np.float32).size, 0)


@requires_device
class TestRealGeometry(unittest.TestCase):
  """Patch 0011's resampling and smoothing over real consecutive frames.

  _grid_line is a staticmethod and the smoothers are plain objects, so this needs no GL context and
  no Scene3DRenderer instance.
  """

  MAX_FRAMES = 2000

  def test_real_frames_produce_finite_geometry(self):
    import numpy as np
    from openpilot.tools.lib.logreader import _LogFileReader
    try:
      from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d import geometry as geo
      from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d import smoothing as sm
      from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d.renderer import Scene3DRenderer, _xyz
    except ImportError as e:
      raise unittest.SkipTest(f"patch 0011 not applied in this tree: {e}")

    lane_y = [sm.GridSmoother(geo.GRID_S) for _ in range(4)]
    lane_z = [sm.GridSmoother(geo.GRID_S) for _ in range(4)]
    edge_y = [sm.GridSmoother(geo.GRID_S) for _ in range(2)]
    edge_z = [sm.GridSmoother(geo.GRID_S) for _ in range(2)]
    path_y, path_z = sm.GridSmoother(geo.PATH_GRID_S), sm.GridSmoother(geo.PATH_GRID_S)
    probs = sm.ScalarSmoother()

    frames = 0
    for fn in reversed(_segments()[-40:]):
      try:
        reader = _LogFileReader(fn)
      except Exception:
        continue
      for msg in reader:
        if msg.which() != "modelV2":
          continue
        model = msg.modelV2
        for i in range(min(4, len(model.laneLines))):
          y, z, n = Scene3DRenderer._grid_line(_xyz(model.laneLines[i]), geo.GRID_S, lane_y[i], lane_z[i])
          self.assertTrue(np.all(np.isfinite(y)) and np.all(np.isfinite(z)), f"laneLine {i} frame {frames}")
          self.assertEqual(y.shape, z.shape)
        for i in range(min(2, len(model.roadEdges))):
          y, z, _ = Scene3DRenderer._grid_line(_xyz(model.roadEdges[i]), geo.GRID_S, edge_y[i], edge_z[i])
          self.assertTrue(np.all(np.isfinite(y)) and np.all(np.isfinite(z)), f"roadEdge {i} frame {frames}")
        py, pz, pn = Scene3DRenderer._grid_line(_xyz(model.position), geo.PATH_GRID_S, path_y, path_z)
        self.assertTrue(np.all(np.isfinite(py)) and np.all(np.isfinite(pz)), f"path frame {frames}")
        self.assertTrue(np.all(np.isfinite(probs.update(
          np.asarray(model.laneLineProbs, dtype=np.float32)))), f"probs frame {frames}")
        # the camera-lean read in render(), which slices the grid by a computed valid count
        if pn > 1:
          float(np.interp(30.0, geo.PATH_GRID_S[:pn], py[:pn]))
        frames += 1
        if frames >= self.MAX_FRAMES:
          break
      if frames >= self.MAX_FRAMES:
        break

    self.assertGreater(frames, 100, "too few real frames to prove anything")


if __name__ == "__main__":
  unittest.main(verbosity=2)
