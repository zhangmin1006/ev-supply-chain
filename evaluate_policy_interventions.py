"""
Evaluate UK government intervention packages against supply-chain shocks.

Outputs:
  results/policy_intervention_evaluation.csv
"""

from __future__ import annotations

import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from model.hybrid_model import EVSupplyChainModel
from model.shocks import SCENARIOS, POLICY_SCENARIO_SOURCES


RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)


POLICY_SUFFIXES = {
    "tier1_policy": "Tier-1 Resilience Package",
    "minerals_policy": "Critical Minerals Security Package",
    "full_policy": "Full Industrial Strategy Package",
}


def _run(name: str, weeks: int, seed: int) -> pd.DataFrame:
    model = EVSupplyChainModel(scenario=SCENARIOS[name], seed=seed, n_weeks=weeks)
    model.run(weeks)
    df = model.get_results()
    df["scenario"] = name
    return df


def _loss_metrics(df: pd.DataFrame, baseline: pd.DataFrame) -> dict[str, float]:
    base = baseline["oem_production_k"].to_numpy()[:len(df)]
    prod = df["oem_production_k"].to_numpy()
    rel = prod / pd.Series(base).clip(lower=1e-9).to_numpy()
    loss = pd.Series(base - prod).clip(lower=0)
    return {
        "avg_output_k_wk": round(float(df["oem_production_k"].mean()), 3),
        "min_output_k_wk": round(float(df["oem_production_k"].min()), 3),
        "peak_loss_pct": round(float(max(0.0, (1.0 - rel.min()) * 100.0)), 3),
        "mean_loss_pct": round(float(pd.Series(1.0 - rel).clip(lower=0).mean() * 100.0), 3),
        "weeks_below_90pct": int((rel < 0.90).sum()),
        "cumulative_loss_k_veh": round(float(loss.sum()), 3),
        "max_backlog_k": round(float(df["total_backlog_k"].max()), 3),
        "max_price_signal": round(float(df["price_signal"].max()), 3),
    }


def evaluate(weeks: int = 260, seed: int = 42) -> pd.DataFrame:
    baseline = _run("baseline", weeks, seed)
    cache: dict[str, pd.DataFrame] = {"baseline": baseline}
    rows = []

    for base_name in POLICY_SCENARIO_SOURCES:
        cache[base_name] = _run(base_name, weeks, seed)
        base_metrics = _loss_metrics(cache[base_name], baseline)

        for suffix, label in POLICY_SUFFIXES.items():
            policy_name = f"{base_name}_{suffix}"
            cache[policy_name] = _run(policy_name, weeks, seed)
            policy_metrics = _loss_metrics(cache[policy_name], baseline)

            avoided_loss = (
                base_metrics["cumulative_loss_k_veh"]
                - policy_metrics["cumulative_loss_k_veh"]
            )
            peak_reduction = (
                base_metrics["peak_loss_pct"] - policy_metrics["peak_loss_pct"]
            )
            rows.append({
                "base_scenario": base_name,
                "policy_package": label,
                "policy_scenario": policy_name,
                "shock_only_cumulative_loss_k": base_metrics["cumulative_loss_k_veh"],
                "policy_cumulative_loss_k": policy_metrics["cumulative_loss_k_veh"],
                "avoided_loss_k_veh": round(avoided_loss, 3),
                "avoided_loss_pct": round(
                    avoided_loss / max(base_metrics["cumulative_loss_k_veh"], 1e-9) * 100.0,
                    3,
                ),
                "shock_only_peak_loss_pct": base_metrics["peak_loss_pct"],
                "policy_peak_loss_pct": policy_metrics["peak_loss_pct"],
                "peak_loss_reduction_pct_pt": round(peak_reduction, 3),
                "shock_only_weeks_below_90pct": base_metrics["weeks_below_90pct"],
                "policy_weeks_below_90pct": policy_metrics["weeks_below_90pct"],
                "shock_only_max_backlog_k": base_metrics["max_backlog_k"],
                "policy_max_backlog_k": policy_metrics["max_backlog_k"],
                "shock_only_avg_output_k_wk": base_metrics["avg_output_k_wk"],
                "policy_avg_output_k_wk": policy_metrics["avg_output_k_wk"],
            })

    return pd.DataFrame(rows).sort_values(
        ["avoided_loss_k_veh", "peak_loss_reduction_pct_pt"],
        ascending=False,
    )


def main() -> None:
    df = evaluate()
    path = os.path.join(RESULTS_DIR, "policy_intervention_evaluation.csv")
    df.to_csv(path, index=False)
    print(f"Wrote {path}")
    print(df.head(12).to_string(index=False))


if __name__ == "__main__":
    main()
