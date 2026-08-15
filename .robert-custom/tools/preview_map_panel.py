#!/usr/bin/env python3
"""
Render the onroad map panel to a PNG, off the car.

Runs the real projection, culling and draw code from map_panel.py against real offline map
data, so styling and zoom can be iterated without flashing the device. Also prints the
per-frame cost of each stage.

  python3 tools/preview_map_panel.py --lat 38.9096 --lon -77.0434 --bearing 30

Needs pycapnp, numpy and pyray, and a mapd offline tree (--root, default the device path).
"""

import argparse
import importlib.util
import math
import os
import sys
import time
import types

DEFAULT_CHECKOUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sunnypilot")


def load_modules(checkout: str, root: str):
  # stub the hardware path module so offline_map imports without the full openpilot tree
  hw = types.ModuleType("openpilot.common.hardware.hw")

  class Paths:
    @staticmethod
    def mapd_root() -> str:
      return root

  hw.Paths = Paths
  sys.modules.setdefault("openpilot.common.hardware.hw", hw)

  spec = importlib.util.spec_from_file_location(
    "offline_map", os.path.join(checkout, "openpilot/sunnypilot/mapd/offline_map.py"))
  om = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(om)

  # pull the pure functions and style table out of map_panel without importing ui_state,
  # which would drag in the whole UI stack
  import numpy as np
  import pyray as rl
  with open(os.path.join(checkout, "openpilot/selfdrive/ui/sunnypilot/onroad/map_panel.py")) as f:
    src = f.read()
  ns = {"np": np, "rl": rl, "math": math, "EARTH_R": 6371000.0}
  for marker in ("ROAD_STYLES = (", "BACKGROUND = ", "def project(", "def visible_ways("):
    i = src.index(marker)
    j = src.index("\n\n\n", i) if marker.startswith("def ") else src.index("\n\n", i)
    exec(src[i:j], ns)
  return om, ns


def main() -> int:
  p = argparse.ArgumentParser()
  p.add_argument("--lat", type=float, required=True)
  p.add_argument("--lon", type=float, required=True)
  p.add_argument("--bearing", type=float, default=0.0)
  p.add_argument("--mpp", type=float, default=1.1, help="metres per pixel")
  p.add_argument("--size", type=int, default=460)
  p.add_argument("--root", default="/data/media/0/osm", help="mapd offline tree")
  p.add_argument("--checkout", default=DEFAULT_CHECKOUT)
  p.add_argument("--out", default="map_preview.png")
  a = p.parse_args()

  om, ns = load_modules(a.checkout, a.root)
  import numpy as np
  import pyray as rl

  t0 = time.perf_counter()
  data = om.load_slice(a.lat, a.lon, 2000.0)
  t1 = time.perf_counter()
  print(f"load_slice:    {(t1 - t0) * 1000:7.1f} ms   ways={data.num_ways} points={len(data.points)}")
  if data.num_ways == 0:
    print(f"no geometry near {a.lat},{a.lon} -- is {a.root} populated for that area?")
    return 1

  rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN)
  rl.init_window(a.size, a.size, b"map preview")
  rl.set_target_fps(0)

  panel = rl.Rectangle(0, 0, a.size, a.size)
  cx, cy = a.size / 2.0, a.size * 0.68

  t0 = time.perf_counter()
  xy = ns["project"](data.points, a.lat, a.lon, a.bearing, a.mpp, cx, cy)
  idx = ns["visible_ways"](xy, data.starts, data.counts, data.classes, panel, 400)
  t1 = time.perf_counter()
  segs = int(data.counts[idx].sum() - len(idx)) if len(idx) else 0
  print(f"project+cull:  {(t1 - t0) * 1000:7.2f} ms   ways={len(idx)} segments={segs}")

  styles = ns["ROAD_STYLES"]
  last = len(styles) - 1
  buf = rl.ffi.new("Vector2[]", len(xy))

  target = rl.load_render_texture(a.size, a.size)
  rl.begin_texture_mode(target)
  rl.clear_background(ns["BACKGROUND"])

  t0 = time.perf_counter()
  rl.ffi.memmove(buf, rl.ffi.from_buffer(xy), xy.nbytes)
  budget = 1200
  for i in idx:
    n = int(data.counts[i])
    if budget <= 0:
      break
    color, width = styles[min(int(data.classes[i]), last)]
    n = min(n, budget + 1)
    s = int(data.starts[i])
    for j in range(s, s + n - 1):
      rl.draw_line_ex(buf[j], buf[j + 1], width, color)
    budget -= n - 1
  t1 = time.perf_counter()
  print(f"draw:          {(t1 - t0) * 1000:7.2f} ms")

  rl.draw_triangle(rl.Vector2(cx, cy - 15), rl.Vector2(cx - 10, cy + 11),
                   rl.Vector2(cx + 10, cy + 11), rl.Color(70, 160, 255, 255))
  rl.end_texture_mode()

  img = rl.load_image_from_texture(target.texture)
  rl.image_flip_vertical(img)  # render textures are bottom-up
  rl.export_image(img, a.out.encode())
  rl.close_window()
  print(f"wrote {a.out}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
