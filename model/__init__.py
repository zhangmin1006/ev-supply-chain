"""
EV Supply Chain ABM + System Dynamics Simulation Model
=======================================================
Investigates supply shock propagation across the EV value chain.

Architecture
------------
  SDModel      — discrete-time stock/flow dynamics (weekly Euler integration)
  Agents       — heterogeneous firm-level decision makers
  HybridModel  — couples the two layers each timestep
  shocks       — predefined shock scenario library

Units
-----
  Time          : weeks  (1 step = 1 week)
  Mineral stocks: weeks of EV-industry supply (normalised)
  Cell output   : GWh / week
  Vehicles      : k vehicles / week
  Prices        : index relative to baseline (1.0)
"""
