"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

import math
from typing import NamedTuple

import numpy as np

from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import (
  LongitudinalMpc, STOP_DISTANCE, T_IDXS, get_T_FOLLOW, get_stopped_equivalence_factor,
)
from openpilot.selfdrive.controls.radard import _LEAD_ACCEL_TAU
from openpilot.sunnypilot.selfdrive.controls.lib.accel_controller.constants import (
  COMFORT_DECEL, MAX_LEAD_ACCEL_TAU, MIN_LEAD_SPEED, STOP_GAP_RESERVE, STOP_HOLD_SPEED_FLOOR, sanitize_profile,
)


class LeadPlan(NamedTuple):
  speed_ceiling: float = math.inf
  selected_lead: int = -1
  selected_lead_track_id: int = -1
  selected_lead_speed: float = math.inf
  selected_lead_accel: float = 0.0
  departure_lead: int = -1
  departure_lead_track_id: int = -1
  departure_lead_speed: float = math.inf
  departure_lead_raw_speed: float = math.inf
  departure_lead_distance: float = math.inf
  departure_lead_separation: float = math.inf
  departure_speed_ceiling: float = math.inf
  closing_speed: float = 0.0
  required_decel: float = 0.0
  has_nearly_stopped_lead: bool = False
  lead_status: bool = False


def _project_ego(v_ego: float, a_ego: float, delay: float) -> tuple[float, float]:
  if a_ego < 0.0:
    stop_time = -v_ego / a_ego if v_ego > 0.0 else 0.0
    if stop_time <= delay:
      distance = -v_ego**2 / (2.0 * a_ego) if v_ego > 0.0 else 0.0
      return distance, 0.0
  return max(v_ego * delay + 0.5 * a_ego * delay**2, 0.0), max(v_ego + a_ego * delay, 0.0)


def _lead_values(lead) -> tuple[float, float, float, float, float] | None:
  if not lead.present:
    return None
  d_rel, v_lead = float(lead.dRel), float(lead.vLeadK)
  if not math.isfinite(d_rel) or d_rel < 0.0 or not math.isfinite(v_lead) or v_lead < MIN_LEAD_SPEED:
    return None

  a_lead = float(lead.aLeadK)
  if not math.isfinite(a_lead):
    a_lead = 0.0
  a_lead_tau = float(lead.aLeadTau)
  if not math.isfinite(a_lead_tau) or not 0.0 < a_lead_tau <= MAX_LEAD_ACCEL_TAU:
    a_lead_tau = _LEAD_ACCEL_TAU
  raw_v_lead = float(getattr(lead, "vLead", v_lead))
  if not math.isfinite(raw_v_lead):
    raw_v_lead = 0.0
  return d_rel, max(v_lead, 0.0), max(raw_v_lead, 0.0), float(np.clip(a_lead, -10.0, 5.0)), a_lead_tau


def calculate_lead_plan(radar_state, v_ego: float, a_ego: float, delay: float, profile: int,
                        follow_personality=log.LongitudinalPersonality.standard) -> LeadPlan:
  if not all(math.isfinite(value) for value in (v_ego, a_ego, delay)) or v_ego < 0.0 or delay < 0.0:
    return LeadPlan()

  leads = (radar_state.leadOne, radar_state.leadTwo)
  lead_status = any(lead.present for lead in leads)
  t_follow = get_T_FOLLOW(follow_personality)
  if not math.isfinite(t_follow) or t_follow < 0.0:
    return LeadPlan(lead_status=lead_status)

  profile = sanitize_profile(profile)
  x_ego, v_ego_delay = _project_ego(v_ego, a_ego, delay)
  comfort_decel = COMFORT_DECEL[profile]
  candidates: list[LeadPlan] = []
  departure_candidates: list[tuple[float, LeadPlan]] = []

  for lead_index, lead in enumerate(leads):
    values = _lead_values(lead)
    if values is None:
      continue

    d_rel, v_lead, raw_v_lead, a_lead, a_lead_tau = values
    lead_xv = LongitudinalMpc.extrapolate_lead(d_rel, v_lead, a_lead, a_lead_tau)
    x_lead = float(np.interp(delay, T_IDXS, lead_xv[:, 0]))
    v_lead_delay = float(np.interp(delay, T_IDXS, lead_xv[:, 1]))
    safety_gap = max(x_lead - x_ego - STOP_DISTANCE - t_follow * v_lead_delay, 0.0)
    closing_speed = max(v_ego_delay - v_lead_delay, 0.0)
    required_decel = 0.0 if closing_speed == 0.0 else math.inf if safety_gap == 0.0 else closing_speed**2 / (2.0 * safety_gap)
    usable_gap = max(safety_gap - STOP_GAP_RESERVE, 0.0)
    speed_ceiling = v_lead_delay + math.sqrt(2.0 * comfort_decel * usable_gap)
    departure_speed_ceiling = v_lead_delay + math.sqrt(2.0 * comfort_decel * safety_gap)
    separation = x_lead - x_ego
    departure_distance = x_lead + float(get_stopped_equivalence_factor(v_lead_delay))

    finite_values = (x_lead, v_lead_delay, safety_gap, usable_gap, closing_speed, speed_ceiling, departure_speed_ceiling, departure_distance)
    if (not all(math.isfinite(value) and value >= 0.0 for value in finite_values) or math.isnan(required_decel)
        or required_decel < 0.0 or not math.isfinite(separation)):
      continue

    track_id = max(int(lead.radarTrackId), -1) if math.isfinite(lead.radarTrackId) else -1
    candidate = LeadPlan(
      speed_ceiling=speed_ceiling, selected_lead=lead_index, selected_lead_track_id=track_id,
      selected_lead_speed=v_lead_delay, selected_lead_accel=a_lead, departure_lead=lead_index,
      departure_lead_track_id=track_id, departure_lead_speed=v_lead_delay, departure_lead_raw_speed=raw_v_lead,
      departure_lead_distance=d_rel, departure_lead_separation=separation,
      departure_speed_ceiling=departure_speed_ceiling, closing_speed=closing_speed, required_decel=required_decel,
      has_nearly_stopped_lead=v_lead_delay < STOP_HOLD_SPEED_FLOOR, lead_status=lead_status,
    )
    candidates.append(candidate)
    departure_candidates.append((departure_distance, candidate))

  if not candidates:
    return LeadPlan(lead_status=lead_status)

  selected = min(candidates, key=lambda candidate: candidate.speed_ceiling)
  departure = min(departure_candidates, key=lambda candidate: candidate[0])[1]
  return selected._replace(
    departure_lead=departure.selected_lead, departure_lead_track_id=departure.selected_lead_track_id,
    departure_lead_speed=departure.selected_lead_speed, departure_lead_raw_speed=departure.departure_lead_raw_speed,
    departure_lead_distance=departure.departure_lead_distance,
    departure_lead_separation=departure.departure_lead_separation,
    departure_speed_ceiling=departure.departure_speed_ceiling,
    has_nearly_stopped_lead=departure.has_nearly_stopped_lead,
  )
