import math

import pytest

from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.accel_controller.constants import (
  LEAD_SAMPLE_FILTER_FRAMES, COMFORT_DECEL, DISTANCE_JUMP_CONFIRM_FRAMES, LEAD_DROPOUT_COAST_TIME, LEAD_RELEASE_CONFIRM_TIME,
  STOP_HOLD_EXIT_FRAMES, TARGET_RELEASE_SLEW, AccelProfile,
)
from openpilot.sunnypilot.selfdrive.controls.lib.accel_controller.lead import LeadPlan
from openpilot.sunnypilot.selfdrive.controls.lib.accel_controller.lead_controller import LeadController

DT = DT_MDL
COMFORT_DECEL_NORMAL = COMFORT_DECEL[AccelProfile.normal]
PROFILE_MAX_ACCEL = 1.5


def _frames(seconds: float) -> int:
  return math.ceil(seconds / DT)


LEAD_CONFIRM_FRAMES = max(LEAD_SAMPLE_FILTER_FRAMES, _frames(LEAD_RELEASE_CONFIRM_TIME))
DROPOUT_FRAMES = max(LEAD_CONFIRM_FRAMES, _frames(LEAD_DROPOUT_COAST_TIME))


def _lead_plan(speed: float, distance: float, speed_ceiling: float = 0.0, closing_speed: float = 0.0,
              required_decel: float = 0.0, track_id: int = 1) -> LeadPlan:
  return LeadPlan(
    speed_ceiling=speed_ceiling, selected_lead=0, selected_lead_track_id=track_id, selected_lead_speed=speed, selected_lead_accel=0.0,
    departure_lead=0, departure_lead_track_id=track_id, departure_lead_speed=speed, departure_lead_distance=distance,
    departure_lead_raw_speed=speed,
    departure_lead_separation=distance, departure_speed_ceiling=speed_ceiling,
    closing_speed=closing_speed, required_decel=required_decel,
    has_nearly_stopped_lead=speed < 0.15, lead_status=True,
  )


def _no_lead() -> LeadPlan:
  return LeadPlan(lead_status=False)


def _run(lead_controller: LeadController, lead_plan: LeadPlan, base_speed: float, v_ego: float, planner_speed: float | None = None,
        planner_accel: float = 0.0, previous_mpc_source=None, previous_plan_accel: float = 0.0) -> float:
  return lead_controller.update(lead_plan, base_speed, v_ego, COMFORT_DECEL_NORMAL, PROFILE_MAX_ACCEL, DT,
                      LEAD_CONFIRM_FRAMES, DROPOUT_FRAMES, v_ego if planner_speed is None else planner_speed,
                      planner_accel, previous_mpc_source, previous_plan_accel)


def test_repeated_stop_reseeds_departure_distance():
  lead_controller = LeadController()
  for _ in range(20):
    _run(lead_controller, _lead_plan(0.0, 6.0), base_speed=8.0, v_ego=0.0)
  for frame in range(20):
    _run(lead_controller, _lead_plan(2.0, 6.0 + 2.0 * (frame + 1) * DT), base_speed=8.0, v_ego=min(3.5, frame * 0.3))

  for _ in range(10):
    _run(lead_controller, _lead_plan(0.0, 3.0), base_speed=8.0, v_ego=0.0)
  assert lead_controller.stop_hold

  released_frame = None
  for frame in range(60):
    _run(lead_controller, _lead_plan(0.2, 3.0 + 0.2 * (frame + 1) * DT), base_speed=8.0, v_ego=0.0)
    if not lead_controller.stop_hold:
      released_frame = frame
      break

  assert released_frame is not None
  assert released_frame * DT <= 2.0


def test_departure_dropout_reseeds_distance_guard():
  lead_controller = LeadController()
  for _ in range(20):
    _run(lead_controller, _lead_plan(0.0, 6.0), base_speed=8.0, v_ego=0.0)
  _run(lead_controller, _no_lead(), base_speed=8.0, v_ego=0.0)

  released_frame = None
  for frame in range(60):
    _run(lead_controller, _lead_plan(0.2, 3.0 + 0.2 * (frame + 1) * DT), base_speed=8.0, v_ego=0.0)
    if not lead_controller.stop_hold:
      released_frame = frame
      break

  assert released_frame is not None
  assert released_frame * DT <= 2.0


def test_departure_dropout_revokes_track_identity_trust():
  lead_controller = LeadController()
  for _ in range(20):
    _run(lead_controller, _lead_plan(0.0, 6.0, track_id=100), base_speed=8.0, v_ego=0.0)

  _run(lead_controller, _no_lead(), base_speed=8.0, v_ego=0.0)
  for frame in range(4):
    _run(lead_controller, _lead_plan(2.0, 20.0 + 0.1 * frame, speed_ceiling=8.0, track_id=100), base_speed=8.0, v_ego=0.0)

  assert lead_controller.launching
  assert lead_controller.leadless_departure
  assert not lead_controller.departure_launching


def test_range_discontinuity_revokes_track_identity_trust():
  lead_controller = LeadController()
  for _ in range(20):
    _run(lead_controller, _lead_plan(0.0, 6.0, track_id=100), base_speed=8.0, v_ego=0.0)

  for frame in range(DISTANCE_JUMP_CONFIRM_FRAMES + STOP_HOLD_EXIT_FRAMES + 2):
    _run(lead_controller, _lead_plan(2.0, 20.0 + 0.1 * frame, speed_ceiling=8.0, track_id=100), base_speed=8.0, v_ego=0.0)
    if not lead_controller.stop_hold:
      break

  assert lead_controller.launching
  assert lead_controller.leadless_departure
  assert not lead_controller.departure_launching


def test_fast_lead_speed_requires_full_departure_distance():
  lead_controller = LeadController()
  for _ in range(20):
    _run(lead_controller, _lead_plan(0.0, 6.0), base_speed=8.0, v_ego=0.0)

  for distance in (6.00, 6.01, 6.02, 6.03):
    target = _run(lead_controller, _lead_plan(1.0, distance), base_speed=8.0, v_ego=0.0)

  assert lead_controller.stop_hold
  assert target == 0.0


def test_lead_disappearance_releases_stop_hold_monotonically():
  lead_controller = LeadController()
  for _ in range(20):
    _run(lead_controller, _lead_plan(0.0, 6.0), base_speed=8.0, v_ego=0.0)

  targets = [_run(lead_controller, _no_lead(), base_speed=8.0, v_ego=0.0) for _ in range(DROPOUT_FRAMES + 10)]
  first_release = next(index for index, target in enumerate(targets) if target > 0.0)
  release_targets = targets[first_release:]

  steps = [after - before for before, after in zip(release_targets[:-1], release_targets[1:], strict=True)]
  assert all(0.0 <= step <= TARGET_RELEASE_SLEW * DT + 1e-9 for step in steps)


def test_no_lead_departure_stays_bounded_when_lead_returns():
  lead_controller = LeadController()
  for _ in range(20):
    _run(lead_controller, _lead_plan(0.0, 6.0), base_speed=8.0, v_ego=0.0)
  for _ in range(LEAD_CONFIRM_FRAMES):
    _run(lead_controller, _no_lead(), base_speed=8.0, v_ego=0.0)

  before = lead_controller.target_speed
  target = _run(lead_controller, _lead_plan(2.0, 6.0, speed_ceiling=8.0), base_speed=8.0, v_ego=0.5)

  assert target <= max(before, 0.5) + TARGET_RELEASE_SLEW * DT + 1e-9


def test_leadless_stop_release_never_exceeds_current_base_speed():
  lead_controller = LeadController()
  for _ in range(20):
    _run(lead_controller, _lead_plan(0.0, 6.0), base_speed=8.0, v_ego=0.0)

  targets = [_run(lead_controller, _no_lead(), base_speed=0.5, v_ego=4.0) for _ in range(LEAD_CONFIRM_FRAMES)]

  assert max(targets) <= 0.5


def test_retained_speed_ceiling_does_not_bind_a_moving_replacement():
  lead_controller = LeadController()
  _run(lead_controller, _lead_plan(0.0, 5.0, speed_ceiling=0.1, track_id=100), base_speed=8.0, v_ego=0.4)

  _run(lead_controller, _lead_plan(2.0, 20.0, speed_ceiling=8.0, track_id=200), base_speed=8.0, v_ego=0.2)

  assert lead_controller.lead_speed_ceiling == pytest.approx(0.1)
  assert not lead_controller.stop_hold


def test_braking_state_does_not_cross_confirmed_track_replacement():
  lead_controller = LeadController()
  for _ in range(LEAD_CONFIRM_FRAMES + 2):
    _run(lead_controller, _lead_plan(8.0, 20.0, speed_ceiling=5.0, track_id=10), base_speed=15.0, v_ego=10.0, planner_accel=-0.5)
  assert lead_controller.braking_for_lead

  _run(lead_controller, _lead_plan(0.0, 25.0, speed_ceiling=4.0, track_id=99), base_speed=8.0, v_ego=0.0, planner_accel=-0.5)

  assert not lead_controller.stop_hold


def test_braking_state_does_not_cross_vision_slot_replacement():
  lead_controller = LeadController()
  for _ in range(LEAD_CONFIRM_FRAMES + 2):
    _run(lead_controller, _lead_plan(8.0, 20.0, speed_ceiling=5.0, track_id=-1), base_speed=15.0, v_ego=10.0, planner_accel=-0.5)
  assert lead_controller.braking_for_lead

  replacement = _lead_plan(0.0, 25.0, speed_ceiling=4.0, track_id=-1)._replace(selected_lead=1, departure_lead=1)
  _run(lead_controller, replacement, base_speed=8.0, v_ego=0.0, planner_accel=-0.5)

  assert not lead_controller.stop_hold


def test_radar_track_keeps_braking_confirmation_across_slots():
  lead_controller = LeadController()
  for frame in range(LEAD_CONFIRM_FRAMES + 2):
    plan = _lead_plan(0.0, 7.0, speed_ceiling=0.8, track_id=100)
    if frame % 2:
      plan = plan._replace(selected_lead=1, departure_lead=1)
    _run(lead_controller, plan, base_speed=8.0, v_ego=0.0, planner_accel=-0.5)

  assert lead_controller.braking_for_lead
  assert lead_controller.stop_hold


def test_vision_slot_churn_releases_conservatively():
  lead_controller = LeadController()
  for _ in range(20):
    _run(lead_controller, _lead_plan(0.0, 6.0, track_id=-1), base_speed=8.0, v_ego=0.0)

  for frame in range(STOP_HOLD_EXIT_FRAMES + 2):
    plan = _lead_plan(2.0, 6.0 + 0.1 * (frame + 1), speed_ceiling=8.0, track_id=-1)
    if frame % 2:
      plan = plan._replace(selected_lead=1, departure_lead=1)
    _run(lead_controller, plan, base_speed=8.0, v_ego=0.0)
    if not lead_controller.stop_hold:
      break

  assert lead_controller.launching
  assert lead_controller.leadless_departure
  assert not lead_controller.departure_launching


def test_radar_track_dropout_keeps_braking_context():
  lead_controller = LeadController()
  for _ in range(LEAD_CONFIRM_FRAMES + 2):
    _run(lead_controller, _lead_plan(8.0, 20.0, speed_ceiling=5.0, track_id=10), base_speed=15.0, v_ego=10.0, planner_accel=-0.5)
  assert lead_controller.braking_for_lead

  _run(lead_controller, _no_lead(), base_speed=15.0, v_ego=10.0, planner_accel=-0.5)

  assert lead_controller.should_coast_on_dropout


def test_range_replacement_starts_a_new_departure_baseline():
  lead_controller = LeadController()
  for _ in range(20):
    _run(lead_controller, _lead_plan(0.0, 6.0), base_speed=8.0, v_ego=0.0)
  for _ in range(2):
    _run(lead_controller, _lead_plan(0.2, 6.0), base_speed=8.0, v_ego=0.0)

  released_frame = None
  for frame in range(60):
    distance = 3.0 + 0.2 * frame * DT
    _run(lead_controller, _lead_plan(0.2, distance), base_speed=8.0, v_ego=0.0)
    if not lead_controller.stop_hold:
      released_frame = frame
      break

  assert released_frame is not None
  assert released_frame * DT <= 2.0


def test_stop_hold_clears_the_previous_recovery_limit():
  lead_controller = LeadController()
  for _ in range(LEAD_CONFIRM_FRAMES + 120):
    _run(lead_controller, _lead_plan(5.0, 20.0, speed_ceiling=8.0), base_speed=12.0, v_ego=10.0)
  assert lead_controller.recovery_accel_limit == pytest.approx(0.0)

  _run(lead_controller, _lead_plan(0.0, 5.0), base_speed=8.0, v_ego=0.2, planner_accel=-0.5)
  assert lead_controller.stop_hold
  assert lead_controller.recovery_accel_limit is None

  for frame in range(9):
    _run(lead_controller, _lead_plan(1.0, 5.0 + 0.04 * frame, speed_ceiling=8.0), base_speed=8.0, v_ego=0.0)
  assert lead_controller.departure_launching

  _run(lead_controller, _lead_plan(5.0, 8.0, speed_ceiling=8.0), base_speed=8.0, v_ego=3.1)
  assert lead_controller.recovery_accel_limit is not None
  assert lead_controller.recovery_accel_limit > 1.4


def test_speed_ceiling_tightens_immediately():
  lead_controller = LeadController()
  _run(lead_controller, _lead_plan(20.0, 100.0, speed_ceiling=25.0), base_speed=30.0, v_ego=20.0)
  assert lead_controller.lead_speed_ceiling == pytest.approx(25.0)
  _run(lead_controller, _lead_plan(15.0, 40.0, speed_ceiling=10.0), base_speed=30.0, v_ego=20.0)
  assert lead_controller.lead_speed_ceiling == pytest.approx(10.0)


def test_speed_ceiling_requires_confirmation_before_release():
  lead_controller = LeadController()
  for _ in range(LEAD_SAMPLE_FILTER_FRAMES + 2):
    _run(lead_controller, _lead_plan(20.0, 100.0, speed_ceiling=20.0), base_speed=30.0, v_ego=20.0)
  assert lead_controller.lead_speed_ceiling == pytest.approx(20.0)

  glitch_len = LEAD_CONFIRM_FRAMES - 2
  for _ in range(glitch_len):
    _run(lead_controller, _lead_plan(20.0, 100.0, speed_ceiling=28.0), base_speed=30.0, v_ego=20.0)
  assert lead_controller.lead_speed_ceiling == pytest.approx(20.0), "brief relief spike must not be trusted"

  for _ in range(LEAD_SAMPLE_FILTER_FRAMES + 2):
    _run(lead_controller, _lead_plan(20.0, 100.0, speed_ceiling=20.0), base_speed=30.0, v_ego=20.0)
  assert lead_controller.lead_speed_ceiling == pytest.approx(20.0)

  for _ in range(LEAD_CONFIRM_FRAMES + LEAD_SAMPLE_FILTER_FRAMES + 2):
    _run(lead_controller, _lead_plan(20.0, 100.0, speed_ceiling=28.0), base_speed=30.0, v_ego=20.0)
  assert lead_controller.lead_speed_ceiling == pytest.approx(28.0), "sustained relief must eventually be trusted"


def test_conflicting_relief_evidence_stays_restricted():
  lead_controller = LeadController()
  for _ in range(LEAD_SAMPLE_FILTER_FRAMES + 2):
    _run(lead_controller, _lead_plan(20.0, 100.0, speed_ceiling=20.0), base_speed=30.0, v_ego=20.0)

  targets = []
  for frame in range(300):
    speed_ceiling = 28.0 if frame % 2 == 0 else 20.0
    targets.append(_run(lead_controller, _lead_plan(20.0, 100.0, speed_ceiling=speed_ceiling), base_speed=30.0, v_ego=20.0))

  assert lead_controller.lead_speed_ceiling == pytest.approx(20.0)
  assert max(targets) == pytest.approx(20.0)


def test_lead_dropout_holds_speed_ceiling_before_release():
  lead_controller = LeadController()
  for _ in range(LEAD_SAMPLE_FILTER_FRAMES + 2):
    _run(lead_controller, _lead_plan(6.0, 50.0, speed_ceiling=6.0), base_speed=18.0, v_ego=10.0)
  assert lead_controller.lead_speed_ceiling == pytest.approx(6.0)

  for _ in range(DROPOUT_FRAMES - 1):
    _run(lead_controller, _no_lead(), base_speed=18.0, v_ego=10.0)
  assert lead_controller.lead_speed_ceiling == pytest.approx(6.0), "must coast, not snap, before the dropout window elapses"

  for _ in range(LEAD_SAMPLE_FILTER_FRAMES + 2):
    _run(lead_controller, _no_lead(), base_speed=18.0, v_ego=10.0)
  assert math.isinf(lead_controller.lead_speed_ceiling), "must release promptly once the dropout window has elapsed"


def test_target_release_is_rate_limited():
  lead_controller = LeadController()
  for _ in range(LEAD_SAMPLE_FILTER_FRAMES + 2):
    _run(lead_controller, _lead_plan(20.0, 60.0, speed_ceiling=22.0), base_speed=30.0, v_ego=22.0)
  before = lead_controller.target_speed

  target = _run(lead_controller, _lead_plan(28.0, 200.0, speed_ceiling=30.0), base_speed=30.0, v_ego=22.0)
  assert target <= before + TARGET_RELEASE_SLEW * DT + 1e-9
