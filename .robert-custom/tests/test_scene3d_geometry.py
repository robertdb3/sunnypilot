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

# honour $SUNNYPILOT the way the render harness does; the working clone is gitignored
REPO = os.environ.get("SUNNYPILOT",
                      os.path.join(os.path.dirname(__file__), "..", "sunnypilot"))
sys.path.insert(0, REPO)

from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d import geometry as geo  # noqa: E402
from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d import theme  # noqa: E402

# `smoothing` and `dimensions` arrive with patch 0011, which is currently NOT applied by
# apply_candidate.sh -- it was pulled back out after the capnp slice crash of 2026-08-18
# (symptom 14). Skip rather than fail so the rest of this file still guards the coordinate
# conversion, and so these tests come back automatically when 0011 is re-applied.
try:  # noqa: E402
  from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d import smoothing as sm  # noqa: E402
  from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d import dimensions as dim  # noqa: E402
  HAVE_0011 = True
except ImportError:  # pragma: no cover - depends on whether 0011 is in the applied set
  sm = dim = None
  HAVE_0011 = False

# 0011 also adds grid helpers to geometry.py; the module imports either way, so probe an attribute.
HAVE_0011 = HAVE_0011 and hasattr(geo, "resample_to_grid")

requires_0011 = unittest.skipUnless(HAVE_0011, "patch 0011 is not in the applied set")



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




@requires_0011
class TestGrid(unittest.TestCase):
  """The render grid is the contract every other piece depends on."""

  def test_grids_are_increasing(self):
    for g in (geo.GRID_S, geo.PATH_GRID_S, geo.DASH_S):
      self.assertTrue(np.all(np.diff(g) > 0), "grid must be strictly increasing")

  def test_grid_starts_behind_and_reaches_max(self):
    self.assertLess(float(geo.GRID_S[0]), 0.0, "grid must extend behind the car")
    self.assertAlmostEqual(float(geo.GRID_S[-1]), 120.0, places=3)

  def test_dash_step_divides_the_dash_length(self):
    """Dash boundaries must land on samples or the triangle count wobbles frame to frame."""
    self.assertAlmostEqual(3.0 % geo.DASH_STEP, 0.0, places=6)

  def test_night_never_fades_later_than_day(self):
    """Headlights do not reach 120 m; encoding that is the point of the night palette."""
    day = geo.distance_fade(geo.GRID_S, 55.0, 120.0)
    night = geo.distance_fade(geo.GRID_S, 38.0, 85.0)
    self.assertTrue(np.all(night <= day + 1e-6))

  def test_fade_is_monotonic_and_bounded(self):
    f = geo.distance_fade(geo.GRID_S, 55.0, 120.0)
    self.assertTrue(np.all((f >= 0.0) & (f <= 1.0)))
    self.assertTrue(np.all(np.diff(f) <= 1e-6), "fade must never increase with distance")
    self.assertAlmostEqual(float(f[0]), 1.0, places=5)
    self.assertAlmostEqual(float(f[-1]), 0.0, places=5)


@requires_0011
class TestResample(unittest.TestCase):
  def test_returns_full_length(self):
    x = np.linspace(0.0, 150.0, 33, dtype=np.float32)
    y = np.sin(x / 40.0).astype(np.float32)
    gy, gz, n = geo.resample_to_grid(x, y, y * 0, geo.GRID_S)
    self.assertEqual(len(gy), len(geo.GRID_S))
    self.assertEqual(len(gz), len(geo.GRID_S))

  def test_exact_at_matching_nodes(self):
    x = np.array([0.0, 10.0, 20.0, 30.0], dtype=np.float32)
    y = np.array([0.0, 1.0, 2.0, 3.0], dtype=np.float32)
    grid = np.array([10.0, 20.0], dtype=np.float32)
    gy, _, _ = geo.resample_to_grid(x, y, y, grid)
    np.testing.assert_allclose(gy, [1.0, 2.0], atol=1e-5)

  def test_masks_beyond_reach_instead_of_flatlining(self):
    """At 6 m/s the path only reaches ~60 m; np.interp would clamp it into a bar to the horizon."""
    x = np.linspace(0.0, 60.0, 33, dtype=np.float32)
    y = np.zeros_like(x)
    _, _, n = geo.resample_to_grid(x, y, y, geo.GRID_S)
    self.assertLess(n, len(geo.GRID_S))
    self.assertLessEqual(float(geo.GRID_S[n - 1]), 60.0 + 1e-6)

  def test_extrapolates_behind_the_first_sample(self):
    x = np.array([0.0, 10.0], dtype=np.float32)
    y = np.array([0.0, 1.0], dtype=np.float32)
    grid = np.array([-10.0, 0.0], dtype=np.float32)
    gy, _, _ = geo.resample_to_grid(x, y, y, grid)
    self.assertAlmostEqual(float(gy[0]), -1.0, places=4)

  def test_survives_nonfinite_and_unsorted(self):
    x = np.array([0.0, 20.0, 10.0, np.nan], dtype=np.float32)
    y = np.array([0.0, 2.0, 1.0, 5.0], dtype=np.float32)
    gy, _, n = geo.resample_to_grid(x, y, y, geo.GRID_S)
    self.assertTrue(np.all(np.isfinite(gy)))

  def test_degenerate_input_is_safe(self):
    e = np.zeros(0, dtype=np.float32)
    gy, gz, n = geo.resample_to_grid(e, e, e, geo.GRID_S)
    self.assertEqual(n, 0)
    self.assertEqual(len(gy), len(geo.GRID_S))


@requires_0011
class TestGridSmoother(unittest.TestCase):
  """The anti-jitter filter. Lag here is the dangerous failure, not noise."""

  def _s(self):
    return sm.GridSmoother(sm.ALPHAS, sm.SNAP_DEV, sm.SNAP_MASK)

  def test_first_update_snaps(self):
    f = self._s()
    y = np.linspace(0.0, 1.0, len(geo.GRID_S)).astype(np.float32)
    np.testing.assert_allclose(f.update(y), y, atol=1e-6)

  def test_converges_to_a_constant(self):
    f = self._s()
    f.update(np.zeros(len(geo.GRID_S), dtype=np.float32))
    target = np.full(len(geo.GRID_S), 0.4, dtype=np.float32)
    for _ in range(200):
      out = f.update(target)
    np.testing.assert_allclose(out, target, atol=1e-3)

  def test_far_lags_more_than_near(self):
    """The whole point of the distance-adaptive alpha curve."""
    f = self._s()
    f.update(np.zeros(len(geo.GRID_S), dtype=np.float32))
    out = f.update(np.full(len(geo.GRID_S), 0.5, dtype=np.float32))
    near = int(np.argmin(np.abs(geo.GRID_S - 3.0)))
    far = int(np.argmin(np.abs(geo.GRID_S - 100.0)))
    self.assertGreater(float(out[near]), float(out[far]))

  def test_alphas_bounded_and_non_increasing(self):
    self.assertTrue(np.all((sm.ALPHAS > 0.0) & (sm.ALPHAS <= 1.0)))
    ahead = sm.ALPHAS[geo.GRID_S >= 0]
    self.assertTrue(np.all(np.diff(ahead) <= 1e-9), "must smooth harder with distance")

  def test_snaps_on_a_lane_change(self):
    """A 3.7 m re-anchor is a real manoeuvre and must never be lagged."""
    f = self._s()
    f.update(np.zeros(len(geo.GRID_S), dtype=np.float32))
    out = f.update(np.full(len(geo.GRID_S), 3.7, dtype=np.float32))
    np.testing.assert_allclose(out, 3.7, atol=1e-6)

  def test_does_not_snap_on_noise(self):
    f = self._s()
    rng = np.random.default_rng(3)
    f.update(np.zeros(len(geo.GRID_S), dtype=np.float32))
    for _ in range(200):
      out = f.update(rng.normal(0.0, 0.1, len(geo.GRID_S)).astype(np.float32))
      self.assertLess(float(np.max(np.abs(out))), sm.SNAP_DEV)

  def test_reset_snaps_again(self):
    f = self._s()
    f.update(np.zeros(len(geo.GRID_S), dtype=np.float32))
    f.reset()
    y = np.full(len(geo.GRID_S), 2.0, dtype=np.float32)
    np.testing.assert_allclose(f.update(y), y, atol=1e-6)

  def test_shape_change_snaps_without_raising(self):
    f = self._s()
    f.update(np.zeros(len(geo.GRID_S), dtype=np.float32))
    short = np.ones(4, dtype=np.float32)
    np.testing.assert_allclose(f.update(short), short, atol=1e-6)

  def test_does_not_alias_the_caller_array(self):
    """SceneState is mutable and dataclasses.replace is shallow; the filter must own its state."""
    f = self._s()
    y = np.zeros(len(geo.GRID_S), dtype=np.float32)
    f.update(y)
    out = f.update(y)
    out[0] = 999.0     # mutating the returned array must not corrupt the filter
    y[0] = -999.0      # nor must mutating the caller's input
    again = f.update(np.zeros(len(geo.GRID_S), dtype=np.float32))
    self.assertLess(abs(float(again[0])), 1.0)

  def test_non_finite_input_holds_last_good(self):
    f = self._s()
    good = np.full(len(geo.GRID_S), 0.5, dtype=np.float32)
    f.update(good)
    bad = good.copy()
    bad[3] = np.nan
    out = f.update(bad)
    self.assertTrue(np.all(np.isfinite(out)))


@requires_0011
class TestDashesOnGrid(unittest.TestCase):
  def _line(self):
    y = np.zeros(len(geo.DASH_S), dtype=np.float32)
    w = np.full(len(geo.DASH_S), 0.2, dtype=np.float32)
    return y, w

  def test_coverage_matches_duty_cycle(self):
    y, w = self._line()
    tris = geo.dashes_on_grid(y, y, w, 0.0, phase=0.0, dash=3.0, gap=6.0)
    full = geo.ribbon_varying(geo.DASH_S, y, y, w)
    ratio = len(tris) / len(full)
    self.assertAlmostEqual(ratio, 1.0 / 3.0, delta=0.12)

  def test_phase_shifts_without_changing_the_count(self):
    """A per-frame triangle-count wobble is itself a stutter."""
    y, w = self._line()
    counts = {len(geo.dashes_on_grid(y, y, w, 0.0, phase=p)) for p in (0.0, 1.5, 3.0, 4.5, 6.0)}
    self.assertLessEqual(max(counts) - min(counts), 2)

  def test_count_is_bounded(self):
    y, w = self._line()
    tris = geo.dashes_on_grid(y, y, w, 0.0)
    self.assertLessEqual(len(tris), 2 * len(geo.DASH_S))

  def test_truncating_shortens_output(self):
    y, w = self._line()
    long_ = geo.dashes_on_grid(y, y, w, 0.0)
    short = geo.dashes_on_grid(y, y, w, 0.0, n_valid=12)
    self.assertLess(len(short), len(long_))


@requires_0011
class TestCarDimensions(unittest.TestCase):
  """Published 2018 Outback figures, so a future edit cannot quietly make it a sedan again."""

  def test_matches_published_size(self):
    self.assertAlmostEqual(dim.BODY_L, 4.816, delta=0.03)
    self.assertAlmostEqual(dim.BODY_W, 1.839, delta=0.03)
    self.assertAlmostEqual(dim.overall_height(), 1.679, delta=0.03)
    self.assertAlmostEqual(dim.WHEELBASE, 2.746, delta=0.01)

  def test_wheels_sit_on_the_wheelbase(self):
    self.assertAlmostEqual(dim.WHEEL_Z * 2.0, dim.WHEELBASE, places=6)
    self.assertLess(dim.WHEEL_Z + dim.WHEEL_R, dim.BODY_L * 0.5)

  def test_greenhouse_is_a_wagon_not_a_sedan(self):
    front, rear = dim.cabin_bounds()
    tail, nose = dim.BODY_L * 0.5, -dim.BODY_L * 0.5
    self.assertLess(tail - rear, 0.5, "roof must run nearly to the tailgate")
    self.assertGreaterEqual(front - nose, 1.4, "a long hood must sit ahead of the windscreen")

  def test_rails_fit_within_the_body(self):
    self.assertLess(dim.RAIL_X + dim.RAIL_W * 0.5, dim.BODY_W * 0.5)

  def test_vertical_stack_is_contiguous(self):
    self.assertAlmostEqual(dim.SILL_Z1, dim.BODY_Z0, places=6)
    self.assertAlmostEqual(dim.BODY_Z1, dim.CABIN_Z0, places=6)
    self.assertAlmostEqual(dim.CABIN_Z1, dim.RAIL_Z0, places=6)


@requires_0011
class TestPaletteReadability(unittest.TestCase):
  """The chase camera only ever sees a car's rear, so that face decides legibility."""

  @staticmethod
  def _luma(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

  def test_key_light_favours_the_rear(self):
    self.assertGreater(dim.FACE_REAR, dim.FACE_FRONT)

  def test_face_multipliers_are_sane(self):
    for k in (dim.FACE_TOP, dim.FACE_FRONT, dim.FACE_REAR, dim.FACE_LEFT, dim.FACE_RIGHT):
      self.assertTrue(0.0 < k <= 1.0)

  def test_green_stays_visible_on_the_rear_face(self):
    """Guards against anyone 'correcting' the paint back toward a literal K4X chip."""
    for pal in (theme.DAY, theme.NIGHT):
      rear = [c * dim.FACE_REAR for c in pal.ego_body[:3]]
      self.assertGreater(self._luma(rear), 45.0, "ego body renders too dark to read")

  def test_body_is_actually_green(self):
    for pal in (theme.DAY, theme.NIGHT):
      r, g, b = pal.ego_body[:3]
      self.assertGreater(g, r, "green channel must dominate")
      self.assertGreater(g, b)

  def test_night_haze_sits_between_the_sky_stops(self):
    """Night haze lighter than the sky paints a grey band across the top of the scene."""
    lo = self._luma(theme.NIGHT.sky_top[:3])
    hi = self._luma(theme.NIGHT.sky_bottom[:3])
    self.assertTrue(lo <= self._luma(theme.NIGHT.haze[:3]) <= hi)

if __name__ == "__main__":
  unittest.main(verbosity=2)
