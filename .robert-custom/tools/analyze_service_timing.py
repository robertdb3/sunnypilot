#!/usr/bin/env python3
import collections
import glob
import json
import math
import statistics
import sys

from openpilot.tools.lib.logreader import _LogFileReader


SERVICES = ["carState", "carControl", "controlsState", "selfdriveState", "selfdriveStateSP",
            "modelV2", "cameraOdometry", "radarState", "longitudinalPlan", "longitudinalPlanSP",
            "driverAssistance", "liveTracks"]


def pct(values, q):
  if not values:
    return math.nan
  return sorted(values)[min(len(values) - 1, int(len(values) * q))]


def main():
  files = sorted(path for pattern in sys.argv[1:] for path in glob.glob(pattern))
  last = {}
  gaps = collections.defaultdict(list)
  comm_times = []
  car = collections.Counter()
  controls = collections.Counter()
  icbm = collections.Counter()
  alerts = collections.Counter()
  combos = collections.Counter()
  latest_car = None
  latest_control = None

  for fn in files:
   for m in _LogFileReader(fn):
    which = m.which()
    t = m.logMonoTime / 1e9
    if which in SERVICES:
      if which in last:
        gaps[which].append(t - last[which])
      last[which] = t
    if which == "logMessage":
      try:
        obj = json.loads(str(m.logMessage))
        payload = obj.get("msg", obj)
        if payload.get("event") == "commIssue":
          comm_times.append((t, payload))
      except Exception:
        pass
    elif which == "carState":
      c = m.carState
      latest_car = (bool(c.cruiseState.available), bool(c.cruiseState.enabled))
      car[(bool(c.cruiseState.available), bool(c.cruiseState.enabled),
           round(c.cruiseState.speedCluster * 2.236936, 1), round(c.vEgo * 2.236936, 1))] += 1
    elif which == "carControl":
      c = m.carControl
      latest_control = (bool(c.enabled), bool(c.latActive), bool(c.longActive))
      controls[(bool(c.enabled), bool(c.latActive), bool(c.longActive))] += 1
    elif which == "selfdriveStateSP":
      c = m.selfdriveStateSP.intelligentCruiseButtonManagement
      icbm[(str(c.state), str(c.sendButton), round(c.vTarget, 1), round(c.vCruiseCluster, 1))] += 1
      combos[(round(c.vTarget, 1), round(c.vCruiseCluster, 1), str(c.state), str(c.sendButton),
              latest_car, latest_control)] += 1
    elif which == "selfdriveState":
      s = m.selfdriveState
      if s.alertText1 or s.alertText2:
        alerts[(s.alertText1, s.alertText2)] += 1

  print("TIMING")
  for service in SERVICES:
    values = gaps[service]
    if values:
      print(service, "n", len(values), "mean", round(statistics.mean(values), 4),
            "p95", round(pct(values, .95), 4), "p99", round(pct(values, .99), 4),
            "max", round(max(values), 4), "gt70ms", sum(v > .07 for v in values),
            "gt100ms", sum(v > .1 for v in values))
  print("COMM")
  for item in comm_times: print(item)
  print("CAR")
  for key, count in car.most_common(): print(count, key)
  print("CONTROLS", controls)
  print("ICBM")
  for key, count in icbm.most_common(): print(count, key)
  print("COMBOS")
  for key, count in combos.most_common(): print(count, key)
  print("ALERTS", alerts)


if __name__ == "__main__":
  main()
