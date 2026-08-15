#!/usr/bin/env python3
"""Regression test for the scalar get_accel_from_plan contract in modeld_v2."""

import ast
import os
import unittest


ROOT = os.environ.get("SUNNYPILOT", os.path.join(os.path.dirname(__file__), "..", "sunnypilot"))
MODELD = os.path.join(ROOT, "openpilot", "sunnypilot", "modeld_v2", "modeld.py")


class TestAccelContract(unittest.TestCase):
  def test_modeld_v2_does_not_unpack_scalar_acceleration(self):
    with open(MODELD, encoding="utf-8") as f:
      tree = ast.parse(f.read(), MODELD)

    assignments = [
      node for node in ast.walk(tree)
      if isinstance(node, ast.Assign)
      and isinstance(node.value, ast.Call)
      and isinstance(node.value.func, ast.Name)
      and node.value.func.id == "get_accel_from_plan"
    ]
    self.assertEqual(len(assignments), 1)
    self.assertEqual(len(assignments[0].targets), 1)
    self.assertIsInstance(assignments[0].targets[0], ast.Name)
    self.assertEqual(assignments[0].targets[0].id, "desired_accel")

  def test_non_action_path_computes_should_stop(self):
    with open(MODELD, encoding="utf-8") as f:
      tree = ast.parse(f.read(), MODELD)

    should_stop_assignments = [
      node for node in ast.walk(tree)
      if isinstance(node, ast.Assign)
      and any(isinstance(target, ast.Name) and target.id == "should_stop" for target in node.targets)
    ]
    self.assertGreaterEqual(len(should_stop_assignments), 2)


if __name__ == "__main__":
  unittest.main(verbosity=2)
