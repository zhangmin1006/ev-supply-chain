"""
Real historical time-series validation for UK automotive performance.

This module benchmarks modelled UK OEM production against the ONS/SMMT monthly
UK vehicle production dataset. The public ONS series is total UK production, not
UK EV-only production, so the primary error metric uses an index comparison:
both observed and modelled monthly production are normalised to 2023 average
production = 100.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"

ONS_SMMT_PAGE = (
    "https://www.ons.gov.uk/economy/economicoutputandproductivity/output/"
    "datasets/uknewvehicleregistrationsandproduction/2025"
)
ONS_SMMT_XLSX = (
    "https://www.ons.gov.uk/file?uri=/economy/economicoutputandproductivity/"
    "output/datasets/uknewvehicleregistrationsandproduction/2025/"
    "smmtvehicleregandproddataset111225.xlsx"
)
OBSERVED_CACHE = DATA_DIR / "uk_vehicle_production_ons_smmt.csv"
ALIGNMENT_PATH = RESULTS_DIR / "real_timeseries_validation_alignment.csv"
SUMMARY_PATH = RESULTS_DIR / "real_timeseries_validation.csv"


@dataclass(frozen=True)
class RealTimeSeriesValidation:
    summary: pd.DataFrame
    alignment: pd.DataFrame
    source_note: str


def _download_ons_smmt_workbook() -> bytes:
    response = requests.get(
        ONS_SMMT_XLSX,
        headers={"User-Agent": "Mozilla/5.0 (EV supply-chain validation)"},
        timeout=25,
    )
    response.raise_for_status()
    return response.content


def load_observed_uk_production(refresh: bool = False) -> tuple[pd.DataFrame, str]:
    """
    Load observed UK monthly vehicle-production performance from ONS/SMMT.

    Returns a cleaned monthly table with ONS seasonally adjusted production
    indices where 2023 average = 100.
    """
    DATA_DIR.mkdir(exist_ok=True)

    source_note = f"ONS/SMMT workbook: {ONS_SMMT_XLSX}"
    if refresh or not OBSERVED_CACHE.exists():
        workbook = BytesIO(_download_ons_smmt_workbook())
        raw = pd.read_excel(workbook, sheet_name="3.VehicleProd", header=5)
        observed = raw.rename(columns={"Month": "month"}).copy()
        observed["month"] = pd.to_datetime(observed["month"], errors="coerce")
        observed = observed.dropna(subset=["month"])
        keep = [
            "month",
            "Cars total, SA",
            "Cars total, SA (indexed: 100 = 2023 average)",
            "All vehicles total, SA",
            "All vehicles total, SA (indexed: 100 = 2023 average)",
            "Cars domestic, SA",
            "Cars exported, SA",
        ]
        observed = observed[[c for c in keep if c in observed.columns]].copy()
        observed.to_csv(OBSERVED_CACHE, index=False)
    else:
        observed = pd.read_csv(OBSERVED_CACHE, parse_dates=["month"])
        source_note = f"Cached ONS/SMMT extract: {OBSERVED_CACHE}"

    for col in observed.columns:
        if col != "month":
            observed[col] = pd.to_numeric(observed[col], errors="coerce")
    return observed, source_note


def model_monthly_uk_output(
    result_df: pd.DataFrame,
    start_date: str = "2023-01-01",
    value_col: str = "oem_uk_oem_k",
) -> pd.DataFrame:
    """Aggregate weekly model UK OEM output to monthly k-vehicle output."""
    if value_col not in result_df.columns:
        value_col = "oem_production_k"
    model = result_df[["week", value_col]].copy()
    model["date"] = pd.to_datetime(start_date) + pd.to_timedelta(model["week"] * 7, unit="D")
    monthly = (
        model.set_index("date")[value_col]
        .resample("MS")
        .sum()
        .rename("model_uk_oem_k")
        .reset_index()
        .rename(columns={"date": "month"})
    )
    baseline_2023 = monthly.loc[monthly["month"].dt.year == 2023, "model_uk_oem_k"].mean()
    monthly["model_index_2023_100"] = monthly["model_uk_oem_k"] / max(float(baseline_2023), 1e-9) * 100.0
    return monthly


def validate_against_real_uk_timeseries(
    model_results: dict[str, pd.DataFrame],
    scenarios: tuple[str, ...] = ("baseline", "uk_supply_chain_friction"),
    refresh_observed: bool = False,
) -> RealTimeSeriesValidation:
    """
    Calculate MAE between modelled UK OEM performance and observed UK production.

    The primary benchmark is ONS/SMMT "Cars total, SA (indexed: 100 = 2023
    average)" because it is a directly observed monthly UK production performance
    series. The level MAE scales the observed index to the model's 2023 monthly
    UK OEM scale, so it should be read as a shape/error diagnostic rather than a
    direct total-industry volume fit.
    """
    RESULTS_DIR.mkdir(exist_ok=True)
    observed, source_note = load_observed_uk_production(refresh=refresh_observed)
    obs_col = "Cars total, SA (indexed: 100 = 2023 average)"
    obs_level_col = "Cars total, SA"
    obs = observed[["month", obs_col, obs_level_col]].dropna(subset=[obs_col]).copy()
    obs = obs[obs["month"] >= pd.Timestamp("2023-01-01")]

    alignment_rows = []
    summary_rows = []
    for scenario in scenarios:
        if scenario not in model_results:
            continue
        model = model_monthly_uk_output(model_results[scenario])
        aligned = obs.merge(model, on="month", how="inner")
        if aligned.empty:
            continue

        model_2023_level = aligned.loc[aligned["month"].dt.year == 2023, "model_uk_oem_k"].mean()
        aligned["observed_index_2023_100"] = aligned[obs_col]
        aligned["observed_scaled_to_model_k"] = aligned["observed_index_2023_100"] / 100.0 * float(model_2023_level)
        aligned["index_error"] = aligned["model_index_2023_100"] - aligned["observed_index_2023_100"]
        aligned["level_error_k"] = aligned["model_uk_oem_k"] - aligned["observed_scaled_to_model_k"]
        aligned["abs_index_error"] = aligned["index_error"].abs()
        aligned["abs_level_error_k"] = aligned["level_error_k"].abs()
        aligned["scenario"] = scenario
        aligned["source"] = "ONS/SMMT UK vehicle production, seasonally adjusted"
        alignment_rows.append(aligned)

        mae_index = float(aligned["abs_index_error"].mean())
        rmse_index = float(np.sqrt(np.mean(np.square(aligned["index_error"]))))
        mape_index = float((aligned["abs_index_error"] / aligned["observed_index_2023_100"].replace(0, np.nan)).mean() * 100.0)
        mae_level = float(aligned["abs_level_error_k"].mean())
        bias_index = float(aligned["index_error"].mean())
        summary_rows.append(
            {
                "scenario": scenario,
                "benchmark_series": "ONS/SMMT cars total SA index, 2023 average = 100",
                "months_compared": int(len(aligned)),
                "start_month": aligned["month"].min().strftime("%Y-%m"),
                "end_month": aligned["month"].max().strftime("%Y-%m"),
                "mae_index_points": round(mae_index, 3),
                "rmse_index_points": round(rmse_index, 3),
                "mape_index_pct": round(mape_index, 3),
                "bias_index_points": round(bias_index, 3),
                "mae_scaled_k_vehicles_per_month": round(mae_level, 3),
                "observed_2023_mean_cars_sa": round(float(aligned.loc[aligned["month"].dt.year == 2023, obs_level_col].mean()), 3),
                "model_2023_mean_k_vehicles_per_month": round(float(model_2023_level), 3),
                "source_url": ONS_SMMT_PAGE,
                "method_note": "Indexed comparison; public observed series is total UK cars, model series is UK EV-oriented OEM output.",
            }
        )

    summary = pd.DataFrame(summary_rows)
    alignment = pd.concat(alignment_rows, ignore_index=True) if alignment_rows else pd.DataFrame()
    summary.to_csv(SUMMARY_PATH, index=False)
    alignment.to_csv(ALIGNMENT_PATH, index=False)
    return RealTimeSeriesValidation(summary=summary, alignment=alignment, source_note=source_note)


def main() -> None:
    from model.hybrid_model import EVSupplyChainModel
    from model.shocks import SCENARIOS

    scenarios = ("baseline", "uk_supply_chain_friction")
    model_results = {}
    for scenario in scenarios:
        model = EVSupplyChainModel(scenario=SCENARIOS[scenario], seed=42, n_weeks=156)
        model.run()
        model_results[scenario] = model.get_results()
    validation = validate_against_real_uk_timeseries(model_results, scenarios=scenarios, refresh_observed=True)
    print(validation.summary.to_string(index=False))
    print(f"Wrote {SUMMARY_PATH}")
    print(f"Wrote {ALIGNMENT_PATH}")


if __name__ == "__main__":
    main()
