"""The preglobal Outback's torque tune resolves to its own values, not the Impreza's.

    python3 tests/test_torque_tune.py

Parses the three TOMLs directly, so no compiled deps and no device.

Why this matters: openpilot's feedforward is `lateral_accel / LAT_ACCEL_FACTOR`, so an error in
that number scales with how hard the car is turning -- invisible on a straight, worst mid-corner.
The Impreza value this platform used to borrow is roughly half of every other Subaru on the same
STEER_MAX=2047 scale, which commands about twice the steer torque a bend needs.
"""
import os
import tomllib
import unittest

TORQUE_DATA = os.path.join(os.path.dirname(__file__), "..", "sunnypilot",
                           "opendbc_repo", "opendbc", "car", "torque_data")

PLATFORM = "SUBARU_OUTBACK_PREGLOBAL_2018"
EXPECTED = [2.0, 1.5, 0.2]

# torqued's relaxed sanity window is [0, 2x] the offline value (RELAXED factor_sanity = 1.0)
RELAXED_SANITY = 1.0


def _load():
  out = {}
  for name in ("substitute", "params", "override"):
    with open(os.path.join(TORQUE_DATA, f"{name}.toml"), "rb") as f:
      out[name] = tomllib.load(f)
  return out["substitute"], out["params"], out["override"]


def _resolve(candidate, sub, params, override):
  target = sub.get(candidate, candidate)
  if target in override:
    return override[target]
  return params[target]


class TestPreglobalOutbackTune(unittest.TestCase):
  def setUp(self):
    self.sub, self.params, self.override = _load()

  def test_resolves_to_its_own_values(self):
    self.assertEqual(_resolve(PLATFORM, self.sub, self.params, self.override), EXPECTED)

  def test_no_longer_borrows_the_impreza(self):
    self.assertNotIn(PLATFORM, self.sub,
                     "platform is substituted again; it should have its own override entry")

  def test_defined_exactly_once(self):
    """get_torque_params() raises 'defined twice in torque config' otherwise."""
    n = sum(PLATFORM in x for x in (self.sub, self.params, self.override))
    self.assertEqual(n, 1, f"{PLATFORM} appears in {n} config files, must be exactly 1")

  def test_legend_order_is_what_we_assume(self):
    self.assertEqual(self.params["legend"],
                     ["LAT_ACCEL_FACTOR", "MAX_LAT_ACCEL_MEASURED", "FRICTION"])


class TestSelfTuneWindowNowReaches(unittest.TestCase):
  """The point of the change. torqued's bounds are a percentage of the OFFLINE value, so anchoring
  on the Impreza capped relaxed learning at 2 x 1.067 = 2.134 -- below every plausible Subaru.
  Self-Tune would pin against the ceiling and stop. The window must now contain the answer."""

  def setUp(self):
    self.sub, self.params, self.override = _load()
    self.factor = _resolve(PLATFORM, self.sub, self.params, self.override)[0]

  def _other_subaru_factors(self):
    out = {}
    for src in (self.params, self.override):
      for k, v in src.items():
        if k.startswith("SUBARU") and k != PLATFORM and isinstance(v, list):
          f = v[0]
          if isinstance(f, float) and f == f:   # skip nan (angle-control cars)
            out[k] = f
    return out

  def test_window_contains_every_other_subaru(self):
    ceiling = (1.0 + RELAXED_SANITY) * self.factor
    for name, f in self._other_subaru_factors().items():
      self.assertLessEqual(f, ceiling,
                           f"relaxed Self-Tune ceiling {ceiling:.3f} cannot reach {name} at {f:.3f}")

  def test_the_old_impreza_anchor_could_not(self):
    """Guards the reasoning itself: with the old anchor the window genuinely fell short."""
    impreza = self.params["SUBARU_IMPREZA"][0]
    old_ceiling = (1.0 + RELAXED_SANITY) * impreza
    unreachable = [n for n, f in self._other_subaru_factors().items() if f > old_ceiling]
    self.assertTrue(unreachable,
                    "the Impreza anchor now reaches everything; this change may be unnecessary")

  def test_new_factor_is_in_family_with_other_subarus(self):
    others = sorted(self._other_subaru_factors().values())
    self.assertGreaterEqual(self.factor, min(others) * 0.7)
    self.assertLessEqual(self.factor, max(others) * 1.3)


class TestScopeIsDeliberate(unittest.TestCase):
  """Only one car was changed. The others are unverified, and the Legacy has a different steering
  ratio (12.5 vs 20). If someone changes them, they should have to update this."""

  OTHERS = ["SUBARU_OUTBACK_PREGLOBAL", "SUBARU_FORESTER_PREGLOBAL", "SUBARU_LEGACY_PREGLOBAL"]

  def test_other_preglobals_still_substitute_to_impreza(self):
    sub, _, _ = _load()
    for p in self.OTHERS:
      self.assertEqual(sub.get(p), "SUBARU_IMPREZA", f"{p} scope changed without updating notes")


if __name__ == "__main__":
  unittest.main(verbosity=2)
