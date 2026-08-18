"""Render the real Scene3D code against synthetic modelV2-shaped data and export PNGs.

Not a simulator: this exercises the exact renderer that will run on the device, with geometry
shaped like what modelV2 actually publishes.

Coordinate convention (corrected in patch 0010): the model/calibrated frame is
**x forward, y RIGHT, z DOWN**. An earlier version of this docstring said "y left", which was
wrong and is exactly the confusion that produced the mirrored scene.

Grids matter and are not interchangeable:
  * laneLines / roadEdges are published on ModelConstants.X_IDXS -- a FIXED distance grid, the
    same 33 numbers every frame (fill_model_msg.py:106,114).
  * position is published on ModelConstants.T_IDXS -- a TIME grid, so its x is speed-dependent
    and moves frame to frame (fill_model_msg.py:90).
Building the path on X_IDXS here would quietly hide the resampling path the renderer needs, so
the path is built on T_IDXS * v_ego like the real message.

Usage:
    python3 render_scenes.py [--out DIR] [--jitter]

    --jitter  run the smoothing measurement instead of exporting scenes
"""
import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np
import pyray as rl

REPO = os.environ.get("SUNNYPILOT", str(Path(__file__).resolve().parent.parent / "sunnypilot"))
sys.path.insert(0, REPO)

from openpilot.selfdrive.modeld.constants import ModelConstants  # noqa: E402
from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d.scene import Scene3D, SceneState  # noqa: E402

W, H = 1600, 800

X = np.array(ModelConstants.X_IDXS, dtype=np.float32)
T = np.array(ModelConstants.T_IDXS, dtype=np.float32)


# ---- draw-call counting -----------------------------------------------------------------------
# Wrapped at the pyray level rather than instrumented into scene.py: the shipped patch stays free
# of measurement code, and this catches every path including vehicles.py's draw_model_ex.
class Counter:
  def __init__(self):
    self.tris = 0
    self.models = 0
    self._orig_tri = rl.draw_triangle_3d
    self._orig_model = rl.draw_model_ex

  def install(self):
    def tri(*a, **k):
      self.tris += 1
      return self._orig_tri(*a, **k)

    def model(*a, **k):
      self.models += 1
      return self._orig_model(*a, **k)

    rl.draw_triangle_3d = tri
    rl.draw_model_ex = model

  def restore(self):
    rl.draw_triangle_3d = self._orig_tri
    rl.draw_model_ex = self._orig_model

  def reset(self):
    self.tris = self.models = 0


def road(curvature=0.0, lane_w=3.7, crown=0.03):
  """Build 4 lane lines + 2 road edges the way modelV2 lays them out, on X_IDXS.

  z is DOWN, so a road that crowns upward toward the centre has *negative* z. Giving z a real
  profile means a re-mirroring regression shows up in the exported PNG instead of hiding.
  """
  y_c = curvature * X ** 2

  def line(offset):
    # gentle crest: highest at the crown, falling away with lateral offset and with distance
    z = (-crown * (1.0 - min(abs(offset) / (lane_w * 2.0), 1.0)) + 0.004 * X).astype(np.float32)
    return (X.copy(), (y_c + offset).astype(np.float32), z)

  lanes = [line(-lane_w * 1.5), line(-lane_w * 0.5), line(lane_w * 0.5), line(lane_w * 1.5)]
  edges = [line(-lane_w * 2.0), line(lane_w * 2.0)]
  return lanes, edges


def path_on_t_idxs(v_ego, curvature=0.0):
  """position is on T_IDXS, so x = v_ego * t -- speed-dependent, unlike the lane grid."""
  px = (v_ego * T).astype(np.float32)
  py = (curvature * px ** 2).astype(np.float32)
  pz = (0.004 * px).astype(np.float32)
  return (px, py, pz)


def scenario_straight_day():
  lanes, edges = road(curvature=0.0)
  return SceneState.from_polylines(
    lane_lines=lanes, lane_line_probs=[0.55, 1.0, 1.0, 0.55],
    road_edges=edges, path=path_on_t_idxs(27.0),
    accel=np.full(33, 0.9, dtype=np.float32),
    leads=[(41.0, 0.3, 0.95, 29.0)], v_ego=27.0,
    light_sensor=10.0, valid=True,
  )


def scenario_curve_day():
  lanes, edges = road(curvature=0.0016)
  return SceneState.from_polylines(
    lane_lines=lanes, lane_line_probs=[0.4, 1.0, 1.0, 0.9],
    road_edges=edges, path=path_on_t_idxs(24.0, 0.0016),
    accel=np.linspace(0.4, -2.2, 33).astype(np.float32),
    leads=[(24.0, 1.4, 0.98, 19.5), (63.0, 3.9, 0.7, 26.0)], v_ego=24.0,
    light_sensor=6.0, valid=True,
  )


def scenario_night_blindspot():
  lanes, edges = road(curvature=-0.0008)
  return SceneState.from_polylines(
    lane_lines=lanes, lane_line_probs=[0.8, 1.0, 1.0, 0.8],
    road_edges=edges, path=path_on_t_idxs(25.5, -0.0008),
    accel=np.full(33, -0.3, dtype=np.float32),
    leads=[(33.0, -0.6, 0.93, 25.0)], v_ego=25.5,
    left_blindspot=1.0,
    light_sensor=95.0, valid=True,
  )


def scenario_low_confidence():
  """Both outer lines barely detected -- exercises the confidence-driven alpha/length path."""
  lanes, edges = road(curvature=0.0004)
  return SceneState.from_polylines(
    lane_lines=lanes, lane_line_probs=[0.2, 0.9, 0.9, 0.2],
    road_edges=edges, path=path_on_t_idxs(18.0, 0.0004),
    accel=np.full(33, 0.2, dtype=np.float32),
    leads=[], v_ego=18.0,
    light_sensor=12.0, valid=True,
  )


def scenario_low_speed():
  """At 6 m/s the path only reaches 60 m; the far grid nodes must be masked, not flat-lined."""
  lanes, edges = road()
  return SceneState.from_polylines(
    lane_lines=lanes, lane_line_probs=[0.9, 1.0, 1.0, 0.9],
    road_edges=edges, path=path_on_t_idxs(6.0),
    accel=np.full(33, 0.1, dtype=np.float32),
    leads=[(12.0, 0.1, 0.99, 4.0)], v_ego=6.0,
    light_sensor=10.0, valid=True,
  )


def scenario_no_model():
  return SceneState(light_sensor=10.0, valid=False)


SCENARIOS = [
  ("scene_day_straight", scenario_straight_day),
  ("scene_day_curve", scenario_curve_day),
  ("scene_night_blindspot", scenario_night_blindspot),
  ("scene_low_confidence", scenario_low_confidence),
  ("scene_low_speed", scenario_low_speed),
  ("scene_no_model", scenario_no_model),
]


def run_scenes(out_dir):
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

  counter = Counter()
  os.makedirs(out_dir, exist_ok=True)

  def draw(state):
    rl.begin_texture_mode(tex)
    rl.begin_scissor_mode(0, 0, W, H)
    scene.render(rect, state)
    rl.end_scissor_mode()
    rl.end_texture_mode()

  print(f"  {'scenario':26s} {'ms/frame':>9s} {'tris':>7s} {'models':>7s}")
  print(f"  {'-' * 26} {'-' * 9} {'-' * 7} {'-' * 7}")
  total_tris = 0
  for name, build in SCENARIOS:
    state = build()
    draw(state)  # warm up (asset gen), then time a steady-state frame

    counter.install()
    counter.reset()
    draw(state)
    counter.restore()
    tris, models = counter.tris, counter.models
    total_tris += tris

    N = 20
    t0 = time.perf_counter()
    for _ in range(N):
      draw(state)
    dt = (time.perf_counter() - t0) / N * 1000.0

    img = rl.load_image_from_texture(tex.texture)
    rl.image_flip_vertical(img)
    rl.export_image(img, os.path.join(out_dir, f"{name}.png").encode())
    rl.unload_image(img)
    print(f"  {name:26s} {dt:8.2f}  {tris:7d} {models:7d}")

  print(f"  {'-' * 26} {'-' * 9} {'-' * 7} {'-' * 7}")
  print(f"  {'TOTAL tris across scenes':26s} {'':9s} {total_tris:7d}")

  scene.unload()
  rl.unload_render_texture(tex)
  rl.close_window()


def run_jitter():
  """Measure what the smoothing actually buys, in metres of frame-to-frame jitter.

  This is the number that says whether "less jumpy" happened. It runs without a GPU because
  smoothing.py is deliberately free of pyray/cereal/ui_state imports.
  """
  try:
    from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d import smoothing as sm
    from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d import geometry as geo
  except ImportError as e:
    print(f"  smoothing.py not present yet ({e}); run after step 3.")
    return

  rng = np.random.default_rng(7)
  grid = geo.GRID_S
  n_frames = 60

  # noise grows with distance, matched to how the model actually behaves
  sigma = 0.02 + 0.004 * np.clip(grid, 0.0, None)
  truth = np.zeros_like(grid)

  raw_frames, sm_frames = [], []
  smoother = sm.GridSmoother(sm.ALPHAS, sm.SNAP_DEV, sm.SNAP_MASK)
  for _ in range(n_frames):
    noisy = truth + rng.normal(0.0, sigma).astype(np.float32)
    raw_frames.append(noisy.copy())
    sm_frames.append(smoother.update(noisy).copy())

  raw = np.array(raw_frames)
  smoothed = np.array(sm_frames)

  def rms_delta(a, idx):
    return float(np.sqrt(np.mean(np.diff(a[:, idx]) ** 2)))

  # Jitter: frame-to-frame movement. This is what "jumpy" actually looks like, and more
  # reduction is strictly better -- there is no such thing as too little jitter.
  print("  jitter (RMS frame-to-frame lateral movement)")
  print(f"  {'distance':>10s} {'raw':>10s} {'smoothed':>10s} {'reduction':>10s}")
  print(f"  {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 10}")
  ok = True
  for target, want in ((3.0, 0.30), (30.0, 0.60), (100.0, 0.60)):
    idx = int(np.argmin(np.abs(grid - target)))
    r, s = rms_delta(raw, idx), rms_delta(smoothed, idx)
    red = 1.0 - s / r if r > 0 else 0.0
    flag = "" if red >= want else f"   FAIL (wanted >= {want * 100:.0f}%)"
    ok = ok and red >= want
    print(f"  {grid[idx]:9.0f}m {r:10.4f} {s:10.4f} {red * 100:9.1f}%{flag}")

  # Responsiveness is the real near-field constraint, NOT jitter reduction. Lag is what makes a
  # filter dangerous, and it is what limits how hard the near field may be smoothed. A step
  # response says directly how long the render takes to believe a real change.
  print("\n  step response (fraction of a real change tracked, by frame)")
  print(f"  {'distance':>10s} {'1 frame':>10s} {'2 frames':>10s} {'5 frames':>10s}")
  print(f"  {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 10}")
  for target, want_2 in ((3.0, 0.60), (30.0, None), (100.0, None)):
    idx = int(np.argmin(np.abs(grid - target)))
    s2 = sm.GridSmoother(sm.ALPHAS, sm.SNAP_DEV, sm.SNAP_MASK)
    s2.update(np.zeros_like(grid))
    # step well under SNAP_DEV so we measure the filter, not the snap path
    step = np.full_like(grid, 0.5)
    resp = [float(s2.update(step)[idx] / 0.5) for _ in range(5)]
    flag = ""
    if want_2 is not None and resp[1] < want_2:
      ok = False
      flag = f"   FAIL (wanted >= {want_2 * 100:.0f}% by frame 2)"
    print(f"  {grid[idx]:9.0f}m {resp[0] * 100:9.1f}% {resp[1] * 100:9.1f}% {resp[4] * 100:9.1f}%{flag}")

  # a real lane change must snap, never glide
  smoother.reset()
  smoother.update(np.zeros_like(grid))
  out = smoother.update(np.full_like(grid, 3.7))
  snapped = bool(np.allclose(out, 3.7))
  print(f"\n  lane-change snap (3.7m step): {'PASS - converged in 1 frame' if snapped else 'FAIL - lagged'}")
  print(f"\n  {'PASS' if ok and snapped else 'FAIL'}")


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "scene_out"))
  ap.add_argument("--jitter", action="store_true")
  args = ap.parse_args()

  if args.jitter:
    run_jitter()
  else:
    run_scenes(args.out)


if __name__ == "__main__":
  main()
