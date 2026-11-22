"""Load and derive the field-level tables for boundary / crop / weather EDA.

Contract enforced here (see ``../../AGENTS.md``):

* no soil, terrain, or satellite inputs;
* crop labels come from the CDL year tables, never ``boundary.crop_name``
  (the six legacy OSM fields carry the placeholder label ``farmland``);
* legacy OSM fields are flagged as ``is_legacy`` and never averaged in with
  CSB fields when summarising size or shape;
* crop+mweather joins are restricted to the shared year window, with no
  interpolation or backfill in either direction.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy import stats
from shapely.geometry import Polygon

EQUAL_AREA_CRS = "EPSG:5070"  # CONUS Albers, equal-area: valid for A and PP
SEASON_START_MONTH = 5
SEASON_END_MONTH = 9
GDD_BASE_C = 10.0
GDD_CAP_C = 30.0

#: CDL crop names collapse into a small, colour-stable set of groups.
CROP_GROUPS = {
    "Corn": "Corn",
    "Soybeans": "Soybeans",
    "Winter Wheat": "Wheat",
    "Alfalfa": "Alfalfa",
    "Grass/Pasture": "Grass/Other",
    "Forest": "Grass/Other",
    "Fallow/Idle": "Grass/Other",
}
CROP_ORDER = ["Corn", "Soybeans", "Wheat", "Alfalfa", "Grass/Other", "Other"]

CDL_RE = re.compile(r"_(\d{4})_cdl\.csv$")


def crop_group(name: str | float | None) -> str:
    if name is None or (isinstance(name, float) and np.isnan(name)):
        return "Other"
    return CROP_GROUPS.get(str(name).strip(), "Other")


@dataclass
class FarmInfo:
    grower_slug: str
    farm_slug: str
    display_name: str
    state: str
    county: str
    farm_dir: Path


@dataclass
class Dataset:
    root: Path
    farms: list[FarmInfo]
    boundaries: gpd.GeoDataFrame
    weather: pd.DataFrame
    cdl: pd.DataFrame
    rotation: pd.DataFrame
    labels: pd.DataFrame
    seasonal: pd.DataFrame
    cell_seasonal: pd.DataFrame
    cdl_years: list[int] = field(default_factory=list)
    weather_years: list[int] = field(default_factory=list)

    @property
    def overlap_years(self) -> list[int]:
        return sorted(set(self.cdl_years) & set(self.weather_years))


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _farm_infos(root: Path, only: set[str] | None) -> list[FarmInfo]:
    infos: list[FarmInfo] = []
    for grower_dir in sorted((root / "growers").glob("*")):
        if not grower_dir.is_dir() or (only and grower_dir.name not in only):
            continue
        for farm_dir in sorted((grower_dir / "farms").glob("*")):
            if not farm_dir.is_dir():
                continue
            meta = _read_json(farm_dir / "farm.json")
            boundary = farm_dir / "boundary" / "field_boundaries.geojson"
            if not boundary.exists():
                continue
            gdf = gpd.read_file(boundary, rows=1)
            county = ""
            if "county_name" in gdf.columns and len(gdf):
                county = str(gdf["county_name"].iloc[0] or "")
            infos.append(
                FarmInfo(
                    grower_slug=grower_dir.name,
                    farm_slug=farm_dir.name,
                    display_name=meta.get("display_name") or grower_dir.name,
                    state=str(meta.get("state") or "").upper(),
                    county=county or "county",
                    farm_dir=farm_dir,
                )
            )
    return infos


def _load_boundaries(farms: list[FarmInfo]) -> gpd.GeoDataFrame:
    frames = []
    for farm in farms:
        gdf = gpd.read_file(farm.farm_dir / "boundary" / "field_boundaries.geojson")
        gdf["field_id"] = gdf["field_id"].astype(str)
        gdf["grower_slug"] = farm.grower_slug
        gdf["farm_slug"] = farm.farm_slug
        gdf["grower_label"] = f"{farm.state} · {farm.county}"
        frames.append(gdf)
    if not frames:
        raise SystemExit("no farm boundaries found")
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=frames[0].crs)


def add_geometry_metrics(boundaries: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Area, perimeter and Polsby-Popper compactness in an equal-area CRS.

    Two compactness values are kept. ``polsby_popper`` uses the full polygon
    including interior holes, which penalises the fine digitisation of USDA
    CSB fields (rings, waterways, farmsteads). ``polsby_popper_exterior`` uses
    the outer ring only, so it compares outline shape fairly between a
    machine-generated CSB polygon and a hand-drawn OSM one.
    """
    gdf = boundaries.to_crs(EQUAL_AREA_CRS)
    out = boundaries.copy()
    out["geom_area_ha"] = gdf.geometry.area / 10000.0
    perim = gdf.geometry.length
    with np.errstate(divide="ignore", invalid="ignore"):
        pp = 4.0 * np.pi * gdf.geometry.area / (perim**2)
    out["perimeter_m"] = perim
    out["polsby_popper"] = np.clip(pp, 0.0, 1.0)

    def _exterior(geom):
        polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        area = sum(Polygon(p.exterior).area for p in polys)
        perim_ext = sum(Polygon(p.exterior).length for p in polys)
        return 4.0 * np.pi * area / (perim_ext**2) if perim_ext else np.nan

    def _holes(geom):
        polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        return sum(len(p.interiors) for p in polys)

    out["polsby_popper_exterior"] = np.clip(
        [_exterior(geom) for geom in gdf.geometry], 0.0, 1.0
    )
    out["interior_holes"] = [_holes(geom) for geom in gdf.geometry]
    cent = gpd.GeoSeries(gdf.geometry.centroid, crs=EQUAL_AREA_CRS).to_crs(4326)
    out["centroid_lon"] = cent.x.to_numpy()
    out["centroid_lat"] = cent.y.to_numpy()
    out["is_legacy"] = out["source"].astype(str).str.contains(
        "openstreetmap", case=False, na=False
    )
    out["source_label"] = np.where(out["is_legacy"], "Legacy OSM", "USDA CSB")
    if "crop_name" in out.columns:
        out["boundary_crop_name"] = out["crop_name"]
    else:
        out["boundary_crop_name"] = None
    return out


def _load_weather(farms: list[FarmInfo]) -> pd.DataFrame:
    frames = []
    for farm in farms:
        tables = farm.farm_dir / "derived" / "tables"
        files = sorted(tables.glob("*_weather_*.csv"))
        files = [p for p in files if "full_composition" not in p.name]
        if not files:
            continue
        df = pd.read_csv(files[0])
        df["date"] = pd.to_datetime(df["date"])
        df["field_id"] = df["field_id"].astype(str)
        df["grower_slug"] = farm.grower_slug
        df["farm_slug"] = farm.farm_slug
        df["grower_label"] = f"{farm.state} · {farm.county}"
        frames.append(df)
    if not frames:
        raise SystemExit("no farm weather tables found")
    weather = pd.concat(frames, ignore_index=True)
    # NASA POWER is a 0.5 deg lat x 0.625 deg lon grid: fields inside one cell
    # share identical weather, so cell-year (not field-year) is the independent
    # unit for any weather test.
    ci = np.floor((weather["lat"] - 0.25) / 0.5).astype(int)
    cj = np.floor((weather["lon"] + 180.0 - 0.3125) / 0.625).astype(int)
    weather["power_cell"] = ci.astype(str) + "_" + cj.astype(str)
    return weather


def _load_cdl(farms: list[FarmInfo]) -> tuple[pd.DataFrame, list[int]]:
    frames = []
    years: set[int] = set()
    for farm in farms:
        tables = farm.farm_dir / "derived" / "tables"
        for path in sorted(tables.glob("*_cdl.csv")):
            match = CDL_RE.search(path.name)
            if not match:
                continue
            year = int(match.group(1))
            years.add(year)
            df = pd.read_csv(path)
            df["field_id"] = df["field_id"].astype(str)
            df["grower_slug"] = farm.grower_slug
            df["farm_slug"] = farm.farm_slug
            df["grower_label"] = f"{farm.state} · {farm.county}"
            df["crop_group"] = df["crop_name"].map(crop_group)
            frames.append(df)
    if not frames:
        raise SystemExit("no CDL year tables found")
    return pd.concat(frames, ignore_index=True), sorted(years)


def _load_rotation(farms: list[FarmInfo]) -> pd.DataFrame:
    frames = []
    for farm in farms:
        files = sorted((farm.farm_dir / "derived" / "tables").glob("*_crop_rotation.csv"))
        if not files:
            continue
        df = pd.read_csv(files[0])
        df["field_id"] = df["field_id"].astype(str)
        df["grower_slug"] = farm.grower_slug
        df["grower_label"] = f"{farm.state} · {farm.county}"
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def resolve_crop_labels(boundaries: gpd.GeoDataFrame, cdl: pd.DataFrame) -> pd.DataFrame:
    """Dominant CDL crop per field in the latest CDL year, with a fallback.

    ``boundary.crop_name`` is only ever a fallback: the legacy OSM fields may
    carry the placeholder ``farmland``, which this function replaces with the
    real CDL crop and records ``crop_label_source`` so the change is auditable.
    """
    if cdl.empty:
        out = boundaries[["field_id", "grower_slug", "grower_label", "source_label"]].copy()
        out["crop_label"] = boundaries["boundary_crop_name"]
        out["crop_label_source"] = "boundary"
        out["cdl_year"] = pd.NA
        out["cdl_pct"] = np.nan
        return out

    latest = int(cdl["year"].max())
    year_df = cdl[cdl["year"] == latest]
    idx = year_df.groupby("field_id")["pct"].idxmax()
    top = year_df.loc[idx, ["field_id", "crop_name", "pct"]].rename(
        columns={"crop_name": "crop_label", "pct": "cdl_pct"}
    )
    top["crop_label_source"] = "cdl"
    top["cdl_year"] = latest

    out = boundaries[
        ["field_id", "grower_slug", "farm_slug", "grower_label", "source_label",
         "is_legacy", "boundary_crop_name", "geom_area_ha", "polsby_popper",
         "polsby_popper_exterior", "interior_holes"]
    ].copy()
    if "area_acres" in boundaries.columns:
        out["boundary_area_acres"] = boundaries["area_acres"]
    out["area_acres"] = out["geom_area_ha"] * 2.47105
    out = out.merge(top, on="field_id", how="left")

    missing = out["crop_label"].isna()
    out.loc[missing, "crop_label"] = out.loc[missing, "boundary_crop_name"]
    out.loc[missing, "crop_label_source"] = "boundary"
    out["crop_group"] = out["crop_label"].map(crop_group)
    return out


def seasonal_weather(weather: pd.DataFrame) -> pd.DataFrame:
    """Growing-season (May-Sep) aggregates per field-year.

    GDD follows the standard corn convention: daily mean temperature capped at
    30 C and floored at the 10 C base, summed over the season.
    """
    w = weather.copy()
    month = w["date"].dt.month
    day = w["date"].dt.day
    mask = (month > SEASON_START_MONTH) & (month < SEASON_END_MONTH)
    mask |= month == SEASON_START_MONTH
    mask |= (month == SEASON_END_MONTH) & (day <= 30)
    w = w[mask].copy()

    tmax = w["T2M_MAX"].clip(upper=GDD_CAP_C)
    tmin = w["T2M_MIN"].clip(upper=GDD_CAP_C)
    tmean = (tmax + tmin) / 2.0
    w["gdd"] = (tmean - GDD_BASE_C).clip(lower=0.0)
    w["year"] = w["date"].dt.year

    grouped = w.groupby(["grower_slug", "grower_label", "farm_slug", "field_id", "year"])
    out = grouped.agg(
        gdd=("gdd", "sum"),
        precip_mm=("PRECTOTCORR", "sum"),
        tmean_c=("T2M", "mean"),
        season_days=("date", "nunique"),
        lat=("lat", "mean"),
        lon=("lon", "mean"),
        power_cell=("power_cell", "first"),
    ).reset_index()
    return out


def monthly_climatology(weather: pd.DataFrame) -> pd.DataFrame:
    w = weather.copy()
    w["year"] = w["date"].dt.year
    w["month"] = w["date"].dt.month
    per_field = w.groupby(
        ["grower_slug", "grower_label", "field_id", "year", "month"]
    ).agg(tmean_c=("T2M", "mean"), precip_mm=("PRECTOTCORR", "sum")).reset_index()
    return per_field


def cell_monthly_climatology(weather: pd.DataFrame) -> pd.DataFrame:
    """Monthly climatology per POWER cell (the honest spread within a grower).

    Sum per FIELD-month first, then average fields within the cell. Summing
    after collapsing to the cell would add one month of rain once per field in
    that cell and inflate precipitation by the field count.
    """
    per_field = monthly_climatology(weather).merge(
        weather[["field_id", "power_cell"]].drop_duplicates(), on="field_id", how="left"
    )
    return (
        per_field.groupby(["grower_slug", "grower_label", "power_cell", "month"],
                          as_index=False)
        .agg(tmean_c=("tmean_c", "mean"), precip_mm=("precip_mm", "mean"),
             n_fields=("field_id", "nunique"))
    )


def n_power_cells(weather: pd.DataFrame) -> pd.Series:
    """Distinct NASA POWER grid cells sampled per grower (weather quantisation)."""
    return weather.groupby("grower_slug")["power_cell"].nunique()


def power_cell_mix(weather: pd.DataFrame) -> pd.Series:
    """Fields per POWER cell, largest first, e.g. ``IA · Story: 7+3``.

    Within-grower weather spread comes from a handful of fields crossing a cell
    edge, not from fields spread evenly over many cells; say so explicitly.
    """
    counts = (
        weather.drop_duplicates("field_id")
        .groupby(["grower_slug", "power_cell"])["field_id"].nunique()
        .sort_values(ascending=False)
    )
    return counts.groupby("grower_slug").apply(
        lambda s: "+".join(str(int(v)) for v in s)
    )


def cell_seasonal(seasonal: pd.DataFrame) -> pd.DataFrame:
    """Collapse fields onto their POWER cell: the independent weather unit."""
    return (
        seasonal.groupby(["grower_slug", "grower_label", "power_cell", "year"],
                         as_index=False)
        .agg(gdd=("gdd", "mean"), precip_mm=("precip_mm", "mean"),
             tmean_c=("tmean_c", "mean"), n_fields=("field_id", "nunique"))
    )


def build_dataset(root: Path, only: set[str] | None = None) -> Dataset:
    farms = _farm_infos(root, only)
    boundaries = add_geometry_metrics(_load_boundaries(farms))
    weather = _load_weather(farms)
    cdl, cdl_years = _load_cdl(farms)
    rotation = _load_rotation(farms)
    labels = resolve_crop_labels(boundaries, cdl)
    seasonal = seasonal_weather(weather)
    return Dataset(
        root=root,
        farms=farms,
        boundaries=boundaries,
        weather=weather,
        cdl=cdl,
        rotation=rotation,
        labels=labels,
        seasonal=seasonal,
        cell_seasonal=cell_seasonal(seasonal),
        cdl_years=cdl_years,
        weather_years=sorted(weather["date"].dt.year.unique().tolist()),
    )


# --------------------------------------------------------------------------
# statistics: nonparametric only, with effect sizes, always reporting n
# --------------------------------------------------------------------------

def cliffs_delta(x: np.ndarray, y: np.ndarray) -> float:
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) == 0 or len(y) == 0:
        return float("nan")
    u = stats.mannwhitneyu(x, y, alternative="two-sided").statistic
    return 2.0 * u / (len(x) * len(y)) - 1.0


def mann_whitney(x, y) -> dict:
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    if len(x) < 3 or len(y) < 3:
        return {"test": "Mann-Whitney U", "n_a": len(x), "n_b": len(y),
                "statistic": np.nan, "p_value": np.nan,
                "effect": np.nan, "effect_name": "Cliff's delta", "note": "n<3"}
    res = stats.mannwhitneyu(x, y, alternative="two-sided")
    return {"test": "Mann-Whitney U", "n_a": len(x), "n_b": len(y),
            "statistic": float(res.statistic), "p_value": float(res.pvalue),
            "effect": cliffs_delta(x, y), "effect_name": "Cliff's delta", "note": ""}


def kruskal(groups: dict[str, list[float]]) -> dict:
    usable = {k: np.asarray(v, float) for k, v in groups.items() if len(v) >= 3}
    if len(usable) < 2:
        return {"test": "Kruskal-Wallis", "n_a": 0, "n_b": 0, "statistic": np.nan,
                "p_value": np.nan, "effect": np.nan,
                "effect_name": "epsilon-squared", "note": "fewer than 2 usable groups"}
    res = stats.kruskal(*usable.values())
    n = sum(len(v) for v in usable.values())
    k = len(usable)
    eps2 = (res.statistic - k + 1) / (n - k) if n > k else np.nan
    return {"test": "Kruskal-Wallis", "n_a": k, "n_b": n,
            "statistic": float(res.statistic), "p_value": float(res.pvalue),
            "effect": float(eps2), "effect_name": "epsilon-squared", "note": ""}


def spearman(x, y) -> dict:
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 5:
        return {"test": "Spearman rho", "n_a": int(mask.sum()), "n_b": int(mask.sum()),
                "statistic": np.nan, "p_value": np.nan, "effect": np.nan,
                "effect_name": "rho", "note": "n<5"}
    res = stats.spearmanr(x[mask], y[mask])
    return {"test": "Spearman rho", "n_a": int(mask.sum()), "n_b": int(mask.sum()),
            "statistic": float(res.statistic), "p_value": float(res.pvalue),
            "effect": float(res.statistic), "effect_name": "rho", "note": ""}


def fmt_p(p: float) -> str:
    if not np.isfinite(p):
        return "n/a"
    if p < 0.001:
        return "p<0.001"
    return f"p={p:.3f}"
