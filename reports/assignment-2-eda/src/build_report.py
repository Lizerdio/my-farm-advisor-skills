#!/usr/bin/env python3
"""Build the one-time Assignment 2 EDA report PDF.

This is an assignment deliverable, not part of the reusable skill. It reads the
ten committed figures in ``reports/assignment-2-eda/figures`` and writes
``reports/assignment-2-eda/assignment-2-eda-report.pdf``. No runtime data is
read at build time.

Usage
-----
    # once, to refresh the committed (downscaled) figure copies from a run
    python src/build_report.py --sync-figures /path/to/eda/boundary-crop-weather/figures

    # build the PDF from the committed figures
    python src/build_report.py

Dependencies (a throwaway venv is fine; this is not the pipeline environment):

    python3 -m venv /tmp/report-venv
    /tmp/report-venv/bin/pip install reportlab pillow
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, KeepTogether, NextPageTemplate, PageBreak,
    PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

REPORT_DIR = Path(__file__).resolve().parent.parent
FIG_DIR = REPORT_DIR / "figures"
OUT_PDF = REPORT_DIR / "assignment-2-eda-report.pdf"

# figure order and the short caption title used under each image
FIG_ORDER = [
    "M1_map_growers_boundaries.png",
    "B1_boundary_size_by_source.png",
    "B2_boundary_shape_vs_size.png",
    "C1_cdl_crop_sequence_heatmap.png",
    "C2_cdl_crop_share_by_grower.png",
    "W1_weather_climate_envelope.png",
    "W2_weather_gdd_by_field.png",
    "B3_boundary_metrics_comparison.png",
    "C3_cdl_rotation_comparison.png",
    "W3_weather_compare_gdd_precip.png",
]

INK = colors.HexColor("#1F2937")
MUTED = colors.HexColor("#4B5563")
LINE = colors.HexColor("#D1D5DB")
SOFT = colors.HexColor("#F3F4F6")
ACCENT = colors.HexColor("#1D4ED8")


# --------------------------------------------------------------------------
# figure preparation
# --------------------------------------------------------------------------

def sync_figures(src: Path) -> None:
    """Copy the runtime figures as small, palette-quantised PNGs.

    The committed copies are for reading in a clone and reviewing the PR; they
    are deliberately not the full-resolution runtime outputs.
    """
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    for name in FIG_ORDER:
        source = src / name
        if not source.exists():
            raise SystemExit(f"missing runtime figure: {source}")
        im = PILImage.open(source).convert("RGB")
        w, h = im.size
        scale = min(1.0, 1100 / max(w, h))
        if scale < 1.0:
            im = im.resize((round(w * scale), round(h * scale)), PILImage.LANCZOS)
        q = im.quantize(colors=224, method=PILImage.MEDIANCUT,
                        dither=PILImage.NONE)
        q.save(FIG_DIR / name, optimize=True)
        print(f"  {name:44s} {q.size[0]}x{q.size[1]}  "
              f"{(FIG_DIR / name).stat().st_size // 1024} KB")


def jpeg_version(name: str, tmp: Path, max_px: int = 1400,
                 quality: int = 84) -> Path:
    """JPEG copy for PDF embedding (small DCT stream, no flate blow-up)."""
    im = PILImage.open(FIG_DIR / name).convert("RGB")
    w, h = im.size
    scale = min(1.0, max_px / max(w, h))
    if scale < 1.0:
        im = im.resize((round(w * scale), round(h * scale)), PILImage.LANCZOS)
    out = tmp / (Path(name).stem + ".jpg")
    im.save(out, "JPEG", quality=quality, optimize=True)
    return out


# --------------------------------------------------------------------------
# styles
# --------------------------------------------------------------------------

def make_styles():
    base = getSampleStyleSheet()
    body = ParagraphStyle(
        "Body", parent=base["BodyText"], fontName="Helvetica", fontSize=9.6,
        leading=13.4, textColor=INK, spaceAfter=5, alignment=TA_LEFT,
    )
    bullet = ParagraphStyle(
        "Bullet", parent=body, leftIndent=13, bulletIndent=3, spaceAfter=3.5,
    )
    h1 = ParagraphStyle(
        "H1", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=13.5,
        leading=16, textColor=INK, spaceBefore=2, spaceAfter=6,
    )
    h2 = ParagraphStyle(
        "H2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=10.5,
        leading=13, textColor=INK, spaceBefore=6, spaceAfter=3,
    )
    title = ParagraphStyle(
        "Title", parent=base["Title"], fontName="Helvetica-Bold", fontSize=22,
        leading=26, textColor=INK, alignment=TA_LEFT, spaceAfter=4,
    )
    subtitle = ParagraphStyle(
        "Subtitle", parent=body, fontSize=11.5, leading=14.5, textColor=MUTED,
        spaceAfter=10,
    )
    caption = ParagraphStyle(
        "Caption", parent=body, fontSize=8.3, leading=10.8, textColor=MUTED,
        spaceBefore=4, spaceAfter=3,
    )
    small = ParagraphStyle(
        "Small", parent=body, fontSize=8.0, leading=10.4, textColor=MUTED,
    )
    cell = ParagraphStyle(
        "Cell", parent=body, fontSize=8.2, leading=10.2, spaceAfter=0,
    )
    cellc = ParagraphStyle("CellC", parent=cell, alignment=TA_CENTER)
    return dict(body=body, bullet=bullet, h1=h1, h2=h2, title=title,
                subtitle=subtitle, caption=caption, small=small, cell=cell,
                cellc=cellc)


# --------------------------------------------------------------------------
# page furniture
# --------------------------------------------------------------------------

def footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(54, 40, doc.pagesize[0] - 54, 40)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 7.4)
    canvas.drawString(54, 27, "Assignment 2  ·  Field boundaries, crop history, "
                              "and weather across three grower groups")
    canvas.drawRightString(doc.pagesize[0] - 54, 27, f"Page {doc.page}")
    canvas.restoreState()


def figure(name: str, width_in: float, tmp: Path, caption: str) -> KeepTogether:
    path = jpeg_version(name, tmp)
    im = PILImage.open(path)
    w, h = im.size
    return KeepTogether([
        Image(str(path), width=width_in * inch, height=width_in * h / w * inch),
        Paragraph(caption, make_styles()["caption"]),
    ])


# --------------------------------------------------------------------------
# document
# --------------------------------------------------------------------------

def build() -> Path:
    st = make_styles()
    tmp = Path(tempfile.mkdtemp(prefix="assignment2-report-"))

    doc = BaseDocTemplate(
        str(OUT_PDF), pagesize=letter,
        leftMargin=54, rightMargin=54, topMargin=54, bottomMargin=54,
        title="Assignment 2 - EDA report",
        author="Elizabeth Giles",
        subject="Field boundaries, crop history, and weather across three grower groups",
    )
    portrait = PageTemplate(
        id="portrait",
        frames=[Frame(54, 54, letter[0] - 108, letter[1] - 108, id="p")],
        onPage=footer,
    )
    land = PageTemplate(
        id="landscape", pagesize=landscape(letter),
        frames=[Frame(40, 46, landscape(letter)[0] - 80,
                      landscape(letter)[1] - 92, id="l")],
        onPage=footer,
    )
    doc.addPageTemplates([portrait, land])

    S: list = []

    # ---------------------------------------------------------------- cover
    S.append(Paragraph("Field boundaries, crop history, and weather "
                       "across three US grower groups", st["title"]))
    S.append(Paragraph("Assignment 2 - exploratory data analysis report  ·  "
                       "22 November 2026", st["subtitle"]))
    S.append(Paragraph("Elizabeth Giles (GitHub: Lizerdio)", st["body"]))
    S.append(Spacer(1, 10))

    S.append(Paragraph(
        "This report compares three ten-field grower groups - one each in Story "
        "County, Iowa; DeKalb County, Illinois; and York County, Nebraska - on "
        "three questions: what their field boundaries are worth as geometry, "
        "what is grown on them, and what weather they experienced. Every figure "
        "and number comes from one run of the EDA workflow in "
        "<i>my-farm-advisor/eda/eda-boundary-crop-weather/</i>, using USDA "
        "Cropland Data Layer (CDL) 2019-2023 and NASA POWER weather 2021-2025.",
        st["body"]))

    S.append(Paragraph("At a glance", st["h2"]))
    bullets = [
        "30 fields, 5,518.6 ha (13,637 ac), three counties. 24 boundaries come "
        "from USDA CSB and 6 are hand-drawn legacy OpenStreetMap fields.",
        "Field size is mostly a data-source fact (USDA median 209 ha vs "
        "hand-drawn 12.0 ha, 17x), while outline roundness is not.",
        "Corn and soybeans fill 96.7% of field-years; the only other crop is a "
        "single legacy pasture field.",
        "Grower identity dominates climate (128 growing degree days of spread "
        "between grower means) over year-to-year weather (52) and "
        "field-to-field spread (37).",
        "Heat separates growers strongly; rain moderately; no within-grower "
        "rain-heat correlation is significant at 2-3 weather cells.",
    ]
    for text in bullets:
        S.append(Paragraph(text, st["bullet"], bulletText="\u2022"))

    S.append(Spacer(1, 8))
    S.append(Paragraph("The three growers", st["h2"]))
    rows = [
        ["Metric", "Iowa - Story", "Illinois - DeKalb", "Nebraska - York"],
        ["Fields (USDA / legacy)", "10 (7 / 3)", "10 (7 / 3)", "10 (10 / 0)"],
        ["Total field area", "1,391.6 ha", "2,095.6 ha", "2,031.4 ha"],
        ["Median field area", "174.9 ha", "257.8 ha", "207.8 ha"],
        ["Mean corn years, 2019-2023", "2.5 of 5", "3.2 of 5", "3.3 of 5"],
        ["Fields with corn in 4-5 years", "1", "4", "5"],
        ["May-Sep rainfall, 2021-2025", "451 mm", "434 mm", "403 mm"],
        ["May-Sep mean temperature", "21.7 \u00b0C", "20.5 \u00b0C", "22.9 \u00b0C"],
        ["Growing degree days, 2021-2025", "1,727", "1,619", "1,875"],
    ]
    data = [[Paragraph(c, st["cell"] if i == 0 else st["cellc"])
             if j else Paragraph(c, st["cell"])
             for j, c in enumerate(r)] for i, r in enumerate(rows)]
    table = Table(data, colWidths=[2.15 * inch, 1.55 * inch, 1.55 * inch,
                                   1.55 * inch], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SOFT),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, LINE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#E5E7EB")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]))
    S.append(table)
    S.append(Spacer(1, 4))
    S.append(Paragraph("All three: 30 fields (24 USDA / 6 legacy), 5,518.6 ha. "
                       "Corn years count the five CDL years; weather means use "
                       "the five weather years.", st["small"]))

    S.append(Paragraph("How the report is organised", st["h2"]))
    for text in [
        "Scope and data first, then the map (M1) and the two source-split "
        "boundary views (B1, B2).",
        "Crops (C1-C3) cover the five-year sequence and how often each field "
        "grows corn; weather (W1-W3) covers the seasonal curve, the three "
        "spreads, and heat with rain.",
        "The last two pages are limitations and what changed after the first "
        "review.",
    ]:
        S.append(Paragraph(text, st["bullet"], bulletText="\u2022"))
    S.append(PageBreak())

    # ------------------------------------------------------- scope and data
    S.append(Paragraph("Scope, data, and comparison design", st["h1"]))
    S.append(Paragraph(
        "The three grower groups are ten-field sets assembled from public field "
        "boundaries, not cooperating producers. Story and DeKalb mix USDA CSB "
        "fields with hand-drawn OpenStreetMap boundaries; York is entirely USDA. "
        "The groups sit in three Corn Belt counties with different climates and "
        "field-size structures, which is the point of the comparison.",
        st["body"]))

    S.append(Paragraph("Why York County, Nebraska", st["h2"]))
    S.append(Paragraph(
        "York was chosen as the third grower to put a western, "
        "center-pivot-irrigated corn-soybean farm alongside the two eastern "
        "rainfed farms; the pipeline farm is named \"York Center-Pivot Farm\". "
        "It also has no legacy OSM fields, so all ten of its boundaries are USDA "
        "CSB - exactly the contrast the source-stratified boundary figures need. "
        "No irrigation data itself is analysed here; the setting is context, not "
        "a variable.",
        st["body"]))

    S.append(Paragraph("Data layers", st["h2"]))
    layers = [
        "<b>Field boundaries</b> - 24 USDA CSB polygons (FIBOA GeoParquet, "
        "pruned by row-group statistics rather than downloaded whole) and 6 "
        "legacy OSM <i>landuse=farmland</i> polygons from Overpass.",
        "<b>Crops</b> - USDA NASS CDL 2019-2023 at 30 m; each field-year is the "
        "dominant CDL class inside the boundary.",
        "<b>Weather</b> - NASA POWER on a 0.5\u00b0 x 0.625\u00b0 grid, daily "
        "2021-2025, reduced to May-Sep rainfall and growing degree days (base "
        "10 \u00b0C, capped at 30 \u00b0C).",
        "<b>Geography</b> - county and state polygons for the locator.",
    ]
    for text in layers:
        S.append(Paragraph(text, st["bullet"], bulletText="\u2022"))

    S.append(Paragraph("Comparison levels", st["h2"]))
    level_rows = [
        ["Level", "Question", "Figures"],
        ["Within field over time", "Does one field's crop or heat history move?",
         "C1, W2"],
        ["Across fields within a grower",
         "How much do fields in the same county differ?", "B1, B2, C2, W1"],
        ["Across growers",
         "Does the grower still matter once boundary source is fixed?",
         "M1, B3, C3, W3"],
    ]
    lvl = Table([[Paragraph(c, st["cell"]) for c in r] for r in level_rows],
                colWidths=[1.75 * inch, 3.7 * inch, 1.35 * inch], hAlign="LEFT")
    lvl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SOFT),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, LINE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#E5E7EB")),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    S.append(lvl)

    S.append(Paragraph("Windows and tests", st["h2"]))
    S.append(Paragraph(
        "Crop figures use CDL years only, weather figures use weather years only, "
        "and joint figures are restricted to the shared 2021-2023 window and "
        "labelled with it. Nothing is interpolated or backfilled. Tests are "
        "nonparametric - Mann-Whitney U, Kruskal-Wallis, Spearman - and every "
        "result reports n and an effect size on a 0-1 scale; the three-year "
        "overlap is descriptive, not a trend claim.",
        st["body"]))

    S.append(Paragraph("Figure index", st["h2"]))
    fig_rows = [
        ["Figure", "What it shows", "Section"],
        ["1 - M1", "The three grower groups on a field-scale map", "Map"],
        ["2 - B1", "Field size by boundary source (log axis)", "Boundaries"],
        ["3 - B2", "Outline roundness vs size; interior holes", "Boundaries"],
        ["4 - C1", "Dominant crop per field and year, 2019-2023", "Crops"],
        ["5 - C2", "Crop mix by grower and year", "Crops"],
        ["6 - W1", "Monthly temperature and precipitation, 2021-2025",
         "Weather"],
        ["7 - W2", "Growing degree days per field and per weather cell",
         "Weather"],
        ["8 - B3", "Size and roundness by grower, source-controlled",
         "Comparisons"],
        ["9 - C3", "Fields by number of corn years, 2019-2023", "Comparisons"],
        ["10 - W3", "Heat against rain, one point per cell-year",
         "Comparisons"],
    ]
    fg = Table([[Paragraph(c, st["cell"]) for c in r] for r in fig_rows],
               colWidths=[0.95 * inch, 3.9 * inch, 1.2 * inch], hAlign="LEFT")
    fg.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SOFT),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, LINE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#E5E7EB")),
        ("TOPPADDING", (0, 0), (-1, -1), 2.4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    S.append(fg)
    S.append(NextPageTemplate("landscape"))
    S.append(PageBreak())

    # ---------------------------------------------------------------- map
    S.append(Paragraph("The map: where the fields are", st["h1"]))
    S.append(Paragraph(
        "Each grower's ten fields span 34-54 km at their widest across one "
        "county rather than forming one contiguous farm, so a panel showing all "
        "ten would shrink a 200 ha field to a speck. Each panel therefore zooms to "
        "the three closest fields (6.7-8.0 km across) and reports distance with "
        "a km scale bar; the corner inset keeps all ten fields visible and boxes "
        "the detail window. Fill is the dominant CDL 2023 crop, rings mark "
        "legacy OSM fields, and the locator places the three study states.",
        st["body"]))
    S.append(figure(
        "M1_map_growers_boundaries.png", 9.4, tmp,
        "<b>Figure 1 - M1.</b> Three field-scale panels plus an all-fields inset "
        "and a lower-48 locator. Story and DeKalb each include three hand-drawn "
        "legacy fields; York is all USDA."))
    for text in [
        "<b>Panel</b> - shape and neighbours of the three closest fields; "
        "fill is the dominant CDL 2023 crop and a km scale bar carries distance.",
        "<b>Inset</b> - all ten fields with the detail window boxed, placed in "
        "the emptiest corner so it never covers a field.",
        "<b>Locator</b> - the lower-48 states with the three study states "
        "filled in the grower colour and a white dot at each cluster centroid.",
        "Legacy fields are 1.1-48 ha and would be sub-pixel even at this zoom, "
        "so they are ringed rather than shown as solid shapes.",
    ]:
        S.append(Paragraph(text, st["bullet"], bulletText="\u2022"))

    frame_rows = [
        ["Grower", "Ten-field footprint", "Detail window", "Inset corner"],
        ["IA - Story", "27.6 x 33.9 km", "6.7 km", "bottom-left"],
        ["IL - DeKalb", "27.1 x 53.5 km", "8.0 km", "top-left"],
        ["NE - York", "36.8 x 37.5 km", "8.0 km", "bottom-left"],
    ]
    fr = Table([[Paragraph(c, st["cell"]) for c in r] for r in frame_rows],
               colWidths=[1.35 * inch, 1.8 * inch, 1.3 * inch, 1.35 * inch],
               hAlign="LEFT")
    fr.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SOFT),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, LINE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#E5E7EB")),
        ("TOPPADDING", (0, 0), (-1, -1), 3.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    S.append(Spacer(1, 4))
    S.append(fr)
    S.append(NextPageTemplate("portrait"))
    S.append(PageBreak())

    # ----------------------------------------------------------- boundaries
    S.append(Paragraph("Boundaries: two sources, one size story", st["h1"]))
    S.append(Paragraph(
        "Boundary source is the first split in every size or shape summary. The "
        "two populations are an order of magnitude apart, and the grower shifts "
        "size again - but shape behaves differently.",
        st["body"]))
    S.append(figure(
        "B1_boundary_size_by_source.png", 6.2, tmp,
        "<b>Figure 2 - B1.</b> Field area by boundary source on a log axis. "
        "USDA median 209 ha vs hand-drawn 12.0 ha (17x; effect size 0.74). "
        "Within USDA the grower still moves the median: Iowa 180, Nebraska 208, "
        "Illinois 270 ha."))
    S.append(Paragraph(
        "A raw, pooled size comparison across these growers would confuse "
        "boundary source with geography, which is why no figure pools them.",
        st["body"]))
    S.append(figure(
        "B2_boundary_shape_vs_size.png", 6.2, tmp,
        "<b>Figure 3 - B2.</b> Outline roundness (1 = perfect circle) against "
        "field size, and interior holes by source. Hand-drawn fields are if "
        "anything rounder (median 0.56 vs 0.45); size barely predicts roundness "
        "(r = -0.20, p = 0.35)."))
    S.append(Paragraph(
        "The real signature is digitisation detail: USDA polygons carry interior "
        "holes from waterways and farmsteads (mean 1.4 per field, up to 6), while "
        "hand-drawn fields carry none.",
        st["body"]))
    S.append(PageBreak())

    # --------------------------------------------------------------- crops
    S.append(Paragraph("Crops: a five-year sequence on every field", st["h1"]))
    S.append(Paragraph(
        "CDL resolves a crop for all 30 fields in all five years, including the "
        "six boundaries whose own label was simply \"farmland\". That is the "
        "key assumption in this section: the boundary label is not a crop, so "
        "the dominant CDL class is used instead and the original label is kept "
        "in the audit table.",
        st["body"]))
    S.append(figure(
        "C1_cdl_crop_sequence_heatmap.png", 4.2, tmp,
        "<b>Figure 4 - C1.</b> Dominant CDL crop per field and year, naturally "
        "sorted (r1 to r10). Italic rows marked * are legacy OSM fields. Five of "
        "the six follow a corn-soy rotation; one DeKalb legacy field is "
        "continuous grass."))
    S.append(figure(
        "C2_cdl_crop_share_by_grower.png", 5.2, tmp,
        "<b>Figure 5 - C2.</b> Crop mix by grower and year, as a share of "
        "fields. Corn and soybeans take 96.7% of all field-years; every other "
        "field-year is the one hand-drawn pasture. No USDA field is ever "
        "anything but corn or soybean."))
    S.append(PageBreak())

    # -------------------------------------------------------------- weather
    S.append(Paragraph("Weather: one curve, three offsets", st["h1"]))
    S.append(Paragraph(
        "Weather is sampled on a coarse grid, not per field: fields inside one "
        "0.5\u00b0 cell share an identical series, so the cell-year - not the "
        "field-year - is the independent unit. The figures print the "
        "fields-per-cell mix next to any within-grower claim. W1 uses the full "
        "2021-2025 record; W3 uses only the 2021-2023 crop overlap, which "
        "excludes Iowa's two wet years - that is why the two rank Iowa "
        "differently.",
        st["body"]))
    S.append(figure(
        "W1_weather_climate_envelope.png", 6.3, tmp,
        "<b>Figure 6 - W1.</b> Monthly temperature and precipitation, with thin "
        "lines for individual weather cells and bold lines for the grower "
        "median. The seasonal curves share one shape but sit about 2.4 \u00b0C "
        "apart; over 2021-2025 Nebraska is driest (403 mm) and Iowa wettest "
        "(451 mm)."))
    S.append(figure(
        "W2_weather_gdd_by_field.png", 6.3, tmp,
        "<b>Figure 7 - W2.</b> Growing degree days per field-year (left) and per "
        "weather cell across years (right). The three spreads behind the story: "
        "128 between growers, 52 year-to-year within a grower, 37 "
        "field-to-field."))
    S.append(Paragraph(
        "The ordering is the finding: after the grower, the year matters more "
        "than which field inside the same weather cell you farm. The year-to-year "
        "value is the mean of each grower's own annual standard deviation; "
        "pooling growers by year first cancels their offsets and understates the "
        "swing.",
        st["body"]))
    S.append(PageBreak())

    # --------------------------------------------------------- comparisons
    S.append(Paragraph("Comparisons: what survives once source is controlled",
                       st["h1"]))
    S.append(figure(
        "B3_boundary_metrics_comparison.png", 6.3, tmp,
        "<b>Figure 8 - B3.</b> Field size by grower and boundary source, with "
        "the source-controlled tests on the right. Size still differs by grower "
        "(Iowa 180, Nebraska 208, Illinois 270 ha; p < 0.001, effect 0.65) but "
        "outline roundness does not (p = 0.58)."))
    S.append(figure(
        "C3_cdl_rotation_comparison.png", 6.0, tmp,
        "<b>Figure 9 - C3.</b> Number of fields per grower that grew corn in 0 "
        "to 5 of the five CDL years. Means are 2.5 (IA), 3.2 (IL) and 3.3 (NE); "
        "fields with corn in four or five years are 1, 4 and 5. The rank test "
        "across growers gives p = 0.24."))
    S.append(Paragraph(
        "Iowa plants corn less often, but ten fields per grower is too few to "
        "call that a grower effect. That is why the chart reports counts and the "
        "mean, and why the test is shown rather than a verdict.",
        st["body"]))
    S.append(PageBreak())

    # ------------------------------------------------------------------ W3
    S.append(Paragraph("Heat and rain together", st["h1"]))
    S.append(figure(
        "W3_weather_compare_gdd_precip.png", 6.3, tmp,
        "<b>Figure 10 - W3.</b> One point per weather-grid cell-year in the "
        "2021-2023 overlap. Heat separates growers strongly (p < 0.001, effect "
        "0.86), rain moderately (p = 0.015, effect 0.35); within a grower, "
        "wetter seasons trend cooler but no correlation is significant."))
    S.append(Paragraph(
        "Inside each grower the rain-heat correlations are -0.54 (Iowa), -0.26 "
        "(Illinois) and -0.53 (Nebraska), none significant with 2-3 cells per "
        "grower. Treat the direction as a hypothesis for a longer record, not a "
        "result.",
        st["body"]))
    S.append(Paragraph("Reading the three comparisons together", st["h2"]))
    for text in [
        "Boundary source changes field size by an order of magnitude, but not "
        "shape; source-controlled comparisons are the only fair regional ones.",
        "Within a grower, year-to-year weather (52 GDD) outweighs field-to-field "
        "spread (37 GDD) because neighbouring fields share a weather cell.",
        "Across growers, heat is the strong, repeatable difference; rain and "
        "rotation differ in direction or significance depending on the window.",
        "Iowa ranks wettest in W1 (2021-2025) and driest in W3 (2021-2023); both "
        "are correct for their windows, and both figures now say so.",
    ]:
        S.append(Paragraph(text, st["bullet"], bulletText="\u2022"))
    S.append(Paragraph("Grower summary", st["h2"]))
    S.append(Paragraph(
        "One line per group, for a reader who wants the practical version rather "
        "than the tests.", st["body"]))
    grower_rows = [
        ["Grower", "What stands out", "What to check before acting on it"],
        ["IA - Story",
         "Coolest and wettest over 2021-2025; corn in 2.5 of 5 years, the "
         "lowest of the three.",
         "The rain rank depends on the window: 2021-2023 alone puts Iowa "
         "third, so do not treat it as the wet site on five years alone."],
        ["IL - DeKalb",
         "Largest fields (median 258 ha) and the most corn after Nebraska "
         "(3.2 of 5 years).",
         "One legacy field has been grass for five years; check that it is "
         "meant to be in the group before averaging the rotation."],
        ["NE - York",
         "Hottest and driest, all ten boundaries USDA, corn in 3.3 of 5 "
         "years.",
         "Heat separates cleanly, but irrigation is not in the data, so a "
         "yield or rotation difference cannot be attributed to it here."],
    ]
    gt = Table([[Paragraph(c, st["cell"]) for c in r] for r in grower_rows],
               colWidths=[0.95 * inch, 2.55 * inch, 2.5 * inch], hAlign="LEFT")
    gt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SOFT),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, LINE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#E5E7EB")),
        ("TOPPADDING", (0, 0), (-1, -1), 3.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    S.append(gt)
    S.append(PageBreak())

    # ---------------------------------------------------------- limitations
    S.append(Paragraph("Limitations and assumptions", st["h1"]))
    S.append(Paragraph(
        "Each figure is paired with an evidence row in the run, and the "
        "limitations below are the ones that change how a number should be "
        "read. A caveat that does not move a reading is not listed just to look "
        "thorough.", st["body"]))
    limitations = [
        "<b>Two boundary sources.</b> 24 fields are USDA CSB and 6 are legacy "
        "OSM; they are never pooled in a size or shape summary. OSM farmland "
        "coverage is sparse in the US Corn Belt, which is why CSB is the primary "
        "source.",
        "<b>\"Farmland\" is not a crop.</b> The legacy boundary label is "
        "resolved with the dominant CDL class; the original label stays in the "
        "audit table (T0). A dominant class can hide field-scale strips or "
        "mixtures.",
        "<b>Year mismatch.</b> CDL covers 2019-2023 and weather 2021-2025. Any "
        "joint statement uses only 2021-2023, three years, and is labelled "
        "descriptive. No trend is claimed.",
        "<b>Coarse weather cells.</b> NASA POWER cells are 0.5\u00b0 by "
        "0.625\u00b0; each grower has 2-3 cells and 7-9 of its 10 fields share "
        "one. Within-grower weather differences are grid artefacts, not "
        "microclimate.",
        "<b>Small samples.</b> Ten fields per grower, 7-10 USDA fields per "
        "group, and 21 weather cell-years in the overlap. Tests are "
        "nonparametric with effect sizes; a p-value near 0.05 is not treated as "
        "a finding.",
        "<b>Public boundaries, assembled groups.</b> The three grower groups are "
        "ten-field sets drawn from public USDA and OSM polygons, not cooperating "
        "producers; field identity is public data, not a farm business record.",
        "<b>Download workarounds.</b> Overpass saturates: the ingest rotates six "
        "mirrors, sends a mandatory User-Agent, encodes the query as "
        "<i>data=</i>, backs off 40-60 s, and journals each response hash so a "
        "rerun can replay it. CSB is pruned with pyarrow row-group statistics "
        "instead of a 16-million-field download. CDL is fetched as cached "
        "by-FIPS GeoTIFFs; 2023 is the latest complete year because 2024 "
        "publishes after the cut-off.",
        "<b>No soil, terrain, or satellite input.</b> Soil was not required for "
        "this assignment and is not the focus; no soil layer is used in any "
        "figure.",
    ]
    for text in limitations:
        S.append(Paragraph(text, st["bullet"], bulletText="\u2022"))

    S.append(Paragraph("What would change these readings", st["h2"]))
    for text in [
        "<b>Field management records</b> (planting date, hybrid, irrigation) "
        "would let rotation and heat be tied to outcomes instead of to "
        "boundary source.",
        "<b>On-farm weather stations or a finer grid</b> would turn the two or "
        "three coarse cells per grower into real within-grower variation.",
        "<b>CDL 2024-2025</b> would extend the joint window past three years "
        "and let the rain rank be tested on the same years W1 uses.",
    ]:
        S.append(Paragraph(text, st["bullet"], bulletText="\u2022"))
    S.append(PageBreak())

    # -------------------------------------------------------- revision loop
    S.append(Paragraph("What changed after the first review", st["h1"]))
    S.append(Paragraph(
        "The first version was treated as a draft: each figure was reviewed, "
        "checked against its evidence row, and rebuilt where the picture or the "
        "wording did not survive the check.",
        st["body"]))
    changes = [
        "<b>W2 year-to-year spread was wrong.</b> The first version pooled "
        "growers by year before taking the standard deviation, which cancels the "
        "grower offsets and understated the swing (28). The corrected value "
        "averages each grower's own annual SD (52), and the order became 128 > "
        "52 > 37. The title, annotation and figure index were corrected.",
        "<b>Precipitation was double-counted.</b> Seasonal totals were summed "
        "after collapsing fields to cells, which adds one month of rain once per "
        "field. The fix sums each field's monthly series first, then averages "
        "within the cell; W1 and W3 numbers were regenerated.",
        "<b>The map showed specks.</b> Panels covering all ten fields could not "
        "make a 200 ha field visible. They now zoom to the three closest fields, "
        "add a km scale bar and a small north arrow, keep all ten in an inset, "
        "and drop the metre tick labels.",
        "<b>C3 became readable.</b> The overlapping corn-vs-soybean scatter was "
        "replaced by grouped bars of fields by 0-5 corn years, with counts "
        "printed and the single pasture field annotated so it cannot hide.",
        "<b>Jargon and clutter were removed.</b> Polsby-Popper became "
        "\"roundness (1 = circle)\", epsilon-squared and Cliff's delta became "
        "\"effect size (0-1)\", POWER cells became \"weather grid cells\", "
        "\"rho\" became \"correlation\", and GDD became \"growing degree "
        "days\". Legends now list only crops that occur, Nebraska fields sort "
        "naturally (r1 to r10), and every figure carries a plain-language "
        "takeaway under its title.",
    ]
    for text in changes:
        S.append(Paragraph(text, st["bullet"], bulletText="\u2022"))

    S.append(Paragraph("Reproducing this report", st["h2"]))
    S.append(Paragraph(
        "The figures come from "
        "<i>my-farm-advisor/eda/eda-boundary-crop-weather/src/run_eda.py</i>, "
        "which reads the runtime data tree and writes figures, tables, a figure "
        "index, an evidence CSV and a manifest under "
        "<i>${DATA_PIPELINE_DATA_ROOT}/data-pipeline/eda/boundary-crop-weather/</i>. "
        "The runtime data itself is not committed. The report embeds downscaled "
        "copies of the final figures and is built by "
        "<i>reports/assignment-2-eda/src/build_report.py</i>.",
        st["body"]))
    S.append(Paragraph(
        "<b>Soil.</b> Soil analysis was not required for this assignment and is "
        "not the focus of this report; no soil data is used anywhere in the ten "
        "figures.",
        st["body"]))
    S.append(Paragraph(
        "<b>Data credits.</b> Boundaries: USDA CSB via the FIBOA hosted "
        "GeoParquet (CC-BY-4.0), plus OpenStreetMap contributors via Overpass "
        "for the six legacy fields. Crops: USDA NASS Cropland Data Layer. "
        "Weather: NASA POWER. Locator: Natural Earth countries and Census "
        "TIGER/Line 2025 states, fetched through this repository's geoadmin "
        "metadata.",
        st["body"]))

    S.append(Paragraph("Where I would take this next", st["h2"]))
    S.append(Paragraph(
        "If I had another pass, I would rather fix the data than draw another "
        "figure: replace the six legacy fields with USDA polygons so field size "
        "is single-source, and add CDL 2024-2025 so the joint window is five "
        "years. Until then I would read the heat ranking as the robust regional "
        "difference and the rain ranking as window-dependent.",
        st["body"]))

    doc.build(S)
    return OUT_PDF


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sync-figures", type=Path, metavar="DIR",
                    help="refresh committed figures from a runtime figures dir")
    ap.add_argument("--report-only", action="store_true",
                    help="skip figure sync (default when --sync-figures absent)")
    args = ap.parse_args()
    if args.sync_figures:
        sync_figures(args.sync_figures)
    out = build()
    size = out.stat().st_size
    print(f"wrote {out}  ({size / 1024:,.0f} KB, {size / 1_000_000:.2f} MB)")
    if size > 1_000_000:
        raise SystemExit("report PDF exceeds the 1 MB repository asset cap")


if __name__ == "__main__":
    main()
