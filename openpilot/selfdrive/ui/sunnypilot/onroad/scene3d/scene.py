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
from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d.vehicles import CarShape, EGO_STYLE

MAX_DIST = 120.0
LANE_WIDTH = 0.20
EDGE_WIDTH = 0.26
PATH_WIDTH_NEAR = 1.55  # tapers with distance, which reads as perspective rather than a flat strip
PATH_WIDTH_FAR = 0.55
PATH_FADE_START = 40.0  # the path is a prediction; it should dissolve, not stop at a flat edge

# Painted lines have a constant real width, but their jitter is angular: a far line wobbling by a
# few centimetres moves by a sub-pixel amount that reads as shimmer. Widening with distance makes
# the line cover enough pixels that the wobble stops being resolvable.
LANE_TAPER_K = 0.90

Z_ROAD, Z_SHADOW, Z_PATH, Z_LANE = 0.010, 0.014, 0.020, 0.028

CAM_POS = (0.0, 9.6, 23.0)
CAM_FOV = 38.0

# The camera used to be bolted to the origin. Letting it drift into a curve and pull back with
# speed makes the whole frame move coherently, and the eye then attributes the motion to the
# camera rather than to the data -- which perceptually hides the jitter that survives smoothing.
# Heavily damped, and never driven by anything the driver acts on.
CAM_LEAD_K, CAM_LEAD_MAX = 0.25, 1.2
CAM_LEAD_RC, CAM_DEPTH_RC = 0.50, 0.80
CAM_TARGET_NEAR, CAM_TARGET_K = 25.0, 0.60
CAM_LOOK_DIST = 40.0    # the lateral offset the camera leans toward

GROUND_AHEAD, GROUND_BEHIND, GROUND_SIDE = 600.0, 60.0, 200.0
GROUND_BANDS = 5        # enough to read as a gradient into haze; still only 10 triangles

# Far-field dissolve. Night fades much earlier and that is more truthful rather than less: the
# headlights genuinely do not reach 120 m, so pretending to know the road out there is the
# dishonest option.
FADE_DAY_START, FADE_DAY_END = 55.0, 120.0
FADE_NIGHT_START, FADE_NIGHT_END = 38.0, 85.0

FADE_DAY = geo.distance_fade(geo.GRID_S, FADE_DAY_START, FADE_DAY_END)
FADE_NIGHT = geo.distance_fade(geo.GRID_S, FADE_NIGHT_START, FADE_NIGHT_END)

# Dashes stop well before the lines do. At 100 m a real painted dash is unresolvable to a real
# driver too, so continuing them is inventing detail; beyond this the road edges and surface carry
# the far field on their own.
DASH_FADE = geo.distance_fade(geo.DASH_S, 28.0, geo.DASH_MAX_S)
DASH_LEN, DASH_GAP = 3.0, 6.0

# Confidence ramp, replacing a hard cutoff at 0.25 that made lines pop in and out.
CONF_LO, CONF_HI = 0.15, 0.70
LANE_MIN_S, LANE_CONF_S = 30.0, 90.0   # a low-confidence line is an honest short stub

LABEL_FONT_SIZE = 26
LABEL_SUB_SIZE = 20

# ---- blind spot -------------------------------------------------------------------------------
# Sized to where a car actually sits alongside, and no larger. At this camera angle the road
# beside the ego occupies a lot of screen, so a zone drawn opaque enough to notice ends up
# carpeting it. The area therefore stays a faint wash and the bright flank rail does the work of
# catching the eye -- which is also how a real BSM lamp behaves: a small bright thing, not a glow
# spread over the whole lane.
BS_NEAR, BS_FAR = -5.5, 0.6
BS_INNER, BS_OUTER = 1.05, 3.4
BS_LAYERS = 2
BS_PULSE_HZ, BS_PULSE_DEPTH = 1.4, 0.20
BS_RAIL_W = 0.22      # bright strip hugging the flank; this is what the eye catches


BS_LAYER_W = np.array([0.55, 1.00], dtype=np.float32)


def _blindspot_mesh(sign: float) -> tuple[np.ndarray, np.ndarray]:
  """Precompute one side at import: (ground wash, flank wall).

  Two parts because they do different jobs. The ground wash says *where* the zone is and stays
  deliberately faint -- amber over dark tarmac dominates at any opacity high enough to notice, and
  at this camera angle the road beside the car is a large part of the frame, so a zone bright
  enough to read ends up carpeting it.

  The rail is what actually catches the eye: a bright, crisp strip hugging the flank, exactly
  where a mirror-mounted BSM lamp would be pointing. Small and bright beats large and dim.

  Two things were tried and rejected here. A per-triangle alpha grid: draw_triangle_3d takes one
  colour per triangle, so any grid coarse enough to afford shows as visible blocks. And a low
  vertical wall along the flank: the chase camera sits almost directly behind, so a wall running
  fore-aft projects nearly edge-on and disappears to a sliver.
  """
  lift = -Z_LANE - 0.01
  wash = []
  for i in range(BS_LAYERS):
    t = i / BS_LAYERS
    outer = BS_OUTER - (BS_OUTER - BS_INNER) * t
    near = BS_NEAR + (BS_FAR - BS_NEAR) * 0.10 * t     # ends pull in slightly per layer, so the
    far = BS_FAR - (BS_FAR - BS_NEAR) * 0.06 * t       # zone tapers instead of stacking squarely
    wash.append(geo.quad(np.array([
      [near, sign * BS_INNER, lift], [far, sign * BS_INNER, lift],
      [far, sign * outer, lift], [near, sign * outer, lift],
    ], dtype=np.float32)))

  rail = geo.quad(np.array([
    [BS_NEAR, sign * BS_INNER, lift], [BS_FAR, sign * BS_INNER, lift],
    [BS_FAR, sign * (BS_INNER + BS_RAIL_W), lift], [BS_NEAR, sign * (BS_INNER + BS_RAIL_W), lift],
  ], dtype=np.float32))

  return np.concatenate(wash, axis=0), rail


_BS_L, _BS_RAIL_L = _blindspot_mesh(-1.0)   # model y is RIGHT, so left is negative y
_BS_R, _BS_RAIL_R = _blindspot_mesh(1.0)


@dataclass
class SceneState:
  """Everything the scene draws.

  Polylines arrive already resampled onto the fixed render grids as (y, z, n_valid) -- x is the
  grid itself. Doing it this way is what lets the renderer filter per index at the model rate; see
  smoothing.py. Plain arrays, so this stays testable without cereal.
  """
  lane_lines: list[tuple[np.ndarray, np.ndarray, int]] = field(default_factory=list)  # on GRID_S
  lane_line_probs: list[float] = field(default_factory=list)
  road_edges: list[tuple[np.ndarray, np.ndarray, int]] = field(default_factory=list)  # on GRID_S
  path: tuple[np.ndarray, np.ndarray, int] | None = None                              # PATH_GRID_S
  accel: np.ndarray | None = None          # planned longitudinal accel along the path
  leads: list[tuple[float, float, float, float]] = field(default_factory=list)  # x, y, prob, v
  v_ego: float = 0.0
  left_blindspot: float = 0.0              # filtered 0..1 opacity, not the raw boolean
  right_blindspot: float = 0.0
  blindspot_enabled: bool = True
  dash_phase: float = 0.0
  cam_x: float = 0.0
  cam_target_z: float = -CAM_TARGET_NEAR
  pulse: float = 1.0
  light_sensor: float = 0.0
  is_metric: bool = False
  valid: bool = False

  @classmethod
  def from_polylines(cls, lane_lines=None, road_edges=None, path=None, **kw) -> "SceneState":
    """Build from raw model-shaped polylines by resampling onto the render grids.

    Convenience for the offline harness and tests. The live renderer resamples itself so it can
    smooth in between; this is the same call sequence without the filter.
    """
    def grid(lines, g):
      out = []
      for x, y, z in lines or []:
        gy, gz, n = geo.resample_to_grid(x, y, z, g)
        out.append((gy, gz, n))
      return out

    p = None
    if path is not None:
      py, pz, pn = geo.resample_to_grid(*path, geo.PATH_GRID_S)
      p = (py, pz, pn)
    return cls(lane_lines=grid(lane_lines, geo.GRID_S),
               road_edges=grid(road_edges, geo.GRID_S), path=p, **kw)


def _tris(tris: np.ndarray, color) -> None:
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


def _tris_colored(tris: np.ndarray, colors) -> None:
  """Per-segment colours; tris are interleaved two-per-segment by ribbon_varying."""
  for i, t in enumerate(tris):
    c = colors[min(i // 2, len(colors) - 1)]
    rl.draw_triangle_3d(
      rl.Vector3(float(t[0][0]), float(t[0][1]), float(t[0][2])),
      rl.Vector3(float(t[1][0]), float(t[1][1]), float(t[1][2])),
      rl.Vector3(float(t[2][0]), float(t[2][1]), float(t[2][2])),
      rl.Color(*c),
    )


def _tris_faded(tris: np.ndarray, color, weights: np.ndarray, scale: float = 1.0) -> None:
  """One colour, per-triangle alpha. Triangles whose alpha rounds to nothing are skipped.

  draw_triangle_3d takes a single colour per triangle, so a true per-vertex gradient would need
  the rlgl batch path. Stepping the alpha across a fine enough mesh is visually equivalent here
  and keeps the fallback simple.
  """
  if tris.size == 0:
    return
  r, g, b, a = color
  for i, t in enumerate(tris):
    alpha = int(a * weights[min(i, len(weights) - 1)] * scale)
    if alpha <= 2:
      continue
    rl.draw_triangle_3d(
      rl.Vector3(float(t[0][0]), float(t[0][1]), float(t[0][2])),
      rl.Vector3(float(t[1][0]), float(t[1][1]), float(t[1][2])),
      rl.Vector3(float(t[2][0]), float(t[2][1]), float(t[2][2])),
      rl.Color(r, g, b, alpha),
    )


def _band(y_l, z_l, y_r, z_r, lift, n) -> np.ndarray:
  """Triangles for a strip between two grid-sampled edges, from node 0 to n."""
  if n < 2:
    return np.zeros((0, 3, 3), dtype=np.float32)
  x = geo.GRID_S[:n]
  left = np.stack([x, y_l[:n], z_l[:n] - lift], axis=1).astype(np.float32)
  right = np.stack([x, y_r[:n], z_r[:n] - lift], axis=1).astype(np.float32)
  lw, rw = geo.car_to_world(left), geo.car_to_world(right)
  a, b, c, d = lw[:-1], rw[:-1], lw[1:], rw[1:]
  return np.concatenate([np.stack([a, c, b], axis=1), np.stack([b, c, d], axis=1)], axis=0)


class Scene3D:
  def __init__(self):
    self._car: CarShape | None = None
    self._theme = ThemeSelector()
    self._camera = rl.Camera3D(
      rl.Vector3(*CAM_POS), rl.Vector3(0.0, 0.0, -CAM_TARGET_NEAR), rl.Vector3(0.0, 1.0, 0.0),
      CAM_FOV, rl.CameraProjection.CAMERA_PERSPECTIVE,
    )
    self._font = None
    self._measure = None
    self._horizon: tuple[float, float, float] | None = None   # (w, h, screen y), camera is fixed
    self._lead_ft: int | None = None

  def _ensure_assets(self):
    if self._car is None:
      self._car = vehicles.make_car()
    if self._font is None:
      try:
        from openpilot.system.ui.lib.application import gui_app, FontWeight
        self._font = gui_app.font(FontWeight.MEDIUM)
      except Exception:
        self._font = rl.get_font_default()   # headless harness has no gui_app
    if self._measure is None:
      # gui_app monkey-patches draw_text_ex to multiply font_size by FONT_SCALE, and
      # measure_text_cached applies the same scale. Measuring with raw measure_text_ex therefore
      # undersizes every label box by 16% and the text overflows its rounded rect.
      # Imported lazily: text_measure pulls in application.py at module scope, which would break
      # this module's headless importability that the offline harness depends on.
      try:
        from openpilot.system.ui.lib.text_measure import measure_text_cached
        self._measure = measure_text_cached
      except Exception:
        self._measure = lambda f, t, s, sp=0: rl.measure_text_ex(f, t, s, sp)  # noqa: TID251

  def unload(self):
    if self._car is not None:
      self._car.unload()
      self._car = None

  def render(self, rect: rl.Rectangle, state: SceneState) -> None:
    self._ensure_assets()
    pal = self._theme.update(state.light_sensor)
    fade = FADE_NIGHT if self._theme.night else FADE_DAY

    self._camera.position.x = state.cam_x
    self._camera.target.x = state.cam_x
    self._camera.target.z = state.cam_target_z

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
      self._draw_road(pal, state, fade)
      self._draw_ego_lane(pal, state, fade)
    self._draw_shadows(pal, state)
    if state.valid:
      self._draw_path(pal, state)
      self._draw_lane_lines(pal, state)
    self._draw_blindspots(pal, state)
    rl.rl_draw_render_batch_active()

    # Cars are closed solids, so the GPU can reject their back faces; the ground ribbons cannot,
    # because their winding flips on curves.
    rl.rl_enable_depth_test()
    rl.rl_enable_backface_culling()
    self._draw_ego(pal)
    self._draw_leads(pal, state)

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
    Fade the far field into the sky instead of pretending the world ends.

    The camera's pitch is fixed, so the projected horizon row only depends on the rect size --
    cache it rather than projecting a point every frame.
    """
    if self._horizon is None or self._horizon[0] != rect.width or self._horizon[1] != rect.height:
      far = rl.get_world_to_screen_ex(
        rl.Vector3(0.0, 0.0, -MAX_DIST * 1.4), self._camera, int(rect.width), int(rect.height)
      )
      self._horizon = (rect.width, rect.height, far.y)

    top = rect.y
    bottom = min(rect.y + rect.height, rect.y + self._horizon[2] + rect.height * 0.12)
    if bottom <= top:
      return
    rl.draw_rectangle_gradient_v(
      int(rect.x), int(top), int(rect.width), int(bottom - top),
      rl.Color(*pal.haze), rl.Color(pal.haze[0], pal.haze[1], pal.haze[2], 0),
    )

  # --- ground --------------------------------------------------------------------------------

  def _draw_ground(self, pal: Palette):
    """Banded so the ground can wash out into haze at the horizon.

    A flat fill to 600 m was the most video-game thing in the scene: real ground loses contrast
    with distance, and without that the far field reads as a painted backdrop.
    """
    gr, gg, gb, ga = pal.ground
    hr, hg, hb, _ = pal.haze
    edges = np.linspace(-GROUND_BEHIND, GROUND_AHEAD, GROUND_BANDS + 1, dtype=np.float32)
    for i in range(GROUND_BANDS):
      t = (i + 0.5) / GROUND_BANDS
      k = float(geo.smoothstep(np.array([t * 1.35], dtype=np.float32))[0])
      col = (int(gr + (hr - gr) * k), int(gg + (hg - gg) * k), int(gb + (hb - gb) * k), ga)
      _tris(geo.quad(np.array([
        [edges[i], GROUND_SIDE, 0.0], [edges[i + 1], GROUND_SIDE, 0.0],
        [edges[i + 1], -GROUND_SIDE, 0.0], [edges[i], -GROUND_SIDE, 0.0],
      ], dtype=np.float32)), col)

  def _draw_road(self, pal: Palette, state: SceneState, fade: np.ndarray):
    if len(state.road_edges) < 2:
      return

    ly, lz, ln = state.road_edges[0]
    ry, rz, rn = state.road_edges[-1]
    n = min(ln, rn)
    if n < 2:
      return

    # a wider shoulder pass under the surface gives the road a lip instead of a paper edge
    for pad, colour, lift in ((0.55, pal.road_shoulder, Z_ROAD), (0.0, pal.road, Z_ROAD + 0.002)):
      _tris(_band(ly - pad, lz, ry + pad, rz, lift, n), colour)

    for ey, ez, en in state.road_edges:
      if en < 2:
        continue
      widths = EDGE_WIDTH * (1.0 + LANE_TAPER_K * np.clip(geo.GRID_S[:en] / 90.0, 0.0, 1.0))
      tris = geo.ribbon_varying(geo.GRID_S[:en], ey[:en], ez[:en], widths, Z_LANE)
      _tris_faded(tris, pal.road_edge, np.repeat(fade[:en - 1], 2))

  def _draw_ego_lane(self, pal: Palette, state: SceneState, fade: np.ndarray):
    """Lightly fill the lane the car is in.

    Costs two triangles per segment and does more for legibility than anything else here: it
    separates 'my lane' from 'the road' at a glance, which is most of what the Tesla view is
    actually communicating. Scaled by confidence, because it is also the scene's strongest claim.
    """
    if len(state.lane_lines) < 4:
      return
    ly, lz, ln = state.lane_lines[1]
    ry, rz, rn = state.lane_lines[2]
    n = min(ln, rn)
    if n < 2:
      return

    conf = min(self._conf(state, 1), self._conf(state, 2))
    if conf <= 0.02:
      return
    tris = _band(ly, lz, ry, rz, Z_ROAD + 0.004, n)
    _tris_faded(tris, pal.ego_lane, np.tile(fade[:n - 1], 2), conf)

  @staticmethod
  def _conf(state: SceneState, i: int) -> float:
    p = state.lane_line_probs[i] if i < len(state.lane_line_probs) else 1.0
    return float(geo.smoothstep(np.array([(p - CONF_LO) / (CONF_HI - CONF_LO)], dtype=np.float32))[0])

  def _draw_lane_lines(self, pal: Palette, state: SceneState):
    """Dashes only, fading out well before the horizon.

    Real dividers are painted broken, and the phase scrolls with ego motion so the road reads as
    moving. Length is gated on confidence: a barely-detected line becomes a short stub near the
    car, which is an honest statement of what the model knows rather than a confident line drawn
    through noise.
    """
    for i, (ly, lz, ln) in enumerate(state.lane_lines):
      conf = self._conf(state, i)
      if conf <= 0.02 or ln < 2:
        continue

      # resample the smoothed line onto the dash grid; even arc steps keep the dash pattern
      # regular and the triangle count fixed
      valid_s = geo.GRID_S[ln - 1]
      reach = min(float(valid_s), LANE_MIN_S + LANE_CONF_S * conf, geo.DASH_MAX_S)
      dy = np.interp(geo.DASH_S, geo.GRID_S[:ln], ly[:ln]).astype(np.float32)
      dz = np.interp(geo.DASH_S, geo.GRID_S[:ln], lz[:ln]).astype(np.float32)
      n = int(np.count_nonzero(geo.DASH_S <= reach))
      if n < 2:
        continue

      widths = LANE_WIDTH * (1.0 + LANE_TAPER_K * np.clip(geo.DASH_S[:n] / 90.0, 0.0, 1.0))
      tris = geo.dashes_on_grid(dy, dz, widths, Z_LANE, state.dash_phase,
                               DASH_LEN, DASH_GAP, n_valid=n)
      if tris.size == 0:
        continue
      # rebuild the per-triangle fade for exactly the segments that survived the dash mask
      mid = 0.5 * (geo.DASH_S[:n - 1] + geo.DASH_S[1:n])
      keep = ((mid + state.dash_phase) % (DASH_LEN + DASH_GAP)) < DASH_LEN
      w = np.repeat(DASH_FADE[:n - 1][keep], 2)
      _tris_faded(tris, pal.lane_line, w, conf)

  def _draw_path(self, pal: Palette, state: SceneState):
    if state.path is None:
      return
    py, pz, pn = state.path
    if pn < 2:
      return
    x = geo.PATH_GRID_S[:pn]
    t = np.clip(x / geo.PATH_GRID_S[-1], 0.0, 1.0)
    widths = PATH_WIDTH_NEAR + (PATH_WIDTH_FAR - PATH_WIDTH_NEAR) * t

    accel = state.accel
    night = self._theme.night
    if accel is not None and len(accel) >= 2:
      # accel is on the model's own time grid; resample onto the path's distance nodes
      a = np.interp(np.linspace(0, len(accel) - 1, pn), np.arange(len(accel)), accel)
      colors = [path_color(float(v), night) for v in a[:-1]]
    else:
      colors = [path_color(0.0, night)] * max(pn - 1, 1)

    # Dissolve the far end instead of stopping at a flat edge. The path is a prediction, and one
    # that gets less certain the further out it goes, so it should not end with a hard boundary.
    tail = geo.distance_fade(x[:-1], PATH_FADE_START, geo.PATH_GRID_S[-1])
    colors = [(r, g, b, int(al * tail[i])) for i, (r, g, b, al) in enumerate(colors)]

    # The separate outline ribbon is gone: it was a second coplanar strip for pure decoration.
    _tris_colored(geo.ribbon_varying(x, py[:pn], pz[:pn], widths, Z_PATH), colors)

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
    """BSM is a lamp, not a position: draw the zone that is occupied, never a car in it.

    Opacity is filtered with a fast attack and a slow release, so a warning can never appear late
    but also cannot flicker as a car drifts along the boundary. The boolean itself is never
    filtered -- only how strongly the zone is drawn.
    """
    if not state.blindspot_enabled:
      return
    for level, wash, rail in ((state.left_blindspot, _BS_L, _BS_RAIL_L),
                              (state.right_blindspot, _BS_R, _BS_RAIL_R)):
      if level <= 0.01:
        continue
      k = level * state.pulse
      _tris_faded(wash, pal.blindspot, np.repeat(BS_LAYER_W, 2), k)
      _tris_faded(rail, pal.blindspot_edge, np.ones(2, dtype=np.float32), k)

  # --- solids --------------------------------------------------------------------------------

  def _draw_ego(self, pal: Palette):
    self._car.draw(0.0, 0.0, pal.ego_body, pal.ego_cabin, style=EGO_STYLE(pal))

  def _draw_leads(self, pal: Palette, state: SceneState):
    for lead_x, lead_y, _prob, _v in state.leads:
      w = geo.car_to_world(np.array([[lead_x, lead_y, 0.0]], dtype=np.float32))[0]
      # leads stay generic: they are other traffic, not more Outbacks
      self._car.draw(float(w[0]), float(w[2]), pal.lead_body, pal.lead_cabin, generic=True)

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

      # The driver acts on this number, so it is never filtered. Hysteretic rounding kills the
      # flicker with zero latency instead: the value shown is always a rounding of the live one.
      from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d.smoothing import hysteretic_round
      raw = lead_x if state.is_metric else lead_x * 3.28084
      self._lead_ft = hysteretic_round(raw, self._lead_ft)
      main = f"{self._lead_ft} m" if state.is_metric else f"{self._lead_ft} ft"

      sub = ""
      if state.v_ego > 2.0:
        sub = f"{lead_x / state.v_ego:.1f} s"
        closing = lead_v - state.v_ego
        if closing < -1.0:
          sub += f"   -{abs(closing) * 2.23694:.0f}"

      self._label(rect, p.x, p.y, main, sub, pal)

  def _label(self, rect: rl.Rectangle, sx: float, sy: float, main: str, sub: str, pal: Palette):
    pad_x, pad_y = 14, 8
    m = self._measure(self._font, main, LABEL_FONT_SIZE, 0)
    s = self._measure(self._font, sub, LABEL_SUB_SIZE, 0) if sub else rl.Vector2(0, 0)

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
