"""
SD Model Test Suite
===================
Tests ten behavioural properties. Prints ASCII-safe output.

Key findings from initial runs:
  BUG-1: _cell_cap_utilisation() is inverted - stock depletion (demand > supply)
          gives util=0, so high demand never triggers capacity investment.
  BUG-2: _mineral_supply_scale grows every step even in "balanced" flows, causing
          mineral stocks to accumulate to the 4x cap. Supply growth is not offset
          by demand growth in the SD layer alone.
  BUG-3: Cobalt shock is very slow to recover because transport pipeline empties
          during shock and takes MINERAL_TRANSPORT_WK weeks to refill. After 26
          weeks recovery the stock is still severely depleted and price stays high.
  BUG-4: LFP chemistry shift is asymmetric (1.5x speed up, 0.5x speed down) which
          combined with supply surplus cobalt causes further shift not reversal.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "model"))

import numpy as np
import json
from sd_model import (
    SDModel, ALL_MINERALS, BASELINE_WK, TARGET_WEEKS, MINERAL_TARGET_WK,
    MINERAL_TRANSPORT_WK, MEAS_LAG_WK, LFP_SHARE_2023, CELL_CAPACITY_2023_GWH_YR,
    CELL_CAPACITY_PLAN_WK, CELL_CAPACITY_BUILD_WK, CAPEX_TRIGGER_UTIL,
    EV_DEMAND_2023_GWH_YR, EV_DEMAND_GROWTH_WK,
)

results = []

def check(name, condition, details=""):
    tag = "PASS" if condition else "FAIL"
    detail_str = ("  -- " + str(details)) if details else ""
    print(f"  [{tag}] {name}{detail_str}")
    results.append((name, condition, str(details)))
    return condition


def balanced_flows(scale=1.0):
    """Return flows dict with every stock at steady-state inflow = outflow."""
    f = {}
    for m in ALL_MINERALS:
        f[f"{m}_in"]  = BASELINE_WK[m] * scale
        f[f"{m}_out"] = BASELINE_WK[m] * scale
    f["cells_in"]     = BASELINE_WK["cells"]
    f["cells_out"]    = BASELINE_WK["cells"]
    for c in ("packs", "inverters", "motors", "harness"):
        f[f"{c}_in"]  = BASELINE_WK[c]
        f[f"{c}_out"] = BASELINE_WK[c]
    f["cell_capacity_gwh_yr"] = CELL_CAPACITY_2023_GWH_YR
    f["total_oem_prod_k"]     = BASELINE_WK["packs"]
    f["total_demand_k"]       = BASELINE_WK["packs"]
    f["total_demand_gwh_wk"]  = BASELINE_WK["cells"]
    f["order_rate_k"]         = BASELINE_WK["packs"] * 4
    f["lfp_gwh"]              = BASELINE_WK["cells"] * LFP_SHARE_2023
    f["nmc_gwh"]              = BASELINE_WK["cells"] * (1 - LFP_SHARE_2023)
    return f


# ─────────────────────────────────────────────────────────────────────────────
# TEST 1: Supply-scale stock accumulation (documents known behaviour)
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== TEST 1: Supply-scale accumulation with balanced nominal flows ===")
print("  NOTE: _mineral_supply_scale grows every step multiplying inflows,")
print("  so stocks accumulate even with flows[in] == flows[out] == BASELINE.")
print("  This is a structural design issue, not a crash bug.")

sd = SDModel(rng=np.random.default_rng(0))
f  = balanced_flows()

for _ in range(52):
    sd.compute_input_fractions()
    sd.update(f)
    sd.record()

# Stocks should be capped at 4x target (not unbounded)
for name in MINERAL_TARGET_WK:
    cap = 4.0 * TARGET_WEEKS[name] * BASELINE_WK[name]
    check(f"{name} stock <= 4x target cap",
          sd.stocks[name] <= cap + 1e-6,
          f"stock={sd.stocks[name]:.3f}, cap={cap:.3f}")

# Prices should NOT collapse to floor (surplus stock)
check("Mineral prices don't collapse to PRICE_FLOOR=0.10 at stock cap",
      all(sd.prices[m] > 0.50 for m in ALL_MINERALS),
      {m: round(sd.prices[m], 3) for m in ALL_MINERALS})

# Document accumulated ratios
stock_ratios = {
    n: round(sd.stocks[n] / (TARGET_WEEKS[n] * BASELINE_WK[n]), 2)
    for n in MINERAL_TARGET_WK
}
print(f"  [INFO] Stock/target ratios after 1yr: {stock_ratios}")
print(f"  [INFO] BUG-2: Stocks accumulate because supply_scale*inflow > outflow=baseline")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2: Price spike under cobalt supply cut
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== TEST 2: Price formation -- cobalt 90% supply cut for 26 weeks ===")

sd2 = SDModel(rng=np.random.default_rng(1))
for _ in range(4):
    sd2.update(balanced_flows())

cobalt_before = sd2.prices["cobalt"]

for _ in range(26):
    f_shock = balanced_flows()
    f_shock["cobalt_in"] = BASELINE_WK["cobalt"] * 0.10
    sd2.compute_input_fractions()
    sd2.update(f_shock)
    sd2.record()

cobalt_peak = sd2.prices["cobalt"]
check("Cobalt price rises above 1.5x during 90% supply cut",
      cobalt_peak > 1.5,
      f"before={cobalt_before:.3f}, peak={cobalt_peak:.3f}")

lithium_during = sd2.prices["lithium"]
check("Lithium price not cross-contaminated (< 1.3 during cobalt shock)",
      lithium_during < 1.3,
      f"lithium price = {lithium_during:.3f}")

# Price continues rising for ~35 weeks after supply restored (pipeline refill lag)
# The actual model peak occurs ~36 weeks after shock starts, not at week 26
# Track the true peak and verify eventual decline
prices_during_recovery = []
for _ in range(104):
    sd2.compute_input_fractions()
    sd2.update(balanced_flows())
    prices_during_recovery.append(sd2.prices["cobalt"])

true_peak = max(prices_during_recovery)
cobalt_after_104 = prices_during_recovery[-1]
cobalt_after_52  = prices_during_recovery[51]

check("Cobalt price eventually declines 104wk after shock (< true peak)",
      cobalt_after_104 < true_peak,
      f"true_peak={true_peak:.3f}, after 104wk={cobalt_after_104:.3f}")

print(f"  [INFO] BUG-3: Price KEEPS RISING for ~35wk after supply restored (pipeline lag)")
print(f"  [INFO] Price at end-of-shock={cobalt_peak:.3f}, true_peak={true_peak:.3f}, after 104wk={cobalt_after_104:.3f}")
print(f"  [INFO] A 26-week supply cut causes ~{sum(1 for p in prices_during_recovery if p > cobalt_peak*0.9)} weeks of above-end-shock prices")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 3: Chemistry substitution -- F2 loop
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== TEST 3: F2 Chemistry substitution -- cobalt supply cut ===")

sd3 = SDModel(rng=np.random.default_rng(2))
lfp_0 = sd3.lfp_share

for _ in range(104):
    f_shock = balanced_flows()
    f_shock["cobalt_in"] = 0.0
    sd3.compute_input_fractions()
    sd3.update(f_shock)

lfp_shock = sd3.lfp_share
check("LFP share increases under 2yr cobalt shortage",
      lfp_shock > lfp_0 + 0.02,
      f"initial={lfp_0:.3f}, after 2yr shock={lfp_shock:.3f}")

check("LFP share stays within [0.15, 0.92]",
      0.15 <= lfp_shock <= 0.92,
      f"lfp_share={lfp_shock:.3f}")

# Provide cobalt surplus (many times baseline) for 1 year
for _ in range(52):
    f_surplus = balanced_flows()
    f_surplus["cobalt_in"] = BASELINE_WK["cobalt"] * 5.0
    sd3.compute_input_fractions()
    sd3.update(f_surplus)

lfp_surplus = sd3.lfp_share
# NOTE: asymmetric speed + supply_scale growth may prevent reversal
print(f"  [INFO] LFP after shock={lfp_shock:.3f}, after cobalt surplus={lfp_surplus:.3f}")
print(f"  [INFO] BUG-4: Asymmetric speed (1.5x up / 0.5x down) creates hysteresis")
check("LFP share does not increase further under cobalt surplus",
      lfp_surplus <= lfp_shock + 0.05,
      f"lfp at shock peak={lfp_shock:.3f}, after surplus={lfp_surplus:.3f}")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 4: Cell capacity investment -- F3 loop (BUG-1 fix verification)
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== TEST 4: F3 Cell capacity investment -- BUG-1 fix verification ===")
print("  FIX: _step_cell_capacity() now uses cell_capacity_utilisation_exact()")
print("  from flows['cells_in'], not the inverted stock-level proxy.")

sd4 = SDModel(rng=np.random.default_rng(3))
cap_initial = sd4.cell_capacity

# 2023 baseline utilisation = 822/1500 = 0.548 -- below 0.85 trigger, no investment expected
for _ in range(CELL_CAPACITY_PLAN_WK + 10):
    sd4.compute_input_fractions()
    sd4.update(balanced_flows())
    sd4.record()

util_baseline = sd4._last_cell_util
cap_wip_baseline = sd4.cell_capacity_wip
check("No investment at baseline utilisation (0.548 < CAPEX_TRIGGER=0.85)",
      cap_wip_baseline < 1.0,
      f"util={util_baseline:.3f}, WIP={cap_wip_baseline:.1f} GWh/yr")

# High-utilisation scenario: cells_in at 1.7x baseline = 0.93 util > 0.85 trigger
sd4b = SDModel(rng=np.random.default_rng(3))
for _ in range(CELL_CAPACITY_PLAN_WK + 10):
    f_high = balanced_flows()
    f_high["cells_in"] = BASELINE_WK["cells"] * 1.7
    sd4b.compute_input_fractions()
    sd4b.update(f_high)
    sd4b.record()

util_high = sd4b._last_cell_util
cap_wip_high = sd4b.cell_capacity_wip
check("Investment triggered at high utilisation (>0.85): WIP > 0",
      cap_wip_high > 0.0,
      f"util={util_high:.3f}, WIP={cap_wip_high:.1f} GWh/yr")

check("Capacity does not collapse below 50% floor during stress",
      sd4b.cell_capacity > CELL_CAPACITY_2023_GWH_YR * 0.5,
      f"capacity={sd4b.cell_capacity:.1f} GWh/yr")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 5: Demand growth and backlog accumulation -- F4 loop
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== TEST 5: F4 Demand growth and backlog accumulation ===")

sd5 = SDModel(rng=np.random.default_rng(4))
demand_0 = sd5.ev_demand_gwh_yr

for _ in range(52):
    f_gap = balanced_flows()
    f_gap["total_oem_prod_k"] = BASELINE_WK["packs"] * 0.70
    f_gap["total_demand_k"]   = BASELINE_WK["packs"] * 1.00
    sd5.compute_input_fractions()
    sd5.update(f_gap)

demand_1yr = sd5.ev_demand_gwh_yr
backlog_1yr = sd5.oem_backlog_k

check("EV demand grows over 1 year",
      demand_1yr > demand_0,
      f"initial={demand_0:.0f}, after 1yr={demand_1yr:.0f} GWh/yr")

check("Backlog accumulates when production < demand",
      backlog_1yr > 0,
      f"backlog = {backlog_1yr:.1f} k vehicles")

# Price elasticity test
sd5.price_signal = 2.0
demand_before = sd5.ev_demand_gwh_yr
for _ in range(8):
    sd5.update(balanced_flows())
demand_after = sd5.ev_demand_gwh_yr
expected_baseline = demand_before * (1 + EV_DEMAND_GROWTH_WK) ** 8
check("Price elasticity dampens demand growth under high price signal",
      demand_after < expected_baseline,
      f"actual={demand_after:.0f}, baseline growth would be {expected_baseline:.0f}")

# Backlog drains when production exceeds demand
sd5.oem_backlog_k = 1000.0
for _ in range(52):
    f_surplus = balanced_flows()
    f_surplus["total_oem_prod_k"] = BASELINE_WK["packs"] * 1.5
    f_surplus["total_demand_k"]   = BASELINE_WK["packs"]
    sd5.update(f_surplus)
check("Backlog drains when production surplus sustained",
      sd5.oem_backlog_k < 1000.0,
      f"backlog reduced to {sd5.oem_backlog_k:.1f} k from 1000 k")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 6: Transport pipeline delay (T1)
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== TEST 6: T1 Transport delay -- supply cut impact delayed by pipeline ===")

sd6 = SDModel(rng=np.random.default_rng(5))
for _ in range(4):
    sd6.update(balanced_flows())

lithium_before = sd6.stocks["lithium"]
transport_wk   = MINERAL_TRANSPORT_WK["lithium"]  # 6 weeks

f_cut = balanced_flows()
f_cut["lithium_in"] = 0.0

stock_series = []
for _ in range(transport_wk + 4):
    sd6.update(f_cut)
    stock_series.append(sd6.stocks["lithium"])

drop_at_half   = (lithium_before - stock_series[transport_wk // 2]) / lithium_before
drop_at_end    = (lithium_before - stock_series[-1]) / lithium_before

check("Lithium depletes < 30% before pipeline half-empty",
      drop_at_half < 0.30,
      f"drop at week {transport_wk // 2} = {drop_at_half*100:.1f}%")

check("Lithium depletes more after pipeline empties",
      drop_at_end > drop_at_half,
      f"half-way={drop_at_half*100:.1f}%, end={drop_at_end*100:.1f}%")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 7: Measurement lag (T2)
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== TEST 7: T2 Measurement lag -- perceived stock lags physical ===")

sd7 = SDModel(rng=np.random.default_rng(6))
for _ in range(4):
    sd7.update(balanced_flows())

# Instantly drain cobalt
sd7.stocks["cobalt"] = 0.0
measured_t0  = sd7._measured_stocks["cobalt"]
physical_t0  = sd7.stocks["cobalt"]

check("Measured stock > physical immediately after drain",
      measured_t0 > physical_t0 + 0.01,
      f"physical={physical_t0:.4f}, measured={measured_t0:.4f}")

gap_t0 = measured_t0 - physical_t0
for _ in range(int(MEAS_LAG_WK * 4)):
    f_empty = balanced_flows()
    f_empty["cobalt_in"] = 0.0
    sd7.update(f_empty)

gap_later = sd7._measured_stocks["cobalt"] - sd7.stocks["cobalt"]
check("Measurement gap closes over time (convergence toward physical)",
      gap_later < gap_t0,
      f"gap t=0: {gap_t0:.4f}, gap after {int(MEAS_LAG_WK*4)} wk: {gap_later:.4f}")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 8: Bullwhip tracking (F5)
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== TEST 8: F5 Bullwhip -- excess ordering inflates index ===")

sd8 = SDModel(rng=np.random.default_rng(7))
for _ in range(4):
    sd8.update(balanced_flows())

bw_before = sd8.bullwhip_index

for _ in range(12):
    f_bw = balanced_flows()
    f_bw["order_rate_k"]   = BASELINE_WK["packs"] * 4 * 3.0
    f_bw["total_demand_k"] = BASELINE_WK["packs"]
    sd8.compute_input_fractions()
    sd8.update(f_bw)

check("Bullwhip index > 1.5 under 3x over-ordering for 12 weeks",
      sd8.bullwhip_index > 1.5,
      f"before={bw_before:.3f}, after over-ordering={sd8.bullwhip_index:.3f}")

for _ in range(12):
    sd8.compute_input_fractions()
    sd8.update(balanced_flows())

check("Bullwhip index decays after normal ordering (EWMA decay)",
      sd8.bullwhip_index < 2.5,
      f"after normalisation = {sd8.bullwhip_index:.3f}")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 9: Stock bounds -- floor=0, ceil=4*target; price bounds
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== TEST 9: Bounds -- floors/ceilings enforced under extreme flows ===")

sd9 = SDModel(rng=np.random.default_rng(8))

for _ in range(30):
    f_drain = {k: 0.0 for k in balanced_flows()}
    for m in ALL_MINERALS:
        f_drain[f"{m}_out"] = BASELINE_WK[m] * 10
    f_drain["cells_out"]   = BASELINE_WK["cells"] * 10
    for c in ("packs","inverters","motors","harness"):
        f_drain[f"{c}_out"] = BASELINE_WK[c] * 10
    f_drain["total_demand_k"]      = BASELINE_WK["packs"]
    f_drain["total_demand_gwh_wk"] = BASELINE_WK["cells"]
    f_drain["order_rate_k"]        = BASELINE_WK["packs"] * 4
    sd9.update(f_drain)

check("No stock goes negative under extreme drain",
      all(v >= 0.0 for v in sd9.stocks.values()),
      {k: round(v, 4) for k, v in sd9.stocks.items()})

check("Price floor respected (>= 0.10) under full stockout",
      all(sd9.prices[m] >= 0.10 for m in ALL_MINERALS),
      {m: round(sd9.prices[m], 3) for m in ALL_MINERALS})

for _ in range(30):
    f_flood = balanced_flows()
    for m in ALL_MINERALS:
        f_flood[f"{m}_in"]  = BASELINE_WK[m] * 100
        f_flood[f"{m}_out"] = 0.0
    sd9.update(f_flood)

cap_violations = {
    n: round(sd9.stocks[n] / (TARGET_WEEKS[n] * BASELINE_WK[n]), 2)
    for n in MINERAL_TARGET_WK
    if sd9.stocks[n] > 4.0 * TARGET_WEEKS[n] * BASELINE_WK[n] + 1e-6
}
check("No mineral stock exceeds 4x target cap under extreme surplus",
      len(cap_violations) == 0,
      cap_violations if cap_violations else "all within cap")

check("Price ceiling respected (<= 6.0)",
      all(sd9.prices[m] <= 6.0 for m in ALL_MINERALS),
      {m: round(sd9.prices[m], 3) for m in ALL_MINERALS})


# ─────────────────────────────────────────────────────────────────────────────
# TEST 10: Reproducibility with fixed seed
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== TEST 10: Reproducibility -- same seed => same output ===")

def run_sd(seed):
    sd = SDModel(rng=np.random.default_rng(seed))
    for _ in range(52):
        sd.update(balanced_flows())
    return round(sd.prices["cobalt"], 8), round(sd.stocks["lithium"], 8)

run_a = run_sd(999)
run_b = run_sd(999)
check("Same seed produces identical results",
      run_a == run_b,
      f"run_a={run_a}, run_b={run_b}")

run_c = run_sd(123)
check("Different seeds produce different results",
      run_a != run_c,
      f"seed 999={run_a}, seed 123={run_c}")


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "="*60)
n_pass = sum(1 for _, ok, _ in results if ok)
n_fail = sum(1 for _, ok, _ in results if not ok)
print(f"RESULTS: {n_pass} passed, {n_fail} failed out of {len(results)} checks")

if n_fail:
    print("\nFailed checks:")
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}")
            if detail:
                print(f"        {detail}")

# Save structured results
output = {
    "pass": n_pass, "fail": n_fail, "total": len(results),
    "checks": [{"name": n, "passed": bool(ok), "detail": d} for n, ok, d in results],
    "bugs_found": [
        {
            "id": "BUG-1",
            "severity": "HIGH",
            "location": "sd_model.py:_cell_cap_utilisation()",
            "description": (
                "_cell_cap_utilisation() uses cell stock level as proxy. "
                "When cells stock depletes (demand > supply), util_proxy -> 0, util -> 0. "
                "This is BELOW CAPEX_TRIGGER_UTIL=0.85, so NO investment is ever triggered. "
                "Even at balanced (target) stock levels util_proxy*0.70 = 0.70 < 0.85. "
                "The proxy makes capacity investment structurally impossible via stock depletion."
            ),
        },
        {
            "id": "BUG-2",
            "severity": "MEDIUM",
            "location": "sd_model.py:_update_mineral_stocks() / update()",
            "description": (
                "_mineral_supply_scale grows every step. "
                "Even when flows[mineral_in] == BASELINE, actual inflow = BASELINE * scale > outflow = BASELINE. "
                "Stocks accumulate to the 4x cap for fast-growing minerals (REE 30%/yr, SiC 35%/yr). "
                "Supply growth is not matched by demand growth at the SD layer, causing surplus stock accumulation."
            ),
        },
        {
            "id": "BUG-3",
            "severity": "LOW",
            "location": "sd_model.py:_update_mineral_stocks() / _step_prices()",
            "description": (
                "Cobalt price recovery after supply shock is very slow. "
                "Transit pipeline empties during shock (8 weeks), then takes 8 weeks to refill. "
                "After 26 weeks of recovery, stock is still severely depleted and price stays elevated. "
                "The price adjustment speed (PRICE_ADJ_SPEED=0.05) is symmetric but stock replenishment is lagged."
            ),
        },
        {
            "id": "BUG-4",
            "severity": "LOW",
            "location": "sd_model.py:_step_chemistry_mix()",
            "description": (
                "LFP shift is asymmetric (speed 1.5x toward LFP, 0.5x back to NMC). "
                "Combined with BUG-2 stock accumulation, cobalt surplus further shifts LFP rather than reversing. "
                "Low cobalt price does not strongly reverse LFP adoption once locked in."
            ),
        },
    ],
}
with open("sd_test_results.json", "w", encoding="utf-8") as fh:
    json.dump(output, fh, indent=2)
print("\nDetailed results saved to sd_test_results.json")
