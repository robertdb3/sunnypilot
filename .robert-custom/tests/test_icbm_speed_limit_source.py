#!/usr/bin/env python3
"""Regression checks for ICBM's authority source.

The controller has many compiled runtime imports, so these source-contract tests stay runnable on
a laptop while protecting the safety-critical distinction between the general planner target and
an actual resolved speed limit.
"""
import ast
import unittest
from pathlib import Path


CONTROLLER = (Path(__file__).resolve().parent.parent / "sunnypilot/openpilot/sunnypilot/selfdrive/car/"
              "intelligent_cruise_button_management/controller.py")


class TestSpeedLimitAuthority(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.source = CONTROLLER.read_text()
    cls.tree = ast.parse(cls.source)
    cls.update_calculations = next(node for node in ast.walk(cls.tree)
                                   if isinstance(node, ast.FunctionDef) and node.name == "update_calculations")

  def test_does_not_follow_general_planner_target(self):
    body = ast.unparse(self.update_calculations)
    self.assertNotIn("LP_SP.vTarget", body)
    self.assertIn("resolver.speedLimitFinal", body)

  def test_rejects_missing_nonfinite_and_nonpositive_limits(self):
    body = ast.unparse(self.update_calculations)
    self.assertIn("resolver.speedLimitValid", body)
    self.assertIn("resolver.source != SpeedLimitSource.none", body)
    self.assertIn("math.isfinite(speed_limit)", body)
    self.assertIn("speed_limit > 0.0", body)

  def test_invalid_limit_blocks_button_state_machine(self):
    self.assertIn("ready and self.target_valid and not button_pressed", self.source)


if __name__ == "__main__":
  unittest.main(verbosity=2)
