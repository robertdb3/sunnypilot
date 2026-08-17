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

  def test_stop_flag_covers_both_action_paths(self):
    """The stop flag must be defined on every path out of get_action_from_model.

    Patch 0007 used to guarantee this by duplicating the assignment into the
    `if 'action' not in model_output` branch, because upstream only set it in
    the `else`. Upstream `37d6dc5` fixed that by hoisting the computation out
    of the conditional instead, so asserting on the *number* of assignments
    would now fail on correct code. Assert the property that actually matters:
    the flag is assigned unconditionally, not inside either branch.
    """
    with open(MODELD, encoding="utf-8") as f:
      tree = ast.parse(f.read(), MODELD)

    func = next(
      (node for node in ast.walk(tree)
       if isinstance(node, ast.FunctionDef) and node.name == "get_action_from_model"),
      None,
    )
    self.assertIsNotNone(func, "get_action_from_model not found in modeld.py")

    names = {"stop", "should_stop"}
    unconditional = [
      node for node in func.body
      if isinstance(node, ast.Assign)
      and any(isinstance(t, ast.Name) and t.id in names for t in node.targets)
    ]
    self.assertTrue(
      unconditional,
      "no unconditional stop-flag assignment in get_action_from_model; if it is set only "
      "inside a branch, the other path raises NameError or reuses a stale value",
    )

    assigned = {t.id for node in unconditional for t in node.targets if isinstance(t, ast.Name)}
    action_calls = [
      node for node in ast.walk(func)
      if isinstance(node, ast.Call)
      and any(kw.arg == "shouldStop" for kw in node.keywords)
    ]
    self.assertTrue(action_calls, "no Action(...) construction passing shouldStop")
    for call in action_calls:
      kw = next(kw for kw in call.keywords if kw.arg == "shouldStop")
      used = {n.id for n in ast.walk(kw.value) if isinstance(n, ast.Name)}
      self.assertTrue(
        used & assigned,
        f"shouldStop is not fed by the unconditional stop flag {sorted(assigned)}",
      )


if __name__ == "__main__":
  unittest.main(verbosity=2)
