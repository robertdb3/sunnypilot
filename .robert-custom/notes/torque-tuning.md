# Tuning lateral torque on the preglobal Outback

Your car used to have no lateral tune of its own — `substitute.toml` mapped
`SUBARU_OUTBACK_PREGLOBAL_2018` to `SUBARU_IMPREZA`, so it steered on a lighter, shorter car's
numbers. **`patches/0005` gives it its own entry.** Why that was necessary, and not just nicer:

| Platform | LAT_ACCEL_FACTOR | FRICTION |
|---|---|---|
| SUBARU_IMPREZA (what it borrowed) | **1.067** | 0.210 |
| SUBARU_IMPREZA_2020 | 2.135 | 0.153 |
| SUBARU_FORESTER | 2.342 | 0.111 |
| SUBARU_OUTBACK 2020-22 | 2.000 | 0.200 |
| **your car now** | **2.000** | **0.200** |

The feedforward is `lateral_accel / LAT_ACCEL_FACTOR`, so a factor half what it should be commands
about twice the steer torque — and the error scales with how hard you are turning. That is why
straight highway felt fine and bends overshot, cut in, and oscillated. Friction was already about
right; this was a one-number problem.

**2.0 is an informed starting point, not a measurement.** Its real job is described below.

**You don't have to guess at replacements.** openpilot runs `torqued`, which fits your car's real
`latAccelFactor` and `friction` from driving — commanded steer torque against measured lateral
acceleration. The process already runs on your device every drive.

The catch is that it's gated off for you:

```python
# selfdrive/locationd/torqued.py
ALLOWED_CARS = ['toyota', 'hyundai', 'rivian', 'honda', 'volkswagen']
self.use_params = CP.brand in ALLOWED_CARS and CP.lateralTuning.which() == 'torque'
```

Subaru is absent, so the values get computed every drive and then discarded. sunnypilot adds an
escape hatch in [`torqued_ext.py`](../sunnypilot/openpilot/sunnypilot/selfdrive/locationd/torqued_ext.py):
when **Enforce Torque Lateral Control** is on, `use_params` follows the **Self-Tune** toggle.

## Setup, offroad

| Setting | Set to | Why |
|---|---|---|
| Steering → **Enforce Torque Lateral Control** | ON | The master gate. Also unlocks Relaxed and Custom Tuning. Your car is already on torque control, so this changes nothing else. |
| Torque Lateral Control → **Self-Tune** | ON | Makes the learned values actually apply. |
| Torque Lateral Control → **Less Restrict Settings** | ON | See below — for your car this is not optional. |
| **Enable Custom Tuning** | OFF | For now. |
| **Manual Real-Time Tuning** | OFF | This *disables* Self-Tune (`use_params = False`). The two are alternatives, not complements. |
| Visuals → **Developer UI** | Bottom or Right | Shows **L.A.F.** and **FRIC.**, which turn green once `liveValid`. |

Then confirm it took:

```bash
python3 tools/torque_status.py
```

The headline line is `useParams`. Before the toggles it reads FALSE — learned values discarded.
After, TRUE.

## Why "Less Restrict Settings" is still needed

**The sanity bounds are a percentage of the offline value** — now 2.0 rather than the Impreza's
1.067, but still a window around a starting guess:

```python
min_lataccel_factor = (1.0 - factor_sanity) * offline_latAccelFactor
max_lataccel_factor = (1.0 + factor_sanity) * offline_latAccelFactor
```

Strict `factor_sanity` is 0.3, giving `[1.4, 2.6]`. Relaxed widens it to ±100%, giving `[0, 4.0]`.
The seed is a guess, so leave room around it — and if the truth still lands outside,
`torque_status.py` says so explicitly as `PINNED at the high bound`.

**The strict data requirements will also probably stall it.** Points are bucketed by commanded
steer torque across eight bands, needing `[100, 300, 500, 500, 500, 500, 300, 100]`, and are only
collected while **engaged above ~34 mph**. The outer bands are firm cornering at speed. Relaxed
drops them to `[1, 200, 300, 500, 500, 300, 200, 1]`.

## Driving to fill the buckets

Engaged, above 34 mph, with a spread of steering effort. **Curvy secondary roads at 40–55 mph are
what fills the outer buckets.** Motorway cruising will never fill them no matter how many hours you
drive — you'll sit at `liveValid: False` indefinitely and it will look broken.

Re-run `torque_status.py` after a drive. It prints per-bucket counts against both the strict and
relaxed minimums and names which band is starving, so you know whether to go find more corners or
whether you're done.

## The trap

The learned cache is keyed on `(carFingerprint, tuning type, friction, latAccelFactor, version)`.
Changing **Custom Tuning** changes the offline values, so it **invalidates the cache and restarts
learning from zero**. Pick a configuration and leave it alone while it accumulates.

## Why the anchor had to be fixed in the patch, not with sliders

torqued's sanity bounds are a percentage of the **offline** value. Anchored on the Impreza's 1.067,
even Relaxed capped learning at `2 x 1.067 = 2.134` — below the Forester (2.342) and barely at the
Impreza 2020 (2.135). Self-Tune would have climbed, **pinned against the ceiling, and stopped**,
looking like it had worked.

With the anchor at 2.0 the relaxed window becomes `[0, 4.0]`, which comfortably contains any
plausible value. That is the whole point of the seed: not to be correct, but to put the search
window somewhere the answer can be found. Self-Tune does the rest.

## When to touch the manual sliders

Probably never, now. Only if `torque_status.py` still reports the learned value pinned against a
bound with Relaxed on — which would mean the truth is more than 100% away from 2.0, and would be
surprising. If that happens, set **Enable Custom Tuning** ON with a better estimate (this
re-anchors the bounds again), leave **Manual Real-Time Tuning** OFF so Self-Tune keeps refining,
and accept that learning restarts.

Manual sliders with **Manual Real-Time Tuning** ON are for back-to-back feel tests on a road you
know. They are a way to explore, not a way to arrive at a number — Self-Tune measures, you can
only estimate.

## What to expect

The biggest change should be exactly where you reported the problem: mid-corner. Straight highway
should feel much the same, because the feedforward term is near zero there either way. If corners
improve but the car still wanders slightly on straights, that is friction, which Self-Tune fits
separately.

## Rough guide to what the numbers do

- **Lateral Acceleration Factor** — how much lateral acceleration the car produces per unit of
  steer torque. The feedforward divides by it, so **lower means more commanded torque**: too low
  is darty, overshooting and cutting into bends (the symptom the Impreza value caused here), too
  high is lazy, under-steering into corners and drifting wide.
- **Friction** — overcomes steering stiction near centre. Too low and it drifts within the lane
  before correcting; too high and it feels notchy on straights.
