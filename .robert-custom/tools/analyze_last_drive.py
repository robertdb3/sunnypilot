#!/usr/bin/env python3
import collections
import glob
import json
import os

from openpilot.tools.lib.logreader import _LogFileReader


ROUTE_GLOB = os.environ.get("ROUTE_GLOB", "/data/media/0/realdata/00000017--c0343146c1--*/rlog.zst")


def enum(value):
  return str(value).split(".")[-1]


def main():
  files = sorted(glob.glob(ROUTE_GLOB))
  print("segments", len(files), *(os.path.basename(os.path.dirname(f)) for f in files))
  comm = collections.Counter()
  alerts = collections.Counter()
  plan = collections.Counter()
  icbm = collections.Counter()
  cruise_transitions = []
  button_events = collections.Counter()
  process_bad = collections.Counter()
  raw_comm_samples = []
  available_last = None

  for fn in files:
    segment = os.path.basename(os.path.dirname(fn)).rsplit("--", 1)[-1]
    for m in _LogFileReader(fn):
      which = m.which()
      if which == "logMessage":
        raw = str(m.logMessage)
        if any(term in raw for term in ("commIssue", "processNotRunning", "modeldLagging", "crash")):
          if len(raw_comm_samples) < 10:
            raw_comm_samples.append(raw[:1200])
          try:
            obj = json.loads(raw)
            payload = obj.get("msg", obj)
            key = (segment, payload.get("event"), tuple(payload.get("invalid", [])),
                   tuple(payload.get("not_alive", [])), tuple(payload.get("not_freq_ok", [])))
          except Exception:
            key = (raw[:500],)
          comm[key] += 1
      elif which == "selfdriveState":
        s = m.selfdriveState
        if s.alertText1 or s.alertText2:
          alerts[(s.alertText1, s.alertText2, enum(s.alertStatus))] += 1
      elif which == "longitudinalPlanSP":
        p = m.longitudinalPlanSP
        r = p.speedLimit.resolver
        a = p.speedLimit.assist
        key = (round(p.vTarget, 2), enum(p.longitudinalPlanSource), round(r.speedLimit, 2),
               bool(r.speedLimitValid), enum(r.source), enum(a.state), bool(a.enabled), bool(a.active))
        plan[key] += 1
      elif which == "selfdriveStateSP":
        s = m.selfdriveStateSP
        c = s.intelligentCruiseButtonManagement
        key = (enum(c.state), enum(c.sendButton), round(c.vTarget, 1), round(c.vCruiseCluster, 1),
               bool(s.mads.enabled), bool(s.mads.active))
        icbm[key] += 1
      elif which == "carState":
        c = m.carState
        available = bool(c.cruiseState.available)
        if available != available_last:
          cruise_transitions.append((segment, round(m.logMonoTime / 1e9, 3), available,
                                     bool(c.cruiseState.enabled), round(c.vEgo, 2)))
          available_last = available
        for b in c.buttonEvents:
          button_events[(enum(b.type), bool(b.pressed))] += 1
      elif which == "managerState":
        for p in m.managerState.processes:
          if p.shouldBeRunning and (not p.running or p.exitCode != 0):
            process_bad[(p.name, bool(p.running), p.exitCode)] += 1

  print("\nCOMM")
  for key, count in comm.most_common(): print(count, key)
  print("\nRAW_COMM_SAMPLES")
  for sample in raw_comm_samples: print(sample)
  print("\nALERTS")
  for key, count in alerts.most_common(): print(count, key)
  print("\nPLAN")
  for key, count in plan.most_common(): print(count, key)
  print("\nICBM")
  for key, count in icbm.most_common(): print(count, key)
  print("\nCRUISE_TRANSITIONS")
  for item in cruise_transitions: print(item)
  print("\nBUTTON_EVENTS", button_events)
  print("\nPROCESS_BAD", process_bad)


if __name__ == "__main__":
  main()
