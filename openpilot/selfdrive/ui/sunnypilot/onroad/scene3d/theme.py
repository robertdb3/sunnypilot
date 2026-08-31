"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

from dataclasses import dataclass

# ui_state.light_sensor is 0..100, derived from camera exposure (ui_state.py:156) where 100 is
# darkest. Switch with hysteresis so passing under a bridge doesn't strobe the whole scene.
NIGHT_ENTER = 68.0
NIGHT_EXIT = 55.0

Rgba = tuple[int, int, int, int]


@dataclass(frozen=True)
class Palette:
  sky_top: Rgba          # sky is a vertical gradient; a flat fill reads as a dead backdrop
  sky_bottom: Rgba
  ground: Rgba
  road: Rgba
  road_shoulder: Rgba    # darker lip at the road edge, gives the surface thickness
  haze: Rgba             # what the ground and the far field wash out into at the horizon
  ego_lane: Rgba         # subtle fill of the lane you're in
  lane_line: Rgba
  road_edge: Rgba
  path_edge: Rgba
  ego_body: Rgba         # 2018 Outback, Wilderness Green Metallic (K4X)
  ego_cabin: Rgba
  ego_cladding: Rgba     # black lower body trim; second-strongest Outback cue after the rails
  ego_rail: Rgba         # roof rails
  ego_lamp: Rgba         # rear running lamps
  mesh_tint: Rgba        # multiplies the mesh's baked vertex colours; near-white by day
  lead_body: Rgba
  lead_cabin: Rgba
  blindspot: Rgba
  blindspot_edge: Rgba
  shadow: Rgba
  label_bg: Rgba
  label_text: Rgba
  label_dim: Rgba


DAY = Palette(
  sky_top=(150, 172, 205, 255),
  sky_bottom=(214, 222, 233, 255),
  ground=(163, 172, 168, 255),
  haze=(206, 214, 226, 255),
  road=(112, 118, 128, 255),
  road_shoulder=(88, 94, 103, 255),
  ego_lane=(255, 255, 255, 26),
  lane_line=(250, 251, 253, 240),
  road_edge=(228, 231, 236, 225),
  path_edge=(178, 214, 255, 190),
  # K4X is roughly #4A5644 as a paint chip. Lifted here for a metallic finish under sky light,
  # and because the baked face tints darken every visible face further; a literal chip value
  # renders as near-black on the rear, which is the only face the chase camera ever sees.
  ego_body=(108, 126, 98, 255),
  ego_cabin=(52, 62, 74, 255),
  ego_cladding=(58, 60, 58, 255),
  ego_rail=(168, 172, 174, 255),
  ego_lamp=(178, 46, 40, 255),
  mesh_tint=(255, 255, 255, 255),
  lead_body=(72, 80, 95, 255),
  lead_cabin=(26, 31, 40, 255),
  blindspot=(252, 170, 44, 22),
  blindspot_edge=(255, 190, 78, 225),
  shadow=(28, 32, 40, 70),
  label_bg=(18, 22, 30, 165),
  label_text=(245, 247, 250, 255),
  label_dim=(178, 188, 202, 255),
)

NIGHT = Palette(
  sky_top=(8, 10, 16, 255),
  sky_bottom=(28, 33, 45, 255),
  ground=(22, 26, 30, 255),
  haze=(15, 18, 26, 255),
  road=(44, 48, 58, 255),
  road_shoulder=(28, 31, 38, 255),
  ego_lane=(150, 190, 255, 20),
  lane_line=(214, 222, 236, 225),
  road_edge=(150, 160, 178, 200),
  path_edge=(120, 184, 255, 195),
  ego_body=(104, 126, 94, 255),
  ego_cabin=(38, 46, 58, 255),
  ego_cladding=(34, 36, 38, 255),
  ego_rail=(120, 126, 130, 255),
  ego_lamp=(150, 34, 30, 255),
  mesh_tint=(150, 160, 178, 255),
  lead_body=(58, 65, 80, 255),
  lead_cabin=(20, 24, 32, 255),
  blindspot=(252, 164, 36, 24),
  blindspot_edge=(255, 186, 70, 235),
  shadow=(0, 0, 0, 95),
  label_bg=(10, 13, 20, 195),
  label_text=(238, 242, 248, 255),
  label_dim=(150, 162, 180, 255),
)


def path_color(accel: float, night: bool) -> Rgba:
  """Colour the planned path by planned longitudinal acceleration.

  Mirrors the hue mapping openpilot already uses for the experimental-mode path
  (onroad/model_renderer.py): red when braking hard, blue-ish cruising, green when accelerating.
  It turns the path from decoration into a readout of what the car intends to do next.
  """
  a = max(-3.0, min(3.0, accel))
  if a >= 0.0:
    k = min(a / 2.0, 1.0)
    r = int(70 + (90 - 70) * k)
    g = int(150 + (220 - 150) * k)
    b = int(245 - (245 - 130) * k)
  else:
    k = min(-a / 2.5, 1.0)
    r = int(70 + (240 - 70) * k)
    g = int(150 - (150 - 110) * k)
    b = int(245 - (245 - 90) * k)

  return (r, g, b, 205 if night else 220)


class ThemeSelector:
  def __init__(self, night: bool = False):
    self.night = night

  def update(self, light_sensor: float) -> Palette:
    # light_sensor is -1 when there is no camera state yet; hold whatever we had
    if light_sensor >= 0:
      if self.night and light_sensor < NIGHT_EXIT:
        self.night = False
      elif not self.night and light_sensor > NIGHT_ENTER:
        self.night = True

    return NIGHT if self.night else DAY
