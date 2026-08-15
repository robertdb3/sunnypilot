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
| Device commit after this work | `7038b6f` (`Fix mirrored 3D scene and recover UI crashes`) |
| Local repair repository | `~/projects/sunnypilot-lat-toggle` |
| Local repair commit | `cc1d2d2` (`Fix mirrored 3D scene and UI recovery`) |
| Device address during this work | `fe80::20a:f5ff:feaf:4679%en0` (link-local and not permanent) |
| Final validation | Long drive: ICBM followed changing limits; return drive: no alerts or UI issues; 3D orientation correct |

The IP address and git hashes will change after future updates. Identify the device and inspect its
actual checkout again rather than treating them as universal constants.

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
| `0007` | Fix tinygrad acceleration contract | Corrects a scalar/tuple mismatch that broke model output and produced `posenet speed invalid` / `nan m/s`. |
| `0008` | Fail-closed ICBM and main-button arbitration | Prevents ICBM from following the 90 mph planner fallback and preserves a physical driver main press over the cancel it causes. |
| `0009` | Move `radard` and `plannerd` to CPU 6 | Isolates the planning chain from the custom UI load that saturated CPU 5 and caused low communication-rate alerts. |
| `0010` | Fix the mirrored 3D frame and developer UI crashes | Corrects Forward/Right/Down model coordinates, supports both schema generations, and avoids redundant model-array conversions. |

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

The 2015 preglobal Subaru does not emit separate deep-button codes for a normal long press.
Raw-CAN measurements showed quick and held SET/RES both use shallow codes `2`/`4`; duration is
the only distinction. Six on-road trials produced exactly one mph after `0.806-0.856 s`. Fine
ICBM therefore repeats the shallow code for at most 19 uninterrupted 50 ms slots (through 0.90
s) and releases sooner when the set speed moves. It abandons a partial hold on driver input,
ACC disengagement, a missed output slot, invalid/crossed target, or direction change. Never
replace this with a one-frame deep-code (`3`/`5`) assumption.

The dedicated **ICBM Fine 1-mph Adjustments** offroad toggle selects the behavior. Leave it off
for coarse-only 5-mph snapping. Turn it on to let ICBM use the measured long hold when a 1-mph
step gets closer to its target. This is intentionally separate from sunnypilot's generic Custom
ACC Speed Increments setting because the physical Subaru semantics are short press = 5-mph snap
and long press = 1 mph. With a 14% speed-limit offset, fine mode targets the rounded percentage
(55 -> 63, 45 -> 51); coarse-only mode preserves the observed 55 -> 65 and 45 -> 50 behavior.

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
PYTHONPATH=/data/openpilot:/data/openpilot/openpilot /usr/local/venv/bin/python <script.py>
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
| `probe_cruise_button.py` | Confirm the car's real short/long cruise-button step behavior. |
| `torque_status.py` | Inspect Self-Tune enablement, validity, learned values, bounds, and bucket progress. |
| `calibrate_wheel_speed.py` | Derive a wheel-speed factor from a real steady highway drive; never guess it. |
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

## What remains deliberately unresolved

- **Decoupled steering while ACC remains on:** not implemented; requires verified button input and
  panda safety work.
- **Wheel-speed calibration:** tooling exists, but the correction must come from a controlled
  highway sample. No guessed factor should be installed.
- **Final torque coefficients:** Self-Tune needs varied, engaged driving to fill its buckets. The
  current offline values are a sane learning anchor.
- **Automatic UI crash restart:** not available in this manager generation. The known schema crash
  was prevented at its source instead. Revisit only after a branch update that supports the API.
- **Driving-model lag:** the redundant UI work was reduced and the subsequent drive was clean, but
  any future occurrence must still be treated as a real disengagement and analyzed from its route.

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

The device checkout, exported patches, and this runbook should agree. If they do not, stop and
reconstruct the actual device diff before driving.

## Focused references

- [`call-trace.md`](call-trace.md) — exact userspace/panda lateral-control chain.
- [`decoupled-toggle.md`](decoupled-toggle.md) — why ACC-on/steering-off is a safety project.
- [`customization-and-risk.md`](customization-and-risk.md) — fork policy and protected surfaces.
- [`map-panel.md`](map-panel.md) — offline map format, performance, and rendering decisions.
- [`torque-tuning.md`](torque-tuning.md) — torque anchor, Self-Tune, bounds, and cache traps.
- [`virginia-watch.md`](virginia-watch.md) — geofence, thresholds, limits, and calibration.
- [`../README.md`](../README.md) — patch inventory and repository usage.

## Final rule

Every alert is real until its exact event and route evidence prove otherwise. These modifications
are driver-assistance customizations, not autonomy. Stay ready to take over immediately, and keep
the ability to return to the known-good commit before experimenting further.
