"""
Collect EV supply-chain tier data from index.html and model/config.py.

The workbook combines:
  - value-chain tiers shown in index.html
  - component reference cards and HS codes from index.html
  - data-source and World Bank indicator metadata from index.html
  - calibrated model parameters from model/config.py

Usage
-----
  python collect_supply_chain_tier_data.py
  python collect_supply_chain_tier_data.py --output ev_supply_chain_tiers.xlsx

Requirements
------------
  - Python packages from requirements.txt
  - Node.js, used only to safely evaluate the JavaScript data literals embedded
    in index.html
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from model import config


ROOT = Path(__file__).resolve().parent
DEFAULT_INDEX = ROOT / "index.html"
DEFAULT_OUTPUT = ROOT / "ev_supply_chain_tiers.xlsx"


def _extract_js_data(index_path: Path) -> dict[str, Any]:
    html = index_path.read_text(encoding="utf-8")
    script_match = re.search(r"<script>(.*)</script>", html, flags=re.DOTALL)
    if not script_match:
        raise RuntimeError("Could not find the inline <script> block in index.html.")

    script = script_match.group(1)
    start = script.find("const components")
    end = script.find("let scActiveChain")
    if start == -1 or end == -1:
        raise RuntimeError("Could not locate the expected JavaScript data block in index.html.")

    data_block = script[start:end]
    export_code = (
        data_block
        + """
const payload = {
  components,
  dataSources,
  WB_COUNTRIES,
  WB_INDICATORS,
  SC_CHAINS,
  SC_TIERS,
  SC_VULNS
};
process.stdout.write(JSON.stringify(payload));
"""
    )

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", dir=ROOT, delete=False) as temp_file:
            temp_file.write(export_code)
            temp_path = Path(temp_file.name)

        result = subprocess.run(
            ["node", str(temp_path)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=ROOT,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Node.js is required to extract JavaScript data from index.html.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Node.js failed while extracting index.html data:\n{exc.stderr}") from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()

    return json.loads(result.stdout)


def _join(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _flatten_mapping_rows(mapping: dict[str, dict[str, Any]], id_name: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for key, values in mapping.items():
        row = {id_name: key}
        for col, value in values.items():
            row[col] = _join(value)
        rows.append(row)
    return pd.DataFrame(rows)


def build_supply_chain_tiers(js_data: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    tier_rows: list[dict[str, Any]] = []
    membership_rows: list[dict[str, Any]] = []

    for tier_index, tier in enumerate(js_data["SC_TIERS"]):
        for node in tier["nodes"]:
            chains = node.get("chains", [])
            tier_rows.append(
                {
                    "tier_index": tier_index,
                    "tier_id": tier.get("id"),
                    "tier_label": tier.get("label"),
                    "tier_subtitle": tier.get("sub"),
                    "node_id": node.get("id"),
                    "node_name": node.get("name"),
                    "risk": node.get("risk"),
                    "stat": node.get("stat"),
                    "lead_firms_or_countries": node.get("lead"),
                    "chains": _join(chains),
                    "note": node.get("note"),
                }
            )
            for chain_id in chains:
                membership_rows.append(
                    {
                        "tier_index": tier_index,
                        "tier_id": tier.get("id"),
                        "node_id": node.get("id"),
                        "node_name": node.get("name"),
                        "chain_id": chain_id,
                    }
                )

    return pd.DataFrame(tier_rows), pd.DataFrame(membership_rows)


def build_components(js_data: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    component_rows: list[dict[str, Any]] = []
    hs_rows: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []

    for component in js_data["components"]:
        component_rows.append(
            {
                "component_id": component.get("id"),
                "component_name": component.get("name"),
                "category": component.get("category"),
                "description": component.get("description"),
                "types": _join(component.get("types", [])),
                "materials": _join(component.get("materials", [])),
                "suppliers": _join(component.get("suppliers", [])),
                "countries": _join(component.get("countries", [])),
                "standards": _join(component.get("standards", [])),
                "supply_note": component.get("supplyNote"),
            }
        )
        for hs in component.get("hsCodes", []):
            hs_rows.append(
                {
                    "component_id": component.get("id"),
                    "component_name": component.get("name"),
                    "hs_code": hs.get("code"),
                    "chinese_description": hs.get("cn"),
                    "english_description": hs.get("en"),
                    "classification_note": hs.get("note"),
                }
            )
        for source in component.get("sources", []):
            source_rows.append(
                {
                    "component_id": component.get("id"),
                    "component_name": component.get("name"),
                    "source_label": source.get("label"),
                    "url": source.get("url"),
                }
            )

    return pd.DataFrame(component_rows), pd.DataFrame(hs_rows), pd.DataFrame(source_rows)


def build_market_country_rows(js_data: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    return pd.DataFrame(js_data["WB_COUNTRIES"]), pd.DataFrame(js_data["WB_INDICATORS"])


def build_data_sources(js_data: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for item in js_data["dataSources"]:
        rows.append(
            {
                "type": item.get("type"),
                "has_api": item.get("api"),
                "name": item.get("name"),
                "organisation": item.get("org"),
                "provides": item.get("provides"),
                "coverage": item.get("coverage"),
                "url": item.get("url"),
                "access": item.get("access"),
            }
        )
    return pd.DataFrame(rows)


def build_model_sheets() -> dict[str, pd.DataFrame]:
    return {
        "model_minerals": _flatten_mapping_rows(config.MINERALS, "mineral_id"),
        "model_cell_makers": _flatten_mapping_rows(config.CELL_MAKERS, "cell_maker_id"),
        "model_tier1": _flatten_mapping_rows(config.TIER1, "subsystem_id"),
        "model_oems": _flatten_mapping_rows(config.OEMS, "oem_id"),
        "model_markets": _flatten_mapping_rows(config.MARKETS, "market_id"),
    }


def build_summary(tiers: pd.DataFrame, components: pd.DataFrame, vulnerabilities: pd.DataFrame) -> pd.DataFrame:
    risk_counts = tiers.groupby("risk", dropna=False).size().reset_index(name="value")
    risk_counts["metric"] = "Tier nodes with risk: " + risk_counts["risk"].astype(str)
    risk_counts = risk_counts[["metric", "value"]]

    rows = pd.DataFrame(
        [
            {"metric": "Supply-chain tiers", "value": int(tiers["tier_id"].nunique())},
            {"metric": "Supply-chain nodes", "value": int(len(tiers))},
            {"metric": "Component reference records", "value": int(len(components))},
            {"metric": "Key vulnerability records", "value": int(len(vulnerabilities))},
            {"metric": "Model minerals", "value": int(len(config.MINERALS))},
            {"metric": "Model cell makers", "value": int(len(config.CELL_MAKERS))},
            {"metric": "Model Tier-1 subsystems", "value": int(len(config.TIER1))},
            {"metric": "Model OEM groups", "value": int(len(config.OEMS))},
            {"metric": "Model end markets", "value": int(len(config.MARKETS))},
        ]
    )
    return pd.concat([rows, risk_counts], ignore_index=True)


def write_workbook(sheets: dict[str, pd.DataFrame], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for sheet_name, df in sheets.items():
            df.to_excel(writer, sheet_name=sheet_name[:31], index=False)

        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column_cells in worksheet.columns:
                max_length = max(len(str(cell.value or "")) for cell in column_cells)
                worksheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 12), 70)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect EV supply-chain tier data into Excel.")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX, help=f"Path to index.html. Default: {DEFAULT_INDEX}")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help=f"Output workbook. Default: {DEFAULT_OUTPUT}")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    index_path = args.index.resolve()
    output_path = args.output.resolve()

    js_data = _extract_js_data(index_path)
    tiers, chain_membership = build_supply_chain_tiers(js_data)
    components, component_hs_codes, component_sources = build_components(js_data)
    wb_countries, wb_indicators = build_market_country_rows(js_data)
    data_sources = build_data_sources(js_data)
    vulnerabilities = pd.DataFrame(js_data["SC_VULNS"])

    index_bytes = index_path.read_bytes()
    notes = pd.DataFrame(
        [
            {"field": "index_html_path", "value": str(index_path)},
            {"field": "index_html_sha256", "value": hashlib.sha256(index_bytes).hexdigest()},
            {"field": "generated_at_utc", "value": datetime.now(timezone.utc).isoformat()},
            {
                "field": "method",
                "value": "JavaScript data arrays extracted from index.html with Node.js; model parameters imported from model/config.py.",
            },
            {
                "field": "comparison_warning",
                "value": "Risk, market share, and production figures mix source years and measurement types. Use stat, note, and source sheets before quantitative comparison.",
            },
        ]
    )

    sheets: dict[str, pd.DataFrame] = {
        "summary": build_summary(tiers, components, vulnerabilities),
        "supply_chain_tiers": tiers,
        "chain_membership": chain_membership,
        "components": components,
        "component_hs_codes": component_hs_codes,
        "component_sources": component_sources,
        "vulnerabilities": vulnerabilities,
        "data_sources": data_sources,
        "wb_countries": wb_countries,
        "wb_indicators": wb_indicators,
        **build_model_sheets(),
        "notes": notes,
    }

    write_workbook(sheets, output_path)
    print(f"Wrote {output_path}")
    print(sheets["summary"].to_string(index=False))


if __name__ == "__main__":
    main()
