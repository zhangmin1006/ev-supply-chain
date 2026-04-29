"""
System Dynamics Layer — Revised Four-Tier Model
================================================
Discrete-time Euler integration (dt = 1 week).

Key structural improvements over the initial version
-----------------------------------------------------
  T1  Explicit mineral transport delays
        Each mineral travels through a FIFO in-transit pipeline before reaching
        on-hand inventory.  Mine → refinery → factory gate delays range from
        3 weeks (copper) to 10 weeks (REE).  This separates the physical
        arrival of supply from the agent decision that ordered it.

  T2  Measurement / perception lags
        Agents observe measured inventory (first-order lag, τ≈2 wk) rather than
        true on-hand stock.  The same measured stock drives the price signal.
        Sharp shocks therefore take several weeks to propagate into prices and
        production decisions, consistent with real industrial response times.

  T3  Bounded softplus price formation
        Replaces the 1/availability scarcity signal which becomes singular as
        stocks approach zero.  The softplus function is smooth, bounded, and
        calibrates easily.

  T4  Logistic LFP chemistry target with cobalt price lag
        The piecewise-linear LFP target is replaced by a logistic function of
        log-cobalt-price.  A first-order lag on cobalt price perception models
        the managerial delay in deciding to re-tool production lines.

  T5  Multi-stage Erlang capacity pipeline with planning queue
        The single first-order capacity WIP stock is replaced by a 26-week
        planning/permitting queue followed by a 3-stage Erlang construction
        pipeline (mean build time 104 weeks).  This produces a more realistic
        capital-cycle delay distribution and avoids the unrealistically instant
        capacity ramp of a single first-order lag.

  T6  Copper as a tracked stock
        The harness subsystem is copper-dependent but copper was absent from
        the original SD mineral stocks.  Copper is now tracked with its own
        inventory, price, and transport pipeline.  The harness agent's
        input_dependency is 0 in the current ABM config; this closes the
        structural gap and allows future copper shock scenarios.

  T7  Stochastic supply disturbances
        Weekly Gaussian noise (σ = 3–7 % depending on mineral) is applied to
        mineral supply flows before they enter the transit pipeline.  This
        produces realistic uncertainty bands without requiring a separate
        Monte Carlo wrapper.

  T8  Actuator saturation on capacity investment
        Investment rate is capped at 30 %/yr of current capacity, preventing
        unrealistic instantaneous over-investment when utilisation spikes.

Feedback loops
--------------
  F1  Supply-demand price loop        (Balancing)
  F2  Chemistry substitution loop     (Balancing)
  F3  Cell-capacity investment cycle  (Reinforcing → Balancing)
  F4  Demand–adoption loop            (Balancing)
  F5  Bullwhip amplification tracking

SD–ABM coupling
---------------
  Before agent steps:
    compute_input_fractions() → uses MEASURED stocks; exposes availability
    fractions and price indices to agents.
  After agent steps:
    update(flows) → absorbs aggregated ABM outputs, advances transit pipelines,
    updates perceived stocks, and runs all feedback loops.

Stock units
-----------
  Mineral stocks (physical)  : weeks of EV-industry baseline consumption
  Mineral stocks (in-transit): weeks of EV-industry baseline consumption
  Price indices               : dimensionless (1.0 = 2023 baseline)
  Cell inventory              : GWh
  Cell capacity               : GWh / year
  Component stocks            : k vehicle-equivalents
  Chemistry mix               : fraction [0, 1]
  EV demand                   : GWh / year (annualised)
  OEM backlog                 : k vehicles
"""

from __future__ import annotations

import numpy as np
from collections import deque
from typing import Dict, List

# ══════════════════════════════════════════════════════════════════════════════
# 1.  Mineral sets
# ══════════════════════════════════════════════════════════════════════════════

# All six tracked minerals (5 original + copper)
ALL_MINERALS: tuple = (
    "lithium", "cobalt", "graphite", "ree", "sic_wafer", "copper",
)

# Minerals linked to battery-cell production (used in battery price BOM)
BATTERY_MINERALS: tuple = (
    "lithium", "cobalt", "graphite", "ree", "sic_wafer",
)

# Minerals with explicit ABM supplier agents; copper is exogenous-steady-state
AGENT_MINERALS: frozenset = frozenset(
    ("lithium", "cobalt", "graphite", "ree", "sic_wafer")
)


# ══════════════════════════════════════════════════════════════════════════════
# 2.  Safety-stock targets and baseline throughputs
# ══════════════════════════════════════════════════════════════════════════════

MINERAL_TARGET_WK: Dict[str, float] = {
    "lithium":   4.0,    # IEA CMMR 2023 typical industry buffer
    "cobalt":    6.0,    # DRC political risk (USGS MCS 2024)
    "graphite":  4.0,
    "ree":       8.0,    # slow processing chain; China-dominant
    "sic_wafer": 12.0,   # 26-wk lead time → large strategic buffer
    "copper":    3.0,    # NEW: harness copper buffer (mature market, short LT)
}

COMPONENT_TARGET_WK: Dict[str, float] = {
    "cells":     4.0,
    "packs":     3.0,
    "inverters": 8.0,
    "motors":    6.0,
    "harness":   2.0,
}

TARGET_WEEKS = {**MINERAL_TARGET_WK, **COMPONENT_TARGET_WK}

# Baseline weekly throughputs (2023 normalised to 1.0 for minerals)
BASELINE_WK: Dict[str, float] = {
    "lithium":   1.0,   "cobalt":    1.0,
    "graphite":  1.0,   "ree":       1.0,   "sic_wafer": 1.0,
    "copper":    1.0,
    "cells":    15.81,                              # IEA GEO 2024: 822 GWh/yr ÷ 52
    "packs":   269.2,   "inverters": 269.2,         # IEA GEO 2024: 14 000 k veh/yr ÷ 52
    "motors":  269.2,   "harness":   269.2,
}


# ══════════════════════════════════════════════════════════════════════════════
# 3.  Transport and measurement delays
# ══════════════════════════════════════════════════════════════════════════════

# Mine/fab → factory gate transport time (weeks)
# Sources: IEA CMMR 2023, Benchmark Mineral Intelligence, Yole 2023
MINERAL_TRANSPORT_WK: Dict[str, int] = {
    "lithium":    6,   # Chile/Australia mine → Li₂CO₃ refinery → cell factory
    "cobalt":     8,   # DRC mine → Umicore/CMOC refinery → cell factory
    "graphite":   4,   # China mine → anode graphite plant → cell factory
    "ree":       10,   # China mine → separation plant → NdFeB magnet factory
    "sic_wafer":  4,   # USA/EU fab → power-module assembly house
    "copper":     3,   # Mine/smelter → wire drawing → harness factory
}

# First-order measurement lag (weeks): time for managers to accurately perceive
# inventory levels.  2 weeks reflects typical ERP cycle-count frequency.
MEAS_LAG_WK: float = 2.0


# ══════════════════════════════════════════════════════════════════════════════
# 4.  Cell capacity
# ══════════════════════════════════════════════════════════════════════════════

CELL_CAPACITY_2023_GWH_YR: float = 1_500.0   # BNEF 2023 nameplate
CELL_CAPACITY_BUILD_WK:    int   = 104        # mean 2-yr gigafactory build time
CELL_CAPACITY_PLAN_WK:     int   = 26         # planning + permitting queue (6 months)
CAP_ERLANG_N:              int   = 3          # number of Erlang pipeline stages
CELL_CAPACITY_DEPREC_WK:   float = 1.0 / (52 * 15)   # 15-yr straight-line depreciation
CAPEX_TRIGGER_UTIL:        float = 0.85       # invest when utilisation > 85 %
CAPEX_MAX_RATE_YR:         float = 0.30       # actuator saturation: max 30 %/yr addition


# ══════════════════════════════════════════════════════════════════════════════
# 5.  Chemistry mix
# ══════════════════════════════════════════════════════════════════════════════

LFP_SHARE_2023:       float = 0.403   # IEA GEO 2024 + BNEF 2023
LFP_SHARE_MIN:        float = 0.15
LFP_SHARE_MAX:        float = 0.92
CHEM_SHIFT_SPEED:     float = 0.003   # fraction/wk convergence to LFP target
LOGISTIC_BETA_S:      float = 8.0     # logistic steepness (higher → sharper switch)
COBALT_LOGISTIC_MID:  float = 1.30    # cobalt price at which LFP target = midpoint
CHEM_COBALT_LAG:      float = 1.0 / 4.0   # cobalt price perception: 4-wk first-order lag


# ══════════════════════════════════════════════════════════════════════════════
# 6.  Commodity price dynamics
# ══════════════════════════════════════════════════════════════════════════════

PRICE_ADJ_SPEED:     float = 0.05   # fraction of gap closed per week
PRICE_FLOOR:         float = 0.10   # absolute floor (avoids log(0))
PRICE_CEIL:          float = 6.00   # cap at 6 × baseline
PRICE_ALPHA:         float = 1.50   # scarcity sensitivity scaling
PRICE_SOFTPLUS_BETA: float = 4.0    # softplus sharpness
PRICE_SIGNAL_ADJ_SPEED: float = 0.20
PRICE_SIGNAL_FLOOR:     float = 0.60
PRICE_SIGNAL_CEIL:      float = 3.00

# Component price-signal construction, based on the hierarchy in
# deep-research-report2.md: use market-facing pack/vehicle indices where
# possible, and proxy indexed prices for inverter, motor, harness, and SiC.
PACK_PRICE_2024_USD_KWH: float = 115.0
PACK_PRICE_2025_USD_KWH: float = 108.0
PACK_COST_LEARNING_WK: float = (PACK_PRICE_2025_USD_KWH / PACK_PRICE_2024_USD_KWH) ** (1 / 52) - 1
PACK_PRICE_PASS_THROUGH: float = 0.80

SIC_INVERTER_COST_SHARE: float = 0.46
MOTOR_MATERIAL_COST_SHARE: float = 0.45
HARNESS_COPPER_COST_SHARE: float = 0.30
HARNESS_LABOUR_COST_SHARE: float = 0.45
HARNESS_PARTS_COST_SHARE: float = 0.25
LABOUR_COST_GROWTH_WK: float = (1.03 ** (1 / 52)) - 1
PARTS_COST_GROWTH_WK: float = (1.02 ** (1 / 52)) - 1

COMPONENT_PRICE_ADJ_SPEED: Dict[str, float] = {
    "pack": 0.20,
    "inverter": 0.12,
    "motor": 0.12,
    "harness": 0.12,
    "vehicle": 0.10,
    "sic": 0.08,
    "parts": 0.08,
    "labour": 0.04,
}

VEHICLE_PRICE_WEIGHTS: Dict[str, float] = {
    "pack": 0.32,
    "inverter": 0.03,
    "motor": 0.05,
    "harness": 0.025,
    "other": 0.595,
}


# ══════════════════════════════════════════════════════════════════════════════
# 7.  Stochastic supply disturbances
# ══════════════════════════════════════════════════════════════════════════════

# Weekly multiplicative supply noise (fraction of weekly flow).
# Reflects yield variation, shipping delays, weather, port congestion.
MINERAL_SUPPLY_VOL: Dict[str, float] = {
    "lithium":   0.04,
    "cobalt":    0.07,   # higher DRC-related instability
    "graphite":  0.03,
    "ree":       0.05,
    "sic_wafer": 0.03,
    "copper":    0.02,
}
DEMAND_VOL: float = 0.015   # weekly demand noise (fraction of growth increment)


# ══════════════════════════════════════════════════════════════════════════════
# 8.  Endogenous mineral supply expansion
# ══════════════════════════════════════════════════════════════════════════════

MINERAL_SUPPLY_GROWTH_WK: Dict[str, float] = {
    "lithium":   (1.20 ** (1 / 52)) - 1,   # 20 %/yr  IEA CMMR 2023
    "cobalt":    (1.05 ** (1 / 52)) - 1,   #  5 %/yr  DRC bottleneck
    "graphite":  (1.15 ** (1 / 52)) - 1,   # 15 %/yr  Mozambique/Madagascar
    "ree":       (1.30 ** (1 / 52)) - 1,   # 30 %/yr  MP Materials, Lynas, China quota expansion
    "sic_wafer": (1.35 ** (1 / 52)) - 1,   # 35 %/yr  Wolfspeed/STM/Infineon fabs
    "copper":    (1.03 ** (1 / 52)) - 1,   #  3 %/yr  mature market
}


# ══════════════════════════════════════════════════════════════════════════════
# 9.  EV demand and bullwhip
# ══════════════════════════════════════════════════════════════════════════════

EV_DEMAND_2023_GWH_YR: float = 820.0
EV_DEMAND_GROWTH_WK:   float = (1.29 ** (1 / 52)) - 1   # 29 %/yr IEA GEO 2024

BULLWHIP_SMOOTH: float = 0.10   # EWMA weight


# ══════════════════════════════════════════════════════════════════════════════
# 10.  SDModel class
# ══════════════════════════════════════════════════════════════════════════════

class SDModel:
    """
    Revised four-tier System Dynamics model with explicit transport delays,
    measurement lags, bounded price formation, logistic LFP substitution,
    multi-stage capacity pipeline, and stochastic supply disturbances.

    Public attributes (backward-compatible)
    ----------------------------------------
    stocks            : Dict[str, float]   physical on-hand inventory
    prices            : Dict[str, float]   commodity price indices (incl. copper)
    cell_capacity     : float              installed cell capacity (GWh/yr)
    cell_capacity_wip : float              property — total capacity WIP
    lfp_share         : float              current LFP chemistry share
    ev_demand_gwh_yr  : float              annualised EV demand (GWh/yr)
    oem_backlog_k     : float              cumulative unfulfilled OEM orders (k)
    bullwhip_index    : float              EWMA order amplification ratio
    input_fractions   : Dict[str, float]   availability fracs for ABM agents
    price_signals     : Dict[str, float]   commodity price indices for ABM agents
    price_signal      : float              smoothed battery/EV price pressure index
    """

    def __init__(self, rng: np.random.Generator | None = None):
        self.rng = rng or np.random.default_rng(42)

        # ── Physical on-hand inventory stocks ─────────────────────────────────
        self.stocks: Dict[str, float] = {
            name: TARGET_WEEKS[name] * BASELINE_WK[name]
            for name in TARGET_WEEKS
        }

        # ── In-transit mineral pipelines (FIFO queues) ────────────────────────
        # Each entry represents one week of supply committed but not yet arrived.
        # Initialised at steady-state (1 unit per week for normalised minerals).
        self._mineral_transit: Dict[str, deque] = {
            mineral: deque(
                [BASELINE_WK[mineral]] * MINERAL_TRANSPORT_WK[mineral]
            )
            for mineral in ALL_MINERALS
        }

        # ── Measured / perceived inventory (first-order lag) ──────────────────
        # At t = 0, measured = actual (no prior information gap).
        self._measured_stocks: Dict[str, float] = dict(self.stocks)

        # ── Commodity price indices (1.0 = 2023 baseline) ────────────────────
        self.prices: Dict[str, float] = {m: 1.0 for m in ALL_MINERALS}
        self.price_signal: float = 1.0
        self.raw_price_signal: float = 1.0
        self.component_prices: Dict[str, float] = {
            "pack": 1.0,
            "inverter": 1.0,
            "motor": 1.0,
            "harness": 1.0,
            "vehicle": 1.0,
            "sic": 1.0,
            "parts": 1.0,
            "labour": 1.0,
        }
        self.raw_component_prices: Dict[str, float] = dict(self.component_prices)
        self._pack_learning_index: float = 1.0

        # ── Cell capacity stocks ──────────────────────────────────────────────
        self.cell_capacity: float = CELL_CAPACITY_2023_GWH_YR
        # Planning/permitting queue: investment decisions enter here and wait
        # CELL_CAPACITY_PLAN_WK weeks before construction starts.
        self._cap_planning_queue: deque = deque([0.0] * CELL_CAPACITY_PLAN_WK)
        # 3-stage Erlang construction pipeline (each stage = build_wk / n stages)
        self._cap_stages: List[float] = [0.0] * CAP_ERLANG_N

        # ── Chemistry mix ─────────────────────────────────────────────────────
        self.lfp_share: float = LFP_SHARE_2023
        # Lagged cobalt price perceived by decision-makers (4-wk first-order lag)
        self._cobalt_price_delayed: float = 1.0

        # ── Tier 4: demand & backlog ──────────────────────────────────────────
        self.ev_demand_gwh_yr: float = EV_DEMAND_2023_GWH_YR
        self.oem_backlog_k:    float = 0.0
        self.bullwhip_index:   float = 1.0

        # ── Cumulative supply expansion multipliers ───────────────────────────
        self._mineral_supply_scale: Dict[str, float] = {m: 1.0 for m in ALL_MINERALS}

        # ── Bullwhip memory ───────────────────────────────────────────────────
        self._prev_orders_k:    float = BASELINE_WK["packs"]
        self._prev_shipments_k: float = BASELINE_WK["packs"]

        # ── Cached signals exposed to ABM agents ─────────────────────────────
        self.input_fractions: Dict[str, float] = {
            name: 1.0 for name in TARGET_WEEKS
        }
        self.price_signals: Dict[str, float] = dict(self.prices)
        self.price_signals["battery_pack"] = self.price_signal
        self.price_signals.update({f"component_{k}": v for k, v in self.component_prices.items()})

        # ── History ───────────────────────────────────────────────────────────
        self.history: list = []

    # ──────────────────────────────────────────────────────────────────────────
    # Backward-compatible property
    # ──────────────────────────────────────────────────────────────────────────

    @property
    def cell_capacity_wip(self) -> float:
        """Total capacity work-in-progress: planning queue + all Erlang stages."""
        return sum(self._cap_planning_queue) + sum(self._cap_stages)

    # ──────────────────────────────────────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────────────────────────────────────

    def compute_input_fractions(self) -> Dict[str, float]:
        """
        Compute availability fractions using MEASURED (perceived) stocks.

        Agents make production and ordering decisions based on what they
        observe, not the true physical inventory.  Using measured stocks
        introduces a 2-week information lag into production constraints.

        fraction = measured_stock / target_stock  (capped at 2.0)
        < 1  →  shortage;  0  →  stockout;  > 1  →  surplus.

        Also refreshes price_signals for agent use.
        """
        fracs: Dict[str, float] = {}
        for name in TARGET_WEEKS:
            target = TARGET_WEEKS[name] * BASELINE_WK[name]
            measured = self._measured_stocks.get(name, self.stocks[name])
            fracs[name] = min(2.0, measured / max(target, 1e-9))
        self.input_fractions = fracs
        self.price_signals = {m: self.prices.get(m, 1.0) for m in ALL_MINERALS}
        self.price_signals["battery_pack"] = self.price_signal
        self.price_signals.update({f"component_{k}": v for k, v in self.component_prices.items()})
        return fracs

    def update(self, flows: Dict[str, float]) -> None:
        """
        Main SD update — called after all ABM agents have stepped.

        Execution order
        ---------------
        1. Advance supply expansion multipliers.
        2. Advance mineral in-transit pipelines → update physical stocks.
        3. Update component stocks from Tier-2/3 agent flows.
        4. Cap all stocks at 4 × target.
        5. Update measured / perceived stocks (first-order lag).
        6. Run feedback loops F1–F5.

        `flows` keys produced by HybridModel._collect_flows():
          {lithium|cobalt|graphite|ree|sic_wafer|copper}_{in|out}
          cells_{in|out}   (GWh)
          {packs|inverters|motors|harness}_{in|out}   (k veh-equiv)
          lfp_gwh, nmc_gwh, cell_capacity_gwh_yr
          total_oem_prod_k, total_demand_k, total_demand_gwh_wk, order_rate_k
        """
        # 1. Advance supply expansion multipliers
        for mineral in ALL_MINERALS:
            self._mineral_supply_scale[mineral] *= (
                1.0 + MINERAL_SUPPLY_GROWTH_WK[mineral]
            )

        # 2. Mineral transport pipelines → on-hand stocks
        self._update_mineral_stocks(flows)

        # 3. Component stocks (no transport pipeline — handled by ABM agents)
        self._update_component_stocks(flows)

        # 4. Cap stocks at 4 × target to prevent unbounded accumulation
        for name in TARGET_WEEKS:
            cap = 4.0 * TARGET_WEEKS[name] * BASELINE_WK[name]
            self.stocks[name] = min(self.stocks[name], cap)

        # 5. Update perceived stocks (first-order measurement lag)
        alpha_meas = 1.0 / MEAS_LAG_WK   # fraction of gap closed per week
        for name in TARGET_WEEKS:
            self._measured_stocks[name] = (
                self._measured_stocks.get(name, self.stocks[name])
                + alpha_meas * (self.stocks[name] - self._measured_stocks.get(name, self.stocks[name]))
            )

        # 6. Feedback loops
        self._step_prices()
        self._step_chemistry_mix()
        self._step_price_signal()
        self._step_cell_capacity(flows)
        self._step_demand_and_backlog(flows)
        self._step_bullwhip(flows)

    def step_internal(self) -> None:
        """Advance purely endogenous stocks (no ABM flows). Exposed for testing."""
        self._step_prices()
        self._step_chemistry_mix()
        self._step_price_signal()

    def record(self) -> None:
        """Append a snapshot of all SD state variables to history."""
        snap: Dict[str, float] = {}
        # Physical inventory stocks
        for name in TARGET_WEEKS:
            snap[name] = self.stocks[name]
        # Measured stocks
        for name in MINERAL_TARGET_WK:
            snap[f"meas_{name}"] = self._measured_stocks.get(name, self.stocks[name])
        # Commodity prices
        for m, p in self.prices.items():
            snap[f"price_{m}"] = p
        snap["price_signal"] = self.price_signal
        snap["raw_price_signal"] = self.raw_price_signal
        for name, value in self.component_prices.items():
            snap[f"price_component_{name}"] = value
        for name, value in self.raw_component_prices.items():
            snap[f"raw_price_component_{name}"] = value
        # Cell capacity
        snap["cell_capacity"]     = self.cell_capacity
        snap["cell_capacity_wip"] = self.cell_capacity_wip
        snap["cell_cap_util"]     = self._cell_cap_utilisation()
        # Chemistry
        snap["lfp_share"]         = self.lfp_share
        # Tier 4
        snap["ev_demand_gwh_yr"]  = self.ev_demand_gwh_yr
        snap["oem_backlog_k"]     = self.oem_backlog_k
        snap["bullwhip_index"]    = self.bullwhip_index
        self.history.append(snap)

    # ──────────────────────────────────────────────────────────────────────────
    # Internal stock update helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _update_mineral_stocks(self, flows: Dict[str, float]) -> None:
        """
        Advance the in-transit pipeline for each mineral and update on-hand stock.

        For agent-controlled minerals (Li, Co, Gr, REE, SiC):
          new_supply = ABM agent output × expansion scale × stochastic noise

        For copper (no ABM agent):
          new_supply defaults to BASELINE_WK["copper"] × expansion scale × noise,
          representing a stable exogenous upstream market.

        The pipeline provides a hard physical transport delay: material ordered
        at time t arrives at time t + MINERAL_TRANSPORT_WK weeks.
        """
        for mineral in ALL_MINERALS:
            # Agent supply this week (normalised fraction of baseline)
            if mineral in AGENT_MINERALS:
                agent_supply = flows.get(f"{mineral}_in", 0.0)
            else:
                # Non-agent mineral: exogenous steady-state baseline
                agent_supply = flows.get(f"{mineral}_in", BASELINE_WK[mineral])

            # Stochastic noise: multiplicative, clipped to avoid extreme draws
            vol = MINERAL_SUPPLY_VOL.get(mineral, 0.03)
            noise = float(np.clip(self.rng.normal(0.0, vol), -3 * vol, 3 * vol))
            noise_factor = max(0.5, min(2.0, 1.0 + noise))

            # New supply entering the transit pipeline this week
            new_in_transit = (
                agent_supply
                * self._mineral_supply_scale[mineral]
                * noise_factor
            )

            # FIFO transit queue: pop oldest arrival, push new supply
            queue = self._mineral_transit[mineral]
            arrived = queue.popleft()
            queue.append(new_in_transit)

            # Outflow (mineral consumed this week by downstream agents)
            outflow = flows.get(f"{mineral}_out", 0.0)

            self.stocks[mineral] = max(0.0, self.stocks[mineral] + arrived - outflow)

    def _update_component_stocks(self, flows: Dict[str, float]) -> None:
        """Update Tier-2/3 component stocks (cells, packs, inverters, motors, harness)."""
        for name in COMPONENT_TARGET_WK:
            inflow  = flows.get(f"{name}_in",  0.0)
            outflow = flows.get(f"{name}_out", 0.0)
            self.stocks[name] = max(0.0, self.stocks[name] + inflow - outflow)

    # ──────────────────────────────────────────────────────────────────────────
    # Feedback loop implementations
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _softplus(x: float, beta: float = PRICE_SOFTPLUS_BETA) -> float:
        """
        Smooth, bounded scarcity function: log(1 + exp(β·x)) / β.

        Approximates max(0, x) for large positive x but is differentiable
        everywhere and avoids the 1/availability singularity of the previous
        price law.  β controls sharpness; higher β → closer to ReLU.
        """
        # Prevent float overflow for large positive x
        if x > 20.0 / beta:
            return x
        return float(np.log1p(np.exp(beta * x)) / beta)

    def _step_prices(self) -> None:
        """
        F1 — Supply-demand price formation (Balancing loop).

        Commodity prices adjust toward a scarcity-based target at speed
        PRICE_ADJ_SPEED.  The scarcity signal uses MEASURED inventory so
        the price response is delayed by the measurement lag.

        Price target:
            p* = 1 + α × softplus_β(1 − availability)

        When availability = 1 (balanced):  softplus(0) → small → p* ≈ 1
        When availability < 1 (shortage): softplus(+) grows → p* > 1
        When availability > 1 (surplus):  softplus(−) → 0 → p* < 1
        """
        for mineral in ALL_MINERALS:
            measured = max(
                self._measured_stocks.get(mineral, self.stocks.get(mineral, 1e-9)),
                1e-9,
            )
            target = (
                TARGET_WEEKS.get(mineral, MINERAL_TARGET_WK.get(mineral, 4.0))
                * BASELINE_WK.get(mineral, 1.0)
            )
            a = measured / max(target, 1e-9)

            scarcity = self._softplus(1.0 - a)
            p_star = max(PRICE_FLOOR, min(PRICE_CEIL, 1.0 + PRICE_ALPHA * scarcity))

            p_new = self.prices[mineral] + PRICE_ADJ_SPEED * (p_star - self.prices[mineral])
            self.prices[mineral] = max(PRICE_FLOOR, min(PRICE_CEIL, p_new))

    def _step_chemistry_mix(self) -> None:
        """
        F2 — Chemistry substitution loop (Balancing).

        LFP share target is a logistic function of log-cobalt-price, replacing
        the previous piecewise-linear mapping.  Benefits:
          • Smooth — no discontinuity at threshold prices.
          • Bounded — asymptotes at LFP_SHARE_MIN and LFP_SHARE_MAX naturally.
          • Calibratable — LOGISTIC_BETA_S and COBALT_LOGISTIC_MID control shape.

        A first-order lag on cobalt price perception (CHEM_COBALT_LAG = 1/4 wk⁻¹)
        models the managerial decision delay before production lines are retooled.

        LFP target mapping:
          cobalt ≪ COBALT_LOGISTIC_MID  →  s_target → LFP_SHARE_MIN (NMC preferred)
          cobalt ≈ COBALT_LOGISTIC_MID  →  s_target ≈ midpoint (0.535)
          cobalt ≫ COBALT_LOGISTIC_MID  →  s_target → LFP_SHARE_MAX (LFP preferred)
        """
        # Update perceived cobalt price (first-order lag, τ = 4 weeks)
        self._cobalt_price_delayed += CHEM_COBALT_LAG * (
            self.prices["cobalt"] - self._cobalt_price_delayed
        )
        cobalt = max(self._cobalt_price_delayed, 0.01)

        # Logistic target: z = log(cobalt/midpoint) centred at zero
        z = float(np.log(cobalt) - np.log(COBALT_LOGISTIC_MID))
        s_target = LFP_SHARE_MIN + (LFP_SHARE_MAX - LFP_SHARE_MIN) / (
            1.0 + np.exp(-LOGISTIC_BETA_S * z)
        )
        s_target = max(LFP_SHARE_MIN, min(LFP_SHARE_MAX, s_target))

        # Asymmetric convergence: faster toward LFP (urgency) than back to NMC (inertia)
        speed = CHEM_SHIFT_SPEED * (1.5 if s_target > self.lfp_share else 0.5)
        self.lfp_share += speed * (s_target - self.lfp_share)
        self.lfp_share = max(LFP_SHARE_MIN, min(LFP_SHARE_MAX, self.lfp_share))

    def _step_price_signal(self) -> None:
        """
        Simulate component-indexed EV price signals exposed to ABM agents.

        The source hierarchy follows deep-research-report2.md:
        - pack is a market-facing battery-pack index, anchored by BNEF-style
          USD/kWh levels and driven by battery-material cost pressure;
        - inverter, motor, and harness are benchmarked proxy indices;
        - SiC is a slow upstream semiconductor cost term;
        - vehicle is the downstream price index used by demand and firm
          decisions.
        """
        self._pack_learning_index = max(
            0.55,
            self._pack_learning_index * (1.0 + PACK_COST_LEARNING_WK),
        )

        parts_raw = self._parts_proxy_index()
        labour_raw = self.component_prices["labour"] * (1.0 + LABOUR_COST_GROWTH_WK)
        sic_raw = self.prices.get("sic_wafer", 1.0)

        pack_cost_raw = self._battery_price_signal()
        pack_raw = (
            (1.0 - PACK_PRICE_PASS_THROUGH)
            + PACK_PRICE_PASS_THROUGH * pack_cost_raw
        ) * self._pack_learning_index

        inverter_raw = (
            (1.0 - SIC_INVERTER_COST_SHARE) * parts_raw
            + SIC_INVERTER_COST_SHARE * sic_raw
        )
        motor_material_raw = 0.70 * self.prices.get("ree", 1.0) + 0.30 * parts_raw
        motor_raw = (
            (1.0 - MOTOR_MATERIAL_COST_SHARE) * parts_raw
            + MOTOR_MATERIAL_COST_SHARE * motor_material_raw
        )
        harness_raw = (
            HARNESS_COPPER_COST_SHARE * self.prices.get("copper", 1.0)
            + HARNESS_LABOUR_COST_SHARE * labour_raw
            + HARNESS_PARTS_COST_SHARE * parts_raw
        )

        vehicle_raw = (
            VEHICLE_PRICE_WEIGHTS["pack"] * pack_raw
            + VEHICLE_PRICE_WEIGHTS["inverter"] * inverter_raw
            + VEHICLE_PRICE_WEIGHTS["motor"] * motor_raw
            + VEHICLE_PRICE_WEIGHTS["harness"] * harness_raw
            + VEHICLE_PRICE_WEIGHTS["other"] * parts_raw
        )

        raw = {
            "pack": pack_raw,
            "inverter": inverter_raw,
            "motor": motor_raw,
            "harness": harness_raw,
            "vehicle": vehicle_raw,
            "sic": sic_raw,
            "parts": parts_raw,
            "labour": labour_raw,
        }

        self.raw_component_prices = {
            k: max(PRICE_SIGNAL_FLOOR, min(PRICE_SIGNAL_CEIL, v))
            for k, v in raw.items()
        }
        for name, target in self.raw_component_prices.items():
            speed = COMPONENT_PRICE_ADJ_SPEED.get(name, PRICE_SIGNAL_ADJ_SPEED)
            self.component_prices[name] += speed * (
                target - self.component_prices[name]
            )
            self.component_prices[name] = max(
                PRICE_SIGNAL_FLOOR,
                min(PRICE_SIGNAL_CEIL, self.component_prices[name]),
            )

        self.raw_price_signal = self.raw_component_prices["vehicle"]
        self.price_signal += PRICE_SIGNAL_ADJ_SPEED * (
            self.component_prices["vehicle"] - self.price_signal
        )
        self.price_signal = max(PRICE_SIGNAL_FLOOR, min(PRICE_SIGNAL_CEIL, self.price_signal))

    def _parts_proxy_index(self) -> float:
        """
        Synthetic motor-vehicle-parts price proxy.

        In the absence of an external BLS/ONS monthly series inside the repo,
        this uses a slow parts-cost trend plus component scarcity pressure.
        The structure can be replaced directly by an observed series later.
        """
        component_names = ("cells", "packs", "inverters", "motors", "harness")
        availability = []
        for name in component_names:
            target = TARGET_WEEKS[name] * BASELINE_WK[name]
            measured = self._measured_stocks.get(name, self.stocks.get(name, target))
            availability.append(measured / max(target, 1e-9))
        avg_availability = max(0.05, float(np.mean(availability)))
        scarcity = self._softplus(1.0 - avg_availability)
        trend = self.component_prices["parts"] * (1.0 + PARTS_COST_GROWTH_WK)
        return trend + 0.20 * scarcity

    def _step_cell_capacity(self, flows: Dict[str, float]) -> None:
        """
        F3 — Cell-capacity investment cycle (Reinforcing → Balancing).

        Investment decision pipeline:
          (1) Investment rate computed when utilisation > 85 %
              → capped at CAPEX_MAX_RATE_YR = 30 %/yr (actuator saturation)
          (2) Planning queue: 26-week FIFO delay (regulatory approval, site prep)
          (3) 3-stage Erlang construction pipeline: total mean delay = 104 weeks
              Each stage has rate n/τ_build = 3/104 per week.
              Erlang gives a peaked delay distribution (narrower than single
              first-order lag), more consistent with real gigafactory timelines.
          (4) Capacity online: last stage output → installed capacity
          (5) Depreciation: straight-line over 15-year economic life

        Total expected time from investment decision to first production:
          26 (planning) + 104 (build) = 130 weeks ≈ 2.5 years.
        """
        util = self._cell_cap_utilisation()
        excess_util = max(0.0, util - CAPEX_TRIGGER_UTIL)

        # Investment rate (proportional control on excess utilisation) with a
        # price-margin modifier: high pack prices support capacity investment,
        # while weak pack prices slow marginal expansion.
        pack_price = self.component_prices.get("pack", self.price_signal)
        price_modifier = max(0.50, min(1.60, 1.0 + 0.75 * (pack_price - 1.0)))
        inv_rate_yr  = (
            excess_util / max(1.0 - CAPEX_TRIGGER_UTIL, 1e-9)
            * 0.20
            * price_modifier
        )
        inv_wk       = self.cell_capacity * inv_rate_yr / 52.0
        # Actuator saturation: cap at CAPEX_MAX_RATE_YR
        inv_wk = min(inv_wk, self.cell_capacity * CAPEX_MAX_RATE_YR / 52.0)
        inv_wk = max(0.0, inv_wk)

        # Planning queue: investment waits CELL_CAPACITY_PLAN_WK weeks
        self._cap_planning_queue.append(inv_wk)
        released = self._cap_planning_queue.popleft()   # exits planning; enters stage 1

        # 3-stage Erlang construction pipeline (Euler integration)
        stage_rate = CAP_ERLANG_N / CELL_CAPACITY_BUILD_WK   # per week per stage
        d0 = released          - stage_rate * self._cap_stages[0]
        d1 = stage_rate * (self._cap_stages[0] - self._cap_stages[1])
        d2 = stage_rate * (self._cap_stages[1] - self._cap_stages[2])
        completion = stage_rate * self._cap_stages[2]

        self._cap_stages[0] = max(0.0, self._cap_stages[0] + d0)
        self._cap_stages[1] = max(0.0, self._cap_stages[1] + d1)
        self._cap_stages[2] = max(0.0, self._cap_stages[2] + d2)

        # Capacity comes online
        self.cell_capacity += completion

        # Depreciation (15-yr straight-line)
        self.cell_capacity = max(
            CELL_CAPACITY_2023_GWH_YR * 0.5,
            self.cell_capacity - self.cell_capacity * CELL_CAPACITY_DEPREC_WK,
        )

        # Soft-sync to ABM agent aggregate (10 % weekly correction; avoids drift)
        agent_cap = flows.get("cell_capacity_gwh_yr", 0.0)
        if agent_cap > 0.0:
            self.cell_capacity = 0.95 * self.cell_capacity + 0.05 * agent_cap

    def _step_demand_and_backlog(self, flows: Dict[str, float]) -> None:
        """
        F4 — EV demand dynamics (Balancing) and OEM backlog accumulation.

        Demand grows at the IEA-calibrated baseline rate.  Price elasticity
        applies to the weekly growth INCREMENT only — preventing the price
        effect from compounding into demand collapse under prolonged price
        elevation.

        A small stochastic demand noise term (σ = 1.5 % of the growth
        increment) reflects real-world week-to-week registration variability.
        """
        price_signal = self.price_signal
        price_effect = 1.0 + (-0.30) * (price_signal - 1.0)
        price_effect = max(0.60, min(1.20, price_effect))

        # Growth increment with price modulation
        growth_increment = (
            self.ev_demand_gwh_yr * EV_DEMAND_GROWTH_WK * price_effect
        )
        # Stochastic demand noise (small, mean-zero)
        demand_noise = float(
            self.rng.normal(0.0, DEMAND_VOL)
            * self.ev_demand_gwh_yr
            * EV_DEMAND_GROWTH_WK
        )
        self.ev_demand_gwh_yr = max(
            EV_DEMAND_2023_GWH_YR * 0.5,
            self.ev_demand_gwh_yr + growth_increment + demand_noise,
        )

        # Soft-couple SD demand to ABM market-agent aggregate (10 %/wk)
        market_gwh_wk = flows.get("total_demand_gwh_wk", 0.0)
        if market_gwh_wk > 0.0:
            self.ev_demand_gwh_yr = (
                0.90 * self.ev_demand_gwh_yr + 0.10 * market_gwh_wk * 52.0
            )

        # OEM backlog: proper stock with shortfall inflow and surplus outflow
        oem_prod_k  = flows.get("total_oem_prod_k", 0.0)
        demand_k    = flows.get("total_demand_k",   0.0)
        shortfall_k = max(0.0, demand_k - oem_prod_k)
        surplus_k   = max(0.0, oem_prod_k - demand_k)
        self.oem_backlog_k = max(
            0.0,
            self.oem_backlog_k + shortfall_k - surplus_k * 0.5,
        )

    def _step_bullwhip(self, flows: Dict[str, float]) -> None:
        """
        F5 — Bullwhip amplification tracking (EWMA-smoothed).

        Compares total Tier-1 component orders against vehicle demand × 4
        (since each vehicle needs 4 subsystem types).  Index > 1 indicates
        upstream amplification.
        """
        order_k   = flows.get("order_rate_k",   self._prev_orders_k)
        vehicle_k = flows.get("total_demand_k", self._prev_shipments_k)
        total_comp_demand = vehicle_k * 4.0

        raw_bw = order_k / max(total_comp_demand, 1e-9)
        self.bullwhip_index = (
            (1.0 - BULLWHIP_SMOOTH) * self.bullwhip_index
            + BULLWHIP_SMOOTH * raw_bw
        )
        self._prev_orders_k    = order_k
        self._prev_shipments_k = vehicle_k

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _cell_cap_utilisation(self) -> float:
        """
        Approximate capacity utilisation from cells inventory proxy.
        util_proxy ≈ 1 when cells are near target → ~70 % utilisation.
        Exact utilisation computed by cell_capacity_utilisation_exact().
        """
        weekly_cap = self.cell_capacity / 52.0
        if weekly_cap < 1e-9:
            return 0.0
        current_cells = self.stocks.get("cells", 0.0)
        target_cells  = TARGET_WEEKS["cells"] * BASELINE_WK["cells"]
        util_proxy    = min(2.0, current_cells / max(target_cells, 1e-9))
        return min(1.0, util_proxy * 0.70)

    def cell_capacity_utilisation_exact(self, weekly_output_gwh: float) -> float:
        """Exact utilisation given actual weekly cell output from ABM agents."""
        weekly_cap = self.cell_capacity / 52.0
        return min(2.0, weekly_output_gwh / max(weekly_cap, 1e-9))

    def _battery_price_signal(self) -> float:
        """
        Aggregate battery pack price pressure index (1.0 = 2023 baseline).

        BOM cost weights at 2023 prices:
          Lithium:  30 %  (cathode Li₂CO₃ / LiOH)
          Cobalt:   15 %  (NMC cathode, weighted by chemistry mix)
          Graphite: 15 %  (anode)
          REE:       5 %  (motor magnet; partially passed through in pack price)
          SiC:      10 %  (inverter; partially passed through)
          Other:    25 %  (labour, Al foil, separator — assumed stable)

        Copper excluded: harness cost is NOT part of the battery pack BOM.
        Cobalt weight is blended by LFP share (LFP = zero cobalt content).
        """
        cobalt_w = 0.15 * (1.0 - self.lfp_share)
        other_w  = 0.25 + 0.15 * self.lfp_share
        weights  = {
            "lithium":   0.30,
            "cobalt":    cobalt_w,
            "graphite":  0.15,
            "ree":       0.05,
            "sic_wafer": 0.10,
        }
        total_w = sum(weights.values()) + other_w
        idx = (
            sum(self.prices[m] * w for m, w in weights.items()) + other_w
        )
        return idx / max(total_w, 1e-9)

    # ──────────────────────────────────────────────────────────────────────────
    # Public derived metrics
    # ──────────────────────────────────────────────────────────────────────────

    def get_price_signal(self) -> float:
        """Smoothed vehicle-facing price pressure index simulated by the SD layer."""
        return self.price_signal

    def weeks_of_supply(self, stock_name: str) -> float:
        """Weeks of baseline EV-industry consumption remaining in a stock."""
        base = BASELINE_WK.get(stock_name, 1.0)
        return self.stocks.get(stock_name, 0.0) / max(base, 1e-9)

    def is_critical(self, stock_name: str, threshold_weeks: float = 1.0) -> bool:
        return self.weeks_of_supply(stock_name) < threshold_weeks

    def summary(self) -> Dict[str, float]:
        """Weeks-of-supply snapshot for all stocks plus key derived metrics."""
        result = {name: round(self.weeks_of_supply(name), 2) for name in TARGET_WEEKS}
        result.update({f"price_{m}": round(p, 3) for m, p in self.prices.items()})
        result.update({
            f"price_component_{name}": round(value, 3)
            for name, value in self.component_prices.items()
        })
        result["lfp_share"]         = round(self.lfp_share, 3)
        result["cell_capacity"]     = round(self.cell_capacity, 1)
        result["cell_cap_util"]     = round(self._cell_cap_utilisation(), 3)
        result["ev_demand_gwh_yr"]  = round(self.ev_demand_gwh_yr, 0)
        result["oem_backlog_k"]     = round(self.oem_backlog_k, 1)
        result["bullwhip_index"]    = round(self.bullwhip_index, 3)
        result["price_signal"]      = round(self.price_signal, 3)
        return result

    def tier_summary(self) -> Dict[str, Dict[str, float]]:
        """State summary grouped by supply-chain tier."""
        return {
            "tier_1_materials": {
                m: round(self.weeks_of_supply(m), 2) for m in MINERAL_TARGET_WK
            },
            "tier_1_prices": {
                m: round(self.prices[m], 3) for m in ALL_MINERALS
            },
            "tier_2_cells": {
                "cells_wks":            round(self.weeks_of_supply("cells"), 2),
                "lfp_share":            round(self.lfp_share, 3),
                "cell_capacity_gwh_yr": round(self.cell_capacity, 1),
                "cell_cap_util":        round(self._cell_cap_utilisation(), 3),
                "wip_gwh_yr":           round(self.cell_capacity_wip, 1),
            },
            "tier_3_components": {
                c: round(self.weeks_of_supply(c), 2)
                for c in ("packs", "inverters", "motors", "harness")
            },
            "tier_4_oem": {
                "ev_demand_gwh_yr": round(self.ev_demand_gwh_yr, 0),
                "oem_backlog_k":    round(self.oem_backlog_k, 1),
                "bullwhip_index":   round(self.bullwhip_index, 3),
                "vehicle_price":    round(self.get_price_signal(), 3),
                "pack_price":       round(self.component_prices["pack"], 3),
                "inverter_price":   round(self.component_prices["inverter"], 3),
                "motor_price":      round(self.component_prices["motor"], 3),
                "harness_price":    round(self.component_prices["harness"], 3),
            },
        }
