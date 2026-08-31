"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Temporal smoothing for the 3D scene.

Deliberately free of pyray, cereal and ui_state imports: that is what lets the offline harness
measure jitter and the unit tests assert the filter's behaviour without a GPU or a device.

WHY PER-INDEX FILTERING IS CORRECT HERE

Everything is resampled onto a fixed distance grid (geometry.GRID_S) first, so index k always
means "the model's estimate at GRID_S[k] metres ahead". Filtering across frames therefore answers
"what is the lateral offset of this line 75 m out?" -- a quantity that is genuinely constant on a
straight and changes slowly on a curve.

It does NOT smear with ego motion, because the grid is ego-relative and re-anchored by the model
every frame. Filtering a world-fixed polyline would smear; this does not.

WHAT IS DELIBERATELY NOT SMOOTHED

Anything the driver acts on. `valid` (the model going invalid must show immediately), the planned
acceleration that colours the path (lagging it would misrepresent planned braking), and the lead
distance readout (which uses hysteretic rounding instead -- flicker-free with zero latency).
"""

import numpy as np

from openpilot.selfdrive.ui.sunnypilot.onroad.scene3d.geometry import GRID_S, PATH_GRID_S

# modelV2's period. NOT the UI frame time: the UI runs at 20 FPS on tizi but 60 on tici, and
# filtering in the draw path would give the same code three different time constants depending on
# hardware. Smoothing runs once per model message, where dt is this constant.
DT = 0.05

# Near points must stay fast: that is where the car actually is, and lag there reads as the render
# sliding, which is worse than jitter. Far points are noisiest and are what we most want to calm.
#
# RC_NEAR is deliberately small. Model noise at the bumper is only ~3 cm, so there is almost
# nothing to gain by smoothing it, and every millisecond of near-field lag makes the geometry feel
# detached from the car. The measured step response at 3 m is ~64% tracked within two frames.
RC_NEAR = 0.06
RC_FAR = 0.55
SMOOTH_REF_DIST = 90.0

# Probabilities are display-only and have no safety coupling, so they can be smoothed harder;
# prob noise is a large share of the flicker as lines fade in and out.
PROB_RC = 0.35

# A real lane change re-anchors which physical line laneLines[1] tracks, stepping a full lane
# width (~3.7 m). Per-frame model noise inside 50 m is well under 0.15 m. 0.75 m sits in that gap,
# so a genuine manoeuvre snaps on the next message and is never lagged, while noise never snaps.
# Beyond 50 m the model's own noise can exceed the threshold, so it is not tested there.
SNAP_DEV = 0.75
SNAP_TEST_DIST = 50.0


def alphas_for(grid: np.ndarray, rc_near: float = RC_NEAR, rc_far: float = RC_FAR) -> np.ndarray:
  """Per-node EMA coefficient, smoothing harder with distance.

  rc is a time constant in seconds, matching FirstOrderFilter's convention, so these constants
  stay meaningful regardless of frame rate.
  """
  t = np.clip(grid / SMOOTH_REF_DIST, 0.0, 1.0)
  rc = rc_near + (rc_far - rc_near) * t
  return (DT / (rc + DT)).astype(np.float32)


ALPHAS = alphas_for(GRID_S)
PATH_ALPHAS = alphas_for(PATH_GRID_S)

SNAP_MASK = GRID_S <= SNAP_TEST_DIST
PATH_SNAP_MASK = PATH_GRID_S <= SNAP_TEST_DIST

PROB_ALPHA = np.float32(DT / (PROB_RC + DT))


class GridSmoother:
  """Per-index EMA over a polyline sampled on a fixed grid.

  Staleness and validity transitions are the caller's business: the renderer knows when modelV2
  went invalid or when a new route started, and calls reset(). This class handles the cases it can
  see for itself -- first update, shape change, non-finite input, and a genuine large manoeuvre.
  """

  def __init__(self, alphas: np.ndarray, snap_dev: float = SNAP_DEV,
               snap_mask: np.ndarray | None = None):
    self._alphas = np.asarray(alphas, dtype=np.float32)
    self._snap_dev = float(snap_dev)
    self._snap_mask = snap_mask if snap_mask is not None else np.ones(len(self._alphas), dtype=bool)
    self._has_snap_region = bool(np.any(self._snap_mask))
    self._x: np.ndarray | None = None

  @property
  def initialised(self) -> bool:
    return self._x is not None

  def reset(self) -> None:
    """Next update() snaps instead of gliding."""
    self._x = None

  def update(self, y: np.ndarray) -> np.ndarray:
    """Filter one frame. Returns a copy, so callers can never mutate the filter's state."""
    y = np.asarray(y, dtype=np.float32)

    # A bad sample must not poison the state; hold the last good output instead.
    if not np.all(np.isfinite(y)):
      return self._x.copy() if self._x is not None else np.zeros_like(y)

    # snap: uninitialised, or the shape changed under us (a stale or foreign model message)
    if self._x is None or self._x.shape != y.shape:
      self._x = y.copy()
      return self._x.copy()

    # snap: a real manoeuvre, judged only where the model is trustworthy enough to judge it
    if self._has_snap_region and self._snap_mask.shape == y.shape:
      if float(np.max(np.abs(y - self._x)[self._snap_mask])) > self._snap_dev:
        self._x = y.copy()
        return self._x.copy()

    self._x += self._alphas * (y - self._x)
    return self._x.copy()


class ScalarSmoother:
  """EMA for a small fixed-length vector with no notion of distance, e.g. lane line probabilities."""

  def __init__(self, alpha: float = PROB_ALPHA):
    self._alpha = np.float32(alpha)
    self._x: np.ndarray | None = None

  def reset(self) -> None:
    self._x = None

  def update(self, y: np.ndarray) -> np.ndarray:
    y = np.asarray(y, dtype=np.float32)
    if not np.all(np.isfinite(y)):
      return self._x.copy() if self._x is not None else np.zeros_like(y)
    if self._x is None or self._x.shape != y.shape:
      self._x = y.copy()
      return self._x.copy()
    self._x += self._alpha * (y - self._x)
    return self._x.copy()


def hysteretic_round(value: float, previous: int | None, margin: float = 0.6) -> int:
  """Round to an integer that only changes once the value is clearly past the boundary.

  For the lead distance readout. Filtering that number would put lag into something the driver
  acts on; this kills the flicker with zero latency instead, because the displayed value is always
  a rounding of the current raw measurement.
  """
  if previous is None:
    return int(round(value))
  if abs(value - previous) >= margin:
    return int(round(value))
  return previous
