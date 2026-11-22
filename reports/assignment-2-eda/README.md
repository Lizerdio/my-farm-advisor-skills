# Assignment 2 EDA report

One-time assignment deliverable: a ten-page PDF built from the ten figures that
`my-farm-advisor/eda/eda-boundary-crop-weather/` generates, comparing three
ten-field grower groups in Story County, Iowa; DeKalb County, Illinois; and York
County, Nebraska.

Author and cover credit: Elizabeth Giles (GitHub: [Lizerdio](https://github.com/Lizerdio)).

This folder is **not** part of the reusable skill. The reusable workflow and its
docs live in [`my-farm-advisor/eda/eda-boundary-crop-weather/`](../../my-farm-advisor/eda/eda-boundary-crop-weather/);
this folder holds only the submitted report.

## Contents

| Path | What it is |
| --- | --- |
| `assignment-2-eda-report.pdf` | The report: 10 pages, ~0.9 MB |
| `figures/*.png` | Downscaled (1100 px, palette-quantised) copies of the ten final figures, ~540 KB total |
| `src/build_report.py` | Builds the PDF from `figures/` with ReportLab |

The committed figure copies are for reading the report in a clone. Full 150 dpi
originals and all tables are runtime outputs and are not committed.

## Rebuild

The report builder needs ReportLab and Pillow in a throwaway environment; it is
deliberately not part of the data-pipeline venv.

```bash
python3 -m venv /tmp/report-venv
/tmp/report-venv/bin/pip install reportlab pillow

# 1. regenerate the runtime figures, then refresh the committed copies
export DATA_PIPELINE_DATA_ROOT=/path/to/my-farm-advisor-runtime
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  ../../my-farm-advisor/eda/eda-boundary-crop-weather/src/run_eda.py
/tmp/report-venv/bin/python src/build_report.py \
  --sync-figures "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/eda/boundary-crop-weather/figures"

# 2. rebuild the PDF from the committed figures
/tmp/report-venv/bin/python src/build_report.py
```

`build_report.py` refuses to write a PDF over 1 MB, which keeps the committed
binary within the repository asset policy without Git LFS.

## Data

Boundaries are USDA CSB (FIBOA hosted GeoParquet, CC-BY-4.0) plus six legacy
OpenStreetMap `landuse=farmland` polygons; crops are USDA NASS CDL 2019–2023;
weather is NASA POWER 2021–2025. No soil layer is used. The three grower groups
are ten-field sets assembled from public boundaries, not cooperating producers.
