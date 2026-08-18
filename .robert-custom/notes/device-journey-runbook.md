# 2018 Outback + comma 3X customization runbook

This is the durable record of the August 2026 customization and debugging journey for this
comma 3X and 2018 Subaru Outback. It records not only what changed, but why each decision was
made, what failed, and which traps must not be repeated.

Use this file as the first stop before changing, rebasing, reinstalling, or troubleshooting this
device. The focused notes linked near the end contain deeper implementation detail.

## Known-good state

Last verified after a successful drive home with no issues:

| Item | Known-good value |
|---|---|
| Vehicle | 2018 Subaru Outback |
| openpilot platform | `SUBARU_OUTBACK_PREGLOBAL_2018` |
| Hardware | comma 3X / `tizi` |
| Fork | sunnypilot `staging`, v2026.003.000 family |
| Device repository | `/data/openpilot` |
| **Device commit currently installed** | `3d67803` — **not known-good.** Installed 2026-08-18; UI crash-loops with Scene3D on (symptom 14). Safe only with `Scene3D=0` |
| Known-good rollback target | `ce4db02231af6645dd71f7a2dc16839a4247d963` |
| Public maintenance repository | `https://github.com/robertdb3/sunnypilot` |
| Local public checkout | `~/projects/custompilot-public-shallow` |
| Candidate branch | `custompilot-staging` (generated and tested; not the normal install target) |
| Install branch *by design* | `custompilot-stable` (manual, protected promotion only) |
| **Branch the device actually tracks** | `custompilot-staging` — since the symptom-10 recovery. Candidates reach this car **without** promotion review. Verify before relying on the protected-promotion model |
| Installer URL | `https://install.sunnypilot.ai/fork/robertdb3/custompilot-stable` |
| Device address during this work | `fe80::20a:f5ff:feaf:4679%en0` (link-local and not permanent) |
| Last rollback validation | Known-good tree restored; `card` and `controlsd` running; public staging/stable driving code matched the restored device tree |

Published branch state as of 2026-08-17, recorded because the candidate has moved ahead of both
stable and the device:

| Branch | Commit | Upstream base | Relationship to the device |
|---|---|---|---|
| `custompilot-stable` | `08e9e98` | `30a9cdc` | Install target; driving code matched the restored device tree |
| `custompilot-staging` | `3d67803` | `37d6dc5` | **Installed on the device 2026-08-18** and crash-looped the UI (symptom 14). Superseded by the next build, which excludes `0011` |

This table goes stale every time the daily build publishes. Re-derive it rather than trusting it,
and note that the candidate's parent is the *stable tip*, not the previous candidate (trap 15), so
successive candidates are siblings:

```bash
git fetch
git rev-parse --short origin/custompilot-staging origin/custompilot-staging^ origin/custompilot-stable
git show origin/custompilot-staging:CUSTOM_FORK_MANIFEST.json | python3 -c 'import json,sys; print(json.load(sys.stdin)["upstream"]["commit"][:12])'
```

A bare `git fetch` is correct here **only** if the two extra refspecs are configured; otherwise see
trap 22 before believing the output.

The candidate carries an upstream jump the device has never run—not just the local customization
changes. Confirm the actual delta before promoting rather than assuming it is limited to your own
patches:

```bash
git fetch origin
git diff origin/custompilot-stable origin/custompilot-staging -- . ':(exclude)CUSTOM_FORK_MANIFEST.json'
```

The IP address and git hashes will change after future updates. The comma was also seen at
`172.20.10.3` on the iPhone hotspot, while IPv6 link-local was the reliable connection. Identify
the device and inspect its actual checkout again rather than treating either address as permanent.
Software reinstalls can regenerate the device's SSH host identity, so verify a changed fingerprint
out of band and refresh the saved host key deliberately; never disable host-key checking globally.

## The architectural facts that drive every decision

### This is a preglobal Subaru

The 2018 Outback is `SUBARU_OUTBACK_PREGLOBAL_2018`, not the global 2020+ platform. It has no
separate LKAS button path in sunnypilot. ACC main is therefore the existing MADS lateral-control
interface on this car.

Both userspace MADS and panda safety observe the latched `Cruise_On` state. Main off drops lateral
authority; main on requests it again. The implemented toggle deliberately preserves that design.
It does **not** provide "ACC on, steering off."

A genuinely decoupled steering toggle would require a new verified button signal, userspace MADS
changes, and panda safety changes with full safety tests. See
[`decoupled-toggle.md`](decoupled-toggle.md). Do not attempt it as a casual Python-only patch.

### Longitudinal control remains stock EyeSight

`openpilotLongitudinalControl` is false on this platform. EyeSight controls throttle, braking, and
following distance. sunnypilot steers and may simulate stock cruise-button presses through ICBM.

This distinction explains the cruise labels:

- **SCC-V** is the vision-derived curve-speed candidate.
- **SCC-M** is the map-derived curve-speed candidate.
- **Speed Limit Assist** is the resolved posted-limit candidate.
- With openpilot longitudinal, the planner selects the lowest speed target among cruise, SCC-V,
  SCC-M, and Speed Limit Assist.
- On this stock-longitudinal Subaru, SCC-V and SCC-M may display active tags, but they do not press
  the stock cruise buttons. ICBM intentionally follows only a valid resolved speed limit.

That last rule is safety-critical. Feeding the general planner target to ICBM caused the stock set
speed to climb toward its unset 90 mph ceiling.

### The installed branch is prebuilt and is not identical to the source clone

The device shipped with `prebuilt` and without the normal build graph (`SConstruct` files were
absent). Native libraries such as `libparams_c.so` were already compiled. Editing a C/C++ header
does not mean the running native library changed.

The local `sunnypilot/staging` clone also moved ahead of the exact device snapshot during this
work. Examples of real incompatibilities encountered:

| Newer source name/API | Installed device name/API |
|---|---|
| `liveParameters` | `vehicleParameters` |
| `liveTorqueParameters` | `lateralTorqueParameters` |
| `liveDelay` | `lateralDelay` |
| `PythonProcess(..., restart_if_crash=True)` supported | `PythonProcess` rejects that keyword |

Therefore:

1. Treat `/data/openpilot` as the authority for device APIs and schemas.
2. Generate incremental patches against the exact device parent commit.
3. Never copy a whole newer-source file onto the device without comparing it to the installed
   version first.
4. When UI code spans nearby staging generations, support both service names explicitly.

There is a second prebuilt boundary: runtime Cap'n Proto source and the Python/native objects made
from it are not automatically one unit on this image. A schema file may load successfully through
`pycapnp` while a prebuilt dependency still exposes an older generated Python dataclass. Source
compilation and schema-resolution tests alone therefore do not prove that constructing the actual
runtime message object will work. Exercise the installed producer/consumer path on the device.

## What was installed, in order

The patch order matters because later patches harden or correct earlier behavior.

| Patch | Purpose | Why it exists |
|---|---|---|
| `0001` | Make ACC main a latching lateral toggle | The stock preglobal controller re-pressed main whenever it saw main off, undoing the driver's press within about 50 ms. |
| `0002` | Offline vector map inset | Reuses mapd's downloaded OSM geometry; avoids raster tiles, API keys, and network dependence. |
| `0003` | Tesla-style 3D scene | Replaces the camera visualization when enabled; draws lanes, road edges, path, leads, and blind spots. |
| `0004` | Preglobal ICBM cruise-button output | Lets a valid speed limit adjust stock EyeSight's set speed through real button semantics. |
| `0005` | Outback-specific torque anchor | Stops this heavier Outback from inheriting the Impreza's `1.067` lateral-acceleration factor; uses `2.0 / 0.2` as the learning anchor. |
| `0006` | Virginia reckless-speed watch | Adds geofenced absolute and 20-over reminders, plus visual border and calibration tooling. |
| prebuilt compatibility port | Register custom setting metadata without rebuilding unavailable native code | Keeps map/scene/watch settings usable across manager transitions on this prebuilt snapshot. |
| ~~`0007`~~ | ~~Fix tinygrad acceleration contract~~ | **Retired 2026-08-17.** Corrected a scalar/tuple mismatch that broke model output and produced `posenet speed invalid` / `nan m/s`. Upstream fixed the same defect in `37d6dc5`, more completely; see symptom 12. |
| `0008` | Fail-closed ICBM and main-button arbitration | Prevents ICBM from following the 90 mph planner fallback and preserves a physical driver main press over the cancel it causes. |
| `0009` | Move `radard` and `plannerd` to CPU 6 | Isolates the planning chain from the custom UI load that saturated CPU 5 and caused low communication-rate alerts. |
| `0010` | Fix the mirrored 3D frame and developer UI crashes | Corrects Forward/Right/Down model coordinates, tolerates both cereal *service* names, and avoids redundant model-array conversions. Trimmed on 2026-08-16: the `liveValid` field fallback was removed once upstream fixed that read at the source (symptom 11). |
| `0011` | Refine the 3D scene | Temporal smoothing, far-field dissolve, scrolling dashes, redesigned blind spot, damped camera. Procedural ego car only (no vendored mesh). Slice defect fixed 2026-08-18; back in the build, **still awaiting on-device validation**. See symptom 14. |

The canonical patch files live in [`patches/`](../patches). The prebuilt compatibility patch lives
under [`ports/`](../ports).

### Reproducibility audit

On 2026-08-15, the complete stack was reconstructed in a temporary clean worktree starting at
device base `30a9cdc`. Patches `0001` through `0006`, the `staging-30a9cdc` compatibility port,
and patches `0007` through `0010` all passed `git apply --check`, applied in that order, and passed
`git diff --check`. The final tracked stack touches 37 source files (the earlier audit note said 29
because it counted collapsed paths rather than expanding every added file inside the new package
directories). Generate the authoritative list from the patch headers instead of maintaining it by
hand:

```bash
rg '^diff --git a/' patches ports | sed -E 's#.*diff --git a/([^ ]+) b/.*#\1#' | sort -u
```

On 2026-08-16 the same audit was repeated against the newer upstream base `5b4820d` after the drift
described in symptom 11. Patches `0001` through `0006`, the `staging-30a9cdc` port, and `0007`
through `0010` again applied in order against a fresh clone, once patch `0010` was corrected. Re-run
this audit against the exact upstream tip whenever the candidate build fails to apply.

On 2026-08-17 the audit was repeated against upstream `37d6dc5` after the drift described in
symptom 12, this time with `0007` **removed**: `0001` through `0006`, the `staging-30a9cdc` port,
then `0008` through `0010` all applied in order against a fresh worktree, and `git diff --check` was
clean. The stack now touches 36 source files—one fewer than the 2026-08-15 count, because
`modeld.py` was `0007`'s only file and no other patch touches it. Note that the audit is only as
current as its named
base—upstream moved on two consecutive days here, so a green audit dated yesterday proves nothing
about today (trap 21).

Repeated later on 2026-08-17 with `0011` added to the applied set and its car-mesh asset removed: all
eleven items applied in order against a fresh worktree at `37d6dc5`, `git diff --check` was clean, and the stack
touches **38** source files. `0011` was checked for the constraints that have bitten before—it touches no
`.capnp`, no `opendbc`, no `params_keys.h`, and `scene3d` calls `params.get_bool` nowhere, taking its only
toggle from the pre-existing `BlindSpot` key via `ui_state`.

Every intentional source difference in the local patched checkout is represented by at least one
tracked patch. The only unmatched local files were generated `__pycache__/*.pyc` bytecode, which
must not be committed. The `sunnypilot/` working clone is intentionally gitignored by the outer
repair repository; the tracked patch stack—not that dirty scratch checkout—is the reproducible
source of truth.

## Symptom-to-cause history

### 1. ACC main turned steering off and immediately back on

**Observed:** pressing cruise main produced only a brief lateral disengagement.

**Cause:** the preglobal `carcontroller` sent a mocked main press whenever `Cruise_On` was false
and EyeSight was ready. It could not distinguish a driver-requested off state from a state that
openpilot had temporarily caused.

**Resolution:** `PreglobalMainCruise` attributes transitions and remembers when the driver turned
main off. Automatic restore is allowed only after openpilot's own cancellation sequence.

**Important limitation:** main off disables both ACC availability and steering. That is honest for
the factory control and avoids a panda/userspace disagreement.

### 2. Custom settings appeared offroad, then disappeared in the car

**Observed:** the map, 3D scene, and Virginia-watch options existed after installation but vanished
after an ignition/manager transition; ICBM could also appear unavailable.

**Cause:** custom parameter keys were added to `params_keys.h`, but the prebuilt native Params
library could not be rebuilt. Direct reads returned `UnknownKeyName`. Manager startup and
transition clears also removed values whose metadata was incomplete.

**Resolution:** use the prebuilt compatibility layer for Python type/default metadata and preserve
the values across the relevant clears. Do not assume editing `params_keys.h` changed
`libparams_c.so`.

**Trap:** an unguarded `get_bool("Scene3D")` on an unregistered key can crash the UI. Unknown keys
must fail closed until compatibility is installed and verified.

### 3. `posenet speed invalid`, `speed error: nan m/s`

**Observed:** cruise could not engage after the custom feature stack was installed.

**Cause:** the staging tinygrad model runner expected a different return contract from
`get_accel_from_plan`. The mismatch stopped valid model/camera-odometry output, so downstream
speed estimates became NaN.

**Resolution:** patch `0007` restores the scalar contract expected by this snapshot. This was a
model pipeline defect, not a wheel-speed calibration problem.

**Since retired:** upstream fixed this itself in `37d6dc5` and the patch was dropped on 2026-08-17.
The device tree at `ce4db02` still predates that fix, so this history stays relevant to any rollback
target older than `37d6dc5`. See symptom 12.

### 4. ICBM attempted to raise the set speed toward 90 mph

**Observed:** ICBM repeatedly increased the stock set speed, including toward the configured
maximum.

**Cause:** it followed `LongitudinalPlanSP.vTarget`. That field is the general planner target and
falls back to the cruise ceiling when no valid speed limit is available.

**Resolution:** ICBM now reads only `speedLimit.resolver.speedLimitFinal` and requires all of:

- resolver validity;
- a non-`none` source;
- a finite, positive limit;
- openpilot enabled with stock ACC ready;
- no driver override, cancel/resume command, or physical cruise-button press.

If any prerequisite is missing, it sends no buttons. Fail closed.

### 5. The lateral toggle disappeared again after ICBM hardening

**Observed:** cruise operation returned, but physical main no longer reliably latched steering off.

**Cause:** a physical main press causes stock cruise to deactivate, which can simultaneously create
an openpilot cancel request. Without attribution, the synthetic cancel/main press could win over
the driver's input.

**Resolution:** patch `0008` gives the physical driver's transition priority and suppresses the
restore sequence for a driver-requested main-off state.

### 6. Repeated low communication-rate takeovers

**Observed:** `TAKE CONTROL IMMEDIATELY` with low communication rate between processes.

**Evidence:** route analysis isolated `radarState` timing. `radard` averaged about 18.7 Hz with
gaps instead of its expected 20 Hz. The custom UI used roughly one full CPU and shared CPU 5 with
lower-priority planning processes, while CPU 6 was nearly idle.

**Resolution:** patch `0009` moves `radard` and `plannerd` together to CPU 6 at their existing
realtime priority. Live parked verification showed `radarState` back at 20.00 Hz.

**Why both moved:** radar fusion and planning form a model-synchronous chain. Separating one while
leaving the other under UI contention would move rather than remove the bottleneck.

### 7. One later critical takeover and a mirrored 3D scene

**Observed:** after a mostly successful long drive, one critical `TAKE CONTROL IMMEDIATELY`
occurred. The synthetic scene showed left lanes on the right and reversed road bends.

**Route evidence:** the critical alert was `Driving Model Lagging`, with approximately 1.1-1.3%
dropped frames. In the same route, the UI crashed while rendering the developer panel because it
read a nonexistent `liveValid` member from the installed torque schema.

**Mirror cause:** the renderer incorrectly documented model output as Forward/Left/Up. sunnypilot's
coordinate reference defines model/calibrated output as **Forward/Right/Down**. Both lateral and
vertical signs were wrong.

**Resolution in `0010`:**

- model `y` maps directly to raylib world-right;
- model `z` is negated to raylib world-up;
- shoulders, lane lifts, and blind-spot sides follow the corrected convention;
- geometry tests assert that ahead-left stays ahead-left and model-up stays world-up;
- developer elements accept both `vehicleParameters`/`liveParameters` and
  `lateralTorqueParameters`/`liveTorqueParameters`;
- model arrays are converted only on a new 20 Hz `modelV2` message, not rebuilt on every 60 Hz UI
  frame.

The return drive after this change had no alerts or UI problems.

### 8. SCC tags appeared but did not slow stock EyeSight

**Observed:** SCC-V and SCC-M flashed near curves, but the Subaru's displayed ACC set speed did not
change and braking was still required.

**Cause:** those tags describe planner candidates. This car has stock longitudinal control, so the
planner cannot directly command EyeSight acceleration or braking. ICBM is the separate bridge that
simulates cruise-button presses, and its safe implementation consumes only a valid resolved posted
speed limit—not SCC-V/SCC-M or the general planner target.

Turning Speed Limit Assist off does not make SCC automatically take over ICBM. Wiring curve targets
into ICBM is a separate feature requiring validation of target arbitration, button timing, minimum
speed, rapid target changes, and driver override behavior. It remains unimplemented.

### 9. Subaru cruise-button probe settled coarse versus fine behavior

Parked and controlled on-car probing established that this preglobal Subaru does not use different
CAN button codes for coarse and fine adjustment:

- both actions use codes `2`/`4` for accel/decel;
- a short press of roughly 100-150 ms performs the coarse 5 mph snap;
- a sustained hold performs the 1 mph action;
- across six trials, the first 1 mph change occurred about 0.806-0.856 seconds into the hold;
- codes `3`/`5` were never observed.

Therefore a one-frame "fine" code is not a solution. Any future exact-mph ICBM implementation must
hold the normal button for a bounded duration, yield immediately to physical driver input, and be
tested against the stock ECU. The desired future setting is a dedicated offroad toggle: default
OFF preserves 5 mph snapping and the existing 14% speed-limit offset behavior; ON would permit
exact 1 mph landing. That toggle and bounded-hold behavior are **not installed now**.

### 10. Fine-step rollout crashed `card` and was fully rolled back

**Observed:** immediately after the fine-step update, the parked car produced multiple EyeSight
faults. The device crash log showed:

```text
TypeError: IntelligentCruiseButtonManagement.__init__() got an unexpected keyword argument 'fineStepEnabled'
```

**Cause:** the change added `fineStepEnabled` to the runtime cereal/Cap'n Proto definition, but the
branch ships a prebuilt `opendbc` Python dataclass without that constructor field. `card` then
crash-looped while converting `CarControlSP`. The car's EyeSight errors were a downstream symptom
of losing the comma's car-interface process, not a vehicle hardware failure.

**Why CI missed it:** the checks compiled Python, resolved the runtime schema, and tested source
logic, but did not instantiate the installed prebuilt dataclass through the real `card` conversion
path. This is another form of the prebuilt-version trap described above.

**Recovery:** the device was reset to known-good commit
`ce4db02231af6645dd71f7a2dc16839a4247d963` while retaining branch name
`custompilot-staging`, then rebooted cleanly. `card` and `controlsd` were verified running. The
public change was reverted in maintenance PR #7 and the protected stable rollback was promoted in
PR #8. Excluding each branch's generated `CUSTOM_FORK_MANIFEST.json`, both published branches were
verified to contain the same driving code as the restored device.

**Important rollback lesson:** at the moment of failure, switching from staging to stable would
not have helped because the broken tree had already been promoted to both. Compare tree contents,
not branch names, before choosing a rollback target. Keep a known-good commit available even when
the branch pipeline looks healthy.

Do not reintroduce the fine-step feature by adding a field to this prebuilt structure. A future
design must avoid that schema/dataclass boundary (for example, a carefully validated setting read
inside the Subaru ICBM implementation) and must include an installed-device smoke test that starts
and observes `card` before promotion.

### 11. A patch that had applied for weeks suddenly failed in the candidate build

**Observed:** on 2026-08-16 the scheduled **Build staging candidate** run failed after about two
minutes. Nothing on the device had changed and no customization had been touched since the last
green run.

```text
error: patch failed: openpilot/selfdrive/ui/sunnypilot/onroad/developer_ui/elements.py:250
error: openpilot/selfdrive/ui/sunnypilot/onroad/developer_ui/elements.py: patch does not apply
```

**Cause:** upstream `sunnypilot/staging` advanced from `30a9cdc` to `5b4820d` and fixed a latent bug
in `elements.py`. No schema field was renamed—`LateralTorqueParameters` has spelled the bit `valid`
all along:

| | `elements.py` reads | `log.capnp` defines |
|---|---|---|
| `30a9cdc` | `ltp.liveValid` | `valid` (no `liveValid`) |
| `5b4820d` | `ltp.valid` | `valid` |

Upstream had been reading a member that does not exist on that struct. That is the same defect
recorded in symptom 7 as the developer-panel UI crash. Patch `0010` carried the buggy line as hunk
context, so the moment upstream corrected it, `git apply --check` no longer matched.

**Resolution:** the two affected hunks were updated to match upstream's corrected text, keeping the
`getattr(ltp, "liveValid", getattr(ltp, "valid", ...))` fallback. The fallback is now belt-and-braces
on current upstream but still resolves correctly, and still covers a device tree that predates the
upstream fix. Verified by applying the entire stack in order against a fresh clone of the new
upstream tip before merging. Maintenance PR #10.

**The more useful signal:** upstream independently fixed a bug this stack had been patching around.
Per step 2 of "Updating or rebasing later", that is the cue to check whether the patch is now partly
redundant. The `liveValid` half of `0010`'s `elements.py` change is; the service-name tolerance
(`lateralTorqueParameters`/`liveTorqueParameters`, `vehicleParameters`/`liveParameters`) is a
separate concern and has not been reassessed.

**Lesson:** treat a red scheduled candidate build as upstream drift until proven otherwise, then ask
*which kind* of drift. A patch whose context breaks because upstream fixed the same bug is telling
you something different from one that breaks because upstream moved code around—the first means part
of your stack may be deletable.

### 12. The same drift, one day later—and this time the patch was deletable

**Observed:** on 2026-08-17 the scheduled **Build staging candidate** run failed after about three
minutes, one day after symptom 11. Again nothing on the device or in the customization had changed.

```text
error: patch failed: openpilot/sunnypilot/modeld_v2/modeld.py:278
error: openpilot/sunnypilot/modeld_v2/modeld.py: patch does not apply
```

**Cause:** upstream advanced from `5b4820d` to `37d6dc5` (v2026.003.000, cut 04:26Z the same
morning) and fixed the accel-contract bug itself. `get_accel_from_plan` has returned a bare scalar
throughout; only the caller was wrong:

| | caller in the `if 'action' not in …` branch | where `should_stop` comes from |
|---|---|---|
| `5b4820d` | `desired_accel, should_stop = …` (**broken unpack**) | set only in the `else` branch |
| `0007` applied | `desired_accel = …` | duplicated into the `if` branch |
| `37d6dc5` | `desired_accel = …` | hoisted out of the conditional, covers **both** branches |

Upstream's fix is a superset of the patch: `0007` repaired only the `if` branch, while upstream
computes `stop = v_ego < 0.3 and desired_accel < 0.1` once, after both branches. Behavior on this
car is identical.

**Resolution:** patch `0007` was **deleted**, not rewritten, and dropped from the `apply_candidate.sh`
loop. This is the outcome symptom 11 predicted—the first time the "deletable" branch of that lesson
actually fired. Verified by applying `0001`–`0006`, the port, then `0008`–`0010` in order against a
fresh worktree at `37d6dc5`: all applied, `git diff --check` clean. Of the 36 files the stack
touches, upstream moved only two—`modeld.py` (this failure) and `params_keys.h` (three new
`ModelManager_*` keys, no collision with `Scene3D` / `RecklessWatch`).

**The part that matters more than the build failure:** this candidate carries a far larger upstream
jump than the last one—**597 files, ~70k insertions** in one squashed snapshot, including **new
driving model weights** (`driving_tinygrad.pkl.chunk02`, 26 MB → 37 MB), a new dmonitoring model, a
rewritten `compile_modeld.py`, and USBGPU handling moved out to `selfdrive/modeld/helpers`. Trap 23
applies with force: a green build here is not a small delta. Promoting it would put a new driving
model and an unrun upstream jump on the car in one step, on top of a device still sitting at
`ce4db02`. Validate the model change on its own terms rather than treating this as a routine
customization refresh.

**Lesson:** two consecutive days of drift on a daily schedule is normal for a fork tracking a
staging branch, not a sign something is wrong. What changes is the *response*: symptom 11 needed a
patch rewrite, symptom 12 needed a deletion. Ask which before touching the hunks—rewriting a patch
upstream has already made redundant carries the redundancy forward indefinitely.

**Outcome:** maintenance PR #14 merged as `46f259c`; the `push`-to-`master` trigger rebuilt the
candidate green and force-published `custompilot-staging` `0a641d2` → `3083a0e` on upstream base
`37d6dc5`. Note that dispatching the workflow on the PR branch would *not* have tested the fix—it
pins `ref: master` on checkout (trap 25), so merging was the only way to exercise it in CI.

**Two process failures worth more than the patch itself:**

1. The guard test asserted `0007`'s implementation rather than the behavior, so it failed against
   upstream's *correct* fix and would have blocked the removal. See trap 24.
2. Verifying the published tip, a hand-written fetch refspec missing its `+` was refused as
   non-fast-forward, leaving the stale `0a641d2` in place and nearly producing a confidently wrong
   report. See trap 22.

Both were caught only by checking a second, independent source—running the test against known-bad
code, and reading the `Publish candidate branch` push line in the run log. Neither would have been
caught by looking harder at the thing itself.

### 13. The 3D view was smooth-looking but rendered the model raw

**Observed:** the scene jittered, lane lines stayed crisp and full-opacity to 120 m where the model
is least certain, the dashes never scrolled, and the ego car was a stack of tinted boxes.

**Cause:** `scene3d` had no temporal filtering at all. On the comma 3X the UI runs at 20 FPS
(`_DEFAULT_FPS = {'tizi': 20}`) and modelV2 arrives at 20 Hz, so this was not a rate mismatch—it was
the driving model's own frame-to-frame variance rendered literally.

**Resolution in `0011`:**

- everything is resampled onto one fixed distance grid before it is filtered or drawn, which is what
  makes per-index smoothing meaningful. `laneLines`/`roadEdges` are published on the constant
  `X_IDXS` grid, but `position` is on `T_IDXS`, so its x is speed-dependent and had to be resampled
  before it could be filtered at all;
- a distance-adaptive EMA smooths far points harder than near ones, and **snaps** rather than glides
  on a lane change, a validity transition or a stale stream. Measured: 68% / 88% / 94% less
  frame-to-frame jitter at 3 / 33 / 104 m, with the near field still tracking 64% of a real change
  within two frames;
- the far field dissolves instead of wobbling, earlier at night because headlights genuinely do not
  reach 120 m;
- dashes scroll with ego motion. They never had, because the pattern was anchored to the polyline
  origin, which is anchored to the car;
- the blind spot fades in fast and out slow, and is a faint wash plus a bright flank rail rather
  than a flat orange rectangle.

**Paid for by:** 39% fewer triangles (2822 -> 1712 across the scenario set) and 84% fewer numpy
geometry builds per frame, mostly from replacing a per-dash `ribbon()` loop with one vectorised
pass. Net CPU is lower than before, which is the only reason the new work is affordable here.

**A real bug found on the way:** `gui_app` monkey-patches `rl.draw_text_ex` to multiply `font_size`
by `FONT_SCALE` (1.16, or 1.242 on the big UI), and `measure_text_cached` applies the same scale.
`scene.py` measured lead labels with raw `rl.measure_text_ex`, which does **not**—so every label box
was sized 16% too small while its text drew at full size and overflowed the rounded rect. The ruff
`TID251` ban on `measure_text_ex` exists precisely to prevent this; the lint rule and the bug are
the same thing. The offline harness cannot reproduce it, because it loads its own font and never
installs the monkey-patch, so this one is verifiable only by test or on the device.

### The ego car: what shipped, and why it is the box car

A real decimated Outback mesh was built and worked. **It is deliberately not shipped.** The source
model was a third-party 2022 Outback downloaded with no licence file, so its author and terms are
unestablished, and an asset you cannot licence is not one you can publish. `0011` therefore ships
the **procedural** car and vendors no mesh.

The loader survives, unused: `make_car()` returns `MeshCarShape` if `assets/outback.gltf` exists and
the procedural `CarShape` otherwise, so dropping in a properly licensed mesh later is a one-file
change with no code edit. Fail-closed by construction—a missing *or unreadable* asset falls back
rather than drawing nothing. Both paths were tested by deleting and by corrupting the file.

Three findings from the mesh work, kept because `tools/build_car_mesh.py` is still in the tree and
because they will apply again to any replacement asset:

- raylib's OBJ loader **ignores** vertex colours (it allocates the buffer and fills it white); its
  glTF loader reads `COLOR_0` correctly. Since there is no shader, baking light into vertex colours
  is the only way to make a loaded mesh look solid—so the asset has to be glTF, not OBJ.
- quadric decimation alone could not get near the triangle budget on a SketchUp export. Every trim
  piece is its own open shell, and quadric collapse will not cross a boundary edge, so it stalled at
  ~24k faces. Vertex clustering first, which fuses shells regardless of connectivity, let it reach
  the budget. With *unwelded* vertices it was worse than stalling: it returned confetti with the
  tyres floating in space.
- accuracy and legibility are not the same thing at this size. The real mesh has correct
  proportions, but on screen the car is about 110 px tall and the procedural box car reads *crisper*
  there—bold flat shapes survive low resolution better than photoreal ones. So the licensing
  constraint and the taste call happen to point the same way: the box car is not a consolation
  prize.

### 14. A green candidate crash-looped the UI within minutes of install

**Observed:** on 2026-08-18 the device installed candidate `3d67803`, booted, was interactive for a
few seconds, and then stuck on the comma logo the moment the car started. SSH still worked.

**Diagnosis over SSH, parked:** `manager`, `camerad`, `modeld`, `dmonitoringmodeld`, `card` and
`controlsd` were all alive—only `openpilot.selfdrive.ui.ui` was missing, with one crash log:

```text
File ".../selfdrive/ui/sunnypilot/onroad/scene3d/renderer.py", line 115, in update
    for i, line in enumerate(model.laneLines[:4])]
File "capnp/lib/capnp.pyx", line 435, in capnp.lib.capnp._DynamicListReader.__getitem__
TypeError: an integer is required
```

**Cause:** patch `0011` slices the `modelV2` lists (`model.laneLines[:4]`, `model.roadEdges[:2]`).
A Cap'n Proto `_DynamicListReader` supports **integer indexing but not slicing**, so the first real
onroad frame raises. It is not a rendering bug and not a performance bug—the scene never got to
draw.

**Why every check passed.** The stack applied cleanly, `git diff --check` was clean, and **166 tests
were green**—including a 256-line geometry suite written specifically for this patch. None of it
touched a capnp object: `render_scenes.py` builds *synthetic modelV2-shaped* data out of numpy
arrays, which slice happily. The offline harness is structurally incapable of reproducing this, and
a green harness was treated as sufficient for a render path the runbook had already flagged as never
installed and never driven.

**Two aggravating factors worth separating from the bug itself:**

1. **The device tracks `custompilot-staging`, not `custompilot-stable`.** It has since the symptom-10
   recovery, which restored `ce4db02` while retaining the branch name. So the candidate reached the
   car directly, with none of the protected-promotion review the pipeline was designed around. Any
   reasoning of the form "promotion is manual, so a candidate cannot reach the car" is **false for
   this device** until its branch is changed. Verify the checkout, not the design.
2. **Ignition cycling does not restart the UI.** `ui` is an `always_run` process, started at manager
   start and not tied to onroad/offroad transitions, and this manager generation does not respawn a
   crashed process. Turning the car off and on left the same manager PIDs and no UI. Only a manager
   restart, a reboot, or a manual launch brings it back.

**Recovery:** `Scene3D` was set false over SSH, which is enough on its own—the key is
`PERSISTENT | BACKUP` with default `b"0"` and no `CLEAR_ON_MANAGER_START`, so it survives restarts
and fails safe. Then reboot offroad, or manually launch the UI as in "The reboot/recovery lesson".
`card` and `controlsd` never dropped, so this was never the symptom-10 condition.

**Immediate resolution:** `0011` was pulled from `apply_candidate.sh` the same night and the next
candidate rebuilt without it.

**Fix, 2026-08-18:** both sites now use only operations a capnp reader supports—`len()` and integer
indexing—and clamp instead of assuming a fixed count:

```python
state.lane_lines = [self._grid_line(_xyz(model.laneLines[i]), geo.GRID_S, self._lane_y[i], self._lane_z[i])
                    for i in range(min(4, len(model.laneLines)))]
```

Behaviour is unchanged: the render harness reports the same 1712 triangles and the same per-scene
counts before and after. Two guards were added in `tests/test_scene3d_capnp_access.py`—a stub that
refuses exactly what pycapnp refuses (proving the old idiom raises and the new one does not), and
an AST scan that fails if any scene3d source slices a model field. **The AST guard was confirmed to
fail against the pre-fix patch text**, reporting `renderer.py:115`, the exact line from the
traceback.

**What could not be tested, and why it matters.** An end-to-end test against a real Cap'n Proto
reader is impossible off-device: the prebuilt `msgq` extension is aarch64-Linux, so importing
`cereal` fails on a laptop *and* on an x86 CI runner (`ipc_pyx.so: slice is not valid mach-o file`).
No amount of offline testing closes this gap for cereal-reading code—only the device can. That is
what `tools/check_ui_health.py` is for.

**Lesson:** a test that never constructs the real object proves only that the code is
self-consistent. This is the same defect as symptom 10—which crash-looped `card` for exactly the
same reason, a prebuilt runtime object the tests never built—and the runbook already said so. The
rule that would have caught both: *if a change reads a cereal message, one test must feed it a real
capnp reader, or the change is unvalidated no matter how many tests are green.*

## The reboot/recovery lesson

During the final recovery, the UI process had crashed and the installed manager did not
automatically restart it. A manually launched UI restored the display without touching steering
or control processes. That was an emergency recovery, not the preferred normal procedure.

An attempt was then made to add `restart_if_crash=True` to the UI process configuration because
the newer source clone supports it. On reboot, the installed manager rejected the keyword and
never started. The launcher console showed:

```text
TypeError: PythonProcess.__init__() got an unexpected keyword argument 'restart_if_crash'
```

The option was removed, the device commit was amended to `7038b6f`, and a second offroad reboot
started manager and the manager-owned UI normally.

Lessons:

- Always inspect the installed constructor/API before backporting a newer configuration option.
- A successful `py_compile` does not execute module-level object construction; it cannot catch an
  unsupported keyword passed while importing `process_config.py`.
- After every reboot, inspect the launcher console and confirm manager plus UI processes—not just
  SSH connectivity.
- Do not add the watchdog option back unless the installed `PythonProcess.__init__` explicitly
  supports it.

## Required operating settings

Names can move between sunnypilot versions, so verify the behavior rather than blindly matching a
screenshot.

### Lateral toggle

- MADS enabled.
- **Toggle with Main Cruise** / `MadsMainCruiseAllowed` enabled.

Expected behavior: physical ACC main off latches steering off; main on makes lateral available
again. ACC availability follows the same factory main state.

### ICBM

- Intelligent Cruise Button Management enabled.
- A valid OSM/resolver speed limit source available.

Expected behavior: it changes the stock set speed only while the system is enabled and ready. It
does nothing on an invalid/missing limit or during driver button input. SCC-V/SCC-M tags do not
become stock button targets.

Current behavior uses the stock short/coarse 5 mph action. There is no working fine-step toggle in
the known-good build; the attempted implementation was reverted after the `card` crash described
above.

### Torque Self-Tune

- Enforce Torque Lateral Control: on.
- Self-Tune: on.
- Less Restrict Settings: on.
- Custom Tuning: off unless deliberately re-anchoring.
- Manual Real-Time Tuning: off; it is an alternative to Self-Tune and can disable learning.

The `2.0 / 0.2` tune is an anchor, not a claimed final measurement. Changing tuning inputs can
invalidate the learned cache and restart accumulation. Use `tools/torque_status.py` and read
[`torque-tuning.md`](torque-tuning.md) before touching these values.

### Visual features

- Map Panel requires downloaded offline OSM data.
- Scene 3D uses model geometry, not the camera feed.
- Virginia watch is a reminder, not legal advice or a substitute for attention.

## Safe change procedure

Perform software work parked. Reboot only after ignition is off and `IsOffroad` is true.

### Before changing anything

```bash
ssh comma@<device-address> "cd /data/openpilot && git status --short && git log -1 --oneline"
```

Then record:

- branch, commit, and dirty state;
- whether `prebuilt` exists;
- exact service names in the installed UI `SubMaster` list;
- exact schema fields in installed `cereal/log.capnp` and `custom.capnp`;
- whether the process-manager API supports any option being added;
- raw offroad state.

Use the device's bundled Python environment for diagnostics:

```bash
PYTHONPATH=/data/openpilot /usr/local/venv/bin/python <script.py>
```

Plain `python3` may not find `openpilot`, `capnp`, or other bundled dependencies even when normal
driving processes can.

### Apply conservatively

1. Copy the patch to `/tmp`.
2. Run `git apply --check` before applying.
3. Apply only the intended files.
4. Run `git diff --check`.
5. Run Python compilation with the device interpreter.
6. Run focused unit tests and the fork-compliance check.
7. Review `git diff` on the device.
8. Commit the exact device change.
9. Export the device diff back into this repair repository.
10. Confirm the exported patch reverse-applies to the committed device state.

Do not use whole-file copies from a newer clone as the final reproducible artifact. If an
emergency copy was necessary, convert the resulting exact device diff into a numbered patch.

### Reboot verification

After confirming `IsOffroad True`, reboot and verify all of the following:

```bash
tmux capture-pane -pq -S-100
ps -eo pid,ppid,state,comm,args | grep -E 'manager.py|openpilot.selfdrive.ui.ui'
cd /data/openpilot && git status --short && git log -1 --oneline
```

SSH being online proves only that AGNOS and networking booted. It does not prove manager,
camerad, UI, or controls started.

For any change that touches `CarControlSP`, `CarStateSP`, cereal, opendbc structures, or ICBM,
explicitly confirm that both `openpilot.selfdrive.car.card` and
`openpilot.selfdrive.controls.controlsd` remain alive for several cycles. Inspect new files in
`/data/community/crashes`; dashboard EyeSight faults plus a missing/crash-looping `card` process is
an immediate rollback condition. After restoring healthy software, a full ignition-off pause and
restart may be needed for vehicle-side EyeSight faults to clear.

The clock may initially be wrong, producing temporary certificate errors and cached model/Prime
fallbacks. Confirm `date -u` becomes correct and that the errors stop before diagnosing this as a
software regression.

## Diagnostic toolbox

The tools in [`tools/`](../tools) exist because each caught a real class of failure:

| Tool | Use |
|---|---|
| `analyze_last_drive.py` | Summarize alerts, crash logs, planner sources, ICBM states, and cruise transitions from route logs. |
| `analyze_service_timing.py` | Measure service gaps and identify which service caused communication-rate alerts. |
| `analyze_proc_load.py` | Correlate UI/model/planning CPU use and per-core saturation. |
| `check_live_service_rates.py` | Verify live rates parked after a CPU-affinity change. |
| `monitor_main_toggle.py` | Observe physical main transitions and restore attribution while parked. |
| `log_es_longitudinal.py` | Read-only capture of what EyeSight actually commands: `ES_Brake` / `ES_Distance` / `ES_Status` on bus 2 (camera intent) beside bus 0 (what reaches the car). `--route-glob` parses drives that already happened, so the first numbers need no new driving. Measures the scaling that openpilot only has global-derived guesses for. See [`preglobal-longitudinal.md`](preglobal-longitudinal.md). |
| `probe_cruise_button.py` | Confirm the car's real short/long cruise-button step behavior. |
| `torque_status.py` | Inspect Self-Tune enablement, validity, learned values, bounds, and bucket progress. Reads either torque-parameter service name and normalizes the validity bit; exits cleanly on a stale cache (see trap 20). |
| `calibrate_wheel_speed.py` | Derive a wheel-speed factor from a real steady highway drive; never guess it. |
| `check_ui_health.py` | Post-install UI smoke check, run on the device. Reports UI/`card`/`controlsd` state, `Scene3D`, and any new crash log, then watches for a fresh crash while you exercise the render path. Exists because symptom 14 could not be caught offline at all. |
| `preview_map_panel.py` / `render_scenes.py` / `render_reckless.py` | Exercise visual code off-device before risking the live UI. |

Route segment names must be sorted numerically, not lexicographically: segment `10` otherwise
appears before segment `2`. Also remember that a route may still be growing while ignition is on.

When diagnosing a takeover, identify the exact alert text/event first. "TAKE CONTROL
IMMEDIATELY" is the severity presentation, not the root cause. In this journey it referred at
different times to communication rate and driving-model lag—two different problems.

## Traps to avoid

1. **Do not use the general longitudinal target for stock button automation.** It can contain the
   unset cruise ceiling. Require an explicit valid speed-limit source.
2. **Do not fight the driver for ACC main.** Synthetic cancel/restore behavior must attribute its
   own transitions and yield to a physical main-off request.
3. **Do not assume a header edit rebuilt a prebuilt native library.** Prove a new Params key is
   accepted by the running library.
4. **Do not read custom Params unguarded.** Unknown keys can crash the UI at boot/onroad.
5. **Do not assume nearby staging commits share cereal service names or fields.** Inspect the
   installed schema and subscriptions.
6. **Do not assume model coordinates are Left/Up.** sunnypilot model output is
   Forward/Right/Down.
7. **Do not put planning work on a UI-saturated core.** Preserve realtime priority and measure
   actual rates after affinity changes.
8. **Do not reboot with ignition on.** It can leave the car and panda in an awkward transient
   state and previously produced an LKAS fault during this work.
9. **Do not trust SSH alone after reboot.** Inspect launcher output and required processes.
10. **Do not add `restart_if_crash=True` on this installed manager.** Its constructor does not
    support the argument.
11. **Do not turn on Manual Real-Time Torque Tuning while expecting Self-Tune to learn.** They are
    competing modes.
12. **Do not change torque anchors casually.** It invalidates caches and changes learning bounds.
13. **Do not guess wheel-speed calibration.** Measure GPS versus `vEgo` during a suitable drive.
14. **Do not cross into panda safety casually.** Safety changes require the full safety test and
    coverage obligation and are a different risk tier.
15. **Do not publish a promotion candidate as a sibling of stable.** Building from the upstream
    commit and committing with that commit as the parent makes GitHub compare from the merge base,
    can display the entire customization stack, and can make an otherwise source-identical
    promotion conflict. Build and validate the tree against the manifest's exact upstream commit,
    but create the published candidate commit with the current `custompilot-stable` tip as its
    parent. Also keep the stable-validation workflow on the repository's default branch so GitHub
    registers and runs the required `validate` check on promotion PRs.
16. **Do not weaken protection when a bot-updated promotion PR says `action_required`.** A push
    made with GitHub's workflow token does not recursively start a normal PR workflow. Manually
    dispatch **Validate custom fork** on `custompilot-staging` and wait for its `validate` job.
    Once staging has been promoted and is an ancestor of stable, treat matching provenance as
    unchanged; requiring its parent to equal the new stable merge commit would create daily churn.
17. **Do not add fields to prebuilt cereal/opendbc Python structures without an installed-runtime
    construction test.** A `.capnp` file resolving does not update an already-built dataclass.
    Exercise the exact `card` conversion path and verify the process stays alive.
18. **Do not assume stable is a rollback merely because of its name.** Confirm its tree predates or
    excludes the regression. During the fine-step incident, staging and stable initially contained
    the same broken code.
19. **Do not infer fine/coarse behavior from button enum names.** On this Subaru, duration—not a
    separate button code—selects 5 mph versus 1 mph behavior.
20. **Do not probe a Cap'n Proto union member with only `except AttributeError`.** The two failure
    modes are different exceptions: a name that is *absent from the schema* raises `AttributeError`,
    but a name that *exists and is not the currently set union member* raises capnp's `KjException`
    ("Tried to get() a union member which is not currently initialized"). A compatibility probe that
    catches only the first works while exactly one spelling exists in the schema, then crashes with a
    raw capnp traceback the moment it meets a stale or foreign cache. Catch both, and verify the
    fallback message is actually reachable rather than assuming it is.
21. **Do not treat a patch stack as validated against anything but a named upstream commit.** The
    stack is checked against two moving authorities: `/data/openpilot` for installed device APIs and
    `upstream/staging` for patch applicability. Green against one proves nothing about the other, and
    upstream can move on any day the schedule fires.
22. **Do not trust a local `custompilot-*` ref without fetching first, and do not assume the fetch
    worked.** The default refspec fetches `master` only, and candidate commits are force-published
    siblings rather than a chain, so a local copy can sit arbitrarily far behind while looking like a
    valid branch. Compare `origin/custompilot-stable` against `origin/custompilot-staging` after an
    explicit fetch, never a stale local ref.

    The second half of this trap was hit on 2026-08-17. A **hand-written refspec missing the leading
    `+`** cannot fast-forward a force-published candidate, so the update is refused and the
    remote-tracking ref keeps its old value—after which `git rev-parse` reports the stale tip with no
    hint anything went wrong. Measured behavior:

    | invocation | stderr | exit | ref updated |
    |---|---|---|---|
    | `git fetch origin 'refs/heads/…:refs/remotes/origin/…'` | `! [rejected] … (non-fast-forward)` | **1** | no |
    | same, plus `-q` | *(nothing)* | **1** | no |
    | same, refspec prefixed `+` | normal | 0 | yes |

    So the failure is loud in the **exit code** and only ever silent in the **output**. What turns it
    into a wrong answer is the combination: `-q` hides the rejection, then chaining the next command
    with `;` (or piping the fetch through `tail`) discards the non-zero status. Use the configured
    refspecs from "Keep the pipeline branches as remote-tracking refs only" so a bare `git fetch`
    does the right thing; if you must write one inline, include the `+` and let the fetch fail the
    command. Then confirm the tip against an independent source—the `Publish candidate branch` step
    of the run log prints the exact `old...new` it pushed—rather than trusting a single `rev-parse`.
23. **Do not assume a green candidate build means only your patches changed.** The build tracks the
    latest upstream staging tip, so a passing run can carry an upstream jump alongside the
    customization stack. Diff stable against staging, excluding the generated manifest, before
    promoting.
24. **Do not let a guard test assert your patch's implementation instead of the property it
    protects.** `test_non_action_path_computes_should_stop` asserted "at least two `should_stop`
    assignments," which was true only because patch `0007` duplicated the line into both branches.
    When upstream fixed the same bug correctly—one assignment, hoisted out of the conditional—the
    test failed against *better* code and would have blocked the patch's removal. A test written this
    way inverts its own purpose: it stops protecting the behavior and starts protecting the
    workaround. Assert the property (the flag is assigned on every path and reaches `shouldStop`),
    then prove the test still fails against the known-bad source so it has not become a tautology.
25. **Do not assume a maintenance PR into `master` is tested by anything.** `update-candidate.yml`
    pins `ref: master` on checkout, so dispatching it against a PR branch rebuilds from `master` and
    tells you nothing about the PR. `validate-custom-fork.yml` triggers only on PRs into
    `custompilot-stable`, and it too pins assets to `master`. Nothing in the repository validates a
    change to `.robert-custom/` before it lands. Reproduce locally by running the real
    `apply_candidate.sh` and `validate_candidate.sh` against a worktree at the current upstream tip,
    from a tracked-only export of the assets (`git archive`), so untracked scratch directories such
    as `.robert-custom/sunnypilot/` cannot leak in and change the result.

26. **Do not bake vertex colours into an OBJ and expect raylib to read them.** Its OBJ loader
    silently discards them and hands back white; its glTF loader reads `COLOR_0` correctly. With no
    shader available, baked vertex colour is the only way a loaded mesh reads as solid, so the
    format is forced. A `.gltf` with base64-inlined buffers is a single text file and can live in a
    patch.
27. **Do not quadric-decimate a CAD or SketchUp export without welding and clustering first.** Every
    trim piece is its own open shell and boundary edges are never collapsed, so decimation either
    stalls far above the budget or—with unwelded vertices—returns confetti with the tyres floating
    in space. Weld, then vertex-cluster to fuse the shells, then collapse.
28. **Do not tint a mesh that already carries baked colours with the body colour.** `draw_model_ex`
    multiplies, so green over green renders a dark smear. Tint near-white and let the vertex colours
    be the paint.
29. **Do not vendor a third-party asset before establishing its licence.** The Outback mesh was
    built, worked, and was then left out of the shipped patch because the source model arrived with
    no licence file. Establish author and terms *before* the asset becomes load-bearing, and prefer
    a design where its absence is a graceful fallback rather than a breakage—that is what made
    dropping it a one-line decision instead of a rewrite.
30. **Do not accept a green offline test suite as validation for code that reads a cereal message.**
    The 3D scene shipped with 166 green tests and crash-looped the UI on the first onroad frame,
    because every test fed it synthetic numpy arrays and the real `modelV2` hands you a Cap'n Proto
    `_DynamicListReader`—which indexes but does not slice. Symptom 10 was the same shape with a
    prebuilt dataclass. If a change reads a cereal message, one test must construct a real reader
    (or a stub that refuses the operations capnp refuses), or the change is unvalidated no matter
    how green the suite is.
31. **Do not reason about what can reach the car from the pipeline's design.** The protected-
    promotion model is real, and it did not apply: this device tracks `custompilot-staging`, so
    candidates install without review. Check the device's actual branch (`git -C /data/openpilot
    rev-parse --abbrev-ref HEAD`) before claiming anything is gated.
32. **Do not expect an ignition cycle to restart a crashed UI.** `ui` is `always_run`—started at
    manager start, not on onroad/offroad transitions—and this manager generation does not respawn
    crashed processes. Turning the car off and on leaves the same manager PIDs and no UI. Only a
    manager restart, a reboot, or a manual launch recovers it.

## What remains deliberately unresolved

- **Decoupled steering while ACC remains on:** not implemented; requires verified button input and
  panda safety work.
- **Wheel-speed calibration:** tooling exists, but the correction must come from a controlled
  highway sample. No guessed factor should be installed.
- **Final torque coefficients:** Self-Tune needs varied, engaged driving to fill its buckets. The
  current offline values are a sane learning anchor.
- **ICBM exact 1 mph landing:** the physical behavior is measured, but the first implementation was
  reverted. Redesign it without a prebuilt schema/dataclass change and add a real `card` smoke test.
- **SCC-V/SCC-M through stock EyeSight:** not wired into ICBM. Displayed curve targets alone cannot
  command longitudinal control on this stock-longitudinal platform.
- **openpilot longitudinal control itself:** disabled at four independent gates, none of them a
  hardware limit—the preglobal platform carries the same three ES messages the global platform
  uses. Enabling it requires panda safety changes and custom firmware, i.e. the tier this fork has
  deliberately never entered (trap 14). Researched in full in
  [`preglobal-longitudinal.md`](preglobal-longitudinal.md); the measurement that would inform any
  decision is not yet taken.
- **Automatic UI crash restart:** not available in this manager generation. The known schema crash
  was prevented at its source instead. Revisit only after a branch update that supports the API.
- **Driving-model lag:** the redundant UI work was reduced and the subsequent drive was clean, but
  any future occurrence must still be treated as a real disengagement and analyzed from its route.
- **Promotion of the `37d6dc5` candidate (`3083a0e`):** built, green, and unpromoted as of
  2026-08-17; it supersedes the `5b4820d` candidate, which was never promoted either. Measured
  delta against stable, excluding the manifest: **600 files, ~70k insertions**—and essentially all
  of it is upstream, not customization. That includes **new driving model weights**
  (`driving_tinygrad.pkl.chunk02`, 26 MB → 37 MB), a new dmonitoring model, a rewritten
  `compile_modeld.py`, and USBGPU handling moved to `selfdrive/modeld/helpers`. The device is still
  at `ce4db02`, so promoting means a new driving model *and* two upstream generations at once.
  Beyond the standard offroad install, `card`/`controlsd` alive across several cycles, and a check
  of `/data/community/crashes`, this one also needs the developer UI panel exercised specifically
  (that panel is what `0010` touches and what crashed in symptom 7) and the model change validated
  on its own terms—`check_live_service_rates.py` for `modelV2`, and alert-free driving before it is
  trusted. Do not treat it as a routine customization refresh.
- **Patch `0011` on the device:** the slice defect is fixed and guarded, and the patch is back in
  the build, but it has **still never run on the car**. The fix is well-founded but unproven where
  it counts. Test it deliberately: install offroad with `Scene3D=0`, confirm the UI comes up, then
  toggle Scene3D on **while parked with the engine running** and run `tools/check_ui_health.py`—the
  crashing path only executes onroad. Capture the CPU baseline first (`analyze_proc_load.py`,
  `check_live_service_rates.py`) and confirm `radarState` back at 20.00 Hz after.
- **The device's tracking branch:** it follows `custompilot-staging`, so every green candidate
  installs without review. Either point it at `custompilot-stable` via the installer URL, or accept
  that the daily build is effectively a direct-to-car channel and treat every candidate as a
  release. This is the single highest-leverage unresolved item here—it is what turned a bad patch
  into a stuck car.
- **A licensed ego-car mesh:** the real-mesh path exists and is tested but ships dormant, because
  the only mesh built so far came from a third-party model with no licence. To enable it, establish
  a properly licensed source, regenerate with `tools/build_car_mesh.py`, and drop the result at
  `scene3d/assets/outback.gltf`—no code change required. Note the model used during development was
  a **2022** Outback (6th generation), not the 5th-gen a 2018 is; at chase distance the difference
  is subtle but it is a known inaccuracy, and symptom 13 records why the box car may be the better
  choice on legibility grounds regardless.

## Updating or rebasing later

Never blindly reapply all patches to a new sunnypilot release.

1. Clone/fetch the exact new source branch.
2. Compare every patch against upstream; drop fixes already incorporated.
3. Re-identify renamed services, Params behavior, process APIs, and Subaru port structure.
4. Apply one patch at a time with tests.
5. Confirm the fork-compliance classification.
6. Install while offroad and validate the smallest behavior first: model services, main toggle,
   ICBM limits, then optional UI features.
7. Keep an untouched known-good commit available for rollback.

Upstream can also change a line underneath a patch that has applied cleanly for weeks, without any
action on your part—including by fixing the very bug the patch works around. The scheduled candidate
build is what detects this, so read its failure as drift detection working rather than as a broken
pipeline, and use it to trigger step 2 above; see symptom 11.

The device checkout, exported patches, and this runbook should agree. If they do not, stop and
reconstruct the actual device diff before driving.

## Public update pipeline and installation

The public repository is maintained so normal updates do not require repeatedly editing the comma
over SSH:

1. The scheduled/manual candidate workflow checks the latest installable sunnypilot staging base.
2. It reapplies the tracked customization stack and runs compliance, focused tests, and provenance
   checks.
3. A passing result updates `custompilot-staging` only.
4. Review and a protected promotion PR are required before anything reaches
   `custompilot-stable`.
5. The comma installs from
   `https://install.sunnypilot.ai/fork/robertdb3/custompilot-stable`.

The branch called staging is a candidate, not a promise of safety; stable is the intended install
target, but its contents must still be checked during incident rollback. The installer/updater may
show a newer commit whose only difference is generated manifest/provenance. Compare source trees
excluding `CUSTOM_FORK_MANIFEST.json` when determining whether driving code actually changed.

A branch switch or reinstall is not required merely to point an already-correct checkout at a
branch, but using the URL is the clean supported way to establish the updater-managed install.
Perform installs offroad, expect commit IDs and possibly the SSH host key to change, and re-run the
full process checks before driving.

### Candidate commits are replaced, not appended

Each build creates a fresh candidate commit whose parent is the current `custompilot-stable` tip,
per trap 15, and force-publishes it. Successive candidates are therefore **siblings, not a chain**:
`0dfc9ed` and `0a641d2` both had `08e9e98` as their parent. Consequences:

- A local `custompilot-staging` branch can never be fast-forwarded after a rebuild. `git pull`
  fails with "not possible to fast-forward" and only a hard reset will match the remote.
- Candidate commits are disposable generated artifacts. Discarding a local one loses nothing; it is
  reproducible from `master` plus the tracked patch stack.

### Keep the pipeline branches as remote-tracking refs only

Do not keep local `custompilot-staging` / `custompilot-stable` branches or a separate candidate
worktree. Nothing is ever authored on them, so a local branch only adds a ref that must be hand
synchronized and that cannot fast-forward. On 2026-08-16 the local copies were found still pointing
at the original `30a9cdc` candidate—months of drift—which is exactly the wrong tree to reach for
mid-incident under trap 18.

Track them as remote-tracking refs instead, so a bare `git fetch` always makes them current:

```bash
git config --add remote.origin.fetch '+refs/heads/custompilot-staging:refs/remotes/origin/custompilot-staging'
git config --add remote.origin.fetch '+refs/heads/custompilot-stable:refs/remotes/origin/custompilot-stable'
```

The default clone refspec fetches `master` only, which is why these branches silently went stale.
When the candidate is needed as browsable files, create a throwaway worktree and delete it after:

```bash
git worktree add /tmp/cand origin/custompilot-staging
```

## Focused references

- [`call-trace.md`](call-trace.md) — exact userspace/panda lateral-control chain.
- [`decoupled-toggle.md`](decoupled-toggle.md) — why ACC-on/steering-off is a safety project.
- [`customization-and-risk.md`](customization-and-risk.md) — fork policy and protected surfaces.
- [`map-panel.md`](map-panel.md) — offline map format, performance, and rendering decisions.
- [`preglobal-longitudinal.md`](preglobal-longitudinal.md) — why openpilot longitudinal is off on
  this car, and what turning it on would actually require.
- [`torque-tuning.md`](torque-tuning.md) — torque anchor, Self-Tune, bounds, and cache traps.
- [`virginia-watch.md`](virginia-watch.md) — geofence, thresholds, limits, and calibration.
- [`../README.md`](../README.md) — patch inventory and repository usage.

## Final rule

Every alert is real until its exact event and route evidence prove otherwise. These modifications
are driver-assistance customizations, not autonomy. Stay ready to take over immediately, and keep
the ability to return to the known-good commit before experimenting further.
