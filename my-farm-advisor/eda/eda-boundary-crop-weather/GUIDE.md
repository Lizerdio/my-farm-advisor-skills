# Guide — boundary / crop / weather EDA

How to read the ten figures, what each test does, and where to change things.
Run the workflow with `src/run_eda.py` (see `README.md`); outputs land in
`${DATA_PIPELINE_DATA_ROOT}/data-pipeline/eda/boundary-crop-weather/`.

## Reading order

1. `M1` — where the fields are. Fill is the CDL 2023 dominant crop; solid fill
   is USDA CSB, dashed outline + ring is a legacy OSM field.
2. `B1`–`B3` — what the boundaries are worth as geometries.
3. `C1`–`C3` — what is actually grown on them.
4. `W1`–`W3` — what the climate is doing over them.

## Figure notes

### M1 — map

Three panels, one per grower. Each panel is a *field-scale* close-up: it frames
only the three closest fields (6.7–8.0 km across) so a 200 ha field fills a
readable share of the panel, and it carries a km scale bar instead of metre
tick labels (the documented `scalebar=True` exception in
`data-pipeline/src/scripts/lib/field_frame.py`, `force_rot=0.0`, small north
arrow). A corner inset shows all ten fields with the detail window boxed, so
the zoom hides nothing; the inset is scored against the drawn fields and sits
in the emptiest corner, so it never covers one. The locator is the lower-48 states in Albers
(`EPSG:5070`) with the three study states filled in the grower colour and a
white dot at each cluster centroid.

Legacy OSM fields are 1.1–48 ha and would be sub-pixel even at this zoom; they
are circled rather than relied on as visible polygons. The rings are the point.
Read the panel for shape and neighbours, the inset for layout, the locator for
where in the country.

### B1 — size by source

Stratified by source on purpose. The two populations are 17× apart in median
(USDA 209 ha, hand-drawn legacy 12 ha; effect size +0.74 on a 0–1 scale), so a
pooled histogram would just show two humps. Median ticks and per-grower medians
are annotated; means are never used.

### B2 — shape vs size

Two roundness values are computed in `lib/eda_data.py`: the full polygon and
the outer outline only (`polsby_popper` / `polsby_popper_exterior`; 1 = perfect
circle). The full-polygon value punishes USDA fields for the waterways and
farmstead holes their digitisation includes, which is why the figure plots the
outline value — the fair comparison across sources. The right panel counts
interior holes directly; that is the real source signature.

### B3 — source-controlled comparison

Left: size on a log axis with source offsets and medians. Right: a one-sentence
takeaway plus three plain rows (comparison, the numbers, p). The full test
statistic, effect size and group sizes stay in `T1`. When a p-value and an
effect size disagree in emphasis, trust the effect size at n≈7–10 per group.

### C1 — crop sequence

One row per field, one column per CDL year, dominant crop (max `pct`) per cell.
Italic ✱ rows are legacy OSM fields. Every cell is labelled, so the heatmap
doubles as a table.

### C2 — crop mix

Share of fields whose dominant CDL crop is each class, per grower-year. Bars are
100% stacked, so read composition, not area.

### C3 — rotation intensity

Grouped bars: for each grower, how many of its ten fields grew corn in 0, 1, 2,
3, 4 or 5 of the five CDL years. A strict corn/soy rotation piles up at 2–3;
continuous corn sits at 4–5; the single 0-corn field is the legacy pasture and
is labelled so it cannot hide. Bar counts are printed, the legend gives each
grower's mean, and the rank test across growers (p=0.24) is shown in the corner
— ten fields per grower is too few to call a grower effect.

### W1 — climate envelope

Bold line = grower median across its NASA POWER cells; thin lines = individual
cells. If the thin lines sit on top of the bold one, within-grower climate
variation is below grid resolution, not absent. The footer prints seasonal
precipitation per grower with the cell range and fields-per-cell mix.

### W2 — GDD distribution and series

Left: field-year distribution (30 points per grower) — fields in the same cell
are the same series, so treat it as a shape summary, not 30 independent samples.
Right: one line per weather grid cell over the five weather years, bold = grower
median, labelled at the right edge.

`W1` averages the full 2021–2025 weather record; `W3` uses only the 2021–2023
overlap, so Iowa ranks wettest in `W1` but driest there — both figures say so.

The annotation gives the three spreads the story rests on, all in growing degree
days: between growers (SD of grower means, 128), year-to-year within a grower
(mean of each grower's own annual SD, 52), and field-to-field within a grower
(SD of cell means, 37). The year-to-year value must be computed per grower:
pooling growers per year first cancels their offsets and understates the swing
(28).

### W3 — GDD vs precipitation

One point per weather-grid cell-year inside the 2021–2023 overlap (n=21).
Colours and labels identify growers; the right-hand panel gives a takeaway plus
plain rows for the across-grower rank tests (heat p<0.001, rain p=0.015) and
the per-grower rain–heat correlations (IA −0.54, IL −0.26, NE −0.53, none
significant). With 2–3 cells per grower this is description, not inference.

## Statistics used

| purpose | test | effect size | helper |
|---|---|---|---|
| two groups | Mann-Whitney U (rank test) | Cliff's delta, shown as effect size 0–1 | `ed.mann_whitney` |
| three+ groups | Kruskal-Wallis (rank test) | epsilon-squared, shown as effect size 0–1 | `ed.kruskal` |
| monotone association | Spearman rho, shown as correlation r | r | `ed.spearman` |

Groups smaller than 3 fall back to descriptive output instead of a test. Nothing
is tested on pooled CSB + legacy fields, and nothing is tested across the
2019–2023 / 2021–2025 window boundary.

## GDD definition

Daily `(min(Tmax,30) + min(Tmin,30))/2 − 10`, floored at 0, summed 1 May – 30
Sep. Recorded in `tables/eda_manifest.json` so a later report can restate it.

## Changing things

- **Add a grower**: run the data pipeline for that grower and add its farm
  directory under `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<slug>/farms/`;
  the workflow discovers farms and derives colour/labels automatically. The map
  and locator handle any number of panels ≥ 1.
- **Change the window**: `CDL_RE` / the weather table glob in `lib/eda_data.py`
  select the years; the overlap is computed, never hardcoded.
- **Crop classes**: `CROP_ORDER` and `CROP_COLORS` (in `lib/eda_data.py` and
  `lib/eda_plots.py`) define the grouping. Keep them in sync; a crop missing
  from `CROP_ORDER` is bucketed into `Other` and appears grey.
- **Re-check the stories**: after any change, compare the run against
  `tables/hypothesis_evidence.csv` and rewrite `STORIES` in `src/run_eda.py`
  before trusting `figures/README.md`.

## Gotchas

- **Precipitation must be summed per field, then averaged per cell.** Summing
  after collapsing to the cell adds one month of rain once per field and
  inflates the numbers by the field count. `cell_monthly_climatology` does it in
  the safe order; keep it that way.
- **`field_frame.py` is loaded by path**, not imported, because
  `data-pipeline/src/scripts/lib` and this workflow's `src/lib` share the name
  `lib`. A plain `import` resolves to the wrong package.
- **`bbox_inches="tight"` and footers**: the shared `save()` puts the provenance
  footer at a negative figure y so cropping lands it under the axis labels and
  legends. Legends placed at negative figure y will collide with it.
- **A 1 ha field in a 60 km panel is invisible.** That is why `M1` zooms to the
  three closest fields (with an all-ten inset), why legacy fields are ringed, and
  why size figures use a log axis.

## Review checklist before quoting a figure

1. Does the printed story match the numbers on the figure and in
   `hypothesis_evidence.csv`?
2. Are legacy and CSB fields still distinguishable (marker, dash, or ✱)?
3. Is the window label present on every joint figure?
4. Are n and the effect size visible, not just p?
5. At README width, are the axis labels and annotation boxes legible and clear
   of data?
