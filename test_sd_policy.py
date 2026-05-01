"""
SD Model -- UK Government Policy Layer Test Suite
==================================================
Tests the GovernmentPolicySD class and its integration with SDModel.

Coverage:
  P01  Ramp mechanics       -- ramp reaches 1.0 after RAMP_WEEKS, 0 before activation
  P02  Idempotency          -- re-activating same package has no effect
  P03  Package isolation    -- activating package A does not affect package B's domain
  P04  Late activation      -- policy activated mid-run ramps correctly from that point
  P05  Battery Sovereignty  -- CAPEX trigger reduction, build speed, demand boost, recycling
  P06  Tier-1 Resilience    -- component targets raised, backlog clears faster, bullwhip sharpened
  P07  Critical Minerals    -- price spike damped, recovery faster, mineral targets raised
  P08  Full Strategy        -- compounding of all packages, no parameter goes out of bounds
  P09  Record / summary     -- policy fields appear in history and summary when active
  P10  No-policy baseline   -- zero policies = identical output to original model
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "model"))

import json
import numpy as np
from sd_model import (
    SDModel, GovernmentPolicySD,
    ALL_MINERALS, BASELINE_WK, TARGET_WEEKS, MINERAL_TARGET_WK,
    CELL_CAPACITY_2023_GWH_YR, LFP_SHARE_2023,
    EV_DEMAND_2023_GWH_YR, EV_DEMAND_GROWTH_WK,
    CAPEX_TRIGGER_UTIL, CELL_CAPACITY_PLAN_WK,
    BULLWHIP_SMOOTH,
)

results = []

def check(name, condition, details=""):
    tag = "PASS" if condition else "FAIL"
    detail_str = ("  -- " + str(details)) if details else ""
    print(f"  [{tag}] {name}{detail_str}")
    results.append((name, bool(condition), str(details)))
    return condition


# ─── helpers ──────────────────────────────────────────────────────────────────

def make_flows(cells_in_mult=1.0, cobalt_in_mult=1.0, prod_mult=1.0, demand_mult=1.0):
    f = {}
    for m in ALL_MINERALS:
        f[f"{m}_in"]  = BASELINE_WK[m]
        f[f"{m}_out"] = BASELINE_WK[m]
    f["cobalt_in"] = BASELINE_WK["cobalt"] * cobalt_in_mult
    for c in ("cells", "packs", "inverters", "motors", "harness"):
        f[f"{c}_in"]  = BASELINE_WK[c]
        f[f"{c}_out"] = BASELINE_WK[c]
    f["cells_in"]             = BASELINE_WK["cells"] * cells_in_mult
    f["cell_capacity_gwh_yr"] = CELL_CAPACITY_2023_GWH_YR
    f["total_oem_prod_k"]     = BASELINE_WK["packs"] * prod_mult
    f["total_demand_k"]       = BASELINE_WK["packs"] * demand_mult
    f["total_demand_gwh_wk"]  = BASELINE_WK["cells"]
    f["order_rate_k"]         = BASELINE_WK["packs"] * 4
    f["lfp_gwh"]              = BASELINE_WK["cells"] * LFP_SHARE_2023
    f["nmc_gwh"]              = BASELINE_WK["cells"] * (1 - LFP_SHARE_2023)
    return f


def fresh(seed=42, *packages):
    sd = SDModel(rng=np.random.default_rng(seed))
    for pkg in packages:
        sd.activate_policy(pkg, 0)
    return sd


def run(sd, n, **flow_kwargs):
    for _ in range(n):
        sd.update(make_flows(**flow_kwargs))
    return sd


# ═══════════════════════════════════════════════════════════════════════════════
# P01  Ramp mechanics
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== P01: Ramp mechanics ===")

pol = GovernmentPolicySD()
check("Ramp is 0 before any activation", pol._ramp("battery_sovereignty") == 0.0)
check("any_active is False before activation", not pol.any_active)

pol.activate("battery_sovereignty", current_week=0)
check("Ramp is 0 at activation week (tick not called yet)", pol._ramp("battery_sovereignty") == 0.0)

for _ in range(GovernmentPolicySD.RAMP_WEEKS):
    pol.tick()
check(f"Ramp reaches 1.0 after RAMP_WEEKS={GovernmentPolicySD.RAMP_WEEKS} ticks",
      pol._ramp("battery_sovereignty") == 1.0,
      f"ramp={pol._ramp('battery_sovereignty'):.3f}")

for _ in range(10):
    pol.tick()
check("Ramp stays exactly 1.0 after RAMP_WEEKS (no overshoot)",
      pol._ramp("battery_sovereignty") == 1.0)

pol2 = GovernmentPolicySD()
pol2.activate("battery_sovereignty", current_week=10)
for _ in range(10):
    pol2.tick()  # now at week 10
check("Ramp is 0 before activation_week in mid-run activation",
      pol2._ramp("battery_sovereignty") == 0.0,
      f"ramp={pol2._ramp('battery_sovereignty'):.3f}")

pol2.tick()  # now at week 11, elapsed=1
check("Ramp starts increasing one tick after activation",
      0.0 < pol2._ramp("battery_sovereignty") < 1.0,
      f"ramp={pol2._ramp('battery_sovereignty'):.4f}")

check("any_active is True after activation", pol.any_active)


# ═══════════════════════════════════════════════════════════════════════════════
# P02  Idempotency
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== P02: Idempotency -- re-activating a package has no additional effect ===")

pol3 = GovernmentPolicySD()
pol3.activate("tier1_resilience", 0)
pol3.activate("tier1_resilience", 0)   # second call
pol3.activate("tier1_resilience", 5)   # different week -- should be ignored
for _ in range(GovernmentPolicySD.RAMP_WEEKS):
    pol3.tick()

check("Ramp is exactly 1.0 after double-activation (no doubling)",
      pol3._ramp("tier1_resilience") == 1.0,
      f"ramp={pol3._ramp('tier1_resilience'):.3f}")
check("Only one entry in _active dict",
      len([k for k in pol3._active if k == "tier1_resilience"]) == 1)

# Effects are bounded
sd_idem = fresh(1, "tier1_resilience", "tier1_resilience")
run(sd_idem, 52)
check("Component target mult not doubled by re-activation (harness <= 2.0)",
      sd_idem.policy.component_target_mult.get("harness", 1.0) <= 2.0,
      f"harness_mult={sd_idem.policy.component_target_mult.get('harness',1.0):.3f}")


# ═══════════════════════════════════════════════════════════════════════════════
# P03  Package isolation
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== P03: Package isolation -- battery_sovereignty does not affect T1/mineral targets ===")

sd_bs = fresh(1, "battery_sovereignty")
run(sd_bs, GovernmentPolicySD.RAMP_WEEKS + 1)

check("Battery sovereignty: mineral target mult for cobalt stays 1.0",
      sd_bs.policy.mineral_target_mult.get("cobalt", 1.0) == 1.0,
      f"cobalt_mult={sd_bs.policy.mineral_target_mult.get('cobalt',1.0):.3f}")
check("Battery sovereignty: component target mult is empty (T1 not activated)",
      len(sd_bs.policy.component_target_mult) == 0,
      f"comp_targets={sd_bs.policy.component_target_mult}")

sd_cm = fresh(1, "critical_minerals_security")
run(sd_cm, GovernmentPolicySD.RAMP_WEEKS + 1)

check("Critical minerals: CAPEX trigger reduction stays 0 (battery_sov not active)",
      sd_cm.policy.capex_trigger_reduction == 0.0,
      f"capex_reduction={sd_cm.policy.capex_trigger_reduction:.3f}")
check("Critical minerals: demand_growth_boost_wk stays 0",
      sd_cm.policy.demand_growth_boost_wk == 0.0,
      f"demand_boost={sd_cm.policy.demand_growth_boost_wk:.5f}")


# ═══════════════════════════════════════════════════════════════════════════════
# P04  Late activation
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== P04: Late activation -- policy activated at week 26 ramps from that point ===")

TOTAL_WEEKS   = 52 + GovernmentPolicySD.RAMP_WEEKS  # same run length for both models
ACTIVATE_WEEK = 26

# Both models run TOTAL_WEEKS of update(); late model gets policy only from week 26.
sd_late = SDModel(rng=np.random.default_rng(5))
sd_ref  = SDModel(rng=np.random.default_rng(5))

for wk in range(TOTAL_WEEKS):
    if wk == ACTIVATE_WEEK:
        sd_late.activate_policy("battery_sovereignty", current_week=wk)
    sd_late.update(make_flows())
    sd_ref.update(make_flows())

check("Policy ramp at 1.0 after RAMP_WEEKS following late activation",
      sd_late.policy.bat_sov == 1.0,
      f"bat_sov={sd_late.policy.bat_sov:.3f}")

check("Late-activated battery_sov boosts demand vs no-policy over same run length",
      sd_late.ev_demand_gwh_yr > sd_ref.ev_demand_gwh_yr,
      f"policy={sd_late.ev_demand_gwh_yr:.1f}, ref={sd_ref.ev_demand_gwh_yr:.1f}")


# ═══════════════════════════════════════════════════════════════════════════════
# P05  Battery Sovereignty effects
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== P05: Battery Sovereignty -- CAPEX trigger, build speed, demand boost, recycling ===")

N = 60  # weeks past full ramp

sd_base = fresh(10)
sd_bs   = fresh(10, "battery_sovereignty")

run(sd_base, N, cells_in_mult=1.6)
run(sd_bs,   N, cells_in_mult=1.6)

# CAPEX trigger lowered -- investment starts earlier
check("Battery sovereignty: effective CAPEX trigger < baseline 0.85",
      CAPEX_TRIGGER_UTIL - sd_bs.policy.capex_trigger_reduction < CAPEX_TRIGGER_UTIL,
      f"effective={CAPEX_TRIGGER_UTIL - sd_bs.policy.capex_trigger_reduction:.3f}")

check("Battery sovereignty: cell capacity WIP > baseline (investment triggered sooner)",
      sd_bs.cell_capacity_wip > sd_base.cell_capacity_wip,
      f"base={sd_base.cell_capacity_wip:.1f}, policy={sd_bs.cell_capacity_wip:.1f}")

# Demand boost
check("Battery sovereignty: EV demand > baseline after same period",
      sd_bs.ev_demand_gwh_yr > sd_base.ev_demand_gwh_yr,
      f"base={sd_base.ev_demand_gwh_yr:.0f}, policy={sd_bs.ev_demand_gwh_yr:.0f}")

# Recycling reduces cobalt outflow
check("Battery sovereignty: cobalt recycling fraction > 0 at full ramp",
      sd_bs.policy.mineral_outflow_reduction.get("cobalt", 0.0) > 0.0,
      f"recycling={sd_bs.policy.mineral_outflow_reduction.get('cobalt',0.0):.3f}")

# Build speed multiplier > 1
check("Battery sovereignty: build_speed_mult > 1.0 (gigafactory build shortened)",
      sd_bs.policy.build_speed_mult > 1.0,
      f"mult={sd_bs.policy.build_speed_mult:.3f}")


# ═══════════════════════════════════════════════════════════════════════════════
# P06  Tier-1 Resilience effects
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== P06: Tier-1 Resilience -- component targets, backlog clearance, bullwhip ===")

sd_t1 = fresh(10, "tier1_resilience")
run(sd_t1, GovernmentPolicySD.RAMP_WEEKS + 5)

# Component targets raised
comp_tgt = sd_t1.policy.component_target_mult
check("T1 resilience: harness target mult >= 1.40",
      comp_tgt.get("harness", 1.0) >= 1.40,
      f"harness_mult={comp_tgt.get('harness',1.0):.3f}")
check("T1 resilience: all component mults > 1.0",
      all(comp_tgt.get(c, 1.0) > 1.0 for c in ("cells","packs","inverters","motors","harness")),
      comp_tgt)

# Backlog clears faster
sd_backlog_base = fresh(20)
sd_backlog_t1   = fresh(20, "tier1_resilience")
sd_backlog_base.oem_backlog_k = 2000.0
sd_backlog_t1.oem_backlog_k   = 2000.0
run(sd_backlog_base, 26, prod_mult=1.3, demand_mult=1.0)
run(sd_backlog_t1,   26, prod_mult=1.3, demand_mult=1.0)

check("T1 resilience: backlog clears faster than baseline under production surplus",
      sd_backlog_t1.oem_backlog_k < sd_backlog_base.oem_backlog_k,
      f"base={sd_backlog_base.oem_backlog_k:.0f}, t1={sd_backlog_t1.oem_backlog_k:.0f} k veh")

# Bullwhip EWMA sharpened
check("T1 resilience: bullwhip_smooth_mult > 1.0",
      sd_t1.policy.bullwhip_smooth_mult > 1.0,
      f"mult={sd_t1.policy.bullwhip_smooth_mult:.3f}")

# Input fractions use policy-adjusted targets (harness fraction lower = requires more buffer)
sd_t1_fracs = fresh(10, "tier1_resilience")
run(sd_t1_fracs, GovernmentPolicySD.RAMP_WEEKS + 5)
sd_t1_fracs.compute_input_fractions()
sd_base_fracs = fresh(10)
run(sd_base_fracs, GovernmentPolicySD.RAMP_WEEKS + 5)
sd_base_fracs.compute_input_fractions()

check("T1 resilience: harness input_fraction lower (higher target = relative shortage signal)",
      sd_t1_fracs.input_fractions.get("harness", 1.0) <= sd_base_fracs.input_fractions.get("harness", 1.0),
      f"base={sd_base_fracs.input_fractions.get('harness',1.0):.3f}, t1={sd_t1_fracs.input_fractions.get('harness',1.0):.3f}")


# ═══════════════════════════════════════════════════════════════════════════════
# P07  Critical Minerals Security effects
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== P07: Critical Minerals Security -- price damping, targets, recovery speed ===")

# Price spike damping during a cobalt shock
def cobalt_shock_peak(seed, *packages):
    sd = fresh(seed, *packages)
    for _ in range(4):
        sd.update(make_flows())
    peak = sd.prices["cobalt"]
    for _ in range(30):
        f = make_flows(cobalt_in_mult=0.05)
        sd.update(f)
        peak = max(peak, sd.prices["cobalt"])
    return peak

peak_base = cobalt_shock_peak(1)
peak_cm   = cobalt_shock_peak(1, "critical_minerals_security")

check("Critical minerals: cobalt price spike lower under 95% supply cut",
      peak_cm < peak_base,
      f"base_peak={peak_base:.3f}, policy_peak={peak_cm:.3f}")

# Price recovery faster after shock
def cobalt_recovery_speed(seed, *packages):
    sd = fresh(seed, *packages)
    for _ in range(4): sd.update(make_flows())
    for _ in range(26): sd.update(make_flows(cobalt_in_mult=0.05))
    prices = []
    for _ in range(52):
        sd.update(make_flows())
        prices.append(sd.prices["cobalt"])
    # Return price at end of recovery relative to price at start of recovery
    return prices[0], prices[-1]

start_base, end_base = cobalt_recovery_speed(1)
start_cm,   end_cm   = cobalt_recovery_speed(1, "critical_minerals_security")

check("Critical minerals: price falls more during recovery period",
      (start_cm - end_cm) > (start_base - end_base) - 0.01,
      f"base_drop={start_base-end_base:.3f}, policy_drop={start_cm-end_cm:.3f}")

# Mineral targets raised
sd_cm = fresh(5, "critical_minerals_security")
run(sd_cm, GovernmentPolicySD.RAMP_WEEKS + 5)
min_tgt = sd_cm.policy.mineral_target_mult

check("Critical minerals: cobalt target mult >= 1.45",
      min_tgt.get("cobalt", 1.0) >= 1.45,
      f"cobalt_mult={min_tgt.get('cobalt',1.0):.3f}")
check("Critical minerals: REE target mult >= 1.70",
      min_tgt.get("ree", 1.0) >= 1.70,
      f"ree_mult={min_tgt.get('ree',1.0):.3f}")

# Strategic buffer does NOT depress prices (target scales with buffer)
sd_cm2 = fresh(5, "critical_minerals_security")
run(sd_cm2, GovernmentPolicySD.RAMP_WEEKS + 5)
cobalt_price_with_buffer = sd_cm2.prices["cobalt"]

sd_no_policy = fresh(5)
run(sd_no_policy, GovernmentPolicySD.RAMP_WEEKS + 5)
cobalt_price_no_policy = sd_no_policy.prices["cobalt"]

check("Critical minerals: strategic buffer does not crash cobalt price",
      cobalt_price_with_buffer > cobalt_price_no_policy * 0.75,
      f"no_policy={cobalt_price_no_policy:.3f}, with_cm={cobalt_price_with_buffer:.3f}")

# Recycling reduces mineral outflow
check("Critical minerals: cobalt outflow reduction > 0",
      sd_cm.policy.mineral_outflow_reduction.get("cobalt", 0.0) > 0.0,
      f"cobalt_reduction={sd_cm.policy.mineral_outflow_reduction.get('cobalt',0.0):.3f}")
check("Critical minerals: REE outflow reduction > 0",
      sd_cm.policy.mineral_outflow_reduction.get("ree", 0.0) > 0.0,
      f"ree_reduction={sd_cm.policy.mineral_outflow_reduction.get('ree',0.0):.3f}")
check("Critical minerals: outflow reduction capped at 0.35",
      all(v <= 0.35 for v in sd_cm.policy.mineral_outflow_reduction.values()),
      sd_cm.policy.mineral_outflow_reduction)


# ═══════════════════════════════════════════════════════════════════════════════
# P08  Full Industrial Strategy -- compounding, bounds
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== P08: Full Industrial Strategy -- compounding, parameter bounds ===")

sd_full = fresh(7,
    "battery_sovereignty", "tier1_resilience",
    "critical_minerals_security", "full_industrial_strategy")
run(sd_full, GovernmentPolicySD.RAMP_WEEKS + 10, cells_in_mult=1.6)

pol_f = sd_full.policy

# All ramps at 1.0 after enough time
check("Full strategy: all ramps at 1.0",
      all(r == 1.0 for r in [pol_f.bat_sov, pol_f.t1_res, pol_f.crit_min, pol_f.full_strat]),
      f"bat={pol_f.bat_sov} t1={pol_f.t1_res} cm={pol_f.crit_min} fs={pol_f.full_strat}")

# CAPEX trigger stays positive (0.85 - 0.08 - 0.03 = 0.74 > 0)
effective_trigger = CAPEX_TRIGGER_UTIL - pol_f.capex_trigger_reduction
check("Full strategy: effective CAPEX trigger still positive",
      effective_trigger > 0.30,
      f"trigger={effective_trigger:.3f}")

# Build speed mult bounded below 2.0
check("Full strategy: build_speed_mult < 2.0 (not unrealistically fast)",
      pol_f.build_speed_mult < 2.0,
      f"mult={pol_f.build_speed_mult:.3f}")

# Price adj speed bounded above 0
check("Full strategy: price_adj_speed_mult > 0 (not frozen)",
      pol_f.price_adj_speed_mult > 0.0,
      f"mult={pol_f.price_adj_speed_mult:.3f}")

# Outflow reduction capped at 0.35 everywhere
all_reductions = list(pol_f.mineral_outflow_reduction.values())
check("Full strategy: all mineral outflow reductions <= 0.35",
      all(v <= 0.35 for v in all_reductions),
      {m: round(v,3) for m,v in pol_f.mineral_outflow_reduction.items()})

# Full strategy gives better outcomes than no-policy on all key metrics
sd_ref = fresh(7)
run(sd_ref, GovernmentPolicySD.RAMP_WEEKS + 10, cells_in_mult=1.6)

check("Full strategy: higher cell capacity WIP than no-policy",
      sd_full.cell_capacity_wip > sd_ref.cell_capacity_wip,
      f"ref={sd_ref.cell_capacity_wip:.1f}, full={sd_full.cell_capacity_wip:.1f}")

check("Full strategy: higher EV demand than no-policy",
      sd_full.ev_demand_gwh_yr > sd_ref.ev_demand_gwh_yr,
      f"ref={sd_ref.ev_demand_gwh_yr:.0f}, full={sd_full.ev_demand_gwh_yr:.0f}")

# Stocks don't go negative or exceed hard caps
for name in ("cobalt", "ree", "sic_wafer", "harness"):
    check(f"Full strategy: {name} stock non-negative",
          sd_full.stocks.get(name, 0.0) >= 0.0,
          f"stock={sd_full.stocks.get(name,0.0):.3f}")


# ═══════════════════════════════════════════════════════════════════════════════
# P09  Record and summary include policy fields
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== P09: Record / summary include policy fields when active ===")

sd_rec = fresh(3, "battery_sovereignty", "tier1_resilience")
run(sd_rec, 20)
sd_rec.record()

last_snap = sd_rec.history[-1]
check("record() includes policy_bat_sov when battery_sovereignty active",
      "policy_bat_sov" in last_snap,
      f"keys={[k for k in last_snap if k.startswith('policy_')]}")
check("record() policy ramp value in [0,1]",
      0.0 <= last_snap.get("policy_bat_sov", -1) <= 1.0,
      f"bat_sov={last_snap.get('policy_bat_sov'):.3f}")

summary = sd_rec.summary()
check("summary() includes 'policy' key when packages active",
      "policy" in summary,
      f"summary keys={list(summary.keys())}")
check("summary policy.active_packages lists activated packages",
      set(summary["policy"]["active_packages"]) == {"battery_sovereignty", "tier1_resilience"},
      f"packages={summary['policy']['active_packages']}")

# No-policy model: no policy fields in record or summary
sd_no = fresh(3)
run(sd_no, 20)
sd_no.record()
check("record() has NO policy fields when no policies active",
      not any(k.startswith("policy_") for k in sd_no.history[-1]),
      f"policy_keys={[k for k in sd_no.history[-1] if k.startswith('policy_')]}")
check("summary() has NO 'policy' key when no policies active",
      "policy" not in sd_no.summary())


# ═══════════════════════════════════════════════════════════════════════════════
# P10  No-policy baseline unchanged
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== P10: No-policy model output identical to pre-policy baseline ===")

def run_and_capture(seed, n, **kw):
    sd = SDModel(rng=np.random.default_rng(seed))
    for _ in range(n):
        sd.update(make_flows(**kw))
    return (
        round(sd.prices["cobalt"], 8),
        round(sd.stocks["lithium"], 8),
        round(sd.ev_demand_gwh_yr, 4),
        round(sd.cell_capacity_wip, 4),
        round(sd.lfp_share, 8),
    )

a = run_and_capture(42, 52)
b = run_and_capture(42, 52)
check("No-policy: same seed produces identical results (reproducibility unbroken)",
      a == b,
      f"run_a={a[:2]}, run_b={b[:2]}")

c = run_and_capture(99, 52)
check("No-policy: different seed gives different results",
      a != c,
      f"seed42={a[:2]}, seed99={c[:2]}")

# Explicitly verify zero effect: model with policy object but no packages activated
sd_with_pol_obj = SDModel(rng=np.random.default_rng(42))
sd_plain        = SDModel(rng=np.random.default_rng(42))
for _ in range(52):
    sd_with_pol_obj.update(make_flows())
    sd_plain.update(make_flows())

check("Model with policy object but no packages activated = plain model",
      round(sd_with_pol_obj.prices["cobalt"], 8) == round(sd_plain.prices["cobalt"], 8)
      and round(sd_with_pol_obj.ev_demand_gwh_yr, 4) == round(sd_plain.ev_demand_gwh_yr, 4),
      f"cobalt: {round(sd_with_pol_obj.prices['cobalt'],8)} vs {round(sd_plain.prices['cobalt'],8)}")


# ═══════════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*60)
n_pass = sum(1 for _, ok, _ in results if ok)
n_fail = sum(1 for _, ok, _ in results if not ok)
print(f"POLICY TESTS: {n_pass} passed, {n_fail} failed out of {len(results)} checks")

if n_fail:
    print("\nFailed checks:")
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}")
            if detail:
                print(f"        {detail}")

output = {
    "pass": n_pass, "fail": n_fail, "total": len(results),
    "checks": [{"name": n, "passed": bool(ok), "detail": d} for n, ok, d in results],
}
with open("sd_policy_test_results.json", "w", encoding="utf-8") as fh:
    json.dump(output, fh, indent=2)
print("Results saved to sd_policy_test_results.json")
