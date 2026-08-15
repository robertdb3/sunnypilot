"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

from openpilot.sunnypilot.selfdrive.reckless_watch.va_outline import BBOX, RINGS

# A GPS fix can wobble tens of metres, and border roads run right along the line. Require several
# consecutive fixes on the new side before believing a crossing, or the alert chatters.
CROSSING_CONFIRM_FIXES = 5

# Beyond this the fix is too uncertain to make a claim about which state we are in.
MAX_HORIZONTAL_ACCURACY = 50.0  # metres


def _in_ring(lon: float, lat: float, ring) -> bool:
  """Standard ray casting. Ring points are (lon, lat)."""
  inside = False
  n = len(ring)
  j = n - 1
  for i in range(n):
    xi, yi = ring[i]
    xj, yj = ring[j]
    if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
      inside = not inside
    j = i
  return inside


def in_virginia(lat: float, lon: float) -> bool:
  """Point in the Virginia outline, including the Eastern Shore and the bay islands."""
  min_lon, min_lat, max_lon, max_lat = BBOX
  # cheap rejection first; the great majority of calls are nowhere near the state
  if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
    return False

  return any(_in_ring(lon, lat, ring) for ring in RINGS)


class StateTracker:
  """Debounced 'are we in Virginia' with edge detection.

  Holds its last confident answer while a fix is poor or absent rather than flapping to False,
  so driving through a tunnel does not read as leaving the state.
  """

  def __init__(self, confirm_fixes: int = CROSSING_CONFIRM_FIXES):
    self.confirm_fixes = confirm_fixes
    self.inside: bool | None = None      # None until the first confident fix
    self._pending: bool | None = None
    self._count = 0

  def update(self, lat: float, lon: float, horizontal_accuracy: float = 0.0) -> bool:
    """Feed a GPS fix. Returns True on the transition INTO Virginia, once."""
    if horizontal_accuracy > MAX_HORIZONTAL_ACCURACY:
      return False

    observed = in_virginia(lat, lon)

    if observed == self.inside:
      self._pending = None
      self._count = 0
      return False

    if observed != self._pending:
      self._pending = observed
      self._count = 1
      return False

    self._count += 1
    if self._count < self.confirm_fixes:
      return False

    first_fix = self.inside is None
    self.inside = observed
    self._pending = None
    self._count = 0

    # Announce only an actual crossing in. The first fix of a drive already inside the state is
    # not a crossing -- waking up in Richmond should not announce anything.
    return observed and not first_fix
