#!/usr/bin/env python3
"""Keep model-synchronous planning off the custom UI's saturated CPU."""
import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent / "sunnypilot/openpilot/selfdrive/controls"


class TestPlanningCoreIsolation(unittest.TestCase):
  def test_radar_and_planner_use_idle_core_six(self):
    for name in ("radard.py", "plannerd.py"):
      tree = ast.parse((ROOT / name).read_text())
      calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and
               isinstance(node.func, ast.Name) and node.func.id == "config_realtime_process"]
      self.assertEqual(len(calls), 1, name)
      self.assertEqual(ast.literal_eval(calls[0].args[0]), 6, name)


if __name__ == "__main__":
  unittest.main(verbosity=2)
