"""
Validation runner for the EV supply-chain ABM + SD model.

The checks here are intentionally layered:
  1. Static configuration and scenario integrity.
  2. Fresh in-memory simulation runs for all scenarios.
  3. Numeric invariants and bounded-state checks.
  4. Baseline calibration checks against the model's own 2023 anchors.
  5. Scenario face-validity checks against expected disruption mechanisms.
  6. Existing results/schema freshness audit.

Outputs are written to results/:
  validation_checks.csv
  validation_scenario_metrics.csv
  validation_report.md
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from model.config import (
    CELL_GLOBAL_GWH_2023,
    CELL_MAKERS,
    EV_GLOBAL_UNITS_2023_K,
    MARKETS,
    OEMS,
)
from model.hybrid_model import EVSupplyChainModel
from model.shocks import SCENARIOS
from model.sd_model import LFP_SHARE_MAX, LFP_SHARE_MIN, PRICE_CEIL, PRICE_FLOOR


ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
CHECKS_PATH = RESULTS_DIR / "validation_checks.csv"
METRICS_PATH = RESULTS_DIR / "validation_scenario_metrics.csv"
REPORT_PATH = RESULTS_DIR / "validation_report.md"


@dataclass
class Check:
    category: str
    check: str
    status: str
    value: str
    expected: str
    detail: str = ""


def _status(ok: bool, warn: bool = False) -> str:
    if ok:
        return "PASS"
    return "WARN" if warn else "FAIL"


def _fmt(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _add(
    checks: list[Check],
    category: str,
    check: str,
    ok: bool,
    value: object,
    expected: str,
    detail: str = "",
    warn: bool = False,
) -> None:
    checks.append(
        Check(
            category=category,
            check=check,
            status=_status(ok, warn),
            value=_fmt(value),
            expected=expected,
            detail=detail,
        )
    )


def _run_scenario(name: str, weeks: int, seed: int) -> pd.DataFrame:
    model = EVSupplyChainModel(scenario=SCENARIOS[name], seed=seed, n_weeks=weeks)
    model.run()
    df = model.get_results()
    df["scenario"] = name
    return df


def _numeric_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]


def _cum_loss_against_baseline(df: pd.DataFrame, baseline: pd.DataFrame) -> float:
    joined = baseline[["week", "oem_production_k"]].merge(
        df[["week", "oem_production_k"]],
        on="week",
        suffixes=("_baseline", "_scenario"),
    )
    return float(
        (joined["oem_production_k_baseline"] - joined["oem_production_k_scenario"])
        .clip(lower=0.0)
        .sum()
    )


def static_checks(checks: list[Check]) -> None:
    cell_share = sum(cfg["market_share"] for cfg in CELL_MAKERS.values())
    oem_units = sum(cfg["annual_target_k"] for cfg in OEMS.values())
    market_gwh = sum(cfg["gwh_2023"] for cfg in MARKETS.values())

    _add(checks, "static", "cell maker market shares sum to 1", abs(cell_share - 1.0) <= 0.005, cell_share, "1.0 +/- 0.005")
    _add(checks, "static", "OEM annual targets sum to 2023 global EV units", abs(oem_units - EV_GLOBAL_UNITS_2023_K) <= 1, oem_units, str(EV_GLOBAL_UNITS_2023_K))
    _add(checks, "static", "market battery demand matches 2023 cell demand anchor", abs(market_gwh - CELL_GLOBAL_GWH_2023) <= 5, market_gwh, f"{CELL_GLOBAL_GWH_2023} +/- 5 GWh")

    probe = EVSupplyChainModel(scenario=SCENARIOS["baseline"], seed=42, n_weeks=1)
    for scenario_name, scenario in SCENARIOS.items():
        for idx, shock in enumerate(scenario.get("shocks", [])):
            target = shock.get("target")
            _add(
                checks,
                "static",
                f"{scenario_name} shock {idx} target exists",
                probe._find_agent(target) is not None,
                target,
                "agent must be found by EVSupplyChainModel._find_agent",
            )
            severity = shock.get("severity", 0.0)
            _add(
                checks,
                "static",
                f"{scenario_name} shock {idx} severity bounded",
                0.0 <= severity <= 1.0,
                severity,
                "0 <= severity <= 1",
            )
            _add(
                checks,
                "static",
                f"{scenario_name} shock {idx} timing valid",
                shock.get("start_week", -1) < shock.get("end_week", 10**9),
                f"{shock.get('start_week')}..{shock.get('end_week')}",
                "start_week < end_week",
            )


def invariant_checks(checks: list[Check], results: dict[str, pd.DataFrame]) -> None:
    for name, df in results.items():
        numeric = _numeric_columns(df)
        finite_ok = bool(np.isfinite(df[numeric].to_numpy(dtype=float)).all())
        _add(checks, "invariants", f"{name} numeric outputs finite", finite_ok, finite_ok, "all numeric values finite")

        nonnegative_cols = [c for c in numeric if c != "week"]
        min_value = float(df[nonnegative_cols].min().min()) if nonnegative_cols else 0.0
        _add(checks, "invariants", f"{name} numeric stocks/flows non-negative", min_value >= -1e-9, min_value, "minimum >= 0")

        if "lfp_share" in df:
            low = float(df["lfp_share"].min())
            high = float(df["lfp_share"].max())
            _add(
                checks,
                "invariants",
                f"{name} LFP share bounded",
                low >= LFP_SHARE_MIN - 1e-9 and high <= LFP_SHARE_MAX + 1e-9,
                f"{low:.3f}..{high:.3f}",
                f"{LFP_SHARE_MIN} <= lfp_share <= {LFP_SHARE_MAX}",
            )

        price_cols = [c for c in df.columns if c.startswith("price_")]
        if price_cols:
            low = float(df[price_cols].min().min())
            high = float(df[price_cols].max().max())
            _add(
                checks,
                "invariants",
                f"{name} price indices bounded",
                low >= PRICE_FLOOR - 1e-9 and high <= PRICE_CEIL + 1e-9,
                f"{low:.3f}..{high:.3f}",
                f"{PRICE_FLOOR} <= prices <= {PRICE_CEIL}",
            )

        if "cell_cap_util" in df:
            high = float(df["cell_cap_util"].max())
            _add(checks, "invariants", f"{name} exact cell utilisation bounded", high <= 2.0 + 1e-9, high, "<= 2.0")


def baseline_checks(checks: list[Check], baseline: pd.DataFrame) -> None:
    first = baseline.iloc[0]
    cell_annual = float(first["cell_production_gwh"] * 52.0)
    oem_annual = float(first["oem_production_k"] * 52.0)
    demand_annual = float(first["market_demand_gwh"] * 52.0)
    expected_market_gwh = float(first.get("active_market_gwh_2023", MARKETS["uk"]["gwh_2023"]))
    expected_oem_k = float(first.get("active_oem_target_k", OEMS["uk_oem"]["annual_target_k"]))
    expected_market_units_k = MARKETS["uk"]["gwh_2023"] * 1000.0 / MARKETS["uk"]["avg_kwh_veh"]
    price0 = float(first["price_signal"])
    backlog_max = float(baseline["total_backlog_k"].max())

    _add(checks, "baseline", "week-0 cell production near active market GWh", abs(cell_annual - expected_market_gwh) / expected_market_gwh <= 0.15, cell_annual, f"{expected_market_gwh} +/- 15%")
    _add(checks, "baseline", "week-0 market demand near active market GWh", abs(demand_annual - expected_market_gwh) / expected_market_gwh <= 0.10, demand_annual, f"{expected_market_gwh} +/- 10%")
    _add(checks, "baseline", "week-0 OEM production near active OEM target", abs(oem_annual - expected_oem_k) / expected_oem_k <= 0.15, oem_annual, f"{expected_oem_k} +/- 15%")
    _add(checks, "baseline", "initial battery price signal near baseline", abs(price0 - 1.0) <= 0.05, price0, "1.0 +/- 0.05")
    _add(checks, "baseline", "baseline backlog does not explode", backlog_max <= expected_market_units_k * 2.0, backlog_max, f"<= two years of active market demand ({expected_market_units_k * 2.0:.1f}k)", warn=True)


def scenario_metrics(results: dict[str, pd.DataFrame], baseline: pd.DataFrame) -> pd.DataFrame:
    baseline_prod_mean = float(baseline["oem_production_k"].mean())
    rows = []
    for name, df in results.items():
        min_prod = float(df["oem_production_k"].min())
        peak_loss_pct = max(0.0, (baseline_prod_mean - min_prod) / max(baseline_prod_mean, 1e-9) * 100.0)
        cum_loss = 0.0 if name == "baseline" else _cum_loss_against_baseline(df, baseline)
        below = df[df["oem_production_k"] < baseline_prod_mean * 0.90]
        recovery_week = int(below["week"].max()) + 1 if not below.empty else 0
        rows.append(
            {
                "scenario": name,
                "mean_oem_production_k_wk": round(float(df["oem_production_k"].mean()), 3),
                "min_oem_production_k_wk": round(min_prod, 3),
                "peak_loss_pct_vs_baseline_mean": round(peak_loss_pct, 3),
                "cumulative_loss_k_veh_vs_baseline": round(cum_loss, 3),
                "recovery_week_below_90pct": recovery_week,
                "max_total_backlog_k": round(float(df["total_backlog_k"].max()), 3),
                "max_price_signal": round(float(df["price_signal"].max()), 3),
                "min_harness_stock_wk": round(float(df["stock_harness_wk"].min()), 3) if "stock_harness_wk" in df else np.nan,
                "min_cobalt_stock_wk": round(float(df["stock_cobalt_wk"].min()), 3) if "stock_cobalt_wk" in df else np.nan,
                "min_graphite_stock_wk": round(float(df["stock_graphite_wk"].min()), 3) if "stock_graphite_wk" in df else np.nan,
                "min_ree_stock_wk": round(float(df["stock_ree_wk"].min()), 3) if "stock_ree_wk" in df else np.nan,
                "min_sic_stock_wk": round(float(df["stock_sic_wafer_wk"].min()), 3) if "stock_sic_wafer_wk" in df else np.nan,
            }
        )
    return pd.DataFrame(rows).sort_values("scenario")


def scenario_face_validity(checks: list[Check], results: dict[str, pd.DataFrame], baseline: pd.DataFrame, metrics: pd.DataFrame) -> None:
    metric = metrics.set_index("scenario")
    focus_region = str(baseline["focus_region"].iloc[0]) if "focus_region" in baseline else "global"

    def col_drop(scenario: str, col: str, start: int, end: int | None = None) -> tuple[float, float]:
        left = baseline.loc[baseline["week"].between(start, end if end is not None else int(baseline["week"].max())), col].mean()
        right = results[scenario].loc[results[scenario]["week"].between(start, end if end is not None else int(results[scenario]["week"].max())), col].mean()
        return float(left), float(right)

    if focus_region == "uk":
        if "uk_supply_chain_friction" in results:
            loss = float(metric.loc["uk_supply_chain_friction", "cumulative_loss_k_veh_vs_baseline"])
            _add(checks, "scenario", "UK trade friction reduces UK OEM output", loss > 0.0, loss, "> 0 cumulative k-vehicle loss", warn=True)
        return

    if "ukraine_harness" in results:
        b, s = col_drop("ukraine_harness", "t1_harness_k", 4, 16)
        _add(checks, "scenario", "Ukraine harness shock lowers harness output after onset", s < b * 0.95, s / max(b, 1e-9), "< 0.95 of baseline weeks 4-16", warn=True)

    if "drc_cobalt" in results:
        max_base_price = float(baseline["price_cobalt"].max())
        max_shock_price = float(results["drc_cobalt"]["price_cobalt"].max())
        min_base_stock = float(baseline["stock_cobalt_wk"].min())
        min_shock_stock = float(results["drc_cobalt"]["stock_cobalt_wk"].min())
        _add(checks, "scenario", "DRC cobalt shock raises cobalt price above baseline", max_shock_price > max_base_price * 1.02, max_shock_price / max(max_base_price, 1e-9), "> 1.02x baseline max", warn=True)
        _add(checks, "scenario", "DRC cobalt shock lowers cobalt stock below baseline", min_shock_stock < min_base_stock * 0.98, min_shock_stock / max(min_base_stock, 1e-9), "< 0.98x baseline min", warn=True)

    if "china_catl_disruption" in results:
        b, s = col_drop("china_catl_disruption", "cell_production_gwh", 13, 78)
        _add(checks, "scenario", "CATL disruption lowers cell production during shock window", s < b * 0.98, s / max(b, 1e-9), "< 0.98 of baseline weeks 13-78", warn=True)

    if {"compound_shock", "drc_cobalt", "ukraine_harness"}.issubset(results):
        compound = float(metric.loc["compound_shock", "cumulative_loss_k_veh_vs_baseline"])
        largest_single = max(
            float(metric.loc["drc_cobalt", "cumulative_loss_k_veh_vs_baseline"]),
            float(metric.loc["ukraine_harness", "cumulative_loss_k_veh_vs_baseline"]),
        )
        _add(checks, "scenario", "compound shock cumulative loss >= largest included single shock", compound >= largest_single, compound, f">= {largest_single:.3f}", warn=True)


def existing_results_audit(checks: list[Check], fresh_results: dict[str, pd.DataFrame]) -> None:
    for name, fresh in fresh_results.items():
        path = RESULTS_DIR / f"{name}.csv"
        if not path.exists():
            _add(checks, "outputs", f"existing {name}.csv present", False, "missing", "file exists", warn=True)
            continue
        try:
            old = pd.read_csv(path, nrows=1)
        except Exception as exc:
            _add(checks, "outputs", f"existing {name}.csv readable", False, type(exc).__name__, "readable CSV", detail=str(exc), warn=True)
            continue
        missing = sorted(set(fresh.columns) - set(old.columns))
        extra = sorted(set(old.columns) - set(fresh.columns))
        _add(
            checks,
            "outputs",
            f"existing {name}.csv schema matches fresh model",
            not missing and not extra,
            f"missing={len(missing)}, extra={len(extra)}",
            "same columns as a fresh run",
            detail=f"missing: {', '.join(missing[:8])}; extra: {', '.join(extra[:8])}",
            warn=True,
        )


def write_report(checks_df: pd.DataFrame, metrics_df: pd.DataFrame, weeks: int, seed: int) -> None:
    counts = checks_df["status"].value_counts().reindex(["PASS", "WARN", "FAIL"], fill_value=0)
    top_failures = checks_df[checks_df["status"].isin(["FAIL", "WARN"])].copy()

    lines = [
        "# EV Supply-Chain Model Validation Report",
        "",
        f"Fresh simulations run: {len(metrics_df)} scenarios, {weeks} weeks, seed {seed}.",
        "",
        "## Summary",
        "",
        f"- PASS: {int(counts['PASS'])}",
        f"- WARN: {int(counts['WARN'])}",
        f"- FAIL: {int(counts['FAIL'])}",
        "",
        "## Scenario Metrics",
        "",
        _markdown_table(metrics_df),
        "",
        "## Warnings And Failures",
        "",
    ]

    if top_failures.empty:
        lines.append("No warnings or failures.")
    else:
        for _, row in top_failures.iterrows():
            lines.append(
                f"- **{row['status']}** `{row['category']}` {row['check']}: "
                f"value `{row['value']}`, expected {row['expected']}. {row.get('detail', '')}"
            )

    lines.extend(
        [
            "",
            "## Files",
            "",
            f"- Checks: `{CHECKS_PATH.relative_to(ROOT)}`",
            f"- Scenario metrics: `{METRICS_PATH.relative_to(ROOT)}`",
        ]
    )

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def _markdown_table(df: pd.DataFrame) -> str:
    """Small dependency-free Markdown table renderer."""
    if df.empty:
        return "_No rows._"

    display = df.astype(object).where(pd.notna(df), "")
    headers = [str(c) for c in display.columns]
    rows = [[str(v) for v in row] for row in display.to_numpy()]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows))
        for i in range(len(headers))
    ]

    def fmt_row(values: list[str]) -> str:
        return "| " + " | ".join(v.ljust(widths[i]) for i, v in enumerate(values)) + " |"

    sep = "| " + " | ".join("-" * widths[i] for i in range(len(headers))) + " |"
    return "\n".join([fmt_row(headers), sep, *(fmt_row(row) for row in rows)])


def run_validation(scenarios: Iterable[str] | None = None, weeks: int = 260, seed: int = 42) -> tuple[pd.DataFrame, pd.DataFrame]:
    RESULTS_DIR.mkdir(exist_ok=True)
    selected = list(scenarios or SCENARIOS.keys())
    if "baseline" not in selected:
        selected.insert(0, "baseline")

    checks: list[Check] = []
    static_checks(checks)

    results: dict[str, pd.DataFrame] = {}
    for name in selected:
        if name not in SCENARIOS:
            _add(checks, "run", f"{name} scenario exists", False, name, "known scenario")
            continue
        try:
            results[name] = _run_scenario(name, weeks, seed)
            _add(checks, "run", f"{name} simulation completes", True, f"{len(results[name])} rows", f"{weeks} rows")
        except Exception as exc:
            _add(checks, "run", f"{name} simulation completes", False, type(exc).__name__, "no exception", detail=str(exc))

    if "baseline" not in results:
        raise RuntimeError("Baseline simulation did not complete; cannot compute validation metrics.")

    invariant_checks(checks, results)
    baseline_checks(checks, results["baseline"])
    metrics = scenario_metrics(results, results["baseline"])
    scenario_face_validity(checks, results, results["baseline"], metrics)
    existing_results_audit(checks, results)

    checks_df = pd.DataFrame([c.__dict__ for c in checks])
    checks_df.to_csv(CHECKS_PATH, index=False)
    metrics.to_csv(METRICS_PATH, index=False)
    write_report(checks_df, metrics, weeks, seed)
    return checks_df, metrics


def main() -> None:
    checks, metrics = run_validation()
    counts = checks["status"].value_counts().reindex(["PASS", "WARN", "FAIL"], fill_value=0)
    print("Validation complete")
    print(f"  PASS: {int(counts['PASS'])}")
    print(f"  WARN: {int(counts['WARN'])}")
    print(f"  FAIL: {int(counts['FAIL'])}")
    print(f"  Wrote {CHECKS_PATH}")
    print(f"  Wrote {METRICS_PATH}")
    print(f"  Wrote {REPORT_PATH}")
    if counts["FAIL"] > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
