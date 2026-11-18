#!/usr/bin/env python3
"""Generate a lightweight interactive HTML map for one or more growers.

Reads the canonical runtime artifacts produced by the My Farm Advisor data
pipeline (farm/field metadata, field inventory, and field boundary GeoJSON)
and writes a single self-contained HTML page per grower.

The page uses Leaflet from a CDN and an online Esri World Imagery basemap, so
only the field polygons and their metadata are embedded. No rasters, satellite
scenes, or downloaded imagery are written into the HTML.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import sys
from pathlib import Path
from typing import Any

DEFAULT_BASEMAP = "esri"
CRS84_NAMES = ("CRS84", "OGC:1.3:CRS84", "EPSG:4326", "WGS 84", "WGS84")

FIELD_STYLE = {
    "color": "#ffd166",
    "weight": 2,
    "opacity": 0.95,
    "fillColor": "#ffd166",
    "fillOpacity": 0.12,
}
FIELD_HOVER_STYLE = {
    "color": "#ffffff",
    "weight": 3,
    "fillColor": "#ffd166",
    "fillOpacity": 0.28,
}
FIELD_ACTIVE_STYLE = {
    "color": "#4cc9f0",
    "weight": 3,
    "fillColor": "#4cc9f0",
    "fillOpacity": 0.30,
}

ESRI_ATTRIBUTION = (
    "Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, "
    "CNES/Airbus DS, USDA, USGS, AeroGRID, IGN, and the GIS User Community"
)


class MapBuildError(RuntimeError):
    """Raised when runtime inputs cannot be turned into a map."""


def _load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _read_inventory(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            str(row["field_id"]): str(row["field_slug"])
            for row in csv.DictReader(handle)
            if row.get("field_id") and row.get("field_slug")
        }


def _crs_name(geojson: dict[str, Any]) -> str:
    crs = geojson.get("crs")
    if not crs:
        return ""
    return str(((crs.get("properties") or {}).get("name")) or "")


def _coords_are_wgs84(geojson: dict[str, Any]) -> bool:
    name = _crs_name(geojson).upper()
    if not name:
        # RFC 7946 GeoJSON is WGS84 by default.
        return True
    return any(token in name for token in CRS84_NAMES)


def _transform_coords(coords: Any, transformer: Any) -> Any:
    if isinstance(coords, (list, tuple)):
        if len(coords) >= 2 and all(isinstance(v, (int, float)) for v in coords[:2]):
            lon, lat = transformer.transform(coords[0], coords[1])
            return [round(lon, 7), round(lat, 7)]
        return [_transform_coords(item, transformer) for item in coords]
    return coords


def _rounded_coords(coords: Any) -> Any:
    if isinstance(coords, (list, tuple)):
        if len(coords) >= 2 and all(isinstance(v, (int, float)) for v in coords[:2]):
            return [round(coords[0], 7), round(coords[1], 7)]
        return [_rounded_coords(item) for item in coords]
    return coords


def _ensure_wgs84(geojson: dict[str, Any], path: Path) -> dict[str, Any]:
    if _coords_are_wgs84(geojson):
        return geojson
    try:
        from pyproj import Transformer  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - runtime venv provides pyproj
        raise MapBuildError(
            f"{path} is in {_crs_name(geojson) or 'an unknown CRS'} and pyproj is unavailable"
        ) from exc
    transformer = Transformer.from_crs(_crs_name(geojson), "EPSG:4326", always_xy=True)
    for feature in geojson.get("features", []):
        geometry = feature.get("geometry") or {}
        if geometry.get("coordinates") is not None:
            geometry["coordinates"] = _transform_coords(geometry["coordinates"], transformer)
    return geojson


def _field_name(field_meta: dict[str, Any], field_slug: str) -> str:
    display = field_meta.get("display_name")
    if display and display != field_slug:
        return str(display)
    return field_slug


def _collect_features(grower_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    grower = _load_json(grower_dir / "grower.json", {}) or {}
    grower_name = str(grower.get("display_name") or grower_dir.name)
    features: list[dict[str, Any]] = []
    warnings: list[str] = []

    farms_dir = grower_dir / "farms"
    if not farms_dir.is_dir():
        raise MapBuildError(f"no farms directory: {farms_dir}")

    for farm_dir in sorted(p for p in farms_dir.iterdir() if p.is_dir()):
        farm = _load_json(farm_dir / "farm.json", {}) or {}
        farm_slug = str(farm.get("farm_slug") or farm_dir.name)
        farm_name = str(farm.get("display_name") or farm_slug)
        boundary_path = farm_dir / "boundary" / "field_boundaries.geojson"
        if not boundary_path.exists():
            warnings.append(f"skip {farm_dir.name}: missing {boundary_path.name}")
            continue

        geojson = _load_json(boundary_path, {}) or {}
        geojson = _ensure_wgs84(geojson, boundary_path)
        inventory = _read_inventory(farm_dir / "manifests" / "field-inventory.csv")

        for raw in geojson.get("features", []):
            props = dict(raw.get("properties") or {})
            field_id = str(props.get("field_id") or "").strip()
            geometry = raw.get("geometry") or {}
            if not field_id or geometry.get("type") not in {"Polygon", "MultiPolygon"}:
                warnings.append(f"skip malformed feature in {boundary_path.name}")
                continue
            field_slug = inventory.get(field_id, field_id)
            field_meta = _load_json(farm_dir / "fields" / field_slug / "field.json", {}) or {}
            area = props.get("area_acres")
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "grower_slug": grower_dir.name,
                        "grower_name": grower_name,
                        "farm_slug": farm_slug,
                        "farm_name": farm_name,
                        "field_slug": field_slug,
                        "field_name": _field_name(field_meta, field_slug),
                        "field_id": field_id,
                        "area_acres": round(float(area), 1) if area is not None else None,
                        "county": props.get("county_name"),
                        "state_fips": props.get("state_fips"),
                        "state": farm.get("state"),
                        "country": farm.get("country"),
                        "source": props.get("source"),
                    },
                    "geometry": {
                        "type": geometry["type"],
                        "coordinates": _rounded_coords(geometry.get("coordinates")),
                    },
                }
            )

    features.sort(key=lambda f: (f["properties"]["farm_slug"], f["properties"]["field_slug"]))
    return features, warnings


def _total_acres(features: list[dict[str, Any]]) -> float:
    return round(sum(f["properties"]["area_acres"] or 0.0 for f in features), 1)


def _escape_json_for_script(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def _render_html(
    *,
    grower_slug: str,
    grower_name: str,
    features: list[dict[str, Any]],
    basemap: str,
) -> str:
    title = html.escape(f"{grower_name} — Field Map")
    farm_count = len({f["properties"]["farm_slug"] for f in features})
    summary = (
        f"{len(features)} field{'s' if len(features) != 1 else ''} · "
        f"{farm_count} farm{'s' if farm_count != 1 else ''} · "
        f"{_total_acres(features):,.1f} ac"
    )
    data_json = _escape_json_for_script(
        {"type": "FeatureCollection", "features": features}
    )
    style_json = _escape_json_for_script(
        {
            "field": FIELD_STYLE,
            "hover": FIELD_HOVER_STYLE,
            "active": FIELD_ACTIVE_STYLE,
        }
    )
    default_basemap = "esri" if basemap == "esri" else "osm"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{title}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
  integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
  integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<style>
  :root {{ --sidebar: 300px; --ink: #e8eef5; --panel: #141a21; --line: #2a3541; }}
  * {{ box-sizing: border-box; }}
  html, body {{ height: 100%; }}
  body {{ margin: 0; display: flex; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0d1117; color: var(--ink); }}
  #sidebar {{ width: var(--sidebar); min-width: var(--sidebar); background: var(--panel); border-right: 1px solid var(--line); display: flex; flex-direction: column; }}
  #sidebar header {{ padding: 14px 16px 12px; border-bottom: 1px solid var(--line); }}
  #sidebar h1 {{ margin: 0 0 4px; font-size: 15px; font-weight: 600; }}
  #sidebar .summary {{ font-size: 12px; color: #93a4b5; }}
  #sidebar .status {{ margin-top: 6px; font-size: 12px; line-height: 1.4; color: #ff9d9d; }}
  #sidebar .status[hidden] {{ display: none; }}
  #field-list {{ overflow-y: auto; flex: 1; padding: 8px; }}
  .farm-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: #7d8fa1; padding: 8px 8px 4px; }}
  .field-btn {{ display: flex; justify-content: space-between; gap: 8px; width: 100%; background: #1a222b; color: var(--ink); border: 1px solid var(--line); border-radius: 6px; padding: 7px 9px; margin: 3px 0; font-size: 12.5px; cursor: pointer; text-align: left; }}
  .field-btn:hover {{ border-color: #4cc9f0; }}
  .field-btn.active {{ border-color: #4cc9f0; background: #16303c; }}
  .field-btn .acres {{ color: #93a4b5; white-space: nowrap; }}
  #map {{ flex: 1; height: 100%; background: #0d1117; }}
  .leaflet-popup-content {{ font-size: 12.5px; line-height: 1.45; }}
  .leaflet-popup-content table {{ border-collapse: collapse; }}
  .leaflet-popup-content th {{ text-align: left; color: #5b6b7b; font-weight: 600; padding-right: 10px; white-space: nowrap; }}
  .leaflet-popup-content td {{ vertical-align: top; }}
  .map-note {{ background: rgba(20,26,33,.9); color: #93a4b5; padding: 4px 8px; border-radius: 4px; font-size: 11px; }}
  .field-pin {{ width: 16px; height: 16px; border-radius: 50%; background: #ffd166; border: 2px solid #0d1117; box-shadow: 0 0 0 1px rgba(255,255,255,.75); cursor: pointer; }}
  @media (max-width: 720px) {{
    body {{ flex-direction: column; }}
    #sidebar {{ width: 100%; min-width: 0; max-height: 38vh; border-right: 0; border-bottom: 1px solid var(--line); }}
    #map {{ height: 62vh; }}
  }}
</style>
</head>
<body data-grower="{html.escape(grower_slug, quote=True)}">
<aside id="sidebar">
  <header>
    <h1>{title}</h1>
    <div class="summary">{summary}</div>
    <div id="status" class="status" role="status" hidden></div>
  </header>
  <div id="field-list" aria-label="Field list"></div>
</aside>
<div id="map" role="application" aria-label="Interactive field map"></div>
<script>
const DATA = {data_json};
const STYLES = {style_json};
const ESRI_ATTRIBUTION = {json.dumps(ESRI_ATTRIBUTION)};

const DEFAULT_BASEMAP = {json.dumps(default_basemap)};

const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({{
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}}[ch]));

function popupHtml(p) {{
  const rows = [
    ['Grower', p.grower_name],
    ['Farm', p.farm_name],
    ['Field', p.field_name],
    ['Field ID', p.field_id],
    ['Area', p.area_acres != null ? p.area_acres.toLocaleString(undefined, {{ maximumFractionDigits: 1 }}) + ' ac' : null],
    ['County', p.county],
    ['State', p.state || p.state_fips],
  ].filter(([, v]) => v != null && v !== '');
  return '<table>' + rows.map(([k, v]) =>
    `<tr><th>${{escapeHtml(k)}}</th><td>${{escapeHtml(v)}}</td></tr>`).join('') + '</table>';
}}

(function initGrowerMap() {{
  const mapEl = document.getElementById('map');
  const listEl = document.getElementById('field-list');
  const statusEl = document.getElementById('status');

  function showStatus(message) {{
    if (typeof console !== 'undefined') {{ console.error(message); }}
    if (statusEl) {{
      statusEl.textContent = message;
      statusEl.hidden = false;
    }}
  }}

  if (typeof L === 'undefined') {{
    showStatus('Map library failed to load. Check the internet connection, then reload.');
    mapEl.textContent = 'Leaflet could not be loaded from the CDN.';
    return;
  }}

  try {{
    const map = L.map(mapEl, {{ zoomControl: true, scrollWheelZoom: true }});

    let activeLayer = null;
    const layerBySlug = {{}};

    const fieldLayer = L.geoJSON(DATA, {{
      style: () => STYLES.field,
      onEachFeature: (feature, layer) => {{
        const p = feature.properties;
        layer.bindPopup(popupHtml(p));
        layerBySlug[p.field_slug] = layer;
        layer.on('mouseover', () => {{ if (activeLayer !== layer) {{ layer.setStyle(STYLES.hover); }} }});
        layer.on('mouseout', () => {{ if (activeLayer !== layer) {{ layer.setStyle(STYLES.field); }} }});
        layer.on('click', () => activate(p.field_slug));
      }}
    }}).addTo(map);

    // Scale-independent pins keep every field findable when the grower's
    // fields are spread far apart and the polygons shrink to a few pixels.
    const markerGroup = L.layerGroup();
    DATA.features.forEach((f) => {{
      const p = f.properties;
      const fieldShape = layerBySlug[p.field_slug];
      if (!fieldShape) {{ return; }}
      const pin = L.marker(fieldShape.getBounds().getCenter(), {{
        icon: L.divIcon({{ className: 'field-pin', iconSize: [16, 16], iconAnchor: [8, 8] }}),
        keyboard: true,
        title: p.field_name + ' - ' + p.area_acres + ' ac'
      }});
      pin.bindTooltip(p.field_name + ' - ' + p.area_acres + ' ac', {{ direction: 'top', offset: [0, -8] }});
      pin.bindPopup(popupHtml(p));
      pin.on('click', () => activate(p.field_slug));
      markerGroup.addLayer(pin);
    }});
    markerGroup.addTo(map);

    function activate(slug) {{
      const layer = layerBySlug[slug];
      if (!layer) {{ return; }}
      if (activeLayer && activeLayer !== layer && activeLayer.setStyle) {{ activeLayer.setStyle(STYLES.field); }}
      activeLayer = layer;
      layer.setStyle(STYLES.active);
      layer.bringToFront();
      document.querySelectorAll('.field-btn').forEach((el) => {{
        el.classList.toggle('active', el.dataset.slug === slug);
      }});
    }}

    const groups = new Map();
    DATA.features.forEach((f) => {{
      const key = `${{f.properties.farm_name}} (${{f.properties.farm_slug}})`;
      if (!groups.has(key)) {{ groups.set(key, []); }}
      groups.get(key).push(f);
    }});
    for (const [farmLabel, feats] of groups) {{
      const heading = document.createElement('div');
      heading.className = 'farm-label';
      heading.textContent = farmLabel;
      listEl.appendChild(heading);
      feats.forEach((f) => {{
        const p = f.properties;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'field-btn';
        btn.dataset.slug = p.field_slug;
        const name = document.createElement('span');
        name.textContent = p.field_name;
        const acres = document.createElement('span');
        acres.className = 'acres';
        acres.textContent = p.area_acres != null
          ? p.area_acres.toLocaleString(undefined, {{ maximumFractionDigits: 1 }}) + ' ac' : '';
        btn.append(name, acres);
        btn.addEventListener('click', () => {{
          const layer = layerBySlug[p.field_slug];
          activate(p.field_slug);
          if (layer) {{
            map.fitBounds(layer.getBounds(), {{ padding: [40, 40], maxZoom: 17 }});
            layer.openPopup();
          }}
        }});
        listEl.appendChild(btn);
      }});
    }}

    if (fieldLayer.getLayers().length) {{
      map.fitBounds(fieldLayer.getBounds(), {{ padding: [30, 30] }});
    }} else {{
      map.setView([41.9, -88.8], 8);
    }}

    // Basemaps and controls load after the polygons and field list, so a tile
    // layer failure cannot leave the page blank.
    const esri = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}',
      {{ maxZoom: 19, attribution: ESRI_ATTRIBUTION }}
    );
    const osm = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'
    }});
    const labels = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{{z}}/{{y}}/{{x}}',
      {{ maxZoom: 19, attribution: 'Labels &copy; Esri' }}
    );
    const basemaps = {{ 'Esri World Imagery': esri, 'OpenStreetMap': osm }};
    const defaultBasemap = DEFAULT_BASEMAP === 'osm' ? osm : esri;
    defaultBasemap.addTo(map);
    labels.addTo(map);

    L.control.layers(basemaps, {{ 'Field boundaries': fieldLayer, 'Field pins': markerGroup, 'Places & labels': labels }}, {{ collapsed: true }}).addTo(map);
    L.control.scale({{ imperial: true, metric: true }}).addTo(map);

    const note = L.control({{ position: 'bottomleft' }});
    note.onAdd = () => {{
      const div = L.DomUtil.create('div', 'map-note');
      div.textContent = 'Basemap: ' + (defaultBasemap === esri ? 'Esri World Imagery' : 'OpenStreetMap');
      return div;
    }};
    note.addTo(map);
  }} catch (err) {{
    showStatus('Map failed to render: ' + (err && err.message ? err.message : String(err)));
  }}
}})();
</script>
</body>
</html>
"""


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _discover_growers(growers_root: Path, slugs: list[str] | None) -> list[Path]:
    if slugs:
        dirs = []
        for slug in slugs:
            candidate = growers_root / slug
            if not (candidate / "grower.json").is_file():
                raise MapBuildError(f"grower not found: {candidate}")
            dirs.append(candidate)
        return dirs
    return sorted(
        p
        for p in growers_root.iterdir()
        if p.is_dir() and (p / "grower.json").is_file()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-root",
        default=os.environ.get("DATA_PIPELINE_DATA_ROOT"),
        help="Runtime data root (defaults to DATA_PIPELINE_DATA_ROOT).",
    )
    parser.add_argument(
        "--grower-slug",
        action="append",
        default=None,
        help="Grower slug to render; repeatable. Defaults to every grower.",
    )
    parser.add_argument(
        "--all-growers",
        action="store_true",
        help="Render every grower with a grower.json (the default).",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Override output directory (default: each grower's derived/dashboards).",
    )
    parser.add_argument(
        "--basemap",
        choices=["esri", "osm"],
        default=DEFAULT_BASEMAP,
        help=f"Default basemap layer (default: {DEFAULT_BASEMAP}).",
    )
    args = parser.parse_args()

    if not args.data_root:
        print("ERROR: --data-root or DATA_PIPELINE_DATA_ROOT is required", file=sys.stderr)
        return 2
    base = Path(args.data_root).expanduser().resolve()
    growers_root = base / "data-pipeline" / "growers"
    if not growers_root.is_dir():
        print(f"ERROR: growers root not found: {growers_root}", file=sys.stderr)
        return 2

    try:
        grower_dirs = _discover_growers(growers_root, args.grower_slug)
    except MapBuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    failures = 0
    for grower_dir in grower_dirs:
        try:
            features, warnings = _collect_features(grower_dir)
        except MapBuildError as exc:
            print(f"ERROR: {grower_dir.name}: {exc}", file=sys.stderr)
            failures += 1
            continue
        for warning in warnings:
            print(f"WARN: {grower_dir.name}: {warning}", file=sys.stderr)
        if not features:
            print(f"ERROR: {grower_dir.name}: no field polygons found", file=sys.stderr)
            failures += 1
            continue

        grower_meta = _load_json(grower_dir / "grower.json", {}) or {}
        grower_name = str(grower_meta.get("display_name") or grower_dir.name)
        html_text = _render_html(
            grower_slug=grower_dir.name,
            grower_name=grower_name,
            features=features,
            basemap=args.basemap,
        )

        out_dir = Path(args.out_dir).expanduser() if args.out_dir else grower_dir / "derived" / "dashboards"
        out_path = out_dir / f"{grower_dir.name}_grower_map.html"
        _write_atomic(out_path, html_text)
        size_kb = out_path.stat().st_size / 1024
        print(
            f"OK {grower_dir.name}: {len(features)} fields, "
            f"{_total_acres(features):,.1f} ac, {size_kb:.1f} KB -> {out_path}"
        )

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
