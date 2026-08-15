"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

from dataclasses import dataclass, field

import numpy as np
import pyray as rl

from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d import geometry as geo
from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d import vehicles
from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d.theme import Palette, ThemeSelector, path_color
from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d.vehicles import CarShape

MAX_DIST = 120.0
PATH_DIST = 70.0        # the path is a near-term intention; drawing it to the horizon is noise
LANE_WIDTH = 0.20
EDGE_WIDTH = 0.26
PATH_WIDTH_NEAR = 1.55  # tapers with distance, which reads as perspective rather than a flat strip
PATH_WIDTH_FAR = 0.55

Z_ROAD, Z_SHADOW, Z_PATH, Z_LANE = 0.010, 0.014, 0.020, 0.028

CAM_POS = (0.0, 9.6, 23.0)
CAM_TARGET = (0.0, 0.0, -30.0)
CAM_FOV = 38.0

GROUND_AHEAD, GROUND_BEHIND, GROUND_SIDE = 600.0, 60.0, 200.0

LABEL_FONT_SIZE = 26
LABEL_SUB_SIZE = 20


@dataclass
class SceneState:
  """Everything the scene draws. Plain arrays so this is testable without cereal."""
  lane_lines: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = field(default_factory=list)
  lane_line_probs: list[float] = field(default_factory=list)
  road_edges: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = field(default_factory=list)
  path: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None
  accel: np.ndarray | None = None          # planned longitudinal accel along the path
  leads: list[tuple[float, float, float, float]] = field(default_factory=list)  # x, y, prob, v
  v_ego: float = 0.0
  left_blindspot: bool = False
  right_blindspot: bool = False
  light_sensor: float = 0.0
  is_metric: bool = False
  valid: bool = False


def _tris(tris: np.ndarray, color: tuple[int, int, int, int]) -> None:
  # note: rl.Color is a cffi constructor, not a Python type, so it cannot be isinstance-checked
  if tris.size == 0:
    return
  c = rl.Color(*color)
  for t in tris:
    rl.draw_triangle_3d(
      rl.Vector3(float(t[0][0]), float(t[0][1]), float(t[0][2])),
      rl.Vector3(float(t[1][0]), float(t[1][1]), float(t[1][2])),
      rl.Vector3(float(t[2][0]), float(t[2][1]), float(t[2][2])),
      c,
    )


def _tris_colored(tris: np.ndarray, colors: list) -> None:
  """Per-segment colours; tris are interleaved two-per-segment by ribbon_varying."""
  for i, t in enumerate(tris):
    c = colors[min(i // 2, len(colors) - 1)]
    rl.draw_triangle_3d(
      rl.Vector3(float(t[0][0]), float(t[0][1]), float(t[0][2])),
      rl.Vector3(float(t[1][0]), float(t[1][1]), float(t[1][2])),
      rl.Vector3(float(t[2][0]), float(t[2][1]), float(t[2][2])),
      rl.Color(*c),
    )


class Scene3D:
  def __init__(self):
    self._car: CarShape | None = None
    self._theme = ThemeSelector()
    self._camera = rl.Camera3D(
      rl.Vector3(*CAM_POS), rl.Vector3(*CAM_TARGET), rl.Vector3(0.0, 1.0, 0.0),
      CAM_FOV, rl.CameraProjection.CAMERA_PERSPECTIVE,
    )
    self._font = None

  def _ensure_assets(self):
    if self._car is None:
      self._car = CarShape()
    if self._font is None:
      try:
        from openpilot.system.ui.lib.application import gui_app, FontWeight
        self._font = gui_app.font(FontWeight.MEDIUM)
      except Exception:
        self._font = rl.get_font_default()   # headless harness has no gui_app

  def unload(self):
    if self._car is not None:
      self._car.unload()
      self._car = None

  def render(self, rect: rl.Rectangle, state: SceneState) -> None:
    self._ensure_assets()
    pal = self._theme.update(state.light_sensor)

    # The CALLER must already be in scissor mode for `rect`. We deliberately do not open our own:
    # rl.end_scissor_mode() disables the scissor test outright rather than restoring the previous
    # box, so nesting would leak the HUD and alerts outside the onroad border.
    #
    # glClear obeys the scissor box, so this clears colour AND depth just within the rect.
    # Clearing depth matters: a 2D draw before the 3D pass leaves depth values that silently
    # reject the entire scene.
    rl.clear_background(rl.Color(*pal.sky_bottom))
    self._draw_sky(rect, pal)

    rl.begin_mode_3d(self._camera)
    rl.rl_disable_backface_culling()

    # Ground layers are coplanar by construction. raylib's depth range is 0.01..1000, nowhere near
    # enough precision to separate them at 100 m, so draw them painter's-order with depth off
    # rather than fighting z-fight with ever-larger fake offsets.
    rl.rl_disable_depth_test()
    self._draw_ground(pal)
    if state.valid:
      self._draw_road(pal, state)
      self._draw_ego_lane(pal, state)
    self._draw_shadows(pal, state)
    if state.valid:
      self._draw_path(pal, state)
      self._draw_lane_lines(pal, state)
    self._draw_blindspots(pal, state)
    rl.rl_draw_render_batch_active()

    rl.rl_enable_depth_test()
    self._draw_ego(pal)
    self._draw_leads(pal, state)

    rl.rl_enable_backface_culling()
    rl.end_mode_3d()

    self._draw_horizon_fade(rect, pal)
    self._draw_lead_labels(rect, pal, state)

  # --- backdrop ------------------------------------------------------------------------------

  def _draw_sky(self, rect: rl.Rectangle, pal: Palette):
    rl.draw_rectangle_gradient_v(
      int(rect.x), int(rect.y), int(rect.width), int(rect.height * 0.62),
      rl.Color(*pal.sky_top), rl.Color(*pal.sky_bottom),
    )

  def _draw_horizon_fade(self, rect: rl.Rectangle, pal: Palette):
    """The model stops at MAX_DIST, so the road would otherwise end in a hard edge mid-scene.
    Fade the far field into the sky instead of pretending the world ends."""
    far = rl.get_world_to_screen_ex(
      rl.Vector3(0.0, 0.0, -MAX_DIST * 1.4), self._camera, int(rect.width), int(rect.height)
    )
    top = rect.y
    bottom = min(rect.y + rect.height, rect.y + far.y + rect.height * 0.12)
    if bottom <= top:
      return
    rl.draw_rectangle_gradient_v(
      int(rect.x), int(top), int(rect.width), int(bottom - top),
      rl.Color(*pal.sky_bottom), rl.Color(pal.sky_bottom[0], pal.sky_bottom[1], pal.sky_bottom[2], 0),
    )

  # --- ground --------------------------------------------------------------------------------

  def _draw_ground(self, pal: Palette):
    _tris(geo.quad(np.array([
      [-GROUND_BEHIND, GROUND_SIDE, 0.0], [GROUND_AHEAD, GROUND_SIDE, 0.0],
      [GROUND_AHEAD, -GROUND_SIDE, 0.0], [-GROUND_BEHIND, -GROUND_SIDE, 0.0],
    ])), pal.ground)

  def _draw_road(self, pal: Palette, state: SceneState):
    if len(state.road_edges) < 2:
      return

    lx, ly, lz = geo.extend_back(*geo.trim(*state.road_edges[0], MAX_DIST))
    rx, ry, rz = geo.extend_back(*geo.trim(*state.road_edges[-1], MAX_DIST))
    n = min(len(lx), len(rx))
    if n < 2:
      return

    # a wider shoulder pass under the surface gives the road a lip instead of a paper edge
    for pad, colour, lift in ((0.55, pal.road_shoulder, Z_ROAD), (0.0, pal.road, Z_ROAD + 0.002)):
      left = np.stack([lx[:n], ly[:n] - pad, lz[:n] - lift], axis=1).astype(np.float32)
      right = np.stack([rx[:n], ry[:n] + pad, rz[:n] - lift], axis=1).astype(np.float32)
      lw, rw = geo.car_to_world(left), geo.car_to_world(right)
      a, b, c, d = lw[:-1], rw[:-1], lw[1:], rw[1:]
      _tris(np.concatenate([np.stack([a, c, b], axis=1), np.stack([b, c, d], axis=1)], axis=0), colour)

    for edge in state.road_edges:
      ex, ey, ez = geo.extend_back(*geo.trim(*edge, MAX_DIST))
      _tris(geo.ribbon(ex, ey, ez, EDGE_WIDTH, Z_LANE), pal.road_edge)

  def _draw_ego_lane(self, pal: Palette, state: SceneState):
    """Lightly fill the lane the car is in.

    Costs two triangles per segment and does more for legibility than anything else here: it
    separates 'my lane' from 'the road' at a glance, which is most of what the Tesla view is
    actually communicating.
    """
    if len(state.lane_lines) < 4:
      return

    lx, ly, lz = geo.extend_back(*geo.trim(*state.lane_lines[1], MAX_DIST))
    rx, ry, rz = geo.extend_back(*geo.trim(*state.lane_lines[2], MAX_DIST))
    n = min(len(lx), len(rx))
    if n < 2:
      return

    left = np.stack([lx[:n], ly[:n], lz[:n] - Z_ROAD - 0.004], axis=1).astype(np.float32)
    right = np.stack([rx[:n], ry[:n], rz[:n] - Z_ROAD - 0.004], axis=1).astype(np.float32)
    lw, rw = geo.car_to_world(left), geo.car_to_world(right)
    a, b, c, d = lw[:-1], rw[:-1], lw[1:], rw[1:]
    _tris(np.concatenate([np.stack([a, c, b], axis=1), np.stack([b, c, d], axis=1)], axis=0),
          pal.ego_lane)

  def _draw_lane_lines(self, pal: Palette, state: SceneState):
    for i, line in enumerate(state.lane_lines):
      prob = state.lane_line_probs[i] if i < len(state.lane_line_probs) else 1.0
      if prob < 0.25:
        continue
      lx, ly, lz = geo.extend_back(*geo.trim(*line, MAX_DIST))
      r, g, b, a = pal.lane_line
      col = (r, g, b, int(a * min(prob * 1.4, 1.0)))
      # dividers are painted broken; the scrolling dashes also give a sense of speed
      _tris(geo.dashes(lx, ly, lz, LANE_WIDTH, Z_LANE), col)

  def _draw_path(self, pal: Palette, state: SceneState):
    if state.path is None:
      return
    px, py, pz = geo.trim(*state.path, PATH_DIST)
    if len(px) < 2:
      return

    t = np.linspace(0.0, 1.0, len(px), dtype=np.float32)
    widths = PATH_WIDTH_NEAR + (PATH_WIDTH_FAR - PATH_WIDTH_NEAR) * t

    accel = state.accel
    night = self._theme.night
    if accel is not None and len(accel) >= 2:
      a = np.interp(np.linspace(0, len(accel) - 1, len(px)), np.arange(len(accel)), accel)
      colors = [path_color(float(v), night) for v in a[:-1]]
    else:
      colors = [path_color(0.0, night)] * max(len(px) - 1, 1)

    _tris_colored(geo.ribbon_varying(px, py, pz, widths, Z_PATH), colors)
    _tris(geo.ribbon_varying(px, py, pz, widths * 0.10, Z_PATH + 0.004), pal.path_edge)

  def _draw_shadows(self, pal: Palette, state: SceneState):
    spots = [(0.0, 0.0)]
    for lead_x, lead_y, _p, _v in state.leads:
      w = geo.car_to_world(np.array([[lead_x, lead_y, 0.0]], dtype=np.float32))[0]
      spots.append((float(w[0]), float(w[2])))

    for wx, wz in spots:
      x0, x1, z0, z1 = vehicles.shadow_quad(wx, wz)
      # world-space corners straight to triangles; car_to_world would double-transform
      y = Z_SHADOW
      c = np.array([[x0, y, z0], [x1, y, z0], [x1, y, z1], [x0, y, z1]], dtype=np.float32)
      _tris(np.stack([np.stack([c[0], c[2], c[1]]), np.stack([c[0], c[3], c[2]])]), pal.shadow)

  def _draw_blindspots(self, pal: Palette, state: SceneState):
    """BSM is a lamp, not a position: draw the zone that is occupied, never a car in it."""
    for active, sign in ((state.left_blindspot, -1.0), (state.right_blindspot, 1.0)):
      if not active:
        continue
      near, far = -5.5, 2.0
      inner, outer = sign * 1.6, sign * 4.5
      lift = np.array([0.0, 0.0, -Z_LANE - 0.01], dtype=np.float32)
      corners = np.array([[near, inner, 0.0], [far, inner, 0.0],
                          [far, outer, 0.0], [near, outer, 0.0]], dtype=np.float32) + lift
      _tris(geo.quad(corners), pal.blindspot)
      # a brighter rim keeps it reading as a marked zone rather than a stain on the road
      for i in range(4):
        a, b = corners[i], corners[(i + 1) % 4]
        _tris(geo.ribbon(np.array([a[0], b[0]]), np.array([a[1], b[1]]),
                         np.array([a[2], b[2]]), 0.16, 0.0), pal.blindspot_edge)

  # --- solids --------------------------------------------------------------------------------

  def _draw_ego(self, pal: Palette):
    self._car.draw(0.0, 0.0, pal.ego_body, pal.ego_cabin)

  def _draw_leads(self, pal: Palette, state: SceneState):
    for lead_x, lead_y, _prob, _v in state.leads:
      w = geo.car_to_world(np.array([[lead_x, lead_y, 0.0]], dtype=np.float32))[0]
      self._car.draw(float(w[0]), float(w[2]), pal.lead_body, pal.lead_cabin)

  # --- readouts ------------------------------------------------------------------------------

  def _draw_lead_labels(self, rect: rl.Rectangle, pal: Palette, state: SceneState):
    """Distance and time gap above each detected lead.

    Neither is on the HUD today, and time gap is the number that actually tells you whether you
    are following too closely. Projected with get_world_to_screen_ex so the label tracks the car.
    """
    for lead_x, lead_y, _prob, lead_v in state.leads[:1]:
      w = geo.car_to_world(np.array([[lead_x, lead_y, 1.9]], dtype=np.float32))[0]
      p = rl.get_world_to_screen_ex(
        rl.Vector3(float(w[0]), float(w[1]), float(w[2])), self._camera,
        int(rect.width), int(rect.height),
      )
      if not (0 <= p.x <= rect.width and 0 <= p.y <= rect.height):
        continue

      if state.is_metric:
        main = f"{lead_x:.0f} m"
      else:
        main = f"{lead_x * 3.28084:.0f} ft"

      sub = ""
      if state.v_ego > 2.0:
        sub = f"{lead_x / state.v_ego:.1f} s"
      if state.v_ego > 2.0:
        closing = lead_v - state.v_ego
        if closing < -1.0:
          sub += f"   -{abs(closing) * 2.23694:.0f}"

      self._label(rect, p.x, p.y, main, sub, pal)

  def _label(self, rect: rl.Rectangle, sx: float, sy: float, main: str, sub: str, pal: Palette):
    pad_x, pad_y = 14, 8
    m = rl.measure_text_ex(self._font, main, LABEL_FONT_SIZE, 0)
    s = rl.measure_text_ex(self._font, sub, LABEL_SUB_SIZE, 0) if sub else rl.Vector2(0, 0)

    w = max(m.x, s.x) + pad_x * 2
    h = m.y + (s.y + 2 if sub else 0) + pad_y * 2
    x = rect.x + sx - w * 0.5
    y = rect.y + sy - h - 10

    rl.draw_rectangle_rounded(rl.Rectangle(x, y, w, h), 0.32, 8, rl.Color(*pal.label_bg))
    rl.draw_text_ex(self._font, main, rl.Vector2(x + (w - m.x) * 0.5, y + pad_y),
                    LABEL_FONT_SIZE, 0, rl.Color(*pal.label_text))
    if sub:
      rl.draw_text_ex(self._font, sub, rl.Vector2(x + (w - s.x) * 0.5, y + pad_y + m.y + 2),
                      LABEL_SUB_SIZE, 0, rl.Color(*pal.label_dim))
