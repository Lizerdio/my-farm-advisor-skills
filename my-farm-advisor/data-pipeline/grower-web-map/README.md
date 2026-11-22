# Grower Web Map

Generate one lightweight, self-contained HTML map per grower from the runtime
tree the My Farm Advisor data pipeline already wrote. Every farm and field of
that grower appears over satellite imagery, each field is clickable, and a
sidebar list zooms to any field.

This is a presentation layer over existing pipeline output. It writes no
rasters, downloads no imagery, and never edits boundaries.

## What it produces

| Item | Detail |
| --- | --- |
| Output path | `<data root>/data-pipeline/growers/<grower>/derived/dashboards/<grower>_grower_map.html` |
| Contents | Embedded field polygons (WGS84 GeoJSON) plus grower, farm, field, id, acreage, county, and state |
| Basemap | Online Esri World Imagery tiles, with OpenStreetMap as an alternate base layer and an optional Esri places/labels overlay |
| Size | Tens of kilobytes; the two seeded demo growers render at about 10 KB (3 fields, Illinois) and 15 KB (3 fields, Iowa) |
| Offline use | The page opens offline, but basemap tiles need internet |

The HTML embeds vector geometry only. Imagery, Digital Elevation Model (DEM)
rasters, soil rasters, and satellite scenes stay in the runtime tree where the
pipeline put them.

## Inputs

All reads are read-only and relative to `${DATA_PIPELINE_DATA_ROOT}/data-pipeline`:

| Path | Used for |
| --- | --- |
| `growers/<grower>/grower.json` | Grower slug and display name |
| `growers/<grower>/farms/<farm>/farm.json` | Farm display name, state, country |
| `growers/<grower>/farms/<farm>/boundary/field_boundaries.geojson` | Field polygons and boundary properties |
| `growers/<grower>/farms/<farm>/manifests/field-inventory.csv` | `field_id` to `field_slug` lookup |
| `growers/<grower>/farms/<farm>/fields/<field_slug>/field.json` | Field display name |

Farms without a boundary file are skipped with a warning. A grower with no
usable polygons fails instead of writing an empty map.

## Install and run

The runtime must exist first. Install or refresh it with the data-pipeline
installer, then seed this subskill:

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime

my-farm-advisor/data-pipeline/scripts/install.sh --non-interactive --force-refresh
my-farm-advisor/data-pipeline/grower-web-map/install.sh
```

Generate maps for every grower that has a `grower.json`:

```bash
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src/scripts/webmap/generate_grower_web_map.py" \
  --all-growers
```

Generate for one grower, or write somewhere else:

```bash
GEN="${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src/scripts/webmap/generate_grower_web_map.py"
PY="${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python"

"${PY}" "${GEN}" --grower-slug il-dekalb-grower
"${PY}" "${GEN}" --grower-slug ia-story-grower --out-dir /tmp/grower-maps
"${PY}" "${GEN}" --all-growers --basemap osm
```

### Options

| Flag | Default | Meaning |
| --- | --- | --- |
| `--data-root <abs-path>` | `DATA_PIPELINE_DATA_ROOT` | Runtime data root containing `data-pipeline/` |
| `--grower-slug <slug>` | all growers | Repeatable; limits generation to named growers |
| `--all-growers` | implicit default | Render every grower with a `grower.json` |
| `--out-dir <dir>` | `<grower>/derived/dashboards` | Redirect output files |
| `--basemap esri\|osm` | `esri` | Which base layer the page opens with |

Exit codes: `0` on success, `1` when a grower fails to render, `2` on usage or
runtime-layout errors.

## What the page does

- Fits the view to all of the grower's fields on load, with standard zoom
  controls, mouse wheel zoom, and a metric and imperial scale bar.
- Draws every field as a highlighted polygon; hover brightens it, click selects
  it and opens a popup.
- Adds a scale-independent pin at each field centre, so fields stay findable
  when a grower's ground is spread across a county and the polygons shrink to a
  few pixels. Pins show field name and acreage on hover and open the same
  popup.
- Popup and sidebar identify the **grower**, **farm**, and **field**, plus field
  id, acreage, county, and state.
- Sidebar groups fields by farm; clicking a field zooms to its bounds and opens
  its popup.
- Layer control switches between Esri World Imagery and OpenStreetMap, and
  toggles the Esri places/labels overlay plus the field boundary and pin
  layers.
- Collapses to a stacked layout under 720 px so it stays usable on a phone.
- Reports a failed initialisation in the sidebar instead of failing silently:
  if the Leaflet library cannot load, the map pane says so.

The page is a single file with no server requirement: open it straight from a
Downloads folder (`file://`). Leaflet comes from unpkg and imagery from Esri, so
tiles need internet; the outlines, pins, list, and popups still render without
it.

## Runtime and asset policy

- Generated HTML is runtime output. Keep it under
  `${DATA_PIPELINE_DATA_ROOT}/data-pipeline` and out of Git.
- The generator never writes inside the skill checkout.
- Seeded script location:
  `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src/scripts/webmap/generate_grower_web_map.py`.
- The data-pipeline installer replaces the whole runtime `src/` tree on
  refresh. Re-run `grower-web-map/install.sh` afterwards; it is a plain
  `rsync -a --delete` of this subskill's `src/` into that folder.

## Projection handling

Boundary files are expected in EPSG:4326 (GeoJSON default). A file with an
explicit non-WGS84 CRS is transformed with `pyproj` from the runtime venv; if
`pyproj` is missing or the CRS cannot be resolved, generation fails with an
error instead of plotting misplaced polygons.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `set DATA_PIPELINE_DATA_ROOT or pass --data-root` | Environment not exported | Export the absolute runtime root |
| `runtime source tree missing` | Runtime never installed | Run `data-pipeline/scripts/install.sh` |
| `grower not found` | Slug typo or grower not seeded | List `growers/` under the runtime root |
| `no field polygons found` | Farm boundary download never ran | Re-run the farm pipeline for that grower |
| Blank grey map | Tiles blocked offline | Allow `server.arcgisonline.com` and `unpkg.com` |
| Red message under the header | Initialisation error (shown instead of failing silently) | Read the message; it names the failing step |
| Map disappeared after a pipeline refresh | Runtime `src/` was replaced | Re-run `grower-web-map/install.sh` |

## Validation

Structural check after changes to the generator or template:

```bash
cd /absolute/path/to/repo
./scripts/validate.sh
```

Then regenerate both demo growers and confirm each file exists, stays small,
contains the expected number of `field_id` values, and parses as HTML with the
embedded JSON intact. A browser-free structural check:

```bash
python3 - <<'PY'
import json, pathlib, re

root = pathlib.Path("/absolute/path/to/my-farm-advisor-runtime")
for page in sorted(root.glob("data-pipeline/growers/*/derived/dashboards/*_grower_map.html")):
    text = page.read_text(encoding="utf-8")
    payload = json.loads(
        re.search(r"const DATA = (.*?);\n", text, re.S).group(1).replace("<\\/", "</")
    )
    fields = payload["features"]
    acres = sum(f["properties"]["area_acres"] for f in fields)
    print(f"{page.parts[-4]}: {len(fields)} fields, {acres:.1f} ac, {page.stat().st_size / 1024:.1f} KB")
PY
```
