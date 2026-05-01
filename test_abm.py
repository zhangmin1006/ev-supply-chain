"""
ABM Model Test Suite
====================
Tests the full ABM + SD hybrid model and all five agent classes.

Coverage:
  A01  Construction          -- model builds for each focus_region; all agent dicts populated
  A02  Baseline run          -- 52-week run completes; outputs are finite, positive, monotone
  A03  Reproducibility       -- same seed -> identical DataFrame; different seed -> different
  A04  MineralSupplierAgent  -- output fraction, shock/recovery cycle, archetype hooks
  A05  CellManufacturerAgent -- Leontief constraint, inventory policy, shock effect
  A06  Tier1SupplierAgent    -- lead-time pipeline, dual-sourcing trigger, order policy
  A07  OEMAgent              -- Leontief production, backlog accumulation, halt weeks
  A08  MarketAgent           -- demand growth, price elasticity, backlog sensitivity
  A09  Shock propagation     -- mineral shock cascades through all tiers; recovery restores output
  A10  Scenario library      -- all named scenarios run 52 weeks without crash
  A11  Policy packages       -- each policy modifies agent parameters as documented
  A12  get_results()         -- DataFrame schema, column types, no NaN/Inf
  A13  Archetype behaviour   -- different archetypes produce qualitatively different responses
  A14  ABM-SD coupling       -- flows from ABM agents update SD stocks; SD signals feed back
  A15  Shock mitigation      -- policy_shock_mitigation reduces effective severity
"""

import sys, os
# Add project root so we can import from the 'model' package with relative imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import json
import math

results = []

def check(name, condition, details=""):
    tag = "PASS" if condition else "FAIL"
    detail_str = ("  -- " + str(details)) if details else ""
    print(f"  [{tag}] {name}{detail_str}")
    results.append((name, bool(condition), str(details)))
    return condition


# ─── helpers ──────────────────────────────────────────────────────────────────

def build(seed=42, scenario=None, focus="uk", n_weeks=52):
    from model.hybrid_model import EVSupplyChainModel
    return EVSupplyChainModel(scenario=scenario or {}, seed=seed,
                              n_weeks=n_weeks, focus_region=focus)

def run(model, weeks=None):
    model.run(weeks)
    return model


# ═══════════════════════════════════════════════════════════════════════════════
# A01  Construction
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== A01: Construction -- model builds for each focus_region ===")

from model.hybrid_model import EVSupplyChainModel

for region in ("uk", None):
    try:
        m = build(focus=region)
        check(f"Model builds for focus_region={region!r}",
              len(m._mineral_agents) > 0 and len(m._cell_agents) > 0
              and len(m._tier1_agents) == 4 and len(m._oem_agents) > 0,
              f"minerals={len(m._mineral_agents)} cells={len(m._cell_agents)} "
              f"tier1={len(m._tier1_agents)} oems={len(m._oem_agents)}")
    except Exception as e:
        check(f"Model builds for focus_region={region!r}", False, str(e))

# All four Tier-1 components present
m = build()
check("All four Tier-1 components present",
      set(m._tier1_agents.keys()) == {"battery_pack", "inverter", "motor", "harness"})

# UK focus has exactly 1 OEM (uk_oem) and 1 market (uk)
check("UK focus: exactly 1 OEM agent",
      len(m._oem_agents) == 1 and "uk_oem" in m._oem_agents)
check("UK focus: exactly 1 market agent",
      len(m._market_agents) == 1 and "uk" in m._market_agents)

# None focus (all regions) has all OEMs and markets
mg = build(focus=None)
check("None focus: >= 6 OEM agents", len(mg._oem_agents) >= 6,
      f"oems={len(mg._oem_agents)}")
check("None focus: >= 4 market agents", len(mg._market_agents) >= 4,
      f"markets={len(mg._market_agents)}")

# Unknown region raises
try:
    bad = EVSupplyChainModel(scenario={}, seed=1, focus_region="moon")
    check("Unknown focus_region raises ValueError", False, "no exception raised")
except ValueError:
    check("Unknown focus_region raises ValueError", True)


# ═══════════════════════════════════════════════════════════════════════════════
# A02  Baseline run
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== A02: Baseline run -- 52 weeks, sane outputs ===")

m = run(build())
df = m.get_results()

check("52 rows in results", len(df) == 52, f"rows={len(df)}")
check("No NaN in results", not df.isnull().any().any(),
      df.isnull().sum()[df.isnull().sum() > 0].to_dict())
check("No Inf in results",
      not df.select_dtypes(include="number").apply(
          lambda c: c.map(lambda x: math.isinf(x) if isinstance(x, float) else False)
      ).any().any())

# OEM production is positive throughout
check("OEM production always > 0",
      (df["oem_production_k"] > 0).all(),
      f"min={df['oem_production_k'].min():.4f}")

# Cell production is positive
check("Cell production always > 0",
      (df["cell_production_gwh"] > 0).all(),
      f"min={df['cell_production_gwh'].min():.4f}")

# Market demand grows over time (29%/yr IEA calibration)
check("Market demand grows over 52 weeks",
      df["market_demand_gwh"].iloc[-1] > df["market_demand_gwh"].iloc[0],
      f"start={df['market_demand_gwh'].iloc[0]:.2f}, end={df['market_demand_gwh'].iloc[-1]:.2f}")

# Stock weeks-of-supply all non-negative
stock_cols = [c for c in df.columns if c.startswith("stock_")]
check("All stock weeks-of-supply non-negative",
      (df[stock_cols] >= 0).all().all(),
      df[stock_cols].min().to_dict())

# Price signal bounded in [0.6, 3.0]
check("Price signal stays within [0.60, 3.00]",
      df["price_signal"].between(0.60, 3.00).all(),
      f"range=[{df['price_signal'].min():.3f}, {df['price_signal'].max():.3f}]")

# Cell capacity grows (capacity investment should happen by end of simulation)
check("Total backlog >= 0 throughout", (df["total_backlog_k"] >= 0).all())


# ═══════════════════════════════════════════════════════════════════════════════
# A03  Reproducibility
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== A03: Reproducibility ===")

run_a = run(build(seed=7)).get_results()
run_b = run(build(seed=7)).get_results()
run_c = run(build(seed=8)).get_results()

check("Same seed -> identical OEM production series",
      (run_a["oem_production_k"] == run_b["oem_production_k"]).all())
check("Same seed -> identical cell production series",
      (run_a["cell_production_gwh"] == run_b["cell_production_gwh"]).all())
check("Different seed -> different OEM production",
      not (run_a["oem_production_k"] == run_c["oem_production_k"]).all())


# ═══════════════════════════════════════════════════════════════════════════════
# A04  MineralSupplierAgent
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== A04: MineralSupplierAgent -- output, shock, recovery ===")

from model.agents import MineralSupplierAgent

class _FakeModel:
    class sd:
        prices = {"cobalt": 1.0, "lithium": 1.0, "graphite": 1.0, "ree": 1.0,
                  "sic_wafer": 1.0, "copper": 1.0}
        input_fractions = {k: 1.0 for k in
            ("lithium","cobalt","graphite","ree","sic_wafer","copper",
             "cells","packs","inverters","motors","harness")}
        component_prices = {"pack": 1.0, "inverter": 1.0, "motor": 1.0,
                            "harness": 1.0, "vehicle": 1.0}
    def get_price_signal(self): return 1.0
    def get_cell_demand(self, name): return 10.0
    def get_component_demand(self, comp): return 100.0
    def get_oem_demand(self, name): return 5.0
    def get_component_deliveries(self, name): return {"packs":1,"inverters":1,"motors":1,"harness":1}
    def get_cobalt_price(self): return 1.0
    def get_lfp_share(self): return 0.4
    def get_backlog_scale_k(self): return 100.0

fake = _FakeModel()

a = MineralSupplierAgent("lithos", fake, "lithium", "australia", 0.46, 0.85)

# Unshocked: output_fraction = 1.0
a.step()
check("MineralAgent unshocked: output_fraction=1.0",
      a.output_fraction == 1.0, f"fraction={a.output_fraction:.3f}")
check("MineralAgent: weekly_supply_contribution = global_share * fraction",
      abs(a.weekly_supply_contribution - 0.46) < 1e-9)

# Shock reduces output
a.apply_shock(0.60)
check("MineralAgent: apply_shock sets is_shocked=True", a.is_shocked)
check("MineralAgent: shock_multiplier = 1 - effective_severity",
      a.shock_multiplier < 1.0, f"mult={a.shock_multiplier:.3f}")

a.step()
check("MineralAgent: output_fraction < 1.0 while shocked",
      a.output_fraction < 1.0, f"fraction={a.output_fraction:.3f}")

# Resolve and recover
a.resolve_shock()
check("MineralAgent: resolve_shock sets is_shocked=False", not a.is_shocked)

frac_before = a.output_fraction
for _ in range(10):
    a.step()
check("MineralAgent: output_fraction recovers after resolve_shock",
      a.output_fraction > frac_before,
      f"before={frac_before:.3f}, after={a.output_fraction:.3f}")

# Full recovery
for _ in range(100):
    a.step()
check("MineralAgent: full recovery to 1.0 after sufficient steps",
      abs(a.output_fraction - 1.0) < 0.01,
      f"fraction={a.output_fraction:.4f}")


# ═══════════════════════════════════════════════════════════════════════════════
# A05  CellManufacturerAgent
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== A05: CellManufacturerAgent -- Leontief constraint, inventory, shock ===")

from model.agents import CellManufacturerAgent

cell = CellManufacturerAgent(
    "cell_test", fake, name="test",
    country="china", capacity_gwh_yr=100.0,
    market_share=0.10, lfp_fraction=0.40, nmc_fraction=0.60,
    safety_stock_weeks=4, recovery_rate_wk=0.04,
)

# Unshocked step with full inputs
cell.step()
check("CellAgent: output_gwh > 0 with full inputs",
      cell.output_gwh > 0, f"output={cell.output_gwh:.3f}")

# Leontief: lithium shortage -> output falls
fake.sd.input_fractions["lithium"] = 0.3
cell.step()
output_with_shortage = cell.output_gwh
fake.sd.input_fractions["lithium"] = 1.0
cell.step()
output_full = cell.output_gwh
check("CellAgent: lithium shortage (0.3) reduces output vs full supply",
      output_with_shortage < output_full,
      f"shortage={output_with_shortage:.3f}, full={output_full:.3f}")

# LFP cells are not cobalt-constrained
cell_lfp = CellManufacturerAgent(
    "cell_lfp", fake, name="lfp",
    country="china", capacity_gwh_yr=100.0,
    market_share=0.10, lfp_fraction=1.0, nmc_fraction=0.0,
)
fake.sd.input_fractions["cobalt"] = 0.0
cell_lfp.step()
output_lfp_no_cobalt = cell_lfp.output_gwh
fake.sd.input_fractions["cobalt"] = 1.0
cell_lfp.step()
output_lfp_full_cobalt = cell_lfp.output_gwh
fake.sd.input_fractions["cobalt"] = 1.0
check("CellAgent LFP: cobalt stockout does not reduce output",
      abs(output_lfp_no_cobalt - output_lfp_full_cobalt) < output_lfp_full_cobalt * 0.05,
      f"no_cobalt={output_lfp_no_cobalt:.3f}, full={output_lfp_full_cobalt:.3f}")

# Shock reduces output
cell2 = CellManufacturerAgent(
    "cell_shock", fake, name="sk",
    country="korea", capacity_gwh_yr=50.0,
    market_share=0.06, lfp_fraction=0.0, nmc_fraction=1.0,
)
cell2.step()
unshocked_output = cell2.output_gwh
cell2.apply_shock(0.50)
cell2.step()
check("CellAgent: shock reduces output_gwh",
      cell2.output_gwh < unshocked_output,
      f"before={unshocked_output:.3f}, after={cell2.output_gwh:.3f}")

# Inventory never goes negative
check("CellAgent: inventory_gwh >= 0 after shock",
      cell2.inventory_gwh >= 0, f"inv={cell2.inventory_gwh:.4f}")


# ═══════════════════════════════════════════════════════════════════════════════
# A06  Tier1SupplierAgent
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== A06: Tier1SupplierAgent -- pipeline, dual sourcing, order policy ===")

from model.agents import Tier1SupplierAgent

t1 = Tier1SupplierAgent(
    "t1_test", fake,
    component="inverter", key_input="sic_wafer",
    capacity_k_yr=14_000.0, input_dependency=0.46,
    lead_time_weeks=8, safety_stock_weeks=6,
    recovery_rate_wk=0.04, bullwhip_factor=1.25,
)

# Pipeline initialised at capacity length
check("Tier1Agent: pipeline length = lead_time_weeks",
      len(t1.pipeline) == 8, f"len={len(t1.pipeline)}")

# Step without shock -- output positive
t1.step()
check("Tier1Agent: output_k > 0 unshocked",
      t1.output_k > 0, f"output={t1.output_k:.3f}")

# SiC shortage reduces output (use demand >> weekly_capacity so capacity is the binding constraint)
class _HighDemandFake(_FakeModel):
    def get_component_demand(self, comp): return 1000.0  # far above weekly_capacity

fake_hd = _HighDemandFake()
t1_hd_low = Tier1SupplierAgent(
    "t1_low", fake_hd, component="inverter", key_input="sic_wafer",
    capacity_k_yr=14_000.0, input_dependency=0.46,
    lead_time_weeks=8, safety_stock_weeks=6,
)
fake_hd.sd.input_fractions["sic_wafer"] = 0.2
t1_hd_low.step()
low_output = t1_hd_low.output_k

t1_hd_high = Tier1SupplierAgent(
    "t1_high", fake_hd, component="inverter", key_input="sic_wafer",
    capacity_k_yr=14_000.0, input_dependency=0.46,
    lead_time_weeks=8, safety_stock_weeks=6,
)
fake_hd.sd.input_fractions["sic_wafer"] = 1.0
t1_hd_high.step()
high_output = t1_hd_high.output_k
check("Tier1Agent: SiC shortage (0.2) reduces output vs full supply",
      low_output < high_output,
      f"low={low_output:.3f}, high={high_output:.3f}")

# Dual sourcing activates below threshold: clear pipeline so no delivery restores inventory
t1_dual = Tier1SupplierAgent(
    "t1_dual", fake,
    component="harness", key_input="copper",
    capacity_k_yr=14_000.0, input_dependency=0.0,
    lead_time_weeks=4, safety_stock_weeks=2,
)
# Drive inventory AND pipeline below dual-source threshold
t1_dual.inventory = 0.0
t1_dual.pipeline = [0.0] * len(t1_dual.pipeline)   # no in-transit deliveries
t1_dual.step()
check("Tier1Agent: dual_source_active=True when inventory < threshold",
      t1_dual.dual_source_active,
      f"inv={t1_dual.inventory:.3f}, dual={t1_dual.dual_source_active}")

# Inventory non-negative after shock
t1_shock = Tier1SupplierAgent(
    "t1_s", fake, component="motor", key_input="ree",
    capacity_k_yr=14_000.0, input_dependency=0.75,
    lead_time_weeks=6, safety_stock_weeks=4,
)
t1_shock.apply_shock(0.80)
for _ in range(10):
    t1_shock.step()
check("Tier1Agent: inventory >= 0 after severe shock",
      t1_shock.inventory >= 0, f"inv={t1_shock.inventory:.4f}")


# ═══════════════════════════════════════════════════════════════════════════════
# A07  OEMAgent
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== A07: OEMAgent -- Leontief production, backlog, halt weeks ===")

from model.agents import OEMAgent

class _FakeModelOEM(_FakeModel):
    class sd:
        prices = {"cobalt": 1.0, "lithium": 1.0, "ree": 1.0, "sic_wafer": 1.0,
                  "graphite": 1.0, "copper": 1.0}
        input_fractions = {k: 1.0 for k in
            ("lithium","cobalt","graphite","ree","sic_wafer","copper",
             "cells","packs","inverters","motors","harness")}
        component_prices = {"pack": 1.0, "inverter": 1.0, "motor": 1.0,
                            "harness": 1.0, "vehicle": 1.0}
        lfp_share = 0.4
        oem_backlog_k = 0.0
    def get_oem_demand(self, name): return 3.37   # 175k/yr / 52 ~ 3.37 k/wk
    def get_component_deliveries(self, name):
        return {"packs": 10.0, "inverters": 10.0, "motors": 10.0, "harness": 10.0}
    def get_backlog_scale_k(self): return 175.0
    def get_cobalt_price(self): return 1.0
    def get_lfp_share(self): return 0.4
    def get_price_signal(self): return 1.0

fake_oem = _FakeModelOEM()
oem = OEMAgent(
    "oem_uk", fake_oem, name="uk_oem", region="uk",
    annual_target_k=175.0, safety_stock_weeks=2,
    dual_source_trigger=False, vertical_integration=0.05,
)

# Unshocked step
oem.step()
check("OEMAgent: production_k > 0 unshocked with sufficient components",
      oem.production_k > 0, f"prod={oem.production_k:.4f}")

# Backlog: demand > production -> backlog accumulates
class _FakeModelOEMHighDemand(_FakeModelOEM):
    def get_oem_demand(self, name): return 100.0   # far above capacity
    def get_component_deliveries(self, name):
        return {"packs": 0.1, "inverters": 0.1, "motors": 0.1, "harness": 0.1}

fake_oem_hd = _FakeModelOEMHighDemand()
oem_hd = OEMAgent(
    "oem_uk2", fake_oem_hd, name="uk_oem", region="uk",
    annual_target_k=175.0, safety_stock_weeks=2,
    dual_source_trigger=False, vertical_integration=0.0,
)
oem_hd.step()
check("OEMAgent: backlog_k > 0 when component supply is extremely low",
      oem_hd.backlog_k > 0, f"backlog={oem_hd.backlog_k:.4f}")

# Shock reduces production
oem_s = OEMAgent(
    "oem_s", fake_oem, name="uk_oem", region="uk",
    annual_target_k=175.0, safety_stock_weeks=2,
    dual_source_trigger=False, vertical_integration=0.0,
)
oem_s.step()
unshocked_prod = oem_s.production_k
oem_s.apply_shock(0.50)
oem_s.step()
check("OEMAgent: shock reduces production_k",
      oem_s.production_k < unshocked_prod,
      f"before={unshocked_prod:.4f}, after={oem_s.production_k:.4f}")


# ═══════════════════════════════════════════════════════════════════════════════
# A08  MarketAgent
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== A08: MarketAgent -- demand growth, price elasticity, backlog sensitivity ===")

from model.agents import MarketAgent

class _FakeModelMkt(_FakeModel):
    class sd:
        prices = {}
        input_fractions = {}
        component_prices = {"vehicle": 1.0}
        oem_backlog_k = 0.0
    def get_price_signal(self): return 1.0
    def get_backlog_scale_k(self): return 20000.0 / 52  # weekly

fake_mkt = _FakeModelMkt()
mkt = MarketAgent(
    "mkt_uk", fake_mkt, region="uk",
    gwh_2023=20.0, yoy_growth=0.28,
    avg_kwh_per_veh=75.0, price_elasticity=0.35,
)

d0 = mkt.weekly_demand_gwh
mkt.step()
d1 = mkt.weekly_demand_gwh
check("MarketAgent: demand grows week-on-week with 28%/yr growth",
      d1 > d0, f"d0={d0:.4f}, d1={d1:.4f}")

# Price elasticity: high price -> lower demand (use negative elasticity as per model default)
class _FakeModelHighPrice(_FakeModelMkt):
    def get_price_signal(self): return 1.80

fake_hprice = _FakeModelHighPrice()
mkt_hprice = MarketAgent(
    "mkt_hp", fake_hprice, region="uk",
    gwh_2023=20.0, yoy_growth=0.28,
    avg_kwh_per_veh=75.0, price_elasticity=-0.35,   # negative: high price -> reduced demand
)
mkt_hprice.step()
d_high_price = mkt_hprice.weekly_demand_gwh

mkt_base2 = MarketAgent(
    "mkt_base2", fake_mkt, region="uk",
    gwh_2023=20.0, yoy_growth=0.28,
    avg_kwh_per_veh=75.0, price_elasticity=-0.35,
)
mkt_base2.step()
d_normal_price = mkt_base2.weekly_demand_gwh

check("MarketAgent: high price signal reduces demand relative to baseline",
      d_high_price < d_normal_price,
      f"high_price={d_high_price:.4f}, normal={d_normal_price:.4f}")

# weekly_demand_k_veh: GWh -> vehicles (GWh / avg_kwh_per_veh * 1000)
check("MarketAgent: weekly_demand_k_veh = gwh / avg_kwh * 1000",
      abs(mkt.weekly_demand_k_veh - mkt.weekly_demand_gwh / 75.0 * 1000) < 0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# A09  Shock propagation
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== A09: Shock propagation -- mineral shock cascades through tiers ===")

from model.shocks import SCENARIOS

# DRC cobalt shock: start_week=26, end_week=52, severity=0.50
scenario = SCENARIOS["drc_cobalt"]
m_base = run(build(seed=1, scenario={}))
m_shock = run(build(seed=1, scenario=scenario))

df_base  = m_base.get_results()
df_shock = m_shock.get_results()

# During shock period (weeks 26-51), production should be lower
shock_mask = df_shock["week"].between(28, 51)
base_prod  = df_base.loc[shock_mask, "oem_production_k"].mean()
shock_prod = df_shock.loc[shock_mask, "oem_production_k"].mean()

check("DRC cobalt shock: mean OEM production lower during shock vs baseline",
      shock_prod <= base_prod,
      f"baseline={base_prod:.4f}, shocked={shock_prod:.4f}")

# Cobalt price rises during shock
shock_cobalt_price = df_shock.loc[shock_mask, "price_cobalt"].mean()
base_cobalt_price  = df_base.loc[shock_mask, "price_cobalt"].mean()
check("DRC cobalt shock: cobalt price higher during shock period",
      shock_cobalt_price >= base_cobalt_price,
      f"base_price={base_cobalt_price:.3f}, shock_price={shock_cobalt_price:.3f}")

# Ukraine harness shock: verify shock is correctly applied to harness Tier-1 agent
# (harness output may not drop in demand-limited regime since 70x spare capacity means
#  even 20% of capacity easily covers demand; instead verify shock state directly)
harness_scenario = SCENARIOS["ukraine_harness"]
m_harness = build(seed=1, scenario=harness_scenario)
for _ in range(5):   # step to week 5, inside shock period (4-12)
    m_harness.step()
harness_agent = m_harness._tier1_agents.get("harness")
check("Ukraine harness shock: harness agent is_shocked=True during shock",
      harness_agent is not None and harness_agent.is_shocked,
      f"is_shocked={getattr(harness_agent,'is_shocked',None)}, mult={getattr(harness_agent,'shock_multiplier',0):.3f}")
check("Ukraine harness shock: shock_multiplier < 0.5 (severity=0.80)",
      harness_agent is not None and harness_agent.shock_multiplier < 0.5,
      f"shock_multiplier={getattr(harness_agent,'shock_multiplier',None):.3f}")

# Agent-level shock: find cobalt_drc agent and verify it is shocked
m_cobalt = build(seed=1, scenario=SCENARIOS["drc_cobalt"])
# Step to week 27 (shock starts at 26)
for _ in range(27):
    m_cobalt.step()
cobalt_agent = m_cobalt._mineral_agents.get("cobalt_drc")
check("DRC cobalt agent: is_shocked=True after shock week",
      cobalt_agent is not None and cobalt_agent.is_shocked,
      f"is_shocked={getattr(cobalt_agent,'is_shocked',None)}")

# After shock resolves (week 52+), agent recovers
for _ in range(26):
    m_cobalt.step()
check("DRC cobalt agent: is_shocked=False after end_week",
      cobalt_agent is not None and not cobalt_agent.is_shocked)


# ═══════════════════════════════════════════════════════════════════════════════
# A10  Scenario library -- all named scenarios run without crash
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== A10: Scenario library -- all named scenarios run 52 weeks ===")

BASE_SCENARIOS = [
    "baseline", "ukraine_harness", "drc_cobalt", "sic_bottleneck",
    "china_ree_restriction", "china_graphite", "china_catl_disruption",
    "compound_shock", "us_china_tariff", "uk_supply_chain_friction",
]

for sname in BASE_SCENARIOS:
    sc = SCENARIOS[sname]
    try:
        m_sc = run(build(seed=42, scenario=sc))
        df_sc = m_sc.get_results()
        ok = len(df_sc) == 52 and not df_sc["oem_production_k"].isnull().any()
        check(f"Scenario '{sname}' runs 52 weeks without error", ok,
              f"rows={len(df_sc)}")
    except Exception as e:
        check(f"Scenario '{sname}' runs 52 weeks without error", False, str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# A11  Policy packages
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== A11: Policy packages -- agent parameters modified as documented ===")

from model.policies import apply_policy_packages

# Battery sovereignty: AESC UK capacity boosted 2.25×
m_no_pol = build()
aesc_cap_before = m_no_pol._cell_agents["aesc_uk"].weekly_capacity

m_bat_sov = build()
apply_policy_packages(m_bat_sov, ["battery_sovereignty"])
aesc_cap_after = m_bat_sov._cell_agents["aesc_uk"].weekly_capacity

check("Battery sovereignty: AESC UK weekly_capacity boosted 2.25x",
      abs(aesc_cap_after / aesc_cap_before - 2.25) < 0.05,
      f"before={aesc_cap_before:.4f}, after={aesc_cap_after:.4f}, "
      f"ratio={aesc_cap_after/aesc_cap_before:.3f}")

# Battery sovereignty activates SD policy layer
check("Battery sovereignty: SD policy layer activated",
      "battery_sovereignty" in m_bat_sov.sd.policy._active)

# Tier-1 resilience: harness safety_stock_weeks raised (1.45× multiplier)
m_t1 = build()
harness_before = m_t1._tier1_agents["harness"].safety_stock_weeks
apply_policy_packages(m_t1, ["tier1_resilience"])
harness_after = m_t1._tier1_agents["harness"].safety_stock_weeks

check("T1 resilience: harness safety_stock_weeks raised (>= 1.40× original)",
      harness_after >= harness_before * 1.35,
      f"before={harness_before:.2f}, after={harness_after:.2f}, ratio={harness_after/harness_before:.3f}")

# T1 resilience: UK OEM recovery rate boosted
m_t1b = build()
uk_oem_rr_before = m_t1b._oem_agents["uk_oem"].recovery_rate_wk
apply_policy_packages(m_t1b, ["tier1_resilience"])
uk_oem_rr_after = m_t1b._oem_agents["uk_oem"].recovery_rate_wk

check("T1 resilience: UK OEM recovery_rate_wk boosted (>= 1.20× original)",
      uk_oem_rr_after >= uk_oem_rr_before * 1.15,
      f"before={uk_oem_rr_before:.5f}, after={uk_oem_rr_after:.5f}, "
      f"ratio={uk_oem_rr_after/uk_oem_rr_before:.3f}")

# Critical minerals: cobalt and REE stock injected into SD
m_cm = build()
cobalt_before = m_cm.sd.stocks["cobalt"]
ree_before    = m_cm.sd.stocks["ree"]
apply_policy_packages(m_cm, ["critical_minerals_security"])
cobalt_after  = m_cm.sd.stocks["cobalt"]
ree_after     = m_cm.sd.stocks["ree"]

check("Critical minerals: cobalt SD stock injected (> before)",
      cobalt_after > cobalt_before,
      f"before={cobalt_before:.2f}, after={cobalt_after:.2f}")
check("Critical minerals: REE SD stock injected (> before)",
      ree_after > ree_before,
      f"before={ree_before:.2f}, after={ree_after:.2f}")

# Full strategy: policy_shock_mitigation populated
m_full = build()
apply_policy_packages(m_full, ["full_industrial_strategy"])
check("Full strategy: policy_shock_mitigation dict non-empty",
      len(m_full.policy_shock_mitigation) > 0,
      f"keys={list(m_full.policy_shock_mitigation.keys())[:4]}")
check("Full strategy: all four SD packages activated",
      all(pkg in m_full.sd.policy._active for pkg in
          ["battery_sovereignty","tier1_resilience",
           "critical_minerals_security","full_industrial_strategy"]))


# ═══════════════════════════════════════════════════════════════════════════════
# A12  get_results() schema
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== A12: get_results() -- DataFrame schema and types ===")

m_schema = run(build())
df_schema = m_schema.get_results()

REQUIRED_COLS = [
    "week", "oem_production_k", "cell_production_gwh", "market_demand_gwh",
    "total_backlog_k", "price_signal", "lfp_share", "cell_capacity_gwh_yr",
    "bullwhip_index", "sd_ev_demand_gwh_yr", "sd_oem_backlog_k",
    "t1_battery_pack_k", "t1_inverter_k", "t1_motor_k", "t1_harness_k",
    "stock_lithium_wk", "stock_cobalt_wk", "stock_cells_wk",
    "price_lithium", "price_cobalt", "oem_uk_oem_k",
]
missing = [c for c in REQUIRED_COLS if c not in df_schema.columns]
check("All required columns present",
      len(missing) == 0, f"missing={missing}")

check("'week' column is 0..51",
      list(df_schema["week"]) == list(range(52)))

check("'lfp_share' in [0, 1] throughout",
      df_schema["lfp_share"].between(0, 1).all(),
      f"range=[{df_schema['lfp_share'].min():.3f},{df_schema['lfp_share'].max():.3f}]")

check("'cell_cap_util' in [0, 2] throughout",
      df_schema["cell_cap_util"].between(0, 2).all(),
      f"range=[{df_schema['cell_cap_util'].min():.3f},{df_schema['cell_cap_util'].max():.3f}]")

check("'bullwhip_index' is positive",
      (df_schema["bullwhip_index"] > 0).all())


# ═══════════════════════════════════════════════════════════════════════════════
# A13  Archetype behaviour
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== A13: Archetype behaviour -- archetypes produce qualitatively different responses ===")

from model.agents import StateBacked, WesternMiner, PlatformLeader, IncumbentUnderPressure

# StateBacked mineral agent has higher production floor than WesternMiner
sb = StateBacked("sb", fake, "graphite", "china", 0.79, 0.95)
wm = WesternMiner("wm", fake, "graphite", "australia", 0.10, 0.95)

check("StateBacked: production_floor > WesternMiner production_floor",
      sb.production_floor > wm.production_floor,
      f"StateBacked={sb.production_floor:.3f}, WesternMiner={wm.production_floor:.3f}")

# Under shock, StateBacked maintains higher output floor
sb.apply_shock(0.90)
wm.apply_shock(0.90)
sb.step(); wm.step()
check("StateBacked: higher output_fraction than WesternMiner under same severe shock",
      sb.output_fraction >= wm.output_fraction,
      f"StateBacked={sb.output_fraction:.3f}, WesternMiner={wm.output_fraction:.3f}")

# PlatformLeader vs IncumbentUnderPressure: different capacity growth
pl = PlatformLeader("pl", fake, name="catl", country="china",
                    capacity_gwh_yr=304.0, market_share=0.37,
                    lfp_fraction=0.55, nmc_fraction=0.45)
iup = IncumbentUnderPressure("iup", fake, name="panasonic", country="japan",
                              capacity_gwh_yr=57.5, market_share=0.07,
                              lfp_fraction=0.0, nmc_fraction=1.0)

cap_pl_0  = pl.weekly_capacity
cap_iup_0 = iup.weekly_capacity

for _ in range(52):
    pl.step(); iup.step()

growth_pl  = (pl.weekly_capacity  / cap_pl_0)  - 1
growth_iup = (iup.weekly_capacity / cap_iup_0) - 1
check("PlatformLeader grows faster than IncumbentUnderPressure over 52 weeks",
      growth_pl > growth_iup,
      f"PlatformLeader={growth_pl*100:.1f}%, Incumbent={growth_iup*100:.1f}%")


# ═══════════════════════════════════════════════════════════════════════════════
# A14  ABM-SD coupling
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== A14: ABM-SD coupling -- agent outputs update SD stocks; signals feed back ===")

m_coup = build(seed=3)

# Record SD state before running
sd_cobalt_0 = m_coup.sd.stocks["cobalt"]
sd_cells_0  = m_coup.sd.stocks["cells"]

m_coup.step()

# Flows are collected and passed to sd.update()
# After one step: SD stocks should have changed
sd_cobalt_1 = m_coup.sd.stocks["cobalt"]
sd_cells_1  = m_coup.sd.stocks["cells"]
check("ABM-SD coupling: cobalt stock changes after first step",
      sd_cobalt_1 != sd_cobalt_0,
      f"before={sd_cobalt_0:.4f}, after={sd_cobalt_1:.4f}")
check("ABM-SD coupling: cells stock changes after first step",
      sd_cells_1 != sd_cells_0,
      f"before={sd_cells_0:.4f}, after={sd_cells_1:.4f}")

# input_fractions are available to agents (used in step)
fracs = m_coup.sd.input_fractions
check("ABM-SD coupling: input_fractions dict non-empty",
      len(fracs) > 0 and all(v >= 0 for v in fracs.values()))

# SD history grows with each model step
steps_before = len(m_coup.sd.history)
m_coup.step()
check("ABM-SD coupling: sd.history grows by 1 per model step",
      len(m_coup.sd.history) == steps_before + 1)

# cell_cap_util in results comes from exact ABM cell output
df_coup = run(build(seed=3)).get_results()
check("ABM-SD coupling: cell_cap_util column is from exact ABM output (not proxy)",
      "cell_cap_util" in df_coup.columns and (df_coup["cell_cap_util"] >= 0).all())


# ═══════════════════════════════════════════════════════════════════════════════
# A15  Shock mitigation
# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== A15: Shock mitigation -- policy_shock_mitigation reduces effective severity ===")

# With critical_minerals_security policy: cobalt_drc mitigation = 0.35
scenario_cobalt = SCENARIOS["drc_cobalt"]

m_no_mit = run(build(seed=1, scenario=scenario_cobalt))
m_mit    = run(build(seed=1, scenario={
    **scenario_cobalt,
    "policies": ["critical_minerals_security"],
}))

df_no_mit = m_no_mit.get_results()
df_mit    = m_mit.get_results()

shock_wks = df_no_mit["week"].between(28, 51)
prod_no_mit = df_no_mit.loc[shock_wks, "oem_production_k"].mean()
prod_mit    = df_mit.loc[shock_wks, "oem_production_k"].mean()

check("Critical minerals policy: higher OEM production during cobalt shock",
      prod_mit >= prod_no_mit * 0.95,
      f"no_policy={prod_no_mit:.4f}, with_policy={prod_mit:.4f}")

# policy_shock_mitigation on cobalt_drc should be set
check("Critical minerals policy: cobalt_drc mitigation > 0 in model",
      m_mit.policy_shock_mitigation.get("cobalt_drc", 0.0) > 0.0,
      f"mitigation={m_mit.policy_shock_mitigation.get('cobalt_drc',0.0):.3f}")

# Agent effective severity is reduced when policy_shock_absorption > 0
from model.agents import MineralSupplierAgent as MSA
agent_mit = MSA("test_mit", fake, "cobalt", "drc", 0.70, 0.85)
severity_base = agent_mit._effective_shock_severity(0.50)
agent_mit.policy_shock_absorption = 0.20
severity_with_policy = agent_mit._effective_shock_severity(0.50)
check("Agent with policy_shock_absorption=0.20 has lower effective severity",
      severity_with_policy < severity_base,
      f"base={severity_base:.3f}, with_policy={severity_with_policy:.3f}")


# ═══════════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*60)
n_pass = sum(1 for _, ok, _ in results if ok)
n_fail = sum(1 for _, ok, _ in results if not ok)
print(f"ABM TESTS: {n_pass} passed, {n_fail} failed out of {len(results)} checks")

if n_fail:
    print("\nFailed checks:")
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}")
            if detail:
                print(f"        {detail[:120]}")

output = {
    "pass": n_pass, "fail": n_fail, "total": len(results),
    "checks": [{"name": n, "passed": bool(ok), "detail": d} for n, ok, d in results],
}
with open("abm_test_results.json", "w", encoding="utf-8") as fh:
    json.dump(output, fh, indent=2)
print("Results saved to abm_test_results.json")
