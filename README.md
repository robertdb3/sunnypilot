# Preglobal Outback longitudinal research

Step 1 of a feasibility study for openpilot longitudinal control on the 2018 Outback (`SUBARU_OUTBACK_PREGLOBAL_2018`).
The question was whether the car follows EyeSight's cruise requests closely enough that openpilot could substitute its own.
This branch has no shared history with the fork's other branches. It holds only this research.

## Data

`data/all.npz` holds 41 minutes of EyeSight cruise with no pedal input, taken from 49 segments of the 2026-10-18 drives
(`data/segments.txt`). Signals are resampled onto carState (100 Hz). Every array is float32 except `t`.

| key | source |
|---|---|
| `t`, `v`, `a`, `en`, `gas`, `brk`, `vset`, `stand` | carState: time, vEgo, aEgo, cruise enabled, pedals, set speed, standstill |
| `pitch` | carControl.orientationNED[1] |
| `es_bp`, `es_bact`, `es_bl`, `es_cact`, `es_bfault` | ES_Brake (0x160) as sent by EyeSight (bus 2) |
| `es_thr`, `es_d_bact`, `es_stand` | ES_Distance (0x161), bus 2 |
| `es_rpm`, `es_s_brake`, `es_s_act` | ES_Status (0x162), bus 2 |
| `fw_bp`, `fw_thr`, `fw_rpm` | the same three messages as forwarded to the car (bus 0, TX echo) |
| `eng_tq`, `whl_tq`, `rpm`, `thr_*`, `gx`, `brkpedal`, `cc_act` | car-side Engine, Throttle, G_Sensor, Brake_Pedal and CruiseControl |
| `seg` | segment id, used so lag shifts never cross a segment boundary |

The raw rlogs are not included. They contain GPS, and the full set is about 680 MB.

## Findings

- **Brake.** ES_Brake.Brake_Pressure against grade-corrected acceleration (`a + g*sin(pitch)`) gives r = 0.83 at a 0.25 s lag. The relationship is close to linear:
  25–50 → −0.5, 100–150 → −1.0, 200–300 → −1.7, 400–600 → −2.8, 600–1000 → −3.7 m/s².
  This matches global Subaru's `max_brake = 600 ≈ −3.5 m/s²`.
- **Throttle.** ES_Distance.Cruise_Throttle gives r = 0.74 at a 0.4 s lag. 808 is idle, and is also the value while braking.
  The acceleration a given value produces depends on speed. Near the top of the range it gives about +1.1 m/s² below 22 mph and about +0.3–0.6 m/s² at highway speed.
- **RPM.** ES_Status.Cruise_RPM gives r = 0.22, so it probably targets the CVT rather than acceleration.
- **Stop-and-go.** EyeSight brakes for 27% of cruise time and holds the car at standstill (about 66 s of engaged standstill in the sample).
- **Forwarding.** The panda forwards ES_Brake unchanged, which means it can also intercept it.
- **AEB.** No emergency-braking events appear in the data. ES_Brake has no undocumented bits that ever change.
  EyeSight also sends undecoded `0x22a` (20 Hz) and `0x325` (10 Hz). Neither correlates with the lead car or with speed.

Proposed design: blend rather than replace. Send `max(brake)` and `min(throttle)` of openpilot and EyeSight, so EyeSight's ACC and AEB stay as a floor.

## Reproducing

```
python -m venv venv && venv/bin/pip install numpy pycapnp cantools zstandard
venv/bin/python scripts/curves.py   # response curves from data/all.npz
venv/bin/python scripts/lag.py      # lag / correlation table
```

To rebuild `data/all.npz` from logs, copy segments into `logs/<segment>/rlog.zst`, for example from `comma@<device>:/data/media/0/realdata`.
Then run `scripts/runx.py` (extract) followed by `scripts/build.py`. `scripts/qscan.py` scans qlogs in `qlogs/` to find cruise-heavy segments.
The other two scripts probe raw CAN: `scripts/rawscan.py` lists camera-bus messages, and `scripts/undoc.py` looks at the undecoded `0x22a` and `0x325`.
The schemas in `schema/` were copied from the device build. `outback2019.dbc` is `_subaru_preglobal_2015.dbc` and `subaru_outback_2019.dbc` concatenated.
