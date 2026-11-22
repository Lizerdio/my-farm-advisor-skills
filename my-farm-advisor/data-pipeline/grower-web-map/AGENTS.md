# Grower Web Map Local Instructions

## Purpose

This folder owns grower-level interactive HTML field maps: one page per grower,
built from runtime field boundaries and metadata, opened directly in a browser.

## Safe edit scope

Keep edits inside this folder. This subskill does not own pipeline ingest,
boundary generation, or reporting; it only reads their runtime output. Do not
edit sibling data-pipeline scripts to make a map work.

## Contract

- Read runtime data only. Never write into the skill checkout.
- Runtime script location after seeding:
  `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src/scripts/webmap/generate_grower_web_map.py`.
- Output location:
  `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/derived/dashboards/<grower>_grower_map.html`.
- `DATA_PIPELINE_DATA_ROOT` is required and must be absolute.
- Run the seeded copy with the runtime interpreter
  (`${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python`), not the system
  Python and not the checkout source.

## Reseeding rule

The data-pipeline installer replaces the whole runtime `src/` tree when it
refreshes. After any `data-pipeline/scripts/install.sh` run, reseed this
subskill:

```bash
my-farm-advisor/data-pipeline/grower-web-map/install.sh
```

`install.sh` writes only
`${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src/scripts/webmap/`.

## Asset rules

- Never commit generated HTML maps, screenshots of maps, or tile caches. They
  are runtime artifacts under `${DATA_PIPELINE_DATA_ROOT}`.
- Never embed imagery, rasters, DEM products, or base64 tiles in the page.
  Basemap imagery must stream from the online tile service.
- Keep basemap attribution visible in every generated page. Removing it breaks
  the provider terms recorded in [PROVENANCE.md](PROVENANCE.md).
- Any screenshot added to documentation counts as a figure and must pass the
  repository figure quality gate before commit.
- New tile providers, CDN dependencies, or front-end libraries require a
  [PROVENANCE.md](PROVENANCE.md) update with license and attribution notes.

## Generator rules

- Field geometry comes from `field_boundaries.geojson` unchanged apart from
  decimal rounding. Never synthesize, buffer, or simplify boundaries here; that
  belongs to the pipeline.
- Popup metadata must be traceable to runtime files. Do not infer crop,
  ownership, yield, or management data.
- Escape all attribute text before inserting it into HTML.
- Keep the page dependency-light: Leaflet plus tile layers, no build step, no
  bundler, no framework.
- Prefer failing with a clear error over emitting a map with wrong coordinates.

## Validation

After changing the generator or template, reseed, regenerate both seeded
growers, confirm field counts and acreage, then run the root validator:

```bash
cd /absolute/path/to/repo
./scripts/validate.sh
```
