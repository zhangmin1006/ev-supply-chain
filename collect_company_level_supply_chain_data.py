"""
Collect company-level EV supply-chain data from index.html and dashboard.html.

The script converts the component and tier information embedded in the HTML
dashboards into a company-centric Excel workbook. It is designed as a companion
to collect_supply_chain_tier_data.py: that file is tier-first, this one is
firm-first.

Usage
-----
  python collect_company_level_supply_chain_data.py
  python collect_company_level_supply_chain_data.py --output ev_supply_chain_companies.xlsx
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from collect_supply_chain_tier_data import _extract_js_data


ROOT = Path(__file__).resolve().parent
DEFAULT_INDEX = ROOT / "index.html"
DEFAULT_DASHBOARD = ROOT / "dashboard.html"
DEFAULT_OUTPUT = ROOT / "ev_supply_chain_companies.xlsx"

RISK_SCORE = {"low": 1, "moderate": 2, "high": 3, "critical": 4}
RISK_LABEL = {value: key for key, value in RISK_SCORE.items()}

NON_COMPANY_TERMS = {
    "others",
    "global",
    "mixed",
    "usa",
    "uk",
    "eu",
    "china",
    "japan",
    "south korea",
    "korea",
    "germany",
    "france",
    "ireland",
    "taiwan",
    "netherlands",
    "switzerland",
    "israel",
    "italy",
    "canada",
    "australia",
    "chile",
    "peru",
    "indonesia",
    "russia",
    "philippines",
    "mozambique",
    "madagascar",
    "india",
    "norway",
    "myanmar",
    "rest of world",
    "dr congo",
    "s. africa",
    "gabon",
}

COMPANY_ALIASES = {
    "lg energy solution": "LG Energy Solution",
    "lg es": "LG Energy Solution",
    "byd cells": "BYD",
    "byd": "BYD",
    "on semiconductor": "ON Semiconductor",
    "on semi": "ON Semiconductor",
    "stmicro": "STMicroelectronics",
    "stmicroelectronics": "STMicroelectronics",
    "stm": "STMicroelectronics",
    "ti": "Texas Instruments",
    "texas instruments": "Texas Instruments",
    "nxp": "NXP Semiconductors",
    "nxp semiconductors": "NXP Semiconductors",
    "zf": "ZF Friedrichshafen",
    "zf friedrichshafen": "ZF Friedrichshafen",
    "sanhua": "SANHUA Intelligent Controls",
    "sanhua intelligent controls": "SANHUA Intelligent Controls",
    "nvidia drive orin": "NVIDIA",
    "nvidia drive platform": "NVIDIA",
    "nvidia": "NVIDIA",
    "qualcomm snapdragon ride": "Qualcomm",
    "qualcomm": "Qualcomm",
    "sony semiconductor": "Sony Semiconductor",
    "sony": "Sony Semiconductor",
    "brusa": "Brusa Elektronik",
    "brusa elektronik": "Brusa Elektronik",
    "mobileye": "Mobileye",
    "china northern re": "China Northern Rare Earth",
    "china northern rare earth": "China Northern Rare Earth",
    "mp materials": "MP Materials",
    "l&f": "L&F",
}

FIRM_METADATA = {
    "CATL": {"home_country": "China", "firm_type": "Battery cell manufacturer", "ticker_or_parent": "300750.SZ; 3750.HK"},
    "BYD": {"home_country": "China", "firm_type": "Vehicle OEM and battery manufacturer", "ticker_or_parent": "002594.SZ; 1211.HK"},
    "LG Energy Solution": {"home_country": "South Korea", "firm_type": "Battery cell manufacturer", "ticker_or_parent": "373220.KS"},
    "Panasonic": {"home_country": "Japan", "firm_type": "Battery cell and electronics manufacturer", "ticker_or_parent": "6752.T"},
    "Samsung SDI": {"home_country": "South Korea", "firm_type": "Battery cell manufacturer", "ticker_or_parent": "006400.KS"},
    "SK On": {"home_country": "South Korea", "firm_type": "Battery cell manufacturer", "ticker_or_parent": "SK Innovation"},
    "CALB": {"home_country": "China", "firm_type": "Battery cell manufacturer", "ticker_or_parent": "3931.HK"},
    "AESC UK": {"home_country": "United Kingdom", "firm_type": "Battery cell manufacturer", "ticker_or_parent": "Envision AESC"},
    "Bosch": {"home_country": "Germany", "firm_type": "Tier-1 supplier", "ticker_or_parent": "Robert Bosch GmbH"},
    "Continental": {"home_country": "Germany", "firm_type": "Tier-1 supplier", "ticker_or_parent": "CON.DE"},
    "ZF Friedrichshafen": {"home_country": "Germany", "firm_type": "Tier-1 supplier", "ticker_or_parent": "Private / foundation owned"},
    "BorgWarner": {"home_country": "United States", "firm_type": "Tier-1 supplier", "ticker_or_parent": "BWA"},
    "Infineon Technologies": {"home_country": "Germany", "firm_type": "Power semiconductor supplier", "ticker_or_parent": "IFX.DE"},
    "Wolfspeed": {"home_country": "United States", "firm_type": "SiC wafer and power semiconductor supplier", "ticker_or_parent": "WOLF"},
    "Coherent": {"home_country": "United States", "firm_type": "SiC substrate and photonics supplier", "ticker_or_parent": "COHR"},
    "NVIDIA": {"home_country": "United States", "firm_type": "ADAS compute and AI chip supplier", "ticker_or_parent": "NVDA"},
    "Qualcomm": {"home_country": "United States", "firm_type": "Automotive compute and connectivity supplier", "ticker_or_parent": "QCOM"},
    "NXP Semiconductors": {"home_country": "Netherlands", "firm_type": "Automotive semiconductor supplier", "ticker_or_parent": "NXPI"},
    "Mobileye": {"home_country": "Israel", "firm_type": "ADAS chip and software supplier", "ticker_or_parent": "MBLY"},
    "Tesla": {"home_country": "United States", "firm_type": "Vehicle OEM", "ticker_or_parent": "TSLA"},
    "SAIC": {"home_country": "China", "firm_type": "Vehicle OEM", "ticker_or_parent": "600104.SS"},
    "Geely": {"home_country": "China", "firm_type": "Vehicle OEM", "ticker_or_parent": "0175.HK"},
    "NIO": {"home_country": "China", "firm_type": "Vehicle OEM", "ticker_or_parent": "NIO; 9866.HK"},
    "Li Auto": {"home_country": "China", "firm_type": "Vehicle OEM", "ticker_or_parent": "LI; 2015.HK"},
    "Xpeng": {"home_country": "China", "firm_type": "Vehicle OEM", "ticker_or_parent": "XPEV; 9868.HK"},
    "Aptiv": {"home_country": "Ireland", "firm_type": "Tier-1 supplier", "ticker_or_parent": "APTV"},
    "Yazaki": {"home_country": "Japan", "firm_type": "Wiring and connector supplier", "ticker_or_parent": "Private"},
    "Sumitomo Electric": {"home_country": "Japan", "firm_type": "Wiring and connector supplier", "ticker_or_parent": "5802.T"},
    "TE Connectivity": {"home_country": "Switzerland / United States", "firm_type": "Connector and sensor supplier", "ticker_or_parent": "TEL"},
    "Valeo": {"home_country": "France", "firm_type": "Tier-1 supplier", "ticker_or_parent": "FR.PA"},
    "Hanon Systems": {"home_country": "South Korea", "firm_type": "Thermal systems supplier", "ticker_or_parent": "018880.KS"},
    "Mahle": {"home_country": "Germany", "firm_type": "Thermal and powertrain supplier", "ticker_or_parent": "Private"},
    "Denso": {"home_country": "Japan", "firm_type": "Tier-1 supplier", "ticker_or_parent": "6902.T"},
}


def _join(values: Any) -> str:
    if values is None:
        return ""
    if isinstance(values, set):
        values = sorted(values)
    if isinstance(values, list):
        return "; ".join(str(value) for value in values if str(value).strip())
    return str(values)


def _split_company_list(text: str) -> list[str]:
    if not text:
        return []
    protected = text.replace("France/Italy", "France and Italy")
    parts = re.split(r",|;", protected)
    return [part.strip() for part in parts if part.strip()]


def _clean_company_name(raw_name: str) -> str | None:
    name = raw_name.strip()
    name = re.sub(r"\([^)]*\)", "", name).strip()
    name = re.sub(r"\s+—.*$", "", name).strip()
    name = re.sub(r"\s+-\s+.*$", "", name).strip()
    name = re.sub(r"\bplatform\b", "", name, flags=re.IGNORECASE).strip()
    name = re.sub(r"\s+", " ", name)
    name = name.strip(" .")

    if not name:
        return None
    if "%" in name or re.search(r"\d", name):
        return None
    if name.lower() in NON_COMPANY_TERMS:
        return None
    if len(name) <= 2 and name.upper() not in {"ZF", "TI"}:
        return None

    alias = COMPANY_ALIASES.get(name.lower())
    if alias:
        return alias
    return name


def _country_from_supplier(raw_name: str) -> str:
    match = re.search(r"\(([^)]*)\)", raw_name)
    if not match:
        return ""
    country = match.group(1)
    country = re.sub(r"\s+—.*$", "", country).strip()
    return country


def _extract_dashboard_data(dashboard_path: Path) -> dict[str, Any]:
    html = dashboard_path.read_text(encoding="utf-8")
    script_match = re.search(r"<script>(.*)</script>", html, flags=re.DOTALL)
    if not script_match:
        return {"OEM_LABELS": {}, "OEM_CONFIG": {}, "CELL_CONFIG": []}

    script = script_match.group(1)
    start = script.find("const OEM_LABELS")
    end = script.find("const WEEK_LABELS")
    if start == -1 or end == -1:
        return {"OEM_LABELS": {}, "OEM_CONFIG": {}, "CELL_CONFIG": []}

    data_block = script[start:end]
    export_code = (
        data_block
        + """
const payload = { OEM_LABELS, OEM_CONFIG, CELL_CONFIG };
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
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()

    return json.loads(result.stdout)


def build_company_relationships(js_data: dict[str, Any], dashboard_data: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    relationship_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    model_exposure_rows: list[dict[str, Any]] = []

    for component in js_data["components"]:
        for raw_supplier in component.get("suppliers", []):
            company = _clean_company_name(raw_supplier)
            if not company:
                continue
            relationship_rows.append(
                {
                    "company_name": company,
                    "raw_name": raw_supplier,
                    "source_file": "index.html",
                    "relationship_type": "component_supplier",
                    "component_id": component.get("id"),
                    "component_name": component.get("name"),
                    "category": component.get("category"),
                    "tier_index": "",
                    "tier_label": "",
                    "node_id": "",
                    "node_name": "",
                    "chains": component.get("category"),
                    "risk": "",
                    "country_hint": _country_from_supplier(raw_supplier),
                    "note": component.get("supplyNote"),
                }
            )
            for source in component.get("sources", []):
                evidence_rows.append(
                    {
                        "company_name": company,
                        "evidence_type": "component_source",
                        "context": component.get("name"),
                        "source_label": source.get("label"),
                        "url": source.get("url"),
                    }
                )

    for tier_index, tier in enumerate(js_data["SC_TIERS"]):
        if tier.get("id") in {"raw", "markets"}:
            continue
        for node in tier.get("nodes", []):
            for raw_company in _split_company_list(node.get("lead", "")):
                company = _clean_company_name(raw_company)
                if not company:
                    continue
                relationship_rows.append(
                    {
                        "company_name": company,
                        "raw_name": raw_company,
                        "source_file": "index.html",
                        "relationship_type": "tier_lead_firm",
                        "component_id": "",
                        "component_name": "",
                        "category": "",
                        "tier_index": tier_index,
                        "tier_label": tier.get("label"),
                        "node_id": node.get("id"),
                        "node_name": node.get("name"),
                        "chains": _join(node.get("chains", [])),
                        "risk": node.get("risk"),
                        "country_hint": "",
                        "note": node.get("note"),
                    }
                )

    oem_labels = dashboard_data.get("OEM_LABELS", {})
    for oem_id, cfg in dashboard_data.get("OEM_CONFIG", {}).items():
        label = oem_labels.get(oem_id, oem_id)
        model_exposure_rows.append(
            {
                "entity_id": oem_id,
                "entity_label": label,
                "entity_type": "oem_group",
                "region": cfg.get("region"),
                "annual_target_k": cfg.get("target"),
                "vertical_integration": cfg.get("vi"),
                "supplier_mix": cfg.get("suppliers"),
                "key_vulnerability": cfg.get("vuln"),
                "source_file": "dashboard.html",
            }
        )
        for supplier_part in _split_company_list(cfg.get("suppliers", "")):
            company = _clean_company_name(supplier_part)
            if not company:
                continue
            share_match = re.search(r"(\d+(?:\.\d+)?)%", supplier_part)
            relationship_rows.append(
                {
                    "company_name": company,
                    "raw_name": supplier_part,
                    "source_file": "dashboard.html",
                    "relationship_type": "model_cell_supplier_to_oem",
                    "component_id": "",
                    "component_name": "Battery cells",
                    "category": "battery",
                    "tier_index": "",
                    "tier_label": "OEM model reference",
                    "node_id": oem_id,
                    "node_name": label,
                    "chains": "battery",
                    "risk": "",
                    "country_hint": cfg.get("region"),
                    "note": f"Supplier share: {share_match.group(1)}%" if share_match else "",
                }
            )

    for cell in dashboard_data.get("CELL_CONFIG", []):
        company = _clean_company_name(cell.get("name", ""))
        if not company:
            continue
        model_exposure_rows.append(
            {
                "entity_id": company.lower().replace(" ", "_"),
                "entity_label": company,
                "entity_type": "cell_maker",
                "region": cell.get("country"),
                "annual_target_k": "",
                "vertical_integration": "",
                "supplier_mix": "",
                "key_vulnerability": "",
                "source_file": "dashboard.html",
                "capacity_gwh_yr": cell.get("cap"),
                "global_cell_share_pct": cell.get("share"),
                "lfp_fraction_pct": cell.get("lfp"),
                "nmc_fraction_pct": cell.get("nmc"),
            }
        )
        relationship_rows.append(
            {
                "company_name": company,
                "raw_name": cell.get("name"),
                "source_file": "dashboard.html",
                "relationship_type": "model_cell_maker",
                "component_id": "",
                "component_name": "Battery cells",
                "category": "battery",
                "tier_index": 2,
                "tier_label": "Component Mfg",
                "node_id": "cells",
                "node_name": "Battery Cells",
                "chains": "battery",
                "risk": "critical",
                "country_hint": cell.get("country"),
                "note": f"Capacity {cell.get('cap')} GWh/yr; share {cell.get('share')}%; LFP {cell.get('lfp')}%; NMC {cell.get('nmc')}%",
            }
        )

    return pd.DataFrame(relationship_rows), pd.DataFrame(evidence_rows), pd.DataFrame(model_exposure_rows)


def build_company_master(relationships: pd.DataFrame, evidence: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if relationships.empty:
        return pd.DataFrame(rows)

    for company, group in relationships.groupby("company_name"):
        metadata = FIRM_METADATA.get(company, {})
        risk_values = [RISK_SCORE.get(str(risk).lower(), 0) for risk in group["risk"] if str(risk).strip()]
        max_risk_score = max(risk_values) if risk_values else 0
        source_count = int(evidence[evidence["company_name"] == company]["url"].nunique()) if not evidence.empty else 0

        rows.append(
            {
                "company_name": company,
                "home_country": metadata.get("home_country") or _first_non_empty(group["country_hint"]),
                "firm_type": metadata.get("firm_type", ""),
                "ticker_or_parent": metadata.get("ticker_or_parent", ""),
                "relationship_count": int(len(group)),
                "relationship_types": _join(set(group["relationship_type"])),
                "components": _join(set(group["component_name"]) - {""}),
                "categories": _join(set(group["category"]) - {""}),
                "tier_nodes": _join(set(group["node_name"]) - {""}),
                "tiers": _join(set(str(value) for value in group["tier_label"] if str(value).strip())),
                "chains": _join(_split_semicolon_values(group["chains"])),
                "highest_exposed_risk": RISK_LABEL.get(max_risk_score, ""),
                "source_files": _join(set(group["source_file"])),
                "supporting_source_count": source_count,
                "notes": _join(set(str(value) for value in group["note"] if str(value).strip())),
            }
        )

    return pd.DataFrame(rows).sort_values(["relationship_count", "company_name"], ascending=[False, True])


def _first_non_empty(series: pd.Series) -> str:
    for value in series:
        if str(value).strip():
            return str(value).strip()
    return ""


def _split_semicolon_values(series: pd.Series) -> set[str]:
    values: set[str] = set()
    for item in series:
        for part in re.split(r";|,", str(item)):
            if part.strip():
                values.add(part.strip())
    return values


def build_component_company_matrix(relationships: pd.DataFrame) -> pd.DataFrame:
    rows = relationships[relationships["component_name"].astype(str).str.len() > 0]
    if rows.empty:
        return pd.DataFrame()
    matrix = pd.crosstab(rows["company_name"], rows["component_name"])
    matrix = matrix.reset_index()
    return matrix


def build_summary(company_master: pd.DataFrame, relationships: pd.DataFrame) -> pd.DataFrame:
    rows = [
        {"metric": "Unique companies", "value": int(len(company_master))},
        {"metric": "Company relationships", "value": int(len(relationships))},
        {"metric": "Companies with metadata", "value": int(company_master["firm_type"].astype(bool).sum())},
        {
            "metric": "Companies exposed to high or critical tier nodes",
            "value": int(company_master["highest_exposed_risk"].isin(["high", "critical"]).sum()),
        },
    ]
    by_type = (
        relationships.groupby("relationship_type", dropna=False)
        .size()
        .reset_index(name="value")
        .rename(columns={"relationship_type": "metric"})
    )
    by_type["metric"] = "Relationships - " + by_type["metric"].astype(str)
    return pd.concat([pd.DataFrame(rows), by_type], ignore_index=True)


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
    parser = argparse.ArgumentParser(description="Collect company-level EV supply-chain data into Excel.")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX, help=f"Path to index.html. Default: {DEFAULT_INDEX}")
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD, help=f"Path to dashboard.html. Default: {DEFAULT_DASHBOARD}")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help=f"Output workbook. Default: {DEFAULT_OUTPUT}")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    index_path = args.index.resolve()
    dashboard_path = args.dashboard.resolve()
    output_path = args.output.resolve()

    js_data = _extract_js_data(index_path)
    dashboard_data = _extract_dashboard_data(dashboard_path) if dashboard_path.exists() else {}
    relationships, evidence, model_exposure = build_company_relationships(js_data, dashboard_data)
    company_master = build_company_master(relationships, evidence)
    component_matrix = build_component_company_matrix(relationships)

    notes = pd.DataFrame(
        [
            {"field": "index_html_path", "value": str(index_path)},
            {"field": "index_html_sha256", "value": hashlib.sha256(index_path.read_bytes()).hexdigest()},
            {"field": "dashboard_html_path", "value": str(dashboard_path) if dashboard_path.exists() else ""},
            {
                "field": "dashboard_html_sha256",
                "value": hashlib.sha256(dashboard_path.read_bytes()).hexdigest() if dashboard_path.exists() else "",
            },
            {"field": "generated_at_utc", "value": datetime.now(timezone.utc).isoformat()},
            {
                "field": "method",
                "value": "Company names extracted from index.html component supplier lists and value-chain lead-firm fields; dashboard.html OEM/cell model references added when available.",
            },
            {
                "field": "caution",
                "value": "This workbook is a normalized company index from the dashboard data. It does not claim exhaustive global market coverage unless a company appears in the source HTML.",
            },
        ]
    )

    sheets = {
        "summary": build_summary(company_master, relationships),
        "company_master": company_master,
        "company_relationships": relationships,
        "company_evidence": evidence,
        "model_exposure": model_exposure,
        "component_company_matrix": component_matrix,
        "notes": notes,
    }

    write_workbook(sheets, output_path)
    print(f"Wrote {output_path}")
    print(sheets["summary"].to_string(index=False))


if __name__ == "__main__":
    main()
