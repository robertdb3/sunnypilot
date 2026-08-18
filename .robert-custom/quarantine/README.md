# Quarantined patches

Patches kept in the tree but **deliberately not applied** by `apply_candidate.sh`, and not scanned
by `verify_candidate.py`. A patch lands here when it has been shown to break the device and is
waiting on a real fix plus on-device validation — never as a way to park work that was merely
untested.

`verify_candidate.py` requires every file named in a patch under `patches/` to appear in the
candidate diff, so an unapplied patch cannot simply stay there; that check is what forces this
directory to exist rather than a commented-out loop entry.

## `0011-refine-3d-scene.patch`

Quarantined 2026-08-18 after it crash-looped the UI on the device within minutes of install. See
symptom 14 in [`../notes/device-journey-runbook.md`](../notes/device-journey-runbook.md).

The scene work itself is sound and measured. The defect is narrow: `renderer.py` slices the
`modelV2` lists —

```python
for i, line in enumerate(model.laneLines[:4])
for i, e in enumerate(model.roadEdges[:2])
```

— and a Cap'n Proto `_DynamicListReader` supports integer indexing but **not** slicing, so the real
message raises `TypeError: an integer is required` on the first onroad frame. The offline harness
feeds synthetic numpy-shaped data and cannot reproduce it.

Before this comes back:

1. fix both slice sites (`list(...)` first, or index explicitly);
2. add a regression test using a reader stub that rejects slicing, and confirm it fails against the
   current patch text;
3. install offroad and confirm the UI survives an onroad transition with Scene3D **on**, with the
   CPU baseline captured first.
