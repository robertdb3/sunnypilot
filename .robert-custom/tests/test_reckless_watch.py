"""Tests for the Virginia reckless-speed watch.

    python3 tests/test_reckless_watch.py

Pure logic, no cereal, no GPS, no screen.

Two things carry the weight here. The geofence has to be right about which side of a border you
are on -- that is the entire feature. And the alert must fire once and then shut up; "does not
keep annoying me" was an explicit requirement, so it gets the most tests.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sunnypilot"))

from openpilot.sunnypilot.selfdrive.reckless_watch import geofence  # noqa: E402
from openpilot.sunnypilot.selfdrive.reckless_watch.watcher import (  # noqa: E402
  RecklessWatch, Reason, STATUTE_ABSOLUTE_MPH, STATUTE_OVER_LIMIT_MPH, DEFAULT_THRESHOLD_MPH,
  REARM_MARGIN_MPH, MS_TO_MPH,
)

MPH = 1.0 / MS_TO_MPH
RICHMOND = (37.5634, -77.4714)          # representative point in central Richmond
GOOD_FIX = 5.0


def _settle(watch, lat, lon, n=10):
  """Feed enough fixes to get past the crossing debounce."""
  for _ in range(n):
    watch.update(lat, lon, GOOD_FIX, 0.0)


class TestGeofence(unittest.TestCase):
  INSIDE = [
    ("Richmond", *RICHMOND),
    ("Arlington (beside DC)", 38.8816, -77.0910),
    ("Virginia Beach", 36.8529, -75.9780),
    ("Abingdon, far southwest", 36.7098, -81.9773),
    ("Winchester, northern tip", 39.1857, -78.1633),
    ("Chincoteague, Eastern Shore", 37.9332, -75.3785),
  ]
  OUTSIDE = [
    ("Washington DC", 38.9072, -77.0369),
    ("Raleigh NC", 35.7796, -78.6382),
    ("Charleston WV", 38.3498, -81.6326),
    ("Ocean City MD, same peninsula", 38.3365, -75.0849),
    ("Kingsport TN", 36.5484, -82.5618),
    ("Atlantic Ocean", 36.5, -74.0),
    ("Los Angeles", 34.0522, -118.2437),
  ]

  def test_inside_points(self):
    for name, lat, lon in self.INSIDE:
      self.assertTrue(geofence.in_virginia(lat, lon), name)

  def test_outside_points(self):
    for name, lat, lon in self.OUTSIDE:
      self.assertFalse(geofence.in_virginia(lat, lon), name)

  def test_eastern_shore_is_separated_from_maryland(self):
    """The Eastern Shore is disjoint from the mainland and shares a peninsula with Maryland.
    A naive simplification merges them; this is the case that catches it."""
    self.assertTrue(geofence.in_virginia(37.9332, -75.3785), "Chincoteague VA")
    self.assertFalse(geofence.in_virginia(38.3365, -75.0849), "Ocean City MD")

  def test_bristol_state_line(self):
    """State Street IS the VA/TN border in Bristol, at about lat 36.596."""
    self.assertFalse(geofence.in_virginia(36.585, -82.1887), "Tennessee side")
    self.assertTrue(geofence.in_virginia(36.605, -82.1887), "Virginia side")

  def test_bbox_never_rejects_a_point_the_polygon_accepts(self):
    min_lon, min_lat, max_lon, max_lat = geofence.BBOX
    for name, lat, lon in self.INSIDE:
      self.assertTrue(min_lat <= lat <= max_lat and min_lon <= lon <= max_lon, name)


class TestStateTracker(unittest.TestCase):
  def test_requires_consecutive_fixes_before_crossing(self):
    t = geofence.StateTracker(confirm_fixes=5)
    for _ in range(10):
      t.update(*RICHMOND, GOOD_FIX)          # establish "inside"
    self.assertTrue(t.inside)

    dc = (38.9072, -77.0369)
    for i in range(4):
      self.assertFalse(t.update(*dc, GOOD_FIX))
      self.assertTrue(t.inside, f"flipped after only {i + 1} fixes")
    t.update(*dc, GOOD_FIX)
    self.assertFalse(t.inside)

  def test_a_single_stray_fix_does_not_flip(self):
    t = geofence.StateTracker(confirm_fixes=5)
    for _ in range(10):
      t.update(*RICHMOND, GOOD_FIX)
    t.update(38.9072, -77.0369, GOOD_FIX)    # one bad sample
    for _ in range(5):
      t.update(*RICHMOND, GOOD_FIX)
    self.assertTrue(t.inside)

  def test_entering_fires_once(self):
    t = geofence.StateTracker(confirm_fixes=3)
    dc = (38.9072, -77.0369)
    for _ in range(5):
      t.update(*dc, GOOD_FIX)
    fires = [t.update(*RICHMOND, GOOD_FIX) for _ in range(10)]
    self.assertEqual(sum(fires), 1)

  def test_starting_a_drive_already_inside_is_not_a_crossing(self):
    """Waking up in Richmond should not announce entering Virginia."""
    t = geofence.StateTracker(confirm_fixes=3)
    fires = [t.update(*RICHMOND, GOOD_FIX) for _ in range(10)]
    self.assertEqual(sum(fires), 0)
    self.assertTrue(t.inside)

  def test_poor_fixes_are_ignored_rather_than_believed(self):
    t = geofence.StateTracker(confirm_fixes=3)
    for _ in range(10):
      t.update(*RICHMOND, GOOD_FIX)
    for _ in range(10):
      t.update(38.9072, -77.0369, 500.0)     # garbage accuracy
    self.assertTrue(t.inside, "a bad fix must not move us out of the state")


class TestAntiNag(unittest.TestCase):
  """'does not keep annoying me audibly' -- the requirement most likely to be got wrong."""

  def _watch(self):
    w = RecklessWatch(threshold_mph=DEFAULT_THRESHOLD_MPH)
    _settle(w, *RICHMOND)
    return w

  def test_fires_once_on_crossing_the_threshold(self):
    w = self._watch()
    fires = 0
    for _ in range(50):
      if w.update(*RICHMOND, GOOD_FIX, 90 * MPH).started_speeding:
        fires += 1
    self.assertEqual(fires, 1)

  def test_stays_flagged_while_over(self):
    w = self._watch()
    for _ in range(20):
      st = w.update(*RICHMOND, GOOD_FIX, 90 * MPH)
    self.assertTrue(st.over)
    self.assertFalse(st.started_speeding)

  def test_a_small_dip_does_not_refire(self):
    w = self._watch()
    w.update(*RICHMOND, GOOD_FIX, 90 * MPH)
    w.update(*RICHMOND, GOOD_FIX, (DEFAULT_THRESHOLD_MPH - 1) * MPH)   # just under
    st = w.update(*RICHMOND, GOOD_FIX, 90 * MPH)
    self.assertFalse(st.started_speeding, "hovering at the threshold must not renag")

  def test_refires_only_after_dropping_well_under(self):
    w = self._watch()
    w.update(*RICHMOND, GOOD_FIX, 90 * MPH)
    w.update(*RICHMOND, GOOD_FIX, (DEFAULT_THRESHOLD_MPH - REARM_MARGIN_MPH - 2) * MPH)
    st = w.update(*RICHMOND, GOOD_FIX, 90 * MPH)
    self.assertTrue(st.started_speeding)

  def test_leaving_the_state_clears_everything(self):
    w = self._watch()
    w.update(*RICHMOND, GOOD_FIX, 90 * MPH)
    for _ in range(10):
      st = w.update(38.9072, -77.0369, GOOD_FIX, 90 * MPH)
    self.assertFalse(st.over)
    self.assertFalse(st.in_virginia)


class TestThresholds(unittest.TestCase):
  def test_statute_values_are_the_current_ones(self):
    """85 since HB 1442 (2020-07-01). It was 80 before; do not let it drift back."""
    self.assertEqual(STATUTE_ABSOLUTE_MPH, 85)
    self.assertEqual(STATUTE_OVER_LIMIT_MPH, 20)

  def test_default_warns_below_the_statute(self):
    self.assertLess(DEFAULT_THRESHOLD_MPH, STATUTE_ABSOLUTE_MPH)

  def test_silent_below_threshold(self):
    w = RecklessWatch()
    _settle(w, *RICHMOND)
    st = w.update(*RICHMOND, GOOD_FIX, 70 * MPH)
    self.assertFalse(st.over)

  def test_nothing_fires_outside_virginia(self):
    w = RecklessWatch()
    _settle(w, 35.7796, -78.6382)          # Raleigh
    st = w.update(35.7796, -78.6382, GOOD_FIX, 100 * MPH)
    self.assertFalse(st.over)
    self.assertFalse(st.started_speeding)

  def test_twenty_over_fires_when_the_limit_is_known(self):
    w = RecklessWatch()
    _settle(w, *RICHMOND)
    st = w.update(*RICHMOND, GOOD_FIX, 66 * MPH, speed_limit_ms=45 * MPH)
    self.assertTrue(st.over)
    self.assertEqual(st.reason, Reason.overLimit)

  def test_twenty_over_silent_when_the_limit_is_unknown(self):
    """OSM coverage is patchy; guessing a limit would produce false alarms."""
    w = RecklessWatch()
    _settle(w, *RICHMOND)
    st = w.update(*RICHMOND, GOOD_FIX, 66 * MPH, speed_limit_ms=0.0)
    self.assertFalse(st.over)

  def test_absolute_rule_still_applies_with_no_limit_data(self):
    w = RecklessWatch()
    _settle(w, *RICHMOND)
    st = w.update(*RICHMOND, GOOD_FIX, 90 * MPH, speed_limit_ms=0.0)
    self.assertTrue(st.over)
    self.assertEqual(st.reason, Reason.absolute)

  def test_reports_the_lower_threshold_when_both_are_breached(self):
    """On a 45 limit, 20-over (65) bites before the absolute 82."""
    w = RecklessWatch()
    _settle(w, *RICHMOND)
    st = w.update(*RICHMOND, GOOD_FIX, 95 * MPH, speed_limit_ms=45 * MPH)
    self.assertEqual(st.reason, Reason.overLimit)
    self.assertAlmostEqual(st.threshold_mph, 65.0, places=0)

  def test_threshold_is_configurable(self):
    w = RecklessWatch(threshold_mph=75)
    _settle(w, *RICHMOND)
    self.assertTrue(w.update(*RICHMOND, GOOD_FIX, 77 * MPH).over)


if __name__ == "__main__":
  unittest.main(verbosity=2)
