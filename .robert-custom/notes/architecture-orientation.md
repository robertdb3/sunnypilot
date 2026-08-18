# Understanding this fork, end to end

A ground-up orientation to openpilot, sunnypilot, this fork, the comma 3X, and the pipeline that
connects them. Written to be read once from the top, then used as a reference.

The other notes in this directory answer *"what happened and what must I not repeat"* — especially
[`device-journey-runbook.md`](device-journey-runbook.md), which is the operational authority. This
file answers the prior question: *"how does any of this work, and what exactly would I be sharing
if I contributed it?"*

Everything here was verified against the checkout at the time of writing. Where a claim comes from
the runbook (i.e. observed on the device rather than readable in this repo), it says so.

---

## 0. The one-paragraph version

comma.ai writes **openpilot**, an open-source driver-assistance system that runs on a small Linux
computer bolted to your windshield. **sunnypilot** is a fork of openpilot that adds features comma
declined to add, most importantly a steering mode that works independently of cruise control.
**This repository** is a fork of sunnypilot that adds six things specific to a 2018 Subaru Outback.
Your changes are not stored as a modified copy of the code — they are stored as **nine patch files
and one compatibility port**, plus a build robot that re-applies them to whatever sunnypilot
published today. The car itself runs
a *fourth* thing: a pre-compiled snapshot that is subtly different from any source tree on this
laptop, which is the single largest source of bugs in this project's history.

---

## 1. Three codebases, nested

Think of it as three layers of authorship, each one a fork of the one above.

```
comma.ai / openpilot          the base system: perception, planning, control, OS
        │
        ├── forked by ──►  sunnypilot / sunnypilot
        │                     adds MADS, ICBM, model choice, extra UI, sunnylink
        │                     tracks comma closely; merges upstream continuously
        │
        └────────────── forked by ──►  robertdb3 / sunnypilot   ← this repo
                                          adds 6 Outback-specific features
                                          tracks sunnypilot daily, automatically
```

### What each layer actually contributes

**openpilot (comma.ai)** is the whole machine: camera drivers, a neural network that looks at the
road and outputs a driving plan, a controller that turns that plan into steering commands, a
safety microcontroller, the operating system (AGNOS), and the car-specific translation layer
(`opendbc`) that knows how to speak CAN bus to a Subaru versus a Toyota.

**sunnypilot** is not a rewrite. It is openpilot plus a parallel set of features, and — this is the
important part — it is built to be *additive rather than invasive*. You'll see three conventions
over and over:

| Convention | Example | Why it matters |
|---|---|---|
| A parallel `sunnypilot/` directory beside comma's code | `openpilot/sunnypilot/`, `openpilot/selfdrive/ui/sunnypilot/`, `opendbc/sunnypilot/car/subaru/` | sunnypilot's own code lives in its own tree; merging comma's updates rarely conflicts |
| A parallel `…SP` message beside comma's message | `carControlSP` next to `carControl`, `longitudinalPlanSP` next to `longitudinalPlan` | sunnypilot never has to widen comma's data structures |
| Comma's *reserved* schema file | `openpilot/cereal/custom.capnp` | comma deliberately ships empty structs "reserved for custom forks" — sunnypilot fills them in |

That third one is worth pausing on, because it's an unusually thoughtful piece of design and it's
the same door your own features walk through. The top of `openpilot/cereal/custom.capnp` reads:

> a home for empty structs reserved for custom forks. These structs are guaranteed to remain
> reserved and empty in mainline cereal … **DO** rename the structs, **DON'T** change the identifier

So comma pre-allocated numbered slots in the message format for forks to use, guaranteeing forks
never collide with comma's future messages. sunnypilot renamed those slots to
`ModularAssistiveDrivingSystem`, `IntelligentCruiseButtonManagement`, and so on.

**This repo** follows the same conventions where it can. Your Subaru code lives in
`opendbc/sunnypilot/car/subaru/` next to sunnypilot's own; your custom messages go in
`custom.capnp`. That consistency is most of why contributing upstream is even plausible — see §8.

### Two sunnypilot concepts you need by name

- **MADS** — *Modular Assistive Driving System*. sunnypilot's headline feature: decoupling **lateral**
  control (steering) from **longitudinal** control (speed). On stock openpilot, steering and cruise
  engage together. MADS lets steering be its own mode. This is the subsystem your ACC-main toggle
  plugs into.
- **ICBM** — *Intelligent Cruise Button Management*. When openpilot **can't** control your throttle
  and brakes directly, ICBM instead presses the stock cruise-control buttons for you, electronically.
  It's a puppet finger on the steering wheel. This matters enormously for your car — see §5.

---

## 2. The repository has two different shapes

This trips people up constantly, so it gets its own section. **The same repository looks
structurally different depending on which branch you check out.**

| | `master` (and sunnypilot's `master-dev`) | `staging` (and your `custompilot-*`) |
|---|---|---|
| Purpose | Development source | **Installable snapshot** |
| `opendbc_repo`, `panda`, `tinygrad_repo` | git **submodules** (pointers to other repos) | **flattened into real directories** |
| `prebuilt` marker file | absent | **present** |
| Native code (`.so`, binaries) | you compile it | **already compiled, committed** |

Verified directly — the same path, two different git object types:

```
master     160000 commit 3b8f263…  opendbc_repo     ← a submodule pointer
staging    040000 tree   9e3bca9…  opendbc_repo     ← real files
staging    100644 blob   e69de29…  prebuilt         ← empty marker file
```

### Why `prebuilt` is the most important file in the system

The device's startup script, [`launch_chffrplus.sh:91`](../../launch_chffrplus.sh), does exactly this:

```bash
cd openpilot/system/manager
if [ ! -f $DIR/prebuilt ]; then
  ./build.py
fi
./manager.py
```

If `prebuilt` exists, **the build step is skipped entirely.** The car boots straight into running
the committed binaries.

The consequences drive at least four entries in the runbook's trap list, and they are all the same
underlying fact — *editing source does not necessarily change what runs*:

- Editing a C/C++ header like `params_keys.h` does **not** rebuild `libparams_c.so`. The header
  becomes documentation of a library that no longer matches it. (Symptom 2.)
- Editing a `.capnp` schema does **not** regenerate the already-built Python dataclasses that other
  prebuilt components import. A schema can load fine through `pycapnp` while the actual object you
  construct at runtime still has the old fields. (Symptom 10 — this one crash-looped `card` and
  produced EyeSight faults in the car.)
- Python *is* interpreted, so Python edits do take effect. Which means the boundary is invisible:
  some of your edits are live, some are inert, and nothing tells you which.

**Practical rule:** `/data/openpilot` on the car is the authority for what the APIs actually are.
Not this laptop's checkout, and not the newest sunnypilot source.

---

## 3. What actually runs on the comma

### The hardware

A **comma 3X** (internal codename `tizi`) is a Qualcomm Android-class SoC in a plastic case,
running **AGNOS**, comma's Linux distribution. It has two cameras (road + driver), a modem, GPS, and
— critically — it is *not* directly connected to your car's control buses. Between the computer and
the car sits the **panda**, a separate microcontroller.

```
   ┌───────────────────────────────────────────────┐
   │  comma 3X  (Linux, Python, neural nets)       │
   │                                               │
   │   camerad → modeld → plannerd → controlsd     │
   │                          │                    │
   │                        card                   │
   └──────────────────────────┼────────────────────┘
                              │ USB
   ┌──────────────────────────▼────────────────────┐
   │  panda  (microcontroller, C, no OS)           │
   │  ── independent safety checks ──              │  ← the safety boundary
   └──────────────────────────┼────────────────────┘
                              │ CAN
   ┌──────────────────────────▼────────────────────┐
   │  the Subaru: EyeSight, EPS, engine, brakes    │
   └───────────────────────────────────────────────┘
```

**The panda is a second opinion, not a relay.** It independently enforces limits — maximum steering
torque, rate of change, whether controls are even allowed right now. If the Linux side goes insane,
the panda refuses to pass the commands along. This is why the panda has its own firmware, its own
test suite, and its own rules, and why touching it is a completely different risk tier from
anything else in this document. **Your fork has never modified it**, and the build robot mechanically
rejects any change that tries (§7).

### The process model

There is no monolithic program. `openpilot/system/manager/manager.py` starts and supervises a few
dozen independent OS processes, declared in
[`process_config.py`](../../openpilot/system/manager/process_config.py). Each has a run condition —
`only_onroad`, `always_run`, `only_offroad`, and so on. A representative slice:

| Process | Job |
|---|---|
| `camerad` | pull frames off the cameras |
| `modeld` / `modeld_tinygrad` | run the driving neural network → a plan |
| `plannerd` | turn the plan into speed/path targets |
| `radard` | fuse radar returns into tracked leads |
| `controlsd` | turn targets into steering/accel commands |
| `card` | translate commands to/from **car**-specific CAN messages |
| `selfdrived` | state machine: engaged? disengaged? what alert? |
| `ui` | the screen |
| `paramsd`, `torqued`, `locationd`, `calibrationd` | continuously learn vehicle & sensor parameters |

sunnypilot adds its own: `modeld_tinygrad`, `mapd_manager`, `models_manager`, `sunnylink_*`, and more.

### How processes talk: cereal

Processes never call each other. They **publish and subscribe to named messages** over shared
memory, using Cap'n Proto for serialisation. The catalogue lives in
[`openpilot/cereal/services.py`](../../openpilot/cereal/services.py), where each entry declares an
expected frequency:

```python
"carState":    (True, 100., 10),   # 100 Hz
"modelV2":     (True,  20., None), #  20 Hz
"radarState":  (True,  20., 5),    #  20 Hz
"carControlSP":(True, 100., 10),   # sunnypilot's parallel message
```

Those declared rates are not documentation — they are **enforced**. If a publisher falls behind, the
system raises a communication-rate alert and hands control back to you. That is precisely what bit
you in symptom 6: the custom UI saturated a CPU core, `radard` slipped from 20 Hz to ~18.7 Hz, and
the car threw `TAKE CONTROL IMMEDIATELY`. Patch `0009` fixed it by moving `radard` and `plannerd` to
an idle core.

**The mental model that makes this intuitive:** it's a real-time pipeline where every stage has a
deadline. A feature that is merely *slow* is not a cosmetic problem — it is a safety event. This is
why the runbook insists on measuring CPU *before* changing anything visual.

---

## 4. Where the driving decision actually comes from

Worth stating plainly, because it reframes what your modifications are and aren't.

1. `modeld` runs a neural network on camera frames. Its output is not "turn the wheel 3°" — it's a
   predicted **path**, lane lines, road edges, lead vehicles, and a desired acceleration/curvature.
2. `plannerd` and `controlsd` convert that into concrete targets, applying limits and driver input.
3. `card` encodes those into the exact CAN frames a Subaru expects.
4. The panda decides whether to let them through.

Two consequences:

- **The driving quality is mostly the model's**, not the code around it. Which is why a sunnypilot
  release that swaps model weights (like the `37d6dc5` one currently sitting unpromoted) is a bigger
  deal than a release that changes ten thousand lines of Python.
- **Your modifications don't touch steps 1–2 at all.** Nothing in your stack changes how the car
  decides where to go. They touch how the *driver* toggles it (0001/0008), how the car's *stock*
  cruise is nudged (0004/0008), how the tuner is *seeded* (0005), how the plan is *displayed*
  (0002/0003/0010), and where processes *run* (0009). That's a meaningfully modest footprint, and
  it's a good story if you contribute.

---

## 5. Your car specifically: why it's the awkward case

The 2018 Outback is `SUBARU_OUTBACK_PREGLOBAL_2018` — Subaru's **pre-global** platform, the older
electrical architecture. Two facts follow, and nearly every custom patch exists because of them.

### Fact 1: stock EyeSight keeps longitudinal control

`openpilotLongitudinalControl` is **false**. openpilot steers; **Subaru's own EyeSight still does all
throttle and braking**, including following distance.

This is the single most counter-intuitive thing about your setup, and it explains a bug you hit:
sunnypilot's UI shows curve-speed tags (**SCC-V** vision-based, **SCC-M** map-based) and a Speed Limit
Assist target. On a car with openpilot longitudinal, the planner picks the lowest of those and
*slows the car*. On yours, **the planner has no throttle to command.** The tags light up and nothing
happens (symptom 8).

The only bridge is **ICBM** — synthesising cruise-button presses. And ICBM is deliberately
conservative: it follows *only* a validated posted speed limit, never the general planner target.
That restriction is not fussiness. When it was wired to the general target, the fallback value when
no limit is known is the **cruise ceiling**, so ICBM cheerfully walked the set speed toward 90 mph
(symptom 4). Patch `0008` made it fail closed.

### Fact 2: there is no LKAS button

On the 2020+ global Subarus, sunnypilot's MADS binds steering to a dedicated LKAS button. Preglobal
cars **don't have one**. The only latched, driver-visible signal available is **ACC main** (`Cruise_On`).

So on your car, ACC main *is* the steering toggle. Both userspace MADS and the panda watch that same
latched state, which is exactly why the implementation preserves it rather than inventing a private
one: if the two disagreed about whether lateral control is permitted, the panda wins and you get a
fault.

**The honest limitation:** main off kills steering *and* ACC availability together. You cannot have
"ACC on, steering off." Getting that would require a new verified button signal, MADS changes, **and
panda safety changes with the full safety test suite** — a different project, documented separately
in [`decoupled-toggle.md`](decoupled-toggle.md).

---

## 6. Your modifications

Nine patches plus one compatibility port. They apply **in a fixed order**, because later ones correct
earlier ones. `0007` existed and was retired on 2026-08-17 once upstream fixed the same bug — the
numbering gap is deliberate.

### The map

| # | Name | Layer | New files | Files it edits |
|---|---|---|---|---|
| 0001 | Driver-respecting ACC main | car port | `opendbc/sunnypilot/car/subaru/main_cruise.py` | Subaru `carcontroller.py` |
| 0002 | Offline vector map inset | UI | `map_panel.py`, `offline_map.py`, `offline.capnp` | UI state, HUD, settings, `params_keys.h` |
| 0003 | 3D driving scene | UI | `scene3d/` (6 files) | both `augmented_road_view.py`, settings, `params_keys.h` |
| 0004 | Preglobal ICBM | car port + control | `opendbc/sunnypilot/car/subaru/icbm.py` | `structs.py`, Subaru cc/interface, `custom.capnp`, `selfdrived.py`, `controlsd_ext.py` |
| 0005 | Outback torque anchor | tuning data | — | two `.toml` files |
| 0006 | Virginia speed watch | feature + UI | `reckless_watch/` (4 files), `reckless_border.py` | `custom.capnp`, HUD, settings, events, `params_keys.h` |
| port | Prebuilt Params compat | workaround | — | `common/params.py` |
| 0008 | Fail-closed ICBM + main arbitration | control | — | ICBM controller, `main_cruise.py`, Subaru `carcontroller.py` |
| 0009 | Planning off the UI core | runtime | — | `plannerd.py`, `radard.py` |
| 0010 | 3D mirror + UI crash fixes | UI | — | `scene3d/*`, developer UI `elements.py` |

*(`0011`, a large 3D-scene refinement, exists only on the unmerged `feat-refine-3d-scene` branch. It
has never been in a candidate build and has never run on the car.)*

### How each one hooks in

**0001 + 0008 — the ACC main toggle.** The stock preglobal `carcontroller` re-pressed cruise main
whenever it observed main off and EyeSight ready. It could not distinguish *"the driver turned this
off"* from *"openpilot turned this off a moment ago"*, so your press was undone in ~50 ms. The new
`PreglobalMainCruise` class **attributes** each transition and remembers a driver-requested off
state. `0008` adds priority: a physical driver press outranks the synthetic cancel that the press
itself provokes. This is a state machine, not a rule — which is why it took two patches.

**0004 + 0008 — ICBM for preglobal.** Adds a Subaru ICBM implementation that converts a resolved
speed limit into real cruise-button semantics, then `0008` clamps it to fail closed: it acts only
with resolver validity, a non-`none` source, a finite positive limit, openpilot enabled, stock ACC
ready, and no driver input. Any doubt → send nothing.

Note this patch touches `opendbc/car/structs.py` and `openpilot/cereal/custom.capnp` — i.e. it
**widens data structures**. That's the boundary that later bit you (symptom 10) when a *different*
change added a field to a prebuilt dataclass and crash-looped `card`.

**0005 — the torque anchor.** Purely two TOML values. Without it the heavy Outback inherits the
Impreza's lateral-acceleration factor (`1.067`); the patch sets its own `2.0 / 0.2` anchor. This is
a *seed for the self-tuner*, not a final measurement — `torqued` learns the real values while you
drive. The smallest and most obviously upstreamable patch in the stack.

**0009 — CPU affinity.** Moves `radard` and `plannerd` to core 6, away from the UI's saturated core 5.
Both moved together deliberately: radar fusion and planning are one model-synchronous chain, so
separating them would relocate the bottleneck rather than remove it.

**0002 / 0003 / 0010 — the visual features.** The map inset reuses geometry that sunnypilot's `mapd`
already downloads (no tiles, no API key, no network). The 3D scene replaces the camera view with a
rendered reconstruction from `modelV2` geometry. `0010` fixed a mirrored scene caused by a
coordinate-convention error worth memorising: **sunnypilot model output is Forward / Right / Down**,
not the Forward/Left/Up the original renderer assumed.

**0006 — Virginia watch.** A geofenced reminder for Virginia's reckless-driving thresholds
(absolute speed, and 20-over). Local, personal, advisory.

**The port — prebuilt Params compatibility.** Not a feature. Custom settings kept vanishing at
ignition because new keys were added to `params_keys.h`, which — per §2 — did **not** rebuild the
native library that validates keys. This supplies the type/default metadata in Python instead, for
your five keys only, preserving strict rejection of everything else.

---

## 7. The pipeline: what's on this laptop and what isn't

### The four places code lives

```
  ┌─ 1. THIS LAPTOP ────────────────────────────────────────────┐
  │  ~/projects/custompilot-public-shallow                      │
  │    • branch master  = the MAINTENANCE repo                  │
  │      └── .robert-custom/   ← patches, tests, tools, notes   │
  │          THIS is the only thing you actually author         │
  │    • submodules NOT checked out (they're gitlinks here)     │
  └───────────────────────┬─────────────────────────────────────┘
                          │  git push → PR → merge to master
                          ▼
  ┌─ 2. GITHUB ACTIONS (a fresh Ubuntu VM, daily at 10:17 UTC) ─┐
  │  fetch sunnypilot/staging → apply 9 patches + 1 port →      │
  │  validate → commit → force-push custompilot-staging         │
  └───────────────────────┬─────────────────────────────────────┘
                          │  MANUAL, protected promotion PR
                          ▼
  ┌─ 3. GITHUB: custompilot-stable ─────────────────────────────┐
  │  the install target. Moves only when you decide.            │
  └───────────────────────┬─────────────────────────────────────┘
                          │  the car's updater pulls
                          ▼
  ┌─ 4. THE COMMA 3X ───────────────────────────────────────────┐
  │  /data/openpilot — prebuilt, live, the API authority        │
  └─────────────────────────────────────────────────────────────┘
```

### The key insight about this design

**You never edit the fork's source. You edit a description of how to modify someone else's source.**

`.robert-custom/patches/*.patch` are unified diffs. The build robot checks out whatever sunnypilot
published *today* and re-applies them. That's why:

- a patch that worked for weeks can fail overnight with no action from you — upstream moved
  underneath it (symptoms 11 and 12, on consecutive days);
- a **red scheduled build is drift detection working**, not a broken pipeline;
- when it fails you ask *which kind* of drift: did upstream move code around (rewrite the patch), or
  did upstream **fix the same bug you were patching around** (delete the patch)?

### What the robot enforces

`apply_candidate.sh` applies in order with `--whitespace=error-all`. Then
[`verify_candidate.py`](../automation/verify_candidate.py) fails the build on:

| Check | Rejects |
|---|---|
| Protected paths | `openpilot/selfdrive/monitoring/`, `opendbc_repo/opendbc/safety/`, `panda/`, AGNOS files, `selfdrived/helpers.py` |
| Schema discipline | changes to stock cereal schemas instead of `custom.capnp` |
| Licensing | modified or missing `LICENSE` / `LICENSE.md`; missing acknowledgment |
| Provenance | a manifest that hides or omits patch-stack changes |

Then `validate_candidate.sh` runs the 10 focused test modules, byte-compiles every changed Python
file, and checks whitespace. Note what this does **not** do: it never starts a process, never talks
to a car, and never constructs a prebuilt runtime object. That gap is exactly how the fine-step
change passed CI and then crash-looped `card` on the vehicle (symptom 10).

### Things about the local setup worth knowing

- **Submodules are not checked out here.** On `master` they're gitlinks, so `opendbc_repo/`,
  `panda/`, `msgq_repo/` are empty directories. You cannot read the Subaru car port from a `master`
  checkout — read it from the `staging` tree instead (`git show 37d6dc5:opendbc_repo/...`), where
  it's flattened into real files.
- **There is no CI on maintenance PRs.** `update-candidate.yml` pins `ref: master` on checkout, so
  dispatching it against a PR branch rebuilds from `master` and proves nothing;
  `validate-custom-fork.yml` only fires on PRs into `custompilot-stable`. To test a change to
  `.robert-custom/` you must reproduce locally, or merge and watch. (Trap 25.)
- **Candidates are siblings, not a chain.** Each build parents its commit on the *stable* tip and
  force-pushes, so `custompilot-staging` can never be fast-forwarded. A hand-written fetch refspec
  without a leading `+` is silently refused and leaves you reading a stale commit. (Trap 22.)
- **~850 MB of untracked scratch lives inside `.robert-custom/`** — `sunnypilot/` (an old working
  clone, 583 MB), `assets/` (car-mesh sources, 101 MB), `.meshvenv/`. **None of it is gitignored.**
  I verified this with `git check-ignore`; the runbook's claim that the working clone is ignored is
  true of an older layout, not this one. Two live risks: a careless `git add -A` commits all of it,
  and `verify_candidate.py` counts untracked files as changes, so running validation locally against
  a dirty tree can give a different answer than CI. Export with `git archive` when testing locally.

---

## 8. If you want to contribute this upstream

### First: the licence you'd be contributing under

sunnypilot is **not** plain MIT. [`LICENSE.md`](../../LICENSE.md) is a "Custom MIT License",
© Haibin Wen / SUNNYPILOT LLC, which grants permission to view and modify but requires **explicit
written permission for commercial, for-profit, or closed-source use**, requires the licence notice
to survive redistribution, and requires a visible acknowledgment. Your fork already complies —
[`CUSTOM_FORK_NOTICE.md`](../automation/CUSTOM_FORK_NOTICE.md) carries the acknowledgment and the
robot fails the build if the licence files are touched.

For contributing this mostly means: you're offering work into a project with a non-standard licence,
so read it before you sign anything, and don't assume "open source" implies the permissions you're
used to from MIT or Apache.

### Second: an honest triage of what's actually shareable

| Patch | Upstream-worthy? | Why |
|---|---|---|
| **0005** torque anchor | **Yes, easily.** | Two TOML values in exactly the format upstream maintains. This is the shape of contribution sunnypilot/opendbc accept routinely. Best first PR. |
| **0001 + 0008** ACC main toggle | **Yes, with work.** | Genuinely useful to *every* preglobal Subaru owner — they all lack an LKAS button. Lives in the correct `opendbc/sunnypilot/car/subaru/` location. Needs: the two patches merged into one coherent change, tests upstream will run, and a clear write-up of the "main off kills ACC too" limitation. |
| **0004** preglobal ICBM | **Plausible.** | Same audience. But it edits `opendbc/car/structs.py` and `custom.capnp` — widening shared structures is a much bigger ask and invites the prebuilt/dataclass problem for others. Expect real review. |
| **0009** CPU affinity | **Discuss first.** | The *diagnosis* (planning starved by UI on core 5) is valuable. The *fix* is tuned to your device and your UI load; upstream may not want a hardcoded core assignment. Contribute the finding, offer the patch. |
| **0002 / 0003 / 0010** visual features | **Probably not as-is.** | Large, opinionated, and they replace core UI. Forks usually keep these. `0010`'s coordinate-convention fix and the `liveValid` read, however, were **real upstream bugs** — those are worth reporting even if the features aren't. |
| **0006** Virginia watch | **No.** | Correctly hyper-local. Could inspire a generic geofenced-reminder feature; the Virginia specifics wouldn't travel. |
| **port** Params compat | **No.** | A workaround for a prebuilt snapshot, not a fix. Upstream builds from source and doesn't have the problem. |

### Third: the blockers to clear before publishing anything

1. **The car mesh licence (blocking, on the `0011` branch).**
   `scene3d/assets/outback.gltf` is derived from a **third-party** 2022 Outback model downloaded
   without a licence file. Origin, author, and terms are unestablished. **Until that's resolved the
   asset must not be published.** The code fails closed — a missing file falls back to the
   procedural car — so deleting it is a complete, safe remedy, not a breakage. Do not let this one
   ride; it's the only item here that's someone else's legal right rather than your engineering call.
2. **Decide what you're claiming.** Several patches are *anchors and workarounds*, not finished
   engineering: the torque values are a learning seed, wheel-speed calibration is unmeasured, and
   exact-mph ICBM was reverted after crashing `card`. Present them as what they are.
3. **`0011` has never run on the car.** Written, audited, committed — never installed, never driven.
   Don't offer it as validated.
4. **Attribution for AI assistance.** Already disclosed in `CUSTOM_FORK_NOTICE.md`. Some projects
   have specific policies; check sunnypilot's contribution guidelines before opening a PR.

### Fourth: what to expect mechanically

sunnypilot runs real CI on PRs — the `tests`, `diff report`, and `PR review` workflows you can see
firing on upstream PRs in this repo's Actions tab, plus a Jenkins trigger for on-device tests.
Anything touching a car port will be expected to pass their car tests. Anything touching
`opendbc/safety/` or `panda/` triggers a much heavier safety-review obligation — which your stack
has deliberately never done, and that's a genuine selling point when you describe it.

---

## 9. A short glossary

| Term | Meaning |
|---|---|
| **AGNOS** | comma's Linux distribution for the device |
| **tizi** | internal codename for the comma 3X |
| **panda** | the safety microcontroller between computer and car |
| **cereal** | the inter-process message system (Cap'n Proto over shared memory) |
| **capnp** | Cap'n Proto — the serialisation format defining message shapes |
| **opendbc** | the car-specific layer: CAN definitions and per-brand ports |
| **MADS** | sunnypilot's decoupled lateral (steering) control mode |
| **ICBM** | sunnypilot pressing your stock cruise buttons electronically |
| **SCC-V / SCC-M** | curve-speed candidates from vision / map. Display-only on your car |
| **preglobal** | Subaru's pre-2020 electrical platform — yours |
| **prebuilt** | marker file meaning "binaries are committed, skip the build" |
| **`…SP` messages** | sunnypilot's parallel messages beside comma's |
| **candidate** | an automatically built, validated, unpromoted tree on `custompilot-staging` |
| **promotion** | the manual, protected move from staging to `custompilot-stable` |
| **drift** | upstream changing code underneath a patch that used to apply |

---

## 10. Where to go next

- **Before touching the car:** [`device-journey-runbook.md`](device-journey-runbook.md) — the
  known-good state, the 25 traps, and the safe-change procedure. Non-optional.
- **The exact lateral control chain:** [`call-trace.md`](call-trace.md)
- **Why ACC-on/steering-off is a safety project:** [`decoupled-toggle.md`](decoupled-toggle.md)
- **Fork policy and protected surfaces:** [`customization-and-risk.md`](customization-and-risk.md)
- **Per-feature deep dives:** [`map-panel.md`](map-panel.md),
  [`torque-tuning.md`](torque-tuning.md), [`virginia-watch.md`](virginia-watch.md)

### The two habits worth keeping

The failures in this project's history divide almost perfectly into two categories, and both have
the same remedy.

**Assuming source equals runtime.** Edited a header, assumed the library changed. Edited a schema,
assumed the dataclass changed. Compiled successfully, assumed it would construct. Every one of these
was caught only by exercising the real thing on the real device.

**Trusting one source.** A test that only ever ran against fixed code. A `rev-parse` on a ref that
silently failed to update. A green CI run that never started a process. Every one of these was
caught only by checking something *independent* — running the test against known-bad code, reading
the push line in the run log, watching `card` stay alive on the car.

So: **prove it on the device, and confirm it twice.** Nearly everything else in the runbook is a
special case of those two.
