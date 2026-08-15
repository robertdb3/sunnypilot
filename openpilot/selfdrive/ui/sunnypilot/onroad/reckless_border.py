"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

import math

import pyray as rl

from openpilot.selfdrive.ui import UI_BORDER_SIZE
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.widgets import Widget

# The onroad border is a flat colour keyed off UIStatus (onroad/augmented_road_view.py:29), so this
# paints a separate pulsing band over it rather than trying to add a state to that mapping. Red
# with a slow pulse, deliberately unlike any engagement colour.
PULSE_HZ = 0.8
ALPHA_MIN, ALPHA_MAX = 70, 235
COLOR = (220, 38, 38)

FADE_RATE = 3.0   # how fast it appears and clears, in alpha-fraction per second


class RecklessBorderRenderer(Widget):
  """Persistent, silent reminder that the car is above the Virginia threshold.

  The audible alert fires once on the rising edge; this is what keeps it visible afterwards
  without nagging.
  """

  def __init__(self):
    super().__init__()
    self._phase = 0.0
    self._level = 0.0     # eased 0..1 so it does not snap on and off

  def update(self, dt: float = 0.05) -> None:
    active = bool(getattr(ui_state, "reckless_over", False))
    target = 1.0 if active else 0.0
    step = FADE_RATE * dt
    self._level = min(self._level + step, target) if target > self._level \
        else max(self._level - step, target)

    if self._level > 0.0:
      self._phase = (self._phase + 2.0 * math.pi * PULSE_HZ * dt) % (2.0 * math.pi)
    else:
      self._phase = 0.0

  def _render(self, rect: rl.Rectangle) -> None:
    if self._level <= 0.01:
      return

    pulse = 0.5 * (1.0 + math.sin(self._phase))
    alpha = int((ALPHA_MIN + (ALPHA_MAX - ALPHA_MIN) * pulse) * self._level)
    color = rl.Color(*COLOR, alpha)

    b = UI_BORDER_SIZE
    x, y, w, h = int(rect.x), int(rect.y), int(rect.width), int(rect.height)
    # four bands rather than a filled rect, so the camera or 3D scene stays visible
    rl.draw_rectangle(x, y, w, b, color)
    rl.draw_rectangle(x, y + h - b, w, b, color)
    rl.draw_rectangle(x, y + b, b, h - 2 * b, color)
    rl.draw_rectangle(x + w - b, y + b, b, h - 2 * b, color)
