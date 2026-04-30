"""
UK government intervention packages for EV supply-chain resilience.

The packages are derived from the Advanced Manufacturing Sector Plan and
DRIVE35 framing: innovation, scale-up, transformation, supply-chain resilience,
and battery manufacturing capacity.  They are deliberately represented as
bounded model levers rather than as fiscal accounting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class PolicyPackage:
    name: str
    label: str
    description: str


POLICY_PACKAGES: dict[str, PolicyPackage] = {
    "battery_sovereignty": PolicyPackage(
        name="battery_sovereignty",
        label="Battery Sovereignty Package",
        description=(
            "DRIVE35-backed UK cell scale-up, offtake support, and recycling "
            "pathways that reduce imported cell concentration risk."
        ),
    ),
    "tier1_resilience": PolicyPackage(
        name="tier1_resilience",
        label="Tier-1 Resilience Package",
        description=(
            "Transformation grants for harness, inverter, motor, and battery-pack "
            "suppliers, raising buffers and shortening recovery."
        ),
    ),
    "critical_minerals_security": PolicyPackage(
        name="critical_minerals_security",
        label="Critical Minerals Security Package",
        description=(
            "Strategic mineral buffers, recycling/urban mining, and offtake "
            "support for cobalt, graphite, REE, and SiC inputs."
        ),
    ),
    "full_industrial_strategy": PolicyPackage(
        name="full_industrial_strategy",
        label="Full Industrial Strategy Package",
        description=(
            "Combined DRIVE35 grants, energy/grid support, skills, data visibility, "
            "battery scale-up, Tier-1 resilience, and minerals security."
        ),
    ),
}


def _multiply_stock_target(agent, multiplier: float) -> None:
    if hasattr(agent, "safety_stock_weeks"):
        agent.safety_stock_weeks *= multiplier

    if hasattr(agent, "weekly_capacity"):
        target = agent.weekly_capacity * getattr(agent, "safety_stock_weeks", 1.0)
        if hasattr(agent, "target_inventory"):
            agent.target_inventory = target
        if hasattr(agent, "inventory"):
            agent.inventory = max(getattr(agent, "inventory", 0.0), target)
        if hasattr(agent, "inventory_gwh"):
            agent.inventory_gwh = max(getattr(agent, "inventory_gwh", 0.0), target)

    if hasattr(agent, "weekly_target"):
        target = agent.weekly_target * getattr(agent, "safety_stock_weeks", 1.0)
        if hasattr(agent, "target_inv"):
            agent.target_inv = target
        if hasattr(agent, "inv"):
            agent.inv = {component: max(qty, target) for component, qty in agent.inv.items()}


def _add_policy_absorption(agent, amount: float) -> None:
    current = getattr(agent, "policy_shock_absorption", 0.0)
    agent.policy_shock_absorption = min(0.35, current + amount)


def _reset_pipeline(agent, lead_time_weeks: int | None = None) -> None:
    if not hasattr(agent, "pipeline"):
        return
    if lead_time_weeks is not None:
        agent.lead_time_weeks = max(1, int(lead_time_weeks))
    weekly = getattr(agent, "weekly_capacity", 0.0)
    agent.pipeline = [weekly] * max(1, int(getattr(agent, "lead_time_weeks", 1)))


def apply_policy_packages(model, packages: Iterable[str] | None) -> None:
    """Apply named intervention packages to a freshly constructed model."""
    selected = list(packages or [])
    if not selected:
        model.policy_packages = []
        return

    if "full_industrial_strategy" in selected:
        selected = [
            "battery_sovereignty",
            "tier1_resilience",
            "critical_minerals_security",
            "full_industrial_strategy",
        ]

    model.policy_packages = selected
    model.policy_notes = [
        POLICY_PACKAGES[p].description for p in selected if p in POLICY_PACKAGES
    ]
    model.policy_shock_mitigation = getattr(model, "policy_shock_mitigation", {})
    model.policy_mineral_supply_boost = getattr(model, "policy_mineral_supply_boost", {})

    if "battery_sovereignty" in selected:
        _apply_battery_sovereignty(model)
    if "tier1_resilience" in selected:
        _apply_tier1_resilience(model)
    if "critical_minerals_security" in selected:
        _apply_critical_minerals_security(model)
    if "full_industrial_strategy" in selected:
        _apply_full_strategy_overlay(model)


def _apply_battery_sovereignty(model) -> None:
    for name, agent in model._cell_agents.items():
        if name == "aesc_uk":
            agent.weekly_capacity *= 2.25
            agent.capacity_growth_wk *= 1.65
            agent.recovery_rate_wk *= 1.35
            _multiply_stock_target(agent, 1.35)
            _add_policy_absorption(agent, 0.10)
        elif getattr(agent, "country", "") in {"uk", "europe", "mixed"}:
            agent.capacity_growth_wk *= 1.20
            _multiply_stock_target(agent, 1.15)
            _add_policy_absorption(agent, 0.05)
        else:
            _add_policy_absorption(agent, 0.03)

    model.policy_shock_mitigation.update({
        "cell_catl": 0.25,
        "cell_byd_cells": 0.10,
        "graphite_chn": max(model.policy_shock_mitigation.get("graphite_chn", 0.0), 0.12),
    })


def _apply_tier1_resilience(model) -> None:
    lead_time_targets = {
        "battery_pack": None,
        "inverter": 12,
        "motor": 9,
        "harness": 4,
    }
    for comp, agent in model._tier1_agents.items():
        _multiply_stock_target(agent, 1.45 if comp == "harness" else 1.25)
        agent.recovery_rate_wk *= 1.30
        agent.capacity_growth_wk *= 1.20
        _add_policy_absorption(agent, 0.10 if comp == "harness" else 0.06)
        _reset_pipeline(agent, lead_time_targets.get(comp))

    if "uk_oem" in model._oem_agents:
        oem = model._oem_agents["uk_oem"]
        _multiply_stock_target(oem, 1.20)
        oem.recovery_rate_wk *= 1.25
        oem.capacity_growth_wk *= 1.15
        _add_policy_absorption(oem, 0.08)

    model.policy_shock_mitigation.update({
        "t1_harness": 0.35,
        "oem_uk_oem": 0.20,
    })


def _apply_critical_minerals_security(model) -> None:
    mineral_buffer = {
        "cobalt": 2.0,
        "graphite": 2.0,
        "ree": 3.0,
        "sic_wafer": 3.0,
        "lithium": 1.0,
    }
    for mineral, extra_weeks in mineral_buffer.items():
        if mineral in model.sd.stocks:
            model.sd.stocks[mineral] += extra_weeks
        if hasattr(model.sd, "_measured_stocks") and mineral in model.sd._measured_stocks:
            model.sd._measured_stocks[mineral] += extra_weeks
        model.policy_mineral_supply_boost[mineral] = max(
            model.policy_mineral_supply_boost.get(mineral, 1.0),
            1.08 if mineral in {"cobalt", "graphite"} else 1.05,
        )

    model.policy_shock_mitigation.update({
        "cobalt_drc": 0.35,
        "graphite_chn": max(model.policy_shock_mitigation.get("graphite_chn", 0.0), 0.35),
        "ree_chn": 0.35,
        "sic_wolfspeed": 0.20,
        "sic_coherent": 0.20,
        "sic_china": 0.15,
    })

    for agent in model._mineral_agents.values():
        if agent.mineral in {"cobalt", "graphite", "ree", "sic_wafer", "lithium"}:
            agent.recovery_rate_wk *= 1.20
            _add_policy_absorption(agent, 0.08)


def _apply_full_strategy_overlay(model) -> None:
    for store in (
        model._mineral_agents,
        model._cell_agents,
        model._tier1_agents,
        model._oem_agents,
    ):
        for agent in store.values():
            if hasattr(agent, "capacity_growth_wk"):
                agent.capacity_growth_wk *= 1.10
            if hasattr(agent, "recovery_rate_wk"):
                agent.recovery_rate_wk *= 1.10
            _add_policy_absorption(agent, 0.04)

    for target, mitigation in list(model.policy_shock_mitigation.items()):
        model.policy_shock_mitigation[target] = min(0.60, mitigation + 0.08)

    if "uk_oem" in model._oem_agents:
        oem = model._oem_agents["uk_oem"]
        oem.vertical_integration = min(0.30, oem.vertical_integration + 0.10)
