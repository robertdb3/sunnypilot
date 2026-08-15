#!/usr/bin/env python3
"""Observe physical preglobal Subaru cruise-button codes on raw camera CAN.

This probe is strictly read-only: it subscribes to ``can`` and ``carState`` and never opens
``sendcan``. It determines whether shallow/deep SET and RES actions are distinct button values
or a duration-dependent use of the same value.

On the 2015 preglobal Subaru DBC, ES_Distance is address 0x161 (353), camera bus 2, and
Cruise_Button occupies the low three bits of byte 6.
"""
import argparse
import time
from dataclasses import dataclass, field

import cereal.messaging as messaging


MPH = 2.2369362920544
ES_DISTANCE = 0x161
CAMERA_BUS = 2
BUTTON_NAMES = {
  0: "released",
  1: "MAIN",
  2: "SET shallow",
  3: "SET deep",
  4: "RES shallow",
  5: "RES deep",
  6: "unknown 6",
  7: "unknown 7",
}


def decode_cruise_button(dat: bytes) -> int:
  """Decode Cruise_Button (48|3@1+) without requiring the DBC runtime."""
  if len(dat) < 7:
    raise ValueError(f"ES_Distance payload is only {len(dat)} bytes")
  return dat[6] & 0x7


@dataclass
class Press:
  started: float
  start_speed_mph: float | None
  codes: list[int] = field(default_factory=list)
  samples: int = 0

  def observe(self, code: int) -> None:
    self.samples += 1
    if not self.codes or self.codes[-1] != code:
      self.codes.append(code)


def fmt_speed(speed: float | None) -> str:
  return "unavailable" if speed is None else f"{speed:.1f} mph"


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--seconds", type=float, default=120.0)
  parser.add_argument("--bus", type=int, default=CAMERA_BUS)
  parser.add_argument("--address", type=lambda value: int(value, 0), default=ES_DISTANCE)
  args = parser.parse_args()

  can_sock = messaging.sub_sock("can", conflate=False, timeout=100)
  sm = messaging.SubMaster(["carState"])

  print("READ ONLY: listening to raw camera CAN; no messages will be transmitted.", flush=True)
  print(f"Watching address 0x{args.address:X} on bus {args.bus} for {args.seconds:.0f} seconds.", flush=True)
  print("While parked: make separate quick taps and deliberate holds of SET and RES.", flush=True)
  print("MAIN and following-distance presses are optional controls. Ctrl-C stops early.\n", flush=True)

  started = time.monotonic()
  current_speed_mph: float | None = None
  previous_speed_mph: float | None = None
  active: Press | None = None
  last_code = 0
  completed: list[tuple[float, Press, float | None]] = []
  frame_count = 0

  try:
    while time.monotonic() - started < args.seconds:
      sm.update(0)
      if sm.updated["carState"]:
        current_speed_mph = sm["carState"].cruiseState.speed * MPH
        if previous_speed_mph is not None and abs(current_speed_mph - previous_speed_mph) >= 0.25:
          delta = current_speed_mph - previous_speed_mph
          print(f"  set speed: {previous_speed_mph:.1f} -> {current_speed_mph:.1f} mph ({delta:+.1f})", flush=True)
        previous_speed_mph = current_speed_mph

      for message in messaging.drain_sock(can_sock, wait_for_one=True):
        now = time.monotonic()
        for frame in message.can:
          if frame.src != args.bus or frame.address != args.address:
            continue
          frame_count += 1
          code = decode_cruise_button(bytes(frame.dat))

          if code != last_code:
            elapsed = now - started
            print(f"{elapsed:7.3f}s  0x{args.address:X}  {last_code} -> {code}  {BUTTON_NAMES[code]}", flush=True)

          if code and active is None:
            active = Press(now, current_speed_mph)
          if code and active is not None:
            active.observe(code)
          if not code and active is not None:
            duration = now - active.started
            completed.append((duration, active, current_speed_mph))
            names = " -> ".join(f"{value} ({BUTTON_NAMES[value]})" for value in active.codes)
            speed_delta = None if active.start_speed_mph is None or current_speed_mph is None else current_speed_mph - active.start_speed_mph
            delta_text = "unavailable" if speed_delta is None else f"{speed_delta:+.1f} mph"
            print(f"           PRESS: {names}; {duration * 1000:.0f} ms / {active.samples} frames; "
                  f"set {fmt_speed(active.start_speed_mph)} -> {fmt_speed(current_speed_mph)} ({delta_text})\n", flush=True)
            active = None
          last_code = code
  except KeyboardInterrupt:
    print("\nStopped by user.", flush=True)

  print("\n--- probe summary ---", flush=True)
  print(f"Matched {frame_count} ES_Distance frames and {len(completed)} complete presses.", flush=True)
  if frame_count == 0:
    print("No matching frames: verify ignition is on and the configured camera bus/address.", flush=True)
  elif not completed:
    print("Frames were present, but no complete physical button press was observed.", flush=True)
  else:
    for index, (duration, press, end_speed) in enumerate(completed, 1):
      codes = "->".join(str(value) for value in press.codes)
      delta = None if press.start_speed_mph is None or end_speed is None else end_speed - press.start_speed_mph
      delta_text = "n/a" if delta is None else f"{delta:+.1f} mph"
      print(f"{index:2d}. codes {codes:<8}  {duration * 1000:5.0f} ms  {press.samples:3d} frames  set delta {delta_text}", flush=True)

  print("\nInterpretation: distinct 2/3 and 4/5 codes can support discrete coarse/fine actions.", flush=True)
  print("If a hold repeats only 2 or 4, fine adjustment instead needs a bounded multi-frame hold.", flush=True)


if __name__ == "__main__":
  main()
