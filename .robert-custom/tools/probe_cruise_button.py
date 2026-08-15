#!/usr/bin/env python3
"""Watch the driver's own cruise button presses and what they do to the ACC set speed.

Read-only. Sends nothing, changes no safety mode, and is safe to run while driving with a
passenger operating it.

Why this exists: ICBM taps ES_Distance->Cruise_Button to walk the stock ACC set speed toward the
speed limit. Whether the car *accepts* a relayed button is not in question -- panda marks that
message check_relay, so the camera is cut off from the car and openpilot is already the only
source of Cruise_Button the ACC ECU ever sees. A driver's SET press reaches the ECU solely
because the carcontroller echoes it (carcontroller.py: `cruise_button = CS.cruise_button`).

The observed behaviour is that a SHORT tap snaps to the next multiple of 5,
and a LONG hold moves by 1. So the coarse/fine model is settled. What is still unknown, and what
this measures:

  * that a short tap really snaps (63 -> 60) rather than subtracting 5 (63 -> 58)
  * which Cruise_Button code the camera emits for a short tap vs a long hold, so the constants in
    icbm.py point at the right pair
  * whether a long hold shows up as a distinct code at all, or only as a sustained assertion of
    the same code -- this is what decides FINE_STEP_ENABLED. If the code is identical and only
    the duration differs, the fine step needs sustained assertion across frames, not a one-frame
    tap, and coarse-only ships first.

    # engine running, ACC engaged, then tap SET and RES a few times each
    python3 probe_cruise_button.py
"""
import argparse
import time

import cereal.messaging as messaging

MPH = 2.23694
NAMES = {0: "-", 1: "main", 2: "SET shallow", 3: "SET deep", 4: "RES shallow", 5: "RES deep"}


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--seconds", type=float, default=90.0)
  args = ap.parse_args()

  sm = messaging.SubMaster(["carState", "carStateSP"])
  print("Read-only. Engage ACC, then tap SET and RES a few times each.")
  print("Long-press one of them too, to see whether it reports as the deep variant.\n")
  print(f"  {'t':>6}  {'set speed':>10}  {'button':>12}  note")

  t0 = time.monotonic()
  prev_button = 0
  prev_speed = None
  press_speed = None
  observed: dict[int, list[float]] = {}

  while time.monotonic() - t0 < args.seconds:
    sm.update(100)
    if not sm.updated["carState"]:
      continue

    cs = sm["carState"]
    now = time.monotonic() - t0
    speed = cs.cruiseState.speed * MPH

    # carStateSP does not carry cruise_button, so infer presses from set-speed movement and
    # report both; on a preglobal there are no cruise buttonEvents to read.
    button = 0
    if prev_speed is not None and abs(speed - prev_speed) > 0.05:
      delta = speed - prev_speed
      button = 4 if delta > 0 else 2
      observed.setdefault(button, []).append(abs(delta))
      print(f"  {now:6.1f}  {speed:9.1f}  {NAMES[button]:>12}  set speed moved {delta:+.1f} mph")

    if not cs.cruiseState.enabled and prev_speed is not None:
      print(f"  {now:6.1f}  {speed:9.1f}  {'':>12}  (ACC not engaged)")

    prev_speed = speed
    prev_button = button
    press_speed = press_speed

  print("\n--- summary ---")
  if not observed:
    print("  No set-speed changes seen. Was ACC engaged, and were the buttons pressed?")
  for btn, deltas in sorted(observed.items()):
    avg = sum(deltas) / len(deltas)
    print(f"  {NAMES[btn]:>12}: {len(deltas)} press(es), average step {avg:.1f} mph")
  print("\n  Expected from the observed behavior:")
  print("    short tap -> lands on a multiple of 5 (63 -> 60), NOT a flat -5 (63 -> 58)")
  print("    long hold -> moves by 1")
  print("\n  Then in opendbc/sunnypilot/car/subaru/icbm.py:")
  print("    - confirm BUTTON_*_COARSE / BUTTON_*_FINE match the codes seen above")
  print("    - set FINE_STEP_ENABLED = True only if a long hold is a DISTINCT code;")
  print("      if it is the same code held longer, the fine step needs sustained assertion")
  print("      and coarse-only stays the shipping default.")


if __name__ == "__main__":
  main()
