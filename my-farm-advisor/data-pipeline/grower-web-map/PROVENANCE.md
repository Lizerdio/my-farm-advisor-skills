# Grower Web Map Provenance

This file records where the grower web-map subskill came from and which
external services it depends on at view time.

## Origin

- Original work for the My Farm Advisor skill catalog, written against the
  existing data-pipeline runtime contract. It is not imported, vendored, or
  derived from another skill or project.
- No upstream code, data, fonts, or map payloads are copied into this folder.
- The generator reads only runtime artifacts that the data pipeline produced
  and writes only HTML under the runtime tree.

## Data sources

| Data | Source | Notes |
| --- | --- | --- |
| Field boundaries | `field_boundaries.geojson` written by the data-pipeline boundary ingest, originally OpenStreetMap/Overpass | Geometry is read as-is; the generator never edits or synthesizes polygons |
| Farm and field metadata | Runtime `grower.json`, `farm.json`, `field.json`, `manifests/field-inventory.csv` | Displayed verbatim after HTML escaping |
| Acreage, county, state | Boundary feature properties plus `farm.json` | Rounded for display only |

## View-time dependencies

These are loaded by the browser when the page is opened. They are not vendored
and not downloaded by this skill.

| Component | Version | License / terms | Use |
| --- | --- | --- | --- |
| Leaflet | 1.9.4 from unpkg, pinned with subresource-integrity hashes | BSD-2-Clause | Map rendering |
| Esri World Imagery tiles | Live service, `server.arcgisonline.com` | Esri Terms of Use; attribution is rendered in the map | Default satellite basemap |
| Esri World Boundaries and Places tiles | Live service, `server.arcgisonline.com` | Esri Terms of Use; attribution is rendered in the map | Optional label overlay |
| OpenStreetMap standard tiles | Live service, `tile.openstreetmap.org` | OpenStreetMap Foundation tile usage policy; ODbL data | Alternate base layer |

Basemap attribution is part of the generated page and must stay visible. Do not
strip it, and do not add a provider that lacks clear reuse terms.

## Policy decisions

- **Satellite first.** Esri World Imagery is the default because growers
  recognize their own fields on it; OpenStreetMap is kept as a fallback for
  networks that block the Esri service.
- **No embedded imagery.** Rasters, tiles, and scenes are never base64-inlined.
  The map embeds vector geometry only, which keeps each page small and keeps
  downloaded imagery out of Git.
- **Runtime outputs only.** Generated HTML lives under
  `${DATA_PIPELINE_DATA_ROOT}/data-pipeline` and is never tracked.
- **Fail loudly on projection errors.** A non-WGS84 boundary file is
  reprojected with `pyproj`; when that is not possible, generation stops rather
  than drawing a misleading map.
- **Pinned front-end dependency.** Leaflet is pinned to 1.9.4 with
  integrity hashes, so a CDN compromise or version drift is visible instead of
  silent.

## Validation

Run the repository validator after changing the generator, docs, or installer:

```bash
./scripts/validate.sh
```

Then regenerate maps for the seeded growers and confirm field counts, acreage,
and file size are stable.
