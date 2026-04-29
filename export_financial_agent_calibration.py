"""
Export the financial calibration used by the redesigned agents.

Usage
-----
  python export_financial_agent_calibration.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from model.config import CELL_MAKERS, OEMS, TIER1
from model.financial_profiles import AGENT_FINANCIAL_PEERS, DEFAULT_FINANCIAL_WORKBOOK, profile_for_agent
from model.hybrid_model import _MINERAL_SOURCES


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "financial_agent_calibration.xlsx"


def build_rows() -> pd.DataFrame:
    rows = []

    for mineral, sources in _MINERAL_SOURCES.items():
        for agent_id, country, share in sources:
            rows.append(
                {
                    "agent_id": agent_id,
                    "agent_type": "mineral_source",
                    "model_entity": mineral,
                    "country_or_region": country,
                    "baseline_share_or_capacity": share,
                }
            )

    for name, cfg in CELL_MAKERS.items():
        rows.append(
            {
                "agent_id": f"cell_{name}",
                "agent_type": "cell_maker",
                "model_entity": name,
                "country_or_region": cfg["country"],
                "baseline_share_or_capacity": cfg["capacity_gwh_yr"],
            }
        )

    for comp, cfg in TIER1.items():
        rows.append(
            {
                "agent_id": f"t1_{comp}",
                "agent_type": "tier1_subsystem",
                "model_entity": comp,
                "country_or_region": "mixed",
                "baseline_share_or_capacity": cfg["capacity_units_yr_k"],
            }
        )

    for name, cfg in OEMS.items():
        rows.append(
            {
                "agent_id": f"oem_{name}",
                "agent_type": "oem_group",
                "model_entity": name,
                "country_or_region": cfg["region"],
                "baseline_share_or_capacity": cfg["annual_target_k"],
            }
        )

    enriched = []
    for row in rows:
        profile = profile_for_agent(row["agent_id"])
        enriched.append(
            {
                **row,
                "financial_peer_companies": "; ".join(AGENT_FINANCIAL_PEERS.get(row["agent_id"], ())),
                "matched_financial_companies": "; ".join(profile.company_names),
                "revenue_latest": profile.revenue_latest,
                "revenue_cagr": profile.revenue_cagr,
                "operating_margin": profile.operating_margin,
                "free_cash_flow_margin": profile.free_cash_flow_margin,
                "capex_intensity": profile.capex_intensity,
                "debt_to_assets": profile.debt_to_assets,
                "recovery_multiplier": profile.recovery_multiplier,
                "inventory_multiplier": profile.inventory_multiplier,
                "growth_multiplier": profile.growth_multiplier,
                "shock_absorption": profile.shock_absorption,
            }
        )

    return pd.DataFrame(enriched)


def main() -> None:
    calibration = build_rows()
    notes = pd.DataFrame(
        [
            {"field": "financial_workbook", "value": str(DEFAULT_FINANCIAL_WORKBOOK)},
            {
                "field": "method",
                "value": "Five-year listed-company financials are converted to bounded recovery, inventory, growth, and shock-absorption multipliers.",
            },
        ]
    )
    with pd.ExcelWriter(DEFAULT_OUTPUT, engine="openpyxl") as writer:
        calibration.to_excel(writer, sheet_name="agent_calibration", index=False)
        notes.to_excel(writer, sheet_name="notes", index=False)
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column_cells in worksheet.columns:
                max_length = max(len(str(cell.value or "")) for cell in column_cells)
                worksheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 12), 70)

    print(f"Wrote {DEFAULT_OUTPUT}")
    print(calibration[["agent_id", "agent_type", "matched_financial_companies", "recovery_multiplier", "growth_multiplier"]].to_string(index=False))


if __name__ == "__main__":
    main()
