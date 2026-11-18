---
name: grower-web-map
description: >
  Grower-level interactive field maps for My Farm Advisor. Use when a grower
  needs one lightweight HTML map of every farm and field over satellite
  imagery, with click-through field metadata, zoom, and a zoom-to-field list.
license: Apache-2.0
metadata:
  author: Clayton Young / Superior Byte Works, LLC (@borealBytes)
  version: "0.1.0"
  skill-author: Clayton Young / Superior Byte Works, LLC (@borealBytes)
  skill-version: "0.1.0"
---

# Grower Web Map

Generate one small, self-contained HTML map per grower from runtime pipeline
output. Polygons come from real field boundaries; imagery streams from an
online satellite basemap at view time, so nothing large is embedded and the
page stays in the tens of kilobytes.

## When to use

- A grower asks to see "all my fields on a map", wants a shareable link or file,
  or needs field-level metadata on hover and click.
- A farm report needs a quick visual index of its fields, farms, and acreage.
- Someone needs to confirm that pipeline boundaries, field names, and areas
  line up for a grower before deeper analysis.

Do not use this skill to author figures for a README or gallery; those follow
the repository figure quality gate and the committed raster renderers.

## Start here

- Read [README.md](README.md) for inputs, outputs, runtime paths, and the
  install and run sequence.
- Read [AGENTS.md](AGENTS.md) before changing the generator or the runtime
  seeding contract.
- Read [PROVENANCE.md](PROVENANCE.md) before changing basemaps, tile
  providers, or any external dependency.
- Open [`src/generate_grower_web_map.py`](src/generate_grower_web_map.py) for
  the discovery, projection, and template logic.

## Quick command

Seeding and generation both run against an installed runtime, never against
the checkout:

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
my-farm-advisor/data-pipeline/grower-web-map/install.sh
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src/scripts/webmap/generate_grower_web_map.py" \
  --all-growers
```

## Output contract

- One file per grower:
  `growers/<grower>/derived/dashboards/<grower>_grower_map.html`
- Only field polygons, derived attributes, and styling are embedded. No
  imagery, rasters, Digital Elevation Models (DEM), or cached data bundles.
- The page is runtime output. It stays under
  `${DATA_PIPELINE_DATA_ROOT}/data-pipeline` and never enters Git.

## Guardrails

- Read boundaries from the pipeline output; never invent, densify, or hand
  edit a field polygon in the generator.
- Keep the metadata popup honest: grower, farm, field, field id, area, and
  county or state come from `grower.json`, `farm.json`, `field.json`, and the
  boundary properties. Do not infer crop, ownership, or yield.
- Keep online-tile dependencies to attributing providers already used
  elsewhere in My Farm Advisor, and keep the attribution visible.
- If a boundary file is not in WGS84 and cannot be reprojected, fail loudly.
  A silently misplaced field is worse than no map.
