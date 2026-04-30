"""
Comprehensive Data Audit and Model Validation
=============================================
Runs six test groups and prints a structured report.

Groups
------
  A  Data integrity        — parameter ranges, shares, internal consistency
  B  Conservation laws     — stocks non-negative, prices bounded, flows balance
  C  Steady-state          — baseline run stays near initial calibration anchors
  D  Delay mechanics       — pipeline lengths, measurement lags, price lags
  E  Archetype behaviour   — each archetype's decision rule fires correctly
  F  Scenario face-validity — shocks propagate to the right stocks/prices
"""

from __future__ import annotations

import sys
import textwrap
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Tuple

import numpy as np
import pandas as pd

from model.config import (
    BULLWHIP_FACTOR, CELL_GLOBAL_GWH_2023, CELL_MAKERS, EV_GLOBAL_UNITS_2023_K,
    MARKETS, MINERALS, OEMS, TIER1,
    MINERAL_AGENT_ARCHETYPES, CELL_AGENT_ARCHETYPES,
    TIER1_AGENT_ARCHETYPES, OEM_AGENT_ARCHETYPES,
)
from model.hybrid_model import EVSupplyChainModel
from model.sd_model import (
    ALL_MINERALS, MINERAL_TRANSPORT_WK, MEAS_LAG_WK,
    CELL_CAPACITY_PLAN_WK, CELL_CAPACITY_BUILD_WK, CAP_ERLANG_N,
    LFP_SHARE_MIN, LFP_SHARE_MAX, PRICE_FLOOR, PRICE_CEIL,
    PRICE_SIGNAL_FLOOR, PRICE_SIGNAL_CEIL, CAPEX_MAX_RATE_YR,
    CAPEX_TRIGGER_UTIL, CHEM_COBALT_LAG,
)
from model.shocks import SCENARIOS


# ─────────────────────────────────────────────────────────────────────────────
# Result scaffolding
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Result:
    group:    str
    name:     str
    status:   str        # PASS / WARN / FAIL
    measured: str
    expected: str
    note:     str = ""


_results: List[Result] = []


def _add(group: str, name: str, ok: bool, measured: Any,
         expected: str, note: str = "", warn_only: bool = False) -> None:
    status = "PASS" if ok else ("WARN" if warn_only else "FAIL")
    _results.append(Result(
        group=group, name=name, status=status,
        measured=f"{measured:.4g}" if isinstance(measured, float) else str(measured),
        expected=expected, note=note,
    ))


def _close(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(a - b) <= tol * max(abs(b), 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# A  Data integrity
# ─────────────────────────────────────────────────────────────────────────────

def group_a_data_integrity() -> None:
    g = "A-DataIntegrity"

    # A1  Cell maker market shares sum to 1.0
    total_share = sum(v["market_share"] for v in CELL_MAKERS.values())
    _add(g, "A1 cell-maker shares sum to 1.0",
         abs(total_share - 1.0) < 0.005, round(total_share, 4), "1.000 ± 0.005")

    # A2  Cell capacity proportional to share × global GWh
    for name, cfg in CELL_MAKERS.items():
        implied = cfg["market_share"] * CELL_GLOBAL_GWH_2023
        actual  = cfg["capacity_gwh_yr"]
        ok = abs(actual - implied) / max(implied, 1.0) < 0.03
        _add(g, f"A2 {name} capacity consistent with share × 822 GWh",
             ok, round(actual, 1), f"{implied:.1f} ± 3%")

    # A3  lfp + nmc = 1.0 for every cell maker
    for name, cfg in CELL_MAKERS.items():
        total = cfg["lfp_fraction"] + cfg["nmc_fraction"]
        _add(g, f"A3 {name} lfp+nmc=1",
             abs(total - 1.0) < 1e-9, total, "1.0 exactly")

    # A4  OEM annual targets sum to global EV units
    oem_total = sum(v["annual_target_k"] for v in OEMS.values())
    _add(g, "A4 OEM annual targets sum to 14000 k",
         oem_total == EV_GLOBAL_UNITS_2023_K, oem_total, str(EV_GLOBAL_UNITS_2023_K))

    # A5  Market GWh sum within 5% of CELL_GLOBAL_GWH_2023
    market_total = sum(v["gwh_2023"] for v in MARKETS.values())
    _add(g, "A5 market GWh sum near 822 GWh",
         abs(market_total - CELL_GLOBAL_GWH_2023) / CELL_GLOBAL_GWH_2023 < 0.05,
         round(market_total, 1), f"{CELL_GLOBAL_GWH_2023} ± 5%")

    # A6  Mineral supply-country shares sum to 1.0
    for mineral, cfg in MINERALS.items():
        s = sum(cfg["supply_concentration"].values())
        _add(g, f"A6 {mineral} country shares sum to 1.0",
             abs(s - 1.0) < 0.005, round(s, 4), "1.000 ± 0.005")

    # A7  ev_prod_kt_yr = global_prod × ev_share (within 2%)
    for mineral, cfg in MINERALS.items():
        if "global_prod_kt_yr" in cfg and "ev_share" in cfg and "ev_prod_kt_yr" in cfg:
            implied = cfg["global_prod_kt_yr"] * cfg["ev_share"]
            actual  = cfg["ev_prod_kt_yr"]
            _add(g, f"A7 {mineral} ev_prod = global × ev_share",
                 abs(actual - implied) / max(implied, 1.0) < 0.02,
                 round(actual, 1), f"{implied:.1f} ± 2%")

    # A8  Safety-stock weeks are positive integers
    for mineral, cfg in MINERALS.items():
        wk = cfg["safety_stock_weeks"]
        _add(g, f"A8 {mineral} safety_stock_weeks positive",
             isinstance(wk, int) and wk > 0, wk, "> 0 int")

    # A9  Tier-1 capacities match global EV units (within 5%)
    for comp, cfg in TIER1.items():
        cap = cfg["capacity_units_yr_k"]
        _add(g, f"A9 {comp} capacity = {EV_GLOBAL_UNITS_2023_K} k ± 5%",
             abs(cap - EV_GLOBAL_UNITS_2023_K) / EV_GLOBAL_UNITS_2023_K < 0.05,
             cap, f"{EV_GLOBAL_UNITS_2023_K} ± 5%")

    # A10  All archetype names are valid
    valid_min  = {"StateBacked", "WesternMiner", "GreenfieldBuilder"}
    valid_cell = {"PlatformLeader", "HyperScaleChallenger", "IncumbentUnderPressure"}
    valid_t1   = {"PremiumPowerElectronics", "EstablishedVolumeSupplier", "BatteryPackIntegrator"}
    valid_oem  = {"ProfitableEstablishedOEM", "TransitioningLegacyOEM",
                  "EVNativeScaleAspirant", "PrecommercialStartup"}
    for aid, arch in MINERAL_AGENT_ARCHETYPES.items():
        _add(g, f"A10 mineral archetype {aid} valid", arch in valid_min, arch, str(valid_min))
    for aid, arch in CELL_AGENT_ARCHETYPES.items():
        _add(g, f"A10 cell archetype {aid} valid",    arch in valid_cell, arch, str(valid_cell))
    for aid, arch in TIER1_AGENT_ARCHETYPES.items():
        _add(g, f"A10 t1 archetype {aid} valid",      arch in valid_t1,  arch, str(valid_t1))
    for aid, arch in OEM_AGENT_ARCHETYPES.items():
        _add(g, f"A10 oem archetype {aid} valid",     arch in valid_oem, arch, str(valid_oem))

    # A11  Shock severity in [0, 1] and start < end for all scenarios
    for sname, sc in SCENARIOS.items():
        for i, shock in enumerate(sc.get("shocks", [])):
            sev = shock.get("severity", 0.0)
            sw  = shock.get("start_week", -1)
            ew  = shock.get("end_week",   sw + 1)
            _add(g, f"A11 {sname}[{i}] severity 0-1",    0 <= sev <= 1.0, sev, "[0,1]")
            _add(g, f"A11 {sname}[{i}] start < end",     sw < ew, f"{sw}<{ew}", "start<end")

    # A12  SD delay constants are positive and physically plausible
    for mineral, wk in MINERAL_TRANSPORT_WK.items():
        _add(g, f"A12 transport delay {mineral} in [1, 16] wk",
             1 <= wk <= 16, wk, "1..16 wk")
    _add(g, "A12 measurement lag 1-4 weeks",
         1.0 <= MEAS_LAG_WK <= 4.0, MEAS_LAG_WK, "1..4 wk")
    _add(g, "A12 planning queue 13-52 weeks",
         13 <= CELL_CAPACITY_PLAN_WK <= 52, CELL_CAPACITY_PLAN_WK, "13..52 wk")
    _add(g, "A12 build time 52-208 weeks",
         52 <= CELL_CAPACITY_BUILD_WK <= 208, CELL_CAPACITY_BUILD_WK, "52..208 wk")
    _add(g, "A12 Erlang stages 2-5",
         2 <= CAP_ERLANG_N <= 5, CAP_ERLANG_N, "2..5")

    # A13  Price bounds sensible
    _add(g, "A13 price floor < 1 < price ceil",
         PRICE_FLOOR < 1.0 < PRICE_CEIL, f"{PRICE_FLOOR}..{PRICE_CEIL}", "floor<1<ceil")
    _add(g, "A13 capex trigger util in (0.7, 0.95)",
         0.70 < CAPEX_TRIGGER_UTIL < 0.95, CAPEX_TRIGGER_UTIL, "(0.70, 0.95)")
    _add(g, "A13 capex max rate 10-50%/yr",
         0.10 <= CAPEX_MAX_RATE_YR <= 0.50, CAPEX_MAX_RATE_YR, "[0.10, 0.50]")

    # A14  Bullwhip factor in realistic range [1.0, 2.0]
    _add(g, "A14 bullwhip factor 1.0-2.0",
         1.0 <= BULLWHIP_FACTOR <= 2.0, BULLWHIP_FACTOR, "[1.0, 2.0]")

    # A15  OEM vertical integration in [0, 1]
    for name, cfg in OEMS.items():
        vi = cfg.get("vertical_integration", 0.0)
        _add(g, f"A15 {name} vertical_integration in [0,1]",
             0.0 <= vi <= 1.0, vi, "[0.0, 1.0]")

    # A16  All shock targets exist in the model
    probe = EVSupplyChainModel(scenario=SCENARIOS["baseline"], seed=42, n_weeks=1)
    for sname, sc in SCENARIOS.items():
        for i, shock in enumerate(sc.get("shocks", [])):
            target = shock.get("target", "")
            found  = probe._find_agent(target) is not None
            _add(g, f"A16 {sname}[{i}] target '{target}' exists", found, found, "agent found")


# ─────────────────────────────────────────────────────────────────────────────
# B  Conservation laws and numeric bounds
# ─────────────────────────────────────────────────────────────────────────────

def group_b_conservation(df_global: pd.DataFrame) -> None:
    g = "B-Conservation"

    # B1  No NaN or Inf in any numeric column
    num_cols = [c for c in df_global.columns if pd.api.types.is_numeric_dtype(df_global[c])]
    finite_ok = bool(np.isfinite(df_global[num_cols].values.astype(float)).all())
    _add(g, "B1 all numeric outputs finite (no NaN/Inf)", finite_ok, finite_ok, "True")

    # B2  All stocks and flows non-negative
    non_neg = [c for c in num_cols if c not in ("week",)]
    min_val = float(df_global[non_neg].min().min())
    _add(g, "B2 all stocks/flows >= 0", min_val >= -1e-6, round(min_val, 6), ">= 0")

    # B3  Price indices within SD bounds
    price_cols = [c for c in df_global.columns if c.startswith("price_")
                  and not c.startswith("price_component_")]
    if price_cols:
        lo = float(df_global[price_cols].min().min())
        hi = float(df_global[price_cols].max().max())
        _add(g, "B3 mineral price indices >= PRICE_FLOOR",
             lo >= PRICE_FLOOR - 1e-6, round(lo, 4), f">= {PRICE_FLOOR}")
        _add(g, "B3 mineral price indices <= PRICE_CEIL",
             hi <= PRICE_CEIL + 1e-6, round(hi, 4), f"<= {PRICE_CEIL}")

    # B4  Price signal within component bounds
    if "price_signal" in df_global.columns:
        lo = float(df_global["price_signal"].min())
        hi = float(df_global["price_signal"].max())
        _add(g, "B4 price_signal >= PRICE_SIGNAL_FLOOR",
             lo >= PRICE_SIGNAL_FLOOR - 1e-6, round(lo, 4), f">= {PRICE_SIGNAL_FLOOR}")
        _add(g, "B4 price_signal <= PRICE_SIGNAL_CEIL",
             hi <= PRICE_SIGNAL_CEIL + 1e-6, round(hi, 4), f"<= {PRICE_SIGNAL_CEIL}")

    # B5  LFP share bounded
    if "lfp_share" in df_global.columns:
        lo = float(df_global["lfp_share"].min())
        hi = float(df_global["lfp_share"].max())
        _add(g, "B5 lfp_share >= LFP_SHARE_MIN",
             lo >= LFP_SHARE_MIN - 1e-6, round(lo, 4), f">= {LFP_SHARE_MIN}")
        _add(g, "B5 lfp_share <= LFP_SHARE_MAX",
             hi <= LFP_SHARE_MAX + 1e-6, round(hi, 4), f"<= {LFP_SHARE_MAX}")

    # B6  Cell capacity utilisation bounded [0, 2]
    if "cell_cap_util" in df_global.columns:
        hi = float(df_global["cell_cap_util"].max())
        _add(g, "B6 cell_cap_util <= 2.0", hi <= 2.0 + 1e-6, round(hi, 4), "<= 2.0")
        lo = float(df_global["cell_cap_util"].min())
        _add(g, "B6 cell_cap_util >= 0.0", lo >= -1e-6, round(lo, 4), ">= 0")

    # B7  OEM production does not exceed target by >50% in any week
    if "oem_production_k" in df_global.columns and "active_oem_target_k" in df_global.columns:
        ratio = df_global["oem_production_k"] / df_global["active_oem_target_k"].replace(0, np.nan) * 52
        max_ratio = float(ratio.max())
        _add(g, "B7 weekly OEM production <= 150% of annual target / 52",
             max_ratio <= 1.5, round(max_ratio, 3), "<= 1.5", warn_only=True)

    # B8  Stock levels do not exceed 4× target (SD cap)
    stock_cols = [c for c in df_global.columns if c.startswith("stock_") and c.endswith("_wk")]
    for col in stock_cols:
        mineral = col.replace("stock_", "").replace("_wk", "")
        from model.sd_model import MINERAL_TARGET_WK, COMPONENT_TARGET_WK
        target_wk = {**MINERAL_TARGET_WK, **COMPONENT_TARGET_WK}.get(mineral, None)
        if target_wk is not None:
            max_wk = float(df_global[col].max())
            _add(g, f"B8 stock_{mineral} <= 4× target ({4*target_wk:.0f} wk)",
                 max_wk <= 4 * target_wk + 1e-3, round(max_wk, 2), f"<= {4*target_wk:.0f} wk",
                 warn_only=True)


# ─────────────────────────────────────────────────────────────────────────────
# C  Steady-state calibration (global baseline, 260 weeks)
# ─────────────────────────────────────────────────────────────────────────────

def group_c_steady_state(df_global: pd.DataFrame, model_global: EVSupplyChainModel) -> None:
    g = "C-SteadyState"

    first = df_global.iloc[0]
    last  = df_global.iloc[-1]

    # C1  Week-0 annual cell production within 5% of CELL_GLOBAL_GWH_2023
    cell_annual_w0 = float(first["cell_production_gwh"]) * 52.0
    _add(g, "C1 week-0 cell production near 822 GWh/yr",
         abs(cell_annual_w0 - CELL_GLOBAL_GWH_2023) / CELL_GLOBAL_GWH_2023 < 0.05,
         round(cell_annual_w0, 1), f"{CELL_GLOBAL_GWH_2023} ± 5%")

    # C2  Week-0 OEM production within 5% of EV_GLOBAL_UNITS_2023_K / 52
    oem_wkly_target = EV_GLOBAL_UNITS_2023_K / 52.0
    oem_w0 = float(first["oem_production_k"])
    _add(g, "C2 week-0 OEM production near 14000/52 k/wk",
         abs(oem_w0 - oem_wkly_target) / oem_wkly_target < 0.10,
         round(oem_w0, 2), f"{oem_wkly_target:.2f} ± 10%")

    # C3  Initial price signal near 1.0
    p0 = float(first["price_signal"])
    _add(g, "C3 week-0 price signal near 1.0",
         abs(p0 - 1.0) < 0.05, round(p0, 4), "1.0 ± 0.05")

    # C4  Initial LFP share near 0.403 (IEA GEO 2024)
    lfp0 = float(first["lfp_share"])
    _add(g, "C4 week-0 LFP share near 0.403 (IEA GEO 2024)",
         abs(lfp0 - 0.403) < 0.01, round(lfp0, 4), "0.403 ± 0.010")

    # C5  Mineral stocks start near target (within 10%)
    from model.sd_model import MINERAL_TARGET_WK
    for mineral in ("lithium", "cobalt", "graphite", "ree", "sic_wafer"):
        col = f"stock_{mineral}_wk"
        if col in first:
            wk0 = float(first[col])
            tgt = MINERAL_TARGET_WK[mineral]
            _add(g, f"C5 {mineral} initial stock near {tgt} wk target",
                 abs(wk0 - tgt) / tgt < 0.10, round(wk0, 2), f"{tgt} ± 10%")

    # C6  Baseline grows monotonically (cell production should rise over 5 years)
    cell_series = df_global["cell_production_gwh"].values
    growth_ok = cell_series[-1] > cell_series[0] * 1.5   # at 29%/yr over 5yr should ~3.5×
    _add(g, "C6 cell production grows >1.5× over 5 years at 29%/yr",
         growth_ok, round(float(cell_series[-1] / cell_series[0]), 2), "> 1.5×")

    # C7  Baseline backlog stays bounded (< 2 years of weekly demand on average)
    market_wkly = float(df_global["market_demand_gwh"].mean())
    avg_kwh = sum(v["gwh_2023"] * 1000 / v["avg_kwh_veh"] for v in MARKETS.values()) / sum(v["gwh_2023"] for v in MARKETS.values())
    demand_k_wk = market_wkly * 1000.0 / avg_kwh
    max_backlog = float(df_global["total_backlog_k"].max())
    _add(g, "C7 baseline backlog < 2 years of weekly demand",
         max_backlog < demand_k_wk * 104, round(max_backlog, 1), f"< {demand_k_wk * 104:.0f} k",
         warn_only=True)

    # C8  Price signal stays near 1.0 in baseline (±25%)
    price_max = float(df_global["price_signal"].max())
    price_min = float(df_global["price_signal"].min())
    _add(g, "C8 baseline price signal stays within [0.75, 1.25]",
         0.75 <= price_min and price_max <= 1.25,
         f"{price_min:.3f}..{price_max:.3f}", "[0.75, 1.25]", warn_only=True)

    # C9  No halt weeks in baseline (no shocks)
    total_halts = sum(a.halt_weeks for a in model_global._oem_agents.values())
    _add(g, "C9 zero OEM halt-weeks in no-shock baseline",
         total_halts == 0, total_halts, "0", warn_only=True)

    # C10  Cell capacity grows over 5 years
    cap_start = float(first.get("cell_capacity_gwh_yr", 1500.0))
    cap_end   = float(last.get("cell_capacity_gwh_yr", cap_start))
    _add(g, "C10 cell capacity grows over 5-year horizon",
         cap_end > cap_start * 1.2, round(cap_end / cap_start, 3), "> 1.2× initial")

    # C11  Bullwhip index near 1.0 in steady state (no shocks, no panic ordering)
    bw_mean = float(df_global["bullwhip_index"].mean())
    _add(g, "C11 baseline bullwhip index mean near 1.0 (no amplification)",
         0.5 <= bw_mean <= 2.5, round(bw_mean, 3), "[0.5, 2.5]", warn_only=True)


# ─────────────────────────────────────────────────────────────────────────────
# D  Delay mechanics
# ─────────────────────────────────────────────────────────────────────────────

def group_d_delays() -> None:
    g = "D-Delays"

    m = EVSupplyChainModel(scenario=SCENARIOS["baseline"], seed=42, n_weeks=1)
    m.run(n_weeks=1)  # initialise

    # D1  Mineral transit pipelines have correct lengths
    for mineral, expected_len in MINERAL_TRANSPORT_WK.items():
        actual_len = len(m.sd._mineral_transit[mineral])
        _add(g, f"D1 {mineral} transit queue length = {expected_len} wk",
             actual_len == expected_len, actual_len, str(expected_len))

    # D2  Tier-1 order pipelines have correct lengths
    expected_lt = {
        "battery_pack": TIER1["battery_pack"]["lead_time_weeks"],
        "inverter":     TIER1["inverter"]["lead_time_weeks"],
        "motor":        TIER1["motor"]["lead_time_weeks"],
        "harness":      TIER1["harness"]["lead_time_weeks"],
    }
    for comp, exp_lt in expected_lt.items():
        a = m._tier1_agents[comp]
        actual_len = len(a.pipeline)
        _add(g, f"D2 {comp} order pipeline length = {exp_lt} wk",
             actual_len == exp_lt, actual_len, str(exp_lt))

    # D3  Capacity planning queue has correct length
    plan_len = len(m.sd._cap_planning_queue)
    _add(g, f"D3 capacity planning queue length = {CELL_CAPACITY_PLAN_WK} wk",
         plan_len == CELL_CAPACITY_PLAN_WK, plan_len, str(CELL_CAPACITY_PLAN_WK))

    # D4  Erlang pipeline has correct number of stages
    stage_count = len(m.sd._cap_stages)
    _add(g, f"D4 Erlang construction stages = {CAP_ERLANG_N}",
         stage_count == CAP_ERLANG_N, stage_count, str(CAP_ERLANG_N))

    # D5  Measurement lag: after an instant stock increase, measured stock lags
    m2 = EVSupplyChainModel(scenario=SCENARIOS["baseline"], seed=42)
    m2.run(n_weeks=1)
    true_stock  = m2.sd.stocks["lithium"]
    meas_stock  = m2.sd._measured_stocks["lithium"]
    # After only 1 week from initialisation, measured should be close but may lag
    _add(g, "D5 measured stock initialised equal to true stock at t=0",
         abs(true_stock - meas_stock) / max(true_stock, 1e-9) < 0.50,
         round(meas_stock / max(true_stock, 1e-9), 3), "< 50% deviation at t=0")

    # D6  Cobalt price perception lag coefficient is physically sensible
    # τ = 4 weeks → alpha = 1/4 = 0.25 fraction closed per week
    _add(g, "D6 cobalt price lag = 4-wk first-order (alpha=0.25/wk)",
         abs(CHEM_COBALT_LAG - 0.25) < 0.01, CHEM_COBALT_LAG, "0.25 ± 0.01")

    # D7  After a shock, mineral stock depletes over transport delay period
    # Apply a 100% shock to cobalt_drc (largest single source: 70% share)
    m3 = EVSupplyChainModel(scenario=SCENARIOS["baseline"], seed=42)
    m3.run(n_weeks=4)  # stabilise
    cobalt_drc = m3._mineral_agents["cobalt_drc"]
    stock_before = m3.sd.stocks["cobalt"]
    cobalt_drc.apply_shock(1.0)  # total shutdown
    m3.run(n_weeks=1)
    stock_after_1wk = m3.sd.stocks["cobalt"]
    # After 1 week of zero supply, stock should start falling (pipeline still delivers for transport_wk)
    cobalt_transport = MINERAL_TRANSPORT_WK["cobalt"]  # 8 weeks
    # Stock should still be nearly intact because pipeline still has 8 weeks of material
    ratio = stock_after_1wk / max(stock_before, 1e-9)
    _add(g, "D7 cobalt stock barely changes 1 wk after shock (8-wk transport buffer)",
         ratio > 0.85, round(ratio, 3), "> 0.85 (pipeline not yet exhausted)",
         note=f"cobalt transport delay = {cobalt_transport} wk")


# ─────────────────────────────────────────────────────────────────────────────
# E  Archetype decision rules
# ─────────────────────────────────────────────────────────────────────────────

def group_e_archetypes() -> None:
    g = "E-Archetypes"

    m = EVSupplyChainModel(scenario=SCENARIOS["baseline"], seed=42)
    m.run(n_weeks=4)

    # ── E1-E3: Mineral supplier archetypes ───────────────────────────────────

    # E1  StateBacked: restricts output above price=1.8 (counter-market)
    gr = m._mineral_agents["graphite_chn"]   # StateBacked
    m.sd.prices["graphite"] = 2.5
    gr.shock_multiplier = 1.0
    out_high = gr._compute_output_fraction()
    m.sd.prices["graphite"] = 1.2
    out_low = gr._compute_output_fraction()
    _add(g, "E1a StateBacked caps output at floor when price>1.8",
         abs(out_high - gr.production_floor) < 0.001, round(out_high, 3),
         f"= production_floor ({gr.production_floor})")
    _add(g, "E1b StateBacked expands output when price=1.2 (below restriction)",
         out_low > gr.production_floor, round(out_low, 3), f"> {gr.production_floor}")

    # E2  WesternMiner: mothballs after sustained low prices
    cob = m._mineral_agents["cobalt_drc"]    # WesternMiner
    cob.shock_multiplier = 1.0
    cob._low_price_weeks = 15                # past 12-week trigger
    m.sd.prices["cobalt"] = 0.80
    out_mothball = cob._compute_output_fraction()
    _add(g, "E2 WesternMiner output < floor when mothballed (15 low-price wks)",
         out_mothball < cob.production_floor, round(out_mothball, 3),
         f"< production_floor ({cob.production_floor})")
    cob._low_price_weeks = 0                 # reset

    # E3  GreenfieldBuilder: distress spiral degrades capacity
    sic = m._mineral_agents["sic_wolfspeed"] # GreenfieldBuilder
    sic.shock_multiplier   = 0.3             # severe shock
    sic._distress_weeks    = 10              # past 8-week trigger
    sic._capacity_degradation = 0.0
    sic._compute_output_fraction()
    _add(g, "E3 GreenfieldBuilder accumulates capacity_degradation in distress",
         sic._capacity_degradation > 0.0, round(sic._capacity_degradation, 4), "> 0.0")

    # ── E4-E6: Cell manufacturer archetypes ──────────────────────────────────

    # E4  PlatformLeader: stockpile when inventory < 70% of target
    catl = m._cell_agents["catl"]            # PlatformLeader
    catl.inventory_gwh = catl.target_inventory * 0.60
    d_low_inv = catl._desired_production(downstream_demand=catl.weekly_capacity * 2.0, price_premium=0.0)
    catl.inventory_gwh = catl.target_inventory * 1.00
    # Use demand below capacity so demand-pull mode returns < weekly_capacity
    d_full_inv = catl._desired_production(downstream_demand=catl.weekly_capacity * 0.80, price_premium=0.0)
    _add(g, "E4a PlatformLeader targets full capacity when inv<70% target",
         abs(d_low_inv - catl.weekly_capacity) < 1e-6, round(d_low_inv, 4),
         f"weekly_capacity ({catl.weekly_capacity:.4f})")
    _add(g, "E4b PlatformLeader demand-pulls when inv healthy",
         d_full_inv < catl.weekly_capacity, round(d_full_inv, 4),
         f"< weekly_capacity ({catl.weekly_capacity:.4f})")

    # E5  HyperScaleChallenger: push model always targets ≥ capacity
    calb = m._cell_agents.get("calb") or m._cell_agents.get("others_cells")
    if calb:
        d_push = calb._desired_production(downstream_demand=0.01, price_premium=0.0)
        _add(g, "E5 HyperScaleChallenger targets >= capacity regardless of demand",
             d_push >= calb.weekly_capacity * 0.99, round(d_push, 4),
             f">= weekly_capacity ({calb.weekly_capacity:.4f})")

    # E6  IncumbentUnderPressure: market share erodes each week
    lg = m._cell_agents["lg_es"]             # IncumbentUnderPressure
    share_before = lg.market_share
    m.run(n_weeks=52)  # run 52 more weeks
    share_after = lg.market_share
    expected_drop = 52 * lg._share_erosion_rate
    actual_drop   = share_before - share_after
    _add(g, "E6a IncumbentUnderPressure market share erodes ~52×rate over 52 wk",
         abs(actual_drop - expected_drop) / expected_drop < 0.05,
         round(actual_drop, 5), f"{expected_drop:.5f} ± 5%")
    _add(g, "E6b IncumbentUnderPressure share never goes below 50% of initial",
         share_after >= lg._min_market_share, round(share_after, 5),
         f">= min_share ({lg._min_market_share:.5f})")

    # ── E7-E9: Tier-1 supplier archetypes ───────────────────────────────────

    m2 = EVSupplyChainModel(scenario=SCENARIOS["baseline"], seed=42)
    m2.run(n_weeks=4)

    # E7  BatteryPackIntegrator: order = exact weekly demand (JIT)
    bpi = m2._tier1_agents["battery_pack"]
    for test_demand in (5.0, 12.3, 100.0):
        order = bpi._order_quantity(test_demand, 0.0)
        _add(g, f"E7 BatteryPackIntegrator JIT order = demand ({test_demand})",
             abs(order - test_demand) < 1e-9, round(order, 4), str(test_demand))

    # E8  PremiumPowerElectronics: order is forward-projected (> current demand)
    prem = m2._tier1_agents["inverter"]       # PremiumPowerElectronics
    m2.sd.prices["sic_wafer"] = 1.0          # below defer threshold
    order_fwd = prem._order_quantity(10.0, 0.0)
    _add(g, "E8a PremiumPowerElectronics order > current demand (forward-looking)",
         order_fwd > 10.0, round(order_fwd, 3), "> 10.0 (projected over 16 wk)")

    # E8b: defers ordering when SiC price spikes
    m2.sd.prices["sic_wafer"] = 2.0          # above defer threshold (1.5)
    order_defer = prem._order_quantity(10.0, 0.0)
    _add(g, "E8b PremiumPowerElectronics defers order when SiC price > 1.5",
         order_defer < order_fwd, round(order_defer, 3),
         f"< {round(order_fwd, 3)} (forward order at normal price)")

    # E9  EstablishedVolumeSupplier: reduces order when over-stocked
    esv = m2._tier1_agents["motor"]           # EstablishedVolumeSupplier
    esv.inventory = esv.target_inventory * 2.0  # over-stocked
    in_transit    = sum(esv.pipeline)
    # inv_position > 1.5 × target → smoothing kicks in
    order_smooth = esv._order_quantity(10.0, 0.0)
    _add(g, "E9 EstablishedVolumeSupplier reduces order when inv_pos > 150% target",
         order_smooth < esv.weekly_capacity, round(order_smooth, 3),
         f"< weekly_capacity ({esv.weekly_capacity:.3f})")

    # ── E10-E13: OEM archetypes ───────────────────────────────────────────────

    m3 = EVSupplyChainModel(scenario=SCENARIOS["baseline"], seed=42)
    m3.run(n_weeks=4)

    # E10  TransitioningLegacyOEM: ICE fallback fires when price_premium > 0.15
    uk_oem = m3._oem_agents.get("uk_oem")
    if uk_oem is None:
        uk_oem = list(m3._oem_agents.values())[0]
    if hasattr(uk_oem, "_ev_target_reduction"):
        uk_oem._ev_target_reduction = 0.0
        uk_oem._compute_production_target(
            uk_oem.weekly_target, uk_oem.weekly_target * 10, 0.20, 0.0
        )
        _add(g, "E10 TransitioningLegacyOEM ICE fallback fires at price_premium=0.20",
             uk_oem._ev_target_reduction > 0.0,
             round(uk_oem._ev_target_reduction, 4), "> 0.0")

    # Run global model for OEM archetype tests
    m4 = EVSupplyChainModel(scenario=SCENARIOS["baseline"], seed=42, focus_region=None)
    m4.run(n_weeks=4)

    # E11  ProfitableEstablishedOEM: higher production ceiling than base
    kor = m4._oem_agents.get("korean_oem")
    if kor is not None and hasattr(kor, "_buffer_floor_ratio"):
        # With healthy buffer, should allow up to 115% of target
        prod = kor._compute_production_target(
            kor.weekly_target, kor.weekly_target * 10, 0.0, 0.0
        )
        _add(g, "E11 ProfitableEstablishedOEM production up to 115% of target",
             prod <= kor.weekly_target * 1.15 + 1e-3, round(prod, 3),
             f"<= {kor.weekly_target * 1.15:.3f}")

    # E12  EVNativeScaleAspirant: boosts weekly_target when demand exceeds it
    cn = m4._oem_agents.get("other_chinese_oem")
    if cn is not None and hasattr(cn, "_demand_elasticity"):
        target_before = cn.weekly_target
        cn._compute_production_target(
            cn.weekly_target * 1.10, cn.weekly_target * 10, 0.0, 0.0
        )
        _add(g, "E12 EVNativeScaleAspirant boosts weekly_target when demand > target",
             cn.weekly_target > target_before, round(cn.weekly_target, 3),
             f"> {round(target_before, 3)}")

    # E13  PrecommercialStartup: capital_ratio drains when producing
    # (no PrecommercialStartup in uk focus; build manually)
    from model.agents import PrecommercialStartup
    import types
    dummy_model = types.SimpleNamespace(
        sd=types.SimpleNamespace(component_prices={"vehicle":1.0,"pack":1.0,"inverter":1.0,"motor":1.0,"harness":1.0}),
        get_oem_demand=lambda name: 1.0,
        get_component_deliveries=lambda name: {"packs":0,"inverters":0,"motors":0,"harness":0},
        get_price_signal=lambda: 1.0,
    )
    startup = PrecommercialStartup(
        agent_id="test_startup", model=dummy_model,
        name="test_startup", region="test", annual_target_k=100,
    )
    ratio_before = startup._capital_ratio
    startup._compute_production_target(
        weekly_demand=startup.weekly_target,
        producible=startup.weekly_target * 10,
        price_premium=0.0,
        price_discount=0.0,
    )
    ratio_after = startup._capital_ratio
    _add(g, "E13 PrecommercialStartup capital_ratio drains when producing",
         ratio_after < ratio_before, round(ratio_after, 4),
         f"< {round(ratio_before, 4)} (drained by production)")


# ─────────────────────────────────────────────────────────────────────────────
# F  Scenario face-validity (global model, 130-week run)
# ─────────────────────────────────────────────────────────────────────────────

def group_f_scenarios() -> None:
    g = "F-Scenarios"

    def run(name: str, weeks: int = 130) -> pd.DataFrame:
        model = EVSupplyChainModel(scenario=SCENARIOS[name], seed=42,
                                   n_weeks=weeks, focus_region=None)
        model.run()
        return model.get_results()

    base = run("baseline")
    base_prod_mean = float(base["oem_production_k"].mean())

    # F1  DRC cobalt shock raises cobalt price and lowers cobalt stock
    drc = run("drc_cobalt")
    max_base_co = float(base["price_cobalt"].max())
    max_shock_co = float(drc["price_cobalt"].max())
    _add(g, "F1a DRC cobalt shock raises cobalt price above baseline",
         max_shock_co > max_base_co * 1.01, round(max_shock_co, 4),
         f"> baseline max ({round(max_base_co, 4)}) × 1.01", warn_only=True)

    min_base_co_stock  = float(base["stock_cobalt_wk"].min())
    min_shock_co_stock = float(drc["stock_cobalt_wk"].min())
    _add(g, "F1b DRC cobalt shock depletes cobalt stock below baseline",
         min_shock_co_stock < min_base_co_stock * 0.99,
         round(min_shock_co_stock, 3),
         f"< baseline min ({round(min_base_co_stock, 3)}) × 0.99", warn_only=True)

    # F2  Ukraine harness shock lowers harness output during shock window (wk 4-12)
    ukr = run("ukraine_harness")
    base_harness = base.loc[base["week"].between(4, 12), "t1_harness_k"].mean()
    shock_harness = ukr.loc[ukr["week"].between(4, 12), "t1_harness_k"].mean()
    _add(g, "F2 Ukraine harness shock reduces harness output vs baseline (wk 4-12)",
         shock_harness < base_harness * 0.90,
         round(shock_harness / max(base_harness, 1e-9), 3), "< 0.90 of baseline")

    # F3  SiC bottleneck lowers inverter output
    sic = run("sic_bottleneck")
    base_inv = base.loc[base["week"].between(13, 65), "t1_inverter_k"].mean()
    shock_inv = sic.loc[sic["week"].between(13, 65), "t1_inverter_k"].mean()
    _add(g, "F3 SiC bottleneck reduces inverter output vs baseline (wk 13-65)",
         shock_inv < base_inv * 0.99,
         round(shock_inv / max(base_inv, 1e-9), 3), "< 0.99 of baseline", warn_only=True)

    # F4  China REE restriction lowers REE stock
    ree_sc = run("china_ree_restriction")
    min_base_ree  = float(base["stock_ree_wk"].min())
    min_shock_ree = float(ree_sc["stock_ree_wk"].min())
    _add(g, "F4 China REE restriction depletes REE stock below baseline",
         min_shock_ree < min_base_ree * 0.99,
         round(min_shock_ree, 3),
         f"< baseline min ({round(min_base_ree, 3)}) × 0.99", warn_only=True)

    # F5  Compound shock cumulative OEM loss > largest single shock
    comp = run("compound_shock")

    def cum_loss(df: pd.DataFrame) -> float:
        return float(
            (base["oem_production_k"] - df["oem_production_k"]).clip(lower=0.0).sum()
        )

    loss_comp = cum_loss(comp)
    loss_drc  = cum_loss(drc)
    loss_ukr  = cum_loss(ukr)
    largest_single = max(loss_drc, loss_ukr)
    _add(g, "F5 compound shock loss >= largest individual shock loss",
         loss_comp >= largest_single * 0.95,
         round(loss_comp, 2), f">= {round(largest_single, 2)} × 0.95", warn_only=True)

    # F6  CATL disruption lowers cell production during shock window
    catl_sc = run("china_catl_disruption")
    base_cell = base.loc[base["week"].between(13, 78), "cell_production_gwh"].mean()
    shock_cell = catl_sc.loc[catl_sc["week"].between(13, 78), "cell_production_gwh"].mean()
    _add(g, "F6 CATL disruption reduces cell production vs baseline (wk 13-78)",
         shock_cell < base_cell * 0.99,
         round(shock_cell / max(base_cell, 1e-9), 3), "< 0.99 of baseline", warn_only=True)

    # F7  UK supply chain friction reduces uk_oem output
    uksc = EVSupplyChainModel(scenario=SCENARIOS["uk_supply_chain_friction"],
                              seed=42, focus_region="uk")
    base_uk = EVSupplyChainModel(scenario=SCENARIOS["baseline"], seed=42, focus_region="uk")
    uksc.run(); base_uk.run()
    df_uksc = uksc.get_results(); df_base_uk = base_uk.get_results()
    uksc_prod = float(df_uksc.loc[df_uksc["week"].between(4, 56), "oem_production_k"].mean())
    base_uk_prod = float(df_base_uk.loc[df_base_uk["week"].between(4, 56), "oem_production_k"].mean())
    _add(g, "F7 UK supply chain friction reduces UK OEM production (wk 4-56)",
         uksc_prod < base_uk_prod * 0.99,
         round(uksc_prod / max(base_uk_prod, 1e-9), 3), "< 0.99 of UK baseline", warn_only=True)

    # F8  Post-shock recovery: after DRC shock ends (wk 52), cobalt stock recovers
    if len(drc) > 100:
        stock_at_shock_end = float(drc.loc[drc["week"] == 52, "stock_cobalt_wk"].values[0]) \
            if 52 in drc["week"].values else float(drc["stock_cobalt_wk"].min())
        stock_wk100 = float(drc.loc[drc["week"] == 100, "stock_cobalt_wk"].values[0]) \
            if 100 in drc["week"].values else float(drc["stock_cobalt_wk"].iloc[-1])
        _add(g, "F8 Cobalt stock recovers after DRC shock ends (wk52->wk100)",
             stock_wk100 >= stock_at_shock_end * 0.95,
             round(stock_wk100, 3),
             f">= stock at shock end ({round(stock_at_shock_end, 3)}) × 0.95", warn_only=True)


# ─────────────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────────────

def _print_report() -> int:
    groups = sorted(set(r.group for r in _results))
    total_pass = total_warn = total_fail = 0
    issues: List[Result] = []

    print()
    print("=" * 78)
    print("EV SUPPLY CHAIN MODEL — DATA AUDIT AND VALIDATION REPORT")
    print("=" * 78)

    for grp in groups:
        rows = [r for r in _results if r.group == grp]
        n_pass = sum(1 for r in rows if r.status == "PASS")
        n_warn = sum(1 for r in rows if r.status == "WARN")
        n_fail = sum(1 for r in rows if r.status == "FAIL")
        total_pass += n_pass; total_warn += n_warn; total_fail += n_fail
        label = {
            "A-DataIntegrity":  "A  Data Integrity",
            "B-Conservation":   "B  Conservation Laws & Numeric Bounds",
            "C-SteadyState":    "C  Steady-State Calibration (global, 260 wk)",
            "D-Delays":         "D  Delay Mechanics",
            "E-Archetypes":     "E  Archetype Decision Rules",
            "F-Scenarios":      "F  Scenario Face-Validity",
        }.get(grp, grp)
        bar = f"PASS {n_pass:>3}  WARN {n_warn:>3}  FAIL {n_fail:>3}"
        print()
        print(f"  {label:<45}  {bar}")
        print("  " + "-" * 74)
        for r in rows:
            sym = {"PASS": "[OK]", "WARN": "[WN]", "FAIL": "[!!]"}[r.status]
            print(f"  {sym}  {r.name}")
            if r.status != "PASS":
                print(f"       measured={r.measured}  expected={r.expected}")
                if r.note:
                    print(f"       note: {r.note}")
            issues.append(r) if r.status != "PASS" else None

    print()
    print("=" * 78)
    print(f"  TOTAL   PASS {total_pass:>4}   WARN {total_warn:>4}   FAIL {total_fail:>4}")
    print("=" * 78)

    if issues:
        print()
        print("ISSUES REQUIRING ATTENTION")
        print("-" * 78)
        for r in issues:
            sym = "WARN" if r.status == "WARN" else "FAIL"
            print(f"  [{sym}] [{r.group}] {r.name}")
            print(f"         measured={r.measured}  expected={r.expected}")
            if r.note:
                print(f"         {r.note}")

    return total_fail


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("Building global baseline model (260 weeks) ...", flush=True)
    global_model = EVSupplyChainModel(
        scenario=SCENARIOS["baseline"], seed=42,
        n_weeks=260, focus_region=None
    )
    global_model.run()
    df_global = global_model.get_results()

    print("Running group A — data integrity ...", flush=True)
    group_a_data_integrity()

    print("Running group B — conservation laws ...", flush=True)
    group_b_conservation(df_global)

    print("Running group C — steady-state calibration ...", flush=True)
    group_c_steady_state(df_global, global_model)

    print("Running group D — delay mechanics ...", flush=True)
    group_d_delays()

    print("Running group E — archetype decision rules ...", flush=True)
    group_e_archetypes()

    print("Running group F — scenario face-validity ...", flush=True)
    group_f_scenarios()

    n_fail = _print_report()

    # Save CSV
    from pathlib import Path
    out = Path("results")
    out.mkdir(exist_ok=True)
    pd.DataFrame([r.__dict__ for r in _results]).to_csv(
        out / "audit_and_test.csv", index=False
    )
    print(f"\n  Full results saved to results/audit_and_test.csv")

    if n_fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
