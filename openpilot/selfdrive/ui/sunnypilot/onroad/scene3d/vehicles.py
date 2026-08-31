"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

import os

import pyray as rl

from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d import dimensions as dim

# Light is baked in: per-face tints for the procedural car, per-vertex colours for the loaded
# mesh. The face constants live in dimensions.py so they can be tested without pyray.
# world axes: +x right, +y up, -z forward
FACE_TOP = dim.FACE_TOP
FACE_FRONT = dim.FACE_FRONT
FACE_REAR = dim.FACE_REAR
FACE_LEFT = dim.FACE_LEFT
FACE_RIGHT = dim.FACE_RIGHT

SHADOW_PAD = dim.SHADOW_PAD
ASSET_DIR = os.path.join(os.path.dirname(__file__), "assets")

TYRE = (16, 18, 22, 255)


class CarStyle:
  """Optional extra parts. Leads pass nothing and stay generic; only the ego car is an Outback."""

  __slots__ = ("cladding", "rail", "lamp", "mesh_tint")

  def __init__(self, cladding=None, rail=None, lamp=None, mesh_tint=None):
    self.cladding = cladding
    self.rail = rail
    self.lamp = lamp
    self.mesh_tint = mesh_tint


def EGO_STYLE(pal) -> CarStyle:
  return CarStyle(cladding=pal.ego_cladding, rail=pal.ego_rail, lamp=pal.ego_lamp,
                  mesh_tint=pal.mesh_tint)


def _shade(color, k: float) -> rl.Color:
  return rl.Color(int(color[0] * k), int(color[1] * k), int(color[2] * k), color[3])


class CarShape:
  """A car built from one reusable unit cube, drawn as scaled per-face instances.

  No external assets, nothing to license or vendor. This is the fallback whenever the mesh is
  missing, and it is what every lead vehicle uses.
  """

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

    The front face is omitted: the camera sits behind the ego car and behind every lead, so no
    car's nose is ever visible. Three fewer draws per car, and nothing to see through because
    seeing into the open front would require looking from in front.
    """
    t = 0.012
    self._face(cx - w * 0.5 + t * 0.5, cy, cz, t, h, ln, _shade(color, FACE_LEFT))
    self._face(cx + w * 0.5 - t * 0.5, cy, cz, t, h, ln, _shade(color, FACE_RIGHT))
    self._face(cx, cy, cz + ln * 0.5 - t * 0.5, w, h, t, _shade(color, FACE_REAR))
    self._face(cx, cy + h * 0.5 - t * 0.5, cz, w, t, ln, _shade(color, FACE_TOP))
    # interior fill so nothing shows through at grazing angles
    self._face(cx, cy, cz, w - t, h - t, ln - t, _shade(color, FACE_RIGHT))

  def draw(self, x: float, z: float, body, cabin, scale: float = 1.0,
           style: CarStyle | None = None, generic: bool = False):
    """Draw a car centred at world (x, z), sitting on the ground plane."""
    s = scale
    sill_h = (dim.SILL_Z1 - dim.SILL_Z0) * s
    body_h = (dim.BODY_Z1 - dim.BODY_Z0) * s
    cab_h = (dim.CABIN_Z1 - dim.CABIN_Z0) * s

    sill_y = (dim.SILL_Z0 + dim.SILL_Z1) * 0.5 * s
    body_y = (dim.BODY_Z0 + dim.BODY_Z1) * 0.5 * s
    cab_y = (dim.CABIN_Z0 + dim.CABIN_Z1) * 0.5 * s

    cab_l = (dim.CABIN_L if not generic else dim.CABIN_L * 0.78) * s
    cab_z = z + (dim.CABIN_OFFS if not generic else 0.0) * s

    # The cladding is a free Outback cue: same triangles, different colour.
    sill_col = style.cladding if (style and style.cladding) else body
    self._shaded_box(x, sill_y, z, dim.SILL_W * s, sill_h, dim.BODY_L * 0.98 * s, sill_col)
    self._shaded_box(x, body_y, z, dim.BODY_W * s, body_h, dim.BODY_L * s, body)
    self._shaded_box(x, cab_y, cab_z, dim.CABIN_W * s, cab_h, cab_l, cabin)

    wr, ww = dim.WHEEL_R * s, dim.WHEEL_W * s
    for dx in (-1, 1):
      for dz in (-1, 1):
        wx = x + dx * (dim.BODY_W * 0.5 * s - ww * 0.5 - dim.WHEEL_INSET * s)
        wz = z + dz * dim.WHEEL_Z * s
        self._face(wx, wr * 0.62, wz, ww, wr * 1.24, wr * 2.0, rl.Color(*TYRE))

    if style is None:
      return

    if style.rail:
      # roof rails: the single most recognisable Outback silhouette cue, two boxes
      rail_y = (dim.RAIL_Z0 + dim.RAIL_Z1) * 0.5 * s
      rail_h = (dim.RAIL_Z1 - dim.RAIL_Z0) * s
      for dx in (-1, 1):
        self._face(x + dx * dim.RAIL_X * s, rail_y, cab_z, dim.RAIL_W * s, rail_h,
                   dim.RAIL_L * s, _shade(style.rail, FACE_TOP))

    if style.lamp:
      # Constant running lamps only. Never wired to brakePressed: openpilot does not control
      # longitudinal on this car, and rendering a brake state invites reading the view as ground
      # truth about the vehicle.
      lz = z + dim.BODY_L * 0.5 * s
      for dx in (-1, 1):
        self._face(x + dx * dim.LAMP_X * s, dim.LAMP_Z * s, lz,
                   dim.LAMP_W * s, dim.LAMP_H * s, 0.03 * s, rl.Color(*style.lamp))


class MeshCarShape(CarShape):
  """Ego car from a decimated real Outback mesh, with the generic car as the fallback.

  Shading is baked into the mesh's vertex colours at build time (see tools/build_car_mesh.py), so
  this still needs no shader. draw_model_ex's tint multiplies over those colours, which is what
  lets the day/night palette shift the whole car without a second asset.

  Inherits CarShape so leads -- and the ego car if the asset is missing -- still render.
  """

  def __init__(self, path: str):
    super().__init__()
    self._model = rl.load_model(path.encode())
    # A model that failed to load comes back with no meshes; fall back rather than draw nothing.
    self._have_mesh = self._model.meshCount > 0
    self._mesh_path = path

  def unload(self):
    if self._have_mesh:
      rl.unload_model(self._model)
      self._have_mesh = False
    super().unload()

  def draw(self, x: float, z: float, body, cabin, scale: float = 1.0,
           style: CarStyle | None = None, generic: bool = False):
    if generic or not self._have_mesh:
      super().draw(x, z, body, cabin, scale, style, generic)
      return
    # The mesh already carries its paint and its baked light in vertex colours, so the tint must
    # be near-white -- passing the body colour here would multiply green by green and render a
    # dark smear. The tint is what shifts the whole car between the day and night palettes.
    tint = (style.mesh_tint if (style and style.mesh_tint) else (255, 255, 255, 255))
    # authored nose-forward in the same frame the scene uses, so no rotation is needed
    rl.draw_model_ex(self._model, rl.Vector3(x, 0.0, z), rl.Vector3(0.0, 1.0, 0.0), 0.0,
                     rl.Vector3(scale, scale, scale), rl.Color(*tint))


def make_car() -> CarShape:
  """Mesh if it was vendored, procedural otherwise. Fails closed by construction."""
  path = os.path.join(ASSET_DIR, dim.MESH_FILE)
  if os.path.exists(path):
    car = MeshCarShape(path)
    if car._have_mesh:
      return car
    car.unload()
  return CarShape()


def shadow_quad(x: float, z: float, scale: float = 1.0):
  """Footprint for a car's contact shadow, in world space.

  Without this the cars look pasted onto the road rather than standing on it -- the single
  cheapest thing that sells the 3D.
  """
  hw = (dim.BODY_W * scale) * 0.5 + SHADOW_PAD
  hl = (dim.BODY_L * scale) * 0.5 + SHADOW_PAD
  return x - hw, x + hw, z - hl, z + hl
