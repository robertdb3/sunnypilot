"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# Reads the offline OSM data that mapd already downloads for speed limits, and turns the
# slice around the car into flat arrays the onroad map panel can project and draw.
#
# Layout on disk, confirmed against real downloads:
#   {mapd_root}/offline/<groupLat>/<groupLon>/<minLat>_<minLon>_<maxLat>_<maxLon>
# where cells are 0.25 degrees and groups are 2 degrees (64 cells per group directory).
# Both are floored, which matters for the negative longitudes across the US.
#
# Cost, measured on the densest cell in the US data (9.3MB, 39913 ways, NW DC):
#   unpack                                    5 ms
#   extracting every way's nodes            326 ms
#   bbox reject first, extract survivors     53 ms
# Way carries its own minLat/maxLat/minLon/maxLon, so rejecting on those never touches the
# node list. That is the whole reason this is cheap enough to do without a preprocessing
# cache. Keep the bbox test before any access to w.nodes.

import math
import os
import threading

import capnp
import numpy as np

from openpilot.common.hardware.hw import Paths

capnp.remove_import_hook()
offline_capnp = capnp.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "offline.capnp"))

CELL_DEG = 0.25
GROUP_DEG = 2
EARTH_R = 6371000.0

# how far around the car we keep geometry, and how far it may drift before we rebuild
DEFAULT_RADIUS_M = 2000.0
REBUILD_DISTANCE_M = 500.0

# lower sorts first when we have to drop ways to stay inside the render budget
CLASS_PRIORITY = {
  "motorway": 0, "trunk": 1, "primary": 2, "motorwayLink": 3, "trunkLink": 3,
  "secondary": 4, "primaryLink": 5, "tertiary": 6, "secondaryLink": 7,
  "tertiaryLink": 8, "unclassified": 9, "residential": 10, "livingStreet": 11,
  "unknown": 12,
}
MAX_PRIORITY = max(CLASS_PRIORITY.values())


def cell_origin(lat: float, lon: float) -> tuple[float, float]:
  """Bottom-left corner of the 0.25 degree cell containing this point."""
  return math.floor(lat / CELL_DEG) * CELL_DEG, math.floor(lon / CELL_DEG) * CELL_DEG


def group_origin(lat: float, lon: float) -> tuple[int, int]:
  """Directory pair for the 2 degree group containing this point."""
  return int(math.floor(lat / GROUP_DEG) * GROUP_DEG), int(math.floor(lon / GROUP_DEG) * GROUP_DEG)


def cell_path(lat: float, lon: float, root: str | None = None) -> str:
  """Absolute path of the offline file covering this point. Existence is not checked."""
  base = root if root is not None else Paths.mapd_root()
  min_lat, min_lon = cell_origin(lat, lon)
  g_lat, g_lon = group_origin(lat, lon)
  name = f"{min_lat:.6f}_{min_lon:.6f}_{min_lat + CELL_DEG:.6f}_{min_lon + CELL_DEG:.6f}"
  return os.path.join(base, "offline", str(g_lat), str(g_lon), name)


class MapSlice:
  """Ways near a point, flattened so projection is a single vectorised operation.

  points is (N, 2) of lat/lon; starts/counts index into it per way; classes holds the
  render priority so the panel can style and cull without going back to capnp.
  """

  def __init__(self, lat: float, lon: float, points: np.ndarray, starts: np.ndarray,
               counts: np.ndarray, classes: np.ndarray):
    self.lat = lat
    self.lon = lon
    self.points = points
    self.starts = starts
    self.counts = counts
    self.classes = classes

  @property
  def num_ways(self) -> int:
    return len(self.starts)

  @staticmethod
  def empty(lat: float = 0.0, lon: float = 0.0) -> "MapSlice":
    return MapSlice(lat, lon, np.zeros((0, 2)), np.zeros(0, dtype=np.int32),
                    np.zeros(0, dtype=np.int32), np.zeros(0, dtype=np.uint8))


def _cells_to_load(lat: float, lon: float, radius_m: float) -> list[tuple[float, float]]:
  """The cell under the car, plus neighbours when the radius crosses a cell edge."""
  d_lat = radius_m / EARTH_R * 180.0 / math.pi
  d_lon = d_lat / max(math.cos(math.radians(lat)), 1e-6)

  cells = set()
  for la in (lat - d_lat, lat, lat + d_lat):
    for lo in (lon - d_lon, lon, lon + d_lon):
      cells.add(cell_origin(la, lo))
  return sorted(cells)


def load_slice(lat: float, lon: float, radius_m: float = DEFAULT_RADIUS_M,
               root: str | None = None) -> MapSlice:
  """Read every cell the radius touches and flatten the ways that fall inside it."""
  d_lat = radius_m / EARTH_R * 180.0 / math.pi
  d_lon = d_lat / max(math.cos(math.radians(lat)), 1e-6)
  lat_lo, lat_hi = lat - d_lat, lat + d_lat
  lon_lo, lon_hi = lon - d_lon, lon + d_lon

  chunks: list[np.ndarray] = []
  starts: list[int] = []
  counts: list[int] = []
  classes: list[int] = []
  offset = 0

  for c_lat, c_lon in _cells_to_load(lat, lon, radius_m):
    # cell_path takes any point inside the cell; the origin plus a nudge stays inside
    path = cell_path(c_lat + CELL_DEG / 2, c_lon + CELL_DEG / 2, root)
    try:
      with open(path, "rb") as f:
        data = f.read()
    except OSError:
      continue  # region not downloaded, or ocean

    try:
      # traversal limit has to be lifted or large cells raise part way through
      msg = offline_capnp.Offline.from_bytes_packed(data, traversal_limit_in_words=2**62)
      ways = msg.ways
    except Exception:
      continue

    for w in ways:
      # bbox reject BEFORE touching w.nodes -- this is the 6x
      if w.maxLat < lat_lo or w.minLat > lat_hi or w.maxLon < lon_lo or w.minLon > lon_hi:
        continue
      nodes = w.nodes
      n = len(nodes)
      if n < 2:
        continue
      arr = np.empty((n, 2))
      for i, node in enumerate(nodes):
        arr[i, 0] = node.latitude
        arr[i, 1] = node.longitude
      chunks.append(arr)
      starts.append(offset)
      counts.append(n)
      classes.append(CLASS_PRIORITY.get(str(w.highwayClass), MAX_PRIORITY))
      offset += n

  if not chunks:
    return MapSlice.empty(lat, lon)

  return MapSlice(lat, lon, np.concatenate(chunks),
                  np.array(starts, dtype=np.int32),
                  np.array(counts, dtype=np.int32),
                  np.array(classes, dtype=np.uint8))


class OfflineMapLoader:
  """Loads slices on a worker thread so the render loop never blocks on disk or capnp."""

  def __init__(self, radius_m: float = DEFAULT_RADIUS_M, root: str | None = None):
    self._radius_m = radius_m
    self._root = root
    self._lock = threading.Lock()
    self._wake = threading.Event()
    self._requested: tuple[float, float] | None = None
    self._slice: MapSlice | None = None
    self._loading = False
    self._thread: threading.Thread | None = None

  def start(self) -> None:
    if self._thread is not None:
      return
    self._thread = threading.Thread(target=self._run, daemon=True, name="offline_map")
    self._thread.start()

  def update_position(self, lat: float, lon: float) -> None:
    """Ask for a rebuild if the car has drifted far enough from the current slice."""
    with self._lock:
      if self._loading:
        return
      cur = self._slice
      if cur is not None and _distance_m(cur.lat, cur.lon, lat, lon) < REBUILD_DISTANCE_M:
        return
      self._requested = (lat, lon)
      self._loading = True
    self._wake.set()

  def get_slice(self) -> MapSlice | None:
    with self._lock:
      return self._slice

  def _run(self) -> None:
    while True:
      self._wake.wait()
      self._wake.clear()
      with self._lock:
        req = self._requested
        self._requested = None
      if req is None:
        with self._lock:
          self._loading = False
        continue
      try:
        result = load_slice(req[0], req[1], self._radius_m, self._root)
      except Exception:
        result = MapSlice.empty(req[0], req[1])
      with self._lock:
        self._slice = result
        self._loading = False


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
  """Equirectangular approximation, plenty accurate for the sub-km checks here."""
  x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
  y = math.radians(lat2 - lat1)
  return math.hypot(x, y) * EARTH_R
