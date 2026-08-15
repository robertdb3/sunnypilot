"""Tests for the torque Self-Tune diagnostic.

    python3 tests/test_torque_status.py

Covers the parts with actual logic -- re-bucketing the cached points and detecting a value that
has run into a sanity bound. The rest of the script is formatting.
"""
import importlib.util
import os
import sys
import unittest

_spec = importlib.util.spec_from_file_location(
  "torque_status", os.path.join(os.path.dirname(__file__), "..", "tools", "torque_status.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

BOUNDS = ts.FALLBACK_BUCKETS
STRICT = ts.FALLBACK_STRICT
RELAXED = ts.FALLBACK_RELAXED


class TestBucketCounts(unittest.TestCase):
  def test_assigns_to_the_matching_band(self):
    pts = [[-0.45, 0.0], [-0.25, 0.0], [0.05, 0.0], [0.35, 0.0]]
    counts = ts.bucket_counts(pts, BOUNDS)
    self.assertEqual(counts[0], 1)   # -0.5..-0.3
    self.assertEqual(counts[1], 1)   # -0.3..-0.2
    self.assertEqual(counts[4], 1)   #  0.0..0.1
    self.assertEqual(counts[7], 1)   #  0.3..0.5
    self.assertEqual(sum(counts), 4)

  def test_bands_are_half_open_lower_inclusive(self):
    """add_point uses `x >= lo and x < hi`, and the first match wins."""
    self.assertEqual(ts.bucket_counts([[-0.3, 0.0]], BOUNDS)[1], 1)   # lands in -0.3..-0.2
    self.assertEqual(ts.bucket_counts([[0.0, 0.0]], BOUNDS)[4], 1)    # lands in 0.0..0.1

  def test_points_outside_every_band_are_dropped(self):
    self.assertEqual(sum(ts.bucket_counts([[-0.9, 0.0], [0.9, 0.0]], BOUNDS)), 0)

  def test_tolerates_empty_and_malformed(self):
    self.assertEqual(sum(ts.bucket_counts([], BOUNDS)), 0)
    self.assertEqual(sum(ts.bucket_counts([[]], BOUNDS)), 0)

  def test_counts_many(self):
    counts = ts.bucket_counts([[0.15, 0.0]] * 250, BOUNDS)
    self.assertEqual(counts[5], 250)


class TestStarvingBuckets(unittest.TestCase):
  def test_reports_only_the_short_ones(self):
    counts = list(STRICT)
    counts[0] = 0
    counts[7] = 5
    self.assertEqual(ts.starving_buckets(counts, STRICT), [0, 7])

  def test_none_when_all_met(self):
    self.assertEqual(ts.starving_buckets(list(STRICT), STRICT), [])

  def test_relaxed_forgives_the_outer_bands(self):
    """The realistic case: plenty of gentle steering, almost no hard cornering."""
    counts = [3, 300, 500, 500, 500, 500, 300, 2]
    self.assertEqual(ts.starving_buckets(counts, STRICT), [0, 7])
    self.assertEqual(ts.starving_buckets(counts, RELAXED), [])


class TestPinned(unittest.TestCase):
  def test_detects_the_high_bound(self):
    self.assertEqual(ts.pinned(2.6, 1.4, 2.6), "high")

  def test_detects_the_low_bound(self):
    self.assertEqual(ts.pinned(1.4, 1.4, 2.6), "low")

  def test_settled_in_the_middle_is_not_pinned(self):
    self.assertIsNone(ts.pinned(2.0, 1.4, 2.6))

  def test_degenerate_bounds_are_not_pinned(self):
    """Offline value of zero collapses the bounds; do not claim a pin from that."""
    self.assertIsNone(ts.pinned(0.0, 0.0, 0.0))

  def test_impreza_anchor_scenario(self):
    """A 2.5 offline factor with strict sanity caps learning at 3.25. A car that truly wants
    3.4 pins high, which is exactly the signal to turn on Less Restrict Settings."""
    offline, sanity = 2.5, 0.3
    lo, hi = (1 - sanity) * offline, (1 + sanity) * offline
    self.assertEqual(ts.pinned(hi, lo, hi), "high")
    relaxed_lo, relaxed_hi = (1 - 1.0) * offline, (1 + 1.0) * offline
    self.assertIsNone(ts.pinned(3.4, relaxed_lo, relaxed_hi))


class TestFallbackConstantsMatchSource(unittest.TestCase):
  """The script imports the real constants at runtime, but that import needs msgq's compiled
  extension and so only succeeds on the device. Off-device it falls back to copies. Parse the
  real values straight out of the source so the copies cannot silently drift.
  """

  SRC = os.path.join(os.path.dirname(__file__), "..", "sunnypilot")

  def _grab(self, path, name):
    import re
    with open(os.path.join(self.SRC, path)) as f:
      src = f.read()
    m = re.search(rf"^{name}\s*=\s*(?:np\.array\()?(\[.*?\])", src, re.M | re.S)
    self.assertIsNotNone(m, f"{name} not found in {path}")
    return eval(m.group(1))  # noqa: S307 - our own source, and only list literals

  def test_bucket_bounds(self):
    real = self._grab("openpilot/selfdrive/locationd/torqued.py", "STEER_BUCKET_BOUNDS")
    self.assertEqual([tuple(b) for b in real], [tuple(b) for b in ts.FALLBACK_BUCKETS])

  def test_strict_minimums(self):
    real = self._grab("openpilot/selfdrive/locationd/torqued.py", "MIN_BUCKET_POINTS")
    self.assertEqual(list(real), ts.FALLBACK_STRICT)

  def test_relaxed_minimums(self):
    real = self._grab("openpilot/sunnypilot/selfdrive/locationd/torqued_ext.py",
                      "RELAXED_MIN_BUCKET_POINTS")
    self.assertEqual(list(real), ts.FALLBACK_RELAXED)

  def test_subaru_still_absent_from_allowed_cars(self):
    """The whole premise: if upstream ever adds subaru, the Enforce-toggle workaround in
    notes/torque-tuning.md stops being necessary and the guide needs updating."""
    with open(os.path.join(self.SRC, "openpilot/selfdrive/locationd/torqued.py")) as f:
      src = f.read()
    import re
    allowed = eval(re.search(r"^ALLOWED_CARS\s*=\s*(\[.*?\])", src, re.M).group(1))  # noqa: S307
    self.assertNotIn("subaru", allowed,
                     "subaru is now in ALLOWED_CARS upstream - update notes/torque-tuning.md")


class TestBar(unittest.TestCase):
  def test_empty_and_full(self):
    self.assertEqual(ts.bar(0, 100, 10), "." * 10)
    self.assertEqual(ts.bar(100, 100, 10), "#" * 10)

  def test_clamps_past_full(self):
    self.assertEqual(ts.bar(1000, 100, 10), "#" * 10)

  def test_zero_minimum_is_safe(self):
    self.assertIn("n/a", ts.bar(5, 0, 10))


if __name__ == "__main__":
  unittest.main(verbosity=2)
