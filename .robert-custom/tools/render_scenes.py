"""Render the real Scene3D code against synthetic modelV2-shaped data and export PNGs.

Not a simulator: this exercises the exact renderer that will run on the device, with geometry
shaped like what modelV2 actually publishes (33-point polylines in car frame, x forward / y left).
"""
import os
import sys
import time
from pathlib import Path

import numpy as np
import pyray as rl

REPO = os.environ.get("SUNNYPILOT", str(Path(__file__).resolve().parent.parent / "sunnypilot"))
sys.path.insert(0, REPO)

from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d.scene import Scene3D, SceneState  # noqa: E402

W, H = 1600, 800

# modelV2 publishes 33 points on a non-uniform x grid out to ~192m
X = np.array([float(i) ** 1.75 * 0.35 for i in range(33)], dtype=np.float32)


def road(curvature=0.0, lane_w=3.7, crown=0.0):
  """Build 4 lane lines + 2 road edges + a path, the way modelV2 lays them out."""
  y_c = curvature * X ** 2
  z = -crown * (X * 0.0)  # flat; kept explicit so a slope is easy to add

  def line(offset):
    return (X.copy(), (y_c + offset).astype(np.float32), (z + 0.0).astype(np.float32))

  lanes = [line(lane_w * 1.5), line(lane_w * 0.5), line(-lane_w * 0.5), line(-lane_w * 1.5)]
  edges = [line(lane_w * 2.0), line(-lane_w * 2.0)]
  path = line(0.0)
  return lanes, edges, path


def scenario_straight_day():
  lanes, edges, path = road(curvature=0.0)
  return SceneState(
    lane_lines=lanes, lane_line_probs=[0.55, 1.0, 1.0, 0.55],
    road_edges=edges, path=path,
    accel=np.full(33, 0.9, dtype=np.float32),
    leads=[(41.0, 0.3, 0.95)], lead_speeds=[29.0], v_ego=27.0,
    light_sensor=10.0, valid=True,
  )


def scenario_curve_day():
  lanes, edges, path = road(curvature=0.0016)
  return SceneState(
    lane_lines=lanes, lane_line_probs=[0.4, 1.0, 1.0, 0.9],
    road_edges=edges, path=path,
    accel=np.linspace(0.4, -2.2, 33).astype(np.float32),
    leads=[(24.0, 1.4, 0.98), (63.0, 3.9, 0.7)], lead_speeds=[19.5, 26.0], v_ego=24.0,
    light_sensor=6.0, valid=True,
  )


def scenario_night_blindspot():
  lanes, edges, path = road(curvature=-0.0008)
  return SceneState(
    lane_lines=lanes, lane_line_probs=[0.8, 1.0, 1.0, 0.8],
    road_edges=edges, path=path,
    accel=np.full(33, -0.3, dtype=np.float32),
    leads=[(33.0, -0.6, 0.93)], lead_speeds=[25.0], v_ego=25.5,
    left_blindspot=True,
    light_sensor=95.0, valid=True,
  )


def scenario_no_model():
  return SceneState(light_sensor=10.0, valid=False)


SCENARIOS = [
  ("scene_day_straight", scenario_straight_day),
  ("scene_day_curve", scenario_curve_day),
  ("scene_night_blindspot", scenario_night_blindspot),
  ("scene_no_model", scenario_no_model),
]


def main():
  # FLAG_WINDOW_HIDDEN crashes GLFW on macOS at large window sizes, so keep the hidden window
  # small and render into a big texture instead. The window aspect must still match the texture:
  # begin_mode_3d takes its projection from the screen dimensions, not the render target.
  rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN | rl.ConfigFlags.FLAG_MSAA_4X_HINT)
  rl.init_window(W // 2, H // 2, b"scene3d")
  rl.set_target_fps(0)

  tex = rl.load_render_texture(W, H)
  scene = Scene3D()
  # the device gets its font from gui_app; headless we load the same Inter face so the
  # screenshots show real on-device typography instead of raylib's bitmap fallback
  font_path = os.path.join(REPO, "openpilot/selfdrive/assets/fonts/Inter-Medium.ttf")
  scene._ensure_assets()
  scene._font = rl.load_font_ex(font_path.encode(), 64, rl.ffi.NULL, 0)
  rl.set_texture_filter(scene._font.texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
  rect = rl.Rectangle(0, 0, W, H)

  for name, build in SCENARIOS:
    state = build()
    # warm up once (asset gen), then time a steady-state frame
    rl.begin_texture_mode(tex)
    rl.begin_scissor_mode(0, 0, W, H)
    scene.render(rect, state)
    rl.end_scissor_mode()
    rl.end_texture_mode()

    N = 20
    t0 = time.perf_counter()
    for _ in range(N):
      rl.begin_texture_mode(tex)
      rl.begin_scissor_mode(0, 0, W, H)
      scene.render(rect, state)
      rl.end_scissor_mode()
      rl.end_texture_mode()
    dt = (time.perf_counter() - t0) / N * 1000.0

    img = rl.load_image_from_texture(tex.texture)
    rl.image_flip_vertical(img)
    rl.export_image(img, f"{name}.png".encode())
    rl.unload_image(img)
    print(f"  {name:26s} {dt:6.1f} ms/frame")

  scene.unload()
  rl.unload_render_texture(tex)
  rl.close_window()


if __name__ == "__main__":
  main()
