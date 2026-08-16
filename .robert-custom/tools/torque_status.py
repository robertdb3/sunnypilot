#!/usr/bin/env python3
"""Explain why Self-Tune has or has not learned your car's torque parameters.

Read-only. Reads the cached LiveTorqueParameters and CarParamsPrevRoute out of the params store.

    # on the device
    python3 torque_status.py

    # or against params copied off the device
    scp -r comma@<device>:/data/params/d ./params_d
    python3 torque_status.py --params ./params_d

Background for a car whose tune is substituted from another model (a preglobal Outback borrows
SUBARU_IMPREZA via torque_data/substitute.toml): openpilot's `torqued` fits latAccelFactor and
friction live, but only applies them for brands in its ALLOWED_CARS list, which excludes Subaru.
sunnypilot re-enables it when "Enforce Torque Lateral Control" is on. This script's headline line
is `useParams`, which says whether the values being learned are actually reaching the controller.
"""
import argparse
import os
import sys

# ---- pure logic, importable by tests without cereal or a device -----------------------------

# mirrors STEER_BUCKET_BOUNDS in selfdrive/locationd/torqued.py; the real values are imported at
# runtime below and these are only the fallback for offline analysis
FALLBACK_BUCKETS = [(-0.5, -0.3), (-0.3, -0.2), (-0.2, -0.1), (-0.1, 0),
                    (0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.5)]
FALLBACK_STRICT = [100, 300, 500, 500, 500, 500, 300, 100]
FALLBACK_RELAXED = [1, 200, 300, 500, 500, 300, 200, 1]


def bucket_counts(points, bounds) -> list[int]:
  """Re-bucket the cached points the same way TorqueBuckets.add_point does.

  Points are [steer_torque, lateral_accel]; the bucket is chosen on the steer torque, and the
  first matching half-open band wins.
  """
  counts = [0] * len(bounds)
  for p in points:
    if len(p) < 1:
      continue
    x = float(p[0])
    for i, (lo, hi) in enumerate(bounds):
      if lo <= x < hi:
        counts[i] += 1
        break
  return counts


def starving_buckets(counts, minimums) -> list[int]:
  """Indices of buckets short of their minimum -- the reason learning has not validated."""
  return [i for i, (c, m) in enumerate(zip(counts, minimums, strict=False)) if c < m]


def pinned(value: float, lo: float, hi: float, tol: float = 1e-3) -> str | None:
  """Whether a learned value has run into a sanity bound rather than settling on its own."""
  if hi > lo:
    if value <= lo + tol * max(abs(lo), 1.0):
      return "low"
    if value >= hi - tol * max(abs(hi), 1.0):
      return "high"
  return None


def bar(count: int, minimum: int, width: int = 24) -> str:
  if minimum <= 0:
    return "n/a".ljust(width)
  filled = min(int(width * count / minimum), width)
  return "#" * filled + "." * (width - filled)


# ---- device side ----------------------------------------------------------------------------

def _load(params_dir):
  """Return (liveTorqueParameters, CarParams) or exit with an explanation."""
  # mirror torqued.py's imports exactly: `opendbc.car.structs.car` IS the capnp CarParams here,
  # there is no top-level `cereal` module in this tree
  try:
    from openpilot.cereal import log
    from opendbc.car.structs import car as capnp_car
  except ImportError as e:
    sys.exit(f"needs openpilot's python environment: {e}\n"
             "Run this on the device, or set PYTHONPATH to the checkout root and opendbc_repo.")

  def _read(name):
    if params_dir:
      path = os.path.join(params_dir, name)
      if not os.path.exists(path):
        return None
      with open(path, "rb") as f:
        return f.read()
    from openpilot.common.params import Params
    return Params().get(name)

  torque_raw = _read("LiveTorqueParameters")
  cp_raw = _read("CarParamsPrevRoute")

  if torque_raw is None:
    sys.exit("No LiveTorqueParameters cached yet. Drive once with the car engaged, then re-run.")

  with log.Event.from_bytes(torque_raw) as evt:
    ltp = None
    for field in ("liveTorqueParameters", "lateralTorqueParameters"):
      # A name absent from the schema raises AttributeError; a name that exists but is not the
      # active union member raises capnp's KjException, which is what a stale cache looks like.
      try:
        ltp = getattr(evt, field)
        break
      except Exception:
        continue
    if ltp is None:
      sys.exit("Cached LiveTorqueParameters has no readable torque-parameters field. "
               "The cache may be stale or from a different schema; drive once with the car "
               "engaged to rewrite it.")
    ltp_dict = ltp.to_dict()
    # Older staging schemas call the fitted-value validity bit `valid`; newer ones call it
    # `liveValid`. Normalize it so the report below has one vocabulary.
    if "liveValid" not in ltp_dict and "valid" in ltp_dict:
      ltp_dict["liveValid"] = ltp_dict["valid"]

  cp = None
  if cp_raw is not None:
    try:
      with capnp_car.CarParams.from_bytes(cp_raw) as msg:
        cp = msg.to_dict()
    except Exception:
      pass   # offline values are a nicety; the bucket analysis is the point

  return ltp_dict, cp


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--params", help="directory of param files copied off the device (default: live)")
  args = ap.parse_args()

  ltp, cp = _load(args.params)

  bounds, strict, relaxed = FALLBACK_BUCKETS, FALLBACK_STRICT, FALLBACK_RELAXED
  factor_sanity, friction_sanity = 0.3, 0.5
  try:
    from openpilot.selfdrive.locationd.torqued import (STEER_BUCKET_BOUNDS, MIN_BUCKET_POINTS,
                                                       FACTOR_SANITY, FRICTION_SANITY)
    from openpilot.sunnypilot.selfdrive.locationd.torqued_ext import RELAXED_MIN_BUCKET_POINTS
    bounds = list(STEER_BUCKET_BOUNDS)
    strict = [int(v) for v in MIN_BUCKET_POINTS]
    relaxed = [int(v) for v in RELAXED_MIN_BUCKET_POINTS]
    factor_sanity, friction_sanity = FACTOR_SANITY, FRICTION_SANITY
  except ImportError:
    print("note: using fallback constants; could not import torqued\n")

  off_factor = off_friction = None
  if cp is not None:
    lat = cp.get("lateralTuning", {})
    if "torque" in lat:
      off_factor = lat["torque"].get("latAccelFactor")
      off_friction = lat["torque"].get("friction")

  print("=" * 68)
  if ltp.get("useParams"):
    print(" useParams: TRUE  -- learned values ARE reaching the controller")
  else:
    print(" useParams: FALSE -- learned values are computed and then DISCARDED")
    print("   Subaru is not in torqued's ALLOWED_CARS. Turn on, offroad:")
    print("     Steering > Enforce Torque Lateral Control")
    print("     Steering > Torque Lateral Control > Self-Tune")
  print("=" * 68)

  live_valid = ltp.get("liveValid")
  print(f"\n liveValid        {live_valid}"
        f"{'' if live_valid else '   (still learning, or a bucket is starving -- see below)'}")
  print(f" totalBucketPoints {ltp.get('totalBucketPoints', 0):.0f}")
  print(f" decay             {ltp.get('decay', 0):.0f}     maxResets {ltp.get('maxResets', 0):.0f}"
        f"     calPerc {ltp.get('calPerc', 0)}")

  print("\n--- learned vs offline ---")
  for name, raw_k, filt_k, offline, sanity in (
    ("latAccelFactor", "latAccelFactorRaw", "latAccelFactorFiltered", off_factor, factor_sanity),
    ("friction", "frictionCoefficientRaw", "frictionCoefficientFiltered", off_friction, friction_sanity),
  ):
    filt = ltp.get(filt_k, 0.0)
    raw = ltp.get(raw_k, 0.0)
    line = f"  {name:15s} learned {filt:7.3f}  (raw {raw:7.3f})"
    if offline is not None:
      lo, hi = (1 - sanity) * offline, (1 + sanity) * offline
      delta = (filt / offline - 1) * 100 if offline else 0
      line += f"   offline {offline:6.3f}  ({delta:+5.1f}%)   bounds [{lo:.3f}, {hi:.3f}]"
      p = pinned(filt, lo, hi)
      if p:
        line += f"\n  {'':15s} PINNED at the {p} bound -- it wants to go further than the"
        line += "\n  " + " " * 15 + "offline anchor allows. Turn on Less Restrict Settings,"
        line += "\n  " + " " * 15 + "or seed a better offline value with Custom Tuning."
    print(line)

  print("\n--- steer torque buckets ---")
  print("  (points are only collected while ENGAGED above ~34 mph)")
  points = ltp.get("points", [])
  counts = bucket_counts(points, bounds)
  print(f"\n  {'bucket':>14}  {'count':>6}  {'strict':>6}  {'relax':>6}  progress vs strict")
  for i, (lo, hi) in enumerate(bounds):
    s = strict[i] if i < len(strict) else 0
    r = relaxed[i] if i < len(relaxed) else 0
    print(f"  {f'{lo:+.1f}..{hi:+.1f}':>14}  {counts[i]:>6}  {s:>6}  {r:>6}  {bar(counts[i], s)}")

  short_strict = starving_buckets(counts, strict)
  short_relaxed = starving_buckets(counts, relaxed)
  print()
  if not short_strict:
    print("  All buckets satisfy even the strict minimums.")
  elif not short_relaxed:
    print("  Relaxed minimums are satisfied; strict are not.")
    print("  -> Less Restrict Settings will let this validate. Without it, it will not.")
  else:
    names = ", ".join(f"{bounds[i][0]:+.1f}..{bounds[i][1]:+.1f}" for i in short_relaxed)
    print(f"  Short even of the relaxed minimums in: {names}")
    print("  Outer buckets need firm cornering above 34 mph while engaged. Motorway cruising")
    print("  will never fill them. Drive curvy secondary roads at 40-55 mph.")

  print("\n  Reminder: changing Custom Tuning changes the offline values, which are part of the")
  print("  cache key -- it wipes the points above and restarts learning from zero.\n")


if __name__ == "__main__":
  main()
