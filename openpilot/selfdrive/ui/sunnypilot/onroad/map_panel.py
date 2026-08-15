"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# Heading-up inset map drawn from the offline OSM data mapd already downloads, so it works
# with no signal. Geometry comes from openpilot.sunnypilot.mapd.offline_map on a worker
# thread; everything here is projection and drawing.
#
# Points are projected with one vectorised numpy pass and memmoved straight into a Vector2
# buffer -- raylib's Vector2 is two float32s, so a float32 (N, 2) array is bit-identical and
# needs no per-point Python work. Only the per-way draw_line_strip calls are interpreted,
# and MAX_DRAWN_WAYS bounds those.

import math

import numpy as np
import pyray as rl

from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.sunnypilot.mapd.offline_map import OfflineMapLoader
from openpilot.system.ui.widgets import Widget

EARTH_R = 6371000.0

PANEL_SIZE = 460
PANEL_MARGIN = 40
CORNER_ROUNDNESS = 0.06

# metres per pixel for each zoom setting, closest first
ZOOM_LEVELS = (0.55, 1.1, 2.2)
DEFAULT_ZOOM = 1

# how much of the panel sits ahead of the car: 0.5 centres it, higher pushes it down
FORWARD_BIAS = 0.68

# hard ceilings per frame. Segments are what actually costs: each one is a draw_line_ex
# call, so this is the number that protects the frame rate, not the way count.
MAX_DRAWN_WAYS = 400
MAX_DRAWN_SEGMENTS = 1200

# (colour, width) per class priority from offline_map.CLASS_PRIORITY, index is the priority
ROAD_STYLES = (
  (rl.Color(255, 186, 92, 255), 6.0),   # 0  motorway
  (rl.Color(252, 168, 86, 255), 5.5),   # 1  trunk
  (rl.Color(247, 224, 138, 255), 5.0),  # 2  primary
  (rl.Color(255, 186, 92, 220), 4.0),   # 3  motorway/trunk links
  (rl.Color(238, 238, 238, 235), 4.0),  # 4  secondary
  (rl.Color(247, 224, 138, 200), 3.0),  # 5  primary link
  (rl.Color(224, 224, 224, 220), 3.5),  # 6  tertiary
  (rl.Color(238, 238, 238, 190), 2.5),  # 7  secondary link
  (rl.Color(224, 224, 224, 190), 2.5),  # 8  tertiary link
  (rl.Color(196, 196, 196, 190), 2.5),  # 9  unclassified
  (rl.Color(178, 178, 178, 190), 2.5),  # 10 residential
  (rl.Color(168, 168, 168, 170), 2.0),  # 11 living street
  (rl.Color(150, 150, 150, 150), 2.0),  # 12 unknown
)

BACKGROUND = rl.Color(18, 20, 24, 205)
BORDER = rl.Color(255, 255, 255, 40)
CAR_COLOR = rl.Color(70, 160, 255, 255)
NO_FIX_COLOR = rl.Color(200, 200, 200, 150)


def project(points: np.ndarray, lat0: float, lon0: float, bearing_deg: float,
            metres_per_px: float, cx: float, cy: float) -> np.ndarray:
  """Project lat/lon to heading-up panel pixels. Returns float32 (N, 2), ready to memmove.

  Local equirectangular about (lat0, lon0), which is well under a pixel of error at the
  couple-of-km radius this draws. Bearing is degrees clockwise from north; the car's
  forward direction maps to screen up.
  """
  if len(points) == 0:
    return np.zeros((0, 2), dtype=np.float32)

  lat_scale = math.pi / 180.0 * EARTH_R
  lon_scale = lat_scale * math.cos(math.radians(lat0))

  east = (points[:, 1] - lon0) * lon_scale
  north = (points[:, 0] - lat0) * lat_scale

  b = math.radians(bearing_deg)
  sin_b, cos_b = math.sin(b), math.cos(b)

  right = east * cos_b - north * sin_b
  forward = east * sin_b + north * cos_b

  out = np.empty((len(points), 2), dtype=np.float32)
  out[:, 0] = cx + right / metres_per_px
  out[:, 1] = cy - forward / metres_per_px
  return out


def visible_ways(xy: np.ndarray, starts: np.ndarray, counts: np.ndarray,
                 classes: np.ndarray, rect: rl.Rectangle, limit: int) -> np.ndarray:
  """Indices of ways overlapping the panel, most important first, capped at limit."""
  if len(starts) == 0:
    return np.zeros(0, dtype=np.int64)

  # per-way screen bbox without a Python loop
  min_x = np.minimum.reduceat(xy[:, 0], starts)
  max_x = np.maximum.reduceat(xy[:, 0], starts)
  min_y = np.minimum.reduceat(xy[:, 1], starts)
  max_y = np.maximum.reduceat(xy[:, 1], starts)

  visible = ((max_x >= rect.x) & (min_x <= rect.x + rect.width) &
             (max_y >= rect.y) & (min_y <= rect.y + rect.height) & (counts >= 2))

  idx = np.flatnonzero(visible)
  # always order by importance, not just when trimming: the caller also stops on a segment
  # budget, and that must drop side streets rather than whatever sorted last by index.
  # Stable, so the result stays deterministic frame to frame.
  idx = idx[np.argsort(classes[idx], kind="stable")]
  return idx[:limit]


class MapPanelRenderer(Widget):
  def __init__(self):
    super().__init__()
    self._loader = OfflineMapLoader()
    self._started = False

    self._lat = 0.0
    self._lon = 0.0
    self._bearing = 0.0
    self._has_fix = False

    self._buf = rl.ffi.new("Vector2[]", 1)
    self._buf_len = 1

  def _gps(self):
    """Newest valid fix. A 3X publishes gpsLocation (qcom) or gpsLocationExternal (ublox)
    depending on hardware, so take whichever socket is actually alive."""
    sm = ui_state.sm
    best = None
    for service in ("gpsLocation", "gpsLocationExternal"):
      if service not in sm.data:
        continue
      if not sm.valid[service] or not sm.alive[service]:
        continue
      msg = sm[service]
      if not msg.hasFix:
        continue
      if best is None or msg.unixTimestampMillis > best.unixTimestampMillis:
        best = msg
    return best

  def update(self):
    if not ui_state.map_panel:
      return

    if not self._started:
      self._loader.start()
      self._started = True

    fix = self._gps()
    if fix is None:
      self._has_fix = False
      return

    self._has_fix = True
    self._lat = fix.latitude
    self._lon = fix.longitude
    self._bearing = fix.bearingDeg
    self._loader.update_position(self._lat, self._lon)

  def _ensure_buffer(self, n: int) -> None:
    if n > self._buf_len:
      self._buf = rl.ffi.new("Vector2[]", n)
      self._buf_len = n

  def _render(self, rect: rl.Rectangle):
    if not ui_state.map_panel:
      return

    panel = rl.Rectangle(
      rect.x + rect.width - PANEL_SIZE - PANEL_MARGIN,
      rect.y + rect.height - PANEL_SIZE - PANEL_MARGIN,
      PANEL_SIZE, PANEL_SIZE,
    )

    rl.draw_rectangle_rounded(panel, CORNER_ROUNDNESS, 12, BACKGROUND)
    rl.draw_rectangle_rounded_lines(panel, CORNER_ROUNDNESS, 12, BORDER)

    cx = panel.x + panel.width / 2.0
    cy = panel.y + panel.height * FORWARD_BIAS

    rl.begin_scissor_mode(int(panel.x), int(panel.y), int(panel.width), int(panel.height))
    try:
      if self._has_fix:
        self._draw_roads(panel, cx, cy)
        self._draw_car(cx, cy)
      else:
        rl.draw_circle(int(cx), int(cy), 8.0, NO_FIX_COLOR)
    finally:
      rl.end_scissor_mode()

  def _draw_roads(self, panel: rl.Rectangle, cx: float, cy: float) -> None:
    data = self._loader.get_slice()
    if data is None or data.num_ways == 0:
      return

    zoom = ui_state.map_panel_zoom
    mpp = ZOOM_LEVELS[zoom] if 0 <= zoom < len(ZOOM_LEVELS) else ZOOM_LEVELS[DEFAULT_ZOOM]

    xy = project(data.points, self._lat, self._lon, self._bearing, mpp, cx, cy)
    idx = visible_ways(xy, data.starts, data.counts, data.classes, panel, MAX_DRAWN_WAYS)
    if len(idx) == 0:
      return

    # one copy of every projected point into the C buffer, then index into it per segment.
    # draw_line_ex rather than draw_line_strip: the strip is always hairline, and GLES
    # commonly ignores glLineWidth, so thickness has to come from geometry.
    self._ensure_buffer(len(xy))
    rl.ffi.memmove(self._buf, rl.ffi.from_buffer(xy), xy.nbytes)

    starts, counts, classes = data.starts, data.counts, data.classes
    styles = ROAD_STYLES
    last = len(styles) - 1
    buf = self._buf
    draw = rl.draw_line_ex
    budget = MAX_DRAWN_SEGMENTS

    for i in idx:
      n = int(counts[i])
      if n < 2:
        continue
      if budget <= 0:
        break
      color, width = styles[min(int(classes[i]), last)]
      start = int(starts[i])
      n = min(n, budget + 1)
      for j in range(start, start + n - 1):
        draw(buf[j], buf[j + 1], width, color)
      budget -= n - 1

  @staticmethod
  def _draw_car(cx: float, cy: float) -> None:
    rl.draw_triangle(
      rl.Vector2(cx, cy - 15),
      rl.Vector2(cx - 10, cy + 11),
      rl.Vector2(cx + 10, cy + 11),
      CAR_COLOR,
    )
