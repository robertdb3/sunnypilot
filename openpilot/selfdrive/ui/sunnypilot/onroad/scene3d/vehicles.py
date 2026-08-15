"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

import pyray as rl

# Low-poly cars from primitives: no external mesh assets, nothing to license or vendor.
#
# Faces are drawn individually with a per-face brightness rather than as a single flat-shaded
# cube. raylib has no lighting without a custom shader, and shaders mean GLSL version differences
# between the desktop and comma backends. Baking a fixed key light into face tints costs nothing,
# never breaks, and is what actually makes the shapes read as solid objects.

# world axes: +x right, +y up, -z forward
FACE_TOP, FACE_FRONT, FACE_REAR, FACE_LEFT, FACE_RIGHT = 1.0, 0.86, 0.58, 0.93, 0.70

# rough Outback-ish proportions, metres
BODY_W, BODY_H, BODY_L = 1.86, 0.52, 4.62
SILL_W, SILL_H = 1.74, 0.26          # lower body, slightly tucked in
CABIN_W, CABIN_H, CABIN_L = 1.60, 0.58, 2.34
CABIN_Z = -0.18                       # cabin sits a little rearward
WHEEL_R, WHEEL_W, WHEEL_INSET = 0.33, 0.22, 0.06
SHADOW_PAD = 0.30


def _shade(color: tuple[int, int, int, int], k: float) -> rl.Color:
  return rl.Color(int(color[0] * k), int(color[1] * k), int(color[2] * k), color[3])


class CarShape:
  """Reusable unit cube, drawn as scaled per-face instances to build a car."""

  def __init__(self):
    self._cube = rl.load_model_from_mesh(rl.gen_mesh_cube(1.0, 1.0, 1.0))
    self._loaded = True

  def unload(self):
    if self._loaded:
      rl.unload_model(self._cube)
      self._loaded = False

  def _face(self, cx, cy, cz, w, h, ln, color: rl.Color):
    rl.draw_model_ex(self._cube, rl.Vector3(cx, cy, cz), rl.Vector3(0.0, 1.0, 0.0), 0.0,
                     rl.Vector3(w, h, ln), color)

  def _shaded_box(self, cx, cy, cz, w, h, ln, color):
    """A box built from thin slabs, each tinted for its facing direction.

    Cheaper and more robust than a shader, and the slight inset between slabs reads as a panel
    gap rather than an artifact.
    """
    t = 0.012
    # sides first, then the lid on top, so the brightest face wins where they meet
    self._face(cx - w * 0.5 + t * 0.5, cy, cz, t, h, ln, _shade(color, FACE_LEFT))
    self._face(cx + w * 0.5 - t * 0.5, cy, cz, t, h, ln, _shade(color, FACE_RIGHT))
    self._face(cx, cy, cz - ln * 0.5 + t * 0.5, w, h, t, _shade(color, FACE_FRONT))
    self._face(cx, cy, cz + ln * 0.5 - t * 0.5, w, h, t, _shade(color, FACE_REAR))
    self._face(cx, cy + h * 0.5 - t * 0.5, cz, w, t, ln, _shade(color, FACE_TOP))
    # interior fill so nothing shows through at grazing angles
    self._face(cx, cy, cz, w - t, h - t, ln - t, _shade(color, FACE_RIGHT))

  def draw(self, x: float, z: float, body: tuple, cabin: tuple, scale: float = 1.0):
    """Draw a car centred at world (x, z), sitting on the ground plane."""
    bw, bh, bl = BODY_W * scale, BODY_H * scale, BODY_L * scale
    sw, sh = SILL_W * scale, SILL_H * scale
    cw, ch, cl = CABIN_W * scale, CABIN_H * scale, CABIN_L * scale
    wr, ww = WHEEL_R * scale, WHEEL_W * scale

    sill_y = wr * 0.72
    body_y = sill_y + sh * 0.5 + bh * 0.5
    cabin_y = body_y + bh * 0.5 + ch * 0.5

    self._shaded_box(x, sill_y, z, sw, sh, bl * 0.98, body)
    self._shaded_box(x, body_y, z, bw, bh, bl, body)
    self._shaded_box(x, cabin_y, z + CABIN_Z * scale, cw, ch, cl, cabin)

    tyre = (16, 18, 22, 255)
    for dx in (-1, 1):
      for dz in (-1, 1):
        wx = x + dx * (bw * 0.5 - ww * 0.5 - WHEEL_INSET * scale)
        wz = z + dz * (bl * 0.31)
        self._face(wx, wr * 0.62, wz, ww, wr * 1.24, wr * 2.0, rl.Color(*tyre))


def shadow_quad(x: float, z: float, scale: float = 1.0):
  """Footprint for a car's contact shadow, in world space.

  Without this the cars look pasted onto the road rather than standing on it — the single
  cheapest thing that sells the 3D.
  """
  hw = (BODY_W * scale) * 0.5 + SHADOW_PAD
  hl = (BODY_L * scale) * 0.5 + SHADOW_PAD
  return x - hw, x + hw, z - hl, z + hl
