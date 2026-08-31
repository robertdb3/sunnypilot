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


# ---- the render grid --------------------------------------------------------------------------
# Everything the scene draws is resampled onto one of these fixed distance grids before it is
# filtered or turned into triangles. Two reasons, and the first is the important one:
#
#   1. Temporal filtering needs a stable meaning per index. modelV2 publishes laneLines and
#      roadEdges on ModelConstants.X_IDXS (a constant 33-value distance grid), so index k already
#      means "at X_IDXS[k] metres ahead" -- but `position` is published on T_IDXS, where x is
#      speed-dependent and slides frame to frame. Resampling both onto one distance grid makes
#      per-index smoothing physically meaningful for all of them: "lateral offset at d metres".
#   2. X_IDXS is quadratic, so it spends 4 of its first 5 samples inside the ego car's own
#      footprint (0 -> 3 m) and then strides 15 m at a time out at the horizon. A geometric grid
#      is much closer to uniform in screen space for the same number of triangles.
#
# Resampling is linear and both grids are fixed, so filtering commutes with resampling -- filter
# after, on 19 values instead of 33.
GRID_S = np.array([-30.0, -18.0, -10.0, -4.0, 0.0, 3.0, 6.5, 10.5, 15.0, 20.0,
                   26.0, 33.0, 41.0, 50.0, 61.0, 74.0, 89.0, 104.0, 120.0], dtype=np.float32)

# the path is a near-term intention; drawing it to the horizon is noise
PATH_GRID_S = np.array([0.0, 3.0, 6.5, 10.5, 15.0, 20.0, 26.0, 33.0, 41.0, 50.0, 61.0, 70.0],
                       dtype=np.float32)

# Dashes need even arc spacing, not the display grid. 1.5 m divides the 3 m dash exactly, so dash
# boundaries always land on a sample and the triangle count cannot wobble between frames.
DASH_STEP = 1.5
DASH_MAX_S = 42.0
DASH_S = np.arange(-30.0, DASH_MAX_S + DASH_STEP, DASH_STEP, dtype=np.float32)


def resample_to_grid(x: np.ndarray, y: np.ndarray, z: np.ndarray,
                     grid: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
  """Resample a car-frame polyline onto `grid`, returning (y_g, z_g, n_valid).

  Always returns full-length arrays so the per-index smoother sees a stable shape. `n_valid` is
  how many leading grid nodes are backed by real model data; the caller truncates to that when
  drawing. Grid nodes beyond the model's reach must NOT be flat-lined -- at 6 m/s the path only
  extends ~60 m, and np.interp would happily clamp it into a straight bar out to the horizon.

  Nodes behind the model's first sample are linearly extrapolated, which is what `extend_back`
  used to do: modelV2 only describes the road ahead, and without it the chase camera looks out
  over bare ground in the near field.
  """
  n = min(len(x), len(y), len(z))
  empty = np.zeros(len(grid), dtype=np.float32)
  if n < 2:
    return empty, empty.copy(), 0

  x, y, z = np.asarray(x[:n], dtype=np.float32), np.asarray(y[:n], dtype=np.float32), np.asarray(z[:n], dtype=np.float32)
  good = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
  if good.sum() < 2:
    return empty, empty.copy(), 0
  x, y, z = x[good], y[good], z[good]

  # np.interp needs increasing xp; the model is already sorted but a stale/foreign message may not be
  if np.any(np.diff(x) <= 0):
    order = np.argsort(x, kind="stable")
    x, y, z = x[order], y[order], z[order]
    keep = np.concatenate([[True], np.diff(x) > 0])
    x, y, z = x[keep], y[keep], z[keep]
    if len(x) < 2:
      return empty, empty.copy(), 0

  y_g = np.interp(grid, x, y).astype(np.float32)
  z_g = np.interp(grid, x, z).astype(np.float32)

  # linear extrapolation behind the first sample instead of np.interp's clamp
  behind = grid < x[0]
  if behind.any():
    slope_y = (y[1] - y[0]) / (x[1] - x[0])
    slope_z = (z[1] - z[0]) / (x[1] - x[0])
    d = (grid[behind] - x[0]).astype(np.float32)
    y_g[behind] = y[0] + slope_y * d
    z_g[behind] = z[0] + slope_z * d

  n_valid = int(np.count_nonzero(grid <= x[-1]))
  return y_g, z_g, n_valid


def smoothstep(t: np.ndarray) -> np.ndarray:
  """3t^2-2t^3 on [0,1]. Used for every fade so nothing has a visible kink."""
  t = np.clip(t, 0.0, 1.0)
  return (t * t * (3.0 - 2.0 * t)).astype(np.float32)


def distance_fade(grid: np.ndarray, start: float, end: float) -> np.ndarray:
  """1.0 out to `start`, smoothly to 0.0 by `end`.

  This is the main tool for being honest about the model rather than decorative: the far field is
  where lane predictions are noisiest, so dissolving it there stops the least trustworthy part of
  the scene from being the most visually prominent.
  """
  return smoothstep((end - grid) / max(end - start, 1e-3))


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


def dashes_on_grid(y: np.ndarray, z: np.ndarray, widths: np.ndarray, z_lift: float,
                   phase: float = 0.0, dash: float = 3.0, gap: float = 6.0,
                   n_valid: int | None = None) -> np.ndarray:
  """Broken lane marking, built in one pass on DASH_S.

  Replaces the old `dashes()`, which called `ribbon()` once per dash -- roughly 17 numpy geometry
  builds per lane line, 68 per frame across four lines, each doing a gradient and a norm on a
  2-4 point array. That is numpy overhead with essentially no numpy work in it.

  Here the whole line becomes a single ribbon and the dashes are a boolean mask over its segments,
  so the cost is one build regardless of how many dashes are visible.

  `phase` scrolls the pattern. The old implementation anchored dashes to the polyline origin,
  which is anchored to the car -- so they never moved, and the road looked static at 70 mph.
  """
  x = DASH_S
  if n_valid is not None:
    n = max(int(n_valid), 0)
    if n < 2:
      return np.zeros((0, 3, 3), dtype=np.float32)
    x, y, z, widths = x[:n], y[:n], z[:n], widths[:n]
  if len(x) < 2:
    return np.zeros((0, 3, 3), dtype=np.float32)

  tris = ribbon_varying(x, y, z, widths, z_lift)

  # keep segments whose midpoint falls inside a painted stretch
  mid = 0.5 * (x[:-1] + x[1:])
  keep = ((mid + phase) % (dash + gap)) < dash
  return tris[np.repeat(keep, 2)]


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
