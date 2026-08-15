#!/usr/bin/env python3
import collections
import time

from openpilot.cereal import messaging


SERVICES = [
  "carState", "controlsState", "selfdriveState", "modelV2", "modelDataV2SP",
  "radarState", "longitudinalPlan", "longitudinalPlanSP", "driverAssistance",
  "selfdriveStateSP", "liveMapDataSP",
]


def main():
  duration = 20.0
  sm = messaging.SubMaster(SERVICES)
  counts = collections.Counter()
  invalid = collections.Counter()
  alerts = collections.Counter()
  icbm_buttons = collections.Counter()
  start = time.monotonic()
  while time.monotonic() - start < duration:
    sm.update(50)
    for service in SERVICES:
      if sm.updated[service]:
        counts[service] += 1
        invalid[service] += not sm.valid[service]
    if sm.updated["selfdriveState"]:
      state = sm["selfdriveState"]
      if state.alertText1 or state.alertText2:
        alerts[(state.alertText1, state.alertText2)] += 1
    if sm.updated["selfdriveStateSP"]:
      icbm = sm["selfdriveStateSP"].intelligentCruiseButtonManagement
      icbm_buttons[(str(icbm.sendButton), round(icbm.vTarget, 1), round(icbm.vCruiseCluster, 1))] += 1

  elapsed = time.monotonic() - start
  for service in SERVICES:
    print(f"{service:22s} {counts[service] / elapsed:7.2f} Hz  "
          f"messages={counts[service]:4d} invalid={invalid[service]:4d} alive={sm.alive[service]}")
  map_data = sm["liveMapDataSP"]
  print("alerts", dict(alerts))
  print("icbm", dict(icbm_buttons))
  print("map", {"valid": bool(map_data.speedLimitValid), "speedLimit": round(map_data.speedLimit, 2),
                "aheadValid": bool(map_data.speedLimitAheadValid), "roadName": map_data.roadName})


if __name__ == "__main__":
  main()
