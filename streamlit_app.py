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
DATA_SCHEMA_VERSION = "uk-focus-v3"
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
    "9 cell makers &nbsp;·&nbsp; 6 tracked materials &nbsp;·&nbsp; 10 shock scenarios &nbsp;·&nbsp; "
    "260-week horizon &nbsp;|&nbsp; Queen's University Belfast</p>",
    unsafe_allow_html=True,
)

# ── Tabs ──────────────────────────────────────────────────────────────────────
T_OVERVIEW, T_SCENARIO, T_OEM, T_STOCKS, T_FOCUS = st.tabs([
    "📊 Overview",
    "🔍 Scenario Analysis",
    "🏭 UK OEM",
    "⛏️ Supply Chain Stocks",
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
        st.metric("UK OEM Volume", "175 k/yr",
                  delta="JLR · MINI · Vauxhall")
    with c6:
        st.metric("Domestic Cell Share", "~0.2%",
                  delta="AESC Sunderland")

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
# TAB 5 — UK & CHINA FOCUS
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


# ── Footer ────────────────────────────────────────────────────────────────────
st.divider()
st.markdown(
    "<p style='text-align:center;color:#64748b;font-size:0.75rem'>"
    "EV Supply Chain Intelligence · ABM + SD Model v2.0 · Queen's University Belfast · "
    "Data: USGS MCS 2024 · IEA GEO 2024 · BNEF 2023 · Company Annual Reports · "
    "<a href='https://github.com/zhangmin1006/ev-supply-chain' style='color:#3b82f6'>"
    "GitHub</a></p>",
    unsafe_allow_html=True,
)
