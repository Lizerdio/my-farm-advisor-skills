# Boundary / crop / weather EDA — three growers

Field-level exploratory analysis of real field boundaries, CDL crop history, and
NASA POWER weather for three US growers. It is the first runnable workflow under
`eda/`: ten static figures plus tidy tables that a later report can cite.

| grower | state · county | fields | USDA CSB | legacy OSM | area |
|---|---|---|---|---|---|
| `il-dekalb-grower` | IL · DeKalb | 10 | 7 | 3 | 2,095.6 ha (5,178 ac) |
| `ia-story-grower` | IA · Story | 10 | 7 | 3 | 1,391.6 ha (3,439 ac) |
| `ne-york-grower` | NE · York | 10 | 10 | 0 | 2,031.4 ha (5,020 ac) |

Totals: 30 fields, 5,518.6 ha (13,637 ac). Analysis window: CDL 2019–2023,
weather 2021–2025, crop × weather overlap 2021–2023.

## What it compares

| level | figures |
|---|---|
| within field over time | `C1` crop sequence, `W2` GDD series |
| across fields within a grower | `B1`, `B2`, `C2`, `W1` |
| across growers | `M1`, `B3`, `C3`, `W3` |

Each category (boundaries, CDL, weather) gets two statistical views and one
comparison/correlation view, plus one map that shows all three growers' real
boundaries with a locator.

## Run it

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  my-farm-advisor/eda/eda-boundary-crop-weather/src/run_eda.py
```

Optional flags: `--data-root`, `--out-dir`, `--growers <slug> [...]`, `--dpi`.
Run from the checkout with the runtime venv; the script reads the runtime tree
and writes only there.

## Outputs

Written to `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/eda/boundary-crop-weather/`:

| file | what it is |
|---|---|
| `figures/M1_map_growers_boundaries.png` | three field-scale grower panels (three closest fields) + all-ten inset + lower-48 locator, CDL 2023 crop fill |
| `figures/B1_boundary_size_by_source.png` | field size by boundary source, log axis |
| `figures/B2_boundary_shape_vs_size.png` | outline roundness vs size; interior holes by source |
| `figures/B3_boundary_metrics_comparison.png` | source-controlled size/shape tests + readable test panel |
| `figures/C1_cdl_crop_sequence_heatmap.png` | dominant CDL crop per field × year |
| `figures/C2_cdl_crop_share_by_grower.png` | crop mix per grower and year |
| `figures/C3_cdl_rotation_comparison.png` | fields per grower by count of corn years (0–5), with tests |
| `figures/W1_weather_climate_envelope.png` | monthly temperature/precipitation per NASA POWER cell |
| `figures/W2_weather_gdd_by_field.png` | seasonal GDD: field-year distribution + per-cell series |
| `figures/W3_weather_compare_gdd_precip.png` | GDD vs precipitation, one point per cell-year |
| `figures/README.md` | figure index: file, category, level, verified one-line story |
| `tables/T0_field_labels.csv` | one row per field: source, area, crop label, crop label source |
| `tables/T1_boundary_metrics_comparison.csv` | Kruskal-Wallis / Mann-Whitney results, boundaries |
| `tables/T2_cdl_rotation_comparison.csv` | corn-year test + per-grower rotation profile |
| `tables/T3_weather_compare_gdd_precip.csv` | GDD/precipitation tests (2021–2023 overlap) |
| `tables/hypothesis_evidence.csv` | the numeric evidence each figure's story is checked against |
| `tables/eda_manifest.json` | inputs with SHA-256, window rules, definitions |

Figures are 60–240 KB PNGs at 150 dpi so the ones chosen for the final report
can be committed without a size problem. Nothing is written inside the checkout.

## Decisions that shape the numbers

- **Crop labels come from CDL, not the boundary file.** Legacy OSM fields carry
  `crop_name: farmland`. The figures use the dominant CDL crop in the latest CDL
  year (2023), keep `boundary_crop_name` in `T0` for audit, and mark legacy rows
  with ✱ in `C1`.
- **Windows never mix silently.** Crop figures use CDL years only, weather
  figures use weather years only, and joint figures are restricted to 2021–2023
  and labelled. No interpolation, no backfill.
- **Boundary source is the first split.** USDA CSB and legacy OSM fields are two
  populations (17× median size difference); they are never pooled in a size or
  shape summary, and `is_legacy` is on every table row.
- **A NASA POWER cell, not a field, is the weather unit.** Fields inside one
  0.5° × 0.625° cell share an identical series, so field-year counts would fake
  replication; tests run on cell-years and the fields-per-cell mix is printed.
- **The map trades metre tick labels for a km scale bar.** A grower's ten
  fields are 34–54 km apart, so no panel showing all ten can make a 200 ha field
  large. `M1` zooms each panel to the three closest fields and adds a km scale
  bar, with an all-ten inset so nothing is hidden. This is the documented
  `scalebar=True` exception in the shared field frame.
- **Nonparametric only.** Mann-Whitney U with Cliff's delta, Kruskal-Wallis with
  epsilon-squared, Spearman rho; every result reports n and an effect size, and
  the 3-year overlap is labelled descriptive.
- **No soil, terrain, or satellite input.**

## What the first run found

Every planned story was treated as a hypothesis and checked against
`tables/hypothesis_evidence.csv` after the run. Three held after sharpening
(`C1`, `C2`, `W3`); seven changed (`M1`, `B1`, `B2`, `B3`, `C3`, `W1`, `W2`).
The two that flipped direction are the interesting ones:

- **`B2` was backwards.** Legacy OSM fields are not the irregular ones — their
  outlines are *more* compact (median 0.56 vs 0.45). What separates the sources
  is digitisation detail: CSB polygons carry interior holes (mean 1.4, up to 6),
  hand-drawn fields carry none.
- **`W2` was backwards.** Year-to-year GDD swings do not dwarf the field spread;
  the grower offset does (SD 128 between growers, 52 year-to-year within a
  grower, 37 field-to-field). The year-to-year number is the mean of each
  grower's own annual SD; pooling growers per year first cancels their offsets
  and understates the swing (28).
- **`B3` lost its "growers are similar" claim.** With source controlled, CSB
  size still differs by grower (IA 180 ha, NE 208, IL 270; p<0.001) while
  compactness does not (p=0.58).
- **`W1` had the spread wrong.** Nebraska is the driest grower but its three
  cells span 1 mm of seasonal total — the *narrowest* internal spread.

## Words used in the figures

The figures avoid statistical shorthand. Where a test is reported it uses plain
names, with the effect size on a 0–1 scale:

| figure wording | means |
|---|---|
| roundness (1 = perfect circle) | perimeter/area compactness of the outline |
| effect size (0–1) | how separated the groups are; 0 = no difference, 1 = complete separation |
| rank test | Kruskal-Wallis / Mann-Whitney, used instead of a t-test because the samples are small |
| correlation (r) | Spearman rank correlation, −1 to +1 |
| growing degree days (heat sum) | seasonal warmth accumulated above 10 °C and capped at 30 °C |
| weather grid cells (NASA, 0.5°) | the coarse grid the weather comes on; all fields in one cell share a series |

See `figures/README.md` for the verified sentence behind each figure and
`GUIDE.md` for how to read them.
