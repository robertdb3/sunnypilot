# openpilot longitudinal on a preglobal Subaru: why it is off, and what it would take

`openpilotLongitudinalControl` is false on this 2018 Outback. EyeSight owns throttle and braking;
sunnypilot only steers. Nearly every longitudinal frustration in
[`device-journey-runbook.md`](device-journey-runbook.md) descends from that one fact — ICBM exists
only because openpilot cannot command speed directly (symptom 4), the SCC-V/SCC-M tags do nothing
because the planner has no throttle to move (symptom 8), and the fine-step attempt that crash-looped
`card` was an attempt to press buttons more precisely (symptom 10).

This note records why it is disabled, and what changing it would actually involve.

**The headline, because it is not what I expected going in:**

> The preglobal platform carries a **complete longitudinal command surface**, structurally identical
> to the global platform that openpilot longitudinal already drives. It is disabled by policy and by
> missing safety code — not by missing hardware capability.

That is a statement about *feasibility*, not about advisability. Read §6 before getting excited.

---

## 1. The capability exists

Preglobal EyeSight commands the car through three messages. Global uses the same three, one address
block higher, with the same signal semantics:

| Role | Preglobal | Global | Key signals |
|---|---|---|---|
| Brake | `ES_Brake` **0x160** | 0x220 | `Brake_Pressure` (16-bit), `Cruise_Brake_Active`, `Cruise_Activated`, `Cruise_Brake_Lights` |
| Throttle | `ES_Distance` **0x161** | 0x221 | `Cruise_Throttle` (12-bit), `Car_Follow`, `Close_Distance`, `Standstill`, `Cruise_Button` |
| RPM | `ES_Status` **0x162** | 0x222 | `Cruise_RPM` (16-bit), `Cruise_Activated`, `Brake` |

Source: `opendbc/dbc/generator/subaru/_subaru_preglobal_2015.dbc`. The 2018 Outback resolves to
`subaru_outback_2019_generated`, which `IMPORT`s that file.

openpilot's global longitudinal drives precisely `Cruise_Throttle`, `Cruise_RPM` and
`Brake_Pressure`. Every signal it needs exists here under the same name.

## 2. It is switched off in four independent places

Each is a real gate. Removing any one alone accomplishes nothing.

**(a) The availability flag.** `opendbc/car/subaru/interface.py:90`:

```python
ret.alphaLongitudinalAvailable = not (ret.flags & (SubaruFlags.GLOBAL_GEN2 | SubaruFlags.PREGLOBAL |
                                                   SubaruFlags.LKAS_ANGLE | SubaruFlags.HYBRID))
ret.openpilotLongitudinalControl = alpha_long and ret.alphaLongitudinalAvailable
```

`PREGLOBAL` is excluded, so no toggle anywhere in the UI can make this true.

**(b) The controller throws the values away.** `carcontroller.py:65-77` computes
`cruise_throttle`, `cruise_rpm` and `cruise_brake` from `actuators.accel` **unconditionally** — that
code runs on your car today. Then the preglobal branch at line 97 sends only:

```python
can_sends.append(subarucan.create_preglobal_es_distance(self.packer, cruise_button, CS.es_distance_msg))
```

`create_preglobal_es_distance` copies every signal verbatim from the camera's message, overrides
`Cruise_Button`, and recomputes the checksum. `Cruise_Throttle` is passed straight through, so the
computed longitudinal values are simply discarded. The global branch below (lines 102-117) hands
them to `create_es_status` and `create_es_brake`. **The plumbing is already there and already runs;
preglobal just has nothing consuming it.**

**(c) Panda would reject the transmission.** `opendbc/safety/modes/subaru_preglobal.h` permits only
two TX messages:

```c
#define SUBARU_PG_COMMON_TX_MSGS \
  {MSG_SUBARU_PG_ES_Distance, SUBARU_PG_MAIN_BUS, 8, .check_relay = true}, \
  {MSG_SUBARU_PG_ES_LKAS,     SUBARU_PG_MAIN_BUS, 8, .check_relay = true},
```

`ES_Brake` (0x160) is not in the list, so panda blocks it. The preglobal `tx_hook` checks **steering
only** — no accel limits, no brake checks — and `subaru_preglobal_init` never reads
`SUBARU_PARAM_LONGITUDINAL`, so the `SubaruSafetyFlags.LONG` bit that `interface.py:98` would set is
simply ignored here.

**(d) CarState never parses the messages.** `carstate.py` captures `es_brake_msg` and
`es_status_msg` only inside `if not (self.CP.flags & SubaruFlags.PREGLOBAL)`. Preglobal reads
`ES_Distance` (for `Cruise_Button` and `Cruise_Fault`) and nothing else longitudinal.

## 3. The relay, and why bus 2 is the interesting one

`check_relay = true` does not mean "check something". In `safety_fwd_hook`, a TX entry with
`check_relay` **statically blocks** the camera's copy of that message from being forwarded, so
openpilot's version is the only one the car ever sees. That is why ICBM works at all: the ACC ECU
receives `Cruise_Button` solely because the carcontroller echoes it.

Consequently, right now:

- `ES_Distance` — camera's copy **blocked**; openpilot rebuilds it (copying `Cruise_Throttle`
  through unchanged) and adds the button.
- `ES_LKAS` — camera's copy **blocked**; openpilot steers.
- `ES_Brake`, `ES_Status`, `ES_DashStatus`, `ES_LDW` — **forwarded untouched** from EyeSight.

This gives a free observation point. On **bus 2** you can watch what EyeSight *wants* to command
while it is genuinely driving the car; on **bus 0** you see what arrives. That is ground truth for
the scaling, obtainable with zero risk, and it is what
[`tools/log_es_longitudinal.py`](../tools/log_es_longitudinal.py) captures.

### A note on where the DBC comes from

No `*_generated.dbc` is committed — `opendbc/dbc/*_generated.dbc` is gitignored, and the prebuilt
device skips the build step entirely (`launch_chffrplus.sh:91`). The files are nonetheless
unnecessary: `opendbc/can/dbc.py` resolves a name through `get_generated_dbcs()`, which lazily
builds every generated DBC **in memory** on first use. So `CANParser("subaru_outback_2019_generated",
...)` works on the device with no file on disk. This is why the probe tool can construct parsers by
exactly the same route `carstate.py` does, and one more reason it only runs on the device.

## 4. What a real implementation would require

Not a sketch — this is the actual checklist, in dependency order.

1. **Measure the scaling** (§5). Everything below depends on numbers nobody has.
2. **Safety limits and TX checks** in `subaru_preglobal.h`: add `ES_Brake` (and likely `ES_Status`)
   to the TX list with `check_relay = true`; define a preglobal `LongitudinalLimits`; call
   `longitudinal_brake_checks` / `longitudinal_gas_checks` / `longitudinal_transmission_rpm_checks`
   in the tx hook; read a `SUBARU_PARAM_LONGITUDINAL` flag in `init`.
3. **Safety tests.** `opendbc/safety/tests/test_subaru_preglobal.py` already exists and would need
   the longitudinal cases. This is a coverage obligation, not a formality — it is the file that
   proves the panda cannot be made to command an unbounded brake.
4. **Custom panda firmware.** Safety code compiles *into* the panda. See §6.
5. **CAN construction**: `create_preglobal_es_brake`, `create_preglobal_es_status`, and extending
   `create_preglobal_es_distance` to override `Cruise_Throttle` instead of copying it. Checksums are
   already solved — `subaru_preglobal_checksum` exists.
6. **CarState**: parse `ES_Brake` / `ES_Status` on the preglobal path.
7. **Interface**: flip availability. Note sunnypilot already overrides comma's preglobal restriction
   in `_get_params_sp` (`interface.py:105` un-sets `dashcamOnly`, which is the only reason this car
   drives at all under sunnypilot) — so that hook is the designed place for a fork to do this,
   rather than patching comma's `_get_params`.
8. **Tuning**: re-derive the accel→signal lookups from §5's measurements.
9. **Staged bring-up** on the car, offroad first.

## 5. The tuning-constant problem

`CarControllerParams` ships these, and they are all derived from **global** cars:

| Constant | Value | Meaning |
|---|---|---|
| `THROTTLE_MIN` / `THROTTLE_MAX` | 808 / 3400 | approx 2 m/s² at max |
| `THROTTLE_INACTIVE` | 1818 | "zero acceleration" |
| `THROTTLE_ENGINE_BRAKE` | 808 | what EyeSight sets while braking |
| `RPM_MIN` / `RPM_INACTIVE` / `RPM_MAX` | 0 / 600 / 3600 | |
| `BRAKE_MIN` / `BRAKE_MAX` | 0 / 600 | "about -3.5 m/s² from testing" |

Whether any of these transfer to preglobal is **unmeasured**. `Cruise_Throttle` is 12-bit on both
platforms, which is suggestive. `Brake_Pressure` is less reassuring: preglobal declares it 16-bit
with a stated range of `[0|255]`, while global declares the same signal 16-bit with `[0|65535]`.
DBC min/max are advisory metadata — the parser does not clamp to them — so this is not a functional
contradiction, but it does mean preglobal's stated maximum is almost certainly an unverified
placeholder from whoever reverse-engineered the message. The usable range is genuinely unknown.

Adopting global's numbers unverified would be the same mistake as symptom 14, one tier more
dangerous: there, an untested assumption crashed the UI; here it would command the brakes.

**Status: not yet measured.** The tool is written and verified; the device was powered down when
this note was written. See §8 for the exact command. Results belong in this section.

## 6. The safety line — tradeoffs, no decision

Longitudinal control cannot be done without changing panda safety code. That has three consequences
worth stating plainly, so the decision is made with them in view rather than around them.

**This fork forbids it today, deliberately.** `verify_candidate.py` `PROTECTED_PREFIXES` blocks
`opendbc_repo/opendbc/safety/` and `panda/` outright, and the build fails on any change there. That
rule is why every patch so far has been in the "userspace mistakes are recoverable" tier (trap 14),
and it is the reason a bad patch has so far only ever cost a stuck UI rather than a control event.

**The firmware is prebuilt and signed.** `panda/board/obj/*.bin.signed` ships in the staging branch.
Safety code is compiled into it, so a change means building and flashing custom panda firmware, and
the device would then be running non-stock safety code. The rollback story gets materially worse:
today a bad candidate is fixed by reverting a Python patch, as on 2026-08-18. With custom safety
firmware, recovery may mean reflashing the panda — potentially in a car that will not drive.

**The test obligation is real.** opendbc's safety suite is not a formality; it is the mechanism by
which "the panda will refuse an unsafe command" is a fact rather than a hope. Anything less than
full coverage for the new limits is not a shortcut, it is removing the backstop.

Against that: the work is *bounded and well-understood*. The global implementation is a working
template, the message surface is identical, and the failure modes are the ones opendbc's limits
framework was built to constrain.

No recommendation is recorded here on purpose.

## 7. Prior art, and why it is still undone

- **comma deprecated the platform.** Preglobal is `dashcamOnly` upstream (`interface.py:21`) — comma
  ships support but refuses to engage. sunnypilot un-sets that flag, which is why the car drives.
  Upstream has no appetite for preglobal longitudinal.
- **A funded bounty went unclaimed.** A [GoFundMe for pre-global Subaru openpilot longitudinal
  support](https://www.gofundme.com/f/preglobal-subaru-openpilot-longitudinal-support) raised
  **$2,550** against a $1,700 goal from 11 donors (Aug 2023), for a working upstream PR with two
  user confirmations. Three years on, the code still excludes preglobal. Strong evidence of effort
  required — not of impossibility.
- **The only real POC was global.**
  [PR #25345](https://github.com/commaai/openpilot/pull/25345) implemented Subaru longitudinal by
  rewriting `cruise_throttle` / `cruise_rpm` and a linear brake signal, tested on a **2018 Crosstrek**
  (a global car), and was closed as superseded. It also hit an instructive bug: a large multi-bit
  signal was mis-split and caused EyeSight faults — a reminder that these messages are checksummed,
  counted, and watched by an ECU that will fault if it dislikes what it sees.
- **sunnypilot's existing preglobal longitudinal work takes the opposite approach.**
  `opendbc/sunnypilot/car/subaru/stop_and_go.py` achieves stop-and-go by **deceiving** EyeSight —
  spoofing `Throttle` (0x140) and `Brake_Pedal` (0xD1) onto the *camera* bus so EyeSight believes
  conditions warrant a resume. Panda permits exactly those two extra TX messages under
  `SUBARU_PG_STOP_AND_GO_TX_MSGS`. It is worth understanding as a precedent: influence EyeSight
  rather than replace it, at a fraction of the risk. Whether more of the longitudinal goal can be
  reached that way is an open and much cheaper question than §4.

## 8. Next step

Measure before deciding anything. On the device, against drives that already happened — **no new
driving, nothing transmitted**:

```bash
PYTHONPATH=/data/openpilot /usr/local/venv/bin/python /tmp/log_es_longitudinal.py \
    --route-glob '/data/media/0/realdata/<route>--*/rlog.zst' --csv /tmp/es.csv
```

The tool must run **on the device**: its route path imports `openpilot.selfdrive.pandad`, whose
compiled extension is aarch64-Linux, so it cannot be exercised on a laptop or an x86 CI runner —
the same barrier documented in symptom 14. Copy it over with `scp` first; `.robert-custom/` is
maintenance-repo only and is not part of the installed tree.

It prints measured ranges beside the global constants, the real brake-pressure→deceleration mapping,
and a bus-2-versus-bus-0 consistency check. Those numbers turn §5 from a blank into a decision.

## Related

- [`device-journey-runbook.md`](device-journey-runbook.md) — symptoms 4, 8, 9, 10 are all
  consequences of longitudinal being unavailable
- [`architecture-orientation.md`](architecture-orientation.md) §5 — why this car is the awkward case
- [`decoupled-toggle.md`](decoupled-toggle.md) — the *lateral* equivalent of this question, and the
  same safety-tier conclusion
