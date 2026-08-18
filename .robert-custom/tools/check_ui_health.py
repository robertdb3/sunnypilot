#!/usr/bin/env python3
"""Post-install UI smoke check. Run ON the device, parked.

    PYTHONPATH=/data/openpilot /usr/local/venv/bin/python tools/check_ui_health.py

Exists because of symptom 14: patch 0011 passed every offline test and then crash-looped the UI on
the first real onroad frame. Nothing off-device can catch that class of bug -- the prebuilt msgq
extension is aarch64-Linux, so a laptop or an x86 CI runner cannot even import cereal, let alone
build a real Cap'n Proto reader. Device verification is not belt-and-braces here; it is the only
verification that exists.

Two things this encodes that cost real time to rediscover:

* `ui` is an `always_run` process started at manager start, and this manager generation does not
  respawn a crashed one. A dead UI stays dead through an ignition cycle, so "I restarted the car"
  proves nothing.
* A UI crash leaves `card` and `controlsd` running. The car looks fine over SSH while the driver
  has no display, which is why this reports them separately rather than a single verdict.

Exit code is 0 only if the UI is alive and nothing new has crashed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

CRASH_DIR = "/data/community/crashes"
WATCH = {
  "ui": "selfdrive.ui.ui",
  "card": "selfdrive.car.card",
  "controlsd": "selfdrive.controls.controlsd",
  "manager": "manager.py",
}


def pids(pattern: str) -> list[str]:
  out = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout
  return [l.split()[0] for l in out.splitlines() if pattern in l and "grep" not in l]


def newest_crash() -> tuple[str | None, float]:
  try:
    logs = [os.path.join(CRASH_DIR, f) for f in os.listdir(CRASH_DIR) if f.endswith(".log")]
  except OSError:
    return None, 0.0
  if not logs:
    return None, 0.0
  newest = max(logs, key=os.path.getmtime)
  return newest, os.path.getmtime(newest)


def param(name: str) -> str:
  try:
    with open(f"/data/params/d/{name}", "rb") as f:
      return f.read().decode(errors="replace").strip() or "(empty)"
  except OSError:
    return "(unset)"


def main() -> int:
  baseline_log, baseline_mtime = newest_crash()
  print(f"Scene3D={param('Scene3D')}  IsOffroad={param('IsOffroad')}")
  if baseline_log:
    age = time.time() - baseline_mtime
    print(f"newest crash log: {os.path.basename(baseline_log)} ({age / 60:.1f} min old)")
  else:
    print("newest crash log: none")

  print()
  ok = True
  for label, pattern in WATCH.items():
    found = pids(pattern)
    state = f"pid {','.join(found)}" if found else "NOT RUNNING"
    print(f"  {label:<11} {state}")
    if label == "ui" and not found:
      ok = False

  if not ok:
    print("\nUI is not running. This manager does not respawn it, and an ignition cycle will not")
    print("bring it back -- reboot offroad, or launch it manually:")
    print("  cd /data/openpilot && setsid env PYTHONPATH=/data/openpilot \\")
    print("    /usr/local/venv/bin/python -m openpilot.selfdrive.ui.ui </dev/null >/tmp/ui.log 2>&1 &")
    if baseline_log:
      print(f"\nLast crash ({os.path.basename(baseline_log)}), final lines:")
      with open(baseline_log, encoding="utf-8", errors="replace") as f:
        for line in f.read().splitlines()[-6:]:
          print("  " + line)
    return 1

  # Watch for a NEW crash: a UI that is up right now may still die on the next onroad frame.
  hold = int(os.environ.get("HOLD_SECONDS", "20"))
  print(f"\nUI alive. Watching {hold}s for a new crash "
        f"(toggle Scene3D on and go onroad now to exercise the render path)...")
  start_pids = set(pids(WATCH["ui"]))
  for _ in range(hold):
    time.sleep(1)
    log, mtime = newest_crash()
    if log and mtime > baseline_mtime:
      print(f"\nNEW CRASH: {os.path.basename(log)}")
      with open(log, encoding="utf-8", errors="replace") as f:
        for line in f.read().splitlines()[-8:]:
          print("  " + line)
      return 1
    if set(pids(WATCH["ui"])) != start_pids:
      print("\nUI pid changed -- it died and/or was restarted during the watch.")
      return 1

  print("No new crashes; UI pid stable. This is necessary, not sufficient: the render path only")
  print("runs onroad with Scene3D on, so re-run this while actually onroad before trusting it.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
