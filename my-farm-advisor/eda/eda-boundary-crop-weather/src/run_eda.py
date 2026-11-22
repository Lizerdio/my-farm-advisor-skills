"""Boundary / crop / weather EDA runner (static PNG + tidy CSV outputs).

Usage
-----
    export DATA_PIPELINE_DATA_ROOT=~/my-farm-advisor-runtime
    ~/my-farm-advisor-runtime/data-pipeline/.venv/bin/python \
        my-farm-advisor/eda/eda-boundary-crop-weather/src/run_eda.py

Writes to ``$DATA_PIPELINE_DATA_ROOT/data-pipeline/eda/boundary-crop-weather``:

    figures/*.png      static figures, DPI 150, sized for a report page
    figures/README.md  figure index with the verified one-line story
    tables/*.csv       tidy tables the later report can cite
    tables/eda_manifest.json
    tables/hypothesis_evidence.csv

No soil, terrain, or satellite input is read, and nothing is written inside
the repository checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import math
import re
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import ListedColormap
from matplotlib.ticker import MaxNLocator
from shapely import affinity
from shapely.geometry import box

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parents[2]
PIPELINE_SCRIPTS = SKILL_ROOT / "data-pipeline" / "src" / "scripts"
sys.path.insert(0, str(HERE))

from lib import eda_data as ed  # noqa: E402
from lib import eda_plots as ep  # noqa: E402


def _load_field_frame():
    """Reuse the data-pipeline frame contract (local-metre axes, north arrow).

    Loaded by path: ``data-pipeline/src/scripts/lib`` and this workflow's
    ``src/lib`` share the name ``lib``, so a plain import would collide.
    """
    path = PIPELINE_SCRIPTS / "lib" / "field_frame.py"
    if not path.exists():
        raise SystemExit(f"shared frame contract not found: {path}")
    spec = importlib.util.spec_from_file_location("mf_field_frame", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


ff = _load_field_frame()

NATIVE_STATES = {"AK", "HI", "PR", "DC", "GU", "VI", "AS", "MP"}

#: Stories are hypotheses until the run checks them against the numbers in
#: ``tables/hypothesis_evidence.csv``.
STORIES: dict[str, str] = {
    "M1": "Each grower's ten fields are scattered 34–54 km wide across one county rather than forming one contiguous farm, so each panel shows the three closest fields at field scale (6.7–8.0 km) and an inset shows all ten.",
    "B1": "Field area spans 269× end to end because it mixes two boundary sources (USDA median 209 ha vs legacy 12 ha, 17×; effect size 0.74 on a 0–1 scale), and even within USDA the grower shifts the median 1.5× (IA 180 ha vs IL 270 ha).",
    "B2": "Outline roundness does not separate the sources — legacy fields are if anything rounder (median 0.56 vs 0.45) — what separates them is digitisation detail: USDA polygons carry interior holes (mean 1.4, up to 6), hand-drawn legacy fields carry none.",
    "B3": "With boundary source held constant the grower still changes field size (IA 180, NE 208, IL 270 ha; p<0.001, effect size 0.65) but not outline roundness (IL 0.33 vs 0.47–0.48; p=0.58) — region sets scale, not shape.",
    "C1": "All 30 fields resolve a full five-year crop sequence, and the six legacy 'farmland' boundaries resolve to real crops — five to a corn/soy rotation, one to continuous grass — so no field is left uncoded.",
    "C2": "Corn and soy take 96.7% of all field-years and top the mix in every grower-year, and all five non-corn/soy field-years belong to the one legacy pasture field: USDA fields are never anything else.",
    "C3": "Iowa grows corn on fewer field-years (mean 2.5 of 5 vs 3.2–3.3) and puts only 1 field into corn in four or five years, against 4 in Illinois and 5 in Nebraska, but ten fields per grower is too few to call it a grower effect (p=0.24, effect size 0.03).",
    "W1": "The seasonal curves share one shape but sit ~2.4 °C apart (NE 22.9 °C vs IL 20.5 °C on the May–Sep mean), and NE is driest (403 mm vs 451 mm in IA); within-grower cell spread is only 1–9 mm of seasonal total because 7–9 of the 10 fields share one weather grid cell.",
    "W2": "Grower identity dominates seasonal heat (SD 128 growing degree days between growers); year-to-year swings are about half that (52) and field-to-field spread within a grower is smaller again (37), because 7–9 of 10 fields sit in one weather grid cell.",
    "W3": "Growers separate strongly on heat (p<0.001, effect size 0.86) and moderately on rain (p=0.015, effect size 0.35); inside each grower wetter seasons trend cooler (r −0.26 to −0.54), but 2–3 grid cells per grower leave that correlation insignificant.",
}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _natural(field_id: str) -> tuple:
    """Natural sort key, so ``ne-r2`` sorts before ``ne-r10``.

    Splitting the whole string on ``-``/``_`` is not enough: ``r10`` is one
    token and sorts before ``r2``. Split digit runs inside every token too.
    """
    key = []
    for token in re.split(r"(\d+)", str(field_id)):
        if not token:
            continue
        key.append((0, int(token)) if token.isdigit() else (1, token.lower()))
    return tuple(key)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _grower_order(ds: ed.Dataset) -> list[str]:
    return [f.grower_slug for f in ds.farms]


def _state_label(ds: ed.Dataset, slug: str) -> str:
    for farm in ds.farms:
        if farm.grower_slug == slug:
            return f"{farm.state} · {farm.county}"
    return slug


def _ordered_labels(ds: ed.Dataset) -> pd.DataFrame:
    grower_index = {s: i for i, s in enumerate(_grower_order(ds))}
    return (
        ds.labels.assign(
            _g=ds.labels["grower_slug"].map(grower_index),
            _n=ds.labels["field_id"].map(_natural),
        )
        .sort_values(["_g", "_n"])
        .drop(columns=["_g", "_n"])
        .reset_index(drop=True)
    )


def _p_plain(p: float) -> str:
    """p-value for a table column headed ``p`` (no redundant ``p=``)."""
    if p is None or not np.isfinite(p):
        return "—"
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def _figure_note(ds: ed.Dataset, extra: str = "") -> str:
    bits = [
        "My Farm Advisor · boundary/crop/weather EDA",
        f"CDL {ds.cdl_years[0]}–{ds.cdl_years[-1]}",
        f"weather {ds.weather_years[0]}–{ds.weather_years[-1]}",
        f"overlap {ds.overlap_years[0]}–{ds.overlap_years[-1]}",
    ]
    if extra:
        bits.append(extra)
    return " · ".join(bits)


def _source_offsets(is_legacy: pd.Series, width: float = 0.16) -> np.ndarray:
    return np.where(is_legacy.to_numpy(), -width, width)


# --------------------------------------------------------------------------
# M1 - the required static map
# --------------------------------------------------------------------------

def _closest_fields(boundaries: gpd.GeoDataFrame, k: int = 3):
    """IDs of the ``k`` fields with the tightest common window.

    A grower's fields are scattered over tens of km, so a county-scale panel
    turns a 200 ha field into a speck. The closest ``k`` give a field-scale
    view of real boundaries; the inset carries the full footprint.
    """
    sub = boundaries.to_crs(ed.EQUAL_AREA_CRS).reset_index(drop=True)
    if len(sub) <= k:
        idx = tuple(range(len(sub)))
    else:
        best, idx = None, None
        for combo in itertools.combinations(range(len(sub)), k):
            b = sub.iloc[list(combo)].total_bounds
            span = max(b[2] - b[0], b[3] - b[1])
            if best is None or span < best:
                best, idx = span, combo
    return sub.field_id.iloc[list(idx)].tolist()


def _fill_extent(ext, aspect: float = 1.15):
    """Grow the short side of a bbox so the panel fills its cell."""
    minx, maxx, miny, maxy = ext
    w, h = maxx - minx, maxy - miny
    if w / h > aspect:          # too wide: grow the height
        need = w / aspect
        cy = (miny + maxy) / 2
        miny, maxy = cy - need / 2, cy + need / 2
    else:                        # too tall: grow the width
        need = h * aspect
        cx = (minx + maxx) / 2
        minx, maxx = cx - need / 2, cx + need / 2
    return minx, maxx, miny, maxy


def fig_map(ds: ed.Dataset, out_dir: Path, geoadmin: Path) -> tuple[Path, dict]:
    labels = ds.labels.set_index("field_id")
    grower_slugs = _grower_order(ds)
    present = [c for c in ed.CROP_ORDER if (labels["crop_group"] == c).any()]

    legend_handles = ep.crop_handles(present) + [
        mpatches.Patch(fc="none", ec=ep.INK, lw=1.2, ls=(0, (3, 2)),
                       label="legacy OSM outline"),
        plt.Line2D([], [], ls="", marker="o", ms=6, mfc="none", mec=ep.INK,
                   mew=1.2, label="legacy field, ringed"),
    ]

    fig = plt.figure(figsize=(14.4, 4.6))
    gs = fig.add_gridspec(1, 4, width_ratios=[1.0, 1.0, 1.0, 1.02],
                          wspace=0.16, left=0.028, right=0.985, top=0.75,
                          bottom=0.17)

    centroids = {}
    inset_places: dict[str, str] = {}
    for i, slug in enumerate(grower_slugs):
        ax = fig.add_subplot(gs[0, i])
        sub = ds.boundaries[ds.boundaries["grower_slug"] == slug].reset_index(drop=True)
        frame = ff.frame_for(sub[["geometry"]], panel_h_in=3.4, panel_w_in=3.4,
                             force_rot=0.0)
        framed = gpd.GeoDataFrame(
            sub.drop(columns="geometry").copy(),
            geometry=frame.geometry.geometry.to_numpy(),
            crs=frame.geometry.crs,
        )
        framed["crop_group"] = framed["field_id"].map(labels["crop_group"])

        detail = framed[framed["field_id"].isin(_closest_fields(sub, 3))]

        for crop in present:
            sel = detail[detail["crop_group"] == crop]
            if sel.empty:
                continue
            sel.plot(ax=ax, color=ep.CROP_COLORS[crop], edgecolor=ep.INK,
                     linewidth=0.8, zorder=3)

        legacy = detail[detail["is_legacy"]]
        if not legacy.empty:
            legacy.boundary.plot(ax=ax, color=ep.INK, linewidth=1.2,
                                 linestyle=(0, (3, 2)), zorder=6)
            for geom in legacy.geometry:
                point = geom.centroid
                ax.plot(point.x, point.y, marker="o", ms=7.0, mfc="none",
                        mec=ep.INK, mew=1.0, zorder=7)

        # main panel: the closest fields, grown to fill the panel cell
        dminx, dminy, dmaxx, dmaxy = detail.total_bounds
        ext = ff.view_extent(dminx, dmaxx, dminy, dmaxy, pad_frac=0.12)
        ext = _fill_extent(ext, aspect=1.15)
        ax.set_xlim(ext[0], ext[1])
        ax.set_ylim(ext[2], ext[3])
        ax.set_aspect("equal")
        ff.decorate(ax, frame, arrow_r=0.044, arrow_fs=7.0,
                    arrow_xy=(0.90, 0.905))

        # inset: the whole grower footprint, with the detail window boxed.
        # Put it in the emptiest corner (top-right is the north arrow) so it
        # never covers a field: score each candidate by the share of its box
        # that falls inside a detail polygon, then take the lowest.
        fminx, fminy, fmaxx, fmaxy = framed.total_bounds
        full = ff.view_extent(fminx, fmaxx, fminy, fmaxy, pad_frac=0.06)
        span_x = ext[1] - ext[0]
        span_y = ext[3] - ext[2]
        polys = [
            affinity.affine_transform(
                geom,
                [1 / span_x, 0, 0, 1 / span_y, -ext[0] / span_x, -ext[2] / span_y],
            )
            for geom in detail.geometry
        ]
        box_w, box_h = 0.31, 0.42
        corners = {
            "bottom-left": (0.015, 0.02),
            "bottom-right": (0.675, 0.02),
            "top-left": (0.015, 0.56),
        }
        coverage = {}
        for name, (cx, cy) in corners.items():
            probe = box(cx, cy, cx + box_w, cy + box_h)
            coverage[name] = sum(p.intersection(probe).area for p in polys) / (
                box_w * box_h)
        corner = min(coverage, key=lambda k: (coverage[k], list(corners).index(k)))
        inset_left = corner.endswith("left")
        inset_places[slug] = f"{corner} ({coverage[corner]:.3f} field coverage)"
        axin = ax.inset_axes([*corners[corner], box_w, box_h])
        for crop in present:
            sel = framed[framed["crop_group"] == crop]
            if sel.empty:
                continue
            sel.plot(ax=axin, color=ep.CROP_COLORS[crop], edgecolor="white",
                     linewidth=0.25, zorder=3)
        fram_legacy = framed[framed["is_legacy"]]
        if not fram_legacy.empty:
            fram_legacy.boundary.plot(ax=axin, color=ep.INK, linewidth=0.5,
                                      linestyle=(0, (2, 1)), zorder=4)
        axin.add_patch(mpatches.Rectangle(
            (ext[0], ext[2]), ext[1] - ext[0], ext[3] - ext[2],
            fill=False, ec=ep.INK, lw=0.9, ls=(0, (2.5, 1.5)), zorder=6))
        axin.set_xlim(full[0], full[1])
        axin.set_ylim(full[2], full[3])
        axin.set_aspect("equal")
        axin.set_xticks([])
        axin.set_yticks([])
        axin.set_xlabel("")
        axin.set_ylabel("")
        axin.grid(False)
        axin.set_facecolor("white")
        for spine in axin.spines.values():
            spine.set_visible(True)
            spine.set_color("#9CA3AF")
            spine.set_linewidth(0.7)
        axin.text(0.04, 0.04, f"all {len(sub)} fields",
                  transform=axin.transAxes, ha="left", va="bottom", fontsize=5.6,
                  color=ep.MUTED, zorder=8,
                  bbox=dict(boxstyle="square,pad=0.10", fc="white", ec="none",
                            alpha=0.85))

        span_km = (ext[1] - ext[0]) / 1000.0
        length_km = min((0.5, 1, 2, 5, 10),
                        key=lambda c: abs(c - 0.22 * span_km))
        ep.scale_bar_km(ax, length_km * 1000.0,
                        x=0.63 if inset_left else 0.05, y=0.045)

        ax.tick_params(labelbottom=False, labelleft=False, length=0)
        ax.set_xlabel("")
        ax.set_ylabel("")
        ax.grid(False)
        for spine in ax.spines.values():
            spine.set_visible(True)
            spine.set_color("#D1D5DB")
            spine.set_linewidth(0.8)
        ax.set_title(
            f"{_state_label(ds, slug)} — {len(sub)} fields · "
            f"{sub['geom_area_ha'].sum():,.0f} ha",
            fontsize=9.4, pad=5,
        )
        centroids[slug] = sub.to_crs(ed.EQUAL_AREA_CRS).geometry.union_all().centroid

    # locator: lower-48 states in Albers equal-area, study states highlighted
    ax_loc = fig.add_subplot(gs[0, 3])
    states = gpd.read_parquet(geoadmin / "l1_states" / "states_usa.parquet")
    conus = states[~states["state_code"].isin(NATIVE_STATES)].to_crs(5070)
    conus.boundary.plot(ax=ax_loc, color="#D1D5DB", linewidth=0.4, zorder=1)

    palette = ep.grower_palette([_state_label(ds, s) for s in grower_slugs])
    for slug in grower_slugs:
        code = next(f.state for f in ds.farms if f.grower_slug == slug)
        patch = conus[conus["state_code"] == code]
        if patch.empty:
            continue
        patch.plot(ax=ax_loc, color=palette[_state_label(ds, slug)],
                   edgecolor="white", linewidth=0.7, zorder=2)
        point = centroids[slug]
        ax_loc.plot(point.x, point.y, marker="o", ms=4.6, mfc="white",
                    mec=ep.INK, mew=0.9, zorder=4)
        dx, dy, ha = {"NE": (0, -13, "center"), "IA": (2, 10, "center"),
                      "IL": (10, -8, "left")}.get(code, (6, -3, "left"))
        ax_loc.annotate(code, xy=(point.x, point.y), xytext=(dx, dy),
                        textcoords="offset points", ha=ha, fontsize=7.6,
                        fontweight="bold", color=ep.INK, zorder=5)
    minx, miny, maxx, maxy = conus.total_bounds
    pad = 0.02 * (maxx - minx)
    ax_loc.set_xlim(minx - pad, maxx + pad)
    ax_loc.set_ylim(miny - pad, maxy + pad)
    ax_loc.set_aspect("equal")
    ax_loc.set_xticks([])
    ax_loc.set_yticks([])
    ax_loc.grid(False)
    ax_loc.set_xlabel("")
    ax_loc.set_ylabel("")
    for spine in ax_loc.spines.values():
        spine.set_visible(False)
    ax_loc.set_title("Locator — lower 48", fontsize=8.8, pad=5)
    ax_loc.text(0.5, -0.02, "white dot = that grower's fields",
                transform=ax_loc.transAxes, ha="center", va="top",
                fontsize=6.6, color=ep.MUTED)

    fig.legend(handles=legend_handles, loc="lower center", ncol=6,
               bbox_to_anchor=(0.43, 0.025), fontsize=7.6)
    ep.title_block(
        fig,
        "Thirty real fields in three counties — crop in 2023",
        "Field-scale close-ups; insets show all ten fields. Rings mark "
        "hand-drawn fields.",
        y=0.975,
    )
    path = ep.save(fig, out_dir / "M1_map_growers_boundaries.png",
                   _figure_note(ds, "boundaries: field_boundaries.geojson"))
    return path, inset_places


# --------------------------------------------------------------------------
# B1-B3 boundaries
# --------------------------------------------------------------------------

def fig_boundary_size(ds: ed.Dataset, out_dir: Path) -> Path:
    b = ds.labels.copy()
    grower_slugs = _grower_order(ds)
    label_of = {s: _state_label(ds, s) for s in grower_slugs}
    palette = ep.grower_palette([label_of[s] for s in grower_slugs])

    fig, ax = plt.subplots(figsize=(9.6, 4.4))
    ypos = {s: i for i, s in enumerate(grower_slugs)}
    y = b["grower_slug"].map(ypos).astype(float).to_numpy()
    y = y + _source_offsets(b["is_legacy"]) + ep.jitter(len(b), 0.045)

    pretty_source = {"USDA CSB": "USDA boundary", "Legacy OSM": "hand-drawn legacy"}
    for source in ("USDA CSB", "Legacy OSM"):
        sel = (b["source_label"] == source).to_numpy()
        ax.scatter(
            b.loc[sel, "geom_area_ha"], y[sel],
            s=60 if source == "Legacy OSM" else 34,
            marker=ep.SOURCE_MARKERS[source],
            c=[palette[_state_label(ds, s)] for s in b.loc[sel, "grower_slug"]],
            edgecolor="white", linewidth=0.6, alpha=0.95, zorder=3,
            label=pretty_source[source],
        )

    for slug in grower_slugs:
        for source, sign in (("USDA CSB", 0.16), ("Legacy OSM", -0.16)):
            sel = b[(b["grower_slug"] == slug) & (b["source_label"] == source)]
            if sel.empty:
                continue
            med = sel["geom_area_ha"].median()
            ax.plot([med, med], [ypos[slug] + sign - 0.075, ypos[slug] + sign + 0.075],
                    color=ep.INK, lw=2.4, solid_capstyle="butt", zorder=4)
            ax.annotate(f"{med:,.0f} ha" if med >= 10 else f"{med:,.1f} ha",
                        xy=(med, ypos[slug] + sign), xytext=(0, 10),
                        textcoords="offset points", ha="center", fontsize=6.8,
                        color=ep.INK)

    ax.set_xscale("log")
    ax.set_yticks(range(len(grower_slugs)))
    ax.set_yticklabels([label_of[s] for s in grower_slugs])
    ax.set_xlabel("field area (hectares, log scale)")
    ax.set_ylim(-0.7, len(grower_slugs) - 0.25)
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower right", title="boundary source")

    csb = b[~b["is_legacy"]]["geom_area_ha"]
    legacy = b[b["is_legacy"]]["geom_area_ha"]
    detail = []
    if len(legacy):
        detail = [
            f"hand-drawn legacy: n={len(legacy)}, median {legacy.median():,.1f} ha",
            f"USDA boundaries: n={len(csb)}, median {csb.median():,.0f} ha",
            f"median ratio {csb.median() / legacy.median():,.0f}×",
        ]
    ep.annotate(ax, "\n".join(detail), x=0.015, y=0.985, ha="left", va="top")
    fig.subplots_adjust(top=0.80)
    ep.title_block(
        fig,
        "Two boundary sources, two very different field sizes",
        "USDA fields are ~17× larger than hand-drawn ones; Iowa's are smaller "
        "than Illinois'.",
    )
    return ep.save(fig, out_dir / "B1_boundary_size_by_source.png", _figure_note(ds))


def fig_boundary_shape(ds: ed.Dataset, out_dir: Path) -> Path:
    b = ds.labels.copy()
    grower_slugs = _grower_order(ds)
    label_of = {s: _state_label(ds, s) for s in grower_slugs}
    palette = ep.grower_palette([label_of[s] for s in grower_slugs])

    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.7),
                             gridspec_kw={"width_ratios": [1.35, 1.0]})
    ax = axes[0]
    for source in ("USDA CSB", "Legacy OSM"):
        for slug in grower_slugs:
            sel = b[(b["source_label"] == source) & (b["grower_slug"] == slug)]
            if sel.empty:
                continue
            ax.scatter(sel["geom_area_ha"], sel["polsby_popper_exterior"],
                       s=64 if source == "Legacy OSM" else 34,
                       marker=ep.SOURCE_MARKERS[source],
                       color=palette[label_of[slug]],
                       edgecolor="white", linewidth=0.6, alpha=0.95, zorder=3)
    csb = b[~b["is_legacy"]]
    rho = ed.spearman(csb["geom_area_ha"], csb["polsby_popper_exterior"])
    ax.set_xscale("log")
    ax.set_xlabel("field area (hectares, log scale)")
    ax.set_ylabel("roundness of outline (1 = perfect circle)")
    ax.set_title("Roundness by size")
    ax.set_ylim(0, 1.0)
    handles = [
        plt.Line2D([], [], ls="", marker=ep.SOURCE_MARKERS["USDA CSB"], color=ep.MUTED,
                   label="USDA boundary"),
        plt.Line2D([], [], ls="", marker=ep.SOURCE_MARKERS["Legacy OSM"], color=ep.MUTED,
                   label="hand-drawn legacy"),
    ] + [plt.Line2D([], [], ls="", marker="o", color=palette[label_of[s]],
                    label=label_of[s]) for s in grower_slugs]
    ax.legend(handles=handles, loc="upper left", ncol=1)
    ep.annotate(
        ax,
        "\n".join([
            f"USDA median {csb['polsby_popper_exterior'].median():.2f}",
            f"legacy median {b[b['is_legacy']]['polsby_popper_exterior'].median():.2f}",
            f"size vs roundness: {rho['effect']:+.2f} correlation, "
            f"{ed.fmt_p(rho['p_value'])}".replace("-", "−"),
        ]),
        x=0.015, y=0.03, ha="left", va="bottom",
    )

    ax2 = axes[1]
    for i, source in enumerate(("USDA CSB", "Legacy OSM")):
        sel = b[b["source_label"] == source]
        if sel.empty:
            continue
        x = np.full(len(sel), i) + ep.jitter(len(sel), 0.12)
        ax2.scatter(x, sel["interior_holes"], s=46,
                    marker=ep.SOURCE_MARKERS[source],
                    c=[palette[_state_label(ds, s)] for s in sel["grower_slug"]],
                    edgecolor="white", linewidth=0.6, alpha=0.95, zorder=3)
        ax2.plot([i - 0.18, i + 0.18], [sel["interior_holes"].median()] * 2,
                 color=ep.INK, lw=2.4, zorder=4)
        ax2.annotate(f"median {sel['interior_holes'].median():.1f}",
                     xy=(i, sel["interior_holes"].median()), xytext=(0, 12),
                     textcoords="offset points", ha="center", fontsize=7)
    ax2.set_xticks([0, 1])
    ax2.set_xticklabels(["USDA boundaries", "hand-drawn legacy"])
    ax2.set_ylabel("interior holes per field outline")
    ax2.set_yticks(range(0, int(max(b["interior_holes"].max(), 1)) + 1))
    ax2.set_title("Interior holes")
    ax2.grid(axis="x", visible=False)

    fig.tight_layout(rect=(0, 0, 1, 0.84))
    ep.title_block(
        fig,
        "Shape is not what separates the two boundary sources",
        "Hand-drawn fields are slightly rounder, not more irregular; USDA "
        "fields carry interior holes.",
    )
    return ep.save(fig, out_dir / "B2_boundary_shape_vs_size.png", _figure_note(ds))


def fig_boundary_compare(ds: ed.Dataset, out_dir: Path) -> tuple[Path, pd.DataFrame]:
    b = ds.labels.copy()
    grower_slugs = _grower_order(ds)
    label_of = {s: _state_label(ds, s) for s in grower_slugs}
    palette = ep.grower_palette([label_of[s] for s in grower_slugs])
    csb = b[~b["is_legacy"]]
    legacy = b[b["is_legacy"]]

    rows = []
    for metric, label in (("geom_area_ha", "area_ha"),
                          ("polsby_popper_exterior", "outline_compactness")):
        groups = {label_of[s]: csb.loc[csb["grower_slug"] == s, metric].tolist()
                  for s in grower_slugs}
        groups = {k: v for k, v in groups.items() if len(v) >= 3}
        res = ed.kruskal(groups)
        rows.append({
            "comparison": "CSB fields across growers", "metric": label,
            "groups": "; ".join(f"{k} (n={len(v)})" for k, v in groups.items()),
            "test": res["test"], "statistic": res["statistic"],
            "p_value": res["p_value"], "effect": res["effect"],
            "effect_name": res["effect_name"], "note": "legacy fields excluded",
        })
    if len(legacy):
        res = ed.mann_whitney(csb["geom_area_ha"], legacy["geom_area_ha"])
        rows.append({
            "comparison": "CSB vs legacy OSM (all growers)", "metric": "area_ha",
            "groups": f"CSB (n={len(csb)}); legacy (n={len(legacy)})",
            "test": res["test"], "statistic": res["statistic"],
            "p_value": res["p_value"], "effect": res["effect"],
            "effect_name": res["effect_name"], "note": "source effect",
        })
    legacy_groups = {label_of[s]: legacy.loc[legacy["grower_slug"] == s, "geom_area_ha"].tolist()
                     for s in grower_slugs}
    legacy_groups = {k: v for k, v in legacy_groups.items() if len(v) >= 3}
    if len(legacy_groups) == 2:
        (name_a, vals_a), (name_b, vals_b) = list(legacy_groups.items())
        res = ed.mann_whitney(vals_a, vals_b)
        rows.append({
            "comparison": f"{name_a} vs {name_b} (legacy OSM only)", "metric": "area_ha",
            "groups": f"{name_a} (n={len(vals_a)}); {name_b} (n={len(vals_b)})",
            "test": res["test"], "statistic": res["statistic"],
            "p_value": res["p_value"], "effect": res["effect"],
            "effect_name": res["effect_name"],
            "note": "one grower has no legacy field",
        })
    table = pd.DataFrame(rows)

    fig, axes = plt.subplots(1, 2, figsize=(10.8, 4.3),
                             gridspec_kw={"width_ratios": [1.2, 1.0]})
    ax = axes[0]
    for i, slug in enumerate(grower_slugs):
        sel = b[b["grower_slug"] == slug]
        for source, sign in (("USDA CSB", 0.18), ("Legacy OSM", -0.18)):
            part = sel[sel["source_label"] == source]
            if part.empty:
                continue
            x = np.full(len(part), i + sign) + ep.jitter(len(part), 0.05)
            ax.scatter(x, part["geom_area_ha"], s=52 if source == "Legacy OSM" else 32,
                       marker=ep.SOURCE_MARKERS[source],
                       color=palette[label_of[slug]],
                       edgecolor="white", linewidth=0.6, alpha=0.95, zorder=3)
            ax.plot([i + sign - 0.1, i + sign + 0.1],
                    [part["geom_area_ha"].median()] * 2,
                    color=ep.INK, lw=2.2, zorder=4)
    ax.set_yscale("log")
    ax.set_xticks(range(len(grower_slugs)))
    ax.set_xticklabels([label_of[s] for s in grower_slugs])
    ax.set_ylabel("field area (hectares, log scale)")
    ax.set_title("Field size by grower and boundary source")
    ax.set_xlim(-0.6, len(grower_slugs) - 0.4)
    handles = [
        plt.Line2D([], [], ls="", marker=ep.SOURCE_MARKERS["USDA CSB"], color=ep.MUTED,
                   label="USDA boundary"),
        plt.Line2D([], [], ls="", marker=ep.SOURCE_MARKERS["Legacy OSM"], color=ep.MUTED,
                   label="hand-drawn legacy"),
    ]
    ax.legend(handles=handles, loc="lower left")

    ax2 = axes[1]
    ax2.axis("off")
    ax2.set_title("What the numbers say", fontsize=9.6, loc="left", pad=2)
    ax2.text(
        0.0, 0.93,
        "\n".join(textwrap.wrap(
            "Size travels with the grower; shape does not. That is why raw "
            "geometry would confuse region with boundary source.", 46)),
        transform=ax2.transAxes, va="top", ha="left", fontsize=8.6,
        color=ep.INK, linespacing=1.3,
    )
    csb_by = {s: csb.loc[csb["grower_slug"] == s, "geom_area_ha"].median()
              for s in grower_slugs}
    pp_by = {s: csb.loc[csb["grower_slug"] == s, "polsby_popper_exterior"].median()
             for s in grower_slugs}
    ep.stat_rows(
        ax2,
        [
            ("Field size across growers",
             " · ".join(f"{label_of[s].split(' · ')[0]} {csb_by[s]:,.0f} ha"
                        for s in grower_slugs),
             f"p {_p_plain(table.iloc[0]['p_value'])}"),
            ("Outline roundness across growers",
             " · ".join(f"{label_of[s].split(' · ')[0]} {pp_by[s]:.2f}"
                        for s in grower_slugs),
             f"p {_p_plain(table.iloc[1]['p_value'])}"),
        ],
        top=0.66, gap=0.22,
    )
    ax2.text(0.0, 0.03,
             "Rank test on 7–10 USDA fields per grower; legacy fields excluded. "
             "p = chance of seeing this by luck.",
             transform=ax2.transAxes, va="bottom", ha="left", fontsize=7.0,
             color=ep.MUTED)
    fig.subplots_adjust(top=0.76, bottom=0.10, wspace=0.10)
    ep.title_block(
        fig,
        "Does the grower still matter once boundary source is fixed?",
        "Field size differs by grower (Iowa ~two-thirds of Illinois) but "
        "roundness does not.",
    )
    path = ep.save(fig, out_dir / "B3_boundary_metrics_comparison.png", _figure_note(ds))
    return path, table


# --------------------------------------------------------------------------
# C1-C3 CDL
# --------------------------------------------------------------------------

def _dominant_cdl(ds: ed.Dataset) -> pd.DataFrame:
    idx = ds.cdl.groupby(["field_id", "year"])["pct"].idxmax()
    return ds.cdl.loc[idx].reset_index(drop=True)


def fig_cdl_sequence(ds: ed.Dataset, out_dir: Path) -> Path:
    years = ds.cdl_years
    labels = _ordered_labels(ds).set_index("field_id")
    order = labels.index.tolist()
    dominant = _dominant_cdl(ds)

    grid = np.full((len(order), len(years)), np.nan)
    names: dict[tuple[int, int], str] = {}
    for i, field in enumerate(order):
        for j, year in enumerate(years):
            sel = dominant[(dominant["field_id"] == field) & (dominant["year"] == year)]
            if sel.empty:
                continue
            row = sel.iloc[0]
            grid[i, j] = ed.CROP_ORDER.index(row["crop_group"])
            names[(i, j)] = str(row["crop_name"])

    cmap = ListedColormap([ep.CROP_COLORS[c] for c in ed.CROP_ORDER])
    fig, ax = plt.subplots(figsize=(7.0, 8.0))
    ax.imshow(grid, cmap=cmap, vmin=-0.5, vmax=len(ed.CROP_ORDER) - 0.5,
              aspect="auto", interpolation="nearest")
    for (i, j), name in names.items():
        short = name.replace("Grass/Pasture", "Grass").replace("Winter Wheat", "Wheat")
        ax.text(j, i, short, ha="center", va="center", fontsize=6.0, color=ep.INK)

    ax.set_xticks(range(len(years)))
    ax.set_xticklabels([str(y) for y in years])
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels([f"{f} ✱" if labels.loc[f, "is_legacy"] else f for f in order],
                       fontsize=6.8)
    for tick, field in zip(ax.get_yticklabels(), order):
        if labels.loc[field, "is_legacy"]:
            tick.set_fontstyle("italic")
    ax.grid(False)
    ax.set_xticks(np.arange(-0.5, len(years), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(order), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.4)
    ax.tick_params(which="minor", length=0)

    for slug in _grower_order(ds):
        members = [k for k, f in enumerate(order) if labels.loc[f, "grower_slug"] == slug]
        if not members:
            continue
        ax.axhline(min(members) - 0.5, color=ep.INK, lw=1.0)
        ax.text(len(years) - 0.34, (min(members) + max(members)) / 2,
                _state_label(ds, slug), rotation=90, va="center", ha="left",
                fontsize=7.2, color=ep.INK)
        ax.text(len(years) - 0.02, (min(members) + max(members)) / 2, " ",
                fontsize=7.2)
    ax.axhline(len(order) - 0.5, color=ep.INK, lw=1.0)

    present = [c for c in ed.CROP_ORDER if c in set(dominant["crop_group"])]
    fig.legend(handles=ep.crop_handles(present), loc="lower center",
               bbox_to_anchor=(0.45, 0.012), ncol=len(present), fontsize=7.4)
    fig.subplots_adjust(bottom=0.13, top=0.88)
    ax.set_title(f"Dominant crop per field, {years[0]}–{years[-1]}  "
                 "(✱ italic = legacy OSM field, label was 'farmland')",
                 fontsize=9.0)
    ax.set_xlabel("CDL year")
    ep.title_block(
        fig,
        "Five years of crops on every field",
        "Corn and soy fill every field-year except one legacy field that "
        "stayed in grass all five years.",
        y=0.985, gap=0.028,
    )
    return ep.save(fig, out_dir / "C1_cdl_crop_sequence_heatmap.png", _figure_note(ds))


def fig_cdl_share(ds: ed.Dataset, out_dir: Path) -> Path:
    years = ds.cdl_years
    grower_slugs = _grower_order(ds)
    dominant = _dominant_cdl(ds).merge(
        ds.labels[["field_id", "is_legacy"]], on="field_id", how="left"
    )
    fig, axes = plt.subplots(1, len(grower_slugs), figsize=(11.2, 4.0), sharey=True)
    if len(grower_slugs) == 1:
        axes = [axes]
    for ax, slug in zip(axes, grower_slugs):
        sub = dominant[dominant["grower_slug"] == slug]
        counts = (sub.groupby(["year", "crop_group"]).size().unstack(fill_value=0)
                  .reindex(columns=ed.CROP_ORDER, fill_value=0)
                  .reindex(years, fill_value=0))
        totals = counts.sum(axis=1).to_numpy(dtype=float)
        bottom = np.zeros(len(counts))
        for crop in ed.CROP_ORDER:
            vals = counts[crop].to_numpy(dtype=float)
            if vals.sum() == 0:
                continue
            share = vals / totals * 100.0
            ax.bar(counts.index, share, bottom=bottom, width=0.72,
                   color=ep.CROP_COLORS[crop], edgecolor="white", linewidth=0.8,
                   label=crop)
            for x, (base, value) in enumerate(zip(bottom, share)):
                if value >= 12:
                    ax.text(counts.index[x], base + value / 2, f"{value:.0f}%",
                            ha="center", va="center", fontsize=6.8,
                            color=ep.INK, fontweight="bold")
            bottom += share
        ax.set_title(f"{_state_label(ds, slug)}  ({int(totals[0])} fields)")
        ax.set_xlabel("CDL year")
        ax.set_ylim(0, 100)
        ax.grid(axis="x", visible=False)
    axes[0].set_ylabel("share of fields (%)")
    present = [c for c in ed.CROP_ORDER if c in set(dominant["crop_group"])]
    fig.legend(handles=ep.crop_handles(present), loc="lower center",
               ncol=len(present), bbox_to_anchor=(0.5, 0.015), fontsize=7.6)
    fig.subplots_adjust(bottom=0.18, top=0.78, wspace=0.12)
    ep.title_block(
        fig,
        "Corn and soy fill almost every field-year",
        "They account for 96.7% of all field-years; the only other crop is one "
        "legacy pasture field.",
    )
    return ep.save(fig, out_dir / "C2_cdl_crop_share_by_grower.png", _figure_note(ds))


def fig_cdl_rotation(ds: ed.Dataset, out_dir: Path) -> tuple[Path, pd.DataFrame]:
    rot = ds.rotation.merge(
        ds.labels[["field_id", "is_legacy", "geom_area_ha"]], on="field_id", how="left"
    )
    grower_slugs = _grower_order(ds)
    label_of = {s: _state_label(ds, s) for s in grower_slugs}
    palette = ep.grower_palette([label_of[s] for s in grower_slugs])

    fig, ax = plt.subplots(figsize=(8.8, 4.8))
    bins = np.arange(0, len(ds.cdl_years) + 1)
    width = 0.26
    offsets = np.linspace(-width, width, len(grower_slugs))
    top = 0
    for off, slug in zip(offsets, grower_slugs):
        sel = rot[rot["grower_slug"] == slug]
        counts = (sel["corn_years"].value_counts()
                  .reindex(bins, fill_value=0).to_numpy())
        top = max(top, int(counts.max()))
        bars = ax.bar(
            bins + off, counts, width=width * 0.9,
            color=palette[label_of[slug]], edgecolor="white", linewidth=0.6,
            zorder=3,
            label=f"{label_of[slug]} — mean {sel['corn_years'].mean():.1f}",
        )
        for rect, n in zip(bars, counts):
            if n:
                ax.text(rect.get_x() + rect.get_width() / 2, n + 0.12, str(n),
                        ha="center", va="bottom", fontsize=7.4, color=ep.INK)

    # the single zero-corn field is the legacy pasture: point at it so it is
    # never hidden behind an annotation
    pasture = rot[rot["is_legacy"] & (rot["corn_years"] == 0)]
    if not pasture.empty:
        slug = pasture.iloc[0]["grower_slug"]
        x0 = offsets[grower_slugs.index(slug)]
        ax.annotate("the one legacy\npasture field", xy=(x0, 0.15),
                    xytext=(x0 + 1.0, min(top, 4) * 0.55), fontsize=7.2,
                    ha="center", va="bottom",
                    arrowprops=dict(arrowstyle="-", color=ep.MUTED, lw=0.8),
                    bbox=dict(boxstyle="round,pad=0.25", fc="white",
                              ec=ep.GRID, lw=0.7), zorder=6)

    ax.set_xticks(bins)
    ax.set_xticklabels([str(int(b)) for b in bins])
    ax.set_xlabel(f"years in corn, {ds.cdl_years[0]}–{ds.cdl_years[-1]} (out of five)")
    ax.set_ylabel("number of fields")
    ax.set_ylim(0, top + 1.8)
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper right", title="grower")

    groups = {label_of[s]: rot.loc[rot["grower_slug"] == s, "corn_years"].tolist()
              for s in grower_slugs}
    res = ed.kruskal(groups)
    rows = [{
        "comparison": "corn years across growers", "metric": "corn_years",
        "groups": "; ".join(f"{k} (n={len(v)})" for k, v in groups.items()),
        "test": res["test"], "statistic": res["statistic"],
        "p_value": res["p_value"], "effect": res["effect"],
        "effect_name": res["effect_name"], "note": "5-year CDL window",
    }]
    for slug in grower_slugs:
        sel = rot[rot["grower_slug"] == slug]
        rows.append({
            "comparison": f"{label_of[slug]} rotation profile", "metric": "corn_years",
            "groups": f"n={len(sel)}", "test": "descriptive",
            "statistic": float(sel["corn_years"].mean()), "p_value": np.nan,
            "effect": float(sel["crop_diversity"].mean()),
            "effect_name": "mean crop diversity",
            "note": f"continuous corn (>=4 corn yrs): {int((sel['corn_years'] >= 4).sum())}",
        })
    table = pd.DataFrame(rows)
    ax.text(0.015, 0.975,
            f"Rank test across growers: {ed.fmt_p(res['p_value'])}",
            transform=ax.transAxes, va="top", fontsize=7.4, color=ep.MUTED)
    fig.subplots_adjust(top=0.79, bottom=0.15)
    ep.title_block(
        fig,
        "How many of the five years each field grew corn",
        "Iowa plants corn less often; ten fields per grower is too few to "
        "call it a grower effect (p=0.24).",
    )
    path = ep.save(fig, out_dir / "C3_cdl_rotation_comparison.png", _figure_note(ds))
    return path, table


# --------------------------------------------------------------------------
# W1-W3 weather
# --------------------------------------------------------------------------

def fig_weather_envelope(ds: ed.Dataset, out_dir: Path) -> Path:
    monthly = ed.cell_monthly_climatology(ds.weather)
    grower_slugs = _grower_order(ds)
    label_of = {s: _state_label(ds, s) for s in grower_slugs}
    palette = ep.grower_palette([label_of[s] for s in grower_slugs])
    cell_mix = ed.power_cell_mix(ds.weather)

    fig, axes = plt.subplots(1, 2, figsize=(11.4, 4.4))
    for ax, value, ylabel, title in (
        (axes[0], "tmean_c", "mean air temperature (°C)", "Monthly temperature"),
        (axes[1], "precip_mm", "monthly precipitation (mm)", "Monthly precipitation"),
    ):
        for slug in grower_slugs:
            sub = monthly[monthly["grower_slug"] == slug]
            # one thin line per NASA POWER cell: the real within-grower spread
            for _, cell in sub.groupby("power_cell"):
                ax.plot(cell["month"], cell[value], color=palette[label_of[slug]],
                        lw=0.9, alpha=0.45, zorder=2)
            mid = sub.groupby("month")[value].median()
            ax.plot(mid.index, mid, color=palette[label_of[slug]], lw=2.4,
                    marker="o", ms=4, label=label_of[slug], zorder=4)
        ax.axvspan(4.6, 9.4, color="#F3F4F6", zorder=0)
        ax.set_xticks(range(1, 13))
        ax.set_xlabel("month")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.set_xlim(0.6, 12.4)
    axes[1].legend(loc="upper left", title="grower")
    fig.tight_layout(rect=(0, 0, 1, 0.84))
    ep.title_block(
        fig,
        "One seasonal curve, about 2 °C apart",
        "Nebraska is warmest and driest, Iowa wettest; thin lines are "
        "individual weather grid cells (shaded = May–Sep).",
    )
    # W1 is the full five-year record; W3 uses the shorter crop overlap, where
    # Iowa ranks dry. Say so on the figure so the pair cannot read as a clash.
    fig.text(
        0.5, 0.902,
        "2021–2025 five-year view: Iowa's wet 2024–2025 lift it to wettest "
        "here; the shorter 2021–2023 overlap is in W3.",
        ha="center", va="top", fontsize=8.2, color=ep.MUTED, style="italic",
    )

    season = ds.seasonal
    detail = []
    for slug in grower_slugs:
        sel = season[season["grower_slug"] == slug]
        cell_means = sel.groupby("power_cell")["precip_mm"].mean()
        detail.append(
            f"{label_of[slug].split(' · ')[0]} {sel['precip_mm'].mean():,.0f} mm "
            f"(cells {cell_means.min():,.0f}–{cell_means.max():,.0f}, "
            f"fields/cell {cell_mix.get(slug, '?')})"
        )
    return ep.save(fig, out_dir / "W1_weather_climate_envelope.png",
                   _figure_note(ds, " · ".join(detail)))


def fig_weather_gdd(ds: ed.Dataset, out_dir: Path) -> Path:
    season = ds.seasonal
    cells = ds.cell_seasonal
    grower_slugs = _grower_order(ds)
    label_of = {s: _state_label(ds, s) for s in grower_slugs}
    palette = ep.grower_palette([label_of[s] for s in grower_slugs])

    fig, axes = plt.subplots(1, 2, figsize=(11.4, 4.5),
                             gridspec_kw={"width_ratios": [1.0, 1.15]})
    ax = axes[0]
    data, colours = [], []
    for slug in grower_slugs:
        data.append(season.loc[season["grower_slug"] == slug, "gdd"])
        colours.append(palette[label_of[slug]])
    bp = ax.boxplot(data, positions=range(len(grower_slugs)), widths=0.5,
                    patch_artist=True, medianprops=dict(color=ep.INK, lw=1.8),
                    showfliers=False)
    for patch, colour in zip(bp["boxes"], colours):
        patch.set_facecolor(colour)
        patch.set_alpha(0.20)
        patch.set_edgecolor(colour)
    for i, (slug, vals) in enumerate(zip(grower_slugs, data)):
        x = np.full(len(vals), i) + ep.jitter(len(vals), 0.13)
        ax.scatter(x, vals, s=20, color=palette[label_of[slug]], alpha=0.8,
                   edgecolor="white", linewidth=0.4, zorder=3)
    ax.set_xticks(range(len(grower_slugs)))
    ax.set_xticklabels([label_of[s] for s in grower_slugs])
    ax.set_ylabel("growing degree days, May–Sep (heat sum)")
    ax.set_title("One dot per field-year; fields in one grid cell repeat")
    ax.grid(axis="x", visible=False)
    ax2 = axes[1]
    label_shift = {"IA": 10, "IL": -10, "NE": 0}
    for slug in grower_slugs:
        sub = cells[cells["grower_slug"] == slug]
        for _, cell in sub.groupby("power_cell"):
            ax2.plot(cell["year"], cell["gdd"], color=palette[label_of[slug]],
                     lw=1.0, alpha=0.45, marker="o", ms=3, zorder=2)
        mid = sub.groupby("year")["gdd"].median()
        ax2.plot(mid.index, mid, color=palette[label_of[slug]], lw=2.6,
                 marker="o", ms=5, zorder=4)
        # label the bold series at its right end: no legend box over the lines
        code = next(f.state for f in ds.farms if f.grower_slug == slug)
        ax2.annotate(code, xy=(mid.index[-1], mid.iloc[-1]),
                     xytext=(7, label_shift.get(code, 0)),
                     textcoords="offset points", va="center", fontsize=8,
                     fontweight="bold", color=palette[label_of[slug]], zorder=6)
    ax2.set_xticks(sorted(season["year"].unique()))
    ax2.set_xlim(2020.55, 2025.85)
    ax2.set_xlabel("year")
    ax2.set_ylabel("growing degree days, May–Sep")
    ax2.set_title("Thin lines are weather grid cells; bold is the grower median")

    # The three spreads must be measured on comparable units. Year-to-year is
    # the mean of the per-grower annual SDs: pooling growers per year first
    # cancels the grower offsets and understates the swing (~28 vs ~52).
    between = cells.groupby("grower_slug")["gdd"].mean().std()
    within = cells.groupby(["grower_slug", "power_cell"])["gdd"].mean().groupby(
        "grower_slug").std().mean()
    yoy = (cells.groupby(["grower_slug", "year"])["gdd"].mean()
           .groupby("grower_slug").std().mean())
    ep.annotate(
        ax,
        "\n".join([
            "How big are the differences?",
            "(growing degree days, May–Sep)",
            f"between growers: {between:,.0f}",
            f"year to year, same grower: {yoy:,.0f}",
            f"field to field, same grower: {within:,.0f}",
            "7–9 of 10 fields share one grid cell",
        ]),
        x=0.02, y=0.98, ha="left", va="top",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.84))
    ep.title_block(
        fig,
        "Where you farm matters most; year-to-year weather comes next",
        "Growing degree days: growers differ by 128, year to year by 52, "
        "fields within a grower by 37.",
    )
    return ep.save(fig, out_dir / "W2_weather_gdd_by_field.png", _figure_note(ds))


def fig_weather_compare(ds: ed.Dataset, out_dir: Path) -> tuple[Path, pd.DataFrame]:
    overlap = ds.overlap_years
    # Weather is constant inside a POWER cell, so cells (not fields) are the
    # independent sample; field-year counts would fake replication.
    panel = ds.cell_seasonal[ds.cell_seasonal["year"].isin(overlap)].copy()
    grower_slugs = _grower_order(ds)
    label_of = {s: _state_label(ds, s) for s in grower_slugs}
    palette = ep.grower_palette([label_of[s] for s in grower_slugs])
    window = f"{overlap[0]}–{overlap[-1]}"

    fig, axes = plt.subplots(1, 2, figsize=(11.6, 5.0),
                             gridspec_kw={"width_ratios": [1.45, 1.0]})
    ax = axes[0]
    for slug in grower_slugs:
        sel = panel[panel["grower_slug"] == slug]
        ax.scatter(sel["precip_mm"], sel["gdd"], s=74,
                   color=palette[label_of[slug]], edgecolor="white", linewidth=0.7,
                   alpha=0.95, zorder=3)
        ax.annotate(f"{label_of[slug]}  (n={len(sel)})",
                    xy=(sel["precip_mm"].median(), sel["gdd"].median()),
                    xytext=(0, -20), textcoords="offset points", ha="center",
                    fontsize=7.6, fontweight="bold", color=palette[label_of[slug]],
                    zorder=6,
                    bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none",
                              alpha=0.85))
    ax.set_xlabel(f"season rainfall, May–Sep (mm) — {window} overlap")
    ax.set_ylabel("growing degree days, May–Sep (heat sum)")
    ax.set_title("One dot per weather-grid cell, per shared year")
    ax.margins(x=0.12, y=0.12)

    rows = []
    for metric, label in (("gdd", "gdd"), ("precip_mm", "season_precip_mm")):
        groups = {label_of[s]: panel.loc[panel["grower_slug"] == s, metric].tolist()
                  for s in grower_slugs}
        res = ed.kruskal(groups)
        rows.append({
            "comparison": "across growers", "metric": label,
            "groups": "; ".join(f"{k} (n={len(v)})" for k, v in groups.items()),
            "test": res["test"], "statistic": res["statistic"],
            "p_value": res["p_value"], "effect": res["effect"],
            "effect_name": res["effect_name"],
            "note": f"POWER cell-years, overlap window {window}",
        })
    for slug in grower_slugs:
        sel = panel[panel["grower_slug"] == slug]
        res = ed.spearman(sel["precip_mm"], sel["gdd"])
        rows.append({
            "comparison": f"{label_of[slug]} heat vs rain", "metric": "gdd~precip",
            "groups": f"n={len(sel)} cell-years", "test": res["test"],
            "statistic": res["statistic"], "p_value": res["p_value"],
            "effect": res["effect"], "effect_name": res["effect_name"],
            "note": f"overlap window {window}",
        })
    table = pd.DataFrame(rows)

    ax2 = axes[1]
    ax2.axis("off")
    ax2.set_title("What the numbers say", fontsize=9.6, loc="left", pad=2)
    ax2.text(
        0.0, 0.94,
        "\n".join(textwrap.wrap(
            "Growers differ clearly in heat and modestly in rain. Inside a "
            "grower, wetter seasons tend to be cooler, but that pattern is a "
            "hint, not a result.", 46)),
        transform=ax2.transAxes, va="top", ha="left", fontsize=8.6,
        color=ep.INK, linespacing=1.3,
    )
    heat = panel.groupby("grower_slug")["gdd"].mean()
    rain = panel.groupby("grower_slug")["precip_mm"].mean()
    rhos = table.iloc[2:]
    ep.stat_rows(
        ax2,
        [
            ("Heat across growers",
             " · ".join(f"{label_of[s].split(' · ')[0]} {heat[s]:,.0f}"
                        for s in grower_slugs) + "  (growing degree days)",
             f"p {_p_plain(table.iloc[0]['p_value'])}"),
            ("Rain across growers",
             " · ".join(f"{label_of[s].split(' · ')[0]} {rain[s]:,.0f} mm"
                        for s in grower_slugs),
             f"p {_p_plain(table.iloc[1]['p_value'])}"),
            ("Wetter → cooler, within a grower",
             " · ".join(f"{_state_label(ds, s).split(' · ')[0]} "
                        f"{r['effect']:+.2f}".replace("-", "−")
                        for s, (_, r) in zip(grower_slugs, rhos.iterrows()))
             + " (correlation)",
             "none < 0.05"),
        ],
        top=0.66, gap=0.22,
    )
    ax2.text(0.0, 0.02,
             f"One point per weather-grid cell-year ({len(panel)} total), "
             f"{window} overlap; 2–3 cells per grower.\n"
             f"{window} only: Iowa's wet 2024–2025 are excluded, so W1 ranks "
             "it wettest.",
             transform=ax2.transAxes, va="bottom", ha="left", fontsize=7.8,
             color=ep.MUTED, linespacing=1.3)

    fig.subplots_adjust(top=0.76, bottom=0.10, wspace=0.10)
    ep.title_block(
        fig,
        "Growers differ in heat and rain; wetter-cooler is a hint, not a result",
        "Heat separates growers strongly, rain moderately; the wetter-cooler "
        "link is not significant.",
    )
    path = ep.save(fig, out_dir / "W3_weather_compare_gdd_precip.png",
                   _figure_note(ds))
    return path, table




# --------------------------------------------------------------------------
# index + manifest
# --------------------------------------------------------------------------

def write_figure_index(ds: ed.Dataset, entries: list[dict], out_dir: Path) -> Path:
    lines = [
        "# Figures — boundary / crop / weather EDA",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · "
        f"CDL {ds.cdl_years[0]}–{ds.cdl_years[-1]} · "
        f"weather {ds.weather_years[0]}–{ds.weather_years[-1]} · "
        f"crop×weather overlap {ds.overlap_years[0]}–{ds.overlap_years[-1]}",
        "",
        "| file | category | comparison level | story |",
        "|---|---|---|---|",
    ]
    for entry in entries:
        lines.append(f"| `{entry['file']}` | {entry['category']} | {entry['level']} | "
                     f"{entry['story']} |")
    lines += [
        "",
        "## Reading rules",
        "",
        "- **Boundary source is the first split.** USDA CSB and legacy OSM fields are two "
        "populations, so size and shape summaries are always stratified by source; legacy "
        "fields are never pooled with CSB fields.",
        "- **Crop labels come from CDL.** The legacy OSM fields carry `farmland` in the "
        "boundary file; every figure uses the dominant CDL crop instead, with ✱ marking "
        "the legacy rows.",
        "- **Window mismatch.** Crop series use CDL years only, weather series use weather "
        "years only, and joint figures are restricted to the shared window and labelled "
        "with it. Nothing is interpolated or backfilled.",
        "- **Two weather views, two windows.** `W1` uses the full 2021–2025 record and "
        "`W3` uses only the 2021–2023 overlap. Iowa's wet 2024–2025 lift it to wettest "
        "in `W1`; those years are excluded from `W3`, so its rank differs by design.",
        "- **Within-grower weather spread is grid-cell spread.** Fields share a few coarse "
        "weather grid cells (7–9 of 10 fields per grower sit in one cell), so small "
        "within-grower differences are not microclimate evidence.",
        "- **The crop × weather figures are descriptive.** The overlap is three years and "
        "2–3 grid cells per grower; read them as description, not inference.",
        "",
        "### Map exception (M1)",
        "",
        "`M1` is the one figure with no metre tick labels: each panel zooms to the three "
        "closest fields so a 200 ha field is not a speck, and distance is given by a km "
        "scale bar instead. This uses the opt-in `scalebar=True` path that "
        "`data-pipeline/src/scripts/lib/field_frame.py` documents for stripped-axis "
        "panels; the repository figure gate otherwise prefers metre axes and no scale "
        "bar. The inset keeps all ten fields visible, so the exception costs no data.",
        "",
        "Regenerate with `src/run_eda.py` from "
        "`my-farm-advisor/eda/eda-boundary-crop-weather/`; see that folder's `README.md` "
        "and `GUIDE.md`.",
    ]
    path = out_dir / "README.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", default=None,
                    help="defaults to $DATA_PIPELINE_DATA_ROOT/data-pipeline")
    ap.add_argument("--out-dir", default=None,
                    help="defaults to <data-root>/eda/boundary-crop-weather")
    ap.add_argument("--growers", nargs="*", default=None,
                    help="optional grower slug filter")
    ap.add_argument("--dpi", type=int, default=ep.DPI)
    args = ap.parse_args(argv)

    import os

    root = Path(args.data_root or Path(os.environ["DATA_PIPELINE_DATA_ROOT"]) / "data-pipeline")
    out_dir = Path(args.out_dir or root / "eda" / "boundary-crop-weather")
    fig_dir = out_dir / "figures"
    tab_dir = out_dir / "tables"
    fig_dir.mkdir(parents=True, exist_ok=True)
    tab_dir.mkdir(parents=True, exist_ok=True)

    ep.apply_style()
    plt.rcParams["savefig.dpi"] = args.dpi
    plt.rcParams["figure.dpi"] = args.dpi

    only = set(args.growers) if args.growers else None
    ds = ed.build_dataset(root, only)
    geoadmin = root / "shared" / "geoadmin"

    print(f"growers: {[f.grower_slug for f in ds.farms]}")
    print(f"fields: {len(ds.labels)}  weather rows: {len(ds.weather):,}  "
          f"cdl rows: {len(ds.cdl):,}")
    print(f"CDL years {ds.cdl_years}  weather years {ds.weather_years}  "
          f"overlap {ds.overlap_years}")

    labels_path = tab_dir / "T0_field_labels.csv"
    _ordered_labels(ds).to_csv(labels_path, index=False)

    entries: list[dict] = []

    def record(key: str, path: Path, category: str, level: str) -> None:
        size = path.stat().st_size / 1024
        print(f"  {key} {path.name}  ({size:,.0f} KB)")
        entries.append({"file": path.name, "category": category, "level": level,
                        "story": STORIES[key]})

    p, inset_places = fig_map(ds, fig_dir, geoadmin)
    record("M1", p, "Map", "across growers")
    p = fig_boundary_size(ds, fig_dir)
    record("B1", p, "Boundary", "across fields, within + across growers")
    p = fig_boundary_shape(ds, fig_dir)
    record("B2", p, "Boundary", "across fields, within + across growers")
    p, t1 = fig_boundary_compare(ds, fig_dir)
    t1.to_csv(tab_dir / "T1_boundary_metrics_comparison.csv", index=False)
    record("B3", p, "Boundary", "across growers (source-controlled)")

    p = fig_cdl_sequence(ds, fig_dir)
    record("C1", p, "CDL", "within field over time")
    p = fig_cdl_share(ds, fig_dir)
    record("C2", p, "CDL", "across fields, within + across growers")
    p, t2 = fig_cdl_rotation(ds, fig_dir)
    t2.to_csv(tab_dir / "T2_cdl_rotation_comparison.csv", index=False)
    record("C3", p, "CDL", "across fields and growers")

    p = fig_weather_envelope(ds, fig_dir)
    record("W1", p, "Weather", "across fields, within + across growers")
    p = fig_weather_gdd(ds, fig_dir)
    record("W2", p, "Weather", "within field over time + across fields")
    p, t3 = fig_weather_compare(ds, fig_dir)
    t3.to_csv(tab_dir / "T3_weather_compare_gdd_precip.csv", index=False)
    record("W3", p, "Weather", "across growers")

    # ----- evidence used to check every story against the numbers
    b = ds.labels
    csb = b[~b["is_legacy"]]
    legacy = b[b["is_legacy"]]
    dominant = _dominant_cdl(ds)
    ev: list[tuple[str, str, str]] = []

    def add(fig: str, metric: str, value) -> None:
        ev.append((fig, metric, str(value)))

    add("M1", "fields per grower", "; ".join(
        f"{s}={int((b['grower_slug'] == s).sum())}" for s in _grower_order(ds)))
    for slug in _grower_order(ds):
        sub = ds.boundaries[ds.boundaries["grower_slug"] == slug].to_crs(ed.EQUAL_AREA_CRS)
        minx, miny, maxx, maxy = sub.total_bounds
        add("M1", f"full footprint — {_state_label(ds, slug)}",
            f"{(maxx - minx) / 1000:.1f} × {(maxy - miny) / 1000:.1f} km "
            f"(diagonal {math.hypot(maxx - minx, maxy - miny) / 1000:.1f} km)")
        detail_ids = _closest_fields(
            ds.boundaries[ds.boundaries["grower_slug"] == slug], 3)
        det = sub[sub["field_id"].isin(detail_ids)]
        dminx, dminy, dmaxx, dmaxy = det.total_bounds
        add("M1", f"detail window (closest 3) — {_state_label(ds, slug)}",
            f"{max(dmaxx - dminx, dmaxy - dminy) / 1000:.1f} km")
    add("M1", "inset corner (field coverage)",
        "; ".join(f"{_state_label(ds, s)}={v}"
                  for s, v in inset_places.items()))
    add("B1", "area ha min / median / max",
        f"{b['geom_area_ha'].min():.1f} / {b['geom_area_ha'].median():.1f} / "
        f"{b['geom_area_ha'].max():.1f}")
    add("B1", "CSB median ha", f"{csb['geom_area_ha'].median():.1f}")
    add("B1", "legacy median ha", f"{legacy['geom_area_ha'].median():.2f}")
    add("B1", "max/min area ratio",
        f"{b['geom_area_ha'].max() / b['geom_area_ha'].min():.0f}x")
    add("B1", "per-grower CSB median ha", "; ".join(
        f"{_state_label(ds, s)}={csb.loc[csb['grower_slug'] == s, 'geom_area_ha'].median():.0f}"
        for s in _grower_order(ds)))
    add("B2", "CSB median outline roundness",
        f"{csb['polsby_popper_exterior'].median():.2f}")
    add("B2", "legacy median outline roundness",
        f"{legacy['polsby_popper_exterior'].median():.2f}")
    add("B2", "CSB median roundness incl. holes", f"{csb['polsby_popper'].median():.2f}")
    add("B2", "legacy median roundness incl. holes", f"{legacy['polsby_popper'].median():.2f}")
    add("B2", "interior holes CSB mean / max",
        f"{csb['interior_holes'].mean():.1f} / {csb['interior_holes'].max():.0f}")
    add("B2", "interior holes legacy mean / max",
        f"{legacy['interior_holes'].mean():.1f} / {legacy['interior_holes'].max():.0f}")
    add("B3", "CSB area rank-test p", f"{t1.iloc[0]['p_value']:.3f}")
    add("B3", "CSB outline-roundness rank-test p", f"{t1.iloc[1]['p_value']:.3f}")
    csb_by_grower = "; ".join(
        f"{_state_label(ds, s)}="
        f"{csb.loc[csb['grower_slug'] == s, 'polsby_popper_exterior'].median():.2f}"
        for s in _grower_order(ds))
    add("B3", "CSB outline roundness median by grower", csb_by_grower)
    add("C1", "fields with full 5-year sequence",
        int(ds.cdl.groupby("field_id")["year"].nunique().eq(len(ds.cdl_years)).sum()))
    add("C1", "legacy labels resolved by CDL",
        int((legacy["crop_label_source"] == "cdl").sum()))
    add("C2", "corn+soy share of field-years (%)",
        f"{100 * dominant['crop_group'].isin(['Corn', 'Soybeans']).mean():.1f}")
    src = dominant.merge(b[["field_id", "source_label"]], on="field_id", how="left")
    add("C2", "non-corn/soy field-years by source", "; ".join(
        f"{s}={int(((src['source_label'] == s) & ~src['crop_group'].isin(['Corn','Soybeans'])).sum())}"
        for s in ("Legacy OSM", "USDA CSB")))
    add("C3", "corn-years rank-test p", f"{t2.iloc[0]['p_value']:.3f}")
    add("C3", "mean corn years by grower", "; ".join(
        f"{_state_label(ds, s)}={ds.rotation.loc[ds.rotation['grower_slug'] == s, 'corn_years'].mean():.1f}"
        for s in _grower_order(ds)))
    add("C3", "fields by corn years (0/1/2/3/4/5)", "; ".join(
        f"{_state_label(ds, s)}="
        + "/".join(str(int((ds.rotation.loc[ds.rotation['grower_slug'] == s,
                                            'corn_years'] == k).sum()))
                   for k in range(0, len(ds.cdl_years) + 1))
        for s in _grower_order(ds)))
    add("C3", "continuous-corn fields by grower", "; ".join(
        f"{_state_label(ds, s)}={int((ds.rotation.loc[ds.rotation['grower_slug'] == s, 'corn_years'] >= 4).sum())}"
        for s in _grower_order(ds)))
    add("W1", "season precip mean mm by grower", "; ".join(
        f"{_state_label(ds, s)}={ds.seasonal.loc[ds.seasonal['grower_slug'] == s, 'precip_mm'].mean():.0f}"
        for s in _grower_order(ds)))
    add("W1", "May–Sep mean temperature by grower (°C)", "; ".join(
        f"{_state_label(ds, s)}={ds.seasonal.loc[ds.seasonal['grower_slug'] == s, 'tmean_c'].mean():.1f}"
        for s in _grower_order(ds)))
    ia_rain = ds.cell_seasonal[ds.cell_seasonal["grower_slug"] == "ia-story-grower"]
    add("W1", "Iowa May–Sep rainfall by year (mm)", "; ".join(
        f"{int(y)}={v:,.0f}" for y, v in
        ia_rain.groupby("year")["precip_mm"].mean().items()))
    add("W1", "POWER-cell precip range within grower (mm)", "; ".join(
        f"{_state_label(ds, s)}="
        f"{ds.cell_seasonal[ds.cell_seasonal['grower_slug'] == s].groupby('power_cell')['precip_mm'].mean().min():.0f}"
        f"–{ds.cell_seasonal[ds.cell_seasonal['grower_slug'] == s].groupby('power_cell')['precip_mm'].mean().max():.0f}"
        for s in _grower_order(ds)))
    add("W2", "distinct weather grid cells per grower",
        "; ".join(f"{s}={int(v)}" for s, v in ed.n_power_cells(ds.weather).items()))
    add("W2", "fields per weather grid cell",
        "; ".join(f"{s}={v}" for s, v in ed.power_cell_mix(ds.weather).items()))
    add("W2", "growing degree days between-grower SD",
        f"{ds.cell_seasonal.groupby('grower_slug')['gdd'].mean().std():.0f}")
    within_sd = (ds.cell_seasonal.groupby(["grower_slug", "power_cell"])["gdd"].mean()
                 .groupby("grower_slug").std().mean())
    add("W2", "growing degree days field-to-field SD (across grid cells, same grower)",
        f"{within_sd:.0f}")
    yoy_by = (ds.cell_seasonal.groupby(["grower_slug", "year"])["gdd"].mean()
              .groupby("grower_slug").std())
    add("W2", "growing degree days year-to-year SD (mean within grower)", f"{yoy_by.mean():.0f}")
    add("W2", "growing degree days year-to-year SD by grower", "; ".join(
        f"{_state_label(ds, s)}={v:.0f}" for s, v in yoy_by.items()))
    add("W3", "growing degree days rank-test p (overlap)", f"{t3.iloc[0]['p_value']:.3f}")
    add("W3", "precip rank-test p (overlap)", f"{t3.iloc[1]['p_value']:.3f}")
    add("W3", "heat~rain correlation by grower", "; ".join(
        f"{r['comparison'].split(' · ')[0]}={r['effect']:+.2f} "
        f"({ed.fmt_p(r['p_value'])})"
        for _, r in t3.iloc[2:].iterrows()))

    pd.DataFrame(ev, columns=["figure", "metric", "value"]).to_csv(
        tab_dir / "hypothesis_evidence.csv", index=False)

    write_figure_index(ds, entries, fig_dir)

    inputs = []
    for farm in ds.farms:
        path = farm.farm_dir / "boundary" / "field_boundaries.geojson"
        if path.exists():
            inputs.append({"path": str(path.relative_to(root)), "sha256": _sha256(path)})
        for path in sorted((farm.farm_dir / "derived" / "tables").glob("*.csv")):
            if re.search(r"_(cdl|crop_rotation|weather_\d{4}_\d{4})", path.name):
                inputs.append({"path": str(path.relative_to(root)), "sha256": _sha256(path)})

    manifest = {
        "workflow": "eda-boundary-crop-weather",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_root": str(root),
        "output_dir": str(out_dir),
        "growers": [f.grower_slug for f in ds.farms],
        "fields": int(len(ds.labels)),
        "legacy_fields": int(ds.labels["is_legacy"].sum()),
        "cdl_years": ds.cdl_years,
        "weather_years": ds.weather_years,
        "overlap_years": ds.overlap_years,
        "window_rules": {
            "crop": "CDL years only",
            "weather": "weather years only",
            "joint": "shared years only; descriptive, no trend inference",
        },
        "weather_sampling": (
            "fields inside one 0.5 deg x 0.625 deg NASA POWER cell share one "
            "series; cell-year is the independent weather unit and the "
            "fields-per-cell mix is printed on the figures"
        ),
        "gdd_definition": ("daily (min(Tmax,30)+min(Tmin,30))/2 - 10, floored at 0, "
                           "summed May 1 - Sep 30"),
        "crop_label_rule": ("dominant CDL crop in the latest CDL year; boundary "
                            "crop_name only as fallback"),
        "statistics": ("nonparametric (Mann-Whitney U, Kruskal-Wallis, Spearman) "
                       "with effect sizes"),
        "dpi": args.dpi,
        "inputs": inputs,
        "outputs": [f"figures/{e['file']}" for e in entries] + [
            "figures/README.md",
            "tables/T0_field_labels.csv",
            "tables/T1_boundary_metrics_comparison.csv",
            "tables/T2_cdl_rotation_comparison.csv",
            "tables/T3_weather_compare_gdd_precip.csv",
            "tables/hypothesis_evidence.csv",
        ],
    }
    (tab_dir / "eda_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"\nfigures -> {fig_dir}")
    print(f"tables  -> {tab_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
