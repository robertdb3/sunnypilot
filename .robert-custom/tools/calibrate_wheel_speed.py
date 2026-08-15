#!/usr/bin/env python3
"""Measure how far the car's speed reading is off, and print the wheelSpeedFactor that fixes it.

Read-only. Compares carState.vEgo against GPS ground speed while cruising, and reports the ratio.

    # on the device, driving (have a passenger run it), highway is ideal
    python3 calibrate_wheel_speed.py --minutes 10

Why this exists: openpilot derives vEgo from wheel-speed counts times a per-platform constant,
then multiplies by CP.wheelSpeedFactor. That factor defaults to 1.0 and Subaru never overrides it,
while Toyota sets 1.035 and Honda 1.025 for exactly this reason (opendbc/car/interfaces.py:242).
A car reading 2-5 mph high at highway speed is 3-7% out, squarely in the range those correct.

Samples are filtered to conditions where GPS speed is trustworthy and the two measurements are
comparable: above a floor speed, near-zero acceleration, and a good horizontal fix. The median is
reported rather than the mean so one bad patch of sky cannot skew it.
"""
import argparse
import statistics
import sys
import time

import cereal.messaging as messaging

MS_TO_MPH = 2.23694

MIN_SPEED_MS = 20.0          # ~45 mph; GPS speed is noisy at low speed and wheel slip matters more
MAX_ACCEL_MS2 = 0.25         # steady cruise only
MAX_HORIZ_ACC_M = 10.0
SETTLE_S = 2.0               # ignore samples right after conditions become valid


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--minutes", type=float, default=10.0)
  ap.add_argument("--service", default=None,
                  help="gpsLocation (qcom, default on 3X) or gpsLocationExternal (ublox)")
  args = ap.parse_args()

  services = [args.service] if args.service else ["gpsLocation", "gpsLocationExternal"]
  sm = messaging.SubMaster(["carState"] + services)

  print("Collecting. Cruise steadily above 45 mph; curves and traffic are fine, just avoid")
  print("accelerating and braking. Ctrl-C to stop early.\n")

  ratios: list[float] = []
  speeds: list[float] = []
  valid_since = None
  t0 = time.monotonic()
  last_report = 0.0
  used_service = None

  try:
    while time.monotonic() - t0 < args.minutes * 60:
      sm.update(200)
      if not sm.updated["carState"]:
        continue

      svc = next((s for s in services if sm.valid.get(s) and sm[s].latitude != 0.0), None)
      if svc is None:
        continue
      if used_service is None:
        used_service = svc
        print(f"  using {svc}\n")

      cs, gps = sm["carState"], sm[svc]
      gps_ms = float(gps.speed)

      ok = (cs.vEgo > MIN_SPEED_MS and gps_ms > MIN_SPEED_MS
            and abs(cs.aEgo) < MAX_ACCEL_MS2
            and 0.0 < gps.horizontalAccuracy < MAX_HORIZ_ACC_M)

      if not ok:
        valid_since = None
        continue
      if valid_since is None:
        valid_since = time.monotonic()
        continue
      if time.monotonic() - valid_since < SETTLE_S:
        continue

      ratios.append(gps_ms / cs.vEgo)
      speeds.append(cs.vEgo)

      now = time.monotonic() - t0
      if now - last_report > 15.0 and ratios:
        med = statistics.median(ratios)
        print(f"  {now / 60:4.1f} min  {len(ratios):5d} samples  running factor {med:.4f}")
        last_report = now
  except KeyboardInterrupt:
    print("\n  stopped early")

  print("\n" + "=" * 62)
  if len(ratios) < 200:
    print(f" Only {len(ratios)} samples. Not enough to trust -- drive longer at a steady")
    print(" highway speed. If it collected nothing, check that GPS has a fix.")
    sys.exit(1)

  med = statistics.median(ratios)
  lo, hi = statistics.quantiles(ratios, n=20)[0], statistics.quantiles(ratios, n=20)[-1]
  spread = (hi - lo) / med * 100
  mean_speed = statistics.mean(speeds)
  err_mph = (1 - med) * mean_speed * MS_TO_MPH

  print(f" samples          {len(ratios)}  ({used_service})")
  print(f" mean speed       {mean_speed * MS_TO_MPH:.1f} mph")
  print(f" indicated error  {err_mph:+.1f} mph  (positive = car reads HIGH)")
  print(f" 5th-95th spread  {spread:.1f}%")
  print()
  print(f"   wheelSpeedFactor = {med:.4f}")
  print()
  print(" Set it in opendbc/car/subaru/interface.py, scoped to the preglobal platform,")
  print(" following the Toyota (1.035) and Honda (1.025) precedent.")
  if spread > 3.0:
    print()
    print(f" NOTE: {spread:.1f}% spread is wide. Likely mixed conditions or a poor GPS stretch.")
    print(" Re-run on open highway before trusting this number.")
  print("=" * 62)


if __name__ == "__main__":
  main()
