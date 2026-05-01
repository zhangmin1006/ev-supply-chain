"""
EV Supply Chain Intelligence Dashboard — Streamlit app
=======================================================
Run locally:   streamlit run streamlit_app.py
Deploy:        Streamlit Community Cloud → https://share.streamlit.io
               Point to: zhangmin1006/ev-supply-chain, branch master, file streamlit_app.py
"""

import json
import os
import re
import sys
import copy
from io import BytesIO
from urllib.parse import quote
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
from model.financial_profiles import (
    AGENT_FINANCIAL_PEERS,
    AGENT_PEER_TICKERS,
    FOUR_TIER_AGENT_GROUPS,
)

# ── Page config (must be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="UK EV Supply Chain Intelligence",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items={
        "Get help": "https://github.com/zhangmin1006/ev-supply-chain",
        "Report a bug": "https://github.com/zhangmin1006/ev-supply-chain/issues",
        "About": "EV Supply Chain ABM + SD Simulation · Queen's University Belfast",
    },
)

# ── Constants ─────────────────────────────────────────────────────────────────
SC_COLOURS = {
    "baseline":                 "#94a3b8",
    "ukraine_harness":          "#ef4444",
    "drc_cobalt":               "#f59e0b",
    "sic_bottleneck":           "#a855f7",
    "china_ree_restriction":    "#10b981",
    "compound_shock":           "#3b82f6",
    "china_graphite":           "#ec4899",
    "us_china_tariff":          "#f97316",
    "uk_supply_chain_friction": "#06b6d4",
    "china_catl_disruption":    "#dc2626",
}
SC_LABELS = {
    "baseline":                 "Baseline",
    "ukraine_harness":          "Ukraine Harness",
    "drc_cobalt":               "DRC Cobalt",
    "sic_bottleneck":           "SiC Bottleneck",
    "china_ree_restriction":    "China REE Restriction",
    "compound_shock":           "Compound Shock",
    "china_graphite":           "China Graphite",
    "us_china_tariff":          "US-China Tariff",
    "uk_supply_chain_friction": "UK Brexit Friction",
    "china_catl_disruption":    "China CATL Disruption",
}
SC_DESC = {
    "baseline":                 "No shocks — pure 29 %/yr demand-growth trajectory",
    "ukraine_harness":          "Ukraine conflict → Leoni/Fujikura plants disrupted (Feb 2022 event)",
    "drc_cobalt":               "DRC political disruption → 50 % cobalt supply loss for 6 months",
    "sic_bottleneck":           "SiC wafer capacity crunch (Wolfspeed + Coherent constrained, 2022–24)",
    "china_ree_restriction":    "China imposes REE/NdFeB export quotas (analogous to Ga/Ge controls 2023)",
    "compound_shock":           "Compound: DRC cobalt + Ukraine harness simultaneously",
    "china_graphite":           "China graphite export restrictions (Oct 2023 permit regime)",
    "us_china_tariff":          "US 100 % EV tariff + EU CVD duties → China retaliates with graphite/REE controls",
    "uk_supply_chain_friction": "Post-Brexit RoO friction + border delays — UK OEM throughput impact ★ new",
    "china_catl_disruption":    "Major CATL plant disruption (Ningde cluster) — 37 % global cell concentration risk ★ new",
}
SHOCK_SCS = [s for s in SC_COLOURS if s != "baseline"]
CHOOSE_SCENARIO = "Choose scenario"
CUSTOM_SHOCK = "Custom shock"

SHOCK_TYPE_TARGETS = {
    "Critical mineral supply": [
        "lithium_aus", "lithium_chl", "lithium_chn", "lithium_other",
        "cobalt_drc", "cobalt_other",
        "graphite_chn", "graphite_other",
        "ree_chn", "ree_other",
        "sic_wolfspeed", "sic_coherent", "sic_china", "sic_other",
    ],
    "Cell manufacturing": [
        "cell_catl", "cell_byd_cells", "cell_lg_es", "cell_panasonic",
        "cell_samsung_sdi", "cell_sk_on", "cell_calb", "cell_aesc_uk",
        "cell_others_cells",
    ],
    "Tier-1 component supply": [
        "t1_battery_pack", "t1_inverter", "t1_motor", "t1_harness",
    ],
    "OEM assembly": [
        "oem_uk_oem", "oem_byd_oem", "oem_other_chinese_oem", "oem_us_oem",
        "oem_german_oem", "oem_korean_oem", "oem_japanese_oem",
    ],
}

SHOCK_TARGET_LABELS = {
    "lithium_aus": "Lithium - Australia",
    "lithium_chl": "Lithium - Chile",
    "lithium_chn": "Lithium - China",
    "lithium_other": "Lithium - other sources",
    "cobalt_drc": "Cobalt - DRC",
    "cobalt_other": "Cobalt - other sources",
    "graphite_chn": "Graphite - China",
    "graphite_other": "Graphite - other sources",
    "ree_chn": "Rare earths - China",
    "ree_other": "Rare earths - other sources",
    "sic_wolfspeed": "SiC wafers - Wolfspeed",
    "sic_coherent": "SiC wafers - Coherent",
    "sic_china": "SiC wafers - China",
    "sic_other": "SiC wafers - other sources",
    "cell_catl": "CATL cells",
    "cell_byd_cells": "BYD cells",
    "cell_lg_es": "LG Energy Solution cells",
    "cell_panasonic": "Panasonic cells",
    "cell_samsung_sdi": "Samsung SDI cells",
    "cell_sk_on": "SK On cells",
    "cell_calb": "CALB cells",
    "cell_aesc_uk": "AESC UK cells",
    "cell_others_cells": "Other cell makers",
    "t1_battery_pack": "Battery pack supplier",
    "t1_inverter": "Inverter supplier",
    "t1_motor": "Motor supplier",
    "t1_harness": "Wiring harness supplier",
    "oem_uk_oem": "UK OEM assembly",
    "oem_byd_oem": "BYD OEM assembly",
    "oem_other_chinese_oem": "Other Chinese OEM assembly",
    "oem_us_oem": "US OEM assembly",
    "oem_german_oem": "European OEM assembly",
    "oem_korean_oem": "Korean OEM assembly",
    "oem_japanese_oem": "Japanese OEM assembly",
}

OEM_COLOURS = {
    "byd_oem":            "#ef4444",
    "other_chinese_oem":  "#f97316",
    "uk_oem":             "#06b6d4",
    "us_oem":             "#3b82f6",
    "german_oem":         "#a855f7",
    "korean_oem":         "#10b981",
    "japanese_oem":       "#f59e0b",
}
OEM_LABELS = {
    "byd_oem":           "BYD (China)",
    "other_chinese_oem": "Other Chinese OEMs",
    "uk_oem":            "UK OEM (JLR / MINI / Vauxhall)",
    "us_oem":            "US OEM (Tesla / GM)",
    "german_oem":        "European OEM (excl. UK)",
    "korean_oem":        "Korean OEM (Hyundai / Kia)",
    "japanese_oem":      "Japanese OEM (Toyota / Honda)",
}

WEEKS = list(range(260))
YEAR_TICKS = {0: "Yr 1", 52: "Yr 2", 104: "Yr 3", 156: "Yr 4", 208: "Yr 5"}
DATA_SCHEMA_VERSION = "uk-focus-v5"
REQUIRED_DATA_KEYS = {
    "focus_region",
    "policy_packages",
    "oem_production_k",
    "oem_uk_oem_k",
    "t1_battery_pack_k",
    "t1_harness_k",
    "t1_inverter_k",
    "t1_motor_k",
    "market_demand_gwh",
    "cell_production_gwh",
}

PLOT_LAYOUT = dict(
    template="plotly_white",
    paper_bgcolor="#ffffff",
    plot_bgcolor="#ffffff",
    font=dict(color="#334155", size=11),
    margin=dict(l=50, r=20, t=36, b=40),
    legend=dict(bgcolor="rgba(255,255,255,0)", bordercolor="#cbd5e1", borderwidth=1,
                font=dict(size=10)),
    xaxis=dict(gridcolor="#e2e8f0", tickfont=dict(size=9),
               tickvals=list(YEAR_TICKS.keys()),
               ticktext=list(YEAR_TICKS.values())),
    yaxis=dict(gridcolor="#e2e8f0", tickfont=dict(size=9)),
)


# ── Data loading ──────────────────────────────────────────────────────────────

@st.cache_data(show_spinner="Running 260-week simulation for all scenarios…")
def load_or_run():
    """Load pre-computed JSON; run simulation if not available."""
    json_path = os.path.join(os.path.dirname(__file__), "results", "simulation_data.json")
    if os.path.exists(json_path):
        with open(json_path) as f:
            cached = json.load(f)
        baseline = cached.get("baseline", {})
        has_required_schema = (
            baseline.get("app_data_schema", [""])[0] == DATA_SCHEMA_VERSION
            and baseline.get("focus_region", [""])[0] == "uk"
            and REQUIRED_DATA_KEYS.issubset(baseline.keys())
        )
        if has_required_schema:
            return cached

    # Fallback: run simulation
    sys.path.insert(0, os.path.dirname(__file__))
    from model.hybrid_model import EVSupplyChainModel
    from model.shocks import SCENARIOS
    import json as _json

    class _Enc(_json.JSONEncoder):
        def default(self, o):
            if isinstance(o, (np.integer,)):  return int(o)
            if isinstance(o, (np.floating,)): return round(float(o), 4)
            if isinstance(o, np.ndarray):     return o.tolist()
            return super().default(o)

    results = {}
    bar = st.progress(0, text="Running simulation…")
    for i, (name, sc) in enumerate(SCENARIOS.items()):
        bar.progress((i + 1) / len(SCENARIOS), text=f"Scenario: {name}")
        m = EVSupplyChainModel(scenario=sc, seed=42, n_weeks=260)
        m.run()
        scenario_data = m.get_results().to_dict(orient="list")
        scenario_data["app_data_schema"] = [DATA_SCHEMA_VERSION] * len(scenario_data["week"])
        results[name] = scenario_data
    bar.empty()

    os.makedirs(os.path.join(os.path.dirname(__file__), "results"), exist_ok=True)
    with open(json_path, "w") as f:
        _json.dump(results, f, cls=_Enc)
    return results


@st.cache_data
def compute_summary(data):
    bl = np.array(data["baseline"]["oem_production_k"])
    rows = []
    for name in SHOCK_SCS:
        d = data.get(name)
        if not d:
            continue
        prod = np.array(d["oem_production_k"])
        base = bl[:len(prod)]
        rel = prod / np.maximum(base, 1e-9)
        losses = np.maximum(0.0, 1.0 - rel)
        peak_loss  = float(losses.max() * 100)
        mean_loss  = float(losses.mean() * 100)
        below_90   = int((rel < 0.9).sum())
        sub = np.where(rel < 0.9)[0]
        rec_week   = int(sub[-1]) + 1 if len(sub) else 0
        cum_loss   = float(np.maximum(0, base - prod).sum())
        rows.append(dict(
            Scenario=SC_LABELS[name],
            Avg_prod=round(float(prod.mean()), 1),
            Peak_loss=round(peak_loss, 1),
            Mean_loss=round(mean_loss, 1),
            Below_90_wks=below_90,
            Recovery_wk=rec_week,
            Cum_loss_k=round(cum_loss, 0),
            _color=SC_COLOURS[name],
        ))
    return pd.DataFrame(rows).sort_values("Peak_loss", ascending=False)


def load_validation_results():
    """Read generated validation artifacts for display in the app."""
    results_dir = os.path.join(os.path.dirname(__file__), "results")
    checks_path = os.path.join(results_dir, "validation_checks.csv")
    metrics_path = os.path.join(results_dir, "validation_scenario_metrics.csv")
    real_ts_path = os.path.join(results_dir, "real_timeseries_validation.csv")
    real_align_path = os.path.join(results_dir, "real_timeseries_validation_alignment.csv")
    report_path = os.path.join(results_dir, "validation_report.md")

    checks = pd.DataFrame()
    metrics = pd.DataFrame()
    real_ts = pd.DataFrame()
    real_align = pd.DataFrame()
    report = ""
    modified = None

    if os.path.exists(checks_path):
        checks = pd.read_csv(checks_path)
        modified = os.path.getmtime(checks_path)
    if os.path.exists(metrics_path):
        metrics = pd.read_csv(metrics_path)
        modified = max(modified or 0, os.path.getmtime(metrics_path))
    if os.path.exists(real_ts_path):
        real_ts = pd.read_csv(real_ts_path)
        modified = max(modified or 0, os.path.getmtime(real_ts_path))
    if os.path.exists(real_align_path):
        real_align = pd.read_csv(real_align_path, parse_dates=["month"])
        modified = max(modified or 0, os.path.getmtime(real_align_path))
    if os.path.exists(report_path):
        with open(report_path, encoding="utf-8") as f:
            report = f.read()
        modified = max(modified or 0, os.path.getmtime(report_path))

    return checks, metrics, real_ts, real_align, report, modified


def load_policy_evaluation():
    path = os.path.join(
        os.path.dirname(__file__),
        "results",
        "policy_intervention_evaluation.csv",
    )
    if not os.path.exists(path):
        return pd.DataFrame(), None
    return pd.read_csv(path), os.path.getmtime(path)


def _annual_growth_to_weekly(rate: float) -> float:
    return (1.0 + rate) ** (1.0 / 52.0) - 1.0


def _build_custom_shock_scenario(params: dict) -> dict:
    start_week = int(params["custom_shock_start_week"])
    end_week = min(int(params["weeks"]), start_week + int(params["custom_shock_duration_weeks"]))
    target = params["custom_shock_target"]
    label = SHOCK_TARGET_LABELS.get(target, target)
    severity = float(params["custom_shock_severity"])
    return {
        "name": "custom_shock",
        "description": f"Custom {params['custom_shock_type'].lower()} shock to {label}",
        "shocks": [
            {
                "target": target,
                "start_week": start_week,
                "end_week": max(start_week + 1, end_week),
                "severity": severity,
            }
        ],
    }


POLICY_SUFFIX_LABELS = {
    "_tier1_policy": "Tier-1 resilience package",
    "_minerals_policy": "Critical minerals security package",
    "_full_policy": "Full industrial strategy package",
}


def _base_scenario_id(scenario_id: str) -> str:
    for suffix in POLICY_SUFFIX_LABELS:
        if scenario_id.endswith(suffix):
            return scenario_id[: -len(suffix)]
    return scenario_id


def _scenario_display_label(scenario_id: str) -> str:
    base_id = _base_scenario_id(scenario_id)
    label = SC_LABELS.get(base_id, scenario_id.replace("_", " ").title())
    for suffix, policy_label in POLICY_SUFFIX_LABELS.items():
        if scenario_id.endswith(suffix):
            return f"{label} + {policy_label}"
    return label


def _validated_base_scenarios(val_metrics: pd.DataFrame) -> list[str]:
    if val_metrics.empty or "scenario" not in val_metrics.columns:
        return []
    scenario_ids = []
    for scenario_id in val_metrics["scenario"].dropna().astype(str):
        base_id = _base_scenario_id(scenario_id)
        if base_id in SC_DESC and base_id not in scenario_ids:
            scenario_ids.append(base_id)
    return scenario_ids


@st.cache_data(show_spinner="Running custom parameter experiment...")
def run_custom_parameter_experiment(params: dict):
    """Run one scenario with user-adjusted ABM and SD parameters."""
    sys.path.insert(0, os.path.dirname(__file__))
    import model.config as cfg
    import model.sd_model as sd
    import model.agents as agents
    import model.hybrid_model as hm
    from model.shocks import SCENARIOS

    orig_cfg = {
        "MARKETS": copy.deepcopy(cfg.MARKETS),
        "OEMS": copy.deepcopy(cfg.OEMS),
        "TIER1": copy.deepcopy(cfg.TIER1),
        "BULLWHIP_FACTOR": cfg.BULLWHIP_FACTOR,
    }
    orig_sd = {
        "MEAS_LAG_WK": sd.MEAS_LAG_WK,
        "PRICE_ADJ_SPEED": sd.PRICE_ADJ_SPEED,
        "PRICE_ALPHA": sd.PRICE_ALPHA,
        "CHEM_SHIFT_SPEED": sd.CHEM_SHIFT_SPEED,
        "CAPEX_TRIGGER_UTIL": sd.CAPEX_TRIGGER_UTIL,
        "MINERAL_TRANSPORT_WK": copy.deepcopy(sd.MINERAL_TRANSPORT_WK),
        "MINERAL_SUPPLY_VOL": copy.deepcopy(sd.MINERAL_SUPPLY_VOL),
        "MINERAL_SUPPLY_GROWTH_WK": copy.deepcopy(sd.MINERAL_SUPPLY_GROWTH_WK),
    }
    orig_agents = {
        "_CELL_GROWTH_WK": agents._CELL_GROWTH_WK,
        "_TIER1_GROWTH_WK": agents._TIER1_GROWTH_WK,
    }
    orig_hm = {"BULLWHIP_FACTOR": hm.BULLWHIP_FACTOR}

    try:
        cfg.MARKETS["uk"]["gwh_2023"] = float(params["uk_market_gwh"])
        cfg.MARKETS["uk"]["yoy"] = float(params["uk_demand_growth"])
        cfg.MARKETS["uk"]["price_elasticity"] = float(params["uk_price_elasticity"])
        cfg.MARKETS["uk"]["backlog_sensitivity"] = float(params["uk_backlog_sensitivity"])
        cfg.MARKETS["uk"]["availability_floor"] = float(params["uk_availability_floor"])

        cfg.OEMS["uk_oem"]["annual_target_k"] = float(params["uk_oem_target_k"])
        cfg.OEMS["uk_oem"]["safety_stock_weeks"] = float(params["uk_oem_safety_weeks"])
        cfg.OEMS["uk_oem"]["vertical_integration"] = float(params["uk_vertical_integration"])

        cfg.TIER1["harness"]["safety_stock_weeks"] = float(params["harness_safety_weeks"])
        cfg.TIER1["harness"]["lead_time_weeks"] = int(params["harness_lead_time"])
        cfg.TIER1["inverter"]["sic_dependency"] = float(params["sic_dependency"])
        cfg.TIER1["motor"]["pmsm_fraction"] = float(params["ree_motor_dependency"])

        cfg.BULLWHIP_FACTOR = float(params["bullwhip_factor"])
        hm.BULLWHIP_FACTOR = float(params["bullwhip_factor"])

        cell_growth_wk = _annual_growth_to_weekly(float(params["cell_capacity_growth"]))
        tier1_growth_wk = _annual_growth_to_weekly(float(params["tier1_capacity_growth"]))
        agents._CELL_GROWTH_WK = cell_growth_wk
        agents._TIER1_GROWTH_WK = tier1_growth_wk

        sd.MEAS_LAG_WK = max(1.0, float(params["measurement_lag_weeks"]))
        sd.PRICE_ADJ_SPEED = float(params["price_adjustment_speed"])
        sd.PRICE_ALPHA = float(params["price_scarcity_sensitivity"])
        sd.CHEM_SHIFT_SPEED = float(params["lfp_shift_speed"])
        sd.CAPEX_TRIGGER_UTIL = float(params["capex_trigger_util"])

        sd.MINERAL_TRANSPORT_WK["cobalt"] = int(params["cobalt_transport_weeks"])
        sd.MINERAL_TRANSPORT_WK["graphite"] = int(params["graphite_transport_weeks"])
        sd.MINERAL_TRANSPORT_WK["ree"] = int(params["ree_transport_weeks"])
        sd.MINERAL_TRANSPORT_WK["sic_wafer"] = int(params["sic_transport_weeks"])

        sd.MINERAL_SUPPLY_VOL["cobalt"] = float(params["cobalt_supply_vol"])
        sd.MINERAL_SUPPLY_VOL["graphite"] = float(params["graphite_supply_vol"])
        sd.MINERAL_SUPPLY_VOL["ree"] = float(params["ree_supply_vol"])
        sd.MINERAL_SUPPLY_VOL["sic_wafer"] = float(params["sic_supply_vol"])

        sd.MINERAL_SUPPLY_GROWTH_WK["cobalt"] = _annual_growth_to_weekly(float(params["cobalt_supply_growth"]))
        sd.MINERAL_SUPPLY_GROWTH_WK["graphite"] = _annual_growth_to_weekly(float(params["graphite_supply_growth"]))
        sd.MINERAL_SUPPLY_GROWTH_WK["ree"] = _annual_growth_to_weekly(float(params["ree_supply_growth"]))
        sd.MINERAL_SUPPLY_GROWTH_WK["sic_wafer"] = _annual_growth_to_weekly(float(params["sic_supply_growth"]))

        if params.get("shock_setup") == CUSTOM_SHOCK:
            scenario = _build_custom_shock_scenario(params)
            scenario_label = SHOCK_TARGET_LABELS.get(
                params["custom_shock_target"], params["custom_shock_target"]
            )
        else:
            scenario = SCENARIOS[params["scenario"]]
            scenario_label = SC_LABELS[params["scenario"]]
        weeks = int(params["weeks"])
        seed = int(params["seed"])

        baseline = hm.EVSupplyChainModel(scenario=SCENARIOS["baseline"], seed=seed, n_weeks=weeks)
        baseline.run(weeks)
        custom = hm.EVSupplyChainModel(scenario=scenario, seed=seed, n_weeks=weeks)
        custom.run(weeks)

        return {
            "baseline": baseline.get_results().to_dict(orient="list"),
            "custom": custom.get_results().to_dict(orient="list"),
            "calibration": custom.get_data_source_calibration_summary().to_dict(orient="records"),
            "scenario": scenario,
            "scenario_label": scenario_label,
        }
    finally:
        cfg.MARKETS.clear(); cfg.MARKETS.update(orig_cfg["MARKETS"])
        cfg.OEMS.clear(); cfg.OEMS.update(orig_cfg["OEMS"])
        cfg.TIER1.clear(); cfg.TIER1.update(orig_cfg["TIER1"])
        cfg.BULLWHIP_FACTOR = orig_cfg["BULLWHIP_FACTOR"]

        sd.MEAS_LAG_WK = orig_sd["MEAS_LAG_WK"]
        sd.PRICE_ADJ_SPEED = orig_sd["PRICE_ADJ_SPEED"]
        sd.PRICE_ALPHA = orig_sd["PRICE_ALPHA"]
        sd.CHEM_SHIFT_SPEED = orig_sd["CHEM_SHIFT_SPEED"]
        sd.CAPEX_TRIGGER_UTIL = orig_sd["CAPEX_TRIGGER_UTIL"]
        sd.MINERAL_TRANSPORT_WK.clear(); sd.MINERAL_TRANSPORT_WK.update(orig_sd["MINERAL_TRANSPORT_WK"])
        sd.MINERAL_SUPPLY_VOL.clear(); sd.MINERAL_SUPPLY_VOL.update(orig_sd["MINERAL_SUPPLY_VOL"])
        sd.MINERAL_SUPPLY_GROWTH_WK.clear(); sd.MINERAL_SUPPLY_GROWTH_WK.update(orig_sd["MINERAL_SUPPLY_GROWTH_WK"])

        agents._CELL_GROWTH_WK = orig_agents["_CELL_GROWTH_WK"]
        agents._TIER1_GROWTH_WK = orig_agents["_TIER1_GROWTH_WK"]
        hm.BULLWHIP_FACTOR = orig_hm["BULLWHIP_FACTOR"]


# ── Chart helpers ─────────────────────────────────────────────────────────────

def line(fig, x, y, name, color, dash="solid", width=1.8, fill=False, row=None, col=None, showlegend=True):
    trace = go.Scatter(
        x=x, y=y, name=name, line=dict(color=color, width=width, dash=dash),
        fill="tozeroy" if fill else "none",
        fillcolor=color.replace(")", ",0.12)").replace("rgb", "rgba") if fill else None,
        showlegend=showlegend, mode="lines",
    )
    if row is not None and col is not None:
        fig.add_trace(trace, row=row, col=col)
    else:
        fig.add_trace(trace)


def std_layout(fig, title="", height=350, show_legend=True):
    fig.update_layout(**PLOT_LAYOUT, title=dict(text=title, font=dict(size=12, color="#0f172a")),
                      height=height, showlegend=show_legend)
    fig.update_xaxes(gridcolor="#e2e8f0", tickfont=dict(size=9, color="#334155"),
                     tickvals=list(YEAR_TICKS.keys()), ticktext=list(YEAR_TICKS.values()))
    fig.update_yaxes(gridcolor="#e2e8f0", tickfont=dict(size=9, color="#334155"))
    return fig


def hex_alpha(hex_col, a):
    h = hex_col.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{a})"


# ── CSS injection ─────────────────────────────────────────────────────────────
st.markdown("""
<style>
  /* hide Streamlit header chrome on cloud */
  #MainMenu {visibility: hidden;}
  .stApp {background: #f8fafc; color: #0f172a;}
  header[data-testid="stHeader"] {background: #f8fafc; border-bottom: 1px solid #e2e8f0;}
  footer {visibility: hidden;}
  h1, h2, h3, h4, h5, h6, p, label, span, div {color: inherit;}
  div[data-testid="stTabs"] button {font-size: 0.82rem; font-weight: 600;}
  /* metric cards */
  div[data-testid="metric-container"] {
    background: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 12px 16px;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
  }
  div[data-testid="stMetricValue"] {font-size: 1.7rem !important; font-weight: 700; color: #0f172a;}
  div[data-testid="stMetricLabel"], div[data-testid="stMetricDelta"] {color: #475569;}
  /* dataframe */
  .stDataFrame {border-radius: 8px; overflow: hidden;}
  .scenario-card {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-left-width: 5px;
    border-radius: 8px;
    padding: 10px 12px;
    min-height: 104px;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
  }
  .scenario-card strong {
    display: block;
    color: #0f172a;
    font-size: 0.92rem;
    margin-bottom: 4px;
  }
  .scenario-card p {
    color: #475569;
    font-size: 0.82rem;
    line-height: 1.35;
    margin: 0;
  }
</style>
""", unsafe_allow_html=True)


# ── Load data ─────────────────────────────────────────────────────────────────
DATA = load_or_run()
BL   = DATA["baseline"]
SUMMARY = compute_summary(DATA)


# ── Header ────────────────────────────────────────────────────────────────────
st.markdown(
    "<h1 style='color:#0f172a;font-size:1.5rem;margin-bottom:2px'>"
    "⚡ UK EV Supply Chain <span style='color:#3b82f6'>Intelligence Dashboard</span></h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<p style='color:#475569;font-size:0.82rem;margin-bottom:16px'>"
    "Hybrid Agent-Based + System Dynamics model &nbsp;·&nbsp; UK OEM focus &nbsp;·&nbsp; "
    "13 agent archetypes &nbsp;·&nbsp; 9 cell makers &nbsp;·&nbsp; 7 tracked materials &nbsp;·&nbsp; "
    "10 shock scenarios &nbsp;·&nbsp; 260-week horizon &nbsp;|&nbsp; Queen's University Belfast</p>",
    unsafe_allow_html=True,
)

# ── Tabs ──────────────────────────────────────────────────────────────────────
T_OVERVIEW, T_SCENARIO, T_PARAMETERS, T_POLICY, T_VALIDATION, T_OEM, T_STOCKS, T_ARCHETYPES, T_MAP, T_MARKET, T_FOCUS = st.tabs([
    "📊 Overview",
    "🔍 Scenario Analysis",
    "Parameter Lab",
    "Policy Evaluation",
    "Validation",
    "🏭 UK OEM",
    "⛏️ Supply Chain Stocks",
    "🤖 Agent Archetypes",
    "Supply Chain Map",
    "📡 Live Data",
    "🇬🇧 UK Stress Focus",
])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════════
with T_OVERVIEW:
    # KPI row
    worst   = SUMMARY.iloc[0]
    highest_cum = SUMMARY.sort_values("Cum_loss_k", ascending=False).iloc[0]
    longest_rec = SUMMARY.sort_values("Recovery_wk", ascending=False).iloc[0]
    bl_peak = max(BL["oem_production_k"])

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    with c1:
        st.metric("Worst Peak Loss", f"{worst.Peak_loss:.1f}%",
                  delta=worst.Scenario, delta_color="inverse")
    with c2:
        st.metric("Highest Cumulative Loss", f"{highest_cum.Cum_loss_k/1000:.1f} M veh",
                  delta=highest_cum.Scenario, delta_color="inverse")
    with c3:
        st.metric("Longest Recovery", f"Week {int(longest_rec.Recovery_wk)}",
                  delta=longest_rec.Scenario, delta_color="inverse")
    with c4:
        st.metric("Baseline Peak Output", f"{bl_peak:.0f} k/wk",
                  delta="Year 5 (demand growth)")
    with c5:
        st.metric("Agent Archetypes", "13",
                  delta="4 tiers · qualitative rules")
    with c6:
        st.metric("UK OEM Volume", "175 k/yr",
                  delta="JLR · MINI · Vauxhall")

    st.divider()

    # Scenario selector
    selected = st.multiselect(
        "Choose scenarios to compare:",
        options=SHOCK_SCS,
        default=[],
        format_func=lambda s: SC_LABELS[s],
        key="ov_select",
    )
    if not selected:
        st.info("Choose one or more scenarios to compare with the baseline.")

    # Production chart
    fig_ov = go.Figure()
    line(fig_ov, WEEKS, BL["oem_production_k"], "Baseline", "#94a3b8", dash="dot", width=2)
    for sc in selected:
        line(fig_ov, WEEKS, DATA[sc]["oem_production_k"],
             SC_LABELS[sc], SC_COLOURS[sc])
    std_layout(fig_ov, "UK OEM Vehicle Production (k vehicles / week)", height=360)
    fig_ov.update_yaxes(title_text="k vehicles / week", title_font=dict(size=10))
    st.plotly_chart(fig_ov, width="stretch")

    # Summary table
    st.subheader("Scenario Impact Summary")
    disp = SUMMARY[SUMMARY["Scenario"].isin([SC_LABELS[s] for s in selected] + ["Baseline"])].copy()
    disp.columns = ["Scenario", "Avg prod (k/wk)", "Peak loss (%)", "Mean loss (%)",
                    "Weeks below 90%", "Recovery week", "Cumulative loss (k veh)", "_color"]
    st.dataframe(
        disp.drop(columns=["_color"]).set_index("Scenario"),
        width="stretch",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — SCENARIO ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════
with T_SCENARIO:
    sc_sel = st.selectbox(
        "Choose scenario for deep-dive analysis:",
        options=[CHOOSE_SCENARIO] + SHOCK_SCS,
        index=0,
        format_func=lambda s: s if s == CHOOSE_SCENARIO else f"{SC_LABELS[s]} - {SC_DESC[s]}",
        key="sc_sel",
    )
    if sc_sel == CHOOSE_SCENARIO:
        st.info("Choose a scenario to show the deep-dive charts.")
        sc_sel = SHOCK_SCS[0]
    col = SC_COLOURS[sc_sel]
    d = DATA[sc_sel]
    bl_prod = np.array(BL["oem_production_k"])
    bl_mean = bl_prod.mean()

    st.markdown(
        f"<div style='background:#ffffff;border:1px solid {col};border-radius:8px;"
        f"padding:10px 16px;margin-bottom:16px'>"
        f"<span style='color:{col};font-weight:700'>{SC_LABELS[sc_sel]}</span>"
        f"<span style='color:#475569;font-size:0.82rem'> — {SC_DESC[sc_sel]}</span></div>",
        unsafe_allow_html=True,
    )

    # Row 1: Production + Relative
    r1c1, r1c2 = st.columns(2)
    with r1c1:
        fig = go.Figure()
        line(fig, WEEKS, bl_prod, "Baseline", "#94a3b8", dash="dot", width=1.5)
        line(fig, WEEKS, d["oem_production_k"], SC_LABELS[sc_sel], col, width=2)
        std_layout(fig, "OEM Vehicle Production (k/week)", 280)
        st.plotly_chart(fig, width="stretch")

    with r1c2:
        rel = [(p / max(bl_prod[i], 1e-6) - 1) * 100
               for i, p in enumerate(d["oem_production_k"])]
        fig = go.Figure()
        fig.add_hline(y=0,   line_dash="dot", line_color="#94a3b8", line_width=1)
        fig.add_hline(y=-10, line_dash="dot", line_color="#f59e0b", line_width=0.8)
        line(fig, WEEKS, rel, "% vs baseline", col, fill=True)
        std_layout(fig, "Production Deviation vs Baseline (%)", 280)
        fig.update_yaxes(title_text="%")
        st.plotly_chart(fig, width="stretch")

    # Row 2: Cells + Cobalt/Graphite
    r2c1, r2c2 = st.columns(2)
    with r2c1:
        fig = go.Figure()
        line(fig, WEEKS, BL["cell_production_gwh"], "Baseline", "#94a3b8", dash="dot", width=1.5)
        line(fig, WEEKS, d["cell_production_gwh"], SC_LABELS[sc_sel], col, width=2)
        std_layout(fig, "Cell Production (GWh / week)", 280)
        fig.update_yaxes(title_text="GWh/week")
        st.plotly_chart(fig, width="stretch")

    with r2c2:
        fig = go.Figure()
        line(fig, WEEKS, BL["stock_cobalt_wk"],   "Cobalt baseline", "#94a3b8", dash="dot", width=1)
        line(fig, WEEKS, d["stock_cobalt_wk"],    "Cobalt", col, width=2)
        line(fig, WEEKS, d["stock_graphite_wk"],  "Graphite", hex_alpha(col, 0.6), dash="dash")
        std_layout(fig, "Cobalt & Graphite Stock (weeks of supply)", 280)
        fig.update_yaxes(title_text="weeks of supply")
        st.plotly_chart(fig, width="stretch")

    # Row 3: Harness/SiC + REE/Price
    r3c1, r3c2 = st.columns(2)
    with r3c1:
        fig = go.Figure()
        line(fig, WEEKS, BL["stock_harness_wk"],   "Harness baseline", "#94a3b8", dash="dot", width=1)
        line(fig, WEEKS, d["stock_harness_wk"],    "Harness (JIT)", col, width=2)
        line(fig, WEEKS, d["stock_sic_wafer_wk"],  "SiC wafer", hex_alpha(col, 0.6), dash="dash")
        std_layout(fig, "Harness & SiC Wafer Stock (weeks of supply)", 280)
        fig.update_yaxes(title_text="weeks of supply")
        st.plotly_chart(fig, width="stretch")

    with r3c2:
        fig = go.Figure()
        line(fig, WEEKS, BL["stock_ree_wk"],  "REE baseline", "#94a3b8", dash="dot", width=1)
        line(fig, WEEKS, d["stock_ree_wk"],   "REE stock", col, width=2)
        line(fig, WEEKS, d["price_signal"],   "Price index", hex_alpha(col, 0.6), dash="dash")
        std_layout(fig, "REE Stock (wks) & Price Pressure Index", 280)
        fig.update_yaxes(title_text="weeks / index")
        st.plotly_chart(fig, width="stretch")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — PARAMETER LAB
# ═══════════════════════════════════════════════════════════════════════════════
with T_PARAMETERS:
    st.subheader("ABM + SD Parameter Lab")
    st.caption(
        "Adjust estimated assumptions and run a fresh UK-focused simulation. "
        "The main dashboard tabs continue to use the validated baseline cache."
    )

    with st.form("parameter_lab_form"):
        p_scenario = CHOOSE_SCENARIO
        p_custom_type = next(iter(SHOCK_TYPE_TARGETS))
        p_custom_target = SHOCK_TYPE_TARGETS[p_custom_type][0]
        p_custom_start = 4
        p_custom_duration = 26
        p_custom_severity = 0.50

        c_top1, c_top2, c_top3, c_top4 = st.columns(4)
        with c_top1:
            p_shock_setup = st.radio(
                "Shock setup",
                options=["Predefined scenario", CUSTOM_SHOCK],
                horizontal=True,
            )
        with c_top2:
            if p_shock_setup == CUSTOM_SHOCK:
                p_custom_type = st.selectbox("Shock type", options=list(SHOCK_TYPE_TARGETS))
            else:
                p_scenario = st.selectbox(
                    "Scenario",
                    options=[CHOOSE_SCENARIO] + SHOCK_SCS,
                    index=0,
                    format_func=lambda s: s if s == CHOOSE_SCENARIO else SC_LABELS[s],
                )
        with c_top3:
            if p_shock_setup == CUSTOM_SHOCK:
                p_custom_target = st.selectbox(
                    "Shock target",
                    options=SHOCK_TYPE_TARGETS[p_custom_type],
                    format_func=lambda target: SHOCK_TARGET_LABELS.get(target, target),
                )
            else:
                p_weeks = st.slider("Horizon (weeks)", 52, 260, 156, 26)
        with c_top4:
            if p_shock_setup == CUSTOM_SHOCK:
                p_weeks = st.slider("Horizon (weeks)", 52, 260, 156, 26)
            p_seed = st.number_input("Random seed", min_value=1, max_value=9999, value=42, step=1)

        if p_shock_setup == CUSTOM_SHOCK:
            st.markdown("**Custom shock timing and severity**")
            s1, s2, s3 = st.columns(3)
            with s1:
                p_custom_start = st.slider("Shock start week", 0, max(0, int(p_weeks) - 1), 4, 1)
            with s2:
                max_duration = max(1, int(p_weeks) - int(p_custom_start))
                p_custom_duration = st.slider("Shock duration (weeks)", 1, max_duration, min(26, max_duration), 1)
            with s3:
                p_custom_severity = st.slider("Output loss severity", 0.05, 1.00, 0.50, 0.05)
        else:
            st.caption("Use custom shock setup to choose a supply-chain layer, target, start week, duration, and severity.")

        st.markdown("**Demand and UK OEM assumptions**")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            p_uk_market = st.number_input("UK market demand 2023 (GWh)", 8.0, 60.0, 20.0, 1.0)
            p_uk_growth = st.slider("UK demand growth (%/yr)", 0.0, 60.0, 28.0, 1.0) / 100.0
        with c2:
            p_uk_price_elasticity = st.slider("UK price elasticity", -0.80, -0.05, -0.36, 0.01)
            p_uk_backlog_sens = st.slider("Backlog demand sensitivity", 0.00, 1.00, 0.60, 0.05)
        with c3:
            p_uk_availability_floor = st.slider("Availability floor", 0.10, 0.90, 0.46, 0.02)
            p_uk_oem_target = st.number_input("UK OEM target (k vehicles/yr)", 50.0, 500.0, 175.0, 5.0)
        with c4:
            p_uk_oem_safety = st.slider("UK OEM safety stock (weeks)", 1.0, 12.0, 5.0, 0.5)
            p_uk_vertical = st.slider("UK vertical integration", 0.00, 1.00, 0.05, 0.05)

        st.markdown("**ABM supplier behavior**")
        c5, c6, c7, c8 = st.columns(4)
        with c5:
            p_harness_safety = st.slider("Harness safety stock (weeks)", 0.5, 8.0, 2.0, 0.5)
            p_harness_lead = st.slider("Harness lead time (weeks)", 1, 16, 6, 1)
        with c6:
            p_bullwhip = st.slider("Bullwhip factor", 0.50, 2.50, 1.25, 0.05)
            p_tier1_growth = st.slider("Tier-1 capacity growth (%/yr)", 0.0, 60.0, 29.0, 1.0) / 100.0
        with c7:
            p_sic_dependency = st.slider("Inverter SiC dependency", 0.00, 1.00, 0.45, 0.05)
            p_ree_dependency = st.slider("Motor REE dependency", 0.00, 1.00, 0.82, 0.05)
        with c8:
            p_cell_growth = st.slider("Cell capacity growth (%/yr)", 0.0, 80.0, 29.0, 1.0) / 100.0

        st.markdown("**SD feedback and uncertainty assumptions**")
        c9, c10, c11, c12 = st.columns(4)
        with c9:
            p_meas_lag = st.slider("Inventory measurement lag (weeks)", 1.0, 8.0, 2.0, 0.5)
            p_price_speed = st.slider("Price adjustment speed", 0.01, 0.20, 0.05, 0.01)
        with c10:
            p_price_alpha = st.slider("Price scarcity sensitivity", 0.50, 4.00, 1.50, 0.10)
            p_lfp_speed = st.slider("LFP shift speed (% gap/week)", 0.05, 1.00, 0.30, 0.05) / 100.0
        with c11:
            p_capex_trigger = st.slider("Cell capex trigger utilisation", 0.50, 0.98, 0.85, 0.01)
            p_cobalt_transport = st.slider("Cobalt transport delay (weeks)", 1, 20, 8, 1)
        with c12:
            p_graphite_transport = st.slider("Graphite transport delay (weeks)", 1, 16, 4, 1)
            p_ree_transport = st.slider("REE transport delay (weeks)", 1, 24, 10, 1)

        c13, c14, c15, c16 = st.columns(4)
        with c13:
            p_sic_transport = st.slider("SiC transport delay (weeks)", 1, 16, 4, 1)
            p_cobalt_vol = st.slider("Cobalt supply volatility", 0.00, 0.20, 0.07, 0.01)
        with c14:
            p_graphite_vol = st.slider("Graphite supply volatility", 0.00, 0.20, 0.03, 0.01)
            p_ree_vol = st.slider("REE supply volatility", 0.00, 0.20, 0.05, 0.01)
        with c15:
            p_sic_vol = st.slider("SiC supply volatility", 0.00, 0.20, 0.03, 0.01)
            p_cobalt_growth = st.slider("Cobalt supply growth (%/yr)", 0.0, 50.0, 5.0, 1.0) / 100.0
        with c16:
            p_graphite_growth = st.slider("Graphite supply growth (%/yr)", 0.0, 60.0, 15.0, 1.0) / 100.0
            p_ree_growth = st.slider("REE supply growth (%/yr)", 0.0, 80.0, 30.0, 1.0) / 100.0

        p_sic_growth = st.slider("SiC wafer supply growth (%/yr)", 0.0, 100.0, 35.0, 1.0) / 100.0
        run_lab = st.form_submit_button("Run parameter experiment", type="primary")

    if run_lab and p_shock_setup != CUSTOM_SHOCK and p_scenario == CHOOSE_SCENARIO:
        st.warning("Choose a scenario before running the parameter experiment.")
    elif run_lab:
        params = {
            "shock_setup": p_shock_setup,
            "scenario": p_scenario,
            "weeks": int(p_weeks),
            "seed": int(p_seed),
            "custom_shock_type": p_custom_type,
            "custom_shock_target": p_custom_target,
            "custom_shock_start_week": p_custom_start,
            "custom_shock_duration_weeks": p_custom_duration,
            "custom_shock_severity": p_custom_severity,
            "uk_market_gwh": p_uk_market,
            "uk_demand_growth": p_uk_growth,
            "uk_price_elasticity": p_uk_price_elasticity,
            "uk_backlog_sensitivity": p_uk_backlog_sens,
            "uk_availability_floor": p_uk_availability_floor,
            "uk_oem_target_k": p_uk_oem_target,
            "uk_oem_safety_weeks": p_uk_oem_safety,
            "uk_vertical_integration": p_uk_vertical,
            "harness_safety_weeks": p_harness_safety,
            "harness_lead_time": p_harness_lead,
            "bullwhip_factor": p_bullwhip,
            "tier1_capacity_growth": p_tier1_growth,
            "sic_dependency": p_sic_dependency,
            "ree_motor_dependency": p_ree_dependency,
            "cell_capacity_growth": p_cell_growth,
            "measurement_lag_weeks": p_meas_lag,
            "price_adjustment_speed": p_price_speed,
            "price_scarcity_sensitivity": p_price_alpha,
            "lfp_shift_speed": p_lfp_speed,
            "capex_trigger_util": p_capex_trigger,
            "cobalt_transport_weeks": p_cobalt_transport,
            "graphite_transport_weeks": p_graphite_transport,
            "ree_transport_weeks": p_ree_transport,
            "sic_transport_weeks": p_sic_transport,
            "cobalt_supply_vol": p_cobalt_vol,
            "graphite_supply_vol": p_graphite_vol,
            "ree_supply_vol": p_ree_vol,
            "sic_supply_vol": p_sic_vol,
            "cobalt_supply_growth": p_cobalt_growth,
            "graphite_supply_growth": p_graphite_growth,
            "ree_supply_growth": p_ree_growth,
            "sic_supply_growth": p_sic_growth,
        }
        result = run_custom_parameter_experiment(params)
        base = result["baseline"]
        custom = result["custom"]
        scenario_label = result["scenario_label"]
        scenario_color = "#38bdf8" if p_shock_setup == CUSTOM_SHOCK else SC_COLOURS[p_scenario]
        weeks = list(range(len(custom["week"])))

        base_prod = np.array(base["oem_production_k"])
        custom_prod = np.array(custom["oem_production_k"])
        rel_loss = np.maximum(0.0, 1.0 - custom_prod / np.maximum(base_prod, 1e-9))
        cum_loss = float(np.maximum(0.0, base_prod - custom_prod).sum())

        k1, k2, k3, k4 = st.columns(4)
        with k1:
            st.metric("Peak loss", f"{rel_loss.max() * 100:.1f}%")
        with k2:
            st.metric("Mean loss", f"{rel_loss.mean() * 100:.1f}%")
        with k3:
            st.metric("Cumulative loss", f"{cum_loss:.0f} k veh")
        with k4:
            st.metric("Final backlog", f"{custom['total_backlog_k'][-1]:.0f} k veh")

        pc1, pc2 = st.columns(2)
        with pc1:
            fig = go.Figure()
            line(fig, weeks, base["oem_production_k"], "Adjusted baseline", "#94a3b8", dash="dot", width=2)
            line(fig, weeks, custom["oem_production_k"], scenario_label, scenario_color, width=2)
            std_layout(fig, "UK OEM Production Under Adjusted Parameters", 320)
            st.plotly_chart(fig, width="stretch")
        with pc2:
            fig = go.Figure()
            line(fig, weeks, custom["price_signal"], "Price pressure", "#f59e0b", width=2)
            line(fig, weeks, custom["stock_harness_wk"], "Harness stock", "#06b6d4", dash="dash")
            line(fig, weeks, custom["stock_cobalt_wk"], "Cobalt stock", "#ef4444", dash="dot")
            std_layout(fig, "Selected SD State Variables", 320)
            st.plotly_chart(fig, width="stretch")

        st.markdown("**Adjusted parameter set**")
        param_df = pd.DataFrame(
            [{"parameter": k, "value": v} for k, v in params.items()]
        )
        st.dataframe(param_df, width="stretch", hide_index=True)

        with st.expander("Source-calibration audit for this run"):
            st.dataframe(pd.DataFrame(result["calibration"]), width="stretch", hide_index=True)
    else:
        st.info("Set the assumptions above, then run the experiment to compare the adjusted scenario with its adjusted baseline.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — POLICY EVALUATION
# ═══════════════════════════════════════════════════════════════════════════════
with T_POLICY:
    st.subheader("UK Government Intervention Evaluation")
    st.caption(
        "Compares shock-only scenarios with matched intervention packages derived from "
        "DRIVE35 and the Advanced Manufacturing Sector Plan."
    )
    pol_df, pol_modified = load_policy_evaluation()

    if pol_df.empty:
        st.warning("No policy evaluation file found. Run `python evaluate_policy_interventions.py` to generate it.")
    else:
        if pol_modified:
            modified_text = pd.to_datetime(pol_modified, unit="s").strftime("%Y-%m-%d %H:%M")
            st.caption(f"Loaded from `results/policy_intervention_evaluation.csv` · last updated {modified_text}")

        best = pol_df.sort_values("avoided_loss_k_veh", ascending=False).iloc[0]
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("Best Avoided Loss", f"{best['avoided_loss_k_veh']:.0f} k veh", best["policy_package"])
        with c2:
            st.metric("Best Avoided Loss %", f"{best['avoided_loss_pct']:.1f}%", best["base_scenario"])
        with c3:
            st.metric("Peak Loss Reduction", f"{best['peak_loss_reduction_pct_pt']:.1f} pp")
        with c4:
            st.metric("Policy Runs", len(pol_df))

        p1, p2 = st.columns([1, 1])
        with p1:
            scenario_filter = st.multiselect(
                "Shock scenario",
                options=sorted(pol_df["base_scenario"].unique()),
                default=sorted(pol_df["base_scenario"].unique()),
            )
        with p2:
            package_filter = st.multiselect(
                "Policy package",
                options=sorted(pol_df["policy_package"].unique()),
                default=sorted(pol_df["policy_package"].unique()),
            )

        view = pol_df[
            pol_df["base_scenario"].isin(scenario_filter)
            & pol_df["policy_package"].isin(package_filter)
        ].copy()

        fig = go.Figure()
        for pkg in sorted(view["policy_package"].unique()):
            sub = view[view["policy_package"] == pkg]
            fig.add_trace(go.Bar(
                x=sub["base_scenario"],
                y=sub["avoided_loss_k_veh"],
                name=pkg,
            ))
        pol_layout = dict(PLOT_LAYOUT)
        pol_layout.update(
            title=dict(text="Avoided UK Vehicle Loss By Intervention", font=dict(size=12, color="#0f172a")),
            height=380,
            barmode="group",
            xaxis=dict(tickangle=-35, gridcolor="#e2e8f0"),
            yaxis=dict(title="Avoided loss (k vehicles)", gridcolor="#e2e8f0"),
        )
        fig.update_layout(**pol_layout)
        st.plotly_chart(fig, width="stretch")

        st.subheader("Policy Impact Table")
        st.dataframe(
            view[[
                "base_scenario",
                "policy_package",
                "shock_only_cumulative_loss_k",
                "policy_cumulative_loss_k",
                "avoided_loss_k_veh",
                "avoided_loss_pct",
                "shock_only_peak_loss_pct",
                "policy_peak_loss_pct",
                "peak_loss_reduction_pct_pt",
                "shock_only_max_backlog_k",
                "policy_max_backlog_k",
            ]],
            width="stretch",
            hide_index=True,
        )

        with st.expander("How the packages are represented in the model"):
            st.markdown(
                """
                - **Battery Sovereignty Package:** increases UK cell scale-up, buffers, recovery, and mitigation of CATL concentration risk.
                - **Tier-1 Resilience Package:** raises component buffers, shortens selected lead times, and improves recovery for harness, inverters, motors, packs, and UK OEM operations.
                - **Critical Minerals Security Package:** adds strategic cobalt, graphite, REE, SiC, and lithium buffers, plus recycling/offtake-style supply boosts.
                - **Full Industrial Strategy Package:** combines the above with a further energy/grid/skills/data overlay that increases growth, recovery, vertical integration, and shock absorption.
                """
            )


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 — VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════
with T_VALIDATION:
    st.subheader("Model Validation Results")
    checks, val_metrics, real_ts_metrics, real_ts_alignment, val_report, val_modified = load_validation_results()

    if checks.empty and val_metrics.empty and not val_report:
        st.warning("No validation artifacts found. Run `python validate_model.py` to generate validation outputs.")
    else:
        if val_modified:
            modified_text = pd.to_datetime(val_modified, unit="s").strftime("%Y-%m-%d %H:%M")
            st.caption(f"Loaded from `results/validation_*.csv` and `results/validation_report.md` · last updated {modified_text}")

        if not checks.empty:
            status_counts = checks["status"].value_counts().to_dict()
            pass_n = int(status_counts.get("PASS", 0))
            warn_n = int(status_counts.get("WARN", 0))
            fail_n = int(status_counts.get("FAIL", 0))

            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.metric("PASS", pass_n)
            with c2:
                st.metric("WARN", warn_n)
            with c3:
                st.metric("FAIL", fail_n, delta_color="inverse")
            with c4:
                st.metric("Total Checks", len(checks))

            if fail_n or warn_n:
                st.subheader("Warnings And Failures")
                st.dataframe(
                    checks[checks["status"].isin(["WARN", "FAIL"])],
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.success("All validation checks passed with no warnings.")

            st.subheader("Validation Checks")
            vf1, vf2 = st.columns([1, 2])
            with vf1:
                status_filter = st.multiselect(
                    "Status filter",
                    options=sorted(checks["status"].dropna().unique()),
                    default=sorted(checks["status"].dropna().unique()),
                )
            with vf2:
                category_filter = st.multiselect(
                    "Category filter",
                    options=sorted(checks["category"].dropna().unique()),
                    default=sorted(checks["category"].dropna().unique()),
                )
            checks_view = checks[
                checks["status"].isin(status_filter)
                & checks["category"].isin(category_filter)
            ]
            st.dataframe(checks_view, width="stretch", hide_index=True)

        if not val_metrics.empty:
            st.subheader("Scenario Validation Metrics")
            validation_scenarios = _validated_base_scenarios(val_metrics)
            if validation_scenarios:
                st.caption("Highlighted below are only the base scenarios included in the validation metrics.")
                card_cols = st.columns(min(3, len(validation_scenarios)))
                for i, scenario_id in enumerate(validation_scenarios):
                    colour = SC_COLOURS.get(scenario_id, "#2563eb")
                    with card_cols[i % len(card_cols)]:
                        st.markdown(
                            f"<div class='scenario-card' style='border-left-color:{colour}'>"
                            f"<strong>{SC_LABELS.get(scenario_id, scenario_id)}</strong>"
                            f"<p>{SC_DESC.get(scenario_id, 'Validation scenario used by the model checks.')}</p>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                st.write("")

            display_metrics = val_metrics.copy()
            if "scenario" in display_metrics.columns:
                display_metrics["scenario_label"] = display_metrics["scenario"].astype(str).map(_scenario_display_label)
                first_cols = ["scenario_label"] + [c for c in display_metrics.columns if c not in {"scenario", "scenario_label"}]
                display_metrics = display_metrics[first_cols]
            st.dataframe(display_metrics, width="stretch", hide_index=True)

            if {"scenario", "cumulative_loss_k_veh_vs_baseline", "max_total_backlog_k"}.issubset(val_metrics.columns):
                validation_plot_df = val_metrics[
                    val_metrics["scenario"].astype(str).isin(validation_scenarios)
                ].copy()
                if validation_plot_df.empty:
                    validation_plot_df = val_metrics.copy()
                validation_plot_df["scenario_label"] = validation_plot_df["scenario"].astype(str).map(_scenario_display_label)
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=validation_plot_df["scenario_label"],
                    y=validation_plot_df["cumulative_loss_k_veh_vs_baseline"],
                    name="Cumulative loss",
                    marker_color="#f97316",
                ))
                fig.add_trace(go.Scatter(
                    x=validation_plot_df["scenario_label"],
                    y=validation_plot_df["max_total_backlog_k"],
                    name="Max backlog",
                    mode="lines+markers",
                    line=dict(color="#2563eb", width=2),
                    yaxis="y2",
                ))
                val_layout = dict(PLOT_LAYOUT)
                val_layout.update(
                    title=dict(text="Validation Scenario Stress Metrics", font=dict(size=12, color="#0f172a")),
                    height=360,
                    yaxis=dict(title="Cumulative loss (k vehicles)", gridcolor="#e2e8f0"),
                    yaxis2=dict(title="Max backlog (k vehicles)", overlaying="y", side="right", gridcolor="#e2e8f0"),
                    xaxis=dict(tickangle=-35, gridcolor="#e2e8f0"),
                )
                fig.update_layout(**val_layout)
                st.plotly_chart(fig, width="stretch")

        if not real_ts_metrics.empty:
            st.subheader("Real Historical Time-Series Validation")
            st.caption(
                "Modelled UK OEM production is benchmarked against the ONS/SMMT monthly UK car production "
                "seasonally adjusted index. Both series are normalised to 2023 average = 100, so MAE is in index points."
            )
            rt1, rt2, rt3 = st.columns(3)
            best_rt = real_ts_metrics.sort_values("mae_index_points").iloc[0]
            with rt1:
                st.metric("Best MAE", f"{best_rt['mae_index_points']:.1f} index pts", best_rt["scenario"])
            with rt2:
                st.metric("Best MAPE", f"{best_rt['mape_index_pct']:.1f}%")
            with rt3:
                st.metric("Months Compared", int(best_rt["months_compared"]))
            st.dataframe(real_ts_metrics, width="stretch", hide_index=True)

            if not real_ts_alignment.empty:
                scenario_options = sorted(real_ts_alignment["scenario"].dropna().unique())
                chosen_rt = st.selectbox("Choose real-data validation scenario", scenario_options, key="real_ts_scenario")
                plot_df = real_ts_alignment[real_ts_alignment["scenario"] == chosen_rt].copy()
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=plot_df["month"],
                    y=plot_df["observed_index_2023_100"],
                    name="Observed ONS/SMMT UK cars",
                    mode="lines+markers",
                    line=dict(color="#10b981", width=2),
                ))
                fig.add_trace(go.Scatter(
                    x=plot_df["month"],
                    y=plot_df["model_index_2023_100"],
                    name="Model UK OEM",
                    mode="lines+markers",
                    line=dict(color="#3b82f6", width=2),
                ))
                rt_layout = dict(PLOT_LAYOUT)
                rt_layout.update(
                    title=dict(text="Observed vs Modelled UK Production Performance", font=dict(size=12, color="#0f172a")),
                    height=340,
                    xaxis=dict(gridcolor="#e2e8f0"),
                    yaxis=dict(title="Index, 2023 average = 100", gridcolor="#e2e8f0"),
                )
                fig.update_layout(**rt_layout)
                st.plotly_chart(fig, width="stretch")

        if val_report:
            with st.expander("Full validation report"):
                st.markdown(val_report)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 — OEM BREAKDOWN
# ═══════════════════════════════════════════════════════════════════════════════
with T_OEM:
    st.markdown(
        "UK EV output aggregates JLR, BMW MINI Oxford, Vauxhall Ellesmere Port, and other UK EV assembly exposure. "
        "Scenario overlays show how global material, cell, and logistics shocks transmit into the UK production endpoint.",
    )

    sc_oem = st.multiselect(
        "Overlay scenario(s):",
        options=SHOCK_SCS,
        default=["uk_supply_chain_friction", "china_catl_disruption", "ukraine_harness"],
        format_func=lambda s: SC_LABELS[s],
        key="oem_sc",
    )

    c1, c2 = st.columns(2)
    bl_uk    = np.array(BL["oem_uk_oem_k"])

    with c1:
        fig = go.Figure()
        line(fig, WEEKS, bl_uk, "Baseline", "#94a3b8", dash="dot", width=1.5)
        for sc in sc_oem:
            d = DATA[sc]
            line(fig, WEEKS, d["oem_uk_oem_k"], SC_LABELS[sc], SC_COLOURS[sc], width=2)
        std_layout(fig, "UK OEM Production (k vehicles / week)", 330)
        fig.update_yaxes(title_text="k vehicles / week")
        st.plotly_chart(fig, width="stretch")

    with c2:
        fig = go.Figure()
        fig.add_hline(y=100, line_dash="dot", line_color="#94a3b8", line_width=0.8)
        for sc in sc_oem:
            d = DATA[sc]
            uk_idx = [v / max(bl_uk[i], 1e-6) * 100 for i, v in enumerate(d["oem_uk_oem_k"])]
            line(fig, WEEKS, uk_idx, SC_LABELS[sc], SC_COLOURS[sc], width=2)
        std_layout(fig, "UK OEM Production Index (100 = baseline)", 310)
        fig.update_yaxes(title_text="Index (100 = baseline)")
        st.plotly_chart(fig, width="stretch")

    c3, c4 = st.columns(2)
    with c3:
        fig = go.Figure()
        line(fig, WEEKS, BL["t1_battery_pack_k"], "Battery packs", "#3b82f6", width=2)
        line(fig, WEEKS, BL["t1_harness_k"], "Harness", "#f97316", dash="dash")
        line(fig, WEEKS, BL["t1_inverter_k"], "Inverter", "#a855f7", dash="dash")
        line(fig, WEEKS, BL["t1_motor_k"], "Motor", "#10b981", dash="dash")
        std_layout(fig, "Tier-1 Throughput Supporting UK Demand", 310)
        fig.update_yaxes(title_text="k units / week")
        st.plotly_chart(fig, width="stretch")

    with c4:
        fig = go.Figure()
        line(fig, WEEKS, BL["market_demand_gwh"], "UK market demand", "#06b6d4", width=2)
        line(fig, WEEKS, BL["cell_production_gwh"], "Cell production", "#94a3b8", dash="dot")
        std_layout(fig, "UK Demand vs Cell Supply Signal", 310)
        fig.update_yaxes(title_text="GWh / week")
        st.plotly_chart(fig, width="stretch")

    st.subheader("UK OEM Reference")
    oem_ref = pd.DataFrame([
        dict(Group=OEM_LABELS["uk_oem"],             Region="UK",     Volume="175 k/yr",   Share="1.25%",  VI="5%",
             Cell_suppliers="Samsung SDI 30%, LG ES 25%, AESC UK 20%, SK On 15%, CATL 10%",
             Key_vulnerability="Post-Brexit RoO; no domestic gigafactory"),
    ])
    st.dataframe(oem_ref.set_index("Group"), width="stretch")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — SUPPLY CHAIN STOCKS
# ═══════════════════════════════════════════════════════════════════════════════
with T_STOCKS:
    st.markdown(
        "Inventory stocks at each supply chain tier. Values < 1 week are critical — the Leontief "
        "constraint propagates downstream and reduces vehicle output."
    )

    sc_stocks = st.multiselect(
        "Overlay scenario(s):",
        options=SHOCK_SCS,
        default=["ukraine_harness", "drc_cobalt", "china_graphite",
                 "china_ree_restriction", "uk_supply_chain_friction"],
        format_func=lambda s: SC_LABELS[s],
        key="stocks_sc",
    )

    stocks_meta = [
        ("stock_cobalt_wk",    "Cobalt Stock",   "DRC supplies 70% of global cobalt. LFP makers (BYD, CALB) are immune.",          6),
        ("stock_graphite_wk",  "Graphite Stock", "China supplies 79% of battery-grade natural graphite (USGS MCS 2024).",           4),
        ("stock_ree_wk",       "REE / NdFeB",    "China processes 85% of NdPr for PMSM motors. 8-week safety-stock buffer.",        8),
        ("stock_sic_wafer_wk", "SiC Wafer",      "Wolfspeed + Coherent = 50% of supply. 12-week buffer, 16-week inverter lead.",   12),
        ("stock_harness_wk",   "Wiring Harness", "JIT — only 2 weeks of safety stock. Ukraine disruption revealed this fragility.", 2),
        ("stock_copper_wk",    "Copper Stock",   "Motor windings & wiring. 3-week transport delay. Low concentration risk.",        3),
        ("price_signal",       "Price Index",    "Composite mineral commodity pressure. 1.0 = baseline; 2.0+ = significant stress.",1),
    ]

    for i in range(0, len(stocks_meta), 2):
        cols = st.columns(2)
        for j, (key, title, note, target) in enumerate(stocks_meta[i:i+2]):
            with cols[j]:
                fig = go.Figure()
                if key != "price_signal":
                    fig.add_hline(y=target, line_dash="dot", line_color="#94a3b8",
                                  line_width=0.8, annotation_text=f"Target {target} wk",
                                  annotation_font_color="#94a3b8", annotation_font_size=9)
                line(fig, WEEKS, BL[key], "Baseline", "#94a3b8", dash="dot", width=1.5)
                for sc in sc_stocks:
                    line(fig, WEEKS, DATA[sc][key], SC_LABELS[sc], SC_COLOURS[sc])
                std_layout(fig, title, 280)
                note_label = "weeks of supply" if key != "price_signal" else "index (1.0 = baseline)"
                fig.update_yaxes(title_text=note_label)
                st.plotly_chart(fig, width="stretch")
                st.caption(note)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 — AGENT ARCHETYPES
# ═══════════════════════════════════════════════════════════════════════════════
with T_ARCHETYPES:
    st.markdown(
        "Each agent in the model belongs to a **behavioural archetype** — a qualitatively distinct "
        "decision rule that reflects real-world strategic posture. Archetypes share the same "
        "common mechanics (shock interface, growth compounding, Leontief constraint) but override "
        "a single protected method to implement categorically different behaviour. "
        "The result is emergent heterogeneity that cannot be captured by parameter variation alone."
    )

    # ── Tier overview cards ───────────────────────────────────────────────────
    mc, cc, tc, oc = st.columns(4)
    def _arch_card(col, colour, emoji, tier, count, names):
        col.markdown(
            f"<div style='border:1px solid {colour};border-radius:10px;padding:12px 14px'>"
            f"<div style='color:{colour};font-size:1.1rem;font-weight:700'>{emoji} Tier {tier}</div>"
            f"<div style='color:#0f172a;font-size:0.95rem;font-weight:600;margin:4px 0'>{count} archetypes</div>"
            f"<div style='color:#475569;font-size:0.78rem'>{names}</div></div>",
            unsafe_allow_html=True,
        )
    _arch_card(mc, "#f59e0b", "⛏️", "0  Minerals", 3, "StateBacked · WesternMiner · GreenfieldBuilder")
    _arch_card(cc, "#3b82f6", "🔋", "1  Cells",    3, "PlatformLeader · HyperScaleChallenger · IncumbentUnderPressure")
    _arch_card(tc, "#a855f7", "⚙️", "2  Suppliers",3, "BatteryPackIntegrator · PremiumPowerElectronics · EstablishedVolumeSupplier")
    _arch_card(oc, "#10b981", "🏭", "3  OEMs",     3, "TransitioningLegacyOEM · EVNativeScaleAspirant · ProfitableEstablishedOEM")

    st.divider()

    # ── Archetype charts ──────────────────────────────────────────────────────
    ac1, ac2 = st.columns(2)

    with ac1:
        # LFP chemistry shift under cobalt shock — PlatformLeader reacts fast, Incumbents slow
        fig = go.Figure()
        line(fig, WEEKS, BL["lfp_share"],
             "LFP share — Baseline", "#94a3b8", dash="dot", width=1.5)
        line(fig, WEEKS, DATA["drc_cobalt"]["lfp_share"],
             "LFP share — DRC Cobalt shock", SC_COLOURS["drc_cobalt"], width=2, fill=True)
        line(fig, WEEKS, DATA["china_catl_disruption"]["lfp_share"],
             "LFP share — CATL disruption", SC_COLOURS["china_catl_disruption"], dash="dash")
        fig.add_hline(y=0.403, line_dash="dot", line_color="#64748b", line_width=0.8,
                      annotation_text="IEA 2024 baseline (40.3%)",
                      annotation_font_color="#64748b", annotation_font_size=9)
        std_layout(fig, "Chemistry Flexibility — LFP Share Evolution", 310)
        fig.update_yaxes(title_text="LFP fraction [0-1]")
        st.plotly_chart(fig, width="stretch")
        st.caption(
            "PlatformLeader (CATL, BYD) shifts to LFP at 0.5 %/wk when cobalt > 1.3×. "
            "IncumbentUnderPressure (LG ES, Panasonic) shifts at 0.1 %/wk — 5× slower. "
            "Fast shifters are insulated from cobalt price shocks; incumbents are exposed."
        )

    with ac2:
        # Bullwhip index — JIT vs forward-ordering amplification
        fig = go.Figure()
        fig.add_hline(y=1.0, line_dash="dot", line_color="#64748b", line_width=0.8,
                      annotation_text="No amplification", annotation_font_color="#64748b",
                      annotation_font_size=9)
        line(fig, WEEKS, BL["bullwhip_index"],
             "Bullwhip — Baseline", "#94a3b8", dash="dot", width=1.5)
        line(fig, WEEKS, DATA["compound_shock"]["bullwhip_index"],
             "Bullwhip — Compound shock", SC_COLOURS["compound_shock"], width=2, fill=True)
        line(fig, WEEKS, DATA["ukraine_harness"]["bullwhip_index"],
             "Bullwhip — Ukraine harness", SC_COLOURS["ukraine_harness"], dash="dash")
        std_layout(fig, "Ordering Amplification — Bullwhip Index", 310)
        fig.update_yaxes(title_text="Bullwhip index (1.0 = no amplification)")
        st.plotly_chart(fig, width="stretch")
        st.caption(
            "BatteryPackIntegrator (JIT) passes demand through exactly. "
            "PremiumPowerElectronics forward-projects demand 16 weeks ahead — amplifying "
            "signals in both directions. EstablishedVolumeSupplier smooths at 80% when "
            "overstocked, damping the amplification cascade."
        )

    # Price cascade chart
    ac3, ac4 = st.columns(2)
    with ac3:
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                            row_heights=[0.5, 0.5])
        line(fig, WEEKS, BL["price_cobalt"], "Cobalt price — Baseline",
             "#94a3b8", dash="dot", width=1, row=1, col=1)
        line(fig, WEEKS, DATA["drc_cobalt"]["price_cobalt"], "Cobalt price — DRC shock",
             SC_COLOURS["drc_cobalt"], width=2, row=1, col=1)
        line(fig, WEEKS, BL["price_signal"], "Price signal — Baseline",
             "#94a3b8", dash="dot", width=1, row=2, col=1)
        line(fig, WEEKS, DATA["drc_cobalt"]["price_signal"], "Price signal — DRC shock",
             SC_COLOURS["drc_cobalt"], width=2, row=2, col=1)
        std_layout(fig, "Cobalt Price -> Composite Price Signal Cascade (DRC shock)", 320)
        fig.update_yaxes(title_text="Price index", row=1, col=1,
                         gridcolor="#e2e8f0", tickfont=dict(size=9))
        fig.update_yaxes(title_text="Signal index", row=2, col=1,
                         gridcolor="#e2e8f0", tickfont=dict(size=9))
        fig.update_xaxes(gridcolor="#e2e8f0", tickfont=dict(size=9),
                         tickvals=list(YEAR_TICKS.keys()), ticktext=list(YEAR_TICKS.values()),
                         row=2, col=1)
        st.plotly_chart(fig, width="stretch")
        st.caption(
            "Cobalt price (Tier-0 output) propagates through a 4-week first-order lag "
            "into the composite OEM price signal. StateBacked miners (China graphite, REE) "
            "further amplify the signal by restricting output when price exceeds 1.8×."
        )

    with ac4:
        # Cell capacity utilisation and cell production under CATL disruption
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                            row_heights=[0.5, 0.5])
        line(fig, WEEKS, BL["cell_cap_util"], "Utilisation — Baseline",
             "#94a3b8", dash="dot", width=1, row=1, col=1)
        line(fig, WEEKS, DATA["china_catl_disruption"]["cell_cap_util"],
             "Utilisation — CATL disruption", SC_COLOURS["china_catl_disruption"], width=2, row=1, col=1)
        line(fig, WEEKS, BL["cell_production_gwh"], "Cell output — Baseline",
             "#94a3b8", dash="dot", width=1, row=2, col=1)
        line(fig, WEEKS, DATA["china_catl_disruption"]["cell_production_gwh"],
             "Cell output — CATL disruption", SC_COLOURS["china_catl_disruption"], width=2, row=2, col=1)
        std_layout(fig, "PlatformLeader Disruption -> Cell Capacity & Output (CATL shock)", 320)
        fig.update_yaxes(title_text="Utilisation", row=1, col=1,
                         gridcolor="#e2e8f0", tickfont=dict(size=9))
        fig.update_yaxes(title_text="GWh/week", row=2, col=1,
                         gridcolor="#e2e8f0", tickfont=dict(size=9))
        fig.update_xaxes(gridcolor="#e2e8f0", tickfont=dict(size=9),
                         tickvals=list(YEAR_TICKS.keys()), ticktext=list(YEAR_TICKS.values()),
                         row=2, col=1)
        st.plotly_chart(fig, width="stretch")
        st.caption(
            "CATL (PlatformLeader, 37% global share) runs at full capacity to rebuild buffer "
            "when inventory < 70% target — a strategic behaviour not exhibited by HyperScaleChallenger "
            "or IncumbentUnderPressure agents, which drives the recovery trajectory difference."
        )

    st.divider()

    # ── Archetype reference tables ────────────────────────────────────────────
    st.subheader("Archetype Reference")

    with st.expander("Tier 0 — Mineral Suppliers (3 archetypes, 12 agents)", expanded=True):
        t0 = pd.DataFrame([
            dict(Archetype="StateBacked",
                 Agents="graphite_chn, ree_chn, cobalt_other, sic_china",
                 Decision_hook="_compute_output_fraction()",
                 Key_behaviour="Restricts exports when price > 1.8× (counter-market). "
                               "Expands normally when 1.0 < price < 1.8×.",
                 Main_parameters="restriction_trigger=1.80, production_floor=0.85",
                 Scenario_trigger="china_graphite, china_ree_restriction, us_china_tariff"),
            dict(Archetype="WesternMiner",
                 Agents="lithium_aus, lithium_chl, cobalt_drc, sic_coherent, sic_other",
                 Decision_hook="_compute_output_fraction()",
                 Key_behaviour="Mothballs (caps output below floor) after 12 consecutive "
                               "low-price (<0.85×) weeks. Recovers gradually when price normalises.",
                 Main_parameters="mothball_threshold=0.85, trigger_wks=12, floor_drop=0.15",
                 Scenario_trigger="drc_cobalt (recovery phase)"),
            dict(Archetype="GreenfieldBuilder",
                 Agents="lithium_other, ree_other, sic_wolfspeed",
                 Decision_hook="_compute_output_fraction()",
                 Key_behaviour="Debt-financed must-run. Permanent capacity degradation "
                               "(0.5%/wk, max 25%) accumulates after 8+ weeks of severe shock.",
                 Main_parameters="distress_threshold=0.50, trigger_wks=8, degradation_rate=0.005",
                 Scenario_trigger="sic_bottleneck, compound_shock"),
        ])
        st.dataframe(t0.set_index("Archetype"), width="stretch")

    with st.expander("Tier 1 — Cell Manufacturers (3 archetypes, 8 agents)", expanded=True):
        t1c = pd.DataFrame([
            dict(Archetype="PlatformLeader",
                 Agents="catl (37%), byd_cells (14%)",
                 Decision_hook="_desired_production()",
                 Key_behaviour="Runs at full capacity when inventory < 70% of target (stockpile mode). "
                               "Fast LFP chemistry shift: +0.5%/wk when cobalt > 1.3×.",
                 Main_parameters="inv_threshold=0.70, chemistry_rate=0.005/wk, max_lfp=0.70",
                 Scenario_trigger="drc_cobalt (benefits), china_catl_disruption (shock)"),
            dict(Archetype="HyperScaleChallenger",
                 Agents="calb (5%), others_cells (12.8%)",
                 Decision_hook="_desired_production()",
                 Key_behaviour="Always pushes at full capacity regardless of downstream demand. "
                               "Growth penalty (50% of growth rate) fires if output < 50% demand for 8+ wks.",
                 Main_parameters="shortfall_trigger=8 wks, growth_penalty=0.50x",
                 Scenario_trigger="compound_shock (growth penalty activates)"),
            dict(Archetype="IncumbentUnderPressure",
                 Agents="lg_es (13%), panasonic (7%), samsung_sdi (6%), sk_on (5%)",
                 Decision_hook="_desired_production() + step()",
                 Key_behaviour="Demand-pull with minimal top-up. Market share erodes 0.01%/wk. "
                               "Slow LFP shift: +0.1%/wk when cobalt > 1.3×, max 50% LFP.",
                 Main_parameters="share_erosion=0.0001/wk, chemistry_rate=0.001/wk, max_lfp=0.50",
                 Scenario_trigger="drc_cobalt (exposed via NMC concentration)"),
        ])
        st.dataframe(t1c.set_index("Archetype"), width="stretch")

    with st.expander("Tier 2 — Sub-system Suppliers (3 archetypes, 4 agents)", expanded=True):
        t2 = pd.DataFrame([
            dict(Archetype="BatteryPackIntegrator",
                 Agents="battery_pack",
                 Decision_hook="_order_quantity()",
                 Key_behaviour="Pure JIT: orders exactly weekly demand, no safety stock buffer, "
                               "no bullwhip factor. Zero amplification by design.",
                 Main_parameters="order = demand (exact), bullwhip bypassed",
                 Scenario_trigger="All (minimal amplification; harness is larger fragility)"),
            dict(Archetype="PremiumPowerElectronics",
                 Agents="inverter (16-wk lead time)",
                 Decision_hook="_order_quantity()",
                 Key_behaviour="Forward-projects demand 16 weeks ahead. Defers to 60% of demand "
                               "when SiC price > 1.5×. Dual-sources below 30% inventory cover.",
                 Main_parameters="defer_threshold=1.50, lead_time_wks=16, dual_source_threshold=0.30",
                 Scenario_trigger="sic_bottleneck (demand deferral)"),
            dict(Archetype="EstablishedVolumeSupplier",
                 Agents="motor (12-wk lead), harness (6-wk lead)",
                 Decision_hook="_order_quantity()",
                 Key_behaviour="Production smoothing: reduces order to 80% when inventory position "
                               "> 150% of target. Standard bullwhip (1.25×) otherwise.",
                 Main_parameters="smoothing_threshold=1.50x, smooth_factor=0.80",
                 Scenario_trigger="ukraine_harness (harness is EstablishedVolumeSupplier)"),
        ])
        st.dataframe(t2.set_index("Archetype"), width="stretch")

    with st.expander("Tier 3 — OEMs (3 archetypes, 6 agents)", expanded=True):
        t3 = pd.DataFrame([
            dict(Archetype="TransitioningLegacyOEM",
                 Agents="german_oem, us_oem, uk_oem",
                 Decision_hook="_compute_production_target()",
                 Key_behaviour="ICE fallback: shifts up to 20% of EV demand to ICE when OEM "
                               "margin signal < 85%. Recovery rate is half the ramp rate (asymmetric).",
                 Main_parameters="fallback_threshold=0.85, max_fallback=0.20, ramp=0.01/wk",
                 Scenario_trigger="drc_cobalt, compound_shock (margin pressure triggers fallback)"),
            dict(Archetype="EVNativeScaleAspirant",
                 Agents="other_chinese_oem (NIO, Li Auto, Xpeng…)",
                 Decision_hook="_compute_production_target()",
                 Key_behaviour="Demand-elastic: boosts weekly target by up to 5% when demand exceeds "
                               "target. 5% proximity boost from co-located supply chain.",
                 Main_parameters="demand_elasticity=0.15, max_boost=0.05/wk, proximity=1.05",
                 Scenario_trigger="us_china_tariff (tariff removes proximity advantage)"),
            dict(Archetype="ProfitableEstablishedOEM",
                 Agents="korean_oem (Hyundai/Kia), japanese_oem (Toyota/Honda)",
                 Decision_hook="_compute_production_target()",
                 Key_behaviour="Buffer-first: full production only when average inventory > 50% of "
                               "target. Scales back to demand-only when below buffer floor.",
                 Main_parameters="buffer_floor_ratio=0.50, max_prod=1.15x target",
                 Scenario_trigger="china_ree_restriction (REE dependency for motors)"),
        ])
        st.dataframe(t3.set_index("Archetype"), width="stretch")

    # Scenario-archetype activation matrix
    st.subheader("Scenario — Archetype Activation Matrix")
    st.caption("Which archetypes are most stressed / activated under each scenario.")
    act_matrix = pd.DataFrame([
        {"Scenario": "DRC Cobalt",         "StateBacked": "",     "WesternMiner": "mothball",    "GreenfieldBuilder": "",       "PlatformLeader": "LFP shift", "HyperScaleChallenger": "",     "IncumbentUnderPressure": "exposed",   "BatteryPackIntegrator": "",         "PremiumPowerElectronics": "",   "EstablishedVolumeSupplier": "",   "TransitioningLegacyOEM": "ICE fallback", "EVNativeScaleAspirant": "",       "ProfitableEstablishedOEM": ""},
        {"Scenario": "Ukraine Harness",    "StateBacked": "",     "WesternMiner": "",            "GreenfieldBuilder": "",       "PlatformLeader": "",          "HyperScaleChallenger": "",     "IncumbentUnderPressure": "",          "BatteryPackIntegrator": "JIT pass",  "PremiumPowerElectronics": "",   "EstablishedVolumeSupplier": "smoothed",  "TransitioningLegacyOEM": "ICE fallback", "EVNativeScaleAspirant": "",       "ProfitableEstablishedOEM": "buffer"},
        {"Scenario": "SiC Bottleneck",     "StateBacked": "",     "WesternMiner": "",            "GreenfieldBuilder": "degrade","PlatformLeader": "",          "HyperScaleChallenger": "",     "IncumbentUnderPressure": "",          "BatteryPackIntegrator": "",          "PremiumPowerElectronics": "defer","EstablishedVolumeSupplier": "",   "TransitioningLegacyOEM": "",             "EVNativeScaleAspirant": "",       "ProfitableEstablishedOEM": "buffer"},
        {"Scenario": "China REE",          "StateBacked": "restrict","WesternMiner": "",         "GreenfieldBuilder": "",       "PlatformLeader": "",          "HyperScaleChallenger": "",     "IncumbentUnderPressure": "",          "BatteryPackIntegrator": "",          "PremiumPowerElectronics": "",   "EstablishedVolumeSupplier": "",   "TransitioningLegacyOEM": "",             "EVNativeScaleAspirant": "",       "ProfitableEstablishedOEM": "REE exposed"},
        {"Scenario": "China Graphite",     "StateBacked": "restrict","WesternMiner": "",         "GreenfieldBuilder": "",       "PlatformLeader": "LFP shift", "HyperScaleChallenger": "push", "IncumbentUnderPressure": "exposed",   "BatteryPackIntegrator": "",          "PremiumPowerElectronics": "",   "EstablishedVolumeSupplier": "",   "TransitioningLegacyOEM": "ICE fallback", "EVNativeScaleAspirant": "",       "ProfitableEstablishedOEM": ""},
        {"Scenario": "Compound Shock",     "StateBacked": "restrict","WesternMiner": "mothball", "GreenfieldBuilder": "degrade","PlatformLeader": "LFP shift", "HyperScaleChallenger": "penalty","IncumbentUnderPressure": "exposed", "BatteryPackIntegrator": "JIT pass",  "PremiumPowerElectronics": "defer","EstablishedVolumeSupplier": "smooth","TransitioningLegacyOEM": "ICE fallback","EVNativeScaleAspirant": "",    "ProfitableEstablishedOEM": "buffer"},
        {"Scenario": "US-China Tariff",    "StateBacked": "restrict","WesternMiner": "",         "GreenfieldBuilder": "",       "PlatformLeader": "shocked",   "HyperScaleChallenger": "shocked","IncumbentUnderPressure": "shocked",  "BatteryPackIntegrator": "",          "PremiumPowerElectronics": "",   "EstablishedVolumeSupplier": "",   "TransitioningLegacyOEM": "",             "EVNativeScaleAspirant": "tariff loss","ProfitableEstablishedOEM": "buffer"},
        {"Scenario": "UK Brexit Friction", "StateBacked": "",     "WesternMiner": "",            "GreenfieldBuilder": "",       "PlatformLeader": "",          "HyperScaleChallenger": "",     "IncumbentUnderPressure": "",          "BatteryPackIntegrator": "",          "PremiumPowerElectronics": "",   "EstablishedVolumeSupplier": "",   "TransitioningLegacyOEM": "ICE fallback", "EVNativeScaleAspirant": "",       "ProfitableEstablishedOEM": ""},
        {"Scenario": "CATL Disruption",    "StateBacked": "",     "WesternMiner": "",            "GreenfieldBuilder": "",       "PlatformLeader": "shocked",   "HyperScaleChallenger": "push", "IncumbentUnderPressure": "gains share","BatteryPackIntegrator": "",         "PremiumPowerElectronics": "",   "EstablishedVolumeSupplier": "",   "TransitioningLegacyOEM": "ICE fallback", "EVNativeScaleAspirant": "",       "ProfitableEstablishedOEM": "buffer"},
    ]).set_index("Scenario")
    st.dataframe(act_matrix, width="stretch")
    st.caption(
        "Empty cells = archetype not meaningfully activated. "
        "ICE fallback fires when OEM margin signal < 0.85 (price premium > 15%). "
        "StateBacked restriction triggers at price > 1.8x. "
        "WesternMiner mothball triggers after 12 consecutive weeks below 0.85x."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 6 — UK & CHINA FOCUS
# ═══════════════════════════════════════════════════════════════════════════════
with T_FOCUS:

    uk_col, cn_col = st.columns(2)

    # ── UK ────────────────────────────────────────────────────────────────────
    with uk_col:
        st.markdown(
            "<div style='border:1px solid #06b6d4;border-radius:10px;padding:16px'>"
            "<h3 style='color:#06b6d4;margin-bottom:6px'>🇬🇧 UK EV Manufacturers</h3>"
            "<p style='color:#475569;font-size:0.8rem'>JLR (Tata Motors), BMW MINI Oxford, "
            "Vauxhall (Stellantis Ellesmere Port). Post-Brexit rules-of-origin threshold "
            "rises to 55% UK/EU content by 2027.</p></div>",
            unsafe_allow_html=True,
        )
        st.markdown("")

        # UK KPIs
        uk_fric = DATA["uk_supply_chain_friction"]
        uk_ua   = DATA["ukraine_harness"]
        bl_uk   = np.array(BL["oem_uk_oem_k"])
        fric_min = min(uk_fric["oem_uk_oem_k"])
        uk_peak_loss = (1 - fric_min / max(bl_uk[:4].mean(), 1e-6)) * 100

        kc1, kc2, kc3, kc4 = st.columns(4)
        kc1.metric("EV Volume", "175 k/yr", "SMMT 2024")
        kc2.metric("Brexit Peak Loss", f"{uk_peak_loss:.1f}%", "yr 1 throughput")
        kc3.metric("Domestic cells", "~0.2%", "AESC only")
        kc4.metric("RoO 2027", "55%", "UK/EU content req.")

        fig_uk = go.Figure()
        line(fig_uk, WEEKS, bl_uk, "Baseline", "#94a3b8", dash="dot", width=1.5)
        line(fig_uk, WEEKS, uk_fric["oem_uk_oem_k"], "Brexit Friction", "#06b6d4", width=2, fill=True)
        line(fig_uk, WEEKS, uk_ua["oem_uk_oem_k"],   "Ukraine Harness", "#ef4444")
        std_layout(fig_uk, "UK OEM Production (k vehicles / week)", 280)
        st.plotly_chart(fig_uk, width="stretch")

    # ── Upstream exposure ─────────────────────────────────────────────────────
    with cn_col:
        st.markdown(
            "<div style='border:1px solid #ef4444;border-radius:10px;padding:16px'>"
            "<h3 style='color:#ef4444;margin-bottom:6px'>Upstream China Exposure</h3>"
            "<p style='color:#475569;font-size:0.8rem'>The UK endpoint remains exposed to "
            "CATL concentration, China graphite controls, and China rare-earth magnet restrictions "
            "through imported cells, packs, motors, and materials.</p></div>",
            unsafe_allow_html=True,
        )
        st.markdown("")

        catl_d  = DATA["china_catl_disruption"]
        graphite_d = DATA["china_graphite"]
        ree_d = DATA["china_ree_restriction"]
        catl_rel = np.array(catl_d["cell_production_gwh"]) / np.maximum(np.array(BL["cell_production_gwh"]), 1e-6)
        catl_peak = max(0.0, (1 - catl_rel.min()) * 100)
        graphite_min = min(graphite_d["stock_graphite_wk"])
        ree_min = min(ree_d["stock_ree_wk"])

        kc1, kc2, kc3, kc4 = st.columns(4)
        kc1.metric("CATL share", "37%", "global cells")
        kc2.metric("Graphite floor", f"{graphite_min:.1f} wk", "China permit shock")
        kc3.metric("REE floor", f"{ree_min:.1f} wk", "magnet shock")
        kc4.metric("CATL disruption", f"{catl_peak:.1f}%", "cell peak loss")

        fig_cn = go.Figure()
        line(fig_cn, WEEKS, BL["oem_uk_oem_k"], "UK baseline", "#94a3b8", dash="dot", width=1.5)
        line(fig_cn, WEEKS, catl_d["oem_uk_oem_k"], "UK output — CATL disruption", "#ef4444", width=2)
        line(fig_cn, WEEKS, graphite_d["oem_uk_oem_k"], "UK output — graphite controls", "#ec4899", width=2)
        line(fig_cn, WEEKS, ree_d["oem_uk_oem_k"], "UK output — REE restriction", "#10b981", width=2)
        std_layout(fig_cn, "UK Output Under China-Linked Upstream Shocks", 280)
        st.plotly_chart(fig_cn, width="stretch")

    st.divider()

    # ── 4-panel comparison ────────────────────────────────────────────────────
    st.subheader("Detailed Comparison Charts")
    r1c1, r1c2 = st.columns(2)

    with r1c1:
        fig = go.Figure()
        line(fig, WEEKS, BL["oem_uk_oem_k"], "Baseline", "#94a3b8", dash="dot", width=1.5)
        line(fig, WEEKS, DATA["uk_supply_chain_friction"]["oem_uk_oem_k"],
             "Brexit Friction", "#06b6d4", width=2, fill=True)
        line(fig, WEEKS, DATA["uk_supply_chain_friction"]["stock_harness_wk"],
             "Harness stock (wks)", "#f97316", dash="dash")
        std_layout(fig, "UK Supply Chain Friction — Production & Harness Stock", 290)
        st.plotly_chart(fig, width="stretch")
        st.caption("−10% throughput yr 1, −5% yr 2. Harness delivery −8% for 26 weeks. "
                   "Calibrated to SMMT 2023 post-Brexit impact assessment.")

    with r1c2:
        fig = go.Figure()
        line(fig, WEEKS, BL["cell_production_gwh"],
             "Cell production (Baseline)", "#94a3b8", dash="dot", width=1.5)
        line(fig, WEEKS, DATA["china_catl_disruption"]["cell_production_gwh"],
             "Cell production (CATL disruption)", "#dc2626", width=2, fill=True)
        line(fig, WEEKS, DATA["china_catl_disruption"]["oem_uk_oem_k"],
             "UK OEM output", "#f97316", dash="dash")
        std_layout(fig, "CATL Disruption — Cell Supply Impact (−45% for 65 weeks)", 290)
        fig.update_yaxes(title_text="GWh or k veh / week")
        st.plotly_chart(fig, width="stretch")
        st.caption("Ningde cluster scenario. Quantifies cost of 37% global cell supply "
                   "concentration in a single manufacturer.")

    r2c1, r2c2 = st.columns(2)
    with r2c1:
        fig = go.Figure()
        fig.add_hline(y=100, line_dash="dot", line_color="#94a3b8", line_width=0.8)
        for sc in SHOCK_SCS:
            uk_idx = [v / max(bl_uk[i], 1e-6) * 100 for i, v in enumerate(DATA[sc]["oem_uk_oem_k"])]
            line(fig, WEEKS, uk_idx, SC_LABELS[sc], SC_COLOURS[sc], width=1.6, showlegend=True)
        std_layout(fig, "UK OEM Production Index — All Scenarios", 310, show_legend=True)
        fig.update_yaxes(title_text="Index (100 = baseline)")
        st.plotly_chart(fig, width="stretch")

    with r2c2:
        # Bar chart showing peak UK production loss by key scenario
        sc_compare = ["ukraine_harness", "drc_cobalt", "china_catl_disruption",
                      "uk_supply_chain_friction", "china_ree_restriction"]
        bl_arr = np.array(BL["oem_uk_oem_k"])
        labels, losses = [], []
        for sc in sc_compare:
            arr = np.array(DATA[sc]["oem_uk_oem_k"])
            rel = arr / np.maximum(bl_arr[:len(arr)], 1e-6)
            labels.append(SC_LABELS[sc])
            losses.append(round(max(0.0, (1 - rel.min()) * 100), 1))
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=labels,
            y=losses,
            marker_color=[SC_COLOURS[sc] for sc in sc_compare],
        ))
        std_layout(fig, "Peak UK Production Loss (%) by Scenario", 310, show_legend=False)
        fig.update_xaxes(tickangle=-30, tickfont=dict(size=8))
        fig.update_yaxes(title_text="Peak loss (%)")
        st.plotly_chart(fig, width="stretch")

    st.divider()

    # Cell maker reference
    st.subheader("Cell Maker Reference — UK & China")
    cell_ref = pd.DataFrame([
        dict(Maker="CATL",        Country="China",        Capacity_GWh=304.1, Share="37.0%", LFP="45%", NMC="55%", Cobalt_exposure="Partial"),
        dict(Maker="BYD Cells",   Country="China",        Capacity_GWh=115.1, Share="14.0%", LFP="90%", NMC="10%", Cobalt_exposure="Minimal"),
        dict(Maker="CALB ★",      Country="China",        Capacity_GWh=41.1,  Share="5.0%",  LFP="80%", NMC="20%", Cobalt_exposure="Low"),
        dict(Maker="AESC UK ★",   Country="UK",           Capacity_GWh=1.6,   Share="0.2%",  LFP="0%",  NMC="100%",Cobalt_exposure="Full (NMC 811)"),
        dict(Maker="LG ES",       Country="South Korea",  Capacity_GWh=106.9, Share="13.0%", LFP="5%",  NMC="95%", Cobalt_exposure="High"),
        dict(Maker="Panasonic",   Country="Japan",        Capacity_GWh=57.5,  Share="7.0%",  LFP="0%",  NMC="100%",Cobalt_exposure="Full (NCA)"),
        dict(Maker="Samsung SDI", Country="South Korea",  Capacity_GWh=49.3,  Share="6.0%",  LFP="0%",  NMC="100%",Cobalt_exposure="Full"),
        dict(Maker="SK On",       Country="South Korea",  Capacity_GWh=41.1,  Share="5.0%",  LFP="0%",  NMC="100%",Cobalt_exposure="Full"),
        dict(Maker="Others",      Country="Mixed",        Capacity_GWh=105.2, Share="12.8%", LFP="50%", NMC="50%", Cobalt_exposure="Moderate"),
    ])
    st.dataframe(cell_ref.set_index("Maker"), width="stretch")
    st.caption("★ = newly added in model v2.0. Sources: IEA GEO 2024; SNE Research 2023; "
               "AESC press release Nov 2023; CALB IPO Prospectus 2022.")


# ══════════════════════════════════════════════════════════════════════════════
# HELPER — World Bank live data fetch
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_world_bank_data():
    """Fetch manufacturing, high-tech export, and GDP data from the World Bank API."""
    COUNTRY_META = {
        "CHN": {"name": "China",       "flag": "\U0001f1e8\U0001f1f3", "role": "Cell & mineral dominant"},
        "KOR": {"name": "South Korea", "flag": "\U0001f1f0\U0001f1f7", "role": "Incumbent cell makers"},
        "JPN": {"name": "Japan",       "flag": "\U0001f1ef\U0001f1f5", "role": "NCA cells, hybrid OEMs"},
        "DEU": {"name": "Germany",     "flag": "\U0001f1e9\U0001f1ea", "role": "Transitioning legacy OEMs"},
        "USA": {"name": "USA",         "flag": "\U0001f1fa\U0001f1f8", "role": "EV scale-up, tariff policy"},
        "AUS": {"name": "Australia",   "flag": "\U0001f1e6\U0001f1fa", "role": "Lithium & nickel exporter"},
        "CHL": {"name": "Chile",       "flag": "\U0001f1e8\U0001f1f1", "role": "Lithium brine producer"},
        "COD": {"name": "DR Congo",    "flag": "\U0001f1e8\U0001f1e9", "role": "Cobalt dominant (70%)"},
        "IDN": {"name": "Indonesia",   "flag": "\U0001f1ee\U0001f1e9", "role": "Nickel laterite producer"},
        "GBR": {"name": "UK",          "flag": "\U0001f1ec\U0001f1e7", "role": "Transitioning OEM, AESC cell"},
    }
    INDICATORS = {
        "NV.IND.MANF.ZS":    "Manufacturing % GDP",
        "TX.VAL.TECH.MF.ZS": "High-tech exports %",
        "NY.GDP.MKTP.CD":    "GDP (USD)",
    }
    codes = ";".join(COUNTRY_META.keys())
    country_data = {iso: {} for iso in COUNTRY_META}

    for ind_code in INDICATORS:
        url = (
            f"https://api.worldbank.org/v2/country/{codes}/indicator/{ind_code}"
            f"?format=json&mrv=3&per_page=500"
        )
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            payload = resp.json()
            if not isinstance(payload, list) or len(payload) < 2:
                continue
            for record in payload[1]:
                iso = record.get("countryiso3code", "")
                if iso not in country_data:
                    continue
                val = record.get("value")
                yr  = record.get("date", "")
                if val is not None:
                    existing = country_data[iso].get(ind_code)
                    if existing is None or yr > existing.get("year", ""):
                        country_data[iso][ind_code] = {"value": val, "year": yr}
        except Exception:
            pass

    countries_list = [{**COUNTRY_META[iso], "iso": iso} for iso in COUNTRY_META]
    return {"countries": countries_list, "data": country_data}


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_critical_mineral_prices():
    """Fetch EV-relevant commodity price benchmarks from open public sources."""
    te_specs = [
        {
            "slug": "lithium",
            "mineral": "Lithium carbonate",
            "ev_use": "Battery cathode precursor",
            "benchmark": "China battery-grade lithium carbonate CFD",
        },
        {
            "slug": "cobalt",
            "mineral": "Cobalt",
            "ev_use": "NMC/NCA cathodes",
            "benchmark": "Cobalt OTC/CFD benchmark",
        },
        {
            "slug": "neodymium",
            "mineral": "Neodymium rare earth",
            "ev_use": "NdFeB traction motor magnets",
            "benchmark": "Neodymium rare earth CFD",
        },
    ]
    wb_specs = [
        ("Nickel", "NMC cathodes, stainless battery systems", "World Bank Pink Sheet monthly"),
        ("Copper", "Motors, wiring harnesses, busbars", "World Bank Pink Sheet monthly"),
        ("Aluminum", "Vehicle lightweighting, pack casings", "World Bank Pink Sheet monthly"),
    ]
    rows = []
    errors = []
    headers = {"User-Agent": "Mozilla/5.0 (EV supply-chain dashboard)"}

    def _num(txt):
        try:
            return float(str(txt).replace(",", "").strip())
        except Exception:
            return np.nan

    for spec in te_specs:
        url = f"https://tradingeconomics.com/commodity/{spec['slug']}"
        try:
            resp = requests.get(url, headers=headers, timeout=12)
            resp.raise_for_status()
            text = resp.text.replace("\r", " ").replace("\n", " ")
            meta = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]+)"', text)
            hay = meta.group(1) if meta else re.sub(r"<[^>]+>", " ", text[:12000])
            price_match = re.search(
                r"(?:traded|fell|rose|increased|decreased|held|was|were)[^.]{0,120}?"
                r"(?:to|at)\s+([0-9][0-9,]*(?:\.[0-9]+)?)\s*([A-Z]{3}\s*/?\s*T|CNY/T|USD/T|RMB/T)",
                hay,
                flags=re.IGNORECASE,
            )
            date_match = re.search(r"on ([A-Z][a-z]+ \d{1,2}, \d{4})", hay)
            month_match = re.search(
                r"past month[^.]{0,80}?([0-9]+(?:\.[0-9]+)?)%",
                hay,
                flags=re.IGNORECASE,
            )
            if not price_match:
                raise ValueError("price text not found")
            price = _num(price_match.group(1))
            unit = price_match.group(2).replace(" ", "").replace("RMB", "CNY")
            rows.append({
                "Mineral": spec["mineral"],
                "Price": price,
                "Unit": unit,
                "Latest_date": date_match.group(1) if date_match else "latest page value",
                "Change": f"{month_match.group(1)}% over past month" if month_match else "",
                "Benchmark": spec["benchmark"],
                "EV_use": spec["ev_use"],
                "Source": "Trading Economics",
                "URL": url,
            })
        except Exception as exc:
            errors.append(f"{spec['mineral']}: {exc}")

    wb_url = (
        "https://thedocs.worldbank.org/en/doc/"
        "74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/"
        "CMO-Historical-Data-Monthly.xlsx"
    )
    try:
        resp = requests.get(wb_url, headers=headers, timeout=20)
        resp.raise_for_status()
        wb = pd.read_excel(BytesIO(resp.content), sheet_name="Monthly Prices", header=4)
        date_col = wb.columns[0]
        wb = wb[wb[date_col].astype(str).str.match(r"^\d{4}M\d{2}$", na=False)]
        wb = wb.replace("...", np.nan).replace("…", np.nan)
        for mineral, ev_use, benchmark in wb_specs:
            if mineral not in wb.columns:
                continue
            series = pd.to_numeric(wb[mineral], errors="coerce")
            valid = series.dropna()
            if valid.empty:
                continue
            idx = valid.index[-1]
            prev = valid.iloc[-2] if len(valid) > 1 else np.nan
            latest = valid.iloc[-1]
            change = ""
            if pd.notna(prev) and prev:
                change = f"{((latest / prev) - 1) * 100:+.1f}% vs prior month"
            rows.append({
                "Mineral": mineral,
                "Price": float(latest),
                "Unit": "USD/mt",
                "Latest_date": str(wb.loc[idx, date_col]),
                "Change": change,
                "Benchmark": benchmark,
                "EV_use": ev_use,
                "Source": "World Bank Pink Sheet",
                "URL": wb_url,
            })
    except Exception as exc:
        errors.append(f"World Bank Pink Sheet: {exc}")

    rows.append({
        "Mineral": "Graphite flake / anode material",
        "Price": np.nan,
        "Unit": "USD/tonne",
        "Latest_date": "public benchmark range",
        "Change": "",
        "Benchmark": "$540-860/t flake; $8,000-12,000/t synthetic graphite",
        "EV_use": "Battery anodes",
        "Source": "Public market range; live benchmarks are mostly paywalled",
        "URL": "https://criticalstrategicmetals.com/minerals/graphite/price/",
    })

    return pd.DataFrame(rows), errors


@st.cache_data(show_spinner=False)
def listed_company_universe():
    """Return listed financial peers used by the model, grouped by model tier."""
    rows = []
    seen = set()
    for tier, agents in FOUR_TIER_AGENT_GROUPS.items():
        for agent_id in agents:
            for company in AGENT_FINANCIAL_PEERS.get(agent_id, ()):
                ticker = AGENT_PEER_TICKERS.get(company, "")
                if not ticker:
                    continue
                key = (tier, agent_id, company, ticker)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "Tier": tier,
                    "Model agent": agent_id,
                    "Company": company,
                    "Ticker": ticker,
                })
    return pd.DataFrame(rows)


@st.cache_data(ttl=900, show_spinner=False)
def fetch_live_stock_prices(symbols):
    """Fetch delayed quote data from Yahoo Finance chart endpoints."""
    rows = []
    errors = []
    headers = {"User-Agent": "Mozilla/5.0 (EV supply-chain dashboard)"}
    for symbol in tuple(symbols):
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}?range=5d&interval=1d"
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            result = (resp.json().get("chart", {}).get("result") or [None])[0]
            if not result:
                raise ValueError("empty quote response")
            meta = result.get("meta", {})
            price = meta.get("regularMarketPrice")
            prev = meta.get("chartPreviousClose") or meta.get("previousClose")
            currency = meta.get("currency", "")
            change = np.nan
            change_pct = np.nan
            if price is not None and prev:
                change = float(price) - float(prev)
                change_pct = (float(price) / float(prev) - 1.0) * 100.0
            rows.append({
                "Ticker": symbol,
                "Price": float(price) if price is not None else np.nan,
                "Currency": currency,
                "Change": change,
                "Change_pct": change_pct,
                "Exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or meta.get("exchange", ""),
                "Market_state": meta.get("marketState", ""),
            })
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")
    return pd.DataFrame(rows), errors


# ══════════════════════════════════════════════════════════════════════════════
# HELPER — 4-tier ABM map HTML component
# ══════════════════════════════════════════════════════════════════════════════

def _make_sc_map_html() -> str:
    """Return a self-contained light-theme HTML showing every ABM agent by tier and archetype."""
    return """<!DOCTYPE html>
<html>
<head>
<meta charset='utf-8'>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #f8fafc; color: #0f172a;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 12px; padding: 14px;
  }
  .callout {
    background: #ffffff; border: 1px solid #93c5fd; border-radius: 8px;
    padding: 10px 14px; margin-bottom: 14px; color: #475569;
    font-size: 11px; line-height: 1.5;
  }
  .callout strong { color: #0f172a; }
  .sc-grid {
    display: grid;
    grid-template-columns: 1fr 22px 1fr 22px 1fr 22px 1fr;
    gap: 0 6px; align-items: start;
  }
  .tier-col { display: flex; flex-direction: column; gap: 5px; }
  .tier-hdr {
    font-size: 10.5px; font-weight: 700; text-align: center;
    padding: 5px 4px; border-radius: 6px; letter-spacing: 0.3px; margin-bottom: 3px;
  }
  .agent {
    background: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px;
    padding: 7px 8px; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
  }
  .ag-name { font-weight: 700; color: #0f172a; font-size: 11px; margin-bottom: 3px; line-height: 1.25; }
  .ag-badge {
    display: inline-block; font-size: 8.5px; font-weight: 700;
    padding: 1px 5px; border-radius: 3px; color: #fff; margin-bottom: 2px;
  }
  .ag-detail { color: #475569; font-size: 9.5px; line-height: 1.25; }
  .mkt-label { color: #334155; font-size: 9.5px; font-weight: 700;
    letter-spacing: 0.3px; margin-top: 8px; margin-bottom: 3px; }
  .mkt-item {
    background: #ffffff; border: 1px solid #cbd5e1; border-radius: 5px;
    padding: 5px 8px; color: #475569; font-size: 10px; margin-bottom: 3px;
  }
  .mkt-item span { color: #0f172a; font-weight: 700; }
  .arrow-col {
    display: flex; align-items: flex-start; justify-content: center;
    padding-top: 40px; font-size: 20px; color: #64748b;
  }
</style>
</head>
<body>
<div class='sc-grid'>

<!-- ── TIER 0 ── -->
<div class='tier-col'>
  <div class='tier-hdr' style='background:#f59e0b22;color:#f59e0b;border:1px solid #f59e0b44'>
    ⛏️ Tier 0 &mdash; Mineral Suppliers
  </div>
  <div class='agent'>
    <div class='ag-name'>Syrah Resources / Ganfeng Lithium</div>
    <span class='ag-badge' style='background:#ef4444'>StateBacked</span>
    <div class='ag-detail'>China &middot; 79% battery graphite</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>China Northern Rare Earth</div>
    <span class='ag-badge' style='background:#ef4444'>StateBacked</span>
    <div class='ag-detail'>China &middot; 85% NdPr processing</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>Glencore</div>
    <span class='ag-badge' style='background:#ef4444'>StateBacked</span>
    <div class='ag-detail'>State-aligned cobalt flows</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>STMicroelectronics / Infineon</div>
    <span class='ag-badge' style='background:#ef4444'>StateBacked</span>
    <div class='ag-detail'>China SiC wafer capacity</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>Pilbara Minerals / Mineral Resources / Albemarle</div>
    <span class='ag-badge' style='background:#f59e0b'>WesternMiner</span>
    <div class='ag-detail'>Pilbara spodumene &middot; 46%</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>SQM / Albemarle</div>
    <span class='ag-badge' style='background:#f59e0b'>WesternMiner</span>
    <div class='ag-detail'>Atacama brine &middot; 30%</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>Glencore / CMOC Group</div>
    <span class='ag-badge' style='background:#f59e0b'>WesternMiner</span>
    <div class='ag-detail'>DRC &middot; 70% global share</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>Coherent</div>
    <span class='ag-badge' style='background:#f59e0b'>WesternMiner</span>
    <div class='ag-detail'>Coherent Corp SiC boules</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>STMicroelectronics / Rohm / Infineon</div>
    <span class='ag-badge' style='background:#f59e0b'>WesternMiner</span>
    <div class='ag-detail'>STMicro / Infineon SiC</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>Albemarle / SQM</div>
    <span class='ag-badge' style='background:#f97316'>GreenfieldBuilder</span>
    <div class='ag-detail'>New entrant capacity</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>MP Materials / Lynas Rare Earths</div>
    <span class='ag-badge' style='background:#f97316'>GreenfieldBuilder</span>
    <div class='ag-detail'>Ex-China REE projects</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>Wolfspeed / Coherent</div>
    <span class='ag-badge' style='background:#f97316'>GreenfieldBuilder</span>
    <div class='ag-detail'>Wolfspeed &middot; debt-financed</div>
  </div>
</div>

<!-- ARROW 0→1 -->
<div class='arrow-col'>&#10132;</div>

<!-- ── TIER 1 ── -->
<div class='tier-col'>
  <div class='tier-hdr' style='background:#3b82f622;color:#3b82f6;border:1px solid #3b82f644'>
    🔋 Tier 1 &mdash; Cell Manufacturers
  </div>
  <div class='agent'>
    <div class='ag-name'>CATL</div>
    <span class='ag-badge' style='background:#3b82f6'>PlatformLeader</span>
    <div class='ag-detail'>37% global &middot; Ningde &middot; LFP+NMC</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>BYD</div>
    <span class='ag-badge' style='background:#3b82f6'>PlatformLeader</span>
    <div class='ag-detail'>14% global &middot; 90% LFP</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>CALB &#9733;</div>
    <span class='ag-badge' style='background:#06b6d4'>HyperScaleChallenger</span>
    <div class='ag-detail'>5% &middot; full-capacity push</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>EVE Energy / Gotion / Farasis / Sunwoda</div>
    <span class='ag-badge' style='background:#06b6d4'>HyperScaleChallenger</span>
    <div class='ag-detail'>12.8% &middot; SVOLT, REPT…</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>LG Energy Solution</div>
    <span class='ag-badge' style='background:#a855f7'>IncumbentUnderPressure</span>
    <div class='ag-detail'>13% &middot; NMC &middot; share eroding</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>Panasonic</div>
    <span class='ag-badge' style='background:#a855f7'>IncumbentUnderPressure</span>
    <div class='ag-detail'>7% &middot; NCA &middot; Tesla partner</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>Samsung SDI</div>
    <span class='ag-badge' style='background:#a855f7'>IncumbentUnderPressure</span>
    <div class='ag-detail'>6% &middot; NMC 811</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>SK On</div>
    <span class='ag-badge' style='background:#a855f7'>IncumbentUnderPressure</span>
    <div class='ag-detail'>5% &middot; NMC &middot; Ford/VW supply</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>AESC UK / Envision AESC &#9733;</div>
    <span class='ag-badge' style='background:#a855f7'>IncumbentUnderPressure</span>
    <div class='ag-detail'>0.2% &middot; Sunderland, UK only</div>
  </div>
</div>

<!-- ARROW 1→2 -->
<div class='arrow-col'>&#10132;</div>

<!-- ── TIER 2 ── -->
<div class='tier-col'>
  <div class='tier-hdr' style='background:#a855f722;color:#a855f7;border:1px solid #a855f744'>
    ⚙️ Tier 2 &mdash; Sub-system Suppliers
  </div>
  <div class='agent'>
    <div class='ag-name'>CATL / BYD / LG Energy Solution / Panasonic / Samsung SDI</div>
    <span class='ag-badge' style='background:#10b981'>BatteryPackIntegrator</span>
    <div class='ag-detail'>JIT &middot; order = demand exactly</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>BorgWarner / Infineon / ONsemi / STMicroelectronics</div>
    <span class='ag-badge' style='background:#6366f1'>PremiumPowerElectronics</span>
    <div class='ag-detail'>16-wk lead &middot; SiC-dependent</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>Nidec / BorgWarner / Denso / Magna</div>
    <span class='ag-badge' style='background:#14b8a6'>EstablishedVolumeSupplier</span>
    <div class='ag-detail'>12-wk lead &middot; REE NdFeB</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>Aptiv / Sumitomo Electric / TE Connectivity</div>
    <span class='ag-badge' style='background:#14b8a6'>EstablishedVolumeSupplier</span>
    <div class='ag-detail'>6-wk lead &middot; Ukraine exposure</div>
  </div>
</div>

<!-- ARROW 2→3 -->
<div class='arrow-col'>&#10132;</div>

<!-- ── TIER 3 + MARKETS ── -->
<div class='tier-col'>
  <div class='tier-hdr' style='background:#10b98122;color:#10b981;border:1px solid #10b98144'>
    🏭 Tier 3 &mdash; OEMs &amp; Markets
  </div>
  <div class='agent'>
    <div class='ag-name'>SAIC / Geely / NIO / Li Auto / XPeng / Leapmotor</div>
    <span class='ag-badge' style='background:#d97706'>EVNativeScaleAspirant</span>
    <div class='ag-detail'>6,825 k/yr &middot; NIO/Li Auto/Xpeng</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>BYD</div>
    <span class='ag-badge' style='background:#64748b'>base OEMAgent</span>
    <div class='ag-detail'>1,575 k/yr &middot; vertically int.</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>Tesla / General Motors / Ford / Rivian / Lucid</div>
    <span class='ag-badge' style='background:#f43f5e'>TransitioningLegacyOEM</span>
    <div class='ag-detail'>1,820 k/yr &middot; Tesla/GM/Ford</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>Volkswagen / BMW / Mercedes-Benz / Stellantis</div>
    <span class='ag-badge' style='background:#f43f5e'>TransitioningLegacyOEM</span>
    <div class='ag-detail'>1,505 k/yr &middot; VW/BMW/Mercedes</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>BMW MINI / Stellantis Vauxhall / Nissan Sunderland &#9733;</div>
    <span class='ag-badge' style='background:#f43f5e'>TransitioningLegacyOEM</span>
    <div class='ag-detail'>175 k/yr &middot; JLR/MINI/Vauxhall</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>Hyundai Motor / Kia</div>
    <span class='ag-badge' style='background:#059669'>ProfitableEstablishedOEM</span>
    <div class='ag-detail'>1,120 k/yr &middot; Hyundai/Kia</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>Toyota / Honda / Nissan</div>
    <span class='ag-badge' style='background:#059669'>ProfitableEstablishedOEM</span>
    <div class='ag-detail'>980 k/yr &middot; Toyota/Honda</div>
  </div>
  <div class='mkt-label'>DEMAND MARKETS (Tier 4)</div>
  <div class='mkt-item'><span>China</span> &nbsp;493 GWh &middot; 60%</div>
  <div class='mkt-item'><span>Europe</span> &nbsp;127 GWh &middot; 15%</div>
  <div class='mkt-item'><span>UK</span> &nbsp;20 GWh &middot; 2.4%</div>
  <div class='mkt-item'><span>USA</span> &nbsp;104 GWh &middot; 13%</div>
  <div class='mkt-item'><span>Japan</span> &nbsp;14 GWh &middot; 1.7%</div>
  <div class='mkt-item'><span>ROW</span> &nbsp;64 GWh &middot; 7.8%</div>
</div>

</div>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 6 — VALUE CHAIN MAP
# ═══════════════════════════════════════════════════════════════════════════════
with T_MAP:
    st.markdown(
        "The diagram below maps every **agent** in the 4-tier ABM model to its tier, "
        "archetype, and real-world counterpart. Arrows represent the Leontief "
        "input-output constraint: each tier can only produce as fast as its "
        "bottleneck input allows. The Sankey below shows annual production volumes "
        "from cell archetype groups through to end markets."
    )
    import streamlit.components.v1 as _components
    _components.html(_make_sc_map_html(), height=870, scrolling=True)

    st.divider()
    st.subheader("Cell-to-Market Flow — Annual Production Volumes")
    st.caption(
        "Sankey shows the downstream flow from cell archetype groups through battery packs "
        "to OEM clusters and end markets (mineral tier omitted to avoid multi-input topology issues)."
    )

    _sk_nodes = [
        "PlatformLeaders\n(CATL+BYD  51%)",
        "HyperScaleChallengers\n(CALB+Others  18%)",
        "Incumbents\n(LG/Pan/SDI/SK  31%)",
        "Battery Packs\n(822 GWh/yr)",
        "Chinese OEMs\n(8,400 k/yr)",
        "European OEMs\n(1,505 k/yr)",
        "US OEMs\n(1,820 k/yr)",
        "Korean+Japanese\n(2,100 k/yr)",
        "UK OEM\n(175 k/yr)",
        "China Market\n(493 GWh)",
        "Europe Market\n(127 GWh)",
        "USA Market\n(104 GWh)",
        "UK Market\n(20 GWh)",
        "Japan+ROW\n(78 GWh)",
    ]
    _sk_colors = [
        "#3b82f6", "#06b6d4", "#a855f7",
        "#10b981",
        "#d97706", "#f43f5e", "#f43f5e", "#059669", "#f43f5e",
        "rgba(239,68,68,0.40)", "rgba(59,130,246,0.40)",
        "rgba(59,130,246,0.40)", "rgba(6,182,212,0.40)",
        "rgba(148,163,184,0.40)",
    ]
    _sk_src = [0, 1, 2,  3, 3, 3, 3, 3,  4,  5,  6,  7,  8]
    _sk_tgt = [3, 3, 3,  4, 5, 6, 7, 8,  9, 10, 11, 12, 13]
    _sk_val = [419, 148, 255,  493, 80, 104, 135, 14,  493, 127, 104, 20, 78]
    _sk_link_col = [
        "rgba(59,130,246,0.20)", "rgba(6,182,212,0.20)", "rgba(168,85,247,0.20)",
        "rgba(217,119,6,0.20)", "rgba(244,63,94,0.20)", "rgba(244,63,94,0.20)",
        "rgba(5,150,105,0.20)", "rgba(244,63,94,0.20)", "rgba(239,68,68,0.13)",
        "rgba(59,130,246,0.13)", "rgba(59,130,246,0.13)", "rgba(6,182,212,0.13)",
        "rgba(148,163,184,0.13)",
    ]
    fig_sk = go.Figure(go.Sankey(
        arrangement="snap",
        node=dict(
            pad=14, thickness=18,
            line=dict(color="#2e3244", width=0.5),
            label=_sk_nodes, color=_sk_colors,
            hovertemplate="%{label}<extra></extra>",
        ),
        link=dict(
            source=_sk_src, target=_sk_tgt, value=_sk_val, color=_sk_link_col,
            hovertemplate="%{source.label} -> %{target.label}: %{value} GWh<extra></extra>",
        ),
    ))
    fig_sk.update_layout(
        template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        font=dict(color="#334155", size=10),
        height=430, margin=dict(l=20, r=20, t=30, b=20),
        title=dict(text="Cell-to-Market Flow  —  Annual Production Volumes",
                   font=dict(size=12, color="#0f172a")),
    )
    st.plotly_chart(fig_sk, width="stretch")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 7 — LIVE MARKET DATA
# ═══════════════════════════════════════════════════════════════════════════════
with T_MARKET:
    st.subheader("Critical EV Mineral Prices")
    st.caption(
        "Fetched from open commodity price sources and cached for 1 hour. "
        "Trading Economics values are delayed page benchmarks; World Bank Pink Sheet values are monthly nominal USD prices."
    )

    if st.button("Refresh mineral prices", key="mineral_price_refresh"):
        fetch_critical_mineral_prices.clear()
        st.rerun()

    try:
        _price_df, _price_errors = fetch_critical_mineral_prices()
    except Exception as _exc:
        _price_df = pd.DataFrame()
        _price_errors = [str(_exc)]

    if not _price_df.empty:
        _view = _price_df.copy()
        _view["Price_display"] = _view.apply(
            lambda r: (
                f"{r['Price']:,.0f} {r['Unit']}"
                if pd.notna(r["Price"])
                else r["Benchmark"]
            ),
            axis=1,
        )
        _cols = st.columns(4)
        for _ci, (_, _row) in enumerate(_view.head(4).iterrows()):
            with _cols[_ci]:
                st.metric(
                    _row["Mineral"],
                    _row["Price_display"],
                    _row["Change"] if _row["Change"] else None,
                )

        st.dataframe(
            _view[["Mineral", "Price_display", "Latest_date", "Benchmark", "EV_use", "Source", "URL"]]
            .rename(columns={
                "Price_display": "Price",
                "Latest_date": "Latest date",
                "EV_use": "EV use",
            }),
            width="stretch",
            hide_index=True,
            column_config={
                "URL": st.column_config.LinkColumn("URL", display_text="source"),
            },
        )

        _chart_df = _view[pd.notna(_view["Price"])].copy()
        if not _chart_df.empty:
            _chart_df["Label"] = _chart_df["Mineral"] + " (" + _chart_df["Unit"] + ")"
            _fig = go.Figure()
            _fig.add_trace(go.Bar(
                x=_chart_df["Label"],
                y=_chart_df["Price"],
                marker_color=["#3b82f6", "#f59e0b", "#10b981", "#a855f7", "#06b6d4", "#ef4444"][:len(_chart_df)],
                hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>",
            ))
            _layout = dict(PLOT_LAYOUT)
            _layout.update(
                title=dict(text="Latest Public Benchmark Prices", font=dict(size=12, color="#0f172a")),
                xaxis=dict(gridcolor="#e2e8f0", tickfont=dict(size=9)),
                yaxis=dict(gridcolor="#e2e8f0", tickfont=dict(size=9), title="Nominal price in source unit"),
                height=320,
            )
            _fig.update_layout(**_layout)
            st.plotly_chart(_fig, width="stretch")
    else:
        st.warning("Could not fetch mineral price data. Check network access or click Refresh.", icon="⚠️")

    if _price_errors:
        with st.expander("Unavailable price feeds"):
            st.write("\n".join(f"- {err}" for err in _price_errors))

    st.caption(
        "Sources: Trading Economics commodity pages; World Bank Commodity Price Data (Pink Sheet). "
        "Graphite spot benchmarks are included as an indicative public range because most live graphite feeds are paywalled."
    )
    st.divider()

    st.subheader("Live Stock Prices — Listed EV Supply-Chain Companies")
    st.caption(
        "Uses the same listed-company peer set as the model's financial calibration. "
        "Choose tiers first, then optionally narrow the company list."
    )
    _universe = listed_company_universe()
    if _universe.empty:
        st.warning("No listed-company peer mapping is available.")
    else:
        _tier_options = list(_universe["Tier"].drop_duplicates())
        _selected_tiers = st.multiselect(
            "Choose tiers",
            options=_tier_options,
            default=[],
            key="stock_price_tiers",
        )
        if not _selected_tiers:
            st.info("Choose one or more tiers to fetch live stock prices.")
        else:
            _tier_universe = _universe[_universe["Tier"].isin(_selected_tiers)].copy()
            _company_options = list(_tier_universe["Company"].drop_duplicates())
            _selected_companies = st.multiselect(
                "Choose listed companies",
                options=_company_options,
                default=[],
                key="stock_price_companies",
            )
            if not _selected_companies:
                st.info("Choose at least one listed company.")
            else:
                _stock_meta = (
                    _tier_universe[_tier_universe["Company"].isin(_selected_companies)]
                    .drop_duplicates(subset=["Company", "Ticker"])
                    .copy()
                )
                _quotes, _quote_errors = fetch_live_stock_prices(tuple(_stock_meta["Ticker"]))
                if not _quotes.empty:
                    _stock_view = _stock_meta.merge(_quotes, on="Ticker", how="left")
                    _stock_view["Price"] = _stock_view.apply(
                        lambda r: f"{r['Price']:,.2f} {r['Currency']}" if pd.notna(r["Price"]) else "n/a",
                        axis=1,
                    )
                    _stock_view["Daily change"] = _stock_view.apply(
                        lambda r: (
                            f"{r['Change']:+.2f} ({r['Change_pct']:+.1f}%)"
                            if pd.notna(r["Change"]) and pd.notna(r["Change_pct"])
                            else ""
                        ),
                        axis=1,
                    )
                    st.dataframe(
                        _stock_view[[
                            "Tier", "Model agent", "Company", "Ticker", "Price",
                            "Daily change", "Exchange", "Market_state",
                        ]].rename(columns={"Market_state": "Market state"}),
                        width="stretch",
                        hide_index=True,
                    )
                else:
                    st.warning("Could not fetch live stock prices for the selected companies.")
                if _quote_errors:
                    with st.expander("Unavailable stock quotes"):
                        st.write("\n".join(f"- {err}" for err in _quote_errors))
    st.divider()

    # ── Critical Mineral Supply ───────────────────────────────────────────────
    st.subheader("Critical Mineral Supply — USGS MCS 2024")
    _min_df = pd.DataFrame([
        dict(Mineral="Lithium",  EV_Use="Cathode (NMC/LFP/NCA)",     Top_Producer="Australia",
             Share="47%",  Second="Chile (27%)", Third="China (15%)",
             Global_Prod_2023="180 kt LCE", USGS_Criticality="Critical"),
        dict(Mineral="Cobalt",   EV_Use="NMC/NCA cathode",           Top_Producer="DR Congo",
             Share="70%",  Second="Russia (4%)", Third="Australia (3%)",
             Global_Prod_2023="190 kt",     USGS_Criticality="Critical"),
        dict(Mineral="Graphite", EV_Use="Anode (natural+synthetic)", Top_Producer="China",
             Share="79%",  Second="Mozambique (7%)", Third="Madagascar (5%)",
             Global_Prod_2023="1,300 kt",   USGS_Criticality="Critical"),
        dict(Mineral="REE",      EV_Use="NdFeB magnets (motors)",    Top_Producer="China",
             Share="70% mining", Second="USA (14%)", Third="Australia (8%)",
             Global_Prod_2023="300 kt REO", USGS_Criticality="Critical"),
        dict(Mineral="SiC",      EV_Use="Power inverter substrates", Top_Producer="China",
             Share="~60%", Second="USA / Wolfspeed", Third="EU / Coherent",
             Global_Prod_2023="~3.5 M wafers", USGS_Criticality="Emerging critical"),
        dict(Mineral="Copper",   EV_Use="Motor windings, HV wiring", Top_Producer="Chile",
             Share="27%",  Second="Peru (10%)", Third="DRC (8%)",
             Global_Prod_2023="22,000 kt",  USGS_Criticality="Watch list"),
    ])
    st.dataframe(_min_df.set_index("Mineral"), width="stretch")
    st.caption("Source: USGS Mineral Commodity Summaries 2024. SiC: Yole Développement 2023.")
    st.divider()

    # ── IEA Battery Demand ────────────────────────────────────────────────────
    st.subheader("EV Battery Demand by Region — IEA Global EV Outlook 2024")
    _iea_df = pd.DataFrame([
        dict(Region="China",   GWh_2022=370, GWh_2023=493, YoY="+33%", Share_2023="60%", Model_GWh=493),
        dict(Region="Europe",  GWh_2022=133, GWh_2023=127, YoY="-5%",  Share_2023="15%", Model_GWh=127),
        dict(Region="UK",      GWh_2022=17,  GWh_2023=20,  YoY="+18%", Share_2023="2%",  Model_GWh=20),
        dict(Region="USA",     GWh_2022=77,  GWh_2023=104, YoY="+35%", Share_2023="13%", Model_GWh=104),
        dict(Region="Japan",   GWh_2022=10,  GWh_2023=14,  YoY="+40%", Share_2023="2%",  Model_GWh=14),
        dict(Region="ROW",     GWh_2022=28,  GWh_2023=64,  YoY="+129%",Share_2023="8%",  Model_GWh=64),
        dict(Region="GLOBAL",  GWh_2022=635, GWh_2023=822, YoY="+29%", Share_2023="100%",Model_GWh=822),
    ])
    st.dataframe(_iea_df.set_index("Region"), width="stretch")
    st.caption("Source: IEA Global EV Outlook 2024, Annex B. Model growth calibrated to 29%/yr CAGR.")
    st.divider()

    # ── Model calibration context ─────────────────────────────────────────────
    st.subheader("Model Calibration vs Real-World Parameters")
    _calib_df = pd.DataFrame([
        dict(Parameter="Global cell demand (2023)",   Real_World="822 GWh/yr (IEA)",
             Model_Setting="822 GWh baseline",         Source="IEA GEO 2024"),
        dict(Parameter="Demand growth rate",          Real_World="~29%/yr CAGR",
             Model_Setting="0.56%/week compound",       Source="IEA GEO 2024"),
        dict(Parameter="CATL market share",           Real_World="37% (SNE Research 2023)",
             Model_Setting="catl agent: 37%",           Source="SNE Research 2023"),
        dict(Parameter="China graphite share",        Real_World="79% battery-grade",
             Model_Setting="graphite_chn: StateBacked", Source="USGS MCS 2024"),
        dict(Parameter="DRC cobalt share",            Real_World="70% mined",
             Model_Setting="cobalt_drc: WesternMiner",  Source="USGS MCS 2024"),
        dict(Parameter="UK OEM annual volume",        Real_World="175 k EVs (SMMT 2024)",
             Model_Setting="uk_oem: 175 k/yr",          Source="SMMT 2024"),
        dict(Parameter="Wiring harness safety stock", Real_World="~2 weeks (JIT)",
             Model_Setting="harness target: 2 wks",     Source="Industry estimate"),
        dict(Parameter="SiC inverter lead time",      Real_World="16-20 weeks",
             Model_Setting="inverter lead_time: 16 wks",Source="Yole Développement 2023"),
        dict(Parameter="LFP share (2023 baseline)",   Real_World="40.3% (IEA GEO 2024)",
             Model_Setting="lfp_share init: 0.403",     Source="IEA GEO 2024"),
    ])
    st.dataframe(_calib_df.set_index("Parameter"), width="stretch")


# ── Footer ────────────────────────────────────────────────────────────────────
st.divider()
st.markdown(
    "<p style='text-align:center;color:#64748b;font-size:0.75rem'>"
    "EV Supply Chain Intelligence · ABM + SD Model v3.0 · 13 agent archetypes · Queen's University Belfast · "
    "Data: USGS MCS 2024 · IEA GEO 2024 · BNEF 2023 · Company Annual Reports · "
    "<a href='https://github.com/zhangmin1006/ev-supply-chain' style='color:#3b82f6'>"
    "GitHub</a></p>",
    unsafe_allow_html=True,
)
