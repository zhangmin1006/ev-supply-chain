"""
ABM Agent Classes
=================
Five agent types model heterogeneous firm behaviour at each supply chain tier.

Decision rules
--------------
  MineralSupplierAgent  — output follows capacity, reduced by shock multiplier.
                          Gradual recovery after shock resolves.
                          Archetype-specific rules via _compute_output_fraction().
  CellManufacturerAgent — Leontief production function over (Li, Co, graphite).
                          Order-up-to inventory policy; chemistry mix determines
                          cobalt sensitivity.
                          Archetype-specific rules via _desired_production().
  Tier1SupplierAgent    — Order-up-to with lead-time pipeline; activates dual
                          sourcing (at 20% cost premium) when critically short.
                          Archetype-specific rules via _order_quantity().
  OEMAgent              — Leontief over all four sub-system inputs (pack,
                          inverter, motor, harness); records halt weeks.
                          Archetype-specific rules via _compute_production_target().
  MarketAgent           — Exogenous demand with YoY growth and price elasticity.

Coupling
--------
  Agents read `input_fractions` from the SD model before stepping.
  After stepping, HybridModel aggregates their outputs into SD flows.

Archetype decision-rule hooks
------------------------------
  Each base class exposes one or two protected methods that encode the core
  decision logic for that tier.  Archetype subclasses override these methods
  to implement qualitatively different behaviours — not just different
  parameter values — while inheriting all common mechanics (shock interface,
  growth compounding, history recording, etc.) from the base class.

  MineralSupplierAgent._compute_output_fraction() -> float
      Returns the output fraction [0, 1] this week.

  CellManufacturerAgent._desired_production(demand, price_premium) -> float
      Returns desired GWh production before capacity/constraint cap.

  Tier1SupplierAgent._order_quantity(weekly_demand, price_premium) -> float
      Returns units to add to the order pipeline this week.

  OEMAgent._compute_production_target(demand, producible, price_premium,
                                       price_discount) -> float
      Returns k vehicles to assemble this week (after all constraints).
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
        self.data_source_calibration = None
        self.data_source_note = ""
        self.data_source_shock_absorption = 0.0

    def _effective_shock_severity(self, severity: float) -> float:
        # Archetype override takes priority over financial-profile calibration.
        base_absorption = (self._shock_absorption_override
                           if self._shock_absorption_override is not None
                           else self.financial_profile.shock_absorption)
        source_absorption = max(0.0, min(0.50, self.data_source_shock_absorption))
        absorption = 1.0 - ((1.0 - base_absorption) * (1.0 - source_absorption))
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

    Archetype hook
    --------------
    Override _compute_output_fraction() to change how output is determined
    from shock_multiplier, production_floor, and price signals.
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
        self.production_floor   = production_floor
        self.price_sensitivity  = price_sensitivity

        # State
        self.shock_multiplier:  float = 1.0
        self.is_shocked:        bool  = False
        self.output_fraction:   float = 1.0

        # Metrics
        self.output_history: List[float] = []

    # ── shock interface ───────────────────────────────────────────────────────

    def apply_shock(self, severity: float) -> None:
        severity = self._effective_shock_severity(severity)
        self.is_shocked       = True
        self.shock_multiplier = max(0.0, 1.0 - severity)

    def resolve_shock(self) -> None:
        self.is_shocked = False

    # ── archetype decision hook ───────────────────────────────────────────────

    def _compute_output_fraction(self) -> float:
        """
        Base rule: floor-bounded shock multiplier + upward price response.
        Archetypes override this to implement qualitatively different production
        decision rules (quota-driven, market-driven, must-run, etc.).
        """
        output = max(self.production_floor, self.shock_multiplier)
        if self.price_sensitivity > 0.0:
            price_idx   = self.model.sd.prices.get(self.mineral, 1.0)
            price_boost = self.price_sensitivity * max(0.0, price_idx - 1.0)
            output      = min(1.0, output + price_boost)
        return output

    # ── step ─────────────────────────────────────────────────────────────────

    def step(self) -> None:
        if not self.is_shocked and self.shock_multiplier < 1.0:
            self.shock_multiplier = min(1.0,
                self.shock_multiplier + self.recovery_rate_wk)
        self.output_fraction = self._compute_output_fraction()
        self.output_history.append(self.output_fraction)

    # ── query ─────────────────────────────────────────────────────────────────

    @property
    def weekly_supply_contribution(self) -> float:
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

    Archetype hook
    --------------
    Override _desired_production(downstream_demand, price_premium) to change
    how much the agent wants to produce before capacity and input constraints
    are applied.
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
        self.financial_fragility = financial_fragility
        self.name              = name
        self.country           = country
        self.weekly_capacity   = capacity_gwh_yr / 52.0
        self.market_share      = market_share
        self.lfp_fraction      = lfp_fraction
        self.nmc_fraction      = nmc_fraction
        self.safety_stock_weeks = safety_stock_weeks * self.financial_profile.inventory_multiplier
        self.recovery_rate_wk  = recovery_rate_wk * self.financial_profile.recovery_multiplier
        self.capacity_growth_wk = _CELL_GROWTH_WK * self.financial_profile.growth_multiplier
        self.inventory_replenishment_weeks = max(1.0, inventory_replenishment_weeks)

        self.inventory_gwh     = self.weekly_capacity * self.safety_stock_weeks
        self.target_inventory  = self.inventory_gwh

        self.shock_multiplier:  float = 1.0
        self.is_shocked:        bool  = False
        self.output_gwh:        float = 0.0
        self.backlog_gwh:       float = 0.0

        self.output_history:      List[float] = []
        self.inventory_history:   List[float] = []
        self.utilisation_history: List[float] = []

    def _effective_shock_severity(self, severity: float) -> float:
        if self.financial_fragility:
            severity = min(1.0, severity * 1.50)
        return super()._effective_shock_severity(severity)

    def _leontief_constraint(self, input_fracs: Dict[str, float]) -> float:
        """
        Leontief input constraint [0, 1], chemistry-aware.

        LFP cells are immune to cobalt shortage.  When the SD cobalt price
        index is high, the effective NMC fraction is gradually reduced to
        reflect industry-level chemistry substitution (F2 feedback loop).
        """
        li  = input_fracs.get("lithium",  1.0)
        co  = input_fracs.get("cobalt",   1.0)
        gr  = input_fracs.get("graphite", 1.0)

        cobalt_price     = self.model.sd.prices.get("cobalt", 1.0)
        cobalt_price_adj = min(1.0, max(0.0, (cobalt_price - 1.0) * 0.15))
        effective_nmc    = self.nmc_fraction * (1.0 - cobalt_price_adj)
        effective_lfp    = 1.0 - effective_nmc

        cobalt_effect = effective_lfp + effective_nmc * co
        return min(li, gr, cobalt_effect)

    def apply_shock(self, severity: float) -> None:
        severity = self._effective_shock_severity(severity)
        self.is_shocked       = True
        self.shock_multiplier = max(0.0, 1.0 - severity)

    def resolve_shock(self) -> None:
        self.is_shocked = False

    # ── archetype decision hook ───────────────────────────────────────────────

    def _desired_production(self, downstream_demand: float,
                            price_premium: float) -> float:
        """
        Base rule: demand-pull with inventory top-up incentive.
        Archetypes override to implement push/pull/strategic-stockpile logic.
        """
        gap = max(0.0, self.target_inventory - self.inventory_gwh)
        production_incentive = 1.0 + min(0.12, 0.10 * price_premium)
        return (
            downstream_demand * production_incentive
            + min(gap / self.inventory_replenishment_weeks,
                  downstream_demand * 0.10)
        )

    # ── step ─────────────────────────────────────────────────────────────────

    def step(self) -> None:
        price_signal  = self._component_price_signal("pack")
        price_premium = max(0.0, price_signal - 1.0)
        price_discount= max(0.0, 1.0 - price_signal)
        growth_multiplier = max(0.25,
            1.0 + 0.80 * price_premium - 0.40 * price_discount)
        self.weekly_capacity   *= (1.0 + self.capacity_growth_wk * growth_multiplier)
        self.target_inventory   = self.weekly_capacity * self.safety_stock_weeks

        if not self.is_shocked and self.shock_multiplier < 1.0:
            self.shock_multiplier = min(1.0,
                self.shock_multiplier + self.recovery_rate_wk)

        input_fracs       = self.model.sd.input_fractions
        constraint        = self._leontief_constraint(input_fracs)
        downstream_demand = self.model.get_cell_demand(self.name)

        desired        = self._desired_production(downstream_demand, price_premium)
        max_production = self.weekly_capacity * self.shock_multiplier * constraint
        self.output_gwh = max(0.0, min(desired, max_production))

        fulfilled          = min(downstream_demand,
                                 self.inventory_gwh + self.output_gwh)
        self.inventory_gwh = max(0.0,
            self.inventory_gwh + self.output_gwh - fulfilled)
        self.backlog_gwh   = max(0.0, downstream_demand - fulfilled)

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

    Archetype hook
    --------------
    Override _order_quantity(weekly_demand, price_premium) to change how
    agents size their orders (JIT pass-through, forward-looking, smoothed, etc.).
    """

    def __init__(self, agent_id: str, model,
                 component: str,
                 key_input: str,
                 capacity_k_yr: float,
                 input_dependency: float,
                 lead_time_weeks: int,
                 safety_stock_weeks: int,
                 recovery_rate_wk: float = 0.04,
                 bullwhip_factor: float = 1.25,
                 dual_source_threshold: float = 0.20,
                 financial_profile: FinancialProfile | None = None):
        super().__init__(agent_id, model, financial_profile)
        self.component          = component
        self.key_input          = key_input
        self.weekly_capacity    = capacity_k_yr / 52.0
        self.input_dependency   = input_dependency
        self.lead_time_weeks    = lead_time_weeks
        self.safety_stock_weeks    = safety_stock_weeks * self.financial_profile.inventory_multiplier
        self.recovery_rate_wk      = recovery_rate_wk * self.financial_profile.recovery_multiplier
        self.capacity_growth_wk    = _TIER1_GROWTH_WK * self.financial_profile.growth_multiplier
        self.bullwhip_factor       = bullwhip_factor
        self.dual_source_threshold = dual_source_threshold

        self.inventory          = self.weekly_capacity * self.safety_stock_weeks
        self.target_inventory   = self.inventory

        self.pipeline: List[float] = [self.weekly_capacity] * lead_time_weeks

        self.shock_multiplier:   float = 1.0
        self.is_shocked:         bool  = False
        self.output_k:           float = 0.0
        self.dual_source_active: bool  = False

        self.output_history:    List[float] = []
        self.inventory_history: List[float] = []
        self.shortage_history:  List[float] = []

    def _input_constraint(self, input_fracs: Dict[str, float]) -> float:
        """
        For components that partially depend on a critical input (SiC, REE):
        effective constraint = (1 - dependency) + dependency × input_fraction
        """
        dependency = self.input_dependency
        if self.key_input == "ree":
            ree_price    = self.model.sd.prices.get("ree", 1.0)
            substitution = min(0.80, max(0.0, (ree_price - 1.0) * 0.75))
            dependency   = max(0.12, self.input_dependency * (1.0 - substitution))

        frac = input_fracs.get(self.key_input, 1.0)
        return (1.0 - dependency) + dependency * frac

    def apply_shock(self, severity: float) -> None:
        severity = self._effective_shock_severity(severity)
        self.is_shocked       = True
        self.shock_multiplier = max(0.0, 1.0 - severity)

    def resolve_shock(self) -> None:
        self.is_shocked = False

    # ── archetype decision hook ───────────────────────────────────────────────

    def _order_quantity(self, weekly_demand: float,
                        price_premium: float) -> float:
        """
        Base rule: order-up-to with bullwhip amplification and dual-sourcing
        boost when critically short.
        Archetypes override to implement JIT pass-through, forward-looking
        demand projection, or production-smoothing policies.
        """
        in_transit   = sum(self.pipeline)
        inv_position = self.inventory + in_transit
        shortfall    = max(0.0, self.target_inventory - inv_position)
        order_incentive = 1.0 + min(0.10, 0.08 * price_premium)
        order = (self.weekly_capacity * order_incentive
                 + shortfall * self.bullwhip_factor)

        if self.inventory < self.target_inventory * self.dual_source_threshold:
            self.dual_source_active = True
            order *= 1.20
        elif self.inventory > self.target_inventory * (self.dual_source_threshold * 3.0):
            self.dual_source_active = False
        return order

    # ── step ─────────────────────────────────────────────────────────────────

    def step(self) -> None:
        price_name = {
            "battery_pack": "pack",
            "inverter":     "inverter",
            "motor":        "motor",
            "harness":      "harness",
        }.get(self.component, "parts")
        price_signal  = self._component_price_signal(price_name)
        price_premium = max(0.0, price_signal - 1.0)
        price_discount= max(0.0, 1.0 - price_signal)
        growth_multiplier = max(0.25,
            1.0 + 0.60 * price_premium - 0.30 * price_discount)
        self.weekly_capacity   *= (1.0 + self.capacity_growth_wk * growth_multiplier)
        self.target_inventory   = self.weekly_capacity * self.safety_stock_weeks

        if not self.is_shocked and self.shock_multiplier < 1.0:
            self.shock_multiplier = min(1.0,
                self.shock_multiplier + self.recovery_rate_wk)

        # Receive oldest pipeline delivery
        if self.pipeline:
            delivery        = self.pipeline.pop(0)
            input_fracs     = self.model.sd.input_fractions
            delivery_actual = delivery * self._input_constraint(input_fracs)
            self.inventory  = min(
                self.inventory + delivery_actual,
                self.target_inventory * 2.5
            )

        # Query current demand (available to _order_quantity implementations)
        weekly_demand = self.model.get_component_demand(self.component)

        # Place new order via archetype decision hook
        order = self._order_quantity(weekly_demand, price_premium)
        self.pipeline.append(order)

        # Determine output this week
        input_fracs = self.model.sd.input_fractions
        constraint  = self._input_constraint(input_fracs)
        overtime    = 1.0 + min(0.08, 0.06 * price_premium)
        max_out     = (self.weekly_capacity * overtime
                       * self.shock_multiplier * constraint)

        self.output_k = min(max_out, self.inventory, weekly_demand * 1.10)
        self.inventory = max(0.0, self.inventory - self.output_k)
        shortage = max(0.0, weekly_demand - self.output_k)

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

    Archetype hook
    --------------
    Override _compute_production_target(weekly_demand, producible,
    price_premium, price_discount) to implement different ordering priorities,
    ICE fallback logic, capital constraints, or domestic-proximity advantages.
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
        self.halt_threshold      = halt_threshold
        self.name                = name
        self.region              = region
        self.weekly_target       = annual_target_k / 52.0
        self.safety_stock_weeks  = safety_stock_weeks * self.financial_profile.inventory_multiplier
        self.dual_source_trigger = dual_source_trigger
        self.vertical_integration= max(0.0, min(1.0, vertical_integration))
        self.recovery_rate_wk    = 0.04 * self.financial_profile.recovery_multiplier
        self.capacity_growth_wk  = _TIER1_GROWTH_WK * self.financial_profile.growth_multiplier

        init = self.weekly_target * self.safety_stock_weeks
        self.inv: Dict[str, float] = {
            "packs":     init,
            "inverters": init,
            "motors":    init,
            "harness":   init,
        }
        self.target_inv = init

        self.production_k:      float = 0.0
        self.backlog_k:         float = 0.0
        self.halt_weeks:        int   = 0
        self.cumulative_loss_k: float = 0.0

        self.shock_multiplier:  float = 1.0
        self.is_shocked:        bool  = False

        self.production_history: List[float] = []
        self.backlog_history:    List[float]  = []
        self.halt_history:       List[int]    = []
        self.inv_history:        List[float]  = []

    def apply_shock(self, severity: float) -> None:
        severity = self._effective_shock_severity(severity)
        self.is_shocked       = True
        self.shock_multiplier = max(0.0, 1.0 - severity)

    def resolve_shock(self) -> None:
        self.is_shocked = False

    # ── archetype decision hook ───────────────────────────────────────────────

    def _compute_production_target(self, weekly_demand: float,
                                   producible: float,
                                   price_premium: float,
                                   price_discount: float) -> float:
        """
        Base rule: demand-pull with backlog clearance, Leontief cap, margin
        adjustment, and shock multiplier.
        Archetypes override to implement buffer-first, ICE-fallback, capital-
        constrained, or demand-elastic production policies.
        """
        target_prod = min(
            weekly_demand + self.backlog_k * 0.15,
            self.weekly_target * 1.10
        )
        target_prod *= max(
            0.70,
            1.0 - (0.20 - 0.10 * self.vertical_integration) * price_premium
        )
        return max(0.0, min(target_prod, producible) * self.shock_multiplier)

    # ── step ─────────────────────────────────────────────────────────────────

    def step(self) -> None:
        vehicle_price  = self._component_price_signal("vehicle")
        component_cost = (
            0.72 * self._component_price_signal("pack")
            + 0.08 * self._component_price_signal("inverter")
            + 0.12 * self._component_price_signal("motor")
            + 0.08 * self._component_price_signal("harness")
        )
        margin_signal  = vehicle_price / max(component_cost, 1e-9)
        price_premium  = max(0.0, 1.0 - margin_signal)
        price_discount = max(0.0, margin_signal - 1.0)

        integration_buffer = 0.50 * self.vertical_integration
        margin_response = (1.0
                           - (0.45 - integration_buffer) * price_premium
                           + 0.20 * price_discount)
        margin_response = max(0.30, min(1.50, margin_response))
        self.weekly_target *= (1.0 + self.capacity_growth_wk * margin_response)
        self.target_inv     = self.weekly_target * self.safety_stock_weeks

        if not self.is_shocked and self.shock_multiplier < 1.0:
            self.shock_multiplier = min(1.0,
                self.shock_multiplier + self.recovery_rate_wk)

        # Receive component deliveries
        deliveries = self.model.get_component_deliveries(self.name)
        for comp, qty in deliveries.items():
            self.inv[comp] = min(
                self.inv.get(comp, 0.0) + qty,
                self.target_inv * 2.5
            )

        # Leontief: producible = min of all component inventories
        internal_bridge = self.weekly_target * self.vertical_integration * 0.35
        effective_inv   = {comp: qty + internal_bridge
                           for comp, qty in self.inv.items()}
        producible = max(0.0, min(effective_inv.values()))

        weekly_demand     = self.model.get_oem_demand(self.name)
        self.production_k = self._compute_production_target(
            weekly_demand, producible, price_premium, price_discount
        )

        # Consume components proportional to production
        for comp in self.inv:
            self.inv[comp] = max(0.0, self.inv[comp] - self.production_k)

        # Halt detection
        if self.production_k < self.weekly_target * self.halt_threshold:
            self.halt_weeks += 1

        shortfall          = max(0.0, weekly_demand - self.production_k)
        surplus            = max(0.0, self.production_k - weekly_demand)
        self.backlog_k     = max(0.0, self.backlog_k + shortfall - surplus)
        self.cumulative_loss_k += shortfall

        avg_inv = sum(self.inv.values()) / 4.0
        self.production_history.append(self.production_k)
        self.backlog_history.append(self.backlog_k)
        self.halt_history.append(
            1 if self.production_k < self.weekly_target * self.halt_threshold else 0
        )
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
        self.region              = region
        self.gwh_annual          = gwh_2023
        self.yoy_growth          = yoy_growth
        self.avg_kwh             = avg_kwh_per_veh
        self.price_elasticity    = price_elasticity
        self.backlog_sensitivity = max(0.0, backlog_sensitivity)
        self.availability_floor  = max(0.10, min(1.0, availability_floor))

        self._weekly_growth     = (1.0 + yoy_growth) ** (1.0 / 52.0) - 1.0
        self.weekly_demand_gwh  = gwh_2023 / 52.0
        self._trend_demand_gwh  = gwh_2023 / 52.0

        self.demand_history: List[float] = []

    @property
    def weekly_demand_k_veh(self) -> float:
        return self.weekly_demand_gwh * 1000.0 / self.avg_kwh

    def step(self) -> None:
        self._trend_demand_gwh *= (1.0 + self._weekly_growth)

        price_idx   = self.model.get_price_signal()
        price_level = 1.0 + self.price_elasticity * (price_idx - 1.0)
        price_level = max(0.50, min(1.20, price_level))

        backlog_scale = (
            self.model.get_backlog_scale_k()
            if hasattr(self.model, "get_backlog_scale_k")
            else EV_GLOBAL_UNITS_2023_K
        )
        backlog_ratio = (self.model.sd.oem_backlog_k
                         / max(backlog_scale, 1e-9))
        availability_level = max(
            self.availability_floor,
            min(1.0, 1.0 - self.backlog_sensitivity * backlog_ratio),
        )

        self.weekly_demand_gwh = (self._trend_demand_gwh
                                  * price_level * availability_level)
        self.demand_history.append(self.weekly_demand_gwh)


# =============================================================================
# Behavioural Archetype Subclasses
# =============================================================================
# Each archetype is a thin subclass that:
#   (a) sets archetype-specific constructor defaults, and
#   (b) overrides the tier's decision-rule hook with qualitatively different
#       logic reflecting the archetype's strategic posture.
#
# Common mechanics (shock interface, growth compounding, Leontief constraint,
# history recording) are inherited unchanged from the base class.
# =============================================================================


# ── Tier 0 Archetypes: Mineral Suppliers ──────────────────────────────────────

class StateBacked(MineralSupplierAgent):
    """State-policy-driven producer (Chinese graphite/REE, cobalt-other).

    Decision rule (_compute_output_fraction)
    -----------------------------------------
    Output follows a state-set production quota, not market price signals:
      - Below the restriction trigger (price ≤ 1.8): output = max(floor, shock_mult)
        with only a weak upward price response (policy limits market-driven expansion).
      - Above the restriction trigger (price > 1.8): strategic export control kicks in,
        capping output at the production floor (restricting exports when prices spike
        rather than cashing in — e.g. China REE/graphite quota behaviour).
    Recovery is fast because state support (subsidies, strategic reserve drawdown)
    accelerates restart after disruptions.
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
        self.recovery_rate_wk           = 0.08
        self._shock_absorption_override = 0.40
        # Price above which state restricts exports rather than expanding them
        self._restriction_trigger: float = 1.80

    def _compute_output_fraction(self) -> float:
        output    = max(self.production_floor, self.shock_multiplier)
        price_idx = self.model.sd.prices.get(self.mineral, 1.0)

        if price_idx > self._restriction_trigger:
            # Strategic export restriction: cap output at the policy floor.
            # State actors restrict supply when prices spike (quota behaviour,
            # export licensing) rather than maximising revenue at market rates.
            return min(output, self.production_floor)

        # Below restriction threshold: modest upward quota expansion when prices
        # are above baseline (state earmarks extra output for domestic industry).
        if price_idx > 1.0:
            output = min(1.0, output + self.price_sensitivity * (price_idx - 1.0))
        return output


class WesternMiner(MineralSupplierAgent):
    """Listed western miner (Australian/Chilean lithium, DRC cobalt).

    Decision rule (_compute_output_fraction)
    -----------------------------------------
    Market-price-driven production with care-and-maintenance mothballing:
      - Tracks consecutive weeks below a low-price threshold.
      - After 12+ weeks of depressed prices, mines go on care-and-maintenance
        (effective floor drops by 15%) — modelling capex-heavy operators deferring
        variable costs when spot prices don't cover operating costs.
      - Strong upward price response: expands output above baseline when prices
        are elevated (investment thesis justification for premium suppliers).
    Limited state support means shocks hit harder and recovery is slower.
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

        # Care-and-maintenance (mothballing) state
        self._low_price_weeks:       int   = 0
        self._mothball_threshold:    float = 0.85  # price index below which weeks accumulate
        self._mothball_trigger_wks:  int   = 12    # consecutive low-price weeks to trigger
        self._mothball_floor_drop:   float = 0.15  # floor reduction when mothballed

    def _compute_output_fraction(self) -> float:
        price_idx = self.model.sd.prices.get(self.mineral, 1.0)

        # Care-and-maintenance counter: sustained low prices force mines offline
        if price_idx < self._mothball_threshold:
            self._low_price_weeks += 1
        else:
            # Prices recovering: count resets faster than it accumulates
            self._low_price_weeks = max(0, self._low_price_weeks - 2)

        output = self.shock_multiplier   # start from current operational capacity

        if self._low_price_weeks >= self._mothball_trigger_wks:
            # Care-and-maintenance: voluntarily cap output at a reduced ceiling.
            # Mines placed on care-and-maintenance when spot prices don't cover
            # operating costs — output is actively curtailed, not just floored.
            effective_ceiling = max(0.0,
                self.production_floor - self._mothball_floor_drop)
            output = min(output, effective_ceiling)
        else:
            # Normal operations: floor ensures minimum contractual output
            output = max(self.production_floor, output)

        # Strong market-responsive expansion when prices are above baseline
        price_boost = self.price_sensitivity * max(0.0, price_idx - 1.0)
        return min(1.0, output + price_boost)


class GreenfieldBuilder(MineralSupplierAgent):
    """Debt-service constrained, high-capex, must-run operator.

    Decision rule (_compute_output_fraction)
    -----------------------------------------
    Debt covenants force near-full utilisation regardless of price (floor=0.90).
    Adds a distress-spiral mechanism: if production is severely impaired for an
    extended period (shock_multiplier < 0.50 for > 8 weeks), the operator enters
    covenant breach, triggering asset degradation — modelling forced equipment
    sales, deferred maintenance, and partial mine closure that permanently erode
    capacity. Maximum permanent capacity loss is capped at 25%.
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
        self.recovery_rate_wk           = 0.02
        self._shock_absorption_override = 0.05

        # Distress-spiral state
        self._distress_threshold:    float = 0.50
        self._distress_weeks:        int   = 0
        self._distress_trigger_wks:  int   = 8
        self._capacity_degradation:  float = 0.0   # permanent capacity loss [0, 0.25]
        self._max_degradation:       float = 0.25
        self._degradation_rate:      float = 0.005  # 0.5% per week in prolonged distress

    def _compute_output_fraction(self) -> float:
        output = max(self.production_floor, self.shock_multiplier)

        if self.shock_multiplier < self._distress_threshold:
            # In prolonged distress: covenant breach → asset degradation
            self._distress_weeks += 1
            if self._distress_weeks > self._distress_trigger_wks:
                self._capacity_degradation = min(
                    self._max_degradation,
                    self._capacity_degradation + self._degradation_rate
                )
        else:
            self._distress_weeks = 0

        # Permanent capacity loss reduces effective output ceiling
        return max(0.0, output * (1.0 - self._capacity_degradation))


# ── Tier 1 Archetypes: Cell Manufacturers ─────────────────────────────────────

class PlatformLeader(CellManufacturerAgent):
    """Technology and scale platform leader (CATL, BYD).

    Decision rule (_desired_production)
    ------------------------------------
    Strategic inventory building: when inventory falls below 70% of target,
    the agent produces at full weekly capacity regardless of current demand —
    using its strong balance sheet to maintain a market-position buffer.
    Above that threshold, uses a generous demand-pull with 15% top-up headroom.

    Additional behaviour (step override)
    --------------------------------------
    Proactive chemistry shift: actively increases LFP fraction when cobalt prices
    exceed the shift trigger (1.30), at 5× the rate of incumbents. This models
    CATL/BYD's technology investment advantage — they can redesign cells and
    renegotiate supply contracts faster than legacy NMC producers.
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
        self.safety_stock_weeks *= 1.10
        self.target_inventory    = self.weekly_capacity * self.safety_stock_weeks
        self.inventory_gwh       = self.target_inventory
        self.capacity_growth_wk  = _CELL_GROWTH_WK * 1.0

        # Chemistry shift parameters
        self._chemistry_shift_trigger: float = 1.30   # cobalt price index threshold
        self._chemistry_shift_rate:    float = 0.005  # LFP fraction gain per week at trigger
        self._max_lfp_fraction: float = min(1.0, lfp_fraction + 0.25)  # adoption ceiling

    def _desired_production(self, downstream_demand: float,
                            price_premium: float) -> float:
        inv_ratio = self.inventory_gwh / max(self.target_inventory, 1e-9)

        if inv_ratio < 0.70:
            # Strategic inventory build: run at full capacity until buffer is healthy.
            # Strong balance sheet absorbs the cost of holding excess inventory.
            return self.weekly_capacity

        # Healthy buffer: demand-pull with generous top-up headroom
        gap = max(0.0, self.target_inventory - self.inventory_gwh)
        return (downstream_demand
                + min(gap / self.inventory_replenishment_weeks,
                      downstream_demand * 0.15))

    def step(self) -> None:
        # Proactive chemistry shift before production decision
        cobalt_price = self.model.sd.prices.get("cobalt", 1.0)
        if cobalt_price > self._chemistry_shift_trigger:
            shift = (self._chemistry_shift_rate
                     * (cobalt_price - self._chemistry_shift_trigger))
            self.lfp_fraction = min(self._max_lfp_fraction,
                                    self.lfp_fraction + shift)
            self.nmc_fraction = max(0.0, 1.0 - self.lfp_fraction)
        super().step()


class HyperScaleChallenger(CellManufacturerAgent):
    """Fast-growing challenger with thin balance sheet (CALB, others_cells).

    Decision rule (_desired_production)
    ------------------------------------
    Push model: always targets maximum capacity output to capture market share.
    The challenger's investment thesis depends on high utilisation rates to
    justify its growth capital, so it does not throttle back when demand is low.

    Additional behaviour (step override)
    --------------------------------------
    Liquidity-crisis trigger: if the agent fails to meet ≥50% of demand for
    more than 8 consecutive weeks, the thin balance sheet hits a wall — growth
    plans are halted (capacity_growth_wk halved). Recovery resets when the
    shortfall clears.
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
        self._shock_absorption_override    = 0.08
        self.inventory_replenishment_weeks = 5.0
        self.safety_stock_weeks           *= 1.30
        self.target_inventory              = self.weekly_capacity * self.safety_stock_weeks
        self.inventory_gwh                 = self.target_inventory
        self.capacity_growth_wk            = _CELL_GROWTH_WK * 1.80

        # Liquidity-crisis tracking
        self._shortfall_weeks:        int   = 0
        self._shortfall_trigger:      int   = 8
        self._growth_penalty_active:  bool  = False
        self._base_growth_rate:       float = _CELL_GROWTH_WK * 1.80

    def _desired_production(self, downstream_demand: float,
                            price_premium: float) -> float:
        # Push model: always attempt to produce at maximum capacity.
        # Does not reduce output when demand is slack — growth capital
        # justification requires consistently high utilisation proof.
        return self.weekly_capacity * (1.0 + min(0.05, 0.03 * price_premium))

    def step(self) -> None:
        super().step()

        # Liquidity-crisis detection: sustained inability to meet demand
        demand = self.model.get_cell_demand(self.name)
        if self.output_gwh < demand * 0.50:
            self._shortfall_weeks += 1
        else:
            self._shortfall_weeks = max(0, self._shortfall_weeks - 1)

        if (self._shortfall_weeks > self._shortfall_trigger
                and not self._growth_penalty_active):
            # Thin balance sheet cannot sustain capex: growth plans frozen
            self._growth_penalty_active = True
            self.capacity_growth_wk    = self._base_growth_rate * 0.50
        elif self._shortfall_weeks == 0 and self._growth_penalty_active:
            # Supply normalised: restore growth ambition
            self._growth_penalty_active = False
            self.capacity_growth_wk    = self._base_growth_rate


class IncumbentUnderPressure(CellManufacturerAgent):
    """NMC-heavy incumbent losing share (LG ES, Panasonic, Samsung SDI, SK On).

    Decision rule (_desired_production)
    ------------------------------------
    Strict demand-pull with minimal inventory top-up (5% vs 10% in base):
    incumbents optimise for margin preservation, not market share capture.
    They do not stockpile because capital efficiency pressure is high.

    Additional behaviour (step override)
    --------------------------------------
    Two compounding pressures applied each week:
      1. Market-share erosion: 0.01%/week structural decline as OEMs diversify
         away from NMC-heavy suppliers. Floor at 50% of initial share.
      2. Slow chemistry shift: NMC-to-LFP transition at 0.1%/week under cobalt
         pressure — constrained by long-term NMC supply contracts and legacy
         production lines. LFP adoption ceiling is 50% (partial transition only).
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
        self.recovery_rate_wk           = 0.025
        self._shock_absorption_override = 0.05
        self.inventory_replenishment_weeks = 6.0
        self.capacity_growth_wk         = _CELL_GROWTH_WK * 0.70

        # Structural decline state
        self._share_erosion_rate:   float = 0.0001   # 0.01%/wk ≈ 0.5%/yr
        self._min_market_share:     float = market_share * 0.50
        self._chemistry_shift_rate: float = 0.001    # 0.1%/wk LFP adoption under pressure
        self._chemistry_trigger:    float = 1.30
        self._max_lfp_fraction:     float = 0.50     # contract lock-in ceiling

    def _desired_production(self, downstream_demand: float,
                            price_premium: float) -> float:
        # Strict demand-pull: no over-production for strategic reasons.
        # Margin-preservation culture — capital locked into existing NMC assets.
        gap = max(0.0, self.target_inventory - self.inventory_gwh)
        return (downstream_demand
                + min(gap / self.inventory_replenishment_weeks,
                      downstream_demand * 0.05))

    def step(self) -> None:
        # Structural market-share erosion (OEM dual-sourcing and LFP adoption)
        self.market_share = max(
            self._min_market_share,
            self.market_share - self._share_erosion_rate
        )

        # Slow chemistry adaptation under cobalt price pressure
        cobalt_price = self.model.sd.prices.get("cobalt", 1.0)
        if cobalt_price > self._chemistry_trigger:
            shift = self._chemistry_shift_rate * (cobalt_price - self._chemistry_trigger)
            self.lfp_fraction = min(self._max_lfp_fraction,
                                    self.lfp_fraction + shift)
            self.nmc_fraction = max(
                1.0 - self._max_lfp_fraction,
                1.0 - self.lfp_fraction
            )

        super().step()


# ── Tier 2 Archetypes: Sub-system Suppliers ───────────────────────────────────

class PremiumPowerElectronics(Tier1SupplierAgent):
    """High-value power electronics (inverter tier): long lead, large buffer.

    Decision rule (_order_quantity)
    --------------------------------
    Forward-looking order sizing: projects demand over the full 16-week lead
    time before computing the order, rather than using only this week's demand.
    This reflects the long planning horizon of fab-constrained component makers.

    Price-sensitive ordering: when the key input (SiC wafer) price exceeds
    the defer threshold (1.5×), the agent draws down inventory rather than
    committing to orders at peak prices — modelling procurement managers
    deliberately timing large orders to avoid price spikes.
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
        self._price_defer_threshold:    float = 1.50

    def _order_quantity(self, weekly_demand: float,
                        price_premium: float) -> float:
        # Forward-horizon demand projection over the full lead-time window
        growth_per_week  = 1.0 + _TIER1_GROWTH_WK
        projected_demand = weekly_demand * (growth_per_week ** self.lead_time_weeks)

        # Price-sensitive deferral: avoid ordering at peak input prices
        input_price = self.model.sd.prices.get(self.key_input, 1.0)
        if input_price > self._price_defer_threshold:
            # Draw down inventory rather than buy at peak — order minimum
            return max(0.0, weekly_demand * 0.60)

        in_transit   = sum(self.pipeline)
        inv_position = self.inventory + in_transit
        shortfall    = max(0.0, self.target_inventory - inv_position)
        order = projected_demand + shortfall * self.bullwhip_factor

        if self.inventory < self.target_inventory * self.dual_source_threshold:
            self.dual_source_active = True
            order *= 1.20
        elif self.inventory > self.target_inventory * (self.dual_source_threshold * 3.0):
            self.dual_source_active = False
        return order


class EstablishedVolumeSupplier(Tier1SupplierAgent):
    """High-volume established supplier (motor, harness).

    Decision rule (_order_quantity)
    --------------------------------
    Standard order-up-to with production smoothing: when the inventory
    position (on-hand + in-transit) exceeds 150% of target, the agent
    deliberately reduces orders to 80% of demand — modelling planned
    maintenance shutdowns and vacation periods that established suppliers
    use to avoid accumulating excessive stock and over-taxing suppliers.
    Below target, uses the standard bullwhip-amplified order-up-to rule.
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

    def _order_quantity(self, weekly_demand: float,
                        price_premium: float) -> float:
        in_transit   = sum(self.pipeline)
        inv_position = self.inventory + in_transit

        # Production smoothing: intentionally reduce ordering when over-stocked
        # (planned maintenance windows, supplier relationship management)
        if inv_position > self.target_inventory * 1.50:
            self.dual_source_active = False
            return max(0.0, weekly_demand * 0.80)

        shortfall       = max(0.0, self.target_inventory - inv_position)
        order_incentive = 1.0 + min(0.10, 0.08 * price_premium)
        order = (self.weekly_capacity * order_incentive
                 + shortfall * self.bullwhip_factor)

        if self.inventory < self.target_inventory * self.dual_source_threshold:
            self.dual_source_active = True
            order *= 1.20
        elif self.inventory > self.target_inventory * (self.dual_source_threshold * 3.0):
            self.dual_source_active = False
        return order


class BatteryPackIntegrator(Tier1SupplierAgent):
    """Battery pack assembly integrator: pure downstream of cell supply.

    Decision rule (_order_quantity)
    --------------------------------
    JIT cell procurement: orders exactly what OEM demand requires this week
    with no bullwhip amplification. Pack integrators are essentially
    assembly services — they do not speculate on inventory and pass
    cell availability directly to OEMs. This makes them acutely exposed
    to cell supply shocks with minimal smoothing.
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

    def _order_quantity(self, weekly_demand: float,
                        price_premium: float) -> float:
        # JIT pass-through: order exactly what downstream demands.
        # No strategic inventory building; cell shortages flow directly to OEMs.
        return weekly_demand


# ── Tier 3 Archetypes: OEMs ───────────────────────────────────────────────────

class ProfitableEstablishedOEM(OEMAgent):
    """Profitable, stable OEM with strong balance sheet (Korean, Japanese).

    Decision rule (_compute_production_target)
    -------------------------------------------
    Buffer-first approach: never reduces production target until average
    component inventory drops below 50% of target (vs 70% in base class).
    When buffers are healthy, targets full demand fulfillment with faster
    backlog clearance (20% clearance rate vs 15% base) and 115% cap.
    When buffers are stressed, switches to conservative mode.
    Strong shock absorption and fast recovery underpin this conservative policy.
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
        self.safety_stock_weeks = 6.0 * self.financial_profile.inventory_multiplier
        self.target_inv         = self.weekly_target * self.safety_stock_weeks
        for comp in self.inv:
            self.inv[comp] = self.target_inv

        self._buffer_floor_ratio: float = 0.50  # only cut production below this

    def _compute_production_target(self, weekly_demand: float,
                                   producible: float,
                                   price_premium: float,
                                   price_discount: float) -> float:
        avg_inv   = sum(self.inv.values()) / 4.0
        inv_ratio = avg_inv / max(self.target_inv, 1e-9)

        if inv_ratio > self._buffer_floor_ratio:
            # Comfortable buffers: prioritise demand fulfillment and backlog clearance
            target_prod = min(
                weekly_demand + self.backlog_k * 0.20,  # faster clearance
                self.weekly_target * 1.15               # willing to run 115%
            )
            # Conservative margin response: only modest production cut under pressure
            integration_buffer = 0.50 * self.vertical_integration
            target_prod *= max(
                0.75,
                1.0 - (0.30 - integration_buffer) * price_premium
                    + 0.20 * price_discount
            )
        else:
            # Buffers stressed: conservative mode — match demand only, no over-run
            target_prod = min(weekly_demand, self.weekly_target)
            target_prod *= max(0.70, 1.0 - 0.25 * price_premium)

        return max(0.0, min(target_prod, producible) * self.shock_multiplier)


class TransitioningLegacyOEM(OEMAgent):
    """ICE-to-EV transitioning legacy OEM (German, US, UK OEMs).

    Decision rule (_compute_production_target)
    -------------------------------------------
    JIT-legacy behaviour with ICE fallback option:
      - Slower backlog clearance (10% vs 15%) reflecting conservative JIT culture.
      - Higher cost sensitivity to margin pressure (0.55 coefficient vs 0.20 base).
      - ICE fallback: when margin signal drops below 0.85 (EV profitability
        squeezed), weekly EV target gradually reduces by up to 20% as capacity
        shifts back toward ICE vehicles. EV commitment recovers when margins
        improve.
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

        # ICE fallback state
        self._ev_target_reduction:      float = 0.0
        self._ice_fallback_threshold:   float = 0.85   # margin signal below which fallback activates
        self._ice_ramp_rate:            float = 0.010  # EV target reduction per week
        self._ice_recovery_rate:        float = 0.005  # EV target re-commitment per week
        self._max_ice_fallback:         float = 0.20   # max 20% shift back to ICE

    def _compute_production_target(self, weekly_demand: float,
                                   producible: float,
                                   price_premium: float,
                                   price_discount: float) -> float:
        # ICE fallback: squeezed EV margins trigger partial capacity reversion
        margin_signal = 1.0 - price_premium
        if margin_signal < self._ice_fallback_threshold:
            self._ev_target_reduction = min(
                self._max_ice_fallback,
                self._ev_target_reduction + self._ice_ramp_rate
            )
        else:
            self._ev_target_reduction = max(
                0.0, self._ev_target_reduction - self._ice_recovery_rate
            )

        effective_demand = weekly_demand * (1.0 - self._ev_target_reduction)
        target_prod = min(
            effective_demand + self.backlog_k * 0.10,   # slow backlog clearance
            self.weekly_target * 1.05
        )
        integration_buffer = 0.50 * self.vertical_integration
        target_prod *= max(
            0.60,
            1.0 - (0.55 - integration_buffer) * price_premium
                + 0.15 * price_discount
        )
        return max(0.0, min(target_prod, producible) * self.shock_multiplier)


class EVNativeScaleAspirant(OEMAgent):
    """EV-native Chinese aspirant scaling rapidly (other_chinese_oem group).

    Decision rule (_compute_production_target)
    -------------------------------------------
    Demand-elastic targeting with domestic proximity advantage:
      - When demand exceeds the current weekly target, the target is
        dynamically boosted (up to 5% per week) to chase market opportunity.
        This models the aggressive scale-up mindset of EV-native firms.
      - Domestic supplier proximity: effective producible quantity receives
        a 5% boost, reflecting faster component replenishment and tighter
        operational coordination with nearby suppliers.
      - Higher production ceiling (120% of target) and faster backlog clearance
        support the scale-capture strategy.
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
        self.safety_stock_weeks = 3.5 * self.financial_profile.inventory_multiplier
        self.target_inv         = self.weekly_target * self.safety_stock_weeks
        for comp in self.inv:
            self.inv[comp] = self.target_inv
        self.capacity_growth_wk = _TIER1_GROWTH_WK * 1.40

        # Demand-elastic targeting
        self._demand_elasticity:      float = 0.15   # target boost fraction per unit over-demand
        self._max_weekly_boost:       float = 0.05   # cap on weekly target increase
        # Domestic proximity advantage
        self._proximity_boost:        float = 1.05   # effective producible multiplier

    def _compute_production_target(self, weekly_demand: float,
                                   producible: float,
                                   price_premium: float,
                                   price_discount: float) -> float:
        # Dynamic targeting: chase demand when it exceeds current target
        if weekly_demand > self.weekly_target:
            boost = min(
                self._max_weekly_boost,
                self._demand_elasticity * (weekly_demand / self.weekly_target - 1.0)
            )
            self.weekly_target *= (1.0 + boost)
            self.target_inv     = self.weekly_target * self.safety_stock_weeks

        # Domestic proximity: tighter supplier integration gives effective
        # component availability advantage
        effective_producible = producible * self._proximity_boost

        target_prod = min(
            weekly_demand + self.backlog_k * 0.15,
            self.weekly_target * 1.20   # willing to run at 120% to capture demand
        )
        integration_buffer = 0.50 * self.vertical_integration
        target_prod *= max(
            0.65,
            1.0 - (0.35 - integration_buffer) * price_premium
                + 0.25 * price_discount
        )
        return max(0.0, min(target_prod, effective_producible) * self.shock_multiplier)


class PrecommercialStartup(OEMAgent):
    """Pre-commercial EV startup with thin capital reserves.

    Decision rule (_compute_production_target)
    -------------------------------------------
    Capital-constrained production: output is capped by an internal capital
    ratio (0–1) that drains when the company is producing and refills slowly
    from investor tranches.
      - When capital_ratio < 10%, an emergency financing round triggers
        (modelled as a one-time top-up to 60% of normal funding). This can
        only occur once.
      - Production ceiling = weekly_target × capital_ratio, so cash scarcity
        directly limits output even when components are available.
      - Slow backlog clearance (5%) and high cost sensitivity reflect the
        startup's inability to absorb margin pressure.
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

        # Capital pool state
        self._capital_ratio:       float = 1.0    # starts fully funded [0, 1]
        self._capital_drain_rate:  float = 0.10   # fraction drained per week at full utilisation
        self._capital_refill_rate: float = 0.05   # fraction refilled per week (investor tranches)
        self._recovery_loan_used:  bool  = False   # one-time emergency financing flag
        self._recovery_loan_level: float = 0.60   # post-round capital ratio

    def _compute_production_target(self, weekly_demand: float,
                                   producible: float,
                                   price_premium: float,
                                   price_discount: float) -> float:
        # Emergency financing: one-time round before the company runs dry
        if self._capital_ratio < 0.10 and not self._recovery_loan_used:
            self._capital_ratio      = self._recovery_loan_level
            self._recovery_loan_used = True

        # Capital-constrained ceiling on production
        capital_limited_target = self.weekly_target * max(0.0, self._capital_ratio)
        target_prod = min(
            weekly_demand + self.backlog_k * 0.05,  # slow clearance (cash-constrained ops)
            capital_limited_target
        )
        target_prod *= max(
            0.50,
            1.0 - 0.60 * price_premium + 0.10 * price_discount
        )
        result = max(0.0, min(target_prod, producible) * self.shock_multiplier)

        # Capital dynamics: producing burns cash; investors top up weekly
        utilisation = result / max(self.weekly_target, 1e-9)
        self._capital_ratio = max(
            0.0,
            min(1.0, self._capital_ratio
                     - self._capital_drain_rate * utilisation
                     + self._capital_refill_rate)
        )
        return result
