"""
EV Supply Chain Simulation — Entry Point
=========================================
Usage
-----
  python run_simulation.py                   # run all scenarios, save plots
  python run_simulation.py --scenarios baseline ukraine_harness drc_cobalt
  python run_simulation.py --list            # list available scenarios
  python run_simulation.py --weeks 130       # 2.5-year run

Outputs
-------
  results/<scenario>.csv                     — weekly time-series data
  results/comparison_plot.png                — multi-panel comparison figure
  results/shock_summary.csv                  — key metrics table
"""

from __future__ import annotations
import argparse
import os
import sys
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")          # non-interactive backend for saved figures
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.lines import Line2D

# ── project path ──────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
from model.hybrid_model import EVSupplyChainModel
from model.shocks import SCENARIOS, get_scenario, list_scenarios

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

# ── colour palette (one per scenario) ────────────────────────────────────────
COLOURS = {
    "baseline":                  "#94a3b8",   # grey
    "ukraine_harness":           "#ef4444",   # red
    "drc_cobalt":                "#f59e0b",   # amber
    "sic_bottleneck":            "#a855f7",   # purple
    "china_ree_restriction":     "#10b981",   # green
    "compound_shock":            "#3b82f6",   # blue
    "china_graphite":            "#ec4899",   # pink
    "us_china_tariff":           "#f97316",   # orange
    "uk_supply_chain_friction":  "#06b6d4",   # cyan  (UK-specific)
    "china_catl_disruption":     "#dc2626",   # deep red  (China cell concentration risk)
}


# =============================================================================
# Run a single scenario
# =============================================================================

def run_scenario(scenario_name: str, n_weeks: int, seed: int = 42) -> pd.DataFrame:
    sc = get_scenario(scenario_name)
    print(f"  Running '{scenario_name}' ({n_weeks} weeks) …", end=" ", flush=True)
    model = EVSupplyChainModel(scenario=sc, seed=seed, n_weeks=n_weeks)
    model.run()
    df = model.get_results()
    df["scenario"] = scenario_name

    # Save CSV
    csv_path = os.path.join(RESULTS_DIR, f"{scenario_name}.csv")
    df.to_csv(csv_path, index=False)
    print(f"done  ->  {csv_path}")
    return df


# =============================================================================
# Shock summary table
# =============================================================================

def build_summary(results: dict[str, pd.DataFrame],
                  baseline_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compare each shock scenario against baseline.
    Returns a DataFrame with one row per scenario.
    """
    baseline_prod = baseline_df["oem_production_k"].to_numpy()

    rows = []
    for name, df in results.items():
        if name == "baseline":
            continue
        prod = df["oem_production_k"].to_numpy()
        base = baseline_prod[:len(prod)]
        rel = prod / np.maximum(base, 1e-9)
        losses = np.maximum(0.0, 1.0 - rel)
        mean_prod = df["oem_production_k"].mean()
        loss_frac = losses.mean()
        peak_loss = losses.max()

        # Weeks below 90% of baseline
        below_90 = int((rel < 0.90).sum())

        # Weeks until full recovery (last week below 90%)
        sub = np.where(rel < 0.90)[0]
        recovery_week = int(df["week"].iloc[sub[-1]]) + 1 if len(sub) else 0

        cum_loss = (
            (baseline_df["oem_production_k"] - df["oem_production_k"])
            .clip(lower=0).sum()
        )

        rows.append({
            "Scenario":               name,
            "Avg production (k/wk)":  round(mean_prod, 1),
            "Peak loss (%)":          round(peak_loss * 100, 1),
            "Mean loss (%)":          round(loss_frac * 100, 1),
            "Weeks below 90%":        int(below_90),
            "Recovery week":          recovery_week,
            "Cumulative loss (k veh)":round(cum_loss, 0),
        })

    columns = [
        "Scenario",
        "Avg production (k/wk)",
        "Peak loss (%)",
        "Mean loss (%)",
        "Weeks below 90%",
        "Recovery week",
        "Cumulative loss (k veh)",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values("Peak loss (%)", ascending=False)


# =============================================================================
# Visualisation
# =============================================================================

def plot_comparison(results: dict[str, pd.DataFrame],
                    baseline_df: pd.DataFrame,
                    n_weeks: int,
                    save_path: str) -> None:
    """
    Six-panel comparison figure:
      [0,0] Global OEM production (k vehicles/week)
      [0,1] Production relative to baseline (%)
      [1,0] Cell production (GWh/week)
      [1,1] Cobalt & graphite stock (weeks of supply)
      [2,0] Harness & SiC stock (weeks of supply)
      [2,1] REE stock + price signal
    """
    weeks = np.arange(n_weeks)

    fig = plt.figure(figsize=(16, 13))
    fig.patch.set_facecolor("#0f1117")
    gs = gridspec.GridSpec(3, 2, figure=fig,
                           hspace=0.45, wspace=0.30,
                           left=0.07, right=0.97, top=0.93, bottom=0.06)
    axes = [fig.add_subplot(gs[r, c]) for r in range(3) for c in range(2)]

    _style_axes(axes)

    bl_prod  = baseline_df["oem_production_k"].values
    bl_cells = baseline_df["cell_production_gwh"].values

    for name, df in results.items():
        col   = COLOURS.get(name, "#888")
        lw    = 2.5 if name == "baseline" else 1.8
        alpha = 1.0 if name == "baseline" else 0.85
        ls    = "--" if name == "baseline" else "-"
        w     = df["week"].values

        # [0] OEM production
        axes[0].plot(w, df["oem_production_k"], color=col, lw=lw,
                     alpha=alpha, ls=ls, label=name)

        # [1] Relative production
        if name != "baseline":
            rel = (df["oem_production_k"].values / np.maximum(bl_prod, 1e-9) - 1) * 100
            axes[1].plot(w, rel, color=col, lw=lw, alpha=alpha)

        # [2] Cell production
        axes[2].plot(w, df["cell_production_gwh"], color=col, lw=lw,
                     alpha=alpha, ls=ls)

        # [3] Cobalt + graphite stocks
        axes[3].plot(w, df["stock_cobalt_wk"],   color=col, lw=lw,
                     alpha=alpha, ls=ls)
        axes[3].plot(w, df["stock_graphite_wk"], color=col, lw=lw,
                     alpha=alpha * 0.5, ls=":")

        # [4] Harness + SiC stocks
        axes[4].plot(w, df["stock_harness_wk"],    color=col, lw=lw,
                     alpha=alpha, ls=ls)
        axes[4].plot(w, df["stock_sic_wafer_wk"],  color=col, lw=lw,
                     alpha=alpha * 0.5, ls=":")

        # [5] REE + price signal
        axes[5].plot(w, df["stock_ree_wk"],   color=col, lw=lw,
                     alpha=alpha, ls=ls)
        axes[5].plot(w, df["price_signal"],   color=col, lw=lw * 0.6,
                     alpha=0.5, ls=":")

    # Reference lines
    axes[1].axhline(0,  color="#94a3b8", lw=0.8, ls="--")
    axes[1].axhline(-10, color="#f59e0b", lw=0.6, ls=":")
    for ax in axes[3:]:
        ax.axhline(1.0, color="#94a3b8", lw=0.6, ls="--")   # target = 1 wk

    # Labels
    _label(axes[0], "OEM Vehicle Production",   "k vehicles / week")
    _label(axes[1], "Production vs Baseline",   "deviation (%)")
    _label(axes[2], "Cell Production",           "GWh / week")
    _label(axes[3], "Cobalt (—) & Graphite (···) Stock", "weeks of supply")
    _label(axes[4], "Harness (—) & SiC (···) Stock",     "weeks of supply")
    _label(axes[5], "REE Stock (—) & Price Index (···)",  "weeks / index")

    # X-axis: year labels
    year_ticks = list(range(0, n_weeks + 1, 52))
    year_labels = [f"Yr {i//52}" for i in year_ticks]
    for ax in axes:
        ax.set_xticks(year_ticks)
        ax.set_xticklabels(year_labels, fontsize=8, color="#94a3b8")

    # Legend
    handles = [
        Line2D([0], [0], color=COLOURS.get(n, "#888"),
               lw=2, label=n.replace("_", " "))
        for n in results
    ]
    fig.legend(handles=handles, loc="upper center", ncol=4,
               bbox_to_anchor=(0.5, 0.975),
               fontsize=9, framealpha=0.15,
               facecolor="#1a1d27", edgecolor="#2e3244",
               labelcolor="#e2e8f0")

    fig.suptitle("EV Supply Chain — Shock Scenario Comparison",
                 fontsize=13, fontweight="bold", color="#e2e8f0", y=0.995)

    plt.savefig(save_path, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"\n  Comparison plot saved -> {save_path}")


def _style_axes(axes) -> None:
    for ax in axes:
        ax.set_facecolor("#1a1d27")
        ax.tick_params(colors="#94a3b8", labelsize=8)
        ax.spines[:].set_color("#2e3244")
        ax.grid(color="#2e3244", lw=0.5, alpha=0.6)


def _label(ax, title: str, ylabel: str) -> None:
    ax.set_title(title, color="#e2e8f0", fontsize=9, pad=5)
    ax.set_ylabel(ylabel, color="#94a3b8", fontsize=8)


# =============================================================================
# CLI
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="EV Supply Chain ABM+SD Simulation"
    )
    parser.add_argument(
        "--scenarios", nargs="+", default=list(SCENARIOS.keys()),
        help="Scenario names to run (default: all)",
    )
    parser.add_argument(
        "--weeks", type=int, default=260,
        help="Simulation horizon in weeks (default: 260 = 5 years)",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List available scenarios and exit",
    )
    args = parser.parse_args()

    if args.list:
        list_scenarios()
        return

    print("=" * 60)
    print("  EV Supply Chain ABM + SD Simulation")
    print(f"  Horizon : {args.weeks} weeks  ({args.weeks/52:.1f} years)")
    print(f"  Scenarios: {', '.join(args.scenarios)}")
    print("=" * 60)

    results: dict[str, pd.DataFrame] = {}
    for sc_name in args.scenarios:
        try:
            results[sc_name] = run_scenario(sc_name, args.weeks, args.seed)
        except KeyError as e:
            print(f"  [WARN] {e}")

    if not results:
        print("No valid scenarios found.")
        return

    # Baseline reference
    if "baseline" not in results:
        print("  Running baseline for comparison …", end=" ")
        results["baseline"] = run_scenario("baseline", args.weeks, args.seed)

    # Summary table
    summary = build_summary(results, results["baseline"])
    summary_path = os.path.join(RESULTS_DIR, "shock_summary.csv")
    summary.to_csv(summary_path, index=False)
    print("\n-- Shock Summary ------------------------------------------")
    print(summary.to_string(index=False))
    print(f"\n  Summary saved -> {summary_path}")

    # Comparison plot
    plot_path = os.path.join(RESULTS_DIR, "comparison_plot.png")
    plot_comparison(results, results["baseline"], args.weeks, plot_path)

    print("\nDone.")


if __name__ == "__main__":
    main()
