"""Turn a full-detail Outback model into the small shaded mesh the 3D scene ships.

Run once when the source model changes; the output is committed as part of patch 0011.

    python3 build_car_mesh.py --src <model.obj> --out <outback.gltf> [--faces 2500] [--flip]

WHY glTF AND NOT OBJ

raylib has no lighting without a custom shader, and patch 0003 rejected shaders because of GLSL
version differences between the desktop and comma backends. So the light has to be baked into the
mesh's vertex colours -- and raylib's OBJ loader ignores the `v x y z r g b` vertex-colour
extension (verified: it allocates the buffer and fills it with white). Its glTF loader reads
COLOR_0 correctly, and a .gltf with its buffers inlined as base64 is a single self-contained TEXT
file, which is what lets it live in a patch at all.

WHAT GETS BAKED

  * a base colour per source material, so glass, tyres, cladding and paint stay distinguishable
  * a diffuse term from each vertex normal against a key light placed above and BEHIND the
    viewer, because the chase camera only ever sees the rear of a car

draw_model_ex's tint multiplies over the result, which is how the day/night palette shifts the
whole car without a second asset.
"""
import argparse
import base64
import json
import os
import shutil
import tempfile

import numpy as np
import trimesh

# Base colour per source material. Names come from the 2022 Outback model's .mtl; anything
# unmatched falls back to the body colour, which is the safe default for sheet metal.
MATERIAL_COLORS = {
  # Deliberately few, bold colours. The car is about 110 px tall on screen at the chase distance,
  # and every extra distinct material becomes speckle once clustering has fused shells and the
  # colour lookup starts voting between neighbours. Bold flat regions read at that size; subtle
  # trim distinctions do not survive it.
  #
  # 2018 Wilderness Green Metallic (K4X). The source model is Autumn Green; this is the override
  # that makes it the user's actual car.
  "Autumn_Green_Metallic": (112, 132, 100),
  "lambert1": (112, 132, 100),
  # glass
  "Windows": (44, 54, 68),
  # rear lamps: the strongest "this is my car" cue from behind
  "BrakeLightsGlass": (168, 40, 34),
  "RedMain": (168, 40, 34),
  "Reflectors": (150, 52, 40),
  "OrangeSide": (170, 96, 44),
  # black lower cladding and window surrounds, the Outback's second signature after the rails
  "WindowsTrims": (42, 44, 48),
  "BlackChrome": (42, 44, 48),
  "BlackTrims1": (46, 48, 52),
  "Black1": (42, 44, 46),
  "ChromeTrims": (132, 138, 146),
  "Mirrors": (96, 112, 88),
  # wheels
  "Tires": (20, 21, 24),
  "Rims1": (140, 146, 152),
  "Rims2": (120, 126, 132),
}
DEFAULT_COLOR = (112, 132, 100)

# Geometry the chase camera can never see, dropped before decimation. This is close to a fifth of
# the source model, and every triangle spent here is one not spent on the silhouette that is
# actually on screen. The front lamps and grille qualify for the same reason the procedural car
# omits its front face: nothing in this scene is ever viewed from in front.
INVISIBLE = (
  "Interior", "UnderCarrier", "Calipers", "BrakeRotors1", "BrakeRotors2",
  "HeadLightsGlass", "DRLs", "Grille1", "Grille2", "Badge1", "License_Plate",
)

# Key light: above and behind the viewer, in scene world axes (x right, y up, +z toward viewer).
# Patch 0003's per-face bake had the rear as its DARKEST face while the chase camera stares
# straight at it, which is the real reason a dark green rendered near-black.
LIGHT_DIR = np.array([0.28, 0.78, 0.56])
AMBIENT, DIFFUSE = 0.68, 0.32

TARGET_LENGTH = 4.82   # dimensions.MESH_TARGET_L; a 2018 Outback is 4.816 m


def material_color(name: str) -> tuple:
  for key, col in MATERIAL_COLORS.items():
    if key.lower() in name.lower():
      return col
  return DEFAULT_COLOR


def load_coloured(src: str) -> trimesh.Trimesh:
  """Concatenate every material group into one mesh carrying per-vertex base colours."""
  scene = trimesh.load(src, process=False, force="scene")
  parts = []
  dropped = 0
  for name, geom in scene.geometry.items():
    if not isinstance(geom, trimesh.Trimesh) or len(geom.faces) == 0:
      continue
    if any(k.lower() in name.lower() for k in INVISIBLE):
      dropped += len(geom.faces)
      continue
    col = material_color(name)
    g = geom.copy()
    g.visual = trimesh.visual.ColorVisuals(
      mesh=g, vertex_colors=np.tile(np.array([*col, 255], np.uint8), (len(g.vertices), 1)))
    parts.append(g)
  if not parts:
    raise SystemExit("no geometry found in source")
  print(f"  {len(parts)} visible groups, {sum(len(p.faces) for p in parts)} faces "
        f"({dropped} dropped as never-visible)")
  return trimesh.util.concatenate(parts)


def normalise(mesh: trimesh.Trimesh, flip: bool) -> trimesh.Trimesh:
  """Scale to a real Outback, orient nose to -z, and sit it on y=0.

  The source is a SketchUp export in inches with Y up and the long axis on Z, which is already
  the scene's handedness -- only the scale and the nose direction need deciding.
  """
  ext = mesh.extents
  long_axis = int(np.argmax(ext))
  scale = TARGET_LENGTH / ext[long_axis]
  mesh.apply_scale(scale)
  print(f"  scaled by {scale:.5f} -> extents {np.round(mesh.extents, 3)}")

  if flip:
    m = np.eye(4)
    m[0, 0] = -1.0
    m[2, 2] = -1.0     # 180 deg about y keeps the mesh right-handed
    mesh.apply_transform(m)

  b = mesh.bounds
  mesh.apply_translation([-(b[0][0] + b[1][0]) * 0.5, -b[0][1], -(b[0][2] + b[1][2]) * 0.5])
  return mesh


def cluster(mesh: trimesh.Trimesh, cell: float) -> trimesh.Trimesh:
  """Vertex-clustering decimation: snap vertices to a grid and collapse each cell to one point.

  This exists because quadric decimation alone cannot get near a small budget on this model. A
  SketchUp export is hundreds of separate open shells -- every trim strip, lamp lens and panel is
  its own surface with its own boundary -- and quadric collapse refuses to cross a boundary edge,
  so it stalls around 24k faces no matter how aggressive it is set.

  Clustering ignores connectivity entirely: two vertices in the same cell become one vertex even
  if they belonged to different shells. That fuses the shells into a single connected surface,
  which both removes detail directly and, more importantly, leaves quadric decimation something it
  can actually keep collapsing afterwards.
  """
  v = np.asarray(mesh.vertices)
  keys = np.floor(v / cell).astype(np.int64)
  _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)

  # representative = mean of the cell's vertices, which keeps surfaces where they were rather
  # than snapping them onto grid corners
  n_cells = len(counts)
  acc = np.zeros((n_cells, 3), dtype=np.float64)
  np.add.at(acc, inverse, v)
  reps = acc / counts[:, None]

  faces = inverse[np.asarray(mesh.faces)]
  ok = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
  out = trimesh.Trimesh(vertices=reps, faces=faces[ok], process=False)
  out.remove_unreferenced_vertices()
  print(f"  clustered @{cell * 1000:.0f}mm: {len(mesh.faces)} -> {len(out.faces)} faces")
  return out


def decimate(mesh: trimesh.Trimesh, target_faces: int, cell: float) -> trimesh.Trimesh:
  """Weld, cluster, then quadric-collapse to the budget."""
  work = trimesh.Trimesh(vertices=mesh.vertices.copy(), faces=mesh.faces.copy(), process=False)
  before = len(work.vertices)
  work.merge_vertices()
  work.update_faces(work.nondegenerate_faces())
  work.remove_unreferenced_vertices()
  print(f"  welded {before} -> {len(work.vertices)} vertices, {len(work.faces)} faces")

  work = cluster(work, cell)

  import fast_simplification
  if len(work.faces) > target_faces:
    reduction = max(0.0, 1.0 - target_faces / len(work.faces))
    v, f = fast_simplification.simplify(
      work.vertices.astype(np.float32), work.faces.astype(np.int32), reduction, agg=9)
    work = trimesh.Trimesh(vertices=v, faces=f, process=False)
    print(f"  quadric -> {len(work.faces)} faces")
  return work


def transfer_colors(src: trimesh.Trimesh, dst: trimesh.Trimesh, k: int = 12) -> np.ndarray:
  """Majority colour among the k nearest source vertices.

  Decimation carries no attributes, so colours have to be re-looked-up. A plain nearest-vertex
  lookup speckles badly here: clustering deliberately fused vertices across material boundaries,
  so a body-panel vertex often lands nearest a chrome trim vertex and picks up a bright grey dot.
  Voting over a neighbourhood makes the dominant material win, which is what the eye expects on a
  large smooth panel.
  """
  from scipy.spatial import cKDTree
  cols = np.asarray(src.visual.vertex_colors)[:, :3]
  tree = cKDTree(src.vertices)
  _, idx = tree.query(dst.vertices, k=min(k, len(src.vertices)))
  idx = np.atleast_2d(idx)

  # pack RGB into one integer so "most common colour" is a single bincount per vertex
  packed = (cols[:, 0].astype(np.int64) << 16) | (cols[:, 1].astype(np.int64) << 8) | cols[:, 2]
  nb = packed[idx]
  out = np.empty((len(dst.vertices), 3), dtype=np.uint8)
  for i, row in enumerate(nb):
    vals, counts = np.unique(row, return_counts=True)
    win = int(vals[np.argmax(counts)])
    out[i] = ((win >> 16) & 255, (win >> 8) & 255, win & 255)
  return out


def bake_shading(mesh: trimesh.Trimesh, base: np.ndarray, smooth_passes: int = 3) -> np.ndarray:
  """Bake a diffuse term into the vertex colours.

  The lambert term is smoothed across mesh neighbours first. Decimation leaves normals noticeably
  irregular, and because there is no shader to average anything at draw time, raw per-vertex
  normals show up directly as blotches on what should be a smooth panel. Averaging over the edge
  graph is the cheap stand-in for the smooth shading a real lighting pass would give.
  """
  n = np.asarray(mesh.vertex_normals, dtype=np.float64)
  ln = np.linalg.norm(n, axis=1, keepdims=True)
  ln[ln < 1e-9] = 1.0
  lam = np.clip((n / ln) @ (LIGHT_DIR / np.linalg.norm(LIGHT_DIR)), 0.0, 1.0)

  edges = np.asarray(mesh.edges_unique)
  if len(edges):
    deg = np.bincount(edges.ravel(), minlength=len(lam)).astype(np.float64)
    deg[deg == 0] = 1.0
    for _ in range(smooth_passes):
      acc = np.zeros_like(lam)
      np.add.at(acc, edges[:, 0], lam[edges[:, 1]])
      np.add.at(acc, edges[:, 1], lam[edges[:, 0]])
      lam = 0.5 * lam + 0.5 * (acc / deg)

  k = (AMBIENT + DIFFUSE * lam)[:, None]
  out = np.clip(base.astype(np.float64) * k, 0, 255).astype(np.uint8)
  return np.concatenate([out, np.full((len(out), 1), 255, np.uint8)], axis=1)


def export_gltf(mesh: trimesh.Trimesh, colors: np.ndarray, out_path: str) -> None:
  """Export, then inline every buffer as a base64 data URI so the result is one text file.

  Normals are deliberately dropped: nothing reads them at runtime (the light is already baked
  into the colours) and they would be a third of the file for no benefit.
  """
  m = trimesh.Trimesh(vertices=mesh.vertices, faces=mesh.faces, process=False)
  m.visual = trimesh.visual.ColorVisuals(mesh=m, vertex_colors=colors)

  tmp = tempfile.mkdtemp()
  try:
    stage = os.path.join(tmp, "car.gltf")
    m.export(stage)
    doc = json.load(open(stage))
    for buf in doc.get("buffers", []):
      uri = buf.get("uri")
      if uri and not uri.startswith("data:"):
        raw = open(os.path.join(tmp, uri), "rb").read()
        buf["uri"] = "data:application/octet-stream;base64," + base64.b64encode(raw).decode()
    doc.pop("images", None)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as fh:
      json.dump(doc, fh, separators=(",", ":"))
  finally:
    shutil.rmtree(tmp, ignore_errors=True)


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--src", required=True)
  ap.add_argument("--out", required=True)
  ap.add_argument("--faces", type=int, default=2500)
  ap.add_argument("--cell", type=float, default=0.035, help="clustering cell size, metres")
  ap.add_argument("--flip", action="store_true", help="rotate 180 deg if the nose points aft")
  args = ap.parse_args()

  print("loading...")
  full = load_coloured(args.src)
  full = normalise(full, args.flip)
  small = decimate(full, args.faces, args.cell)
  base = transfer_colors(full, small)
  colors = bake_shading(small, base)
  export_gltf(small, colors, args.out)

  size = os.path.getsize(args.out)
  print(f"  wrote {args.out}  {len(small.faces)} faces  {size / 1024:.0f} KB")
  print(f"  extents {np.round(small.extents, 3)}  bounds y {small.bounds[0][1]:.3f}..{small.bounds[1][1]:.3f}")


if __name__ == "__main__":
  main()
