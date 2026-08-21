from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d.theme import (
  DAY, NIGHT, NIGHT_ENTER, NIGHT_EXIT, ThemeMode, ThemeSelector,
)


def test_auto_theme_uses_correct_sensor_direction():
  selector = ThemeSelector()
  assert selector.update(NIGHT_ENTER - 1) == NIGHT

  selector = ThemeSelector(night=True)
  assert selector.update(NIGHT_EXIT + 1) == DAY


def test_forced_modes_ignore_sensor():
  selector = ThemeSelector(dt=1.0)
  selector.update(100.0)

  for _ in range(100):
    palette = selector.update(0.0, ThemeMode.LIGHT)
  assert not selector.night
  assert palette.sky_top == DAY.sky_top

  for _ in range(100):
    palette = selector.update(100.0, ThemeMode.DARK)
  assert selector.night
  assert palette.sky_top == NIGHT.sky_top


def test_short_shadow_does_not_switch_to_dark():
  selector = ThemeSelector(dt=0.05)
  selector.update(80.0)

  # Three seconds under a bridge is far shorter than the sensor filter time constant.
  for _ in range(60):
    selector.update(0.0)
  assert not selector.night


def test_palette_transition_is_gradual():
  selector = ThemeSelector(dt=0.05)
  selector.update(80.0)
  first = selector.update(80.0, ThemeMode.DARK)

  assert selector.night
  assert NIGHT.sky_top[0] < first.sky_top[0] < DAY.sky_top[0]
