#!/usr/bin/env python3
import time

from openpilot.cereal import messaging


def main():
  sm = messaging.SubMaster(["carState", "selfdriveStateSP"])
  last = None
  start = time.monotonic()
  while time.monotonic() - start < 60.0:
    sm.update(100)
    if not (sm.updated["carState"] or sm.updated["selfdriveStateSP"]):
      continue
    state = (bool(sm["carState"].cruiseState.available),
             bool(sm["selfdriveStateSP"].mads.enabled),
             bool(sm["selfdriveStateSP"].mads.active))
    if state != last:
      print(f"{time.monotonic() - start:5.2f}s cruise_available={state[0]} "
            f"mads_enabled={state[1]} mads_active={state[2]}", flush=True)
      last = state


if __name__ == "__main__":
  main()
