# How far you can customize, and where the line actually is

Written for a 2018 Outback (`SUBARU_OUTBACK_PREGLOBAL_2018`) on sunnypilot `staging`, comma 3X.

Forking is not what gets a device banned. Comma ships the fork installer themselves. Three
specific surfaces are what matter, and they're written down in your own checkout at
[`docs/SAFETY.md`](../sunnypilot/docs/SAFETY.md):

> * Do not disable or nerf **driver monitoring**
> * Do not disable or nerf **excessive actuation checks**
> * If your fork modifies any of the code in `opendbc/safety/`:
>   * your fork cannot use the openpilot trademark
>   * your fork must preserve the full **safety test suite** and all tests must pass, including
>     any new coverage required by the fork's changes

Everything below is organized by which of those you're near.

Before flashing anything, run:

```bash
bash scripts/check-fork-compliance.sh /data/openpilot
```

---

## Start here: your lateral tune isn't your car's

Two findings specific to your platform, both worth more than any code you could write.

**Your torque tune is borrowed from an Impreza.**
[`torque_data/substitute.toml:84`](../sunnypilot/opendbc_repo/opendbc/car/torque_data/substitute.toml:84):

```toml
"SUBARU_OUTBACK_PREGLOBAL_2018" = "SUBARU_IMPREZA"
```

Nobody ever collected lateral data for the preglobal Outback, so it inherits the Impreza's.
Different mass, different steering ratio (yours is 20, from the preglobal Forester specs).

**NNLC would do the same thing.** There is no neural lateral model for your platform. The fuzzy
matcher in [`nnlc/helpers.py:47-61`](../sunnypilot/openpilot/sunnypilot/selfdrive/controls/lib/nnlc/helpers.py:47)
scores your fingerprint against all 114 available models:

```
0.731  SUBARU_LEGACY_PREGLOBAL     <- best match, below the 0.9 threshold
0.651  SUBARU_OUTBACK
0.583  SUBARU_IMPREZA_2020
```

Nothing clears 0.9, so it falls through to the same `substitute.toml` entry and loads the
Impreza model with `fuzzyFingerprint` set. Turning NNLC on isn't wrong, but know that it's
another car's model, not yours.

**So the highest-value knob you have is a slider in the settings menu**, not a patch:

Settings → sunnypilot → Steering → Torque Lateral Control

| Param | What it does |
|---|---|
| `TorqueParamsOverrideEnabled` | unlocks the two below |
| `TorqueParamsOverrideLatAccelFactor` | how hard it steers per unit of requested lateral accel (default 2.5) |
| `TorqueParamsOverrideFriction` | overcomes steering stiction; raise if it wanders in straights (default 0.1) |
| `LiveTorqueParamsToggle` | let openpilot learn these from your driving instead |

Change one at a time, drive the same road, compare. Zero ban surface.

---

## Tier 0 — already shipped as settings. No code, no risk.

Worth exhausting before writing anything.

**Your car specifically:** `SubaruStopAndGo` is a real toggle and your platform qualifies —
[`subaru.py:38-42`](../sunnypilot/openpilot/selfdrive/ui/sunnypilot/layouts/settings/vehicle/brands/subaru.py:38)
gates it on `not (GLOBAL_GEN2 | HYBRID)`, and preglobal is neither. Toggle it offroad.

**Steering feel** ([`steering.py`](../sunnypilot/openpilot/selfdrive/ui/sunnypilot/layouts/settings/steering.py),
`steering_sub_layouts/`)
- `BlinkerPauseLateralControl` + `BlinkerMinLateralControlSpeed` + `BlinkerLateralReengageDelay`
  — hand steering back when you signal, above a speed you pick
- `AutoLaneChangeTimer`, `AutoLaneChangeBsmDelay`, `RoadEdgeLaneChangeEnabled`
- `LateralJerkTorqueController`, `EnforceTorqueControl`
- MADS settings — steering mode on brake (remain active / pause / disengage), UEM

**Model & path** ([`settings/models.py`](../sunnypilot/openpilot/selfdrive/ui/sunnypilot/layouts/settings/models.py))
- Model Manager: download and swap driving models
- `CameraOffset` — shift the path if it tracks left or right of center
- `LagdToggle` / `LagdToggleDelay` — learned vs. fixed steer actuator delay
- `LaneTurnDesire` / `LaneTurnValue`

**UI** ([`settings/visuals.py`](../sunnypilot/openpilot/selfdrive/ui/sunnypilot/layouts/settings/visuals.py),
[`display.py`](../sunnypilot/openpilot/selfdrive/ui/sunnypilot/layouts/settings/display.py))
- `DevUIInfo` — live metrics panel, bottom / right / both. Best free debugging tool you have
- `ChevronInfo` — distance, speed, or time under the lead car marker
- `RocketFuel`, `ShowTurnSignals`, `HideVEgoUI`
- `OnroadScreenOffTimer`, `OnroadScreenOffBrightness`, screensaver

252 params exist in [`params_keys.h`](../sunnypilot/openpilot/common/params_keys.h) — more than
the UI exposes. Worth reading through once.

## Tier 1 — car port and UI code. Not protected. Go nuts.

This is where the ACC main toggle fix lives, and where button remapping, engagement behavior,
HUD work, and new car features belong.

- `opendbc_repo/opendbc/car/<brand>/` — carstate, carcontroller, CAN packing
- `opendbc_repo/opendbc/sunnypilot/car/` — fork additions (MADS, stop-and-go, per-brand ext)
- `openpilot/selfdrive/ui/` — everything on screen
- `openpilot/sunnypilot/` — fork userspace: NNLC, MADS state machine, model manager

None of it is a ban surface. The compliance script reports these as clear.

Practical notes: work against a route in cabana before touching the car, keep changes as a
patch file (sunnypilot updates overwrite the checkout), and remember `/data/openpilot` is a git
repo — `git diff` there is your source of truth for what you've actually changed.

## Tier 2 — `opendbc/safety/`. Allowed, but you inherit a real obligation.

Modifying panda safety is explicitly permitted. What it costs, from
[`safety/tests/test.sh`](../sunnypilot/opendbc_repo/opendbc/safety/tests/test.sh):

```bash
cd opendbc_repo/opendbc/safety/tests && ./test.sh
```

- the full suite must pass (`python -m unittest discover`)
- **100% line coverage is enforced** — `gcovr --fail-under-line=100`. Every new safety line
  needs a test. This is the part people underestimate.
- MISRA C:2012 separately: `tests/misra/test_misra.sh`
- and your fork can't use the openpilot trademark

This is not a warning against doing it — sunnypilot does it, that's where MADS lives, and they
carry [652 lines of MADS safety tests](../sunnypilot/opendbc_repo/opendbc/safety/tests/mads_common.py)
to hold up their end. It's a warning that it's a project, not an afternoon.

Concretely for you: the "steering off while ACC stays on" idea in
[`decoupled-toggle.md`](decoupled-toggle.md) needs
`opendbc/safety/modes/subaru_preglobal.h`, so it lands squarely here — and
`test_subaru_preglobal.py` has no MADS coverage today (no `MadsSafetyTestBase` mixin), so you'd
be writing the missing preglobal MADS safety tests as part of it. That's also exactly why it'd
be worth upstreaming rather than keeping local.

## Tier 3 — don't.

- `openpilot/selfdrive/monitoring/` — driver monitoring
- `openpilot/selfdrive/selfdrived/helpers.py` — `ExcessiveActuationCheck`

A ban here is loss of upload access to comma's servers. It is **permanent**, and comma says they
won't reverse it "even if the device is sold to a new owner." The device still drives; you lose
connect, prime, and route data.

## If you ever see a ban

Check `useradmin.comma.ai` — `uploads ignored` is the symptom.

A ban is not automatically proof you did something. In September 2024 a comma server-side change
accidentally banned a wave of sunnypilot users; comma reversed it automatically. If it happens
and your compliance check is clean, contact comma support with your dongle ID rather than
assuming you're at fault.

## Sources

- [`docs/SAFETY.md`](../sunnypilot/docs/SAFETY.md) in your checkout — the authoritative rule
- [openpilot Forks wiki](https://github.com/commaai/openpilot/wiki/Forks)
- [comma safety docs](https://docs.comma.ai/concepts/safety/)
- [sunnypilot terms](https://www.sunnypilot.ai/terms)
- [Sept 2024 accidental ban wave](https://commaguide.com/sunnypilot-users-mass-ban-heres-what-you-need-to-do)

Policy language can move. Re-read `docs/SAFETY.md` on whatever branch you're actually running.
