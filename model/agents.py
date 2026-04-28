"""
ABM Agent Classes
=================
Five agent types model heterogeneous firm behaviour at each supply chain tier.

Decision rules
--------------
  MineralSupplierAgent  — output follows capacity, reduced by shock multiplier.
                          Gradual recovery after shock resolves.
  CellManufacturerAgent — Leontief production function over (Li, Co, graphite).
                          Order-up-to inventory policy; chemistry mix determines
                          cobalt sensitivity.
  Tier1SupplierAgent    — Order-up-to with lead-time pipeline; activates dual
                          sourcing (at 20% cost premium) when critically short.
  OEMAgent              — Leontief over all four sub-system inputs (pack,
                          inverter, motor, harness); records halt weeks.
  MarketAgent           — Exogenous demand with YoY growth and price elasticity.

Coupling
--------
  Agents read `input_fractions` from the SD model before stepping.
  After stepping, HybridModel aggregates their outputs into SD flows.
"""

from __future__ import annotations
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Weekly capacity growth rates (applied each step)
# Cell capacity: IEA GEO 2024 29%/yr → weekly
_CELL_GROWTH_WK = (1.29 ** (1 / 52)) - 1   # ≈ 0.00491
# Tier-1 / OEM demand grows at same rate so Tier-1 capacity tracks demand
_TIER1_GROWTH_WK = _CELL_GROWTH_WK


# ── Base ─────────────────────────────────────────────────────────────────────

class Agent:
    def __init__(self, agent_id: str, model):
        self.agent_id = agent_id
        self.model    = model

    def step(self) -> None:
        raise NotImplementedError


# ── 1. Mineral Supplier ───────────────────────────────────────────────────────

class MineralSupplierAgent(Agent):
    """
    Models one mineral supply source (a country or producer group).
    Output is expressed as a fraction of baseline weekly production.

    Behaviour
    ---------
    - Normal:  output_fraction = 1.0
    - Shocked: output_fraction = 1 - severity  (immediate reduction)
    - Recovery: output_fraction rises by recovery_rate_wk each week
                once the shock is resolved.
    """

    def __init__(self, agent_id: str, model,
                 mineral: str,
                 country: str,
                 global_share: float,
                 ev_share_of_global: float,
                 safety_stock_weeks: int = 4,
                 recovery_rate_wk: float = 0.04):
        super().__init__(agent_id, model)
        self.mineral           = mineral
        self.country           = country
        self.global_share      = global_share      # fraction of world production
        self.ev_share          = ev_share_of_global  # fraction going to EV sector
        self.safety_stock_weeks = safety_stock_weeks
        self.recovery_rate_wk  = recovery_rate_wk

        # State
        self.shock_multiplier:  float = 1.0
        self.is_shocked:        bool  = False
        self.output_fraction:   float = 1.0   # fraction of baseline this week

        # Metrics
        self.output_history: List[float] = []

    # ── shock interface ───────────────────────────────────────────────────────

    def apply_shock(self, severity: float) -> None:
        """severity ∈ [0, 1]:  0 = no effect, 1 = total shutdown."""
        self.is_shocked       = True
        self.shock_multiplier = max(0.0, 1.0 - severity)

    def resolve_shock(self) -> None:
        self.is_shocked = False   # recovery handled in step()

    # ── step ─────────────────────────────────────────────────────────────────

    def step(self) -> None:
        if not self.is_shocked and self.shock_multiplier < 1.0:
            # Gradual capacity restoration
            self.shock_multiplier = min(1.0,
                self.shock_multiplier + self.recovery_rate_wk)

        self.output_fraction = self.shock_multiplier
        self.output_history.append(self.output_fraction)

    # ── query ─────────────────────────────────────────────────────────────────

    @property
    def weekly_supply_contribution(self) -> float:
        """
        Fraction of EV-industry baseline mineral supply provided this week.
        = global_share × shock_multiplier
        (ev_share is already normalised into the SD stock baseline, so we
        only need the country share to apportion the EV-allocated supply.)
        """
        return self.global_share * self.output_fraction


# ── 2. Cell Manufacturer ─────────────────────────────────────────────────────

class CellManufacturerAgent(Agent):
    """
    Models a battery cell manufacturer (CATL, LG ES, Panasonic, …).

    Production constraint (Leontief):
        effective_production = capacity × min(input_fracs) × shock_multiplier

    Where input_fracs are:
        lithium   — always required
        graphite  — always required
        cobalt    — only required for NMC/NCA fraction

    Inventory policy: order-up-to with 4-week target.
    Output: GWh of cells produced this week.
    """

    def __init__(self, agent_id: str, model,
                 name: str,
                 country: str,
                 capacity_gwh_yr: float,
                 market_share: float,
                 lfp_fraction: float,
                 nmc_fraction: float,
                 safety_stock_weeks: int = 4,
                 recovery_rate_wk: float = 0.04):
        super().__init__(agent_id, model)
        self.name              = name
        self.country           = country
        self.weekly_capacity   = capacity_gwh_yr / 52.0   # GWh/week
        self.market_share      = market_share
        self.lfp_fraction      = lfp_fraction
        self.nmc_fraction      = nmc_fraction
        self.safety_stock_weeks = safety_stock_weeks
        self.recovery_rate_wk  = recovery_rate_wk

        # Inventory
        self.inventory_gwh     = self.weekly_capacity * safety_stock_weeks
        self.target_inventory  = self.inventory_gwh

        # State
        self.shock_multiplier:  float = 1.0
        self.is_shocked:        bool  = False
        self.output_gwh:        float = 0.0
        self.backlog_gwh:       float = 0.0

        # Metrics
        self.output_history:     List[float] = []
        self.inventory_history:  List[float] = []
        self.utilisation_history: List[float] = []

    def _leontief_constraint(self, input_fracs: Dict[str, float]) -> float:
        """
        Returns the tightest Leontief input constraint [0, 1].
        LFP cells are immune to cobalt shortage.
        """
        li  = input_fracs.get("lithium",  1.0)
        co  = input_fracs.get("cobalt",   1.0)
        gr  = input_fracs.get("graphite", 1.0)

        # Cobalt constraint only applies to NMC/NCA portion
        cobalt_effect = self.lfp_fraction + self.nmc_fraction * co

        return min(li, gr, cobalt_effect)

    def apply_shock(self, severity: float) -> None:
        self.is_shocked       = True
        self.shock_multiplier = max(0.0, 1.0 - severity)

    def resolve_shock(self) -> None:
        self.is_shocked = False

    def step(self) -> None:
        # Gradual recovery
        if not self.is_shocked and self.shock_multiplier < 1.0:
            self.shock_multiplier = min(1.0,
                self.shock_multiplier + self.recovery_rate_wk)

        # Pull input availability from SD model
        input_fracs = self.model.sd.input_fractions
        constraint  = self._leontief_constraint(input_fracs)

        # Order-up-to: produce extra to replenish inventory
        gap = max(0.0, self.target_inventory - self.inventory_gwh)
        desired = self.weekly_capacity + gap / 4.0

        max_production = self.weekly_capacity * self.shock_multiplier * constraint
        self.output_gwh = min(desired, max_production)
        self.output_gwh = max(0.0, self.output_gwh)

        # Update inventory
        downstream_demand = self.model.get_cell_demand(self.name)
        fulfilled = min(downstream_demand, self.inventory_gwh + self.output_gwh)
        self.inventory_gwh = max(
            0.0,
            self.inventory_gwh + self.output_gwh - fulfilled
        )
        self.backlog_gwh = max(0.0, downstream_demand - fulfilled)

        # Record
        util = self.output_gwh / max(self.weekly_capacity, 1e-9)
        self.output_history.append(self.output_gwh)
        self.inventory_history.append(self.inventory_gwh)
        self.utilisation_history.append(util)


# ── 3. Tier-1 Supplier ────────────────────────────────────────────────────────

class Tier1SupplierAgent(Agent):
    """
    Models a sub-system supplier (battery pack, inverter, motor, harness).

    Key features
    ------------
    - Lead-time order pipeline: orders placed today arrive after `lead_time_weeks`.
    - Order-up-to-S policy with bullwhip amplification.
    - Dual sourcing activates when inventory falls below 20% of target.
    - SiC/REE dependency modelled as Leontief constraint on the relevant input.
    """

    def __init__(self, agent_id: str, model,
                 component: str,
                 key_input: str,
                 capacity_k_yr: float,
                 input_dependency: float,     # fraction of output needing key input
                 lead_time_weeks: int,
                 safety_stock_weeks: int,
                 recovery_rate_wk: float = 0.04,
                 bullwhip_factor: float = 1.25):
        super().__init__(agent_id, model)
        self.component          = component
        self.key_input          = key_input
        self.weekly_capacity    = capacity_k_yr / 52.0   # k units/week
        self.input_dependency   = input_dependency
        self.lead_time_weeks    = lead_time_weeks
        self.safety_stock_weeks = safety_stock_weeks
        self.recovery_rate_wk   = recovery_rate_wk
        self.bullwhip_factor    = bullwhip_factor

        # Inventory (k vehicle-equiv)
        self.inventory          = self.weekly_capacity * safety_stock_weeks
        self.target_inventory   = self.inventory

        # Lead-time order pipeline: list length = lead_time_weeks
        self.pipeline: List[float] = [self.weekly_capacity] * lead_time_weeks

        # State
        self.shock_multiplier:   float = 1.0
        self.is_shocked:         bool  = False
        self.output_k:           float = 0.0
        self.dual_source_active: bool  = False

        # Metrics
        self.output_history:   List[float] = []
        self.inventory_history: List[float] = []
        self.shortage_history:  List[float] = []

    def _input_constraint(self, input_fracs: Dict[str, float]) -> float:
        """
        For components that partially depend on a critical input (SiC, REE):
        effective constraint = (1 - dependency) + dependency × input_fraction
        This allows the non-critical portion to keep running.
        """
        frac = input_fracs.get(self.key_input, 1.0)
        return (1.0 - self.input_dependency) + self.input_dependency * frac

    def apply_shock(self, severity: float) -> None:
        self.is_shocked       = True
        self.shock_multiplier = max(0.0, 1.0 - severity)

    def resolve_shock(self) -> None:
        self.is_shocked = False

    def step(self) -> None:
        # Gradual recovery
        if not self.is_shocked and self.shock_multiplier < 1.0:
            self.shock_multiplier = min(1.0,
                self.shock_multiplier + self.recovery_rate_wk)

        # Receive oldest pipeline delivery
        if self.pipeline:
            delivery = self.pipeline.pop(0)
            input_fracs = self.model.sd.input_fractions
            delivery_actual = delivery * self._input_constraint(input_fracs)
            self.inventory = min(
                self.inventory + delivery_actual,
                self.target_inventory * 2.5
            )

        # Place new order (order-up-to + bullwhip)
        in_transit = sum(self.pipeline)
        inv_position = self.inventory + in_transit
        shortfall = max(0.0, self.target_inventory - inv_position)
        order = (self.weekly_capacity + shortfall * self.bullwhip_factor)

        # Dual sourcing: boost order if critically low
        if self.inventory < self.target_inventory * 0.20:
            self.dual_source_active = True
            order *= 1.20   # 20% premium sourcing
        elif self.inventory > self.target_inventory * 0.60:
            self.dual_source_active = False

        self.pipeline.append(order)

        # Determine output this week
        input_fracs = self.model.sd.input_fractions
        constraint  = self._input_constraint(input_fracs)
        max_out = self.weekly_capacity * self.shock_multiplier * constraint

        weekly_demand = self.model.get_component_demand(self.component)
        self.output_k = min(max_out, self.inventory)
        self.inventory = max(0.0, self.inventory - self.output_k)

        shortage = max(0.0, weekly_demand - self.output_k)

        # Record
        self.output_history.append(self.output_k)
        self.inventory_history.append(self.inventory)
        self.shortage_history.append(shortage)


# ── 4. OEM ────────────────────────────────────────────────────────────────────

class OEMAgent(Agent):
    """
    Models a vehicle OEM (group by region: Chinese, US, German, Korean).

    Production function: strict Leontief over four sub-system inputs.
        producible = min(pack_inv, inverter_inv, motor_inv, harness_inv)

    Records halt_weeks when production falls below 10% of weekly target.
    """

    def __init__(self, agent_id: str, model,
                 name: str,
                 region: str,
                 annual_target_k: int,
                 safety_stock_weeks: int = 5,
                 dual_source_trigger: float = 0.20):
        super().__init__(agent_id, model)
        self.name                = name
        self.region              = region
        self.weekly_target       = annual_target_k / 52.0   # k vehicles/week
        self.safety_stock_weeks  = safety_stock_weeks
        self.dual_source_trigger = dual_source_trigger

        # Component inventories (k vehicle-equivalents each)
        init = self.weekly_target * safety_stock_weeks
        self.inv: Dict[str, float] = {
            "packs":     init,
            "inverters": init,
            "motors":    init,
            "harness":   init,
        }
        self.target_inv = init

        # Production state
        self.production_k:         float = 0.0
        self.backlog_k:            float = 0.0
        self.halt_weeks:           int   = 0
        self.cumulative_loss_k:    float = 0.0

        # Shock state (e.g. Brexit friction, trade sanctions, plant closures)
        self.shock_multiplier:     float = 1.0
        self.is_shocked:           bool  = False

        # Metrics
        self.production_history:  List[float] = []
        self.backlog_history:     List[float] = []
        self.halt_history:        List[int]   = []
        self.inv_history:         List[float] = []

    def apply_shock(self, severity: float) -> None:
        """severity ∈ [0, 1]: fraction of OEM assembly throughput lost."""
        self.is_shocked       = True
        self.shock_multiplier = max(0.0, 1.0 - severity)

    def resolve_shock(self) -> None:
        self.is_shocked = False   # gradual recovery handled in step()

    def step(self) -> None:
        # Gradual recovery once shock is resolved
        if not self.is_shocked and self.shock_multiplier < 1.0:
            self.shock_multiplier = min(1.0, self.shock_multiplier + 0.04)
        # Receive component deliveries
        deliveries = self.model.get_component_deliveries(self.name)
        for comp, qty in deliveries.items():
            self.inv[comp] = min(
                self.inv.get(comp, 0.0) + qty,
                self.target_inv * 2.5
            )

        # Leontief: producible = min of all component inventories
        producible = min(self.inv.values())
        producible = max(0.0, producible)

        # Demand this week (from market + clear backlog)
        weekly_demand = self.model.get_oem_demand(self.name)
        target_prod   = min(
            weekly_demand + self.backlog_k * 0.15,
            self.weekly_target * 1.10   # max 110% of target
        )
        self.production_k = min(target_prod, producible) * self.shock_multiplier
        self.production_k = max(0.0, self.production_k)

        # Consume components (proportional to production)
        for comp in self.inv:
            self.inv[comp] = max(0.0, self.inv[comp] - self.production_k)

        # Halt detection
        if self.production_k < self.weekly_target * 0.10:
            self.halt_weeks += 1

        # Backlog and cumulative loss
        shortfall = max(0.0, weekly_demand - self.production_k)
        self.backlog_k = max(0.0, self.backlog_k + shortfall)
        self.cumulative_loss_k += shortfall

        # Record
        avg_inv = sum(self.inv.values()) / 4.0
        self.production_history.append(self.production_k)
        self.backlog_history.append(self.backlog_k)
        self.halt_history.append(1 if self.production_k < self.weekly_target * 0.10 else 0)
        self.inv_history.append(avg_inv)


# ── 5. Market Demand ──────────────────────────────────────────────────────────

class MarketAgent(Agent):
    """
    Generates EV demand for a region.
    Demand grows at a fixed YoY rate with price-elasticity response.
    """

    def __init__(self, agent_id: str, model,
                 region: str,
                 gwh_2023: float,
                 yoy_growth: float,
                 avg_kwh_per_veh: float,
                 price_elasticity: float = -0.30):
        super().__init__(agent_id, model)
        self.region           = region
        self.gwh_annual       = gwh_2023
        self.yoy_growth       = yoy_growth
        self.avg_kwh          = avg_kwh_per_veh
        self.price_elasticity = price_elasticity

        self._weekly_growth   = (1.0 + yoy_growth) ** (1.0 / 52.0) - 1.0
        self.weekly_demand_gwh = gwh_2023 / 52.0

        # Metrics
        self.demand_history: List[float] = []

    @property
    def weekly_demand_k_veh(self) -> float:
        return self.weekly_demand_gwh * 1000.0 / self.avg_kwh   # k vehicles

    def step(self) -> None:
        # Compound weekly growth
        self.weekly_demand_gwh *= (1.0 + self._weekly_growth)

        # Price response from SD model (global price index signal)
        price_idx = self.model.get_price_signal()
        price_effect = 1.0 + self.price_elasticity * (price_idx - 1.0)
        price_effect = max(0.60, min(1.20, price_effect))   # bound ±40%
        self.weekly_demand_gwh *= price_effect

        self.demand_history.append(self.weekly_demand_gwh)
