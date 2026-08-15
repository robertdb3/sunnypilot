# If you want lateral off while ACC stays available

The shipped fix couples the two: ACC main off means both cruise and steering off. On this car
that is honest, because ACC main *is* the master switch. If what you actually want is "keep
adaptive cruise, steer it myself", that needs a lateral toggle that isn't the main button.

This is untested work, listed here so the tradeoffs are on the record. Don't run it on the road
without checking the signals in cabana first.

## What has to change

### 1. A button source

Candidates on a preglobal Outback, none verified on a real car:

- **`CruiseControl->OnOffButton`** (0x144 bit 2) — the physical main press, distinct from the
  latched `Cruise_On` state. Risk: unknown whether the ECU also sets this bit in response to
  openpilot's mocked `ES_Distance` press, which would produce phantom toggles.
- **`ES_DashStatus->Cruise_Distance`** (0x166 bits 21-23) — the follow-distance setting. A
  change means the distance button was pressed. Genuinely independent of main, but every
  lateral toggle would also cycle your ACC follow distance. Risk: it may not respond while
  main is off.
- **A double press of main within ~1 s** — needs no new signals, but is awkward and slow.

`Cruise_Distance` is the only one that gives real independence. Confirm in a route first:
plot `Cruise_Distance` while pressing the distance button with cruise on and with cruise off.

### 2. Userspace MADS wiring

`opendbc/sunnypilot/car/subaru/mads.py` — extend `update_mads` with a preglobal branch that
sets `self.lkas_button` from the chosen signal and emits `ButtonType.lkas` on the rising edge.
The existing `create_lkas_button_events` is written around the global cars'
`LKAS_Dash_State` value encoding (1/2), so preglobal wants its own edge detector, not that one.

`openpilot/sunnypilot/mads/mads.py`:

- add subaru-preglobal to `MADS_NO_ACC_MAIN_BUTTON` (`helpers.py:15`) or add an equivalent
  per-platform flag, so `self.no_main_cruise` is True and main off stops forcing `lkasDisable`
  (`mads.py:174-177`)
- set `self.allow_always = True` for the platform (`mads.py:41-46`), so the button works with
  main off
- with `no_main_cruise` set, `MadsMainCruiseAllowed` is removed from params
  (`helpers.py:74-76`), which is what you want — main should no longer drive lateral at all

### 3. Panda safety

This is the part that makes it a real project rather than a patch. `opendbc/safety/sunnypilot/mads.h:94-96`
drops `controls_allowed_lateral` on every `acc_main` falling edge, so userspace changes alone
would trip the 200-frame mismatch check in `mads.py:105-113` and disengage anyway.

`opendbc/safety/modes/subaru_preglobal.h` needs to:

- set `mads_button_press = MADS_BUTTON_PRESSED` from the same signal userspace uses, the way
  `subaru.h:110-115` does for global cars
- stop feeding `acc_main_on` from `Cruise_On`, or otherwise exempt this platform from the
  ACC-main-off disengage

That is a change to the safety firmware. It needs the opendbc safety test suite
(`opendbc/safety/tests/`) extended and passing, and it is the kind of change that should go
through a sunnypilot PR and review rather than a local patch.

## Recommendation

Run the shipped fix first. If coupled behavior turns out to be what you wanted, you're done.
If you still want the decoupled version after driving it, the ordered path is: confirm
`Cruise_Distance` (or `OnOffButton`) behaves in cabana → userspace wiring → safety change with
tests → PR to sunnypilot, since preglobal Subaru owners all have this gap.
