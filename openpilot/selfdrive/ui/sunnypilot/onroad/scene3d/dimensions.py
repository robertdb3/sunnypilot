"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Vehicle dimensions for the 3D scene, in metres.

Kept free of pyray so the numbers are unit-testable, and so that swapping the procedural car for a
loaded mesh is a data change rather than a code change: the mesh is normalised to exactly these
figures at build time.

Published 2018 Subaru Outback: length 4.816 m (189.6 in), width 1.839 m (72.4 in),
height 1.679 m (66.1 in), wheelbase 2.746 m (108.1 in), ground clearance 0.221 m (8.7 in).
"""

# ---- overall ----------------------------------------------------------------------------------
BODY_L = 4.82
BODY_W = 1.84
BODY_H = 1.66          # roof rail top; see the stack below
WHEELBASE = 2.746
CLEARANCE = 0.221

# 225/65R17 -> 724 mm overall diameter
WHEEL_R = 0.362
WHEEL_W = 0.225
WHEEL_INSET = 0.06

# The car spans z in [-BODY_L/2, +BODY_L/2] with -z forward, so the nose is at -2.41 and the
# tail at +2.41. Wheel centres sit at +/- half the wheelbase, which is a real measurement rather
# than the fraction-of-length guess it replaces.
WHEEL_Z = WHEELBASE * 0.5      # 1.373

# ---- vertical stack ---------------------------------------------------------------------------
# Absolute heights rather than a relative pile, so they can be checked against the published
# overall height instead of drifting.
SILL_Z0, SILL_Z1 = 0.22, 0.62      # black lower cladding
BODY_Z0, BODY_Z1 = 0.62, 1.10      # beltline; its top is the hood line
CABIN_Z0, CABIN_Z1 = 1.10, 1.58    # greenhouse
RAIL_Z0, RAIL_Z1 = 1.58, 1.66      # roof rails

SILL_W = 1.76                      # cladding tucks in from the body sides
CABIN_W = 1.62

# The wagon greenhouse is the single strongest Outback cue after the roof rails. A long hood with
# the roof running nearly to the tailgate is what separates it from a sedan; the previous
# 2.34 m cabin sitting 0.18 m forward of centre read as a saloon.
HOOD_L = 1.55                      # nose to windscreen base
CABIN_L = 2.90
CABIN_OFFS = 0.59                  # +z is rearward, so the greenhouse sits over the cargo area

RAIL_L = 1.55
RAIL_W = 0.07
RAIL_X = 0.62                      # lateral offset of each rail from centreline

# Tail lamps, drawn on the rear face. The chase camera looks straight at the back of the car, so
# these are the strongest "this is my car" cue available for the money.
LAMP_W, LAMP_H = 0.34, 0.14
LAMP_X, LAMP_Z = 0.72, 0.95

SHADOW_PAD = 0.30

# ---- baked lighting -----------------------------------------------------------------------------
# raylib has no lighting without a shader, so the key light is baked into per-face tints. It comes
# from above and BEHIND the viewer, which matters more than it looks: the chase camera stares at
# the back of every car in the scene, so a rear face dimmer than the front (as it was) means the
# only face anyone ever sees is the darkest one, and a dark paint colour renders almost black.
# Lives here rather than in vehicles.py so it can be tested without importing pyray.
FACE_TOP, FACE_FRONT, FACE_REAR, FACE_LEFT, FACE_RIGHT = 1.00, 0.62, 0.86, 0.92, 0.72

# ---- mesh normalisation -----------------------------------------------------------------------
# build_car_mesh.py scales the source model until its bounding box length equals MESH_TARGET_L and
# drops it onto y=0. Everything else about the mesh follows from that.
MESH_TARGET_L = BODY_L
MESH_FILE = "outback.gltf"


def overall_height() -> float:
  """Top of the roof rails. Published figure is 1.679 m with rails."""
  return RAIL_Z1


def cabin_bounds() -> tuple[float, float]:
  """(front, rear) z of the greenhouse. -z is forward, so front < rear."""
  return CABIN_OFFS - CABIN_L * 0.5, CABIN_OFFS + CABIN_L * 0.5
