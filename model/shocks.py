"""
Shock Scenario Library
======================
Pre-defined supply shock scenarios, each calibrated to a documented
historical event or plausible risk based on published data.

Scenario format
---------------
Each scenario is a dict:
  {
    "name":        str          — scenario identifier
    "description": str          — human-readable label
    "shocks": [
      {
        "target":     str       — agent_id to shock (see _MINERAL_SOURCES in hybrid_model)
        "start_week": int       — week shock begins (0-indexed)
        "end_week":   int       — week shock resolves (recovery starts)
        "severity":   float     — fraction of output lost [0, 1]
      },
      ...
    ]
  }

Target agent IDs
----------------
  Mineral suppliers : cobalt_drc, cobalt_other, graphite_chn, ree_chn,
                      sic_wolfspeed, sic_coherent, sic_china, lithium_aus, ...
  Cell manufacturers: cell_catl, cell_byd_cells, cell_lg_es, ...
  Tier-1 suppliers  : t1_battery_pack, t1_inverter, t1_motor, t1_harness
  OEMs              : oem_chinese_oem, oem_us_oem, oem_german_oem, oem_korean_oem
"""

from __future__ import annotations
from typing import Dict, Any


# ── Individual shock scenarios ────────────────────────────────────────────────

SCENARIOS: Dict[str, Dict[str, Any]] = {

    # ------------------------------------------------------------------
    # BASELINE — no shocks; pure demand-growth run
    # ------------------------------------------------------------------
    "baseline": {
        "name":        "baseline",
        "description": "No supply shocks — baseline demand-growth trajectory",
        "shocks":      [],
    },

    # ------------------------------------------------------------------
    # S1: Ukraine wiring harness disruption
    # Calibration: Leoni AG disruption disclosure Feb 2022.
    #   - Leoni Ukraine plants shut within 72 hrs of invasion.
    #   - BMW, VW, Porsche, Audi production halted within 1 week.
    #   - Leoni Ukraine revenue ~€800M/yr = ~30% of EU harness supply.
    #   - Full recovery took ~6 months (phased relocation to Morocco/Romania).
    # Model target: t1_harness (aggregate Tier-1 harness supplier)
    # Severity: 0.80 for 8 weeks (acute phase), then 40% shortfall for 16 wks
    # ------------------------------------------------------------------
    "ukraine_harness": {
        "name":        "ukraine_harness",
        "description": "Ukraine conflict → Leoni/Fujikura plants disrupted "
                       "(calibrated to Feb 2022 event)",
        "shocks": [
            # Acute phase: week 4 (month 1 of simulation)
            {
                "target":     "t1_harness",
                "start_week":  4,
                "end_week":   12,
                "severity":   0.80,   # −80% EU harness supply (Leoni AR)
            },
            # Partial recovery: relocated production ramps slowly
            {
                "target":     "t1_harness",
                "start_week": 12,
                "end_week":   36,
                "severity":   0.35,   # −35% lingering shortfall
            },
        ],
    },

    # ------------------------------------------------------------------
    # S2: DRC cobalt supply disruption
    # Calibration: IEA Critical Minerals Market Review 2023, scenario analysis.
    #   - DRC political instability scenarios reduce cobalt export by 30–70%.
    #   - LFP manufacturers (CATL BYD) immune; NMC makers (LG ES, Panasonic) hit.
    #   - Modelled as 50% reduction in DRC cobalt supply for 26 weeks.
    #   - Recovery as artisanal/small-scale mines reopen gradually.
    # Model target: cobalt_drc (70% of global cobalt)
    # ------------------------------------------------------------------
    "drc_cobalt": {
        "name":        "drc_cobalt",
        "description": "DRC political disruption → 50% cobalt supply loss for 6 months "
                       "(IEA CMMR 2023 scenario)",
        "shocks": [
            {
                "target":     "cobalt_drc",
                "start_week": 26,   # shock in second half of year 1
                "end_week":   52,
                "severity":   0.50,
            },
        ],
    },

    # ------------------------------------------------------------------
    # S3: SiC wafer supply bottleneck
    # Calibration: 2022–2024 SiC capacity crunch; OEM direct supply agreements.
    #   - Wolfspeed, Coherent total capacity ~50% of demand in 2022.
    #   - Lead times extended to 52 weeks; Tesla, Renault locked supply via LTAs.
    #   - Modelled as sustained 30% shortfall at Wolfspeed + Coherent for 52 wks.
    # Source: Wolfspeed 10-K 2023; STMicroelectronics investor day 2023.
    # ------------------------------------------------------------------
    "sic_bottleneck": {
        "name":        "sic_bottleneck",
        "description": "SiC wafer capacity crunch (Wolfspeed + Coherent constrained) "
                       "— calibrated to 2022–24 shortage",
        "shocks": [
            {
                "target":     "sic_wolfspeed",
                "start_week": 13,   # beginning of Q2 year 1
                "end_week":   65,   # 52-week sustained shortage
                "severity":   0.35,
            },
            {
                "target":     "sic_coherent",
                "start_week": 13,
                "end_week":   65,
                "severity":   0.30,
            },
        ],
    },

    # ------------------------------------------------------------------
    # S4: China rare-earth export restriction
    # Calibration: Analogous to gallium/germanium controls (Aug 2023).
    #   - China controls ~85–90% of REE processing (USGS MCS 2024).
    #   - A 40% export quota on NdFeB magnets/precursors = estimated impact.
    #   - Recovery slow: Western processing capacity takes 2–3 yrs to build.
    # Source: MOFCOM export control notices 2023; USGS MCS 2024.
    # ------------------------------------------------------------------
    "china_ree_restriction": {
        "name":        "china_ree_restriction",
        "description": "China imposes REE/NdFeB magnet export quotas "
                       "(analogous to Ga/Ge controls, MOFCOM 2023)",
        "shocks": [
            {
                "target":     "ree_chn",
                "start_week": 52,    # beginning of year 2
                "end_week":   156,   # 2-year restriction (104 weeks)
                "severity":   0.40,
            },
        ],
    },

    # ------------------------------------------------------------------
    # S5: Compound shock — DRC cobalt + Ukraine harness simultaneously
    # Stress-test scenario: tests compounding multi-tier disruption.
    # Both shocks hit at the same time, representing correlated geopolitical risk.
    # ------------------------------------------------------------------
    "compound_shock": {
        "name":        "compound_shock",
        "description": "Compound: DRC cobalt + Ukraine harness simultaneously "
                       "(correlated geopolitical stress test)",
        "shocks": [
            {
                "target":     "cobalt_drc",
                "start_week":  4,
                "end_week":   30,
                "severity":   0.50,
            },
            {
                "target":     "t1_harness",
                "start_week":  4,
                "end_week":   12,
                "severity":   0.80,
            },
            {
                "target":     "t1_harness",
                "start_week": 12,
                "end_week":   36,
                "severity":   0.35,
            },
        ],
    },

    # ------------------------------------------------------------------
    # S7: US-China tariff escalation + Chinese retaliation
    # Calibration:
    #   - US IRA (2022) + Biden 100% tariff on Chinese EVs (May 2024)
    #   - EU: 17–38% countervailing duties on Chinese EVs (Oct 2024)
    #   - China retaliation: REE/graphite export controls (analogous to
    #     gallium/germanium Aug 2023; graphite Oct 2023)
    #   - CATL/BYD cells excluded from US market → US OEM scrambles
    #     for alternative supply; modelled as partial cell shortage
    # Sources: USTR Section 301 review 2024; European Commission CVD
    #   investigation 2024; MOFCOM export control notices.
    # ------------------------------------------------------------------
    "us_china_tariff": {
        "name":        "us_china_tariff",
        "description": "US 100% EV tariff + EU CVD duties → Chinese cell makers "
                       "lose Western market; China retaliates with graphite/REE controls",
        "shocks": [
            # US OEM loses access to CATL cells (tariff makes them uneconomic)
            # Modelled as 60% reduction in CATL output available to US market
            # (CATL can redirect to China/ROW but US OEM must find alternatives)
            {
                "target":     "cell_catl",
                "start_week": 26,
                "end_week":   260,
                "severity":   0.30,   # industry-wide CATL capacity reduction
            },
            # Chinese graphite export controls (retaliation; Oct 2023 baseline)
            {
                "target":     "graphite_chn",
                "start_week": 26,
                "end_week":   130,
                "severity":   0.35,
            },
            # REE export quota (escalation; analogous to Ga/Ge controls)
            {
                "target":     "ree_chn",
                "start_week": 52,
                "end_week":   260,
                "severity":   0.30,
            },
        ],
    },

    # ------------------------------------------------------------------
    # S8: UK post-Brexit supply chain friction
    # Calibration:
    #   - UK Automotive Council / SMMT 2023: post-Brexit border friction adds
    #     ~1.8% to component costs and 6–12 hours to cross-channel logistics.
    #   - Rules-of-origin (RoO) threshold rises: 40% UK/EU content (2024) →
    #     45% (2026) → 55% (2027).  UK OEMs sourcing Korean cells may face 10%
    #     tariffs (MFN rate) on finished vehicles exported to the EU if RoO
    #     threshold not met — forcing costly supply chain re-routing.
    #   - JLR disclosed "significant" Brexit compliance costs in FY2023 AR.
    #   - Harness: ~30% of EU harness already Ukraine-exposed; UK adds border
    #     delay risk on top (Dover–Calais friction especially under strike action).
    #   - Modelled as 10% OEM throughput reduction (friction, rerouting, customs
    #     clearance overhead) for 52 weeks, fading to 5% in yr 2 as firms adapt.
    #   - Additional 8% harness delivery delay for 26 weeks (border congestion).
    # Sources: SMMT Post-Brexit Impact Assessment 2023; JLR AR 2022/23;
    #          UK-EU TCA rules of origin schedule (OJ L 444/1, Dec 2020);
    #          Warwick Manufacturing Group supply chain analysis 2023.
    # ------------------------------------------------------------------
    "uk_supply_chain_friction": {
        "name":        "uk_supply_chain_friction",
        "description": "Post-Brexit RoO friction + border delays reduce UK OEM "
                       "throughput (JLR, MINI, Vauxhall); harness delivery impact",
        "shocks": [
            # Yr 1: full friction — customs overhead, RoO compliance cost, rerouting
            {
                "target":     "oem_uk_oem",
                "start_week":  4,
                "end_week":   56,
                "severity":   0.10,   # −10% throughput (SMMT cost-impact estimate)
            },
            # Yr 2: partial adaptation — new routing established, some RoO compliance
            {
                "target":     "oem_uk_oem",
                "start_week": 56,
                "end_week":  108,
                "severity":   0.05,   # −5% residual friction
            },
            # Cross-channel harness delay (Dover–Calais congestion, JIT disruption)
            {
                "target":     "t1_harness",
                "start_week":  4,
                "end_week":   30,
                "severity":   0.08,   # −8% harness delivery rate (border dwell time)
            },
        ],
    },

    # ------------------------------------------------------------------
    # S9: CATL production disruption — single-supplier concentration risk
    # Calibration:
    #   - CATL holds 37% global cell market share (IEA GEO 2024); concentration
    #     in Fujian/Ningde province creates single-geography risk.
    #   - Analogous event: partial disruption of CATL Yibin base (2022 Sichuan
    #     power rationing cut output ~15% for 3 weeks).
    #   - Stress-test scenario: major disruption (fire, flood, regulatory action)
    #     at primary Ningde / Yibin cluster → 45% CATL output loss for 12 months.
    #   - Model note: cells flow through a shared pool (each maker's market_share
    #     fraction of total demand), so all OEMs receive proportional impact.
    #     OEM-specific cell_sources differentiation requires a future per-OEM
    #     routing extension.  This scenario quantifies the aggregate global cost
    #     of 37% cell supply concentration in a single maker.
    #   - Theoretical differential (per cell_sources metadata):
    #       byd_oem: 5% CATL-exposed → near-immune in extended model
    #       other_chinese_oem: 55% CATL-exposed → most vulnerable
    #       us_oem: 30% CATL-exposed → significant
    #       german_oem / uk_oem: 10% CATL-exposed → mild
    # Sources: CATL 2023 Annual Report (plant locations); BNEF Concentration Risk
    #          Report 2023; Sichuan power-rationing disclosure Aug 2022.
    # ------------------------------------------------------------------
    "china_catl_disruption": {
        "name":        "china_catl_disruption",
        "description": "Major CATL plant disruption (Ningde cluster, 45% loss, 15 months) "
                       "— quantifies 37% single-supplier cell concentration risk globally",
        "shocks": [
            {
                "target":     "cell_catl",
                "start_week": 13,    # Q2 yr 1 (sudden event)
                "end_week":   78,    # 65-week disruption (~15 months; slow rebuild)
                "severity":   0.45,  # −45% CATL output (major cluster disruption)
            },
        ],
    },

    # ------------------------------------------------------------------
    # S6: China graphite export restriction
    # China controls 79% of natural graphite production (USGS MCS 2024).
    # Export restrictions announced Oct 2023 (permit requirement).
    # ------------------------------------------------------------------
    "china_graphite": {
        "name":        "china_graphite",
        "description": "China graphite export restrictions "
                       "(Oct 2023 permit regime — extended stress scenario)",
        "shocks": [
            {
                "target":     "graphite_chn",
                "start_week": 8,
                "end_week":   60,
                "severity":   0.45,
            },
        ],
    },
}


def get_scenario(name: str) -> Dict[str, Any]:
    """Return a scenario dict by name; raises KeyError if not found."""
    if name not in SCENARIOS:
        available = ", ".join(SCENARIOS.keys())
        raise KeyError(f"Unknown scenario '{name}'. Available: {available}")
    return SCENARIOS[name]


def list_scenarios() -> None:
    """Print a summary of all available scenarios."""
    print(f"{'ID':<25}  {'Description'}")
    print("-" * 80)
    for sid, sc in SCENARIOS.items():
        print(f"  {sid:<23}  {sc['description']}")
