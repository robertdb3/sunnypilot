"""Render the REAL RecklessBorderRenderer over the real 3D scene.

Stubs only openpilot.selfdrive.ui.ui_state, which pulls msgq's aarch64 extension. Everything
drawn here is the code that will run on the device.
"""
import math
import os
import sys
import types
from pathlib import Path

import numpy as np
import pyray as rl

REPO = os.environ.get("SUNNYPILOT", str(Path(__file__).resolve().parent.parent / "sunnypilot"))
sys.path.insert(0, REPO)

# --- stub ui_state before importing anything that touches it ---------------------------------
_ui = types.ModuleType("openpilot.selfdrive.ui.ui_state")


class _UIState:
  reckless_over = False
  is_metric = False
  light_sensor = 0.0
  sm = {}


_ui.ui_state = _UIState()
_ui.UIStatus = types.SimpleNamespace(DISENGAGED=0, OVERRIDE=1, ENGAGED=2)
sys.modules["openpilot.selfdrive.ui.ui_state"] = _ui

from openpilot.selfdrive.ui.sunnypilot.onroad.reckless_border import RecklessBorderRenderer  # noqa: E402
from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d.scene import Scene3D, SceneState  # noqa: E402

W, H = 1600, 800
X = np.array([float(i) ** 1.75 * 0.35 for i in range(33)], dtype=np.float32)
ENGAGED_GREEN = (0x16, 0x7F, 0x40, 0xFF)
UI_BORDER = 30


def road(curvature=0.0, lane_w=3.7):
  y_c = curvature * X ** 2

  def line(off):
    return (X.copy(), (y_c + off).astype(np.float32), np.zeros_like(X))

  return ([line(lane_w * 1.5), line(lane_w * .5), line(-lane_w * .5), line(-lane_w * 1.5)],
          [line(lane_w * 2), line(-lane_w * 2)], line(0.0))


def scene_state(night=False):
  lanes, edges, path = road(curvature=0.0011)
  return SceneState(
    lane_lines=lanes, lane_line_probs=[0.6, 1.0, 1.0, 0.9],
    road_edges=edges, path=path,
    accel=np.full(33, 0.3, dtype=np.float32),
    leads=[(46.0, 0.8, 0.95, 39.0)],
    v_ego=38.0, light_sensor=95.0 if night else 8.0, valid=True,
  )


def engagement_border(rect):
  c = rl.Color(*ENGAGED_GREEN)
  x, y, w, h = int(rect.x), int(rect.y), int(rect.width), int(rect.height)
  rl.draw_rectangle(x, y, w, UI_BORDER, c)
  rl.draw_rectangle(x, y + h - UI_BORDER, w, UI_BORDER, c)
  rl.draw_rectangle(x, y + UI_BORDER, UI_BORDER, h - 2 * UI_BORDER, c)
  rl.draw_rectangle(x + w - UI_BORDER, y + UI_BORDER, UI_BORDER, h - 2 * UI_BORDER, c)


def main():
  rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN | rl.ConfigFlags.FLAG_MSAA_4X_HINT)
  rl.init_window(W // 2, H // 2, b"reckless")
  tex = rl.load_render_texture(W, H)
  scene = Scene3D()
  scene._ensure_assets()
  scene._font = rl.load_font_ex(
    f"{REPO}/openpilot/selfdrive/assets/fonts/Inter-Medium.ttf".encode(), 64, rl.ffi.NULL, 0)
  rl.set_texture_filter(scene._font.texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)

  border = RecklessBorderRenderer()
  rect = rl.Rectangle(0, 0, W, H)
  inner = rl.Rectangle(UI_BORDER, UI_BORDER, W - 2 * UI_BORDER, H - 2 * UI_BORDER)

  shots = [
    ("reckless_off", False, 0.0, False),
    ("reckless_day_peak", True, math.pi / 2, False),
    ("reckless_day_trough", True, -math.pi / 2, False),
    ("reckless_night_peak", True, math.pi / 2, True),
  ]

  for name, active, phase, night in shots:
    _UIState.reckless_over = active
    border._level = 1.0 if active else 0.0
    border._phase = phase % (2 * math.pi)

    rl.begin_texture_mode(tex)
    rl.begin_scissor_mode(0, 0, W, H)
    scene.render(inner, scene_state(night))
    rl.end_scissor_mode()
    engagement_border(rect)          # what the border normally looks like, underneath
    border.render(rect)              # the real renderer on top
    rl.end_texture_mode()

    img = rl.load_image_from_texture(tex.texture)
    rl.image_flip_vertical(img)
    rl.export_image(img, f"{name}.png".encode())
    c = rl.get_image_color(img, 8, H // 2)
    print(f"  {name:22s} border pixel rgba=({c.r},{c.g},{c.b},{c.a})")
    rl.unload_image(img)

  scene.unload()
  rl.unload_render_texture(tex)
  rl.close_window()


if __name__ == "__main__":
  main()
