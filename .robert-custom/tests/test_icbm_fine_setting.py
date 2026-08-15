#!/usr/bin/env python3
"""Source-contract checks for the prebuilt-compatible ICBM fine-step toggle."""
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent / "sunnypilot"


class TestFineSettingPlumbing(unittest.TestCase):
  def test_param_is_registered_with_false_default(self):
    params_py = (ROOT / "openpilot/common/params.py").read_text()
    params_h = (ROOT / "openpilot/common/params_keys.h").read_text()
    expected = '"IcbmFineAdjustments", {PERSISTENT | BACKUP, BOOL, "0"}'
    self.assertIn('b"IcbmFineAdjustments": (ParamKeyType.BOOL, b"0"', params_py)
    self.assertIn(expected, params_h)

  def test_setting_reaches_the_car_port(self):
    controller = (ROOT / "openpilot/sunnypilot/selfdrive/car/"
                  "intelligent_cruise_button_management/controller.py").read_text()
    schema = (ROOT / "openpilot/cereal/custom.capnp").read_text()
    publisher = (ROOT / "openpilot/selfdrive/selfdrived/selfdrived.py").read_text()
    car_port = (ROOT / "opendbc_repo/opendbc/sunnypilot/car/subaru/icbm.py").read_text()
    self.assertIn('Params().get_bool("IcbmFineAdjustments")', controller)
    self.assertIn("fineStepEnabled @4 :Bool;", schema)
    self.assertIn("icbm.fineStepEnabled = self.icbm.fine_step_enabled", publisher)
    self.assertIn('getattr(icbm, "fineStepEnabled", False)', car_port)

  def test_ui_uses_dedicated_toggle_not_generic_increment_setting(self):
    ui = (ROOT / "openpilot/selfdrive/ui/sunnypilot/layouts/settings/cruise.py").read_text()
    self.assertIn('title=tr("ICBM Fine 1-mph Adjustments")', ui)
    self.assertIn('param="IcbmFineAdjustments"', ui)


if __name__ == "__main__":
  unittest.main(verbosity=2)
