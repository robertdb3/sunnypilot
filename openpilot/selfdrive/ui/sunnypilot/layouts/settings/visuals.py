"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.common.params import Params
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.multilang import tr, tr_noop
from openpilot.system.ui.sunnypilot.widgets.list_view import toggle_item_sp, multiple_button_item_sp, option_item_sp
from openpilot.system.ui.widgets.scroller_tici import Scroller
from openpilot.system.ui.widgets import Widget

CHEVRON_INFO_DESCRIPTION = {
  "enabled": tr_noop("Display useful metrics below the chevron that tracks the lead car " +
                     "only applicable to cars with sunnypilot longitudinal control."),
  "disabled": tr_noop("This feature requires sunnypilot longitudinal control to be available.")
}


class VisualsLayout(Widget):
  def __init__(self):
    super().__init__()

    self._params = Params()
    items = self._initialize_items()
    self._scroller = Scroller(items, line_separator=True, spacing=0)

  def _initialize_items(self):
    self._toggle_defs = {
      "BlindSpot": (
        lambda: tr("Show Blind Spot Warnings"),
        tr("Enabling this will display warnings when a vehicle is detected in your " +
           "blind spot as long as your car has BSM supported."),
        None,
      ),
      "TorqueBar": (
        lambda: tr("Steering Arc"),
        tr("Display steering arc on the driving screen when lateral control is enabled."),
        None,
      ),
      "RainbowMode": (
        lambda: tr("Enable Tesla Rainbow Mode"),
        tr("A beautiful rainbow effect on the path the model wants to take. " +
           "It does not affect driving in any way."),
        None,
      ),
      "StandstillTimer": (
        lambda: tr("Enable Standstill Timer"),
        tr("Show a timer on the HUD when the car is at a standstill."),
        None,
      ),
      "RoadNameToggle": (
        lambda: tr("Display Road Name"),
        tr("Displays the name of the road the car is traveling on." +
           "<br>The OpenStreetMap database of the location must be downloaded from " +
           "the OSM panel to fetch the road name."),
        None,
      ),
      "GreenLightAlert": (
        lambda: tr("Green Traffic Light Alert (Beta)"),
        tr("A chime and on-screen alert will play when the traffic light you are waiting for " +
           "turns green and you have no vehicle in front of you." +
           "<br>Note: This chime is only designed as a notification. " +
           "It is the driver's responsibility to observe their environment and make decisions accordingly."),
        None,
      ),
      "LeadDepartAlert": (
        lambda: tr("Lead Departure Alert (Beta)"),
        tr("A chime and on-screen alert will play when you are stopped, and the vehicle in front of you start moving." +
           "<br>Note: This chime is only designed as a notification. " +
           "It is the driver's responsibility to observe their environment and make decisions accordingly."),
        None,
      ),
      "TrueVEgoUI": (
        lambda: tr("Speedometer: Always Display True Speed"),
        tr("For applicable vehicles, always display the true vehicle current speed from wheel speed sensors."),
        None,
      ),
      "HideVEgoUI": (
        lambda: tr("Speedometer: Hide from Onroad Screen"),
        tr("When enabled, the speedometer on the onroad screen is not displayed."),
        None,
      ),
      "ShowTurnSignals": (
        lambda: tr("Display Turn Signals"),
        tr("When enabled, visual turn indicators are drawn on the HUD."),
        None,
      ),
      "RocketFuel": (
        lambda: tr("Real-time Acceleration Bar"),
        tr("Show an indicator on the left side of the screen to display real-time vehicle acceleration and deceleration. " +
           "This displays what the car is currently doing, not what the planner is requesting."),
        None,
      ),
      "RecklessWatch": (
        lambda: tr("Virginia Reckless Speed Watch"),
        tr("Virginia treats 20 mph over the limit, or any speed above 85 mph, as reckless driving "
           "\u2014 a criminal offence rather than a ticket. Notifies you on entering the state, "
           "sounds once when you cross the threshold below, then shows a pulsing red border while "
           "you stay above it. The 20-over check stays quiet where no speed limit data exists. "
           "A driver aid, not legal advice: your car's speed will differ from an officer's radar."),
        None,
      ),
      "Scene3D": (
        lambda: tr("3D Driving Scene"),
        tr("Replace the camera view with a rendered 3D scene: your car, the road, lane lines, "
           "the planned path, and any vehicles the model actually detects ahead. Only measured "
           "objects are drawn \u2014 nothing is shown behind you, and a blind spot appears as a "
           "zone rather than a car, because the sensor reports presence and not position."),
        None,
      ),
      "MapPanel": (
        lambda: tr("Map Panel"),
        tr("Show a heading-up map in the corner of the driving screen. It is drawn from the offline " +
           "maps downloaded on the OSM page, so it needs no data connection. Download your region " +
           "there first, otherwise the panel stays empty."),
        None,
      ),
    }
    self._toggles = {}
    for param, (title, desc, callback) in self._toggle_defs.items():
      toggle = toggle_item_sp(
        title=title,
        description=desc,
        param=param,
        initial_state=ui_state.params.get_bool(param),
        callback=callback,
      )
      self._toggles[param] = toggle

    self._chevron_info = multiple_button_item_sp(
      title=lambda: tr("Display Metrics Below Chevron"),
      description="",
      buttons=[lambda: tr("Off"), lambda: tr("Distance"), lambda: tr("Speed"), lambda: tr("Time"), lambda: tr("All")],
      param="ChevronInfo",
      inline=False
    )
    self._dev_ui_info = multiple_button_item_sp(
      title=lambda: tr("Developer UI"),
      description=lambda: tr("Display real-time parameters and metrics from various sources."),
      buttons=[lambda: tr("Off"), lambda: tr("Bottom"), lambda: tr("Right"), lambda: tr("Right & Bottom")],
      param="DevUIInfo",
      button_width=350,
      inline=False
    )

    self._reckless_threshold = option_item_sp(
      title=lambda: tr("Reckless Watch Alert Speed"),
      param="RecklessWatchThresholdMph",
      description=lambda: tr("Sound the alert at this speed. Virginia's statute is above 85 mph; "
                             "the default of 82 leaves room to react and absorbs speedometer error."),
      min_value=60,
      max_value=90,
      value_change_step=1,
      label_callback=lambda x: f"{x} mph",
    )

    self._map_panel_zoom = multiple_button_item_sp(
      title=lambda: tr("Map Panel Zoom"),
      description=lambda: tr("How much ground the map panel covers."),
      buttons=[lambda: tr("Close"), lambda: tr("Medium"), lambda: tr("Wide")],
      param="MapPanelZoom",
      button_width=350,
      inline=False
    )

    self._scene_3d_theme = multiple_button_item_sp(
      title=lambda: tr("3D Scene Appearance"),
      description=lambda: tr("Use ambient light to switch automatically, or keep the 3D driving scene light or dark."),
      buttons=[lambda: tr("Auto"), lambda: tr("Always Light"), lambda: tr("Always Dark")],
      param="Scene3DTheme",
      button_width=350,
      inline=False
    )

    items = list(self._toggles.values()) + [
      self._reckless_threshold,
      self._chevron_info,
      self._dev_ui_info,
      self._scene_3d_theme,
      self._map_panel_zoom,
    ]
    return items

  def _update_state(self):
    super()._update_state()

    for param in self._toggle_defs:
      self._toggles[param].action_item.set_state(self._params.get_bool(param))

    self._dev_ui_info.action_item.set_selected_button(ui_state.params.get("DevUIInfo", return_default=True))
    self._scene_3d_theme.action_item.set_selected_button(ui_state.params.get("Scene3DTheme", return_default=True))

    if ui_state.has_longitudinal_control:
      self._chevron_info.set_description(tr(CHEVRON_INFO_DESCRIPTION["enabled"]))
      self._chevron_info.action_item.set_selected_button(ui_state.params.get("ChevronInfo", return_default=True))
      self._chevron_info.action_item.set_enabled(True)
    else:
      self._chevron_info.set_description(tr(CHEVRON_INFO_DESCRIPTION["disabled"]))
      self._chevron_info.action_item.set_enabled(False)
      ui_state.params.put("ChevronInfo", 0)

  def _render(self, rect):
    self._scroller.render(rect)

  def show_event(self):
    self._scroller.show_event()
    if not ui_state.has_longitudinal_control:
      self._chevron_info.set_description(tr(CHEVRON_INFO_DESCRIPTION["disabled"]))
      self._chevron_info.show_description(True)
