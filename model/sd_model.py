"""
System Dynamics Layer
=====================
Tracks aggregate inventory stocks along the EV supply chain.
Uses discrete-time Euler integration (dt = 1 week).

Stock units
-----------
  Mineral stocks    : weeks of EV-industry consumption at baseline
  Cell inventory    : GWh
  Component stocks  : k vehicle-equivalents

Each stock has:
  inflow  — replenishment from upstream (agents report production fraction)
  outflow — consumption by downstream tier (Leontief min of inputs)

The SDModel exposes `input_fractions` to the ABM layer each step:
  input_fractions[stock_name] = current_stock / target_stock
  Values < 1 signal a shortage; agents scale production accordingly.
"""

from __future__ import annotations
import numpy as np
from typing import Dict


# Target stock levels (weeks of baseline throughput held as buffer).
# Derived from config.py MINERALS / TIER1 safety_stock_weeks.
# Set here as standalone constants so the SD model is self-contained.
TARGET_WEEKS = {
    # raw minerals (at cell-manufacturer level)
    "lithium":       4.0,
    "cobalt":        6.0,
    "graphite":      4.0,
    "ree":           8.0,
    "sic_wafer":    12.0,
    # processed components (at OEM level)
    "cells":         4.0,   # GWh
    "packs":         3.0,   # k vehicle-equiv
    "inverters":     8.0,   # k units — SiC tension drives larger buffer
    "motors":        6.0,   # k units
    "harness":       2.0,   # k vehicle-sets — JIT, critical vulnerability
}

# Baseline weekly throughput (each stock set to its target at t=0)
# Absolute baselines (weekly, 2023 production levels):
#   Cells:      822 GWh/yr ÷ 52  = 15.81 GWh/wk
#   Components: 14,000 k veh/yr ÷ 52 = 269.2 k/wk
BASELINE_WK = {
    "lithium":    1.0,    # normalised; minerals tracked as fraction of demand
    "cobalt":     1.0,
    "graphite":   1.0,
    "ree":        1.0,
    "sic_wafer":  1.0,
    "cells":     15.81,   # GWh/week  (822 / 52)
    "packs":    269.2,    # k vehicle-equiv/week  (14000 / 52)
    "inverters":269.2,
    "motors":   269.2,
    "harness":  269.2,
}


class SDModel:
    """
    Discrete-time System Dynamics model.

    The model is intentionally simple: each stock is updated by one
    inflow and one outflow each week.  Complexity is in the ABM agents
    that determine what those flows are.
    """

    def __init__(self, rng: np.random.Generator | None = None):
        self.rng = rng or np.random.default_rng(42)

        # Initialise stocks at target (safety-stock) levels
        self.stocks: Dict[str, float] = {
            name: TARGET_WEEKS[name] * BASELINE_WK[name]
            for name in TARGET_WEEKS
        }

        # History: list of stock snapshots (one dict per week)
        self.history: list[Dict[str, float]] = []

        # Cached input fractions (computed each step, read by agents)
        self.input_fractions: Dict[str, float] = {
            name: 1.0 for name in TARGET_WEEKS
        }

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    def compute_input_fractions(self) -> Dict[str, float]:
        """
        Return the availability fraction for each stock.
        = current_stock / target_stock  (capped at 2.0 — surplus)
        Values < 1 signal shortage; 0 = complete stockout.
        """
        fracs = {}
        for name in TARGET_WEEKS:
            target = TARGET_WEEKS[name] * BASELINE_WK[name]
            fracs[name] = min(2.0, self.stocks[name] / max(target, 1e-9))
        self.input_fractions = fracs
        return fracs

    def update(self, flows: Dict[str, float]) -> None:
        """
        Euler integration: stock[t+1] = stock[t] + inflow[t] - outflow[t].

        `flows` is a dict produced by HybridModel each step:
          {
            "lithium_in":    <fraction of baseline supply>,
            "lithium_out":   <fraction of baseline demand>,
            "cobalt_in":     ...,
            ...
            "cells_in":      <GWh produced this week>,
            "cells_out":     <GWh consumed this week>,
            ...
          }
        Mineral stocks use absolute-normalised units (fraction × baseline_wk).
        Component stocks use absolute GWh or k-units.
        """
        for name in TARGET_WEEKS:
            inflow  = flows.get(f"{name}_in",  0.0)
            outflow = flows.get(f"{name}_out", 0.0)
            self.stocks[name] = max(0.0, self.stocks[name] + inflow - outflow)

        # Cap stocks at 4× target to prevent unbounded accumulation
        for name in TARGET_WEEKS:
            cap = 4.0 * TARGET_WEEKS[name] * BASELINE_WK[name]
            self.stocks[name] = min(self.stocks[name], cap)

    def record(self) -> None:
        """Append current stock snapshot to history."""
        self.history.append(dict(self.stocks))

    # ------------------------------------------------------------------
    # Derived metrics
    # ------------------------------------------------------------------

    def weeks_of_supply(self, stock_name: str) -> float:
        """Return how many weeks of baseline consumption remain in stock."""
        base = BASELINE_WK.get(stock_name, 1.0)
        return self.stocks[stock_name] / max(base, 1e-9)

    def is_critical(self, stock_name: str, threshold_weeks: float = 1.0) -> bool:
        return self.weeks_of_supply(stock_name) < threshold_weeks

    def summary(self) -> Dict[str, float]:
        """Weeks-of-supply for all stocks (human-readable snapshot)."""
        return {name: round(self.weeks_of_supply(name), 2)
                for name in TARGET_WEEKS}
