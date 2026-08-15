# Offline vector map inset

![medium zoom](../docs/map_preview_medium.png)

Heading-up map in the corner of the driving screen, drawn from the OSM data mapd already
downloads. **No tiles, no API key, no network** — it works wherever your offline region is
downloaded. `patches/0002-offline-vector-map-inset.patch`.

The image above is real output from the shipped rendering code (Dupont Circle, DC, bearing 30°),
produced by `tools/preview_map_panel.py` — not a mockup.

## Why vector and not map tiles

Because the data was already on your device. mapd's offline format turned out to carry full
geometry, not just the speed limits it uses:

```capnp
struct Way {
  name @0 :Text;
  ref @1 :Text;
  nodes @7 :List(Coordinates);      # every lat/lon in the path
  oneWay @11 :Bool;
  highwayClass @15 :HighwayClass;   # motorway .. livingStreet, 14 values
}
```

It's Cap'n Proto packed, and `pycapnp==2.1.0` is already an openpilot dependency, so nothing new
gets installed. `highwayClass` is what makes interstates render differently from side streets.

Raster tiles would have meant a provider API key, a tile cache, network dependence, and
OpenStreetMap's tile-usage policy. None of that applies here.

## On-disk layout

Confirmed against a real download, not inferred:

```
{mapd_root}/offline/<groupLat>/<groupLon>/<minLat>_<minLon>_<maxLat>_<maxLon>
/data/media/0/osm/offline/38/-78/38.750000_-77.250000_39.000000_-77.000000
```

Cells are 0.25°, grouped into 2° directories, 64 cells per group, filenames `%.6f`.

**Both are floored, not truncated.** Go's `int()` truncates toward zero, which would send
longitude −107.5 to directory −106 instead of −108 — wrong for the whole western hemisphere.
`cell_origin()` and `group_origin()` use `math.floor`, and `test_paths_match_real_download`
pins this against filenames from an actual download.

## Performance

Measured on the densest cell in the US data (9.3 MB, 39,913 ways, NW DC / Arlington):

| stage | cost | where it runs |
|---|---|---|
| capnp unpack | 5 ms | worker thread |
| extract every way's nodes | 326 ms | — (avoided) |
| **bbox reject first, extract survivors** | **53 ms** | worker thread, once per cell |
| project + cull, 16.7k points | 0.3–0.8 ms | render thread, every frame |
| draw, 600 segments | 0.4–0.8 ms | render thread, every frame |

Two things make this cheap enough to skip a preprocessing cache:

**`Way` carries its own bounding box**, so rejecting a distant way never touches its node list.
That's the 326 ms → 53 ms. The bbox test must stay before any `w.nodes` access.

**Projection is one vectorised numpy pass**, then a single `memmove` into the raylib buffer.
`Vector2` is two float32s, so a float32 `(N,2)` array is bit-identical — no per-point Python.
`test_output_is_float32_for_memmove` pins that assumption.

About 1 ms/frame total against a 50 ms budget. Expect maybe 4× slower on the 845 — still fine.

## Design notes

**Segment budget, not a way budget.** Each segment is a `draw_line_ex` call, so segments are
what cost. `draw_line_strip` would be one call per way but is always hairline, and GLES commonly
ignores `glLineWidth`, so thickness has to come from geometry. Ways are always sorted by
importance before the budget applies, so what gets dropped is side streets.

**GPS source is picked at runtime.** A 3X publishes `gpsLocation` (qcomgpsd) or
`gpsLocationExternal` (ubloxd) depending on whether a ublox sits at `/dev/ttyHS0`. The panel
takes whichever socket is alive with a fix rather than hardcoding either.

**No tap-to-expand.** Intercepting touches inside the onroad view would fight its existing click
handler. Zoom is a setting instead — Close / Medium / Wide. Worth revisiting if you want it.

## Settings

Settings → sunnypilot → Visuals:
- **Map Panel** — on/off, default off
- **Map Panel Zoom** — Close (~250 m across), Medium (~500 m), Wide (~1 km)

Needs your region downloaded on the OSM page. You already have the whole US, so this is done.

## Iterating on the look without flashing

```bash
python3 tools/preview_map_panel.py --lat 38.9096 --lon -77.0434 --bearing 30 --mpp 1.1
```

Runs the real projection, culling and draw code and writes a PNG, plus per-stage timings. Point
`--root` at a copy of the offline tree if you're working off the device.

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install pycapnp==2.1.0 numpy pyray
.venv/bin/python tests/test_offline_map.py
```

20 tests. Cell files are synthesised through the vendored schema rather than shipping real OSM
extracts, so fixtures are deterministic and no map data is redistributed.

## Attribution

`openpilot/sunnypilot/mapd/offline.capnp` is vendored from
[pfeiferj/mapd](https://github.com/pfeiferj/mapd) (MIT), with the Go package annotations removed
so pycapnp can load it. Struct id and field numbering are unchanged — the wire format depends on
those, so don't renumber. Map data itself is © OpenStreetMap contributors, ODbL.
