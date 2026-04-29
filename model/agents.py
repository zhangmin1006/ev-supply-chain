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

from .config import EV_GLOBAL_UNITS_2023_K
from .financial_profiles import DEFAULT_PROFILE, FinancialProfile

# Weekly capacity growth rates (applied each step)
# Cell capacity: IEA GEO 2024 29%/yr → weekly
_CELL_GROWTH_WK = (1.29 ** (1 / 52)) - 1   # ≈ 0.00491
# Tier-1 / OEM demand grows at same rate so Tier-1 capacity tracks demand
_TIER1_GROWTH_WK = _CELL_GROWTH_WK


# ── Base ─────────────────────────────────────────────────────────────────────

class Agent:
    def __init__(self, agent_id: str, model,
                 financial_profile: FinancialProfile | None = None):
        self.agent_id = agent_id
        self.model    = model
        self.financial_profile = financial_profile or DEFAULT_PROFILE
        # Set by archetype subclasses to override financial-profile shock_absorption.
        self._shock_absorption_override: Optional[float] = None

    def _effective_shock_severity(self, severity: float) -> float:
        # Archetype override takes priority over financial-profile calibration.
        absorption = (self._shock_absorption_override
                      if self._shock_absorption_override is not None
                      else self.financial_profile.shock_absorption)
        return max(0.0, min(1.0, severity * (1.0 - absorption)))

    def _sd_price_signal(self) -> float:
        """Single SD price signal used by agents for economic decisions."""
        return max(0.60, min(3.00, self.model.get_price_signal()))

    def _component_price_signal(self, name: str) -> float:
        """Component-specific SD price index used by tier-level decisions."""
        price = self.model.sd.component_prices.get(name, self._sd_price_signal())
        return max(0.60, min(3.00, price))

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
                 recovery_rate_wk: float = 0.04,
                 production_floor: float = 0.0,
                 price_sensitivity: float = 0.0,
                 financial_profile: FinancialProfile | None = None):
        super().__init__(agent_id, model, financial_profile)
        self.mineral            = mineral
        self.country            = country
        self.global_share       = global_share
        self.ev_share           = ev_share_of_global
        self.safety_stock_weeks = safety_stock_weeks * self.financial_profile.inventory_multiplier
        self.recovery_rate_wk   = recovery_rate_wk * self.financial_profile.recovery_multiplier
        # Floor below which shocks cannot reduce output (state/debt-service mandate).
        self.production_floor   = production_floor
        # Fraction of price-index deviation added as output boost when prices rise.
        self.price_sensitivity  = price_sensitivity

        # State
        self.shock_multiplier:  float = 1.0
        self.is_shocked:        bool  = False
        self.output_fraction:   float = 1.0   # fraction of baseline this week

        # Metrics
        self.output_history: List[float] = []

    # ── shock interface ───────────────────────────────────────────────────────

    def apply_shock(self, severity: float) -> None:
        """severity ∈ [0, 1]:  0 = no effect, 1 = total shutdown."""
        severity = self._effective_shock_severity(severity)
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

        # Production floor: state mandate or debt-service covenants prevent
        # output from falling below a minimum fraction even when shocked.
        self.output_fraction = max(self.production_floor, self.shock_multiplier)

        # Price-sensitivity: market-driven producers expand output when the
        # commodity price is above baseline (positive price signal).
        if self.price_sensitivity > 0.0:
            price_idx   = self.model.sd.prices.get(self.mineral, 1.0)
            price_boost = self.price_sensitivity * max(0.0, price_idx - 1.0)
            self.output_fraction = min(1.0, self.output_fraction + price_boost)

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
                 recovery_rate_wk: float = 0.04,
                 inventory_replenishment_weeks: float = 4.0,
                 financial_fragility: bool = False,
                 financial_profile: FinancialProfile | None = None):
        super().__init__(agent_id, model, financial_profile)
        # Thin-balance-sheet flag: amplifies effective shock severity by 50%.
        self.financial_fragility = financial_fragility
        self.name              = name
        self.country           = country
        self.weekly_capacity   = capacity_gwh_yr / 52.0   # GWh/week
        self.market_share      = market_share
        self.lfp_fraction      = lfp_fraction
        self.nmc_fraction      = nmc_fraction
        self.safety_stock_weeks = safety_stock_weeks * self.financial_profile.inventory_multiplier
        self.recovery_rate_wk  = recovery_rate_wk * self.financial_profile.recovery_multiplier
        self.capacity_growth_wk = _CELL_GROWTH_WK * self.financial_profile.growth_multiplier
        self.inventory_replenishment_weeks = max(1.0, inventory_replenishment_weeks)

        # Inventory
        self.inventory_gwh     = self.weekly_capacity * self.safety_stock_weeks
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

    def _effective_shock_severity(self, severity: float) -> float:
        if self.financial_fragility:
            severity = min(1.0, severity * 1.50)  # thin balance-sheet amplifier
        return super()._effective_shock_severity(severity)

    def _leontief_constraint(self, input_fracs: Dict[str, float]) -> float:
        """
        Leontief input constraint [0, 1], chemistry-aware.

        LFP cells are immune to cobalt shortage.  When the SD cobalt price
        index is high, the effective NMC fraction is gradually reduced to
        reflect industry-level chemistry substitution (F2 feedback loop).
        The SD lfp_share signal provides the market-level mix adjustment;
        individual agents retain their own chemistry profile but are modulated
        by the macro price signal.
        """
        li  = input_fracs.get("lithium",  1.0)
        co  = input_fracs.get("cobalt",   1.0)
        gr  = input_fracs.get("graphite", 1.0)

        # Read cobalt price from SD price_signals (1.0 = baseline)
        cobalt_price = self.model.sd.prices.get("cobalt", 1.0)

        # Effective NMC fraction: high cobalt price partially reduces cobalt
        # intensity as manufacturers blend in more LFP sub-cells / redesign.
        # Effect: at cobalt_price = 2.0, effective NMC fraction falls 15%.
        cobalt_price_adj = min(1.0, max(0.0, (cobalt_price - 1.0) * 0.15))
        effective_nmc   = self.nmc_fraction * (1.0 - cobalt_price_adj)
        effective_lfp   = 1.0 - effective_nmc   # conserves total to 1

        cobalt_effect = effective_lfp + effective_nmc * co

        return min(li, gr, cobalt_effect)

    def apply_shock(self, severity: float) -> None:
        severity = self._effective_shock_severity(severity)
        self.is_shocked       = True
        self.shock_multiplier = max(0.0, 1.0 - severity)

    def resolve_shock(self) -> None:
        self.is_shocked = False

    def step(self) -> None:
        price_signal = self._component_price_signal("pack")
        price_premium = max(0.0, price_signal - 1.0)
        price_discount = max(0.0, 1.0 - price_signal)
        growth_multiplier = max(0.25, 1.0 + 0.80 * price_premium - 0.40 * price_discount)
        self.weekly_capacity *= (1.0 + self.capacity_growth_wk * growth_multiplier)
        self.target_inventory = self.weekly_capacity * self.safety_stock_weeks

        # Gradual recovery
        if not self.is_shocked and self.shock_multiplier < 1.0:
            self.shock_multiplier = min(1.0,
                self.shock_multiplier + self.recovery_rate_wk)

        # Pull input availability from SD model
        input_fracs = self.model.sd.input_fractions
        constraint  = self._leontief_constraint(input_fracs)
        downstream_demand = self.model.get_cell_demand(self.name)

        # Order-up-to: produce extra to replenish inventory
        gap = max(0.0, self.target_inventory - self.inventory_gwh)
        production_incentive = 1.0 + min(0.12, 0.10 * price_premium)
        desired = (
            downstream_demand * production_incentive
            + min(gap / self.inventory_replenishment_weeks, downstream_demand * 0.10)
        )

        max_production = self.weekly_capacity * self.shock_multiplier * constraint
        self.output_gwh = min(desired, max_production)
        self.output_gwh = max(0.0, self.output_gwh)

        # Update inventory
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
                 bullwhip_factor: float = 1.25,
                 dual_source_threshold: float = 0.20,
                 financial_profile: FinancialProfile | None = None):
        super().__init__(agent_id, model, financial_profile)
        self.component          = component
        self.key_input          = key_input
        self.weekly_capacity    = capacity_k_yr / 52.0   # k units/week
        self.input_dependency   = input_dependency
        self.lead_time_weeks    = lead_time_weeks
        self.safety_stock_weeks    = safety_stock_weeks * self.financial_profile.inventory_multiplier
        self.recovery_rate_wk      = recovery_rate_wk * self.financial_profile.recovery_multiplier
        self.capacity_growth_wk    = _TIER1_GROWTH_WK * self.financial_profile.growth_multiplier
        self.bullwhip_factor       = bullwhip_factor
        self.dual_source_threshold = dual_source_threshold

        # Inventory (k vehicle-equiv)
        self.inventory          = self.weekly_capacity * self.safety_stock_weeks
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
        dependency = self.input_dependency
        if self.key_input == "ree":
            # Sustained rare-earth scarcity pushes OEMs/suppliers toward
            # induction, wound-rotor, or ferrite-assisted motor designs. This
            # keeps baseline growth from becoming a pure REE exhaustion story
            # while preserving sensitivity to acute REE shocks.
            ree_price = self.model.sd.prices.get("ree", 1.0)
            substitution = min(0.80, max(0.0, (ree_price - 1.0) * 0.75))
            dependency = max(0.12, self.input_dependency * (1.0 - substitution))

        frac = input_fracs.get(self.key_input, 1.0)
        return (1.0 - dependency) + dependency * frac

    def apply_shock(self, severity: float) -> None:
        severity = self._effective_shock_severity(severity)
        self.is_shocked       = True
        self.shock_multiplier = max(0.0, 1.0 - severity)

    def resolve_shock(self) -> None:
        self.is_shocked = False

    def step(self) -> None:
        price_name = {
            "battery_pack": "pack",
            "inverter": "inverter",
            "motor": "motor",
            "harness": "harness",
        }.get(self.component, "parts")
        price_signal = self._component_price_signal(price_name)
        price_premium = max(0.0, price_signal - 1.0)
        price_discount = max(0.0, 1.0 - price_signal)
        growth_multiplier = max(0.25, 1.0 + 0.60 * price_premium - 0.30 * price_discount)
        self.weekly_capacity *= (1.0 + self.capacity_growth_wk * growth_multiplier)
        self.target_inventory = self.weekly_capacity * self.safety_stock_weeks

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
        order_incentive = 1.0 + min(0.10, 0.08 * price_premium)
        order = (self.weekly_capacity * order_incentive + shortfall * self.bullwhip_factor)

        # Dual sourcing: boost order if critically low
        if self.inventory < self.target_inventory * self.dual_source_threshold:
            self.dual_source_active = True
            order *= 1.20   # 20% premium sourcing
        elif self.inventory > self.target_inventory * (self.dual_source_threshold * 3.0):
            self.dual_source_active = False

        self.pipeline.append(order)

        # Determine output this week
        input_fracs = self.model.sd.input_fractions
        constraint  = self._input_constraint(input_fracs)
        overtime = 1.0 + min(0.08, 0.06 * price_premium)
        max_out = self.weekly_capacity * overtime * self.shock_multiplier * constraint

        weekly_demand = self.model.get_component_demand(self.component)
        self.output_k = min(max_out, self.inventory, weekly_demand * 1.10)
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
                 dual_source_trigger: float = 0.20,
                 halt_threshold: float = 0.10,
                 vertical_integration: float = 0.0,
                 financial_profile: FinancialProfile | None = None):
        super().__init__(agent_id, model, financial_profile)
        # Production fraction below which a halt-week is recorded.
        self.halt_threshold = halt_threshold
        self.name                = name
        self.region              = region
        self.weekly_target       = annual_target_k / 52.0   # k vehicles/week
        self.safety_stock_weeks  = safety_stock_weeks * self.financial_profile.inventory_multiplier
        self.dual_source_trigger = dual_source_trigger
        self.vertical_integration = max(0.0, min(1.0, vertical_integration))
        self.recovery_rate_wk    = 0.04 * self.financial_profile.recovery_multiplier
        self.capacity_growth_wk  = _TIER1_GROWTH_WK * self.financial_profile.growth_multiplier

        # Component inventories (k vehicle-equivalents each)
        init = self.weekly_target * self.safety_stock_weeks
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
        severity = self._effective_shock_severity(severity)
        self.is_shocked       = True
        self.shock_multiplier = max(0.0, 1.0 - severity)

    def resolve_shock(self) -> None:
        self.is_shocked = False   # gradual recovery handled in step()

    def step(self) -> None:
        vehicle_price = self._component_price_signal("vehicle")
        component_cost = (
            0.72 * self._component_price_signal("pack")
            + 0.08 * self._component_price_signal("inverter")
            + 0.12 * self._component_price_signal("motor")
            + 0.08 * self._component_price_signal("harness")
        )
        margin_signal = vehicle_price / max(component_cost, 1e-9)
        price_premium = max(0.0, 1.0 - margin_signal)
        price_discount = max(0.0, margin_signal - 1.0)
        integration_buffer = 0.50 * self.vertical_integration
        margin_response = 1.0 - (0.45 - integration_buffer) * price_premium + 0.20 * price_discount
        margin_response = max(0.30, min(1.50, margin_response))
        self.weekly_target *= (1.0 + self.capacity_growth_wk * margin_response)
        self.target_inv = self.weekly_target * self.safety_stock_weeks

        # Gradual recovery once shock is resolved
        if not self.is_shocked and self.shock_multiplier < 1.0:
            self.shock_multiplier = min(1.0, self.shock_multiplier + self.recovery_rate_wk)
        # Receive component deliveries
        deliveries = self.model.get_component_deliveries(self.name)
        for comp, qty in deliveries.items():
            self.inv[comp] = min(
                self.inv.get(comp, 0.0) + qty,
                self.target_inv * 2.5
            )

        # Leontief: producible = min of all component inventories
        # Vertically integrated OEMs can internally bridge part of a component
        # shortfall (e.g. BYD cells/power electronics, Toyota/Panasonic ties).
        # The bridge is capped so integration cushions shortages without
        # erasing the Leontief assembly logic.
        internal_bridge = self.weekly_target * self.vertical_integration * 0.35
        effective_inv = {
            comp: qty + internal_bridge
            for comp, qty in self.inv.items()
        }
        producible = min(effective_inv.values())
        producible = max(0.0, producible)

        # Demand this week (from market + clear backlog)
        weekly_demand = self.model.get_oem_demand(self.name)
        target_prod   = min(
            weekly_demand + self.backlog_k * 0.15,
            self.weekly_target * 1.10   # max 110% of target
        )
        target_prod *= max(0.70, 1.0 - (0.20 - 0.10 * self.vertical_integration) * price_premium)
        self.production_k = min(target_prod, producible) * self.shock_multiplier
        self.production_k = max(0.0, self.production_k)

        # Consume components (proportional to production)
        for comp in self.inv:
            self.inv[comp] = max(0.0, self.inv[comp] - self.production_k)

        # Halt detection
        if self.production_k < self.weekly_target * self.halt_threshold:
            self.halt_weeks += 1

        # Backlog and cumulative loss. Production above current-week demand is
        # catch-up output and clears the waiting order book one-for-one.
        shortfall = max(0.0, weekly_demand - self.production_k)
        surplus   = max(0.0, self.production_k - weekly_demand)
        self.backlog_k = max(0.0, self.backlog_k + shortfall - surplus)
        self.cumulative_loss_k += shortfall

        # Record
        avg_inv = sum(self.inv.values()) / 4.0
        self.production_history.append(self.production_k)
        self.backlog_history.append(self.backlog_k)
        self.halt_history.append(1 if self.production_k < self.weekly_target * self.halt_threshold else 0)
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
                 price_elasticity: float = -0.30,
                 backlog_sensitivity: float = 0.35,
                 availability_floor: float = 0.55):
        super().__init__(agent_id, model)
        self.region           = region
        self.gwh_annual       = gwh_2023
        self.yoy_growth       = yoy_growth
        self.avg_kwh          = avg_kwh_per_veh
        self.price_elasticity = price_elasticity
        self.backlog_sensitivity = max(0.0, backlog_sensitivity)
        self.availability_floor = max(0.10, min(1.0, availability_floor))

        self._weekly_growth    = (1.0 + yoy_growth) ** (1.0 / 52.0) - 1.0
        self.weekly_demand_gwh = gwh_2023 / 52.0
        self._trend_demand_gwh = gwh_2023 / 52.0  # price-independent trend; step() applies price as level adjustment

        # Metrics
        self.demand_history: List[float] = []

    @property
    def weekly_demand_k_veh(self) -> float:
        return self.weekly_demand_gwh * 1000.0 / self.avg_kwh   # k vehicles

    def step(self) -> None:
        # Advance trend demand (compound growth, price-independent)
        self._trend_demand_gwh *= (1.0 + self._weekly_growth)

        # Price elasticity: apply as a LEVEL adjustment, not a compounding factor.
        # This means demand returns toward trend when prices normalise, instead
        # of permanently compounding the price impact week after week.
        price_idx    = self.model.get_price_signal()
        price_level  = 1.0 + self.price_elasticity * (price_idx - 1.0)
        price_level  = max(0.50, min(1.20, price_level))

        # Long order books reduce realised near-term demand as buyers defer,
        # cancel, or switch segments. This closes the market-availability
        # feedback loop and prevents the no-shock baseline from accumulating an
        # unbounded order backlog when material supply lags demand growth.
        backlog_scale = (
            self.model.get_backlog_scale_k()
            if hasattr(self.model, "get_backlog_scale_k")
            else EV_GLOBAL_UNITS_2023_K
        )
        backlog_ratio = self.model.sd.oem_backlog_k / max(backlog_scale, 1e-9)
        availability_level = max(
            self.availability_floor,
            min(1.0, 1.0 - self.backlog_sensitivity * backlog_ratio),
        )

        self.weekly_demand_gwh = self._trend_demand_gwh * price_level * availability_level
        self.demand_history.append(self.weekly_demand_gwh)


# =============================================================================
# Behavioural Archetype Subclasses
# =============================================================================
# Each archetype is a thin subclass that sets archetype-specific defaults and
# then overrides key behavioural attributes after calling super().__init__().
# The financial-profile calibration still refines safety_stock and recovery
# where no explicit override is applied; shock_absorption and growth are always
# set exactly as specified by the archetype.
# =============================================================================


# ── Tier 1 Archetypes: Mineral Suppliers ──────────────────────────────────────

class StateBacked(MineralSupplierAgent):
    """State-policy-driven producer (Chinese graphite/REE, cobalt-other).

    Behaviour: state mandate keeps output above 85% even when shocked.
    Low price sensitivity (production target set by policy, not market).
    Fast recovery because state support accelerates restart.
    """
    ARCHETYPE = "StateBacked"

    def __init__(self, agent_id: str, model,
                 mineral: str, country: str,
                 global_share: float, ev_share_of_global: float,
                 safety_stock_weeks: int = 4,
                 financial_profile: FinancialProfile | None = None):
        super().__init__(
            agent_id, model, mineral, country, global_share, ev_share_of_global,
            safety_stock_weeks=safety_stock_weeks,
            recovery_rate_wk=0.08,
            production_floor=0.85,
            price_sensitivity=0.10,
            financial_profile=financial_profile,
        )
        self.recovery_rate_wk           = 0.08   # state support → fast recovery
        self._shock_absorption_override = 0.40   # political backing buffers shocks


class WesternMiner(MineralSupplierAgent):
    """Listed western miner (Australian/Chilean lithium, DRC cobalt).

    Behaviour: market-driven output; meaningful price response; limited
    state support so shocks hit harder; moderate recovery.
    """
    ARCHETYPE = "WesternMiner"

    def __init__(self, agent_id: str, model,
                 mineral: str, country: str,
                 global_share: float, ev_share_of_global: float,
                 safety_stock_weeks: int = 4,
                 financial_profile: FinancialProfile | None = None):
        super().__init__(
            agent_id, model, mineral, country, global_share, ev_share_of_global,
            safety_stock_weeks=safety_stock_weeks,
            recovery_rate_wk=0.04,
            production_floor=0.60,
            price_sensitivity=0.35,
            financial_profile=financial_profile,
        )
        self.recovery_rate_wk           = 0.04
        self._shock_absorption_override = 0.15


class GreenfieldBuilder(MineralSupplierAgent):
    """Debt-service constrained, high-capex, must-run operator.

    Behaviour: covenant obligations force near-full utilisation (floor=0.90);
    almost zero price flexibility; very slow recovery after disruption because
    thin cash buffers extend repair and restart timelines.
    """
    ARCHETYPE = "GreenfieldBuilder"

    def __init__(self, agent_id: str, model,
                 mineral: str, country: str,
                 global_share: float, ev_share_of_global: float,
                 safety_stock_weeks: int = 4,
                 financial_profile: FinancialProfile | None = None):
        super().__init__(
            agent_id, model, mineral, country, global_share, ev_share_of_global,
            safety_stock_weeks=safety_stock_weeks,
            recovery_rate_wk=0.02,
            production_floor=0.90,
            price_sensitivity=0.05,
            financial_profile=financial_profile,
        )
        self.recovery_rate_wk           = 0.02   # thin cash → slow restart
        self._shock_absorption_override = 0.05   # nearly no financial cushion


# ── Tier 2 Archetypes: Cell Manufacturers ─────────────────────────────────────

class PlatformLeader(CellManufacturerAgent):
    """Technology and scale platform leader (CATL, BYD).

    Behaviour: strong balance sheet absorbs shocks; 10% extra safety stock;
    grows at market baseline rate (already at scale); high shock absorption.
    """
    ARCHETYPE = "PlatformLeader"

    def __init__(self, agent_id: str, model,
                 name: str, country: str,
                 capacity_gwh_yr: float, market_share: float,
                 lfp_fraction: float, nmc_fraction: float,
                 safety_stock_weeks: int = 4,
                 recovery_rate_wk: float = 0.04,
                 financial_profile: FinancialProfile | None = None):
        super().__init__(
            agent_id, model, name, country, capacity_gwh_yr, market_share,
            lfp_fraction, nmc_fraction,
            safety_stock_weeks=safety_stock_weeks,
            recovery_rate_wk=recovery_rate_wk,
            financial_profile=financial_profile,
        )
        self._shock_absorption_override = 0.35
        self.inventory_replenishment_weeks = 3.0
        # 10% larger safety buffer than base; adjust targets accordingly
        self.safety_stock_weeks *= 1.10
        self.target_inventory    = self.weekly_capacity * self.safety_stock_weeks
        self.inventory_gwh       = self.target_inventory
        # Grows at market baseline; not trying to hyper-scale from a small base
        self.capacity_growth_wk  = _CELL_GROWTH_WK * 1.0


class HyperScaleChallenger(CellManufacturerAgent):
    """Fast-growing challenger with thin balance sheet (CALB, others_cells).

    Behaviour: 1.8× growth ambition; 30% larger inventory as operational hedge
    for a fragile balance sheet; shocks are amplified (financial_fragility=True).
    """
    ARCHETYPE = "HyperScaleChallenger"

    def __init__(self, agent_id: str, model,
                 name: str, country: str,
                 capacity_gwh_yr: float, market_share: float,
                 lfp_fraction: float, nmc_fraction: float,
                 safety_stock_weeks: int = 4,
                 recovery_rate_wk: float = 0.04,
                 financial_profile: FinancialProfile | None = None):
        super().__init__(
            agent_id, model, name, country, capacity_gwh_yr, market_share,
            lfp_fraction, nmc_fraction,
            safety_stock_weeks=safety_stock_weeks,
            recovery_rate_wk=recovery_rate_wk,
            financial_fragility=True,
            financial_profile=financial_profile,
        )
        self._shock_absorption_override = 0.08
        self.inventory_replenishment_weeks = 5.0
        # 30% larger buffer to compensate for supply-chain inexperience
        self.safety_stock_weeks *= 1.30
        self.target_inventory    = self.weekly_capacity * self.safety_stock_weeks
        self.inventory_gwh       = self.target_inventory
        # Hyper-scale growth target
        self.capacity_growth_wk  = _CELL_GROWTH_WK * 1.80


class IncumbentUnderPressure(CellManufacturerAgent):
    """NMC-heavy incumbent losing share (LG ES, Panasonic, Samsung SDI, SK On).

    Behaviour: heavy cobalt exposure; below-market growth as OEMs diversify away;
    low shock absorption from thin EV-segment margins; slow recovery.
    """
    ARCHETYPE = "IncumbentUnderPressure"

    def __init__(self, agent_id: str, model,
                 name: str, country: str,
                 capacity_gwh_yr: float, market_share: float,
                 lfp_fraction: float, nmc_fraction: float,
                 safety_stock_weeks: int = 5,
                 recovery_rate_wk: float = 0.03,
                 financial_profile: FinancialProfile | None = None):
        super().__init__(
            agent_id, model, name, country, capacity_gwh_yr, market_share,
            lfp_fraction, nmc_fraction,
            safety_stock_weeks=safety_stock_weeks,
            recovery_rate_wk=recovery_rate_wk,
            financial_profile=financial_profile,
        )
        self.recovery_rate_wk           = 0.025  # slow restart under margin pressure
        self._shock_absorption_override = 0.05
        self.inventory_replenishment_weeks = 6.0
        # Below-market growth as market share erodes
        self.capacity_growth_wk         = _CELL_GROWTH_WK * 0.70


# ── Tier 3 Archetypes: Sub-system Suppliers ───────────────────────────────────

class PremiumPowerElectronics(Tier1SupplierAgent):
    """High-value power electronics (inverter tier): long lead, large buffer.

    Behaviour: 16-week lead time reflects fab capacity constraints; 8-week
    safety stock; early dual-sourcing trigger at 15% of target; strong
    balance sheets absorb shocks without immediate production cuts.
    """
    ARCHETYPE = "PremiumPowerElectronics"

    def __init__(self, agent_id: str, model,
                 component: str, key_input: str,
                 capacity_k_yr: float, input_dependency: float,
                 lead_time_weeks: int = 16,
                 safety_stock_weeks: int = 8,
                 recovery_rate_wk: float = 0.025,
                 bullwhip_factor: float = 1.25,
                 financial_profile: FinancialProfile | None = None):
        super().__init__(
            agent_id, model, component, key_input, capacity_k_yr, input_dependency,
            lead_time_weeks=lead_time_weeks,
            safety_stock_weeks=safety_stock_weeks,
            recovery_rate_wk=recovery_rate_wk,
            bullwhip_factor=bullwhip_factor,
            dual_source_threshold=0.15,
            financial_profile=financial_profile,
        )
        self._shock_absorption_override = 0.35
        self.recovery_rate_wk           = recovery_rate_wk


class EstablishedVolumeSupplier(Tier1SupplierAgent):
    """High-volume established supplier (motor, harness).

    Behaviour: moderate 8-week lead time; 4-week buffer; standard
    dual-sourcing trigger; solid but not exceptional resilience.
    """
    ARCHETYPE = "EstablishedVolumeSupplier"

    def __init__(self, agent_id: str, model,
                 component: str, key_input: str,
                 capacity_k_yr: float, input_dependency: float,
                 lead_time_weeks: int = 8,
                 safety_stock_weeks: int = 4,
                 recovery_rate_wk: float = 0.04,
                 bullwhip_factor: float = 1.25,
                 financial_profile: FinancialProfile | None = None):
        super().__init__(
            agent_id, model, component, key_input, capacity_k_yr, input_dependency,
            lead_time_weeks=lead_time_weeks,
            safety_stock_weeks=safety_stock_weeks,
            recovery_rate_wk=recovery_rate_wk,
            bullwhip_factor=bullwhip_factor,
            dual_source_threshold=0.20,
            financial_profile=financial_profile,
        )
        self._shock_absorption_override = 0.20
        self.recovery_rate_wk           = recovery_rate_wk


class BatteryPackIntegrator(Tier1SupplierAgent):
    """Battery pack assembly integrator: pure downstream of cell supply.

    Behaviour: 4-week lead time; 3-week lean buffer; directly exposed to
    cell availability; limited ability to absorb input shocks.
    """
    ARCHETYPE = "BatteryPackIntegrator"

    def __init__(self, agent_id: str, model,
                 component: str, key_input: str,
                 capacity_k_yr: float, input_dependency: float,
                 lead_time_weeks: int = 4,
                 safety_stock_weeks: int = 3,
                 recovery_rate_wk: float = 0.05,
                 bullwhip_factor: float = 1.25,
                 financial_profile: FinancialProfile | None = None):
        super().__init__(
            agent_id, model, component, key_input, capacity_k_yr, input_dependency,
            lead_time_weeks=lead_time_weeks,
            safety_stock_weeks=safety_stock_weeks,
            recovery_rate_wk=recovery_rate_wk,
            bullwhip_factor=bullwhip_factor,
            dual_source_threshold=0.20,
            financial_profile=financial_profile,
        )
        self._shock_absorption_override = 0.10
        self.recovery_rate_wk           = recovery_rate_wk


# ── Tier 4 Archetypes: OEMs ───────────────────────────────────────────────────

class ProfitableEstablishedOEM(OEMAgent):
    """Profitable, stable OEM with strong balance sheet (Korean, Japanese).

    Behaviour: 6-week buffer; strong shock absorption; fast recovery
    underpinned by solid free cash flow and conservative supply-chain policy.
    """
    ARCHETYPE = "ProfitableEstablishedOEM"

    def __init__(self, agent_id: str, model,
                 name: str, region: str, annual_target_k: int,
                 safety_stock_weeks: int = 6,
                 dual_source_trigger: float = 0.20,
                 vertical_integration: float = 0.0,
                 financial_profile: FinancialProfile | None = None):
        super().__init__(
            agent_id, model, name, region, annual_target_k,
            safety_stock_weeks=safety_stock_weeks,
            dual_source_trigger=dual_source_trigger,
            vertical_integration=vertical_integration,
            financial_profile=financial_profile,
        )
        self._shock_absorption_override = 0.45
        self.recovery_rate_wk           = 0.06 * self.financial_profile.recovery_multiplier
        # Enforce archetype's 6-week buffer regardless of config value
        self.safety_stock_weeks = 6.0 * self.financial_profile.inventory_multiplier
        self.target_inv         = self.weekly_target * self.safety_stock_weeks
        for comp in self.inv:
            self.inv[comp] = self.target_inv


class TransitioningLegacyOEM(OEMAgent):
    """ICE-to-EV transitioning legacy OEM (German, US, UK OEMs).

    Behaviour: 4-week buffer (ICE-era JIT habits); cost pressure limits
    buffer investment; moderate shock tolerance; medium recovery.
    """
    ARCHETYPE = "TransitioningLegacyOEM"

    def __init__(self, agent_id: str, model,
                 name: str, region: str, annual_target_k: int,
                 safety_stock_weeks: int = 4,
                 dual_source_trigger: float = 0.20,
                 vertical_integration: float = 0.0,
                 financial_profile: FinancialProfile | None = None):
        super().__init__(
            agent_id, model, name, region, annual_target_k,
            safety_stock_weeks=safety_stock_weeks,
            dual_source_trigger=dual_source_trigger,
            vertical_integration=vertical_integration,
            financial_profile=financial_profile,
        )
        self._shock_absorption_override = 0.15
        self.recovery_rate_wk           = 0.04 * self.financial_profile.recovery_multiplier
        self.safety_stock_weeks = 4.0 * self.financial_profile.inventory_multiplier
        self.target_inv         = self.weekly_target * self.safety_stock_weeks
        for comp in self.inv:
            self.inv[comp] = self.target_inv


class EVNativeScaleAspirant(OEMAgent):
    """EV-native Chinese aspirant scaling rapidly (other_chinese_oem group).

    Behaviour: lean 3.5-week buffer (aggressive JIT); high growth aspiration
    (1.4× baseline); moderate shock tolerance from domestic supply-chain proximity.
    """
    ARCHETYPE = "EVNativeScaleAspirant"

    def __init__(self, agent_id: str, model,
                 name: str, region: str, annual_target_k: int,
                 safety_stock_weeks: int = 4,
                 dual_source_trigger: float = 0.20,
                 vertical_integration: float = 0.0,
                 financial_profile: FinancialProfile | None = None):
        super().__init__(
            agent_id, model, name, region, annual_target_k,
            safety_stock_weeks=safety_stock_weeks,
            dual_source_trigger=dual_source_trigger,
            vertical_integration=vertical_integration,
            financial_profile=financial_profile,
        )
        self._shock_absorption_override = 0.25
        # Lean 3.5-week buffer (aggressive JIT scaling mindset)
        self.safety_stock_weeks = 3.5 * self.financial_profile.inventory_multiplier
        self.target_inv         = self.weekly_target * self.safety_stock_weeks
        for comp in self.inv:
            self.inv[comp] = self.target_inv
        # High growth target: 40% above market baseline
        self.capacity_growth_wk = _TIER1_GROWTH_WK * 1.40


class PrecommercialStartup(OEMAgent):
    """Pre-commercial EV startup with thin capital reserves.

    Behaviour: 2-week skeleton buffer; near-zero shock absorption; halts
    when production drops below 15% of target (tighter trip-wire than incumbents);
    very slow recovery due to constrained access to capital.
    """
    ARCHETYPE = "PrecommercialStartup"

    def __init__(self, agent_id: str, model,
                 name: str, region: str, annual_target_k: int,
                 safety_stock_weeks: int = 2,
                 dual_source_trigger: float = 0.20,
                 vertical_integration: float = 0.0,
                 financial_profile: FinancialProfile | None = None):
        super().__init__(
            agent_id, model, name, region, annual_target_k,
            safety_stock_weeks=safety_stock_weeks,
            dual_source_trigger=dual_source_trigger,
            halt_threshold=0.15,
            vertical_integration=vertical_integration,
            financial_profile=financial_profile,
        )
        self._shock_absorption_override = 0.03
        self.recovery_rate_wk           = 0.02
        self.safety_stock_weeks = 2.0 * self.financial_profile.inventory_multiplier
        self.target_inv         = self.weekly_target * self.safety_stock_weeks
        for comp in self.inv:
            self.inv[comp] = self.target_inv
