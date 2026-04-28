"""
Model Parameters — EV Supply Chain ABM+SD
==========================================
All values sourced from publicly available data as cited.
Units are documented inline.

Primary sources
---------------
  USGS  — Mineral Commodity Summaries 2024 (MCS 2024)
  IEA   — Global EV Outlook 2024 (GEO 2024)
  IEA   — Critical Minerals Market Review 2023 (CMMR 2023)
  BNEF  — BloombergNEF Battery Price Survey 2023
  Yole  — Power Electronics Market Report 2023
  Co.AR — Company Annual Reports (CATL, LG ES, Panasonic, Infineon, Wolfspeed)
"""

# ── Simulation control ────────────────────────────────────────────────────────
SIM = {
    "dt":        1,       # time step (weeks)
    "n_weeks":   260,     # horizon: 5 years = 52 × 5
    "seed":      42,
    "warm_up":   4,       # weeks before metrics are recorded (stock stabilisation)
}

# ── Critical mineral supply  (USGS MCS 2024) ─────────────────────────────────
# Stocks are tracked in "weeks of EV-industry consumption" so shocks are
# immediately interpretable.  Baseline safety stock reflects typical
# industry inventory practice (shorter = JIT, longer = strategic buffer).
#
# EV-industry consumption fractions (IEA CMMR 2023):
#   lithium   ~37% of global production goes to EVs
#   cobalt    ~28% of global production allocated to all batteries
#   graphite  ~30% of natural graphite is anode-grade for batteries
#   nickel    ~15% battery-grade nickel goes to EVs
#   ree       ~25% of Nd-Pr production to EV permanent magnets
#   sic       ~60% of SiC wafers to automotive (growing from 40% in 2021)

MINERALS = {
    "lithium": {
        # USGS 2024: 180 kt Li content mined globally
        "global_prod_kt_yr":   180.0,
        "ev_share":              0.37,   # IEA CMMR 2023
        "ev_prod_kt_yr":        66.6,    # = 180 × 0.37
        # Leontief intensity: kg of mineral per kWh of cell
        # Source: IEA CMMR 2023 Table A.2
        "kg_per_kwh":            0.075,  # all chemistries
        "safety_stock_weeks":    4,
        "supply_concentration":  {       # USGS MCS 2024
            "australia": 0.46,
            "chile":     0.30,
            "china":     0.14,
            "others":    0.10,
        },
        "price_usd_t":        13_500,    # 2024 spot Li₂CO₃ equivalent
        "price_volatility":    0.35,     # ann. σ/μ (BNEF)
    },
    "cobalt": {
        "global_prod_kt_yr":   190.0,
        "ev_share":              0.28,   # IEA CMMR 2023
        "ev_prod_kt_yr":        53.2,
        # NMC-only intensity; weighted by chemistry mix below
        "kg_per_kwh_nmc":        0.020,  # IEA CMMR 2023
        "kg_per_kwh_lfp":        0.000,
        "safety_stock_weeks":    6,
        "supply_concentration":  {
            "drc":       0.70,           # USGS MCS 2024 — systemic risk node
            "russia":    0.04,
            "australia": 0.03,
            "others":    0.23,
        },
        "price_usd_t":        33_000,
        "price_volatility":    0.40,
    },
    "graphite": {
        "global_prod_kt_yr": 1_300.0,
        "ev_share":              0.30,   # anode-grade fraction
        "ev_prod_kt_yr":       390.0,
        "kg_per_kwh":            0.095,  # IEA CMMR 2023 (anode C)
        "safety_stock_weeks":    4,
        "supply_concentration":  {
            "china":      0.79,          # USGS MCS 2024
            "mozambique": 0.09,
            "madagascar": 0.03,
            "others":     0.09,
        },
        "price_usd_t":         1_100,
        "price_volatility":    0.20,
    },
    "ree": {
        # Neodymium as proxy for NdFeB magnet supply (Nd ~85% of magnet RE)
        "global_prod_kt_yr":    40.0,    # USGS MCS 2024 (Nd+Pr fraction)
        "ev_share":              0.25,
        "ev_prod_kt_yr":        10.0,
        # kg NdFeB magnet per PMSM motor; PMSM fraction of EV motors = 0.82
        "kg_per_motor":          1.2,    # IEA CMMR 2023
        "pmsm_fraction":         0.82,
        "safety_stock_weeks":    8,      # longer buffer; processing is in China
        "supply_concentration":  {
            "china":  0.85,
            "usa":    0.12,
            "others": 0.03,
        },
        "price_usd_t":        65_000,
        "price_volatility":    0.30,
    },
    "sic_wafer": {
        # t SiC crystal substrate; not a mineral in USGS sense but same logic
        "global_prod_t_yr":   2_500.0,  # Yole Intelligence 2023
        "ev_share":              0.60,   # share to automotive electronics
        "ev_prod_t_yr":       1_500.0,
        # SiC content per EV inverter; 45% of inverters now SiC (Yole 2023)
        "g_per_inverter":       20.0,    # g SiC crystal per SiC MOSFET module
        "sic_inverter_fraction": 0.45,   # fraction of EV inverters using SiC
        "safety_stock_weeks":   12,      # long lead time (26 wk) → large buffer
        "supply_concentration":  {
            "wolfspeed_usa":  0.30,
            "coherent_usa":   0.20,
            "sicc_china":     0.18,
            "stm_eu":         0.12,
            "others":         0.20,
        },
        "price_usd_wafer":     250,      # 150mm substrate (Wolfspeed 10-K 2023)
        "price_volatility":    0.15,
    },
}

# ── Cell manufacturers (IEA GEO 2024 + BNEF 2023 + company filings) ──────────
# Global cell deployed 2023: 822 GWh  (IEA GEO 2024)
# Global nameplate capacity 2024: ~1,500 GWh  (BNEF)
# We calibrate capacity_gwh_yr to each maker's actual 2023 deliveries
# (= market_share × 822 GWh) so the model starts in steady state.
# Nameplate capacity creates ~1.8× over-production and unbounded cell stocks.

CELL_GLOBAL_GWH_2023    = 822.0    # GWh actually deployed in new EVs
CELL_GROWTH_RATE_WK     = (1.29 ** (1/52)) - 1   # 29% per year → weekly

CELL_MAKERS = {
    "catl": {
        "country":           "china",
        "capacity_gwh_yr":    304.1,   # = 822 × 0.37 (market_share × deployed)
        "market_share":        0.37,
        # Chemistry mix (CATL 2023 mix estimate, BNEF)
        "lfp_fraction":        0.45,
        "nmc_fraction":        0.55,
        "safety_stock_weeks":  4,
        "recovery_rate_wk":    0.04,   # capacity ramp rate after shock
    },
    "byd_cells": {
        "country":           "china",
        "capacity_gwh_yr":    115.1,   # = 822 × 0.14
        "market_share":        0.14,
        "lfp_fraction":        0.90,   # BYD Blade = LFP dominant
        "nmc_fraction":        0.10,
        "safety_stock_weeks":  4,
        "recovery_rate_wk":    0.04,
    },
    "lg_es": {
        "country":           "south_korea",
        "capacity_gwh_yr":    106.9,   # = 822 × 0.13
        "market_share":        0.13,
        "lfp_fraction":        0.05,
        "nmc_fraction":        0.95,
        "safety_stock_weeks":  6,
        "recovery_rate_wk":    0.03,
    },
    "panasonic": {
        "country":           "japan",
        "capacity_gwh_yr":     57.5,   # = 822 × 0.07
        "market_share":        0.07,
        "lfp_fraction":        0.00,
        "nmc_fraction":        1.00,   # NCA = NMC family for cobalt intensity
        "safety_stock_weeks":  6,
        "recovery_rate_wk":    0.03,
    },
    "samsung_sdi": {
        "country":           "south_korea",
        "capacity_gwh_yr":     49.3,   # = 822 × 0.06
        "market_share":        0.06,
        "lfp_fraction":        0.00,
        "nmc_fraction":        1.00,
        "safety_stock_weeks":  5,
        "recovery_rate_wk":    0.03,
    },
    "sk_on": {
        "country":           "south_korea",
        "capacity_gwh_yr":     41.1,   # = 822 × 0.05
        "market_share":        0.05,
        "lfp_fraction":        0.00,
        "nmc_fraction":        1.00,
        "safety_stock_weeks":  5,
        "recovery_rate_wk":    0.03,
    },
    "calb": {
        # CALB (China Aviation Lithium Battery) — 4th-largest Chinese cell maker 2023
        # 2023 deliveries ~41 GWh; LCTP (Lithium Cell Technology Platform) = LFP dominant
        # Source: SNE Research 2023; CALB prospectus (HK IPO 2022); BNEF 2023
        "country":           "china",
        "capacity_gwh_yr":    41.1,    # = 822 × 0.05
        "market_share":        0.05,
        "lfp_fraction":        0.80,   # CALB heavily LFP; some NMC for premium OEMs
        "nmc_fraction":        0.20,
        "safety_stock_weeks":  4,
        "recovery_rate_wk":    0.04,
    },
    "aesc_uk": {
        # Envision AESC Sunderland — UK's only active gigafactory as of 2023
        # Current output: ~1.8 GWh/yr supplying Nissan Leaf/Ariya
        # Expansion to 35 GWh by 2030 planned (UK Automotive Transformation Fund award)
        # Source: AESC press release Nov 2023; UK BEIS ATF grant announcement 2023
        "country":           "uk",
        "capacity_gwh_yr":     1.6,    # = 822 × 0.002 (tiny global share)
        "market_share":        0.002,
        "lfp_fraction":        0.00,
        "nmc_fraction":        1.00,   # NMC 811 for high energy density (Nissan spec)
        "safety_stock_weeks":  5,
        "recovery_rate_wk":    0.03,
    },
    "others_cells": {
        # Residual: EVE Energy, SVOLT, Farasis, Freyr, ACC, Northvolt, etc.
        # Adjusted down from 0.18 to 0.128 to account for CALB + AESC split-out
        "country":           "mixed",
        "capacity_gwh_yr":    105.2,   # = 822 × 0.128
        "market_share":        0.128,
        "lfp_fraction":        0.50,
        "nmc_fraction":        0.50,
        "safety_stock_weeks":  4,
        "recovery_rate_wk":    0.04,
    },
}

# ── Sub-system suppliers  (Tier 1) ────────────────────────────────────────────
# Global EV production 2023: ~14 million units  (IEA GEO 2024)
EV_GLOBAL_UNITS_2023_K  = 14_000   # k vehicles (14 million)

TIER1 = {
    "battery_pack": {
        # Pack assembly = cell output converted to vehicle-equivalent packs
        # Key input: cells_gwh (from cell manufacturers)
        "capacity_units_yr_k":   14_000,
        "key_input":              "cells",
        "lead_time_weeks":        4,
        "safety_stock_weeks":     3,
        "recovery_rate_wk":       0.05,
    },
    "inverter": {
        # Key input: sic_wafer (for SiC inverters) + electronics
        "capacity_units_yr_k":   14_000,
        "key_input":              "sic_wafer",
        # Fraction of inverters that actually need SiC (rest use Si IGBT)
        "sic_dependency":         0.45,   # Yole 2023
        "lead_time_weeks":        16,
        "safety_stock_weeks":     8,
        "recovery_rate_wk":       0.025,  # slow; specialised manufacturing
    },
    "motor": {
        # Key input: ree (for NdFeB magnets in PMSM)
        "capacity_units_yr_k":   14_000,
        "key_input":              "ree",
        "pmsm_fraction":          0.82,   # fraction needing REE magnets
        "lead_time_weeks":        12,
        "safety_stock_weeks":     6,
        "recovery_rate_wk":       0.04,
    },
    "harness": {
        # Labour-intensive JIT supply; Ukraine disruption scenario target
        # Source: Leoni 2022 disclosure; Aptiv AR 2023
        "capacity_units_yr_k":   14_000,
        "key_input":              "copper",  # tracked separately (not shocked here)
        "ukraine_share":           0.30,     # ~30% EU harness from Ukraine
        "lead_time_weeks":         6,
        "safety_stock_weeks":      2,        # lean JIT — the critical vulnerability
        "recovery_rate_wk":        0.06,
    },
}

# ── OEM profiles ──────────────────────────────────────────────────────────────
# Global EV production 2023: ~14 million units  (IEA GEO 2024)
# Chinese OEMs split into BYD (highly vertically integrated) and other Chinese
# (SAIC, Geely, NIO, Xpeng, Li Auto, GAC, CHERY — varied integration levels).
# UK OEM carved out from the European group (JLR, BMW Mini Oxford, Vauxhall Ellesmere Port).
# Total: 1575 + 6825 + 175 + 1505 + 1820 + 1120 + 980 = 14 000 k  ✓
OEMS = {
    "byd_oem": {
        # BYD Motor — pure-BEV production only (excludes DM-i plug-in hybrids)
        # BYD 2023 Annual Report: 1,574,822 BEV units sold globally
        # World's highest vertical integration: Blade (LFP) cells, e-Platform 3.0 motors,
        # BYD Semiconductor chips, IGBT modules — nearly fully self-supplied
        # Source: BYD Annual Report 2023; IEA GEO 2024; BNEF EV Maker Tracker 2024
        "region":                "china",
        "annual_target_k":        1_575,   # BYD BEV only (BYD AR 2023: 1,574,822 units)
        "vertical_integration":   0.95,    # highest in industry; cells + motors + chips in-house
        "safety_stock_weeks":     3,       # internalised supply chain → shorter buffer needed
        "dual_source_trigger":    0.15,
        "cell_sources": {"byd_cells": 0.95, "catl": 0.05},
    },
    "other_chinese_oem": {
        # SAIC, Geely (Zeekr/Lynk&Co), NIO, Xpeng, Li Auto, GAC Aion, CHERY, etc.
        # Residual Chinese BEV volume after BYD split-out: 8,400 − 1,575 = 6,825 k
        # Wide range of integration: SAIC moderate (CATL JV), NIO/Xpeng low (outsourced cells)
        # Li Auto uses CATL/CALB for EREV; GAC Aion self-developing cells (Gigafactory 2025)
        # Source: IEA GEO 2024 (China total); SNE Research 2023; company filings
        "region":                "china",
        "annual_target_k":        6_825,   # remaining ~49% of 14M global EV market
        "vertical_integration":   0.50,    # average across heterogeneous group
        "safety_stock_weeks":     4,
        "dual_source_trigger":    0.20,
        # Heavy CATL reliance is the key vulnerability vs BYD's self-supply
        "cell_sources": {"catl": 0.55, "calb": 0.15,
                         "byd_cells": 0.10, "others_cells": 0.20},
    },
    "uk_oem": {
        # Jaguar Land Rover (Tata Motors), BMW Mini (Oxford plant), Vauxhall (Stellantis,
        # Ellesmere Port), LEVC (London Electric Vehicle Company, Geely-owned)
        # UK EV production 2023: ~175 k units (SMMT 2024 production statistics)
        # No domestic gigafactory at scale — AESC Sunderland is the only active UK cell plant
        # (1.8 GWh/yr, Nissan supply). Post-Brexit rules-of-origin (40 → 55% threshold by 2027)
        # create sourcing pressure to use EU/UK cells, making supply chain particularly fragile.
        # Source: SMMT Production Statistics 2024; UK BEIS Automotive Transformation Fund 2023;
        #         IEA GEO 2024; JLR Annual Report 2023/24
        "region":                "uk",
        "annual_target_k":          175,   # ~1.25% of 14M (SMMT 2024)
        "vertical_integration":   0.05,    # minimal — no domestic cell capacity at scale
        "safety_stock_weeks":     5,
        "dual_source_trigger":    0.20,
        # Primarily Korean suppliers manufacturing in EU (satisfies rules-of-origin partially)
        # JLR: Samsung SDI partnership; BMW Mini: Samsung SDI (Hungary);
        # Vauxhall: LG ES (Poland), SK On (Hungary); AESC Sunderland: Nissan + future JLR
        "cell_sources": {"samsung_sdi": 0.30, "lg_es": 0.25,
                         "sk_on": 0.15, "aesc_uk": 0.20, "catl": 0.10},
    },
    "us_oem": {
        "region":                "usa",
        "annual_target_k":        1_820,   # ~13% of 14M
        "vertical_integration":   0.40,    # Tesla in-house + GM Ultium
        "safety_stock_weeks":     5,
        "dual_source_trigger":    0.25,
        "cell_sources": {"panasonic": 0.40, "catl": 0.30, "lg_es": 0.30},
    },
    "german_oem": {
        # Continental European OEMs: VW Group, BMW, Mercedes-Benz, Renault, Stellantis-EU
        # Excludes UK production (carved out to uk_oem above)
        # 1,680 k − 175 k (UK) = 1,505 k remaining European OEM volume
        # Source: IEA GEO 2024; ACEA EV registrations 2023
        "region":                "europe",
        "annual_target_k":        1_505,   # ~10.75% of 14M (EU excl. UK)
        "vertical_integration":   0.20,    # heavy Tier-1 reliance; VW PowerCo ramping
        "safety_stock_weeks":     6,
        "dual_source_trigger":    0.20,
        "cell_sources": {"lg_es": 0.35, "samsung_sdi": 0.30,
                         "sk_on": 0.25, "catl": 0.10},
    },
    "korean_oem": {
        "region":                "korea",
        "annual_target_k":        1_120,   # ~8% of 14M
        "vertical_integration":   0.45,
        "safety_stock_weeks":     5,
        "dual_source_trigger":    0.20,
        "cell_sources": {"sk_on": 0.50, "samsung_sdi": 0.30, "lg_es": 0.20},
    },
    "japanese_oem": {
        # Toyota, Honda, Nissan — late EV push; ~7% of 14M global EV units (IEA GEO 2024)
        # Toyota bZ series, Honda e:N2, Nissan Ariya
        "region":                "japan",
        "annual_target_k":          980,   # ~7% of 14M
        "vertical_integration":   0.30,    # Panasonic partnership (Toyota)
        "safety_stock_weeks":     6,
        "dual_source_trigger":    0.20,
        "cell_sources": {"panasonic": 0.60, "samsung_sdi": 0.20, "lg_es": 0.20},
    },
}

# ── Market demand (IEA GEO 2024 + SMMT 2024) ─────────────────────────────────
# UK split out from Europe: SMMT 2024 reports 314,967 new BEV registrations in 2023
# (315 k × 65 kWh/veh ≈ 20 GWh).  Europe adjusted: 147 − 20 = 127 GWh.
# UK YoY growth: SMMT 2024 shows +18% BEV in 2023; policy tailwinds (ZEV mandate
# from 2024: 22% of new car sales must be zero emission) accelerate to ~28%/yr.
MARKETS = {
    "china":   {"gwh_2023": 493, "yoy": 0.33, "avg_kwh_veh": 62},
    # Europe excl. UK: Germany, France, Norway, Netherlands, etc.
    "europe":  {"gwh_2023": 127, "yoy": 0.11, "avg_kwh_veh": 68},
    # UK: split from Europe; ZEV mandate (2024) drives faster growth than EU average
    # Source: SMMT EV Registration Data 2024; UK DfT ZEV mandate consultation 2023
    "uk":      {"gwh_2023":  20, "yoy": 0.28, "avg_kwh_veh": 65},
    "usa":     {"gwh_2023": 104, "yoy": 0.35, "avg_kwh_veh": 85},
    # Japan/Korea row separated from rest-of-world for Japanese OEM demand routing
    # IEA GEO 2024: Japan+Korea ~14 GWh combined; ~6% YoY
    "japan":   {"gwh_2023":  14, "yoy": 0.06, "avg_kwh_veh": 48},
    # Residual ROW adjusted: 78 − 14 = 64 GWh (SE Asia, Middle East, LatAm)
    "row":     {"gwh_2023":  64, "yoy": 0.42, "avg_kwh_veh": 55},
}

DEMAND_PRICE_ELASTICITY = -0.30   # % demand change per 1% price rise (IEA 2023)

# ── SD model initial conditions ───────────────────────────────────────────────
# Each stock is initialised at its target safety-stock level
# (= baseline weekly throughput × safety_stock_weeks)
# These are derived from MINERALS / TIER1 / CELL_MAKERS above;
# the SDModel computes them at initialisation.

# Adjustment factor: how strongly a stock shortfall constrains downstream
# production.  1.0 = proportional (linear); >1 = amplified; <1 = dampened.
LEONTIEF_STRICTNESS = 1.0   # keep linear for first version

# Bullwhip amplification: each tier over-orders by this fraction when
# stock falls below target.  Empirically 1.2–1.5 in automotive (Lee 1997).
BULLWHIP_FACTOR = 1.25
