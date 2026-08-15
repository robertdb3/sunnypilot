"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

import numpy as np

# openpilot's calibrated/model frame is x forward, y RIGHT, z DOWN.
# raylib's world is x right, y up, right-handed, so forward is -z.
# Getting this backwards produces a scene that looks plausible but is mirrored, so it is done in
# exactly one place and unit tested.


def car_to_world(pts: np.ndarray) -> np.ndarray:
  """(N,3) points in car frame -> (N,3) in raylib world space."""
  out = np.empty_like(pts, dtype=np.float32)
  out[:, 0] = pts[:, 1]   # right   =  right
  out[:, 1] = -pts[:, 2]  # up      = -down
  out[:, 2] = -pts[:, 0]  # -into screen = forward
  return out


def trim(x: np.ndarray, y: np.ndarray, z: np.ndarray, max_dist: float) -> tuple[np.ndarray, ...]:
  """Drop points beyond max_dist ahead, and any non-finite rows the model may emit."""
  n = min(len(x), len(y), len(z))
  if n < 2:
    empty = np.zeros(0, dtype=np.float32)
    return empty, empty, empty

  x, y, z = np.asarray(x[:n]), np.asarray(y[:n]), np.asarray(z[:n])
  keep = np.isfinite(x) & np.isfinite(y) & np.isfinite(z) & (x <= max_dist) & (x >= -2.0)
  return x[keep], y[keep], z[keep]


def extend_back(x: np.ndarray, y: np.ndarray, z: np.ndarray, dist: float = 30.0) -> tuple[np.ndarray, ...]:
  """Extrapolate a polyline backwards past the car.

  modelV2 only describes the road ahead, so without this the road surface starts at the ego car
  and the chase camera looks out over bare ground in the near field.
  """
  if len(x) < 2:
    return x, y, z

  dx = float(x[1] - x[0])
  dy = float(y[1] - y[0])
  if abs(dx) < 1e-3:
    return x, y, z

  steps = np.arange(dist, 0.0, -5.0, dtype=np.float32)
  bx = x[0] - steps
  by = y[0] - steps * (dy / dx)
  bz = np.full_like(steps, z[0])
  return (np.concatenate([bx, x]), np.concatenate([by, y]), np.concatenate([bz, z]))


def ribbon(x: np.ndarray, y: np.ndarray, z: np.ndarray, width: float, z_lift: float = 0.0) -> np.ndarray:
  """Flat ribbon of the given width centred on a car-frame polyline.

  Returns (T,3,3) world-space triangle vertices, ready to hand to draw_triangle_3d.
  """
  if len(x) < 2:
    return np.zeros((0, 3, 3), dtype=np.float32)

  # Model z points down, so a positive visual lift subtracts from z.
  pts = np.stack([x, y, z - z_lift], axis=1).astype(np.float32)

  # perpendicular in the ground plane, from forward differences
  d = np.gradient(pts[:, :2], axis=0)
  norm = np.linalg.norm(d, axis=1, keepdims=True)
  norm[norm < 1e-6] = 1e-6
  d = d / norm
  # left normal of (dx, dy) is (-dy, dx)
  n = np.stack([-d[:, 1], d[:, 0]], axis=1) * (width * 0.5)

  left = pts.copy()
  right = pts.copy()
  left[:, 0] += n[:, 0]
  left[:, 1] += n[:, 1]
  right[:, 0] -= n[:, 0]
  right[:, 1] -= n[:, 1]

  lw = car_to_world(left)
  rw = car_to_world(right)

  # two triangles per segment, wound so the visible face points up (+y)
  a, b = lw[:-1], rw[:-1]
  c, d2 = lw[1:], rw[1:]
  tris = np.concatenate([
    np.stack([a, c, b], axis=1),
    np.stack([b, c, d2], axis=1),
  ], axis=0)
  return tris.astype(np.float32)


def ribbon_varying(x: np.ndarray, y: np.ndarray, z: np.ndarray, widths: np.ndarray,
                   z_lift: float = 0.0) -> np.ndarray:
  """Ribbon whose half-width varies per point, so the path can taper with distance."""
  if len(x) < 2:
    return np.zeros((0, 3, 3), dtype=np.float32)

  pts = np.stack([x, y, z - z_lift], axis=1).astype(np.float32)
  d = np.gradient(pts[:, :2], axis=0)
  norm = np.linalg.norm(d, axis=1, keepdims=True)
  norm[norm < 1e-6] = 1e-6
  d = d / norm
  half = np.asarray(widths, dtype=np.float32).reshape(-1, 1) * 0.5
  n = np.stack([-d[:, 1], d[:, 0]], axis=1) * half

  left, right = pts.copy(), pts.copy()
  left[:, 0] += n[:, 0]
  left[:, 1] += n[:, 1]
  right[:, 0] -= n[:, 0]
  right[:, 1] -= n[:, 1]

  lw, rw = car_to_world(left), car_to_world(right)
  a, b, c, d2 = lw[:-1], rw[:-1], lw[1:], rw[1:]
  # interleaved so triangle 2i and 2i+1 both belong to segment i, letting callers colour per segment
  tris = np.empty((2 * (len(pts) - 1), 3, 3), dtype=np.float32)
  tris[0::2] = np.stack([a, c, b], axis=1)
  tris[1::2] = np.stack([b, c, d2], axis=1)
  return tris


def dashes(x: np.ndarray, y: np.ndarray, z: np.ndarray, width: float, z_lift: float = 0.0,
           dash: float = 3.0, gap: float = 6.0) -> np.ndarray:
  """Broken lane marking. Real dividers are painted, not continuous, and the eye reads the
  difference immediately — it also gives a sense of speed as the scene scrolls."""
  if len(x) < 2:
    return np.zeros((0, 3, 3), dtype=np.float32)

  seg = np.hypot(np.diff(x), np.diff(y))
  s = np.concatenate([[0.0], np.cumsum(seg)])
  period = dash + gap

  out = []
  start = 0.0
  while start < s[-1]:
    end = min(start + dash, s[-1])
    lo, hi = np.searchsorted(s, start), np.searchsorted(s, end)
    idx = np.arange(max(lo - 1, 0), min(hi + 1, len(s)))
    if len(idx) >= 2:
      out.append(ribbon(x[idx], y[idx], z[idx], width, z_lift))
    start += period

  return np.concatenate(out, axis=0) if out else np.zeros((0, 3, 3), dtype=np.float32)


def quad(corners_car: np.ndarray) -> np.ndarray:
  """4 car-frame corners (in order) -> (2,3,3) world triangles."""
  w = car_to_world(np.asarray(corners_car, dtype=np.float32))
  return np.stack([
    np.stack([w[0], w[2], w[1]]),
    np.stack([w[0], w[3], w[2]]),
  ]).astype(np.float32)


def lead_positions(leads, max_count: int = 2, min_prob: float = 0.5) -> list[tuple[float, float, float, float]]:
  """Extract (x, y, prob, v) for the most probable leads from modelV2.leadsV3.

  Speed travels with the lead rather than in a parallel list: the results are sorted by distance,
  so a separate array indexed against the original order would pair speeds to the wrong cars.

  Deliberately reads leadsV3 and not radarState: radarState leads are gated on
  openpilotLongitudinalControl and a radar, and this car (Subaru, radarUnavailable) has neither,
  so radarState would render nothing.
  """
  out: list[tuple[float, float, float, float]] = []
  for lead in leads or []:
    prob = float(getattr(lead, "prob", 0.0))
    xs, ys = getattr(lead, "x", None), getattr(lead, "y", None)
    if prob < min_prob or not xs or not ys:
      continue
    vs = getattr(lead, "v", None)
    out.append((float(xs[0]), float(ys[0]), prob, float(vs[0]) if vs else 0.0))

  out.sort(key=lambda t: t[0])
  return out[:max_count]
