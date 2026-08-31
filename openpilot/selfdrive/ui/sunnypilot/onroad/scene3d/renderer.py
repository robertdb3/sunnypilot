"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

from dataclasses import replace

import numpy as np
import pyray as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d import geometry as geo
from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d import smoothing as sm
from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d import scene as sc
from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d.scene import Scene3D, SceneState

# A model message that never arrives is not the same as one that says "invalid". If the stream
# stalls this long, glide out of it rather than easing toward stale geometry.
STALE_FRAMES = 10

# Fast attack so a warning can never appear late; slow release so a car hovering on the boundary
# does not flicker. The boolean is never filtered -- only how strongly the zone is drawn.
BS_ATTACK_RC, BS_RELEASE_RC = 0.05, 0.45


def _xyz(line) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
  return (
    np.asarray(line.x, dtype=np.float32),
    np.asarray(line.y, dtype=np.float32),
    np.asarray(line.z, dtype=np.float32),
  )


class Scene3DRenderer:
  """Adapts live cereal messages into SceneState and draws the scene.

  Kept separate from Scene3D so the renderer itself stays importable and testable without
  cereal, ui_state, or a running device.

  All temporal filtering lives here rather than in the draw path. update() runs once per UI frame
  but the model branch is gated on sm.updated, so geometry smoothing sees a constant 0.05 s step
  regardless of whether the UI is running at 20 FPS (tizi) or 60 (tici). Filtering while drawing
  would give the same constants three different time constants depending on hardware, and would
  cost 3x as much on tici for no benefit.
  """

  def __init__(self, fps: float = 20.0):
    self._scene = Scene3D()
    self._state = SceneState()
    dt = 1.0 / max(fps, 1.0)

    self._lane_y = [sm.GridSmoother(sm.ALPHAS, sm.SNAP_DEV, sm.SNAP_MASK) for _ in range(4)]
    self._lane_z = [sm.GridSmoother(sm.ALPHAS, sm.SNAP_DEV, sm.SNAP_MASK) for _ in range(4)]
    self._edge_y = [sm.GridSmoother(sm.ALPHAS, sm.SNAP_DEV, sm.SNAP_MASK) for _ in range(2)]
    self._edge_z = [sm.GridSmoother(sm.ALPHAS, sm.SNAP_DEV, sm.SNAP_MASK) for _ in range(2)]
    self._path_y = sm.GridSmoother(sm.PATH_ALPHAS, sm.SNAP_DEV, sm.PATH_SNAP_MASK)
    self._path_z = sm.GridSmoother(sm.PATH_ALPHAS, sm.SNAP_DEV, sm.PATH_SNAP_MASK)
    self._probs = sm.ScalarSmoother()

    self._bs_l = FirstOrderFilter(0.0, BS_RELEASE_RC, dt)
    self._bs_r = FirstOrderFilter(0.0, BS_RELEASE_RC, dt)
    self._cam_x = FirstOrderFilter(0.0, sc.CAM_LEAD_RC, dt)
    self._cam_z = FirstOrderFilter(-sc.CAM_TARGET_NEAR, sc.CAM_DEPTH_RC, dt)

    self._dt = dt
    self._dash_phase = 0.0
    self._frame = 0
    self._since_model = 0
    self._was_valid = False
    self._started_frame = -1

  def unload(self):
    self._scene.unload()

  def _reset_smoothers(self):
    """Snap, do not glide. Easing the scene into existence from stale geometry after the model
    comes back would animate a lie."""
    for f in (*self._lane_y, *self._lane_z, *self._edge_y, *self._edge_z,
              self._path_y, self._path_z, self._probs):
      f.reset()
    self._cam_x.initialized = False
    self._cam_z.initialized = False

  def update(self) -> None:
    sm_ = ui_state.sm
    self._frame += 1
    # modelV2 arrives at 20 Hz while the UI may render faster. Preserve the array references
    # between message updates instead of converting every model field to numpy on every frame.
    state = replace(self._state, light_sensor=ui_state.light_sensor, is_metric=ui_state.is_metric)

    if sm_.updated.get("carState"):
      cs = sm_["carState"]
      raw_left, raw_right = bool(cs.leftBlindspot), bool(cs.rightBlindspot)
      state.v_ego = float(cs.vEgo)
    else:
      raw_left, raw_right = self._raw_bs
      state.v_ego = self._state.v_ego
    self._raw_bs = (raw_left, raw_right)

    model_valid = sm_.valid.get("modelV2") and sm_.recv_frame["modelV2"] >= ui_state.started_frame

    # a new route, or the model coming back, must not be eased into
    if (model_valid and not self._was_valid) or ui_state.started_frame != self._started_frame:
      self._reset_smoothers()
    self._was_valid = bool(model_valid)
    self._started_frame = ui_state.started_frame

    if model_valid and sm_.updated.get("modelV2"):
      self._since_model = 0
      model = sm_["modelV2"]
      state.lane_lines = [self._grid_line(_xyz(model.laneLines[i]), geo.GRID_S, self._lane_y[i], self._lane_z[i])
                          for i in range(min(4, len(model.laneLines)))]
      state.lane_line_probs = list(self._probs.update(np.asarray(model.laneLineProbs, dtype=np.float32)))
      state.road_edges = [self._grid_line(_xyz(model.roadEdges[i]), geo.GRID_S, self._edge_y[i], self._edge_z[i])
                          for i in range(min(2, len(model.roadEdges)))]
      state.path = self._grid_line(_xyz(model.position), geo.PATH_GRID_S, self._path_y, self._path_z)
      # planned longitudinal accel, used to colour the path the way the experimental-mode
      # overlay already does (onroad/model_renderer.py). Never smoothed: it communicates planned
      # braking, and lagging that would misrepresent intent.
      if len(model.acceleration.x):
        state.accel = np.asarray(model.acceleration.x, dtype=np.float32)
      # leadsV3, not radarState: radarState leads are gated on openpilotLongitudinalControl plus a
      # radar, and cars like this Subaru have neither, so radarState would render nothing.
      state.leads = geo.lead_positions(model.leadsV3)
      state.valid = len(state.lane_lines) > 0
    elif not model_valid:
      state.valid = False
    else:
      self._since_model += 1
      if self._since_model > STALE_FRAMES:
        self._reset_smoothers()

    # --- per-frame animation, at UI rate ---
    state.blindspot_enabled = bool(ui_state.blindspot)
    for filt, raw in ((self._bs_l, raw_left), (self._bs_r, raw_right)):
      target = 1.0 if raw else 0.0
      filt.update_alpha(BS_ATTACK_RC if target > filt.x else BS_RELEASE_RC)
      filt.update(target)
    state.left_blindspot = float(self._bs_l.x)
    state.right_blindspot = float(self._bs_r.x)

    # frame counter, not a wall clock: deterministic, testable, and time.time() is banned here
    if raw_left or raw_right:
      ph = 2.0 * np.pi * sc.BS_PULSE_HZ * self._frame * self._dt
      state.pulse = float(1.0 - sc.BS_PULSE_DEPTH + sc.BS_PULSE_DEPTH * (0.5 + 0.5 * np.sin(ph)))
    else:
      state.pulse = 1.0

    # dashes scroll at true road speed; held at a standstill so they do not creep
    if state.v_ego > 0.5:
      self._dash_phase = (self._dash_phase + state.v_ego * self._dt) % (sc.DASH_LEN + sc.DASH_GAP)
    state.dash_phase = self._dash_phase

    # Camera: aesthetics only, never driven by anything the driver acts on.
    lean = 0.0
    if state.valid and state.path is not None:
      py, _pz, pn = state.path
      if pn >= 2:
        lean = float(np.interp(sc.CAM_LOOK_DIST, geo.PATH_GRID_S[:pn], py[:pn]))
    self._cam_x.update(float(np.clip(sc.CAM_LEAD_K * lean, -sc.CAM_LEAD_MAX, sc.CAM_LEAD_MAX)))
    self._cam_z.update(-(sc.CAM_TARGET_NEAR + sc.CAM_TARGET_K * state.v_ego))
    state.cam_x = float(self._cam_x.x)
    state.cam_target_z = float(self._cam_z.x)

    self._state = state

  _raw_bs: tuple[bool, bool] = (False, False)

  @staticmethod
  def _grid_line(xyz, grid, fy: sm.GridSmoother, fz: sm.GridSmoother):
    """Resample onto the fixed grid, then filter per index.

    Order matters and this way round is the cheap one: resampling first means the filter sees a
    stable index->distance mapping (which is what makes per-index EMA meaningful at all), and it
    runs over 19 values instead of the model's 33.
    """
    y, z, n = geo.resample_to_grid(*xyz, grid)
    return fy.update(y), fz.update(z), n

  def render(self, rect: rl.Rectangle) -> None:
    """Caller must already be inside begin_scissor_mode for `rect`."""
    self._scene.render(rect, self._state)
