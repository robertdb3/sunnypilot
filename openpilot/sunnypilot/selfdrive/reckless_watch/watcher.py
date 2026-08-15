"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

from dataclasses import dataclass
from enum import IntEnum

from openpilot.sunnypilot.selfdrive.reckless_watch.geofence import StateTracker

MS_TO_MPH = 2.23694

# Virginia Code 46.2-862: reckless driving is (i) 20 mph or more over the applicable maximum speed
# limit, or (ii) in excess of 85 mph regardless of the limit. Class 1 misdemeanor, up to 12 months.
# The absolute figure was 80 before HB 1442 raised it on 2020-07-01 -- do not "correct" it back.
STATUTE_ABSOLUTE_MPH = 85
STATUTE_OVER_LIMIT_MPH = 20

# Default warn point, a few mph under the statute so there is room to react. User-configurable.
DEFAULT_THRESHOLD_MPH = 82

# Drop this far below the threshold before the audible alert can fire again. Without it, sitting
# at the threshold retriggers constantly, which is exactly the nagging this feature must avoid.
REARM_MARGIN_MPH = 3.0


class Reason(IntEnum):
  none = 0
  absolute = 1      # over the absolute threshold
  overLimit = 2     # 20+ over a known posted limit


@dataclass
class WatchState:
  in_virginia: bool = False
  over: bool = False
  reason: Reason = Reason.none
  threshold_mph: float = 0.0        # what was actually breached, for the alert text
  entered_virginia: bool = False    # one-shot edge
  started_speeding: bool = False    # one-shot edge


class RecklessWatch:
  """Decides when to warn about Virginia reckless-driving speed thresholds.

  Deliberately free of cereal and params so it can be tested as pure logic. The caller supplies
  position, speed and the resolved speed limit; this returns edges and a sticky over/under state.
  """

  def __init__(self, threshold_mph: float = DEFAULT_THRESHOLD_MPH):
    self.threshold_mph = threshold_mph
    self.state_tracker = StateTracker()
    self._alerted = False
    self._state = WatchState()

  def _limits(self, speed_limit_ms: float) -> list[tuple[float, Reason]]:
    """Active thresholds in mph, lowest first.

    The 20-over rule only participates when a limit is actually known. OSM coverage is patchy and
    a guessed limit would produce false alarms; the absolute rule still applies either way.
    """
    out: list[tuple[float, Reason]] = [(self.threshold_mph, Reason.absolute)]
    if speed_limit_ms > 0:
      limit_mph = speed_limit_ms * MS_TO_MPH
      out.append((limit_mph + STATUTE_OVER_LIMIT_MPH, Reason.overLimit))
    out.sort()
    return out

  def update(self, lat: float, lon: float, horizontal_accuracy: float,
             v_ego_ms: float, speed_limit_ms: float = 0.0) -> WatchState:
    entered = self.state_tracker.update(lat, lon, horizontal_accuracy)
    inside = bool(self.state_tracker.inside)

    st = WatchState(in_virginia=inside, entered_virginia=entered)

    if not inside:
      # leaving the state clears everything, including the re-arm latch
      self._alerted = False
      self._state = st
      return st

    speed_mph = v_ego_ms * MS_TO_MPH
    breached = [(t, r) for t, r in self._limits(speed_limit_ms) if speed_mph > t]

    if breached:
      threshold, reason = breached[0]
      st.over = True
      st.reason = reason
      st.threshold_mph = threshold
      if not self._alerted:
        st.started_speeding = True
        self._alerted = True
    else:
      # Re-arm only once genuinely back under, not the instant the number dips. Hovering at the
      # threshold must not produce a stream of alerts.
      lowest = self._limits(speed_limit_ms)[0][0]
      if speed_mph < lowest - REARM_MARGIN_MPH:
        self._alerted = False

    self._state = st
    return st
