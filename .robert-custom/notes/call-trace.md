# Call trace: what controls lateral on a preglobal Subaru

All paths relative to a `sunnypilot` checkout of `staging` @ `c691111`.

## Platform identification

`opendbc_repo/opendbc/car/subaru/values.py:191-196`

```python
SUBARU_OUTBACK_PREGLOBAL_2018 = SubaruPlatformConfig(
  [SubaruCarDocs("Subaru Outback 2018-19")],
  SUBARU_FORESTER_PREGLOBAL.specs,
  {Bus.pt: 'subaru_outback_2019_generated'},
  flags=SubaruFlags.PREGLOBAL,
)
```

A 2018 Outback is preglobal — the global platform starts with the 2020 Outback. Also note
`interface.py:78-80`: this platform and the preglobal Forester get
`PREGLOBAL_REVERSED_DRIVER_TORQUE`.

### Why sunnypilot and not upstream openpilot

`opendbc_repo/opendbc/car/subaru/interface.py:21` marks preglobal dashcam-only:

```python
ret.dashcamOnly = bool(ret.flags & (SubaruFlags.PREGLOBAL | SubaruFlags.LKAS_ANGLE | SubaruFlags.HYBRID))
```

sunnypilot's `_get_params_sp` overrides it at `interface.py:105`, dropping `PREGLOBAL` from the
mask. That is why this fork drives the car and upstream doesn't.

Also relevant: `alphaLongitudinalAvailable` excludes `PREGLOBAL` (`interface.py:90-92`), so
`openpilotLongitudinalControl` is always False here — longitudinal is stock EyeSight ACC, and
openpilot only steers plus spams stop-and-go resume.

## Lateral engagement chain

```
CruiseControl 0x144 bit 48 (Cruise_On)
  └─> carstate.py:103        ret.cruiseState.available
        ├─> mads.py:156-176  lkasEnable / lkasDisable events   (userspace)
        │     └─> state.py   MADS state machine -> enabled / active
        │           └─> CC.latActive -> carcontroller.py:45-49 -> ES_LKAS torque
        └─> subaru_preglobal.h:45  acc_main_on                  (panda)
              └─> safety.h:385 mads_state_update(...)
                    └─> mads.h:87-96  controls_allowed_lateral
```

Both layers latch on the rising edge of ACC main and drop on the falling edge. They agree, and
`mads.py:105-113` (`data_sample`) actively disengages if they disagree for 200 frames.

## The interfering write

`opendbc_repo/opendbc/car/subaru/carcontroller.py:80-97`, pre-fix:

```python
if self.CP.flags & SubaruFlags.PREGLOBAL:
  if self.frame % 5 == 0:
    if pcm_cancel_cmd:
      cruise_button = 1
    elif not CS.out.cruiseState.available and CS.ready:
      cruise_button = 1
    else:
      cruise_button = CS.cruise_button

    if cruise_button == 1 and self.cruise_button_prev == 1:
      cruise_button = 0
    self.cruise_button_prev = cruise_button

    can_sends.append(subarucan.create_preglobal_es_distance(self.packer, cruise_button, CS.es_distance_msg))
```

`Cruise_Button` semantics, from `opendbc/dbc/generator/subaru/_subaru_preglobal_2015.dbc:228`:

```
CM_ SG_ 353 Cruise_Button "1 = main, 2 = set shallow, 3 = set deep, 4 = resume shallow, 5 resume deep";
```

`CS.cruise_button` is the camera's value passed through (`carstate.py:118`), `CS.ready` is
`not ES_DashStatus->Not_Ready_Startup` (`carstate.py:119`). The `%5` cadence means at 100 Hz
openpilot re-presses main within 50 ms of the driver's press, and the unstick line makes it a
clean 1/0/1/0 sequence of distinct presses.

`pcm_cancel_cmd` comes from `openpilot/selfdrive/controls/controlsd.py:181`:

```python
CC.cruiseControl.cancel = CS.cruiseState.enabled and (not CC.enabled or not self.CP.pcmCruise)
```

Because `Cruise_On` (bit 48) and `Cruise_Activated` (bit 49) live in the same message and fall
together on a main press, a driver press normally does *not* raise `cancel` — but relying on
that timing would be fragile, which is why the fix attributes transitions by tracking
openpilot's own presses rather than by watching `pcm_cancel_cmd`.

## MADS button sources, by platform

| | button signal | userspace | panda |
|---|---|---|---|
| Subaru global | `ES_LKAS_State->LKAS_Dash_State` | `sunnypilot/car/subaru/mads.py:46` | `safety/modes/subaru.h:113` |
| Subaru preglobal | none | — | — |
| Hyundai w/ LDA, CAN-FD | LDA button | `mads.py:41-43` sets `allow_always` | brand safety |
| Tesla | screen button | `allow_always` | brand safety |
| Rivian, Tesla w/o vehicle bus | none | `MADS_NO_ACC_MAIN_BUTTON`, `helpers.py:15` | — |

Preglobal Subaru is the only case with neither a button nor a `no_main_cruise` exemption, so
ACC main is its whole lateral UI.

## Signals available on this car for a future dedicated button

From `_subaru_preglobal_2015.dbc`, `CruiseControl` (0x144, powertrain bus) carries raw wheel
button bits that openpilot does not currently parse:

```
BO_ 324 CruiseControl: 8 XXX
 SG_ OnOffButton : 2|1@1+ (1,0) [0|1] "" XXX
 SG_ SET_BUTTON  : 3|1@1+ (1,0) [0|1] "" XXX
 SG_ RES_BUTTON  : 4|1@1+ (1,0) [0|1] "" XXX
 SG_ Button      : 13|1@1+ (1,0) [0|1] "" XXX
 SG_ Cruise_On   : 48|1@1+ (1,0) [0|1] "" XXX
 SG_ Cruise_Activated : 49|1@1+ (1,0) [0|1] "" XXX
```

`ES_DashStatus->Cruise_Distance` (0x166, 3 bits) tracks the follow-distance setting, so distance
button presses are observable as changes in that value.

None of these are verified on a real 2018 Outback — they are DBC entries, not measurements.
Check them in a route with cabana before building anything on them:

```bash
cd /path/to/sunnypilot && tools/cabana/cabana --demo
```

or open one of your own routes and watch `CruiseControl` byte 0 while pressing the buttons.
