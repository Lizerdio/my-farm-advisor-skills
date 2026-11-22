"""Shared plotting style for the boundary / crop / weather EDA figures.

Kept deliberately small: one colour map, one save helper, one annotation
helper, plus the title/table/scale-bar helpers the revision added. Charts are
static PNGs sized for a report page, not a dashboard.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.transforms as mtransforms

#: Crop-group colours are fixed (not cycled) so a crop keeps its colour in
#: every figure and in the map legend.
CROP_COLORS = {
    "Corn": "#E69F00",
    "Soybeans": "#56B4E9",
    "Wheat": "#CC79A7",
    "Alfalfa": "#F0E442",
    "Grass/Other": "#009E73",
    "Other": "#9CA3AF",
}

#: Grower accent colours for non-crop figures (Okabe-Ito picks).
GROWER_COLORS = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#56B4E9"]
SOURCE_MARKERS = {"USDA CSB": "o", "Legacy OSM": "X"}

INK = "#111827"
MUTED = "#4B5563"
GRID = "#E5E7EB"

#: Figure width in inches at DPI 150 keeps each PNG near 200-400 KB.
DPI = 150


def apply_style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.edgecolor": MUTED,
            "axes.labelcolor": INK,
            "axes.titlesize": 10.5,
            "axes.titleweight": "bold",
            "axes.labelsize": 9,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.6,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.fontsize": 8,
            "legend.frameon": False,
            "font.size": 8.5,
            "figure.dpi": DPI,
            "savefig.dpi": DPI,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.15,
        }
    )


def grower_palette(labels: list[str]) -> dict[str, str]:
    ordered = list(dict.fromkeys(labels))
    return {label: GROWER_COLORS[i % len(GROWER_COLORS)] for i, label in enumerate(ordered)}


def save(fig, path: Path, note: str | None = None) -> Path:
    """Save with a consistent footer so every PNG states its own provenance.

    The footer sits below the figure box (negative figure y) so tight-bbox
    cropping puts it underneath the axis labels and legends instead of on top
    of them.
    """
    if note:
        fig.text(0.005, -0.02, note, fontsize=6.8, color=MUTED, va="top")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    return path


def annotate(ax, text: str, x: float = 0.985, y: float = 0.985,
             ha: str = "right", va: str = "top") -> None:
    ax.text(
        x, y, text, transform=ax.transAxes, ha=ha, va=va, fontsize=7.2,
        color=INK, linespacing=1.35,
        bbox=dict(boxstyle="round,pad=0.35", fc="white", ec=GRID, lw=0.8, alpha=0.95),
        zorder=20,
    )


def jitter(n: int, width: float = 0.09, seed: int = 7):
    import numpy as np

    rng = np.random.default_rng(seed)
    return rng.uniform(-width, width, n)


def title_block(fig, title: str, subtitle: str | None = None,
                y: float = 0.985, size: float = 11.0,
                gap: float = 0.055, sub_size: float = 8.6) -> None:
    """Centred title plus a smaller plain-English takeaway underneath.

    ``gap`` is in figure fraction, so taller figures (e.g. C1) pass a smaller
    value to keep the subtitle tight under the title.
    """
    fig.text(0.5, y, title, ha="center", va="top", fontsize=size,
             fontweight="bold", color=INK)
    if subtitle:
        fig.text(0.5, y - gap, subtitle, ha="center", va="top",
                 fontsize=sub_size, color=MUTED, style="italic")


def crop_handles(present) -> list:
    """Legend handles for the crop classes that actually occur in a figure."""
    import matplotlib.patches as mpatches

    order = list(present)
    return [mpatches.Patch(fc=CROP_COLORS[c], ec="white", lw=0.6, label=c)
            for c in order]


def scale_bar_km(ax, length_m: float, label: str | None = None,
                 x: float = 0.05, y: float = 0.045,
                 color: str = INK) -> None:
    """Horizontal scale bar in the data units (metres) with a km label.

    Used only on the map panel where the metre tick labels are stripped.
    """
    tr = mtransforms.blended_transform_factory(ax.transData, ax.transAxes)
    lo, hi = ax.get_xlim()
    x0 = lo + x * (hi - lo)
    ax.plot([x0, x0 + length_m], [y, y], transform=tr, color=color, lw=1.8,
            solid_capstyle="butt", zorder=20, clip_on=False)
    for xx in (x0, x0 + length_m):
        ax.plot([xx, xx], [y - 0.012, y + 0.012], transform=tr, color=color,
                lw=1.2, zorder=20, clip_on=False)
    ax.text(x0 + length_m / 2, y + 0.032,
            label or f"{length_m / 1000:g} km", transform=tr, ha="center",
            va="bottom", fontsize=7.4, color=color, zorder=20,
            bbox=dict(boxstyle="square,pad=0.12", fc="white", ec="none",
                      alpha=0.9))


def simple_table(ax, header: list[str], rows: list[list[str]],
                 col_x: tuple = (0.0, 0.46, 0.84), fontsize: float = 8.2,
                 top: float = 0.62, row_gap: float = 0.17,
                 wrap: int = 26) -> None:
    """Small readable table in a blank axes: proportional font, one rule.

    ``col_x`` is in axes fraction. Cells wrap at ~24 characters so a short
    phrase fits without shrinking the font.
    """
    for col, text in zip(col_x, header):
        ax.text(col, top + 0.09, text, transform=ax.transAxes, ha="left",
                va="bottom", fontsize=fontsize, fontweight="bold", color=INK)
    ax.plot([col_x[0], col_x[-1] + 0.14], [top + 0.06, top + 0.06],
            transform=ax.transAxes, color=MUTED, lw=0.8, clip_on=False)
    for r, row in enumerate(rows):
        y = top - r * row_gap
        for col, value in zip(col_x, row):
            ax.text(col, y, "\n".join(textwrap.wrap(str(value), wrap)),
                    transform=ax.transAxes, ha="left", va="top",
                    fontsize=fontsize, color=INK, linespacing=1.25)


def stat_rows(ax, rows: list[tuple[str, str, str]],
              top: float = 0.70, gap: float = 0.235,
              label_size: float = 8.6, value_size: float = 8.0) -> None:
    """Stacked label / value / p blocks for a blank axes.

    Two-column tables kept colliding at report width, so each comparison gets
    a bold label with the p-value right-aligned on the same line and the
    numbers beneath it.
    """
    for i, (label, value, p) in enumerate(rows):
        y = top - i * gap
        ax.text(0.0, y, label, transform=ax.transAxes, ha="left", va="top",
                fontsize=label_size, fontweight="bold", color=INK)
        ax.text(1.0, y, p, transform=ax.transAxes, ha="right", va="top",
                fontsize=label_size, color=INK)
        ax.text(0.0, y - 0.052, value, transform=ax.transAxes, ha="left",
                va="top", fontsize=value_size, color=INK, linespacing=1.25)


