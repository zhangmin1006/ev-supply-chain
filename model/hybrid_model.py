"""
Hybrid ABM + SD Model
=====================
Integrates the System Dynamics layer and all ABM agents into one
executable simulation.

Step execution order each week
-------------------------------
  1. Apply any shocks scheduled for this week.
  2. SD → agents: compute input_fractions from current stocks.
  3. Mineral supplier agents step  → report supply fractions.
  4. Cell manufacturer agents step → report GWh produced.
  5. Tier-1 supplier agents step   → report k-units produced.
  6. OEM agents step               → report k vehicles assembled.
  7. Market agents step            → update demand.
  8. Aggregate all outputs into SD flows → update SD stocks.
  9. Record metrics.

Public interface
----------------
  model = EVSupplyChainModel(scenario=None)
  model.run(n_weeks=260)
  results = model.get_results()   # returns pd.DataFrame
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any

from .config import (
    SIM, MINERALS, CELL_MAKERS, TIER1, OEMS, MARKETS,
    CELL_GLOBAL_GWH_2023, EV_GLOBAL_UNITS_2023_K,
    DEMAND_PRICE_ELASTICITY, BULLWHIP_FACTOR,
    MINERAL_AGENT_ARCHETYPES, CELL_AGENT_ARCHETYPES,
    TIER1_AGENT_ARCHETYPES, OEM_AGENT_ARCHETYPES,
)
from .sd_model import SDModel, BASELINE_WK
from .financial_profiles import profile_for_agent, coverage_report, FOUR_TIER_AGENT_GROUPS
from .data_source_calibration import apply_data_source_calibration
from .agents import (
    MineralSupplierAgent, CellManufacturerAgent,
    Tier1SupplierAgent, OEMAgent, MarketAgent,
    # Tier 1 archetypes
    StateBacked, WesternMiner, GreenfieldBuilder,
    # Tier 2 archetypes
    PlatformLeader, HyperScaleChallenger, IncumbentUnderPressure,
    # Tier 3 archetypes
    PremiumPowerElectronics, EstablishedVolumeSupplier, BatteryPackIntegrator,
    # Tier 4 archetypes
    ProfitableEstablishedOEM, TransitioningLegacyOEM,
    EVNativeScaleAspirant, PrecommercialStartup,
)

# ── Archetype class registries ────────────────────────────────────────────────
_MINERAL_ARCHETYPE_CLASSES = {
    "StateBacked":      StateBacked,
    "WesternMiner":     WesternMiner,
    "GreenfieldBuilder":GreenfieldBuilder,
}
_CELL_ARCHETYPE_CLASSES = {
    "PlatformLeader":          PlatformLeader,
    "HyperScaleChallenger":    HyperScaleChallenger,
    "IncumbentUnderPressure":  IncumbentUnderPressure,
}
_TIER1_ARCHETYPE_CLASSES = {
    "PremiumPowerElectronics":  PremiumPowerElectronics,
    "EstablishedVolumeSupplier":EstablishedVolumeSupplier,
    "BatteryPackIntegrator":    BatteryPackIntegrator,
}
_OEM_ARCHETYPE_CLASSES = {
    "ProfitableEstablishedOEM": ProfitableEstablishedOEM,
    "TransitioningLegacyOEM":   TransitioningLegacyOEM,
    "EVNativeScaleAspirant":    EVNativeScaleAspirant,
    "PrecommercialStartup":     PrecommercialStartup,
}


# ── Mineral supplier configuration (derived from config.MINERALS) ─────────────
_MINERAL_SOURCES = {
    "lithium": [
        ("lithium_aus",   "australia", 0.46),
        ("lithium_chl",   "chile",     0.30),
        ("lithium_chn",   "china",     0.14),
        ("lithium_other", "others",    0.10),
    ],
    "cobalt": [
        ("cobalt_drc",   "drc",       0.70),
        ("cobalt_other", "others",    0.30),
    ],
    "graphite": [
        ("graphite_chn",   "china",   0.79),
        ("graphite_other", "others",  0.21),
    ],
    "ree": [
        ("ree_chn",   "china",  0.85),
        ("ree_other", "others", 0.15),
    ],
    "sic_wafer": [
        ("sic_wolfspeed", "usa",   0.30),
        ("sic_coherent",  "usa",   0.20),
        ("sic_china",     "china", 0.18),
        ("sic_other",     "eu",    0.32),
    ],
}


class EVSupplyChainModel:
    """
    Main simulation model.  Instantiate once per scenario, call run().
    """

    def __init__(self,
                 scenario: Optional[Dict[str, Any]] = None,
                 seed: int = 42,
                 n_weeks: int = 260,
                 focus_region: Optional[str] = "uk"):
        self.rng      = np.random.default_rng(seed)
        self.scenario = scenario or {}
        self.n_weeks  = n_weeks
        self.week     = 0
        self.focus_region = focus_region
        self._active_oem_names = [
            name for name, cfg in OEMS.items()
            if focus_region is None or cfg["region"] == focus_region
        ]
        self._active_market_regions = [
            region for region in MARKETS
            if focus_region is None or region == focus_region
        ]
        if not self._active_oem_names:
            raise ValueError(f"No OEM agents configured for focus_region={focus_region!r}")
        if not self._active_market_regions:
            raise ValueError(f"No market agents configured for focus_region={focus_region!r}")
        self.active_oem_target_k = sum(
            OEMS[name]["annual_target_k"] for name in self._active_oem_names
        )
        self.active_market_gwh_2023 = sum(
            MARKETS[region]["gwh_2023"] for region in self._active_market_regions
        )
        self.active_market_units_2023_k = sum(
            MARKETS[region]["gwh_2023"] * 1000.0 / MARKETS[region]["avg_kwh_veh"]
            for region in self._active_market_regions
        )
        self.market_scope_fraction = (
            1.0 if focus_region is None
            else self.active_market_gwh_2023 / max(CELL_GLOBAL_GWH_2023, 1e-9)
        )

        # ── Layers ──────────────────────────────────────────────────────────
        self.sd = SDModel(rng=self.rng)

        # ── Build agents ─────────────────────────────────────────────────────
        self._mineral_agents:  Dict[str, MineralSupplierAgent]  = {}
        self._cell_agents:     Dict[str, CellManufacturerAgent] = {}
        self._tier1_agents:    Dict[str, Tier1SupplierAgent]    = {}
        self._oem_agents:      Dict[str, OEMAgent]              = {}
        self._market_agents:   Dict[str, MarketAgent]           = {}

        self._build_mineral_agents()
        self._build_cell_agents()
        self._build_tier1_agents()
        self._build_oem_agents()
        self._build_market_agents()

        # ── Shock schedule {week: [shock_dict, …]} ───────────────────────────
        self._shock_schedule: Dict[int, List[Dict]] = {}
        self._active_shocks:  Dict[str, Dict]       = {}
        if scenario:
            self._load_shocks(scenario.get("shocks", []))

        # ── Metrics records ──────────────────────────────────────────────────
        self._records: List[Dict] = []

    # =========================================================================
    # Agent construction
    # =========================================================================

    def _build_mineral_agents(self) -> None:
        for mineral, sources in _MINERAL_SOURCES.items():
            ev_share = MINERALS[mineral]["ev_share"]
            safety   = MINERALS[mineral]["safety_stock_weeks"]
            for aid, country, gshare in sources:
                archetype_name = MINERAL_AGENT_ARCHETYPES.get(aid)
                cls = _MINERAL_ARCHETYPE_CLASSES.get(archetype_name, MineralSupplierAgent)
                if cls is MineralSupplierAgent:
                    a = cls(
                        agent_id=aid, model=self,
                        mineral=mineral, country=country,
                        global_share=gshare,
                        ev_share_of_global=ev_share,
                        safety_stock_weeks=safety,
                        recovery_rate_wk=0.04,
                        financial_profile=profile_for_agent(aid),
                    )
                else:
                    # Archetype subclasses accept a subset of parameters;
                    # recovery_rate and production_floor use archetype defaults.
                    a = cls(
                        agent_id=aid, model=self,
                        mineral=mineral, country=country,
                        global_share=gshare,
                        ev_share_of_global=ev_share,
                        safety_stock_weeks=safety,
                        financial_profile=profile_for_agent(aid),
                    )
                apply_data_source_calibration(a, country)
                self._mineral_agents[aid] = a

    def _build_cell_agents(self) -> None:
        for name, cfg in CELL_MAKERS.items():
            archetype_name = CELL_AGENT_ARCHETYPES.get(name)
            cls = _CELL_ARCHETYPE_CLASSES.get(archetype_name, CellManufacturerAgent)
            a = cls(
                agent_id=f"cell_{name}", model=self,
                name=name,
                country=cfg["country"],
                capacity_gwh_yr=cfg["capacity_gwh_yr"] * self.market_scope_fraction,
                market_share=cfg["market_share"],
                lfp_fraction=cfg["lfp_fraction"],
                nmc_fraction=cfg["nmc_fraction"],
                safety_stock_weeks=cfg["safety_stock_weeks"],
                recovery_rate_wk=cfg["recovery_rate_wk"],
                financial_profile=profile_for_agent(f"cell_{name}"),
            )
            apply_data_source_calibration(a, cfg["country"])
            self._cell_agents[name] = a

    def _build_tier1_agents(self) -> None:
        cfg_map = {
            "battery_pack": ("cells",     1.00, TIER1["battery_pack"]),
            "inverter":     ("sic_wafer", TIER1["inverter"]["sic_dependency"],
                             TIER1["inverter"]),
            "motor":        ("ree",       TIER1["motor"]["pmsm_fraction"],
                             TIER1["motor"]),
            "harness":      ("copper",    0.00, TIER1["harness"]),
        }
        for comp, (key_input, dep, cfg) in cfg_map.items():
            archetype_name = TIER1_AGENT_ARCHETYPES.get(comp)
            cls = _TIER1_ARCHETYPE_CLASSES.get(archetype_name, Tier1SupplierAgent)
            a = cls(
                agent_id=f"t1_{comp}", model=self,
                component=comp,
                key_input=key_input,
                capacity_k_yr=cfg["capacity_units_yr_k"],
                input_dependency=dep,
                lead_time_weeks=cfg["lead_time_weeks"],
                safety_stock_weeks=cfg["safety_stock_weeks"],
                recovery_rate_wk=cfg["recovery_rate_wk"],
                bullwhip_factor=BULLWHIP_FACTOR,
                financial_profile=profile_for_agent(f"t1_{comp}"),
            )
            calibration_region = {
                "battery_pack": "mixed",
                "inverter": "europe",
                "motor": "mixed",
                "harness": "uk",
            }.get(comp, "mixed")
            apply_data_source_calibration(a, calibration_region)
            self._tier1_agents[comp] = a

    def _build_oem_agents(self) -> None:
        for name in self._active_oem_names:
            cfg = OEMS[name]
            archetype_name = OEM_AGENT_ARCHETYPES.get(name)
            cls = _OEM_ARCHETYPE_CLASSES.get(archetype_name, OEMAgent)
            a = cls(
                agent_id=f"oem_{name}", model=self,
                name=name,
                region=cfg["region"],
                annual_target_k=cfg["annual_target_k"],
                safety_stock_weeks=cfg["safety_stock_weeks"],
                dual_source_trigger=cfg["dual_source_trigger"],
                vertical_integration=cfg.get("vertical_integration", 0.0),
                financial_profile=profile_for_agent(f"oem_{name}"),
            )
            apply_data_source_calibration(a, cfg["region"])
            self._oem_agents[name] = a

    def _build_market_agents(self) -> None:
        for region in self._active_market_regions:
            cfg = MARKETS[region]
            a = MarketAgent(
                agent_id=f"mkt_{region}", model=self,
                region=region,
                gwh_2023=cfg["gwh_2023"],
                yoy_growth=cfg["yoy"],
                avg_kwh_per_veh=cfg["avg_kwh_veh"],
                price_elasticity=cfg.get("price_elasticity", DEMAND_PRICE_ELASTICITY),
                backlog_sensitivity=cfg.get("backlog_sensitivity", 0.35),
                availability_floor=cfg.get("availability_floor", 0.55),
            )
            self._market_agents[region] = a

    # =========================================================================
    # Shock management
    # =========================================================================

    def _load_shocks(self, shocks: List[Dict]) -> None:
        for shock in shocks:
            w = shock["start_week"]
            self._shock_schedule.setdefault(w, []).append(shock)
            end_w = shock.get("end_week")
            if end_w is not None:
                resolve = {"_resolve": True, "target": shock["target"]}
                self._shock_schedule.setdefault(end_w, []).append(resolve)

    def _apply_shocks(self) -> None:
        for shock in self._shock_schedule.get(self.week, []):
            if shock.get("_resolve"):
                target = shock["target"]
                agent  = self._find_agent(target)
                if agent:
                    agent.resolve_shock()
                continue

            target   = shock["target"]
            severity = shock.get("severity", 0.5)
            agent    = self._find_agent(target)
            if agent:
                agent.apply_shock(severity)
                self._active_shocks[target] = shock

    def _find_agent(self, agent_id: str):
        for store in (self._mineral_agents, self._cell_agents,
                      self._tier1_agents, self._oem_agents):
            # Fast path: dict key matches (mineral agents: e.g. "cobalt_drc")
            if agent_id in store:
                return store[agent_id]
            # Slow path: agent_id attribute matches (tier-1: "t1_harness",
            # cell: "cell_catl" — stored under plain name as dict key)
            for agent in store.values():
                if agent.agent_id == agent_id:
                    return agent
        return None

    # =========================================================================
    # Agent demand/delivery queries (called by agents during step)
    # =========================================================================

    def get_cell_demand(self, maker_name: str) -> float:
        """GWh/week demanded from a cell maker (proportional to market share)."""
        total_demand_gwh = sum(
            a.weekly_demand_gwh for a in self._market_agents.values()
        )
        share = CELL_MAKERS.get(maker_name, {}).get("market_share", 0.0)
        return total_demand_gwh * share

    def get_component_demand(self, component: str) -> float:
        """k units/week demanded for a sub-system component."""
        total_veh = sum(
            a.weekly_target for a in self._oem_agents.values()
        )
        return total_veh  # 1:1 ratio: one of each component per vehicle

    def get_oem_demand(self, oem_name: str) -> float:
        """k vehicles/week demanded from a specific OEM (global demand × OEM share)."""
        total_demand_k_veh = sum(
            mkt.weekly_demand_k_veh for mkt in self._market_agents.values()
        )
        active_target = max(self.active_oem_target_k, 1e-9)
        return total_demand_k_veh * (OEMS[oem_name]["annual_target_k"] / active_target)

    def get_component_deliveries(self, oem_name: str) -> Dict[str, float]:
        """k-unit deliveries of each component to an OEM this week."""
        active_target = max(self.active_oem_target_k, 1e-9)
        oem_share = OEMS[oem_name]["annual_target_k"] / active_target
        # Map tier-1 agent keys to OEM inventory keys
        key_map = {
            "battery_pack": "packs",
            "inverter":     "inverters",
            "motor":        "motors",
            "harness":      "harness",
        }
        return {
            key_map[comp]: agent.output_k * oem_share
            for comp, agent in self._tier1_agents.items()
        }

    def get_price_signal(self) -> float:
        """
        Aggregate vehicle-facing price pressure index (1.0 = baseline).
        Delegated to the SD model's report-based smoothed price-signal state.
        """
        return self.sd.get_price_signal()

    def get_cobalt_price(self) -> float:
        """Cobalt price index from the SD model (1.0 = 2023 baseline)."""
        return self.sd.prices.get("cobalt", 1.0)

    def get_lfp_share(self) -> float:
        """Current industry LFP share from SD chemistry-mix stock."""
        return self.sd.lfp_share

    def get_backlog_scale_k(self) -> float:
        """Reference market size for active-region availability feedback."""
        return max(self.active_market_units_2023_k, self.active_oem_target_k, 1e-9)

    # =========================================================================
    # SD flow aggregation
    # =========================================================================

    def _collect_flows(self) -> Dict[str, float]:
        """
        Aggregate agent outputs into SD inflow/outflow pairs plus the new
        SD-state quantities introduced by the four-tier redesign.

        Existing keys (backward-compatible):
          {mineral}_in / {mineral}_out  — normalised mineral supply/demand
          cells_in / cells_out          — GWh
          {component}_in / {component}_out — k vehicle-equiv

        New keys passed to the redesigned SDModel:
          lfp_gwh               — GWh of LFP cells produced this week
          nmc_gwh               — GWh of NMC/NCA cells produced this week
          cell_capacity_gwh_yr  — sum of all cell agent weekly capacities × 52
          total_oem_prod_k      — k vehicles assembled this week (all OEMs)
          total_demand_k        — k vehicles demanded this week (all markets)
          order_rate_k          — k units ordered by Tier-1 agents (bullwhip numerator)
        """
        flows: Dict[str, float] = {}

        # ── Tier 1: Mineral inflows (normalised supply fractions) ─────────────
        for mineral in ("lithium", "cobalt", "graphite", "ree", "sic_wafer"):
            flows[f"{mineral}_in"] = sum(
                a.weekly_supply_contribution
                for a in self._mineral_agents.values()
                if a.mineral == mineral
            )

        # ── Tier 1: Mineral outflows (as fraction of baseline weekly demand) ──
        total_cell_gwh  = sum(a.output_gwh for a in self._cell_agents.values())
        baseline_gwh_wk = BASELINE_WK["cells"]   # 15.81 GWh/wk
        cell_fraction   = total_cell_gwh / max(baseline_gwh_wk, 1e-9)

        flows["lithium_out"]  = cell_fraction
        flows["graphite_out"] = cell_fraction

        # Cobalt consumption weighted by NMC fraction (LFP cells use no cobalt)
        nmc_gwh = sum(a.output_gwh * a.nmc_fraction for a in self._cell_agents.values())
        lfp_gwh = sum(a.output_gwh * a.lfp_fraction for a in self._cell_agents.values())
        _nmc_baseline_frac = sum(
            cfg["market_share"] * cfg["nmc_fraction"] for cfg in CELL_MAKERS.values()
        )  # ≈ 0.597
        nmc_baseline     = baseline_gwh_wk * _nmc_baseline_frac
        # Cobalt outflow: reflect SD chemistry mix (lfp_share) so price signal
        # feeds back into cobalt consumption — the F2 chemistry loop.
        sd_nmc_share     = 1.0 - self.sd.lfp_share
        effective_cobalt_out = (nmc_gwh / max(nmc_baseline, 1e-9)) * sd_nmc_share / max(_nmc_baseline_frac, 1e-9)
        flows["cobalt_out"]   = max(0.0, effective_cobalt_out)

        # REE outflow — proportional to motor production
        motor_agent = self._tier1_agents["motor"]
        total_motor_k = motor_agent.output_k
        base_ree_dep = max(TIER1["motor"]["pmsm_fraction"], 1e-9)
        ree_price = self.sd.prices.get("ree", 1.0)
        substitution = min(0.80, max(0.0, (ree_price - 1.0) * 0.75))
        effective_ree_dep = max(
            0.12,
            motor_agent.input_dependency * (1.0 - substitution),
        )
        flows["ree_out"] = (
            total_motor_k / max(BASELINE_WK["motors"], 1e-9)
        ) * (effective_ree_dep / base_ree_dep)

        # SiC outflow — SiC-fraction of inverter production
        total_inv_k     = self._tier1_agents["inverter"].output_k
        sic_dep         = TIER1["inverter"]["sic_dependency"]
        flows["sic_wafer_out"] = (total_inv_k * sic_dep) / max(
            BASELINE_WK["inverters"] * sic_dep, 1e-9
        )

        # ── Tier 2: Cell inventory flows (GWh) ───────────────────────────────
        # Average kWh/vehicle: 822 GWh/yr ÷ 14 000 k veh/yr = 58.7 kWh/veh
        _kwh_per_veh     = 58.7
        flows["cells_in"]  = total_cell_gwh
        flows["cells_out"] = self._tier1_agents["battery_pack"].output_k * (_kwh_per_veh / 1000.0)

        # New: per-chemistry cell production for SD chemistry tracking
        flows["lfp_gwh"]  = lfp_gwh
        flows["nmc_gwh"]  = nmc_gwh

        # New: total cell capacity (agents expand weekly; sum for SD sync)
        flows["cell_capacity_gwh_yr"] = sum(
            a.weekly_capacity * 52.0 for a in self._cell_agents.values()
        )

        # ── Tier 3: Component inventory flows (k vehicle-equiv) ───────────────
        total_oem_prod = sum(a.production_k for a in self._oem_agents.values())
        for comp, agent_key in {
            "packs":     "battery_pack",
            "inverters": "inverter",
            "motors":    "motor",
            "harness":   "harness",
        }.items():
            flows[f"{comp}_in"]  = self._tier1_agents[agent_key].output_k
            flows[f"{comp}_out"] = total_oem_prod  # 1 of each per vehicle assembled

        # New: order rate = sum of pipeline additions by Tier-1 agents (bullwhip)
        flows["order_rate_k"] = sum(
            a.pipeline[-1] if a.pipeline else 0.0
            for a in self._tier1_agents.values()
        )

        # ── Copper: exogenous steady-state (no ABM agent; harness dependency) ───
        # Provides a stable copper balance so the SD copper stock remains near
        # target.  A future copper disruption scenario can override copper_in.
        total_harness_k = self._tier1_agents["harness"].output_k
        flows["copper_in"]  = total_harness_k / max(BASELINE_WK["harness"], 1e-9)
        flows["copper_out"] = flows["copper_in"]   # steady-state; net change = 0

        # ── Tier 4: Demand and backlog aggregates ─────────────────────────────
        flows["total_oem_prod_k"]   = total_oem_prod
        total_demand_k              = sum(
            mkt.weekly_demand_k_veh for mkt in self._market_agents.values()
        )
        flows["total_demand_k"]     = total_demand_k
        flows["total_demand_gwh_wk"]= sum(
            mkt.weekly_demand_gwh for mkt in self._market_agents.values()
        )

        return flows

    # =========================================================================
    # Main run loop
    # =========================================================================

    def step(self) -> None:
        # 1. Shocks
        self._apply_shocks()

        # 2. SD → agents
        self.sd.compute_input_fractions()

        # 3–7. Agents step in tier order
        for a in self._mineral_agents.values():
            a.step()
        for a in self._cell_agents.values():
            a.step()
        for a in self._tier1_agents.values():
            a.step()
        for a in self._oem_agents.values():
            a.step()
        for a in self._market_agents.values():
            a.step()

        # 8. SD update
        flows = self._collect_flows()
        self.sd.update(flows)
        self.sd.record()

        # 9. Metrics
        self._record_metrics()
        self.week += 1

    def run(self, n_weeks: Optional[int] = None) -> None:
        steps = n_weeks or self.n_weeks
        for _ in range(steps):
            self.step()

    # =========================================================================
    # Metrics and results
    # =========================================================================

    def _record_metrics(self) -> None:
        total_cell_gwh  = sum(a.output_gwh    for a in self._cell_agents.values())
        total_prod_k    = sum(a.production_k  for a in self._oem_agents.values())
        total_demand_gwh= sum(a.weekly_demand_gwh for a in self._market_agents.values())
        total_backlog   = sum(a.backlog_k     for a in self._oem_agents.values())

        row: Dict[str, Any] = {
            "week":                self.week,
            "focus_region":        self.focus_region or "global",
            "active_oem_target_k": self.active_oem_target_k,
            "active_market_gwh_2023": self.active_market_gwh_2023,
            "cell_production_gwh": total_cell_gwh,
            "oem_production_k":    total_prod_k,
            "market_demand_gwh":   total_demand_gwh,
            "total_backlog_k":     total_backlog,
            # SD-derived vehicle-facing price signal
            "price_signal":        self.get_price_signal(),
            "raw_price_signal":    self.sd.raw_price_signal,
        }

        # Per-OEM production
        for name, a in self._oem_agents.items():
            row[f"oem_{name}_k"] = a.production_k

        # SD inventory stocks — weeks of supply (all tiers, incl. copper)
        for stock in ("lithium", "cobalt", "graphite", "ree", "sic_wafer", "copper",
                      "cells", "packs", "inverters", "motors", "harness"):
            row[f"stock_{stock}_wk"] = self.sd.weeks_of_supply(stock)

        # SD Tier 1 price indices (incl. copper)
        for mineral in ("lithium", "cobalt", "graphite", "ree", "sic_wafer", "copper"):
            row[f"price_{mineral}"] = self.sd.prices.get(mineral, 1.0)

        # SD component price indices from the report-based price module
        for name, value in self.sd.component_prices.items():
            row[f"price_component_{name}"] = value
        component_cost = (
            0.72 * self.sd.component_prices.get("pack", 1.0)
            + 0.08 * self.sd.component_prices.get("inverter", 1.0)
            + 0.12 * self.sd.component_prices.get("motor", 1.0)
            + 0.08 * self.sd.component_prices.get("harness", 1.0)
        )
        row["oem_price_margin_signal"] = (
            self.sd.component_prices.get("vehicle", 1.0) / max(component_cost, 1e-9)
        )

        # SD Tier 2 cell capacity & chemistry (new)
        row["cell_capacity_gwh_yr"] = self.sd.cell_capacity
        row["cell_cap_util"]        = self.sd.cell_capacity_utilisation_exact(total_cell_gwh)
        row["lfp_share"]            = self.sd.lfp_share

        # SD Tier 4 demand & backlog (new)
        row["sd_ev_demand_gwh_yr"]  = self.sd.ev_demand_gwh_yr
        row["sd_oem_backlog_k"]     = self.sd.oem_backlog_k
        row["bullwhip_index"]       = self.sd.bullwhip_index

        # Tier-1 subsystem output
        for comp, a in self._tier1_agents.items():
            row[f"t1_{comp}_k"] = a.output_k

        self._records.append(row)

    def get_results(self) -> pd.DataFrame:
        """Return simulation history as a tidy DataFrame."""
        return pd.DataFrame(self._records)

    def get_calibration_summary(self) -> pd.DataFrame:
        """
        Return a DataFrame showing the financial calibration applied to each
        agent: which listed companies were matched, coverage percentage, and
        the resulting multipliers.

        Useful for auditing how much of the calibration is data-driven vs.
        defaulting to 1.0 (no financial data found).
        """
        return coverage_report()

    def get_tier_calibration(self) -> pd.DataFrame:
        """
        Return aggregate calibration multipliers by supply-chain tier, showing
        how the listed-company financial data shapes each tier's behaviour.
        """
        from .financial_profiles import profile_for_tier
        rows = []
        for tier_label, agent_ids in FOUR_TIER_AGENT_GROUPS.items():
            p = profile_for_tier(tier_label)
            rows.append({
                "tier":                tier_label,
                "agents":              len(agent_ids),
                "matched_companies":   "; ".join(p.company_names[:5])
                                       + ("…" if len(p.company_names) > 5 else ""),
                "recovery_multiplier": round(p.recovery_multiplier, 3),
                "inventory_multiplier":round(p.inventory_multiplier, 3),
                "growth_multiplier":   round(p.growth_multiplier, 3),
                "shock_absorption":    round(p.shock_absorption, 3),
            })
        return pd.DataFrame(rows)

    def get_data_source_calibration_summary(self) -> pd.DataFrame:
        """
        Return the country/region data-source calibration applied to each agent.

        This audits the bounded multipliers derived from the data-source
        registry in index.html, currently using the World Bank country
        indicators seeded in model.data_source_calibration.
        """
        rows = []
        agent_groups = (
            ("minerals", self._mineral_agents),
            ("cells", self._cell_agents),
            ("tier1", self._tier1_agents),
            ("oem", self._oem_agents),
        )
        for layer, agents in agent_groups:
            for key, agent in agents.items():
                cal = getattr(agent, "data_source_calibration", None)
                if cal is None:
                    continue
                rows.append({
                    "layer": layer,
                    "agent": key,
                    "agent_id": agent.agent_id,
                    "country_or_region": (
                        getattr(agent, "data_source_region", None)
                        or getattr(agent, "country", None)
                        or getattr(agent, "region", None)
                        or "mixed"
                    ),
                    "recovery_multiplier": round(cal.recovery_multiplier, 3),
                    "inventory_multiplier": round(cal.inventory_multiplier, 3),
                    "growth_multiplier": round(cal.growth_multiplier, 3),
                    "shock_absorption": round(cal.shock_absorption, 3),
                    "source_note": cal.source_note,
                })
        return pd.DataFrame(rows)

    def get_shock_summary(self) -> Dict[str, Any]:
        """Key disruption metrics for scenario comparison."""
        df = self.get_results()
        if df.empty:
            return {}

        baseline_prod   = df["oem_production_k"].iloc[:SIM["warm_up"]].mean()
        if baseline_prod == 0:
            baseline_prod = df["oem_production_k"].max()

        min_prod        = df["oem_production_k"].min()
        peak_loss_pct   = (1 - min_prod / max(baseline_prod, 1e-9)) * 100
        cumulative_loss = df["oem_production_k"].apply(
            lambda x: max(0, baseline_prod - x)
        ).sum()

        # Recovery: weeks until production returns to ≥90% of baseline
        recovery_threshold = baseline_prod * 0.90
        below = df[df["oem_production_k"] < recovery_threshold]
        recovery_week = int(below["week"].max()) + 1 if not below.empty else 0

        return {
            "peak_loss_pct":     round(peak_loss_pct, 1),
            "cumulative_loss_k": round(cumulative_loss, 0),
            "recovery_week":     recovery_week,
            "total_halt_weeks":  sum(
                a.halt_weeks for a in self._oem_agents.values()
            ),
        }
