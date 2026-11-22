# Local instructions — eda-boundary-crop-weather

Scope: this folder only. It is a runnable workflow inside `my-farm-advisor/`,
not a separate skill, so it inherits the parent skill and root policy.

## Layout

```
src/run_eda.py          runner: builds the dataset, draws all 10 figures,
                        writes tables, figure index, manifest, evidence
src/lib/eda_data.py     loaders, geometry metrics, crop labels, seasonal weather,
                        POWER-cell helpers, nonparametric tests
src/lib/eda_plots.py    palette, style, save() footer, annotation and jitter helpers
```

Docs: `README.md` (what it is and what it found), `GUIDE.md` (how to read each
figure and where to change things). Keep both short; put new detail in the file
that already covers it instead of duplicating across docs.

## Runtime contract

- Read from `${DATA_PIPELINE_DATA_ROOT}/data-pipeline`, write to
  `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/eda/boundary-crop-weather/`.
- Never write figures, tables, caches, or `__pycache__` into the checkout.
- Run with the runtime venv:
  `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python src/run_eda.py`.
- Do not add an `install.sh` step or copy `src` into the runtime: this workflow
  reads runtime data, it is not part of the pipeline bootstrap.

## Analysis rules (do not regress these)

- Crop labels come from the dominant CDL crop, never from `boundary_crop_name`
  (`farmland` for legacy OSM fields). Keep `boundary_crop_name` in `T0` for audit.
- Never pool USDA CSB and legacy OSM fields in a size or shape summary; stratify
  by source and keep `is_legacy` on every row.
- Crop figures use CDL years only, weather figures use weather years only, and
  joint figures are restricted to the computed overlap and labelled with it.
- The independent weather unit is the NASA POWER cell-year, not the field-year.
  Report the fields-per-cell mix next to any within-grower weather claim.
- Nonparametric tests only, always with n and an effect size. Do not upgrade the
  3-year overlap to a trend claim.
- No soil, terrain, or satellite inputs.
- Figures use the shared field frame (`field_frame.frame_for` + `decorate`,
  `force_rot=0.0`) and the shared `save()` footer.

## Story discipline

`STORIES` in `src/run_eda.py` is a list of hypotheses, not conclusions. After
changing data, windows, or grouping:

1. Run the workflow.
2. Compare each story against `tables/hypothesis_evidence.csv`.
3. Rewrite any story the numbers contradict, rerun, and say in the change note
   which stories held and which changed.

The first run flipped two stories' direction (shape-by-source, year-to-year GDD);
assume a new story is wrong until the evidence table says otherwise.

## Figure gate

Every PNG is reviewed with vision before it is quoted: no empty or clipped
panels, no annotation box on top of data, legible labels at README width, footer
clear of axis labels and legends, source and window stated. Keep PNGs small
(150 dpi, report-sized figures); they are runtime outputs until a report copies
the chosen ones into the repository.
