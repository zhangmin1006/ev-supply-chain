"""
EV Supply Chain Intelligence Dashboard — Streamlit app
=======================================================
Run locally:   streamlit run streamlit_app.py
Deploy:        Streamlit Community Cloud → https://share.streamlit.io
               Point to: zhangmin1006/ev-supply-chain, branch master, file streamlit_app.py
"""

import json
import os
import sys
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests

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
DATA_SCHEMA_VERSION = "uk-focus-v4"
REQUIRED_DATA_KEYS = {
    "focus_region",
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
    template="plotly_dark",
    paper_bgcolor="#1a1d27",
    plot_bgcolor="#1a1d27",
    font=dict(color="#94a3b8", size=11),
    margin=dict(l=50, r=20, t=36, b=40),
    legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor="#2e3244", borderwidth=1,
                font=dict(size=10)),
    xaxis=dict(gridcolor="#2e3244", tickfont=dict(size=9),
               tickvals=list(YEAR_TICKS.keys()),
               ticktext=list(YEAR_TICKS.values())),
    yaxis=dict(gridcolor="#2e3244", tickfont=dict(size=9)),
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
    fig.update_layout(**PLOT_LAYOUT, title=dict(text=title, font=dict(size=12, color="#e2e8f0")),
                      height=height, showlegend=show_legend)
    fig.update_xaxes(gridcolor="#2e3244", tickfont=dict(size=9),
                     tickvals=list(YEAR_TICKS.keys()), ticktext=list(YEAR_TICKS.values()))
    fig.update_yaxes(gridcolor="#2e3244", tickfont=dict(size=9))
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
  header[data-testid="stHeader"] {background: #0f1117; border-bottom: 1px solid #2e3244;}
  footer {visibility: hidden;}
  div[data-testid="stTabs"] button {font-size: 0.82rem; font-weight: 600;}
  /* metric cards */
  div[data-testid="metric-container"] {
    background: #20232f; border: 1px solid #2e3244; border-radius: 10px; padding: 12px 16px;
  }
  div[data-testid="stMetricValue"] {font-size: 1.7rem !important; font-weight: 700;}
  /* dataframe */
  .stDataFrame {border-radius: 8px; overflow: hidden;}
</style>
""", unsafe_allow_html=True)


# ── Load data ─────────────────────────────────────────────────────────────────
DATA = load_or_run()
BL   = DATA["baseline"]
SUMMARY = compute_summary(DATA)


# ── Header ────────────────────────────────────────────────────────────────────
st.markdown(
    "<h1 style='color:#e2e8f0;font-size:1.5rem;margin-bottom:2px'>"
    "⚡ UK EV Supply Chain <span style='color:#3b82f6'>Intelligence Dashboard</span></h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<p style='color:#94a3b8;font-size:0.82rem;margin-bottom:16px'>"
    "Hybrid Agent-Based + System Dynamics model &nbsp;·&nbsp; UK OEM focus &nbsp;·&nbsp; "
    "13 agent archetypes &nbsp;·&nbsp; 9 cell makers &nbsp;·&nbsp; 7 tracked materials &nbsp;·&nbsp; "
    "10 shock scenarios &nbsp;·&nbsp; 260-week horizon &nbsp;|&nbsp; Queen's University Belfast</p>",
    unsafe_allow_html=True,
)

# ── Tabs ──────────────────────────────────────────────────────────────────────
T_OVERVIEW, T_SCENARIO, T_OEM, T_STOCKS, T_ARCHETYPES, T_MAP, T_MARKET, T_FOCUS = st.tabs([
    "📊 Overview",
    "🔍 Scenario Analysis",
    "🏭 UK OEM",
    "⛏️ Supply Chain Stocks",
    "🤖 Agent Archetypes",
    "🔗 Value Chain",
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
        "Select scenarios to compare:",
        options=SHOCK_SCS,
        default=SHOCK_SCS,
        format_func=lambda s: SC_LABELS[s],
        key="ov_select",
    )

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
        "Select scenario for deep-dive analysis:",
        options=SHOCK_SCS,
        format_func=lambda s: f"{SC_LABELS[s]} — {SC_DESC[s]}",
        key="sc_sel",
    )
    col = SC_COLOURS[sc_sel]
    d = DATA[sc_sel]
    bl_prod = np.array(BL["oem_production_k"])
    bl_mean = bl_prod.mean()

    st.markdown(
        f"<div style='background:#20232f;border:1px solid {col};border-radius:8px;"
        f"padding:10px 16px;margin-bottom:16px'>"
        f"<span style='color:{col};font-weight:700'>{SC_LABELS[sc_sel]}</span>"
        f"<span style='color:#94a3b8;font-size:0.82rem'> — {SC_DESC[sc_sel]}</span></div>",
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
# TAB 3 — OEM BREAKDOWN
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
            f"<div style='color:#e2e8f0;font-size:0.95rem;font-weight:600;margin:4px 0'>{count} archetypes</div>"
            f"<div style='color:#94a3b8;font-size:0.78rem'>{names}</div></div>",
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
                         gridcolor="#2e3244", tickfont=dict(size=9))
        fig.update_yaxes(title_text="Signal index", row=2, col=1,
                         gridcolor="#2e3244", tickfont=dict(size=9))
        fig.update_xaxes(gridcolor="#2e3244", tickfont=dict(size=9),
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
                         gridcolor="#2e3244", tickfont=dict(size=9))
        fig.update_yaxes(title_text="GWh/week", row=2, col=1,
                         gridcolor="#2e3244", tickfont=dict(size=9))
        fig.update_xaxes(gridcolor="#2e3244", tickfont=dict(size=9),
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
            "<p style='color:#94a3b8;font-size:0.8rem'>JLR (Tata Motors), BMW MINI Oxford, "
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
            "<p style='color:#94a3b8;font-size:0.8rem'>The UK endpoint remains exposed to "
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


# ══════════════════════════════════════════════════════════════════════════════
# HELPER — 4-tier ABM map HTML component
# ══════════════════════════════════════════════════════════════════════════════

def _make_sc_map_html() -> str:
    """Return a self-contained dark-theme HTML showing every ABM agent by tier and archetype."""
    return """<!DOCTYPE html>
<html>
<head>
<meta charset='utf-8'>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #0f1117; color: #e2e8f0;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 12px; padding: 14px;
  }
  .callout {
    background: #20232f; border: 1px solid #3b82f6; border-radius: 8px;
    padding: 10px 14px; margin-bottom: 14px; color: #94a3b8;
    font-size: 11px; line-height: 1.5;
  }
  .callout strong { color: #e2e8f0; }
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
    background: #20232f; border: 1px solid #2e3244; border-radius: 6px;
    padding: 6px 8px;
  }
  .ag-name { font-weight: 600; color: #e2e8f0; font-size: 11px; margin-bottom: 2px; }
  .ag-badge {
    display: inline-block; font-size: 8.5px; font-weight: 700;
    padding: 1px 5px; border-radius: 3px; color: #fff; margin-bottom: 2px;
  }
  .ag-detail { color: #64748b; font-size: 9.5px; }
  .mkt-label { color: #64748b; font-size: 9.5px; font-weight: 700;
    letter-spacing: 0.3px; margin-top: 8px; margin-bottom: 3px; }
  .mkt-item {
    background: #1a1d27; border: 1px solid #2e3244; border-radius: 5px;
    padding: 5px 8px; color: #94a3b8; font-size: 10px; margin-bottom: 3px;
  }
  .mkt-item span { color: #e2e8f0; font-weight: 600; }
  .arrow-col {
    display: flex; align-items: flex-start; justify-content: center;
    padding-top: 40px; font-size: 20px; color: #2e3244;
  }
</style>
</head>
<body>
<div class='callout'>
  <strong>ABM Model — 4-Tier Agent Architecture</strong> &mdash;
  Every agent in the simulation is shown below with its archetype badge and real-world anchor.
  The model abstracts the full industry value chain into 4 tiers linked by Leontief
  input-output constraints. For the complete 6-tier industry value chain (raw mining &rarr;
  chemicals &rarr; cells &rarr; sub-systems &rarr; OEMs &rarr; markets), open
  <strong>index.html &rsaquo; Value Chain tab</strong> in the repository root.
</div>

<div class='sc-grid'>

<!-- ── TIER 0 ── -->
<div class='tier-col'>
  <div class='tier-hdr' style='background:#f59e0b22;color:#f59e0b;border:1px solid #f59e0b44'>
    ⛏️ Tier 0 &mdash; Mineral Suppliers
  </div>
  <div class='agent'>
    <div class='ag-name'>graphite_chn</div>
    <span class='ag-badge' style='background:#ef4444'>StateBacked</span>
    <div class='ag-detail'>China &middot; 79% battery graphite</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>ree_chn</div>
    <span class='ag-badge' style='background:#ef4444'>StateBacked</span>
    <div class='ag-detail'>China &middot; 85% NdPr processing</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>cobalt_other</div>
    <span class='ag-badge' style='background:#ef4444'>StateBacked</span>
    <div class='ag-detail'>State-aligned cobalt flows</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>sic_china</div>
    <span class='ag-badge' style='background:#ef4444'>StateBacked</span>
    <div class='ag-detail'>China SiC wafer capacity</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>lithium_aus</div>
    <span class='ag-badge' style='background:#f59e0b'>WesternMiner</span>
    <div class='ag-detail'>Pilbara spodumene &middot; 46%</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>lithium_chl</div>
    <span class='ag-badge' style='background:#f59e0b'>WesternMiner</span>
    <div class='ag-detail'>Atacama brine &middot; 30%</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>cobalt_drc</div>
    <span class='ag-badge' style='background:#f59e0b'>WesternMiner</span>
    <div class='ag-detail'>DRC &middot; 70% global share</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>sic_coherent</div>
    <span class='ag-badge' style='background:#f59e0b'>WesternMiner</span>
    <div class='ag-detail'>Coherent Corp SiC boules</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>sic_other</div>
    <span class='ag-badge' style='background:#f59e0b'>WesternMiner</span>
    <div class='ag-detail'>STMicro / Infineon SiC</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>lithium_other</div>
    <span class='ag-badge' style='background:#f97316'>GreenfieldBuilder</span>
    <div class='ag-detail'>New entrant capacity</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>ree_other</div>
    <span class='ag-badge' style='background:#f97316'>GreenfieldBuilder</span>
    <div class='ag-detail'>Ex-China REE projects</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>sic_wolfspeed</div>
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
    <div class='ag-name'>catl</div>
    <span class='ag-badge' style='background:#3b82f6'>PlatformLeader</span>
    <div class='ag-detail'>37% global &middot; Ningde &middot; LFP+NMC</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>byd_cells</div>
    <span class='ag-badge' style='background:#3b82f6'>PlatformLeader</span>
    <div class='ag-detail'>14% global &middot; 90% LFP</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>calb &#9733;</div>
    <span class='ag-badge' style='background:#06b6d4'>HyperScaleChallenger</span>
    <div class='ag-detail'>5% &middot; full-capacity push</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>others_cells</div>
    <span class='ag-badge' style='background:#06b6d4'>HyperScaleChallenger</span>
    <div class='ag-detail'>12.8% &middot; SVOLT, REPT…</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>lg_es</div>
    <span class='ag-badge' style='background:#a855f7'>IncumbentUnderPressure</span>
    <div class='ag-detail'>13% &middot; NMC &middot; share eroding</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>panasonic</div>
    <span class='ag-badge' style='background:#a855f7'>IncumbentUnderPressure</span>
    <div class='ag-detail'>7% &middot; NCA &middot; Tesla partner</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>samsung_sdi</div>
    <span class='ag-badge' style='background:#a855f7'>IncumbentUnderPressure</span>
    <div class='ag-detail'>6% &middot; NMC 811</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>sk_on</div>
    <span class='ag-badge' style='background:#a855f7'>IncumbentUnderPressure</span>
    <div class='ag-detail'>5% &middot; NMC &middot; Ford/VW supply</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>aesc_uk &#9733;</div>
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
    <div class='ag-name'>battery_pack</div>
    <span class='ag-badge' style='background:#10b981'>BatteryPackIntegrator</span>
    <div class='ag-detail'>JIT &middot; order = demand exactly</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>inverter</div>
    <span class='ag-badge' style='background:#6366f1'>PremiumPowerElectronics</span>
    <div class='ag-detail'>16-wk lead &middot; SiC-dependent</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>motor</div>
    <span class='ag-badge' style='background:#14b8a6'>EstablishedVolumeSupplier</span>
    <div class='ag-detail'>12-wk lead &middot; REE NdFeB</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>harness</div>
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
    <div class='ag-name'>other_chinese_oem</div>
    <span class='ag-badge' style='background:#d97706'>EVNativeScaleAspirant</span>
    <div class='ag-detail'>6,825 k/yr &middot; NIO/Li Auto/Xpeng</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>byd_oem</div>
    <span class='ag-badge' style='background:#64748b'>base OEMAgent</span>
    <div class='ag-detail'>1,575 k/yr &middot; vertically int.</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>us_oem</div>
    <span class='ag-badge' style='background:#f43f5e'>TransitioningLegacyOEM</span>
    <div class='ag-detail'>1,820 k/yr &middot; Tesla/GM/Ford</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>german_oem</div>
    <span class='ag-badge' style='background:#f43f5e'>TransitioningLegacyOEM</span>
    <div class='ag-detail'>1,505 k/yr &middot; VW/BMW/Mercedes</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>uk_oem &#9733;</div>
    <span class='ag-badge' style='background:#f43f5e'>TransitioningLegacyOEM</span>
    <div class='ag-detail'>175 k/yr &middot; JLR/MINI/Vauxhall</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>korean_oem</div>
    <span class='ag-badge' style='background:#059669'>ProfitableEstablishedOEM</span>
    <div class='ag-detail'>1,120 k/yr &middot; Hyundai/Kia</div>
  </div>
  <div class='agent'>
    <div class='ag-name'>japanese_oem</div>
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
    st.info(
        "**Full 6-tier Industry Value Chain:** For refined chemicals, cell components "
        "(anode / cathode / electrolyte), power electronics sub-tiers, and all "
        "component HS codes and standards, open **index.html** in the repository root "
        "and navigate to the **Value Chain** tab.",
        icon="🔗",
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
        template="plotly_dark", paper_bgcolor="#1a1d27", plot_bgcolor="#1a1d27",
        font=dict(color="#e2e8f0", size=10),
        height=430, margin=dict(l=20, r=20, t=30, b=20),
        title=dict(text="Cell-to-Market Flow  —  Annual Production Volumes",
                   font=dict(size=12, color="#e2e8f0")),
    )
    st.plotly_chart(fig_sk, width="stretch")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 7 — LIVE MARKET DATA
# ═══════════════════════════════════════════════════════════════════════════════
with T_MARKET:
    st.subheader("Live Manufacturing & Economic Indicators")
    st.caption(
        "Fetched live from the World Bank Open Data API (cached 1 hour). "
        "Most-recent value within the last 3 reported years shown."
    )

    if st.button("Refresh live data", key="wb_refresh"):
        fetch_world_bank_data.clear()
        st.rerun()

    try:
        _wb    = fetch_world_bank_data()
        _wb_ok = True
    except Exception as _exc:
        _wb_ok     = False
        _wb_errmsg = str(_exc)

    if _wb_ok:
        _countries = _wb["countries"]
        _wb_data   = _wb["data"]
        _MFG  = "NV.IND.MANF.ZS"
        _TECH = "TX.VAL.TECH.MF.ZS"
        _GDP  = "NY.GDP.MKTP.CD"

        def _wbfmt(rec, ind, suffix="", scale=1.0, dec=1):
            entry = rec.get(ind)
            if not entry or entry.get("value") is None:
                return "—", ""
            return f"{entry['value'] * scale:,.{dec}f}{suffix}", entry.get("year", "")

        for _row_slice in (_countries[:5], _countries[5:]):
            _cols = st.columns(5)
            for _ci, _c in enumerate(_row_slice):
                _iso  = _c["iso"]
                _cr   = _wb_data.get(_iso, {})
                _mfg, _mfg_yr   = _wbfmt(_cr, _MFG,  suffix="%")
                _tech, _tech_yr = _wbfmt(_cr, _TECH, suffix="%")
                _gdp, _gdp_yr   = _wbfmt(_cr, _GDP,  suffix=" T", scale=1e-12, dec=2)
                with _cols[_ci]:
                    st.markdown(
                        f"<div style='background:#20232f;border:1px solid #2e3244;"
                        f"border-radius:10px;padding:12px 14px;margin-bottom:6px'>"
                        f"<div style='font-size:1.4rem'>{_c['flag']}</div>"
                        f"<div style='color:#e2e8f0;font-weight:700;font-size:.9rem'>{_c['name']}</div>"
                        f"<div style='color:#64748b;font-size:.72rem;margin-bottom:8px'>{_c['role']}</div>"
                        f"<div style='color:#94a3b8;font-size:.78rem'>Mfg % GDP</div>"
                        f"<div style='color:#3b82f6;font-weight:700;font-size:1rem'>{_mfg}"
                        f"<span style='color:#64748b;font-size:.68rem'> {_mfg_yr}</span></div>"
                        f"<div style='color:#94a3b8;font-size:.78rem;margin-top:4px'>Hi-tech exports</div>"
                        f"<div style='color:#10b981;font-weight:700;font-size:1rem'>{_tech}"
                        f"<span style='color:#64748b;font-size:.68rem'> {_tech_yr}</span></div>"
                        f"<div style='color:#94a3b8;font-size:.78rem;margin-top:4px'>GDP</div>"
                        f"<div style='color:#f59e0b;font-weight:700;font-size:1rem'>${_gdp}"
                        f"<span style='color:#64748b;font-size:.68rem'> {_gdp_yr}</span></div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
    else:
        st.warning(f"Could not fetch World Bank data ({_wb_errmsg}). "
                   "Check network access or click Refresh.", icon="⚠️")

    st.caption(
        "Source: World Bank Open Data API · NV.IND.MANF.ZS · TX.VAL.TECH.MF.ZS · "
        "NY.GDP.MKTP.CD · License: CC BY 4.0"
    )
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
