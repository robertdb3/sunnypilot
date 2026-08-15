#!/usr/bin/env python3
"""
Tests for the offline map reader and the map panel's projection/culling.

Cell files are synthesised through the vendored schema rather than shipping real OSM
extracts, so the fixtures are deterministic and no map data is redistributed. The path
scheme is checked against filenames observed in a real mapd download.

Needs pycapnp and numpy:

    python3 -m venv .venv && .venv/bin/pip install pycapnp==2.1.0 numpy
    .venv/bin/python tests/test_offline_map.py
"""

import importlib.util
import math
import os
import sys
import tempfile
import types
import unittest

CHECKOUT = os.environ.get("SUNNYPILOT",
                          os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sunnypilot"))
READER = os.path.join(CHECKOUT, "openpilot/sunnypilot/mapd/offline_map.py")
PANEL = os.path.join(CHECKOUT, "openpilot/selfdrive/ui/sunnypilot/onroad/map_panel.py")


def _load():
  hw = types.ModuleType("openpilot.common.hardware.hw")

  class Paths:
    @staticmethod
    def mapd_root() -> str:
      return "/nonexistent"

  hw.Paths = Paths
  sys.modules.setdefault("openpilot.common.hardware.hw", hw)
  spec = importlib.util.spec_from_file_location("offline_map", READER)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


om = _load()
import numpy as np  # noqa: E402


def _panel_funcs():
  """project/visible_ways from map_panel without importing the whole UI stack."""
  import pyray as rl
  with open(PANEL) as f:
    src = f.read()
  ns = {"np": np, "rl": rl, "math": math, "EARTH_R": 6371000.0}
  for fn in ("def project(", "def visible_ways("):
    i = src.index(fn)
    exec(src[i:src.index("\n\n\n", i)], ns)
  return ns, rl


def write_cell(root, cell_lat, cell_lon, ways):
  """Write a synthetic offline cell. ways is [(highwayClass, [(lat, lon), ...]), ...]."""
  path = om.cell_path(cell_lat + 0.01, cell_lon + 0.01, root)
  os.makedirs(os.path.dirname(path), exist_ok=True)

  msg = om.offline_capnp.Offline.new_message()
  msg.minLat, msg.minLon = cell_lat, cell_lon
  msg.maxLat, msg.maxLon = cell_lat + om.CELL_DEG, cell_lon + om.CELL_DEG
  msg.overlap = 0.001
  ws = msg.init("ways", len(ways))
  for i, (cls, nodes) in enumerate(ways):
    w = ws[i]
    w.highwayClass = cls
    w.name = f"way{i}"
    w.minLat = min(n[0] for n in nodes)
    w.maxLat = max(n[0] for n in nodes)
    w.minLon = min(n[1] for n in nodes)
    w.maxLon = max(n[1] for n in nodes)
    ns = w.init("nodes", len(nodes))
    for j, (la, lo) in enumerate(nodes):
      ns[j].latitude, ns[j].longitude = la, lo
  with open(path, "wb") as f:
    f.write(msg.to_bytes_packed())
  return path


class TestPathScheme(unittest.TestCase):
  """Cells are 0.25 deg, grouped into 2 deg directories, both floored."""

  def test_cell_origin_floors(self):
    self.assertEqual(om.cell_origin(38.8977, -77.0365), (38.75, -77.25))
    self.assertEqual(om.cell_origin(42.834, -106.360), (42.75, -106.5))

  def test_cell_origin_on_boundary(self):
    self.assertEqual(om.cell_origin(38.75, -77.25), (38.75, -77.25))

  def test_group_floors_not_truncates(self):
    # int() would give -106 here and point at the wrong directory
    self.assertEqual(om.group_origin(43.9, -106.3), (42, -108))
    self.assertEqual(om.group_origin(38.8977, -77.0365), (38, -78))

  def test_paths_match_real_download(self):
    # filenames observed in map-data.pfeifer.dev offline/38/-78 and offline/42/-108
    cases = [
      (38.8977, -77.0365, "38/-78/38.750000_-77.250000_39.000000_-77.000000"),
      (42.8340, -106.360, "42/-108/42.750000_-106.500000_43.000000_-106.250000"),
      (43.9000, -106.300, "42/-108/43.750000_-106.500000_44.000000_-106.250000"),
    ]
    for lat, lon, tail in cases:
      with self.subTest(lat=lat, lon=lon):
        self.assertTrue(om.cell_path(lat, lon, "/r").endswith(tail),
                        f"{om.cell_path(lat, lon, '/r')} != .../{tail}")


class TestLoadSlice(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.root = self.tmp.name
    self.addCleanup(self.tmp.cleanup)

  def test_missing_region_is_empty_not_an_error(self):
    s = om.load_slice(38.8977, -77.0365, 2000.0, self.root)
    self.assertEqual(s.num_ways, 0)

  def test_loads_and_flattens(self):
    write_cell(self.root, 38.75, -77.25, [
      ("motorway", [(38.90, -77.04), (38.901, -77.041), (38.902, -77.042)]),
      ("residential", [(38.90, -77.05), (38.9005, -77.0505)]),
    ])
    s = om.load_slice(38.90, -77.04, 2000.0, self.root)
    self.assertEqual(s.num_ways, 2)
    self.assertEqual(len(s.points), 5)
    self.assertEqual(list(s.counts), [3, 2])
    self.assertEqual(list(s.starts), [0, 3])
    self.assertEqual(s.classes[0], om.CLASS_PRIORITY["motorway"])
    self.assertEqual(s.classes[1], om.CLASS_PRIORITY["residential"])

  def test_bbox_filter_drops_distant_ways(self):
    write_cell(self.root, 38.75, -77.25, [
      ("motorway", [(38.90, -77.04), (38.901, -77.041)]),
      ("motorway", [(38.79, -77.20), (38.791, -77.201)]),   # ~11 km away
    ])
    s = om.load_slice(38.90, -77.04, 2000.0, self.root)
    self.assertEqual(s.num_ways, 1)

  def test_single_node_ways_dropped(self):
    write_cell(self.root, 38.75, -77.25, [("motorway", [(38.90, -77.04)])])
    self.assertEqual(om.load_slice(38.90, -77.04, 2000.0, self.root).num_ways, 0)

  def test_spans_adjacent_cells(self):
    # a point just inside one cell with a 2 km radius reaching the neighbour
    write_cell(self.root, 38.75, -77.25, [("motorway", [(38.9995, -77.04), (38.9996, -77.041)])])
    write_cell(self.root, 39.00, -77.25, [("motorway", [(39.0005, -77.04), (39.0006, -77.041)])])
    s = om.load_slice(38.9999, -77.04, 2000.0, self.root)
    self.assertEqual(s.num_ways, 2, "radius crossing a cell edge should load both cells")


class TestProjection(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    try:
      cls.ns, cls.rl = _panel_funcs()
    except ImportError as e:
      raise unittest.SkipTest(f"pyray unavailable: {e}") from e

  def test_car_position_maps_to_centre(self):
    xy = self.ns["project"](np.array([[38.9, -77.0]]), 38.9, -77.0, 0.0, 1.0, 100.0, 200.0)
    self.assertAlmostEqual(float(xy[0, 0]), 100.0, places=3)
    self.assertAlmostEqual(float(xy[0, 1]), 200.0, places=3)

  def test_north_is_up_at_zero_bearing(self):
    # 100 m north => 100 px up (screen y decreases)
    d = 100.0 / 6371000.0 * 180.0 / math.pi
    xy = self.ns["project"](np.array([[38.9 + d, -77.0]]), 38.9, -77.0, 0.0, 1.0, 0.0, 0.0)
    self.assertAlmostEqual(float(xy[0, 0]), 0.0, places=1)
    self.assertAlmostEqual(float(xy[0, 1]), -100.0, places=1)

  def test_heading_up_rotates(self):
    # heading east: a point 100 m north should appear to the LEFT of the car
    d = 100.0 / 6371000.0 * 180.0 / math.pi
    xy = self.ns["project"](np.array([[38.9 + d, -77.0]]), 38.9, -77.0, 90.0, 1.0, 0.0, 0.0)
    self.assertAlmostEqual(float(xy[0, 0]), -100.0, places=1)
    self.assertAlmostEqual(float(xy[0, 1]), 0.0, places=1)

  def test_metres_per_pixel_scales(self):
    d = 100.0 / 6371000.0 * 180.0 / math.pi
    xy = self.ns["project"](np.array([[38.9 + d, -77.0]]), 38.9, -77.0, 0.0, 2.0, 0.0, 0.0)
    self.assertAlmostEqual(float(xy[0, 1]), -50.0, places=1)

  def test_empty_input(self):
    out = self.ns["project"](np.zeros((0, 2)), 38.9, -77.0, 0.0, 1.0, 0.0, 0.0)
    self.assertEqual(out.shape, (0, 2))
    self.assertEqual(out.dtype, np.float32)

  def test_output_is_float32_for_memmove(self):
    """Vector2 is two float32s; the memmove into the raylib buffer relies on this."""
    xy = self.ns["project"](np.array([[38.9, -77.0]]), 38.9, -77.0, 0.0, 1.0, 0.0, 0.0)
    self.assertEqual(xy.dtype, np.float32)
    self.assertEqual(xy.strides[0], self.rl.ffi.sizeof("Vector2"))


class TestCulling(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    try:
      cls.ns, cls.rl = _panel_funcs()
    except ImportError as e:
      raise unittest.SkipTest(f"pyray unavailable: {e}") from e

  def _rect(self):
    return self.rl.Rectangle(0, 0, 100, 100)

  def test_offscreen_ways_dropped(self):
    xy = np.array([[10, 10], [20, 20], [500, 500], [600, 600]], dtype=np.float32)
    idx = self.ns["visible_ways"](xy, np.array([0, 2]), np.array([2, 2]),
                                  np.array([0, 0], dtype=np.uint8), self._rect(), 100)
    self.assertEqual(list(idx), [0])

  def test_way_crossing_panel_is_kept(self):
    # both endpoints outside, but the way spans the panel
    xy = np.array([[-50, 50], [150, 50]], dtype=np.float32)
    idx = self.ns["visible_ways"](xy, np.array([0]), np.array([2]),
                                  np.array([0], dtype=np.uint8), self._rect(), 100)
    self.assertEqual(list(idx), [0])

  def test_budget_keeps_most_important(self):
    xy = np.array([[10, 10], [20, 20]] * 3, dtype=np.float32)
    starts = np.array([0, 2, 4])
    counts = np.array([2, 2, 2])
    classes = np.array([10, 0, 5], dtype=np.uint8)  # residential, motorway, primary-link
    idx = self.ns["visible_ways"](xy, starts, counts, classes, self._rect(), 2)
    self.assertEqual(list(idx), [1, 2], "should keep the two lowest priority numbers")

  def test_always_ordered_by_importance(self):
    """Ordering must hold even under the limit: the caller's segment budget cuts the tail,
    so an unsorted result would drop motorways and keep side streets."""
    xy = np.array([[10, 10], [20, 20]] * 3, dtype=np.float32)
    idx = self.ns["visible_ways"](xy, np.array([0, 2, 4]), np.array([2, 2, 2]),
                                  np.array([10, 0, 5], dtype=np.uint8), self._rect(), 100)
    self.assertEqual(list(idx), [1, 2, 0])

  def test_empty(self):
    idx = self.ns["visible_ways"](np.zeros((0, 2), dtype=np.float32), np.zeros(0, dtype=np.int32),
                                  np.zeros(0, dtype=np.int32), np.zeros(0, dtype=np.uint8),
                                  self._rect(), 10)
    self.assertEqual(len(idx), 0)


if __name__ == "__main__":
  unittest.main(verbosity=2)
