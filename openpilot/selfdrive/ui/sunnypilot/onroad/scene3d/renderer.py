"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

from dataclasses import replace

import numpy as np
import pyray as rl

from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d import geometry as geo
from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d.scene import Scene3D, SceneState


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
  """

  def __init__(self):
    self._scene = Scene3D()
    self._state = SceneState()

  def unload(self):
    self._scene.unload()

  def update(self) -> None:
    sm = ui_state.sm
    # modelV2 arrives at 20 Hz while the UI renders at 60 Hz. Preserve the immutable array
    # references between message updates instead of converting every model field to numpy three
    # times per model frame.
    state = replace(self._state, light_sensor=ui_state.light_sensor, is_metric=ui_state.is_metric)

    if sm.updated.get("carState"):
      cs = sm["carState"]
      state.left_blindspot = bool(cs.leftBlindspot)
      state.right_blindspot = bool(cs.rightBlindspot)
      state.v_ego = float(cs.vEgo)
    else:
      state.left_blindspot = self._state.left_blindspot
      state.right_blindspot = self._state.right_blindspot
      state.v_ego = self._state.v_ego

    model_valid = sm.valid.get("modelV2") and sm.recv_frame["modelV2"] >= ui_state.started_frame
    if model_valid and sm.updated.get("modelV2"):
      model = sm["modelV2"]
      state.lane_lines = [_xyz(line) for line in model.laneLines]
      state.lane_line_probs = [float(p) for p in model.laneLineProbs]
      state.road_edges = [_xyz(edge) for edge in model.roadEdges]
      state.path = _xyz(model.position)
      # planned longitudinal accel, used to colour the path the way the experimental-mode
      # overlay already does (onroad/model_renderer.py)
      if len(model.acceleration.x):
        state.accel = np.asarray(model.acceleration.x, dtype=np.float32)
      # leadsV3, not radarState: radarState leads are gated on openpilotLongitudinalControl plus a
      # radar, and cars like this Subaru have neither, so radarState would render nothing.
      state.leads = geo.lead_positions(model.leadsV3)
      state.valid = len(state.lane_lines) > 0
    elif not model_valid:
      state.valid = False

    self._state = state

  def render(self, rect: rl.Rectangle) -> None:
    """Caller must already be inside begin_scissor_mode for `rect`."""
    self._scene.render(rect, self._state)
