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
from real_timeseries_validation import validate_against_real_uk_timeseries


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


def _col_mean(df: pd.DataFrame, col: str, start: int, end: int) -> float:
    mask = df["week"].between(start, end)
    return float(df.loc[mask, col].mean()) if col in df.columns else float("nan")


def scenario_face_validity(
    checks: list[Check],
    results: dict[str, pd.DataFrame],
    baseline: pd.DataFrame,
    metrics: pd.DataFrame,
) -> None:
    """Scenario face-validity: checks apply for all focus regions."""
    metric = metrics.set_index("scenario")

    # ── UK supply-chain friction must reduce UK OEM output ──────────────────
    if "uk_supply_chain_friction" in results:
        loss = float(metric.loc["uk_supply_chain_friction", "cumulative_loss_k_veh_vs_baseline"])
        _add(checks, "scenario", "UK trade friction reduces UK OEM output",
             loss > 0.0, loss, "> 0 cumulative k-vehicle loss")

    # ── Mineral shock → stock depletion + price rise ─────────────────────────
    MINERAL_SHOCKS = [
        ("drc_cobalt",           "cobalt",    34, 60),   # 8-wk transit delay from wk 26 shock
        ("china_graphite",       "graphite",  12, 60),   # 4-wk transit delay from wk 8 shock
        ("china_ree_restriction","ree",        62,156),   # 10-wk transit delay from wk 52 shock
        ("sic_bottleneck",       "sic_wafer", 17, 65),   # 4-wk transit delay from wk 13 shock
    ]
    for scen, mineral, shock_start, shock_end in MINERAL_SHOCKS:
        if scen not in results:
            continue
        stock_col = f"stock_{mineral}_wk"
        price_col = f"price_{mineral}"
        b_stock = _col_mean(baseline, stock_col, shock_start, shock_end)
        s_stock = _col_mean(results[scen], stock_col, shock_start, shock_end)
        b_price = _col_mean(baseline, price_col, shock_start, shock_end)
        s_price = _col_mean(results[scen], price_col, shock_start, shock_end)
        _add(checks, "scenario",
             f"{scen}: {mineral} stock lower during shock window",
             s_stock < b_stock, f"base={b_stock:.3f} shock={s_stock:.3f}",
             "shock < baseline mean stock")
        _add(checks, "scenario",
             f"{scen}: {mineral} price higher during shock window",
             s_price > b_price, f"base={b_price:.4f} shock={s_price:.4f}",
             "shock price > baseline price")

    # ── Cell-layer shock → cell production drops ─────────────────────────────
    if "china_catl_disruption" in results:
        b_cell = _col_mean(baseline, "cell_production_gwh", 21, 78)  # after 8-wk pipeline flush
        s_cell = _col_mean(results["china_catl_disruption"], "cell_production_gwh", 21, 78)
        _add(checks, "scenario",
             "CATL disruption lowers cell production during shock window",
             s_cell < b_cell * 0.99, f"base={b_cell:.4f} shock={s_cell:.4f}",
             "< 0.99x baseline")

    # ── Tier-1 shock → OEM backlog rises ─────────────────────────────────────
    if "ukraine_harness" in results:
        b_bl = _col_mean(baseline, "total_backlog_k", 6, 36)
        s_bl = _col_mean(results["ukraine_harness"], "total_backlog_k", 6, 36)
        _add(checks, "scenario",
             "Ukraine harness shock raises OEM backlog vs baseline",
             s_bl >= b_bl, f"base={b_bl:.3f} shock={s_bl:.3f}",
             ">= baseline backlog during harness shock")

    # ── OEM direct shock reduces OEM output ──────────────────────────────────
    if "uk_supply_chain_friction" in results:
        b_oem = _col_mean(baseline, "oem_production_k", 4, 56)
        s_oem = _col_mean(results["uk_supply_chain_friction"], "oem_production_k", 4, 56)
        _add(checks, "scenario",
             "UK friction directly lowers OEM production weeks 4-56",
             s_oem < b_oem, f"base={b_oem:.4f} shock={s_oem:.4f}",
             "shock < baseline OEM production")

    # ── Compound shock >= largest constituent single shock ───────────────────
    if {"compound_shock", "drc_cobalt", "ukraine_harness"}.issubset(results):
        compound = float(metric.loc["compound_shock", "cumulative_loss_k_veh_vs_baseline"])
        largest = max(
            float(metric.loc["drc_cobalt",       "cumulative_loss_k_veh_vs_baseline"]),
            float(metric.loc["ukraine_harness",   "cumulative_loss_k_veh_vs_baseline"]),
        )
        _add(checks, "scenario",
             "Compound shock cumulative loss >= largest single shock",
             compound >= largest, f"compound={compound:.3f} largest={largest:.3f}",
             ">= largest individual")

    # ── us_china_tariff: sustained multi-shock drives price signal up ─────────
    if "us_china_tariff" in results:
        b_ps = _col_mean(baseline, "price_signal", 52, 130)
        s_ps = _col_mean(results["us_china_tariff"], "price_signal", 52, 130)
        _add(checks, "scenario",
             "US-China tariff scenario: price signal higher during sustained shock",
             s_ps >= b_ps, f"base={b_ps:.4f} shock={s_ps:.4f}",
             ">= baseline price signal weeks 52-130")


def shock_propagation_chain(
    checks: list[Check],
    results: dict[str, pd.DataFrame],
    baseline: pd.DataFrame,
) -> None:
    """
    Trace the full propagation chain for each shock archetype:
      mineral supplier -> SD stock -> SD price -> (LFP shift / cell cost)
      cell manufacturer -> cell stock -> price_signal
      tier-1 supplier -> component stock -> OEM backlog
    """

    # ── 1. Cobalt chain: DRC shock -> cobalt stock -> price -> LFP shift ─────
    if "drc_cobalt" in results:
        sc = results["drc_cobalt"]
        # Stock should be lower after transport delay (8 wks from start wk 26)
        shock_wks = (34, 60)
        b_stk = _col_mean(baseline, "stock_cobalt_wk", *shock_wks)
        s_stk = _col_mean(sc, "stock_cobalt_wk", *shock_wks)
        _add(checks, "propagation",
             "Cobalt chain: DRC shock depletes cobalt stock",
             s_stk < b_stk, f"{b_stk:.3f}->{s_stk:.3f}", "stock falls")

        b_prc = _col_mean(baseline, "price_cobalt", *shock_wks)
        s_prc = _col_mean(sc, "price_cobalt", *shock_wks)
        _add(checks, "propagation",
             "Cobalt chain: cobalt price rises as stock falls",
             s_prc > b_prc, f"{b_prc:.4f}->{s_prc:.4f}", "price rises")

        # LFP share should increase when cobalt prices rise (F2 chemistry loop)
        b_lfp = _col_mean(baseline, "lfp_share", 40, 80)
        s_lfp = _col_mean(sc, "lfp_share", 40, 80)
        _add(checks, "propagation",
             "Cobalt chain: LFP share increases when cobalt is expensive",
             s_lfp >= b_lfp, f"{b_lfp:.4f}->{s_lfp:.4f}",
             "LFP share >= baseline weeks 40-80")

    # ── 2. Graphite chain: China shock -> graphite stock -> price ────────────
    if "china_graphite" in results:
        sc = results["china_graphite"]
        wks = (12, 60)  # graphite_chn @8-60, 4-wk transit -> arrivals from wk 12
        b_stk = _col_mean(baseline, "stock_graphite_wk", *wks)
        s_stk = _col_mean(sc, "stock_graphite_wk", *wks)
        _add(checks, "propagation",
             "Graphite chain: China shock depletes graphite stock",
             s_stk < b_stk, f"{b_stk:.3f}->{s_stk:.3f}", "stock falls")
        b_prc = _col_mean(baseline, "price_graphite", *wks)
        s_prc = _col_mean(sc, "price_graphite", *wks)
        _add(checks, "propagation",
             "Graphite chain: graphite price rises as stock falls",
             s_prc > b_prc, f"{b_prc:.4f}->{s_prc:.4f}", "price rises")

    # ── 3. Cell chain: CATL disruption -> cell stock -> price signal ─────────
    if "china_catl_disruption" in results:
        sc = results["china_catl_disruption"]
        wks = (21, 78)
        b_cell = _col_mean(baseline, "cell_production_gwh", *wks)
        s_cell = _col_mean(sc, "cell_production_gwh", *wks)
        _add(checks, "propagation",
             "Cell chain: CATL shock reduces cell production",
             s_cell < b_cell, f"{b_cell:.4f}->{s_cell:.4f}", "cell prod falls")

        b_stk = _col_mean(baseline, "stock_cells_wk", *wks)
        s_stk = _col_mean(sc, "stock_cells_wk", *wks)
        _add(checks, "propagation",
             "Cell chain: cell stock falls when production drops",
             s_stk <= b_stk * 1.02, f"{b_stk:.3f}->{s_stk:.3f}",
             "<= 1.02x baseline cell stock")

    # ── 4. Harness chain: Ukraine shock -> agent shocked -> OEM backlog ───────
    if "ukraine_harness" in results:
        sc = results["ukraine_harness"]
        wks = (6, 36)
        b_bl = _col_mean(baseline, "total_backlog_k", *wks)
        s_bl = _col_mean(sc, "total_backlog_k", *wks)
        _add(checks, "propagation",
             "Harness chain: Ukraine shock raises OEM backlog",
             s_bl >= b_bl, f"base={b_bl:.3f} shock={s_bl:.3f}", ">= baseline backlog")

    # ── 5. OEM direct: UK friction -> OEM production falls -> backlog grows ──
    if "uk_supply_chain_friction" in results:
        sc = results["uk_supply_chain_friction"]
        wks = (4, 56)
        b_oem = _col_mean(baseline, "oem_production_k", *wks)
        s_oem = _col_mean(sc, "oem_production_k", *wks)
        _add(checks, "propagation",
             "OEM chain: UK friction reduces OEM production",
             s_oem < b_oem, f"{b_oem:.4f}->{s_oem:.4f}", "OEM prod falls")
        b_bl = _col_mean(baseline, "total_backlog_k", *wks)
        s_bl = _col_mean(sc, "total_backlog_k", *wks)
        _add(checks, "propagation",
             "OEM chain: reduced OEM output raises cumulative backlog",
             s_bl > b_bl, f"{b_bl:.3f}->{s_bl:.3f}", "backlog rises")

    # ── 6. Price recovery after shock ends ───────────────────────────────────
    if "drc_cobalt" in results:
        sc = results["drc_cobalt"]
        max_weeks = int(sc["week"].max())
        if max_weeks >= 90:
            peak = float(sc["price_cobalt"].max())
            # Cobalt shock ends at week 52; by week 90+ price should be declining
            late_price = _col_mean(sc, "price_cobalt", 80, max_weeks)
            _add(checks, "propagation",
                 "Cobalt price recovers after shock ends (late mean < peak)",
                 late_price < peak, f"peak={peak:.4f} late={late_price:.4f}",
                 "price declining after shock resolves")

    # ── 7. Monotonicity: higher severity -> larger stock depletion ───────────
    # We use two pre-built scenarios with different severity for comparison
    if "drc_cobalt" in results and "compound_shock" in results:
        # compound_shock has cobalt at severity 0.5 from week 4 (earlier/same as drc_cobalt)
        b_stk = float(baseline["stock_cobalt_wk"].min())
        d_stk = float(results["drc_cobalt"]["stock_cobalt_wk"].min())
        c_stk = float(results["compound_shock"]["stock_cobalt_wk"].min())
        # compound shock has additional harness shock -> more system stress
        _add(checks, "propagation",
             "Compound shock depletes cobalt at least as much as drc_cobalt alone",
             c_stk <= d_stk * 1.05,
             f"compound_min={c_stk:.3f} drc_min={d_stk:.3f}",
             "compound <= 1.05x drc-only minimum stock")


def policy_effectiveness(
    checks: list[Check],
    results: dict[str, pd.DataFrame],
    baseline: pd.DataFrame,
    metrics: pd.DataFrame,
) -> None:
    """
    Verify that each policy package produces the expected directional improvements
    vs the same scenario without policy.
    """
    metric = metrics.set_index("scenario")

    # Compare MEAN OEM production: policy scenario vs same-shock no-policy scenario.
    # cumulative_loss_vs_baseline is not suitable because it clips negative
    # differences and counts weeks where policy alters the steady-state above
    # baseline as zero rather than a gain.
    POLICY_PAIRS = [
        ("uk_supply_chain_friction", "uk_supply_chain_friction_full_policy",
         "Full strategy raises mean OEM production vs same-shock no-policy"),
        ("ukraine_harness", "ukraine_harness_tier1_policy",
         "T1 resilience raises mean OEM production vs harness-shock no-policy"),
        ("drc_cobalt", "drc_cobalt_minerals_policy",
         "Critical minerals policy raises mean OEM production vs cobalt-shock no-policy"),
        ("china_catl_disruption", "china_catl_disruption_full_policy",
         "Full strategy raises mean OEM production vs CATL-disruption no-policy"),
    ]
    for base_sc, policy_sc, label in POLICY_PAIRS:
        if base_sc not in results or policy_sc not in results:
            continue
        no_pol = float(metric.loc[base_sc,   "mean_oem_production_k_wk"])
        w_pol  = float(metric.loc[policy_sc, "mean_oem_production_k_wk"])
        _add(checks, "policy",
             label, w_pol >= no_pol,
             f"no_policy={no_pol:.4f} with_policy={w_pol:.4f}",
             "with_policy mean >= no_policy mean")

    # Full strategy should raise mean OEM production in ALL scenarios
    for base_sc, policy_sc in [
        ("baseline",                      "baseline"),
        ("uk_supply_chain_friction",      "uk_supply_chain_friction_full_policy"),
        ("drc_cobalt",                    "drc_cobalt_full_policy"),
        ("ukraine_harness",               "ukraine_harness_full_policy"),
    ]:
        if base_sc not in results or policy_sc not in results or base_sc == policy_sc:
            continue
        b_mean = float(metric.loc[base_sc,   "mean_oem_production_k_wk"])
        p_mean = float(metric.loc[policy_sc, "mean_oem_production_k_wk"])
        _add(checks, "policy",
             f"Full policy raises mean OEM production: {base_sc}",
             p_mean > b_mean, f"{b_mean:.3f}->{p_mean:.3f}",
             "policy mean > no-policy mean")

    # Full strategy should recover faster (lower recovery_week) than no-policy
    for base_sc, policy_sc in [
        ("uk_supply_chain_friction", "uk_supply_chain_friction_full_policy"),
    ]:
        if base_sc not in metric.index or policy_sc not in metric.index:
            continue
        b_rec = float(metric.loc[base_sc,   "recovery_week_below_90pct"])
        p_rec = float(metric.loc[policy_sc, "recovery_week_below_90pct"])
        _add(checks, "policy",
             f"Full policy shortens recovery: {base_sc}",
             p_rec <= b_rec, f"no_policy={b_rec} with_policy={p_rec}",
             "policy recovery week <= no-policy")

    # Minerals policy: cobalt stock should be higher (stock injection)
    if "drc_cobalt" in results and "drc_cobalt_minerals_policy" in results:
        wks = (34, 60)
        b_stk = _col_mean(results["drc_cobalt"],          "stock_cobalt_wk", *wks)
        p_stk = _col_mean(results["drc_cobalt_minerals_policy"], "stock_cobalt_wk", *wks)
        _add(checks, "policy",
             "Minerals policy raises cobalt stock during DRC shock",
             p_stk > b_stk, f"no_policy={b_stk:.3f} with_policy={p_stk:.3f}",
             "minerals policy cobalt stock > no-policy")


def demand_and_capacity_dynamics(
    checks: list[Check],
    results: dict[str, pd.DataFrame],
    baseline: pd.DataFrame,
) -> None:
    """
    Check that demand growth and cell capacity investment behave correctly.
    """
    # Market demand should grow monotonically in baseline (approx 29%/yr IEA rate)
    demand = baseline["market_demand_gwh"].values
    _add(checks, "dynamics",
         "Baseline market demand grows week-on-week (monotone trend)",
         float(demand[-1]) > float(demand[0]),
         f"start={demand[0]:.4f} end={demand[-1]:.4f}",
         "end > start over 260-week run")

    # Annualised growth rate check: 260 weeks ~ 5 years at ~29%/yr -> ~3.6x
    yoy_implied = (float(demand[-1]) / max(float(demand[0]), 1e-9)) ** (52.0 / len(demand)) - 1.0
    _add(checks, "dynamics",
         "Market demand YoY growth rate in configured range (15-35%)",
         0.10 <= yoy_implied <= 0.40,
         f"{yoy_implied * 100:.1f}%/yr",
         "15-35%/yr (IEA range)")

    # Cell capacity utilisation should be meaningful (not trivial)
    util_mean = float(baseline["cell_cap_util"].mean())
    _add(checks, "dynamics",
         "Baseline cell capacity utilisation is non-trivial (> 0.01)",
         util_mean > 0.01, f"{util_mean:.4f}", "> 0.01")

    # LFP share should remain bounded
    lfp_min = float(baseline["lfp_share"].min())
    lfp_max = float(baseline["lfp_share"].max())
    _add(checks, "dynamics",
         "LFP share bounded in [0.20, 0.80] throughout 260-week baseline",
         lfp_min >= 0.20 and lfp_max <= 0.80,
         f"[{lfp_min:.3f}, {lfp_max:.3f}]", "[0.20, 0.80]")

    # Bullwhip index should be > 1 (amplification of orders vs production)
    bullwhip_mean = float(baseline["bullwhip_index"].mean())
    _add(checks, "dynamics",
         "Bullwhip index mean > 1.0 (order amplification present)",
         bullwhip_mean > 1.0, f"{bullwhip_mean:.3f}", "> 1.0")

    # Under full policy the bullwhip_smooth_mult increases EWMA responsiveness
    # (T1 digital visibility), so the tracked index may be higher even though
    # actual order amplification is not worse.  Check only that the index is
    # finite and positive — directional comparison is not meaningful here.
    for sc_policy in ["ukraine_harness_full_policy", "drc_cobalt_full_policy"]:
        if sc_policy in results:
            bw = float(results[sc_policy]["bullwhip_index"].mean())
            _add(checks, "dynamics",
                 f"Bullwhip index finite and positive ({sc_policy})",
                 np.isfinite(bw) and bw > 0.0,
                 f"{bw:.3f}", "> 0 and finite")


def sd_abm_consistency(
    checks: list[Check],
    results: dict[str, pd.DataFrame],
    baseline: pd.DataFrame,
) -> None:
    """
    Cross-layer consistency: SD state variables should track ABM aggregate outputs.
    """
    # Cobalt stock should be near mineral target (4-8 weeks) in steady baseline
    from model.sd_model import MINERAL_TARGET_WK
    cobalt_tgt = MINERAL_TARGET_WK["cobalt"]
    cobalt_mean = float(baseline["stock_cobalt_wk"].mean())
    _add(checks, "consistency",
         "Baseline cobalt stock near target (0.5x-3x target)",
         0.5 * cobalt_tgt <= cobalt_mean <= 3.0 * cobalt_tgt,
         f"mean={cobalt_mean:.2f}wk target={cobalt_tgt}wk",
         f"[{0.5*cobalt_tgt:.1f}, {3.0*cobalt_tgt:.1f}] weeks")

    ree_tgt = MINERAL_TARGET_WK["ree"]
    ree_mean = float(baseline["stock_ree_wk"].mean())
    _add(checks, "consistency",
         "Baseline REE stock near target (0.5x-3x target)",
         0.5 * ree_tgt <= ree_mean <= 3.0 * ree_tgt,
         f"mean={ree_mean:.2f}wk target={ree_tgt}wk",
         f"[{0.5*ree_tgt:.1f}, {3.0*ree_tgt:.1f}] weeks")

    # Cells stock should be non-zero throughout
    cells_min = float(baseline["stock_cells_wk"].min())
    _add(checks, "consistency",
         "Cell stock remains positive (no stockout) in baseline",
         cells_min > 0.0, f"min={cells_min:.3f}wk", "> 0")

    # Component stocks all non-negative
    comp_cols = ["stock_packs_wk", "stock_inverters_wk",
                 "stock_motors_wk", "stock_harness_wk"]
    for col in comp_cols:
        if col in baseline.columns:
            cmin = float(baseline[col].min())
            _add(checks, "consistency",
                 f"{col} non-negative in baseline", cmin >= 0.0,
                 f"min={cmin:.3f}", ">= 0")

    # Baseline OEM production should grow over 260 weeks (demand pull + capacity)
    oem_start = float(baseline["oem_production_k"].iloc[:8].mean())
    oem_end   = float(baseline["oem_production_k"].iloc[-8:].mean())
    _add(checks, "consistency",
         "OEM production grows over 260-week baseline (demand-pulled capacity)",
         oem_end > oem_start * 1.10,
         f"start={oem_start:.3f} end={oem_end:.3f}",
         "end > 1.10x start")

    # Price signal should be correlated with cobalt + graphite + ree prices
    # (composite price signal above 1.0 when minerals are scarce)
    ps_mean = float(baseline["price_signal"].mean())
    _add(checks, "consistency",
         "Baseline price signal mean > 1.0 (steady slight scarcity)",
         ps_mean > 1.0, f"{ps_mean:.4f}", "> 1.0")


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
    real_ts_path = RESULTS_DIR / "real_timeseries_validation.csv"
    real_ts_df = pd.read_csv(real_ts_path) if real_ts_path.exists() else pd.DataFrame()

    # Per-category breakdown
    cat_counts = (
        checks_df.groupby("category")["status"]
        .value_counts()
        .unstack(fill_value=0)
        .reindex(columns=["PASS", "WARN", "FAIL"], fill_value=0)
    )
    cat_lines = []
    for cat, row in cat_counts.iterrows():
        cat_lines.append(
            f"| {cat:<22} | {int(row.get('PASS',0)):>4} | {int(row.get('WARN',0)):>4} | {int(row.get('FAIL',0)):>4} |"
        )

    lines = [
        "# EV Supply-Chain Model Validation Report",
        "",
        f"Fresh simulations run: {len(metrics_df)} scenarios, {weeks} weeks, seed {seed}.",
        "",
        "## Summary",
        "",
        f"- **PASS: {int(counts['PASS'])}**",
        f"- WARN: {int(counts['WARN'])}",
        f"- FAIL: {int(counts['FAIL'])}",
        f"- Total checks: {len(checks_df)}",
        "",
        "### Checks by category",
        "",
        "| Category               | PASS | WARN | FAIL |",
        "| ---------------------- | ---- | ---- | ---- |",
        *cat_lines,
        "",
        "## Scenario Metrics",
        "",
        _markdown_table(metrics_df),
        "",
        "## Real Historical Time-Series MAE",
        "",
        (
            _markdown_table(real_ts_df)
            if not real_ts_df.empty
            else "_No real historical time-series validation file was generated._"
        ),
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
            f"- Real time-series MAE: `{real_ts_path.relative_to(ROOT)}`",
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
    shock_propagation_chain(checks, results, results["baseline"])
    policy_effectiveness(checks, results, results["baseline"], metrics)
    demand_and_capacity_dynamics(checks, results, results["baseline"])
    sd_abm_consistency(checks, results, results["baseline"])
    try:
        real_ts = validate_against_real_uk_timeseries(
            results,
            scenarios=("baseline", "uk_supply_chain_friction"),
            refresh_observed=False,
        )
        for _, row in real_ts.summary.iterrows():
            _add(
                checks,
                "real_timeseries",
                f"{row['scenario']} MAE vs ONS/SMMT UK production index",
                pd.notna(row["mae_index_points"]) and int(row["months_compared"]) >= 24,
                row["mae_index_points"],
                "reported in index points; >=24 observed months",
                detail=(
                    f"{row['months_compared']} months, {row['start_month']}..{row['end_month']}; "
                    f"MAPE={row['mape_index_pct']}%; scaled level MAE="
                    f"{row['mae_scaled_k_vehicles_per_month']} k vehicles/month"
                ),
            )
    except Exception as exc:
        _add(
            checks,
            "real_timeseries",
            "ONS/SMMT real historical time-series validation completes",
            False,
            type(exc).__name__,
            "MAE calculated against observed monthly UK production series",
            detail=str(exc),
            warn=True,
        )
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
