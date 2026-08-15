#!/usr/bin/env python3
import collections
import statistics
import sys

from openpilot.tools.lib.logreader import _LogFileReader


WANTED = ("openpilot.selfdrive.ui.ui", "openpilot.selfdrive.controls.radard",
          "openpilot.selfdrive.controls.plannerd", "openpilot.selfdrive.modeld.modeld_tinygrad")


def main():
  prev_time = None
  prev_proc = {}
  prev_cpu = {}
  loads = collections.defaultdict(list)
  cores = collections.defaultdict(list)
  rows = []
  for m in _LogFileReader(sys.argv[1]):
    if m.which() != "procLog":
      continue
    t = m.logMonoTime / 1e9
    current_proc = {}
    for p in m.procLog.procs:
      cmd = " ".join(p.cmdline)
      for wanted in WANTED:
        if wanted in cmd:
          current_proc[wanted] = (p.pid, p.cpuUser + p.cpuSystem)
    current_cpu = {c.cpuNum: c.user + c.system + c.irq + c.softirq for c in m.procLog.cpuTimes}
    if prev_time is not None:
      dt = t - prev_time
      row = {"t": t}
      for name, (pid, value) in current_proc.items():
        if name in prev_proc and prev_proc[name][0] == pid:
          load = 100 * (value - prev_proc[name][1]) / dt
          loads[name].append(load)
          row[name] = load
      for cpu, value in current_cpu.items():
        if cpu in prev_cpu:
          load = 100 * (value - prev_cpu[cpu]) / dt
          cores[cpu].append(load)
          row[f"cpu{cpu}"] = load
      rows.append(row)
    prev_time, prev_proc, prev_cpu = t, current_proc, current_cpu

  for name in WANTED:
    vals = loads[name]
    print(name, "mean", round(statistics.mean(vals), 1), "max", round(max(vals), 1))
  for cpu in sorted(cores):
    vals = cores[cpu]
    print(f"cpu{cpu}", "mean", round(statistics.mean(vals), 1), "max", round(max(vals), 1))
  print("AROUND_COMM")
  for row in rows:
    if 488 <= row["t"] <= 504:
      print({k: round(v, 1) for k, v in row.items()})


if __name__ == "__main__":
  main()
