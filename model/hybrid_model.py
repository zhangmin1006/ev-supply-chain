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
)
from .sd_model import SDModel, BASELINE_WK
from .agents import (
    MineralSupplierAgent, CellManufacturerAgent,
    Tier1SupplierAgent, OEMAgent, MarketAgent,
)


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
                 n_weeks: int = 260):
        self.rng      = np.random.default_rng(seed)
        self.scenario = scenario or {}
        self.n_weeks  = n_weeks
        self.week     = 0

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
                a = MineralSupplierAgent(
                    agent_id=aid, model=self,
                    mineral=mineral, country=country,
                    global_share=gshare,
                    ev_share_of_global=ev_share,
                    safety_stock_weeks=safety,
                    recovery_rate_wk=0.04,
                )
                self._mineral_agents[aid] = a

    def _build_cell_agents(self) -> None:
        for name, cfg in CELL_MAKERS.items():
            a = CellManufacturerAgent(
                agent_id=f"cell_{name}", model=self,
                name=name,
                country=cfg["country"],
                capacity_gwh_yr=cfg["capacity_gwh_yr"],
                market_share=cfg["market_share"],
                lfp_fraction=cfg["lfp_fraction"],
                nmc_fraction=cfg["nmc_fraction"],
                safety_stock_weeks=cfg["safety_stock_weeks"],
                recovery_rate_wk=cfg["recovery_rate_wk"],
            )
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
            a = Tier1SupplierAgent(
                agent_id=f"t1_{comp}", model=self,
                component=comp,
                key_input=key_input,
                capacity_k_yr=cfg["capacity_units_yr_k"],
                input_dependency=dep,
                lead_time_weeks=cfg["lead_time_weeks"],
                safety_stock_weeks=cfg["safety_stock_weeks"],
                recovery_rate_wk=cfg["recovery_rate_wk"],
                bullwhip_factor=BULLWHIP_FACTOR,
            )
            self._tier1_agents[comp] = a

    def _build_oem_agents(self) -> None:
        for name, cfg in OEMS.items():
            a = OEMAgent(
                agent_id=f"oem_{name}", model=self,
                name=name,
                region=cfg["region"],
                annual_target_k=cfg["annual_target_k"],
                safety_stock_weeks=cfg["safety_stock_weeks"],
                dual_source_trigger=cfg["dual_source_trigger"],
            )
            self._oem_agents[name] = a

    def _build_market_agents(self) -> None:
        for region, cfg in MARKETS.items():
            a = MarketAgent(
                agent_id=f"mkt_{region}", model=self,
                region=region,
                gwh_2023=cfg["gwh_2023"],
                yoy_growth=cfg["yoy"],
                avg_kwh_per_veh=cfg["avg_kwh_veh"],
                price_elasticity=DEMAND_PRICE_ELASTICITY,
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
        return total_demand_k_veh * (
            OEMS[oem_name]["annual_target_k"] / EV_GLOBAL_UNITS_2023_K
        )

    def get_component_deliveries(self, oem_name: str) -> Dict[str, float]:
        """k-unit deliveries of each component to an OEM this week."""
        oem_share = OEMS[oem_name]["annual_target_k"] / EV_GLOBAL_UNITS_2023_K
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
        Aggregate price pressure index (1.0 = baseline).
        Weighted average of mineral stock shortfalls mapped to price.

        Calibration: BNEF/IEA historical data shows:
          - Lithium carbonate: +400% peak 2021-22 from supply tightness
          - Cobalt: +170% 2017-18 DRC risk premium
          - Graphite: +80% after China Oct-2023 export controls
        We use a convex (1/frac − 1) response so price doubles at 50% stock,
        triples at 33% stock — consistent with observed commodity behaviour.
        Signal is capped at 3.0 to prevent instability.
        """
        weights = {"lithium": 0.30, "cobalt": 0.25,
                   "graphite": 0.20, "ree": 0.15, "sic_wafer": 0.10}
        price_idx = 0.0
        for mineral, w in weights.items():
            frac = max(0.05, self.sd.input_fractions.get(mineral, 1.0))
            # Convex response: 1/frac gives 2× at 50% stock, 4× at 25%
            # Subtract 1 so baseline (frac=1) → contribution = 0
            price_component = min(3.0, 1.0 / frac)
            price_idx += w * price_component
        return min(3.0, price_idx)

    # =========================================================================
    # SD flow aggregation
    # =========================================================================

    def _collect_flows(self) -> Dict[str, float]:
        """
        Aggregate agent outputs into SD inflow/outflow pairs.

        Mineral flows (normalised):
          inflow  = sum of supplier output fractions × their EV share weight
          outflow = cell production as fraction of baseline demand

        Component flows (absolute):
          inflow  = production this week (GWh or k-units)
          outflow = consumption this week by downstream tier
        """
        flows: Dict[str, float] = {}

        # ── Mineral flows ────────────────────────────────────────────────────
        # Baseline weekly consumption = 1.0 (normalised mineral unit)
        for mineral in ("lithium", "cobalt", "graphite", "ree", "sic_wafer"):
            total_supply = sum(
                a.weekly_supply_contribution
                for a in self._mineral_agents.values()
                if a.mineral == mineral
            )
            flows[f"{mineral}_in"] = total_supply

        # Mineral outflow = cell production (as fraction of baseline demand)
        total_cell_gwh = sum(
            a.output_gwh for a in self._cell_agents.values()
        )
        baseline_gwh_wk = BASELINE_WK["cells"]   # 15.81 GWh/wk
        cell_fraction   = total_cell_gwh / max(baseline_gwh_wk, 1e-9)

        # kg of mineral per kWh → weekly consumption fraction
        # For normalised stocks the "1 unit" of weekly mineral is the amount
        # needed to produce baseline_gwh_wk of cells.
        flows["lithium_out"]  = cell_fraction
        flows["graphite_out"] = cell_fraction

        # Cobalt depends on NMC fraction; weighted average across makers
        nmc_weighted_output = sum(
            a.output_gwh * a.nmc_fraction for a in self._cell_agents.values()
        )
        # Weighted NMC fraction at baseline: sum(market_share × nmc_fraction)
        _nmc_weighted_frac = sum(
            cfg["market_share"] * cfg["nmc_fraction"]
            for cfg in CELL_MAKERS.values()
        )  # ≈ 0.611
        nmc_baseline = baseline_gwh_wk * _nmc_weighted_frac
        flows["cobalt_out"] = nmc_weighted_output / max(nmc_baseline, 1e-9)

        # REE consumed by motor production
        total_motor_k = self._tier1_agents["motor"].output_k
        baseline_motor = BASELINE_WK["motors"]
        flows["ree_out"] = total_motor_k / max(baseline_motor, 1e-9)

        # SiC consumed by inverter production (only SiC-fraction)
        total_inv_k = self._tier1_agents["inverter"].output_k
        sic_dep     = TIER1["inverter"]["sic_dependency"]
        flows["sic_wafer_out"] = (total_inv_k * sic_dep) / max(
            BASELINE_WK["inverters"] * sic_dep, 1e-9
        )

        # ── Cell flows (absolute GWh) ─────────────────────────────────────────
        flows["cells_in"]  = total_cell_gwh
        flows["cells_out"] = self._tier1_agents["battery_pack"].output_k * (
            58.7 / 1000.0
        )  # 58.7 kWh avg (822 GWh/yr ÷ 14000k veh/yr) × k vehicles → GWh

        # ── Component flows (k vehicle-equiv) ────────────────────────────────
        total_oem_prod = sum(
            a.production_k for a in self._oem_agents.values()
        )
        for comp in ("packs", "inverters", "motors", "harness"):
            agent_key = {
                "packs":     "battery_pack",
                "inverters": "inverter",
                "motors":    "motor",
                "harness":   "harness",
            }[comp]
            flows[f"{comp}_in"]  = self._tier1_agents[agent_key].output_k
            flows[f"{comp}_out"] = total_oem_prod   # each OEM consumes 1 per vehicle

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
        total_cell_gwh = sum(
            a.output_gwh for a in self._cell_agents.values()
        )
        total_prod_k = sum(
            a.production_k for a in self._oem_agents.values()
        )
        total_demand_gwh = sum(
            a.weekly_demand_gwh for a in self._market_agents.values()
        )
        total_backlog = sum(
            a.backlog_k for a in self._oem_agents.values()
        )

        row: Dict[str, Any] = {
            "week":              self.week,
            "cell_production_gwh": total_cell_gwh,
            "oem_production_k":  total_prod_k,
            "market_demand_gwh": total_demand_gwh,
            "total_backlog_k":   total_backlog,
            "price_signal":      self.get_price_signal(),
        }

        # Per-OEM production
        for name, a in self._oem_agents.items():
            row[f"oem_{name}_k"] = a.production_k

        # SD stock weeks-of-supply
        for stock in ("lithium", "cobalt", "graphite",
                      "ree", "sic_wafer", "cells", "harness"):
            row[f"stock_{stock}_wk"] = self.sd.weeks_of_supply(stock)

        # Tier-1 output
        for comp, a in self._tier1_agents.items():
            row[f"t1_{comp}_k"] = a.output_k

        self._records.append(row)

    def get_results(self) -> pd.DataFrame:
        """Return simulation history as a tidy DataFrame."""
        return pd.DataFrame(self._records)

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
