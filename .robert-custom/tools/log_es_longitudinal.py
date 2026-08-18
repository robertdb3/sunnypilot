#!/usr/bin/env python3
"""Capture what EyeSight actually commands longitudinally, on this preglobal Outback.

READ-ONLY. This subscribes and parses; it never constructs a publisher and never sends a CAN
frame. That property is the whole reason it is safe to run while driving, so keep it: there is no
`PubMaster`, no `pub_sock`, and no `can_list_to_can_capnp` anywhere in this file, and there should
never be.

Why this exists
---------------
`openpilotLongitudinalControl` is false on this platform, so the runbook's whole longitudinal story
is workarounds around EyeSight. But the preglobal platform *does* carry a full longitudinal command
surface -- ES_Brake/ES_Distance/ES_Status, structurally identical to the global platform that
openpilot longitudinal already drives:

    ES_Brake    0x160   Brake_Pressure (16-bit), Cruise_Brake_Active, Cruise_Activated
    ES_Distance 0x161   Cruise_Throttle (12-bit), Car_Follow, Close_Distance, Standstill
    ES_Status   0x162   Cruise_RPM (16-bit), Cruise_Activated

What is *not* known is the scaling. `CarControllerParams` ships THROTTLE_INACTIVE=1818,
THROTTLE_MAX=3400, BRAKE_MAX=600 (~-3.5 m/s^2), RPM_MAX=3600 -- all derived from **global** cars.
Whether any of that transfers to preglobal is unmeasured, and guessing it is exactly the kind of
thing that goes wrong quietly. This tool measures it from EyeSight's own behaviour.

The bus split is the useful part
--------------------------------
Panda marks ES_Distance and ES_LKAS `check_relay`, so the camera's copies are statically blocked and
openpilot's replace them. ES_Brake and ES_Status are *not* in the preglobal TX list, so they are
forwarded from the camera untouched. Therefore:

  * bus 2 (camera) = what EyeSight *wants* -- ground truth for the command scaling
  * bus 0 (main)   = what actually reaches the car

For ES_Brake the two should agree (pure forward). For ES_Distance they will differ in Cruise_Button
whenever ICBM or a driver press is active. A disagreement anywhere else is worth understanding
before anyone writes a line of longitudinal control.

Usage
-----
Offline, against a route already on the device (no driving needed -- start here)::

    PYTHONPATH=/data/openpilot /usr/local/venv/bin/python log_es_longitudinal.py \
        --route-glob '/data/media/0/realdata/00000017--c0343146c1--*/rlog.zst' --csv /tmp/es.csv

Live, parked or with a passenger operating it, EyeSight ACC engaged::

    PYTHONPATH=/data/openpilot /usr/local/venv/bin/python log_es_longitudinal.py \
        --seconds 300 --csv /tmp/es.csv

Either way it prints an analysis at the end comparing the measured ranges against the global-derived
constants openpilot ships.
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import re
import sys
import time

# Signals we care about, per message. Kept explicit so a DBC rename is a loud KeyError rather than a
# column of silent zeros.
ES_BRAKE_SIGNALS = ("Brake_Pressure", "Cruise_Brake_Active", "Cruise_Activated", "Cruise_Brake_Lights", "Cruise_Fault")
ES_DISTANCE_SIGNALS = ("Cruise_Throttle", "Car_Follow", "Close_Distance", "Standstill", "Cruise_Fault", "Cruise_Button")
ES_STATUS_SIGNALS = ("Cruise_RPM", "Cruise_Activated", "Brake")

# What opendbc ships for Subaru longitudinal. All derived from GLOBAL cars; this tool exists to find
# out whether any of it is true here. See opendbc/car/subaru/values.py CarControllerParams.
GLOBAL_CONSTANTS = {
  "THROTTLE_MIN": 808,
  "THROTTLE_INACTIVE": 1818,
  "THROTTLE_MAX": 3400,
  "RPM_MIN": 0,
  "RPM_INACTIVE": 600,
  "RPM_MAX": 3600,
  "BRAKE_MIN": 0,
  "BRAKE_MAX": 600,  # about -3.5 m/s^2 on global, per the opendbc comment
}

CAM_BUS = 2
MAIN_BUS = 0


def segment_key(path: str):
  """Sort route segments numerically.

  Lexicographic sorting puts segment 10 before segment 2, which silently reorders a drive. The
  runbook calls this out; analyze_last_drive.py still uses a plain sorted().
  """
  name = os.path.basename(os.path.dirname(path))
  m = re.search(r"--(\d+)$", name)
  return (name.rsplit("--", 1)[0], int(m.group(1)) if m else -1)


def build_parsers(fingerprint: str):
  from opendbc.car.subaru.values import DBC
  from opendbc.car import Bus
  from opendbc.can.parser import CANParser

  dbc = DBC[fingerprint][Bus.pt]
  # Empty message list parses everything in the DBC, matching carstate.get_can_parsers().
  return dbc, CANParser(dbc, [], CAM_BUS), CANParser(dbc, [], MAIN_BUS)


def read_row(cp_cam, cp_main, state: dict) -> dict:
  """One sample: EyeSight's command (bus 2) beside what reached the car (bus 0)."""
  row = {"t": round(state.get("t", 0.0), 3)}

  for sig in ES_BRAKE_SIGNALS:
    row[f"cam_brake_{sig}"] = cp_cam.vl["ES_Brake"][sig]
  for sig in ES_DISTANCE_SIGNALS:
    row[f"cam_dist_{sig}"] = cp_cam.vl["ES_Distance"][sig]
  for sig in ES_STATUS_SIGNALS:
    row[f"cam_status_{sig}"] = cp_cam.vl["ES_Status"][sig]

  # Only the signals whose bus-0 value is genuinely informative: brake should be a pure forward,
  # throttle should be a verbatim copy, button is where openpilot legitimately differs.
  row["main_brake_Brake_Pressure"] = cp_main.vl["ES_Brake"]["Brake_Pressure"]
  row["main_dist_Cruise_Throttle"] = cp_main.vl["ES_Distance"]["Cruise_Throttle"]
  row["main_dist_Cruise_Button"] = cp_main.vl["ES_Distance"]["Cruise_Button"]

  for k in ("v_ego", "a_ego", "standstill", "cruise_enabled", "cruise_speed", "brake_pressed", "gas_pressed"):
    row[k] = state.get(k, "")
  return row


def collect_route(route_glob: str, decimate: int) -> list[dict]:
  from openpilot.tools.lib.logreader import _LogFileReader
  from openpilot.selfdrive.pandad import can_capnp_to_list

  files = sorted(glob.glob(route_glob), key=segment_key)
  if not files:
    print(f"no segments matched {route_glob}", file=sys.stderr)
    return []
  print(f"segments {len(files)}: " + " ".join(os.path.basename(os.path.dirname(f)) for f in files))

  fingerprint = None
  for fn in files:
    for m in _LogFileReader(fn):
      if m.which() == "carParams":
        fingerprint = m.carParams.carFingerprint
        break
    if fingerprint:
      break
  if fingerprint is None:
    print("no carParams in route; cannot pick a DBC", file=sys.stderr)
    return []
  print(f"carFingerprint: {fingerprint}")

  dbc, cp_cam, cp_main = build_parsers(fingerprint)
  print(f"dbc: {dbc}")

  rows: list[dict] = []
  state: dict = {}
  pending: list[bytes] = []
  n_can = 0
  t0 = None

  def flush():
    if not pending:
      return
    packets = can_capnp_to_list(pending)
    cp_cam.update(packets)
    cp_main.update(packets)
    pending.clear()

  for fn in files:
    for m in _LogFileReader(fn):
      which = m.which()
      if which == "carState":
        cs = m.carState
        state.update(v_ego=round(cs.vEgo, 4), a_ego=round(cs.aEgo, 4), standstill=int(cs.standstill),
                     cruise_enabled=int(cs.cruiseState.enabled), cruise_speed=round(cs.cruiseState.speed, 3),
                     brake_pressed=int(cs.brakePressed), gas_pressed=int(cs.gasPressed))
      elif which == "can":
        if t0 is None:
          t0 = m.logMonoTime
        state["t"] = (m.logMonoTime - t0) / 1e9
        pending.append(m.as_builder().to_bytes())
        n_can += 1
        if len(pending) >= 16:
          flush()
        if n_can % decimate == 0:
          try:
            rows.append(read_row(cp_cam, cp_main, state))
          except KeyError as e:
            print(f"signal missing from DBC: {e}. The DBC mapping is wrong; stop here.", file=sys.stderr)
            return rows
  flush()
  return rows


def collect_live(seconds: float, decimate: int) -> list[dict]:
  import cereal.messaging as messaging
  from openpilot.selfdrive.pandad import can_capnp_to_list

  # Read the fingerprint off carParams rather than decoding the cached CarParamsCache blob.
  sm = messaging.SubMaster(["carState", "carParams"])
  can_sock = messaging.sub_sock("can", conflate=False, timeout=100)

  print("waiting for carParams ...")
  while not sm.updated["carParams"]:
    sm.update(100)
  fingerprint = sm["carParams"].carFingerprint
  print(f"carFingerprint: {fingerprint}")

  dbc, cp_cam, cp_main = build_parsers(fingerprint)
  print(f"dbc: {dbc}")
  print(f"read-only capture for {seconds:.0f}s -- engage EyeSight ACC and drive normally\n")

  rows: list[dict] = []
  state: dict = {}
  start = time.monotonic()
  n = 0
  while time.monotonic() - start < seconds:
    sm.update(0)
    if sm.updated["carState"]:
      cs = sm["carState"]
      state.update(v_ego=round(cs.vEgo, 4), a_ego=round(cs.aEgo, 4), standstill=int(cs.standstill),
                   cruise_enabled=int(cs.cruiseState.enabled), cruise_speed=round(cs.cruiseState.speed, 3),
                   brake_pressed=int(cs.brakePressed), gas_pressed=int(cs.gasPressed))

    can_strs = messaging.drain_sock_raw(can_sock)
    if can_strs:
      packets = can_capnp_to_list(can_strs)
      cp_cam.update(packets)
      cp_main.update(packets)
      n += 1
      state["t"] = time.monotonic() - start
      if n % decimate == 0:
        try:
          rows.append(read_row(cp_cam, cp_main, state))
        except KeyError as e:
          print(f"signal missing from DBC: {e}", file=sys.stderr)
          return rows
        if len(rows) % 40 == 0:
          r = rows[-1]
          print(f"  t={r['t']:6.1f}  v={r['v_ego']:5.1f}  a={r['a_ego']:6.2f}  "
                f"thr={r['cam_dist_Cruise_Throttle']:6.0f}  rpm={r['cam_status_Cruise_RPM']:6.0f}  "
                f"brk={r['cam_brake_Brake_Pressure']:5.0f}  act={r['cam_brake_Cruise_Brake_Active']:.0f}")
  return rows


def pct(values: list[float], p: float) -> float:
  if not values:
    return float("nan")
  s = sorted(values)
  return s[min(len(s) - 1, int(p / 100.0 * len(s)))]


def analyse(rows: list[dict]) -> None:
  if not rows:
    print("\nno samples captured")
    return

  eng = [r for r in rows if r.get("cruise_enabled") == 1]
  print(f"\n{'=' * 78}\n{len(rows)} samples, {len(eng)} with stock cruise engaged\n{'=' * 78}")
  if not eng:
    print("No engaged samples -- the interesting signals only move under EyeSight ACC. Re-run with\n"
          "cruise actually engaged, or point --route-glob at a drive where it was.")
    return

  thr = [float(r["cam_dist_Cruise_Throttle"]) for r in eng]
  rpm = [float(r["cam_status_Cruise_RPM"]) for r in eng]
  brk = [float(r["cam_brake_Brake_Pressure"]) for r in eng]

  print(f"\n{'signal':<22} {'min':>8} {'p05':>8} {'median':>8} {'p95':>8} {'max':>8}   global ships")
  for name, vals, gmin, gmax in (("Cruise_Throttle", thr, "THROTTLE_MIN", "THROTTLE_MAX"),
                                 ("Cruise_RPM", rpm, "RPM_MIN", "RPM_MAX"),
                                 ("Brake_Pressure", brk, "BRAKE_MIN", "BRAKE_MAX")):
    print(f"{name:<22} {min(vals):8.0f} {pct(vals, 5):8.0f} {pct(vals, 50):8.0f} "
          f"{pct(vals, 95):8.0f} {max(vals):8.0f}   {GLOBAL_CONSTANTS[gmin]}..{GLOBAL_CONSTANTS[gmax]}")

  # Real zero-acceleration throttle: what EyeSight holds while cruising flat.
  steady = [r for r in eng if abs(float(r["a_ego"] or 0)) < 0.1 and float(r["v_ego"] or 0) > 8]
  if steady:
    vals = [float(r["cam_dist_Cruise_Throttle"]) for r in steady]
    print(f"\nsteady cruise (|a|<0.1, v>8 m/s, n={len(steady)}): Cruise_Throttle median "
          f"{pct(vals, 50):.0f}   global THROTTLE_INACTIVE = {GLOBAL_CONSTANTS['THROTTLE_INACTIVE']}")
  else:
    print("\nno steady-cruise samples -- need some flat cruising to pin THROTTLE_INACTIVE")

  # Brake pressure vs measured deceleration: the BRAKE_LOOKUP the global port guesses at.
  braking = [r for r in eng if float(r["cam_brake_Brake_Pressure"] or 0) > 0]
  if braking:
    print(f"\nbraking samples: {len(braking)}")
    buckets: dict[int, list[float]] = {}
    for r in braking:
      b = int(float(r["cam_brake_Brake_Pressure"]) // 50 * 50)
      buckets.setdefault(b, []).append(float(r["a_ego"] or 0))
    print(f"  {'Brake_Pressure':>16}  {'n':>5}  {'median a_ego (m/s^2)':>22}")
    for b in sorted(buckets):
      v = buckets[b]
      print(f"  {b:>10}-{b + 49:<5}  {len(v):>5}  {pct(v, 50):>22.2f}")
    print("\n  This is the real BRAKE_LOOKUP. Global assumes BRAKE_MAX=600 ~ -3.5 m/s^2; compare the\n"
          "  deceleration actually reached at max observed pressure before believing that here.")
  else:
    print("\nno braking samples -- need a drive where EyeSight actually slowed for a lead")

  # Forwarding sanity: ES_Brake should be identical on both buses today.
  mismatch = [r for r in rows
              if float(r["cam_brake_Brake_Pressure"] or 0) != float(r["main_brake_Brake_Pressure"] or 0)]
  print(f"\nES_Brake bus2 vs bus0 mismatches: {len(mismatch)} / {len(rows)}")
  if mismatch:
    print("  Unexpected -- ES_Brake is not in the preglobal TX list, so it should be a pure forward.\n"
          "  Understand this before trusting any of the above.")

  thr_mismatch = [r for r in rows
                  if float(r["cam_dist_Cruise_Throttle"] or 0) != float(r["main_dist_Cruise_Throttle"] or 0)]
  print(f"ES_Distance Cruise_Throttle bus2 vs bus0 mismatches: {len(thr_mismatch)} / {len(rows)}")
  print("  openpilot rebuilds ES_Distance and copies Cruise_Throttle verbatim, so 0 is expected.\n"
        "  Non-zero would mean the copy is lossy -- which would matter a great deal.")


def main() -> int:
  ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  ap.add_argument("--route-glob", help="parse an existing rlog instead of running live")
  ap.add_argument("--seconds", type=float, default=300.0, help="live capture duration")
  ap.add_argument("--decimate", type=int, default=5, help="emit one row per N can updates")
  ap.add_argument("--csv", help="write samples here")
  args = ap.parse_args()

  rows = collect_route(args.route_glob, args.decimate) if args.route_glob \
      else collect_live(args.seconds, args.decimate)

  if args.csv and rows:
    with open(args.csv, "w", newline="") as f:
      w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
      w.writeheader()
      w.writerows(rows)
    print(f"\nwrote {len(rows)} rows to {args.csv}")

  analyse(rows)
  return 0


if __name__ == "__main__":
  sys.exit(main())
