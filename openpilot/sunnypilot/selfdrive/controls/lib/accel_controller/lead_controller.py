"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

import math

import numpy as np

from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import LongitudinalPlanSource
from openpilot.sunnypilot.selfdrive.controls.lib.accel_controller.constants import (
  BRAKING_ACCEL_THRESHOLD, LEAD_SAMPLE_FILTER_FRAMES, DISTANCE_JUMP_CONFIRM_FRAMES, LAUNCH_END_SPEED, LAUNCH_TARGET_HEADROOM,
  LEAD_RECOVERY_ACCEL_SLEW, LEAD_RECOVERY_HEADROOM, LEAD_RECOVERY_DECEL_RATE, SPEED_DEADBAND, STOP_HOLD_CREEP_DISTANCE,
  STOP_HOLD_EGO_SPEED, STOP_HOLD_EXIT_FRAMES, STOP_HOLD_MAX_LEAD_DISTANCE, STOP_HOLD_SPEED_FLOOR, TARGET_RELEASE_SLEW,
)
from openpilot.sunnypilot.selfdrive.controls.lib.accel_controller.lead import LeadPlan


def _median(samples: list[float]) -> float:
  return sorted(samples)[len(samples) // 2]


def _slew(current: float, target: float, rate: float, dt: float) -> float:
  return float(np.clip(target, current - rate * dt, current + rate * dt))


def _max_distance_step(lead_speed: float, dt: float) -> float:
  return max(STOP_HOLD_CREEP_DISTANCE / 2.0, 3.0 * max(lead_speed, 0.0) * dt)


def _same_lead(first: int, first_track_id: int, second: int, second_track_id: int) -> bool:
  if first < 0 or second < 0:
    return False
  if first_track_id >= 0 or second_track_id >= 0:
    return first_track_id >= 0 and first_track_id == second_track_id
  return first == second


class LeadController:
  def __init__(self) -> None:
    self.lead_speed_samples = [math.inf] * LEAD_SAMPLE_FILTER_FRAMES
    self.lead_accel_samples = [0.0] * LEAD_SAMPLE_FILTER_FRAMES

    self.lead_speed_ceiling = math.inf
    self.release_confirm_frames = 0
    self.lead_loss_frames = 0
    self._dropout_was_restricting = False
    self._dropout_was_braking = False

    self.target_speed: float | None = None
    self.e2e_braking_handoff = False
    self.lead_recovery = False
    self.recovery_accel_limit: float | None = None

    self.stop_hold = False
    self.launching = False
    self.departure_launching = False
    self.leadless_departure = False
    self.held_lead = -1
    self.held_lead_track_id = -1
    self.held_lead_trusted = False
    self.departure_confirm_frames = 0
    self.no_departure_lead_frames = 0
    self.departure_distance_ref: float | None = None
    self.last_departure_distance: float | None = None
    self.pending_distance_jump: float | None = None
    self.distance_jump_frames = 0
    self.braking_for_lead = False
    self._lead_frames = 0

    self.restricting = False
    self.releasing = False
    self.has_lead = False
    self.required_decel = 0.0
    self.selected_lead = -1
    self.selected_lead_track_id = -1
    self.raw_speed_ceiling = math.inf

  @property
  def filtered_lead_speed(self) -> float:
    return _median(self.lead_speed_samples)

  @property
  def filtered_lead_accel(self) -> float:
    return _median(self.lead_accel_samples)

  @property
  def should_coast_on_dropout(self) -> bool:
    return not self.has_lead and self._dropout_was_restricting and self._dropout_was_braking and math.isfinite(self.lead_speed_ceiling)

  def reset(self) -> None:
    self.__init__()

  def _update_lead_bookkeeping(self, lead_plan: LeadPlan, was_restricting: bool) -> None:
    self.has_lead = lead_plan.selected_lead >= 0
    self.raw_speed_ceiling = lead_plan.speed_ceiling if self.has_lead else math.inf
    if self.has_lead:
      self.lead_loss_frames = 0
      self._dropout_was_braking = False
    else:
      if self.lead_loss_frames == 0:
        self._dropout_was_restricting = was_restricting
      self.lead_loss_frames += 1
    self.selected_lead = lead_plan.selected_lead
    self.selected_lead_track_id = lead_plan.selected_lead_track_id if self.has_lead else -1

  def _update_speed_sample(self, lead_plan: LeadPlan) -> None:
    if not self.has_lead:
      self.lead_speed_samples.append(math.inf)
      self.lead_speed_samples.pop(0)
      self.lead_accel_samples.append(0.0)
      self.lead_accel_samples.pop(0)
      return
    if self.raw_speed_ceiling <= self.lead_speed_ceiling + 1e-9:
      self.lead_speed_samples.append(lead_plan.selected_lead_speed)
      self.lead_speed_samples.pop(0)
      self.lead_accel_samples.append(lead_plan.selected_lead_accel)
      self.lead_accel_samples.pop(0)

  def _update_speed_ceiling(self, lead_confirm_frames: int, dropout_frames: int) -> None:
    candidate = self.raw_speed_ceiling
    if candidate <= self.lead_speed_ceiling:
      self.lead_speed_ceiling = candidate
      self.release_confirm_frames = 0
      return

    if not self.has_lead:
      hold_frames = dropout_frames if self._dropout_was_restricting else lead_confirm_frames
      if self.lead_loss_frames <= hold_frames:
        return
      self.lead_speed_ceiling = candidate
      self.release_confirm_frames = 0
      return

    if candidate >= self.lead_speed_ceiling + SPEED_DEADBAND:
      self.release_confirm_frames += 1
    else:
      self.release_confirm_frames = 0
    if self.release_confirm_frames > lead_confirm_frames:
      self.lead_speed_ceiling = candidate
      self.release_confirm_frames = 0

  def _guarded_distance(self, raw: float, lead_speed: float, dt: float) -> float:
    if self.last_departure_distance is not None:
      delta = raw - self.last_departure_distance
      if abs(delta) > _max_distance_step(lead_speed, dt):
        consistent = self.pending_distance_jump is not None and delta * self.pending_distance_jump > 0.0
        self.distance_jump_frames = self.distance_jump_frames + 1 if consistent else 1
        self.pending_distance_jump = delta
        if self.distance_jump_frames >= DISTANCE_JUMP_CONFIRM_FRAMES:
          self.pending_distance_jump = None
          self.distance_jump_frames = 0
          self.departure_confirm_frames = 0
          if abs(delta) >= STOP_HOLD_CREEP_DISTANCE:
            self.held_lead_trusted = False
        else:
          raw = self.last_departure_distance
      else:
        self.pending_distance_jump = None
        self.distance_jump_frames = 0
    self.last_departure_distance = raw
    return raw

  def _reset_distance_guard(self) -> None:
    self.last_departure_distance = self.pending_distance_jump = None
    self.distance_jump_frames = 0

  def _reset_departure_confirmation(self) -> None:
    self.departure_confirm_frames = 0
    self.departure_distance_ref = None
    self.no_departure_lead_frames = 0

  def _set_held_lead(self, lead_plan: LeadPlan, replacement: bool = False) -> None:
    self.held_lead = lead_plan.departure_lead
    self.held_lead_track_id = lead_plan.departure_lead_track_id
    self.held_lead_trusted = not replacement and self.held_lead_track_id >= 0
    self._reset_distance_guard()
    if self.held_lead >= 0:
      self.last_departure_distance = lead_plan.departure_lead_distance
    self._reset_departure_confirmation()

  def _update_stop_hold(self, lead_plan: LeadPlan, v_ego: float, base_speed: float, dt: float, lead_confirm_frames: int) -> bool:
    if self.stop_hold:
      has_departure_lead = _same_lead(self.held_lead, self.held_lead_track_id,
                                      lead_plan.departure_lead, lead_plan.departure_lead_track_id)
      if lead_plan.departure_lead >= 0 and not has_departure_lead:
        continuous_vision_lead = (self.held_lead_track_id < 0 and lead_plan.departure_lead_track_id < 0
                                  and self.last_departure_distance is not None
                                  and abs(lead_plan.departure_lead_distance - self.last_departure_distance)
                                  <= _max_distance_step(lead_plan.departure_lead_speed, dt))
        if continuous_vision_lead:
          self.held_lead = lead_plan.departure_lead
          self.held_lead_trusted = False
        else:
          self._set_held_lead(lead_plan, replacement=True)
        has_departure_lead = True
      if has_departure_lead or lead_plan.lead_status:
        self.no_departure_lead_frames = 0
      else:
        self.no_departure_lead_frames += 1
      lead_speed = lead_plan.departure_lead_speed if has_departure_lead else 0.0
      raw_lead_speed = lead_plan.departure_lead_raw_speed if has_departure_lead else 0.0
      evidence = has_departure_lead and min(lead_speed, raw_lead_speed) > STOP_HOLD_SPEED_FLOOR
      distance = None
      if has_departure_lead:
        distance = self._guarded_distance(lead_plan.departure_lead_distance, lead_speed, dt)

      if evidence:
        if self.departure_confirm_frames == 0:
          self.departure_distance_ref = distance
        self.departure_confirm_frames += 1
      else:
        self.departure_confirm_frames = 0
        self.departure_distance_ref = None
        if not has_departure_lead:
          self.held_lead_trusted = False
          self._reset_distance_guard()

      growth = 0.0
      if self.departure_confirm_frames > 0 and self.departure_distance_ref is not None and distance is not None:
        growth = distance - self.departure_distance_ref

      dwell_ready = self.departure_confirm_frames >= STOP_HOLD_EXIT_FRAMES
      departing_with_lead = has_departure_lead and dwell_ready and growth + 1e-9 >= STOP_HOLD_CREEP_DISTANCE
      departing_no_lead = (not lead_plan.lead_status and self.no_departure_lead_frames >= lead_confirm_frames
                           and base_speed > STOP_HOLD_SPEED_FLOOR)

      if departing_with_lead or departing_no_lead:
        trusted_departure = departing_with_lead and self.held_lead_trusted
        self.stop_hold = False
        self.launching = True
        self.departure_launching = trusted_departure
        self.leadless_departure = not trusted_departure
        self.held_lead = self.held_lead_track_id = -1
        self.held_lead_trusted = False
        self._reset_departure_confirmation()
        self.target_speed = min(v_ego, base_speed)
      else:
        self.target_speed = 0.0
      return self.stop_hold

    departure_separation = lead_plan.departure_lead_separation if lead_plan.departure_lead >= 0 else math.inf
    stopped_lead_hold = lead_plan.has_nearly_stopped_lead and (
      lead_plan.departure_speed_ceiling < STOP_HOLD_SPEED_FLOOR
      or (self.braking_for_lead and departure_separation <= STOP_HOLD_MAX_LEAD_DISTANCE)
    )
    retained_stop_hold = not self.has_lead and math.isfinite(self.lead_speed_ceiling) and self.lead_speed_ceiling < STOP_HOLD_SPEED_FLOOR
    if not self.launching and v_ego < STOP_HOLD_EGO_SPEED and (
      stopped_lead_hold or retained_stop_hold
    ):
      self.stop_hold = True
      self._set_held_lead(lead_plan)
      self.launching = self.departure_launching = self.leadless_departure = False
      self.lead_recovery = False
      self.recovery_accel_limit = None
      self.target_speed = 0.0
      return True

    return False

  def _update_launch(self, lead_plan: LeadPlan, base_speed: float, v_ego: float, dt: float, lead_confirm_frames: int) -> None:
    if not self.launching:
      return
    if v_ego >= LAUNCH_END_SPEED:
      self.launching = self.departure_launching = self.leadless_departure = False
      return
    invalid_lead = lead_plan.lead_status and not self.has_lead
    renewed_stop = self.has_lead and lead_plan.has_nearly_stopped_lead
    if invalid_lead or renewed_stop:
      self.launching = self.departure_launching = self.leadless_departure = False
      if v_ego < STOP_HOLD_EGO_SPEED:
        self.stop_hold = True
        self._set_held_lead(lead_plan)
        self.target_speed = 0.0
      return
    self.releasing = True
    if self.departure_launching:
      self.target_speed = base_speed
    elif self.leadless_departure:
      self.target_speed = min(base_speed, max(self.target_speed or 0.0, v_ego) + TARGET_RELEASE_SLEW * dt)
    elif not self.has_lead and self.lead_loss_frames >= lead_confirm_frames:
      self.target_speed = min(base_speed, self.lead_speed_ceiling)
    else:
      launch_target = min(base_speed, v_ego + LAUNCH_TARGET_HEADROOM)
      self.target_speed = min(base_speed, max(self.target_speed or 0.0, launch_target) + TARGET_RELEASE_SLEW * dt)

  def _update_recovery(self, ceiling: float, base_speed: float, v_ego: float, profile_max_accel: float, dt: float) -> None:
    if math.isfinite(self.filtered_lead_speed):
      recovery_speed = min(base_speed, self.filtered_lead_speed + LEAD_RECOVERY_HEADROOM)
      desired_accel_limit = float(np.clip(recovery_speed - v_ego, 0.0, profile_max_accel))
    else:
      desired_accel_limit = 0.0
    if self.filtered_lead_accel < BRAKING_ACCEL_THRESHOLD:
      # Avoid stacking the recovery slew on top of a braking lead.
      desired_accel_limit = profile_max_accel
    if self.recovery_accel_limit is None:
      self.recovery_accel_limit = profile_max_accel
    self.recovery_accel_limit = _slew(self.recovery_accel_limit, desired_accel_limit, LEAD_RECOVERY_ACCEL_SLEW, dt)

    if ceiling <= self.target_speed - SPEED_DEADBAND:
      self.target_speed = max(ceiling, self.target_speed - LEAD_RECOVERY_DECEL_RATE * dt)
      self.restricting = True
    elif ceiling >= self.target_speed + SPEED_DEADBAND:
      self.target_speed = min(ceiling, self.target_speed + profile_max_accel * dt)
      self.releasing = True

  def _update_target_law(self, lead_plan: LeadPlan, base_speed: float, v_ego: float, comfort_decel: float,
                         profile_max_accel: float, dt: float, planner_speed: float,
                         dropout_frames: int, was_restricting: bool) -> None:
    ceiling = min(base_speed, self.lead_speed_ceiling)
    new_recovery = self.has_lead and lead_plan.closing_speed <= 0.0
    still_within_dropout = not self.has_lead and self.lead_loss_frames <= dropout_frames
    self.lead_recovery = new_recovery or (self.lead_recovery and (self.has_lead or still_within_dropout))
    if self.lead_recovery:
      self._update_recovery(ceiling, base_speed, v_ego, profile_max_accel, dt)
      return

    self.recovery_accel_limit = None
    synced_to_planner = ceiling < self.target_speed and planner_speed < self.target_speed
    if synced_to_planner:
      self.target_speed = max(planner_speed, self.target_speed - comfort_decel * dt)

    if ceiling <= self.target_speed - SPEED_DEADBAND or (was_restricting and ceiling < self.target_speed):
      if not synced_to_planner:
        self.target_speed = max(ceiling, self.target_speed - comfort_decel * dt)
      self.restricting = True
    elif ceiling >= self.target_speed + SPEED_DEADBAND:
      self.target_speed = min(ceiling, self.target_speed + TARGET_RELEASE_SLEW * dt)
      self.releasing = True

  def update(self, lead_plan: LeadPlan, base_speed: float, v_ego: float, comfort_decel: float, profile_max_accel: float,
             dt: float, lead_confirm_frames: int, dropout_frames: int, planner_speed: float,
             planner_accel: float, previous_mpc_source, previous_plan_accel: float) -> float:
    was_restricting = self.restricting
    was_braking_for_lead = self.braking_for_lead
    previous_lead = self.selected_lead
    previous_track_id = self.selected_lead_track_id
    holding_below_cruise = (not self.lead_recovery and self.target_speed is not None and math.isfinite(self.lead_speed_ceiling)
                            and self.lead_speed_ceiling < base_speed - SPEED_DEADBAND
                            and self.lead_speed_ceiling - v_ego < LEAD_RECOVERY_HEADROOM)
    self.restricting = self.releasing = False
    self._update_lead_bookkeeping(lead_plan, was_restricting or holding_below_cruise)
    lead_changed = not _same_lead(previous_lead, previous_track_id, self.selected_lead, self.selected_lead_track_id)
    if not self.has_lead or lead_changed:
      self._lead_frames = 0
    if self.braking_for_lead and lead_changed:
      self.braking_for_lead = False
    if not self.has_lead and self.lead_loss_frames == 1:
      self._dropout_was_braking = was_braking_for_lead and planner_accel <= BRAKING_ACCEL_THRESHOLD
    self._update_speed_ceiling(lead_confirm_frames, dropout_frames)
    self._update_speed_sample(lead_plan)
    self.required_decel = lead_plan.required_decel

    self._lead_frames += int(self.has_lead)
    if (self._lead_frames >= lead_confirm_frames and math.isfinite(self.lead_speed_ceiling)
        and self.has_lead and planner_accel <= BRAKING_ACCEL_THRESHOLD):
      self.braking_for_lead = True
    elif not self.has_lead and self.lead_loss_frames >= lead_confirm_frames:
      self.braking_for_lead = False

    if self.target_speed is None:
      self.target_speed = min(base_speed, v_ego)
      e2e_handoff = previous_mpc_source == LongitudinalPlanSource.e2e
      self.e2e_braking_handoff = e2e_handoff and math.isfinite(previous_plan_accel) and previous_plan_accel <= BRAKING_ACCEL_THRESHOLD
      stop_hold_reason = lead_plan.has_nearly_stopped_lead or (math.isfinite(self.lead_speed_ceiling) and self.lead_speed_ceiling < STOP_HOLD_SPEED_FLOOR)
      if v_ego < STOP_HOLD_EGO_SPEED and not stop_hold_reason:
        self.target_speed = min(base_speed, v_ego + LAUNCH_TARGET_HEADROOM)
        self.launching = True
        self.departure_launching = False
    elif self.e2e_braking_handoff and planner_accel > BRAKING_ACCEL_THRESHOLD:
      self.e2e_braking_handoff = False

    self.target_speed = min(self.target_speed, base_speed)

    if self._update_stop_hold(lead_plan, v_ego, base_speed, dt, lead_confirm_frames):
      return self.target_speed

    self._update_launch(lead_plan, base_speed, v_ego, dt, lead_confirm_frames)
    if self.launching or self.stop_hold:
      return self.target_speed

    self._update_target_law(lead_plan, base_speed, v_ego, comfort_decel, profile_max_accel, dt, planner_speed,
                            dropout_frames, was_restricting)
    return self.target_speed
