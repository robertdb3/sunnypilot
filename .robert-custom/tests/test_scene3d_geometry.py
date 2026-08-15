"""Geometry tests for the 3D driving scene.

Numpy only, no pyray and no cereal, so this runs anywhere with plain python3:

    python3 tests/test_scene3d_geometry.py

The coordinate conversion is the thing worth guarding. openpilot model output is x-forward /
y-right / z-down and raylib is y-up with -z forward; getting it wrong produces a scene that looks
completely plausible but is mirrored, which is exactly the kind of bug that survives eyeballing.
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sunnypilot"))

from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d import geometry as geo  # noqa: E402


class TestCarToWorld(unittest.TestCase):
  def test_straight_ahead_is_negative_z(self):
    w = geo.car_to_world(np.array([[10.0, 0.0, 0.0]], dtype=np.float32))[0]
    self.assertAlmostEqual(float(w[0]), 0.0, places=5)
    self.assertAlmostEqual(float(w[1]), 0.0, places=5)
    self.assertAlmostEqual(float(w[2]), -10.0, places=5)

  def test_model_left_is_world_negative_x(self):
    # model y is RIGHT, so 2 m left is y=-2 and must remain x=-2 on screen.
    w = geo.car_to_world(np.array([[0.0, -2.0, 0.0]], dtype=np.float32))[0]
    self.assertAlmostEqual(float(w[0]), -2.0, places=5)

  def test_model_up_is_world_up(self):
    # model z is DOWN, so 1.5 m up is z=-1.5.
    w = geo.car_to_world(np.array([[0.0, 0.0, -1.5]], dtype=np.float32))[0]
    self.assertAlmostEqual(float(w[1]), 1.5, places=5)

  def test_not_mirrored(self):
    """A point ahead-and-left must stay ahead-and-left, not flip sides."""
    w = geo.car_to_world(np.array([[20.0, -3.0, 0.0]], dtype=np.float32))[0]
    self.assertLess(float(w[2]), 0.0, "ahead must be -z")
    self.assertLess(float(w[0]), 0.0, "left must be -x")


class TestTrim(unittest.TestCase):
  def test_drops_points_beyond_max(self):
    x = np.arange(0.0, 200.0, 10.0, dtype=np.float32)
    y = np.zeros_like(x)
    tx, _, _ = geo.trim(x, y, y, 100.0)
    self.assertTrue(len(tx) > 0)
    self.assertLessEqual(float(tx.max()), 100.0)

  def test_drops_non_finite(self):
    x = np.array([0.0, 1.0, np.nan, 3.0], dtype=np.float32)
    y = np.array([0.0, 0.0, 0.0, np.inf], dtype=np.float32)
    tx, ty, _ = geo.trim(x, y, np.zeros(4, dtype=np.float32), 100.0)
    self.assertEqual(len(tx), 2)
    self.assertTrue(np.all(np.isfinite(tx)) and np.all(np.isfinite(ty)))

  def test_too_short_returns_empty(self):
    one = np.array([1.0], dtype=np.float32)
    tx, _, _ = geo.trim(one, one, one, 100.0)
    self.assertEqual(len(tx), 0)


class TestExtendBack(unittest.TestCase):
  def test_prepends_points_behind_the_car(self):
    x = np.array([0.0, 10.0, 20.0], dtype=np.float32)
    y = np.zeros_like(x)
    ex, _, _ = geo.extend_back(x, y, y, 30.0)
    self.assertLess(float(ex.min()), 0.0, "should extrapolate behind the car")
    self.assertEqual(float(ex.max()), 20.0, "must not change what is ahead")

  def test_follows_initial_heading(self):
    # heading drifts left by 1 m per 10 m: going backwards must drift right
    x = np.array([0.0, 10.0], dtype=np.float32)
    y = np.array([0.0, 1.0], dtype=np.float32)
    ex, ey, _ = geo.extend_back(x, y, np.zeros(2, dtype=np.float32), 10.0)
    self.assertAlmostEqual(float(ey[0]), -1.0, places=4)
    self.assertAlmostEqual(float(ex[0]), -10.0, places=4)

  def test_short_input_untouched(self):
    x = np.array([1.0], dtype=np.float32)
    ex, _, _ = geo.extend_back(x, x, x)
    self.assertEqual(len(ex), 1)


class TestRibbon(unittest.TestCase):
  def test_two_triangles_per_segment(self):
    x = np.arange(0.0, 50.0, 10.0, dtype=np.float32)
    y = np.zeros_like(x)
    tris = geo.ribbon(x, y, y, 1.0)
    self.assertEqual(tris.shape, ((len(x) - 1) * 2, 3, 3))

  def test_width_is_respected(self):
    x = np.arange(0.0, 50.0, 10.0, dtype=np.float32)
    y = np.zeros_like(x)
    tris = geo.ribbon(x, y, y, 3.0)
    spread = float(tris[..., 0].max() - tris[..., 0].min())
    self.assertAlmostEqual(spread, 3.0, places=3)

  def test_z_lift_raises_the_ribbon(self):
    x = np.arange(0.0, 30.0, 10.0, dtype=np.float32)
    y = np.zeros_like(x)
    tris = geo.ribbon(x, y, y, 1.0, z_lift=0.25)
    self.assertAlmostEqual(float(tris[..., 1].min()), 0.25, places=4)

  def test_empty_input_is_safe(self):
    e = np.zeros(0, dtype=np.float32)
    self.assertEqual(geo.ribbon(e, e, e, 1.0).shape, (0, 3, 3))


class _Lead:
  def __init__(self, prob, x, y, v=0.0):
    self.prob, self.x, self.y, self.v = prob, [x], [y], [v]


class TestLeadPositions(unittest.TestCase):
  def test_filters_low_probability(self):
    leads = [_Lead(0.9, 20.0, 0.0), _Lead(0.1, 40.0, 0.0)]
    self.assertEqual(len(geo.lead_positions(leads)), 1)

  def test_sorted_by_distance_and_capped(self):
    leads = [_Lead(0.9, 60.0, 0.0), _Lead(0.9, 20.0, 0.0), _Lead(0.9, 40.0, 0.0)]
    got = geo.lead_positions(leads, max_count=2)
    self.assertEqual([g[0] for g in got], [20.0, 40.0])

  def test_handles_none_and_empty(self):
    self.assertEqual(geo.lead_positions(None), [])
    self.assertEqual(geo.lead_positions([]), [])

  def test_speed_stays_with_its_lead_after_sorting(self):
    """Results are distance-sorted, so speed must travel in the tuple, not a parallel list."""
    leads = [_Lead(0.9, 60.0, 0.0, v=31.0), _Lead(0.9, 20.0, 0.0, v=12.0)]
    got = geo.lead_positions(leads)
    self.assertEqual([(g[0], g[3]) for g in got], [(20.0, 12.0), (60.0, 31.0)])


class TestRibbonVarying(unittest.TestCase):
  def test_taper_narrows_with_distance(self):
    x = np.arange(0.0, 60.0, 10.0, dtype=np.float32)
    y = np.zeros_like(x)
    widths = np.linspace(3.0, 1.0, len(x)).astype(np.float32)
    tris = geo.ribbon_varying(x, y, y, widths)
    near = float(tris[0][..., 0].max() - tris[0][..., 0].min())
    far = float(tris[-1][..., 0].max() - tris[-1][..., 0].min())
    self.assertGreater(near, far)

  def test_triangles_interleaved_two_per_segment(self):
    """Callers colour by segment via i // 2, which relies on this ordering."""
    x = np.arange(0.0, 40.0, 10.0, dtype=np.float32)
    y = np.zeros_like(x)
    tris = geo.ribbon_varying(x, y, y, np.full(len(x), 2.0, dtype=np.float32))
    self.assertEqual(len(tris), (len(x) - 1) * 2)
    # both triangles of segment 0 must sit at the same depth range as each other
    self.assertAlmostEqual(float(tris[0][..., 2].min()), float(tris[1][..., 2].min()), places=4)

  def test_empty_is_safe(self):
    e = np.zeros(0, dtype=np.float32)
    self.assertEqual(geo.ribbon_varying(e, e, e, e).shape, (0, 3, 3))


class TestDashes(unittest.TestCase):
  def test_produces_gaps(self):
    x = np.arange(0.0, 90.0, 1.0, dtype=np.float32)
    y = np.zeros_like(x)
    solid = geo.ribbon(x, y, y, 0.2)
    dashed = geo.dashes(x, y, y, 0.2, dash=3.0, gap=6.0)
    self.assertGreater(len(solid), len(dashed), "dashed must cover less than solid")
    self.assertGreater(len(dashed), 0)

  def test_short_line_is_safe(self):
    x = np.array([0.0], dtype=np.float32)
    self.assertEqual(geo.dashes(x, x, x, 0.2).shape, (0, 3, 3))


if __name__ == "__main__":
  unittest.main(verbosity=2)
