"""
Identify publicly listed companies in the EV supply chain.

The script builds on collect_company_level_supply_chain_data.py. It extracts
companies from index.html and dashboard.html, flags listed companies using a
curated ticker map, and writes an Excel workbook focused on public-market
entities and their EV supply-chain roles.

Usage
-----
  python identify_listed_ev_supply_chain_companies.py
  python identify_listed_ev_supply_chain_companies.py --output listed_ev_supply_chain_companies.xlsx
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from collect_company_level_supply_chain_data import (
    DEFAULT_DASHBOARD,
    DEFAULT_INDEX,
    FIRM_METADATA,
    _extract_dashboard_data,
    build_company_master,
    build_company_relationships,
)
from collect_supply_chain_tier_data import _extract_js_data


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "listed_ev_supply_chain_companies.xlsx"


LISTING_OVERRIDES = {
    "Aptiv": {"listed_status": "listed", "primary_ticker": "APTV", "primary_exchange": "NYSE"},
    "Analog Devices": {"listed_status": "listed", "primary_ticker": "ADI", "primary_exchange": "NASDAQ"},
    "Amphenol": {"listed_status": "listed", "primary_ticker": "APH", "primary_exchange": "NYSE"},
    "BorgWarner": {"listed_status": "listed", "primary_ticker": "BWA", "primary_exchange": "NYSE"},
    "Brembo": {"listed_status": "listed", "primary_ticker": "BRE", "primary_exchange": "Borsa Italiana"},
    "BYD": {"listed_status": "listed", "primary_ticker": "002594.SZ", "primary_exchange": "Shenzhen Stock Exchange"},
    "CALB": {"listed_status": "listed", "primary_ticker": "3931.HK", "primary_exchange": "Hong Kong Stock Exchange"},
    "CATL": {"listed_status": "listed", "primary_ticker": "300750.SZ", "primary_exchange": "Shenzhen Stock Exchange"},
    "Coherent": {"listed_status": "listed", "primary_ticker": "COHR", "primary_exchange": "NYSE"},
    "Continental": {"listed_status": "listed", "primary_ticker": "CON.DE", "primary_exchange": "Xetra"},
    "Dana": {"listed_status": "listed", "primary_ticker": "DAN", "primary_exchange": "NYSE"},
    "Delta Electronics": {"listed_status": "listed", "primary_ticker": "2308.TW", "primary_exchange": "Taiwan Stock Exchange"},
    "Denso": {"listed_status": "listed", "primary_ticker": "6902.T", "primary_exchange": "Tokyo Stock Exchange"},
    "Eaton": {"listed_status": "listed", "primary_ticker": "ETN", "primary_exchange": "NYSE"},
    "Geely": {"listed_status": "listed", "primary_ticker": "0175.HK", "primary_exchange": "Hong Kong Stock Exchange"},
    "Hanon Systems": {"listed_status": "listed", "primary_ticker": "018880.KS", "primary_exchange": "Korea Exchange"},
    "Hitachi Astemo": {"listed_status": "subsidiary of listed company", "primary_ticker": "6501.T", "primary_exchange": "Tokyo Stock Exchange"},
    "Hyundai Mobis": {"listed_status": "listed", "primary_ticker": "012330.KS", "primary_exchange": "Korea Exchange"},
    "Infineon Technologies": {"listed_status": "listed", "primary_ticker": "IFX.DE", "primary_exchange": "Xetra"},
    "L&F": {"listed_status": "listed", "primary_ticker": "066970.KQ", "primary_exchange": "KOSDAQ"},
    "Leoni": {"listed_status": "private / delisted", "primary_ticker": "", "primary_exchange": ""},
    "LG Energy Solution": {"listed_status": "listed", "primary_ticker": "373220.KS", "primary_exchange": "Korea Exchange"},
    "Li Auto": {"listed_status": "listed", "primary_ticker": "LI", "primary_exchange": "NASDAQ"},
    "Littelfuse": {"listed_status": "listed", "primary_ticker": "LFUS", "primary_exchange": "NASDAQ"},
    "Luminar Technologies": {"listed_status": "listed", "primary_ticker": "LAZR", "primary_exchange": "NASDAQ"},
    "Mahle": {"listed_status": "private", "primary_ticker": "", "primary_exchange": ""},
    "Mobileye": {"listed_status": "listed", "primary_ticker": "MBLY", "primary_exchange": "NASDAQ"},
    "Modine Manufacturing": {"listed_status": "listed", "primary_ticker": "MOD", "primary_exchange": "NYSE"},
    "Murata Manufacturing": {"listed_status": "listed", "primary_ticker": "6981.T", "primary_exchange": "Tokyo Stock Exchange"},
    "Nidec": {"listed_status": "listed", "primary_ticker": "6594.T", "primary_exchange": "Tokyo Stock Exchange"},
    "NIO": {"listed_status": "listed", "primary_ticker": "NIO", "primary_exchange": "NYSE"},
    "Norsk Hydro": {"listed_status": "listed", "primary_ticker": "NHY.OL", "primary_exchange": "Oslo Stock Exchange"},
    "Novelis": {"listed_status": "subsidiary of listed company", "primary_ticker": "HINDALCO.NS", "primary_exchange": "NSE India"},
    "NVIDIA": {"listed_status": "listed", "primary_ticker": "NVDA", "primary_exchange": "NASDAQ"},
    "NXP Semiconductors": {"listed_status": "listed", "primary_ticker": "NXPI", "primary_exchange": "NASDAQ"},
    "ON Semiconductor": {"listed_status": "listed", "primary_ticker": "ON", "primary_exchange": "NASDAQ"},
    "Panasonic": {"listed_status": "listed", "primary_ticker": "6752.T", "primary_exchange": "Tokyo Stock Exchange"},
    "Phoenix Contact": {"listed_status": "private", "primary_ticker": "", "primary_exchange": ""},
    "Qualcomm": {"listed_status": "listed", "primary_ticker": "QCOM", "primary_exchange": "NASDAQ"},
    "Renesas Electronics": {"listed_status": "listed", "primary_ticker": "6723.T", "primary_exchange": "Tokyo Stock Exchange"},
    "SAIC": {"listed_status": "listed", "primary_ticker": "600104.SS", "primary_exchange": "Shanghai Stock Exchange"},
    "Samsung SDI": {"listed_status": "listed", "primary_ticker": "006400.KS", "primary_exchange": "Korea Exchange"},
    "Sanden Holdings": {"listed_status": "listed", "primary_ticker": "6444.T", "primary_exchange": "Tokyo Stock Exchange"},
    "SANHUA Intelligent Controls": {"listed_status": "listed", "primary_ticker": "002050.SZ", "primary_exchange": "Shenzhen Stock Exchange"},
    "Sensata Technologies": {"listed_status": "listed", "primary_ticker": "ST", "primary_exchange": "NYSE"},
    "SK On": {"listed_status": "subsidiary of listed company", "primary_ticker": "096770.KS", "primary_exchange": "Korea Exchange"},
    "Sony Semiconductor": {"listed_status": "subsidiary of listed company", "primary_ticker": "6758.T", "primary_exchange": "Tokyo Stock Exchange"},
    "STMicroelectronics": {"listed_status": "listed", "primary_ticker": "STM", "primary_exchange": "NYSE / Euronext Paris"},
    "Sumitomo Electric": {"listed_status": "listed", "primary_ticker": "5802.T", "primary_exchange": "Tokyo Stock Exchange"},
    "Taiyo Yuden": {"listed_status": "listed", "primary_ticker": "6976.T", "primary_exchange": "Tokyo Stock Exchange"},
    "TDK": {"listed_status": "listed", "primary_ticker": "6762.T", "primary_exchange": "Tokyo Stock Exchange"},
    "TE Connectivity": {"listed_status": "listed", "primary_ticker": "TEL", "primary_exchange": "NYSE"},
    "Tesla": {"listed_status": "listed", "primary_ticker": "TSLA", "primary_exchange": "NASDAQ"},
    "Texas Instruments": {"listed_status": "listed", "primary_ticker": "TXN", "primary_exchange": "NASDAQ"},
    "Toray Industries": {"listed_status": "listed", "primary_ticker": "3402.T", "primary_exchange": "Tokyo Stock Exchange"},
    "Valeo": {"listed_status": "listed", "primary_ticker": "FR.PA", "primary_exchange": "Euronext Paris"},
    "Vicor": {"listed_status": "listed", "primary_ticker": "VICR", "primary_exchange": "NASDAQ"},
    "Vishay": {"listed_status": "listed", "primary_ticker": "VSH", "primary_exchange": "NYSE"},
    "Wolfspeed": {"listed_status": "listed", "primary_ticker": "WOLF", "primary_exchange": "NYSE"},
    "Xpeng": {"listed_status": "listed", "primary_ticker": "XPEV", "primary_exchange": "NYSE"},
    "XPeng": {"listed_status": "listed", "primary_ticker": "XPEV", "primary_exchange": "NYSE"},
    "Yazaki": {"listed_status": "private", "primary_ticker": "", "primary_exchange": ""},
    "ZF Friedrichshafen": {"listed_status": "private", "primary_ticker": "", "primary_exchange": ""},
}


def listing_from_metadata(company_name: str) -> dict[str, str]:
    override = LISTING_OVERRIDES.get(company_name)
    if override:
        return override

    ticker = FIRM_METADATA.get(company_name, {}).get("ticker_or_parent", "")
    if not ticker or ticker.lower() in {"private", "private / foundation owned"}:
        return {"listed_status": "private_or_unknown", "primary_ticker": "", "primary_exchange": ""}

    if any(marker in ticker for marker in [".", ";"]) or ticker.isupper():
        primary_ticker = ticker.split(";")[0].strip()
        return {"listed_status": "listed_or_listed_parent", "primary_ticker": primary_ticker, "primary_exchange": ""}

    return {"listed_status": "private_or_unknown", "primary_ticker": "", "primary_exchange": ""}


def build_listed_company_sheets(index_path: Path, dashboard_path: Path) -> dict[str, pd.DataFrame]:
    js_data = _extract_js_data(index_path)
    dashboard_data = _extract_dashboard_data(dashboard_path) if dashboard_path.exists() else {}
    relationships, evidence, _model_exposure = build_company_relationships(js_data, dashboard_data)
    company_master = build_company_master(relationships, evidence)

    listing_rows = []
    for _, row in company_master.iterrows():
        listing = listing_from_metadata(row["company_name"])
        listing_rows.append({**row.to_dict(), **listing})

    all_companies = pd.DataFrame(listing_rows)
    listed = all_companies[
        all_companies["listed_status"].isin(["listed", "subsidiary of listed company", "listed_or_listed_parent"])
    ].copy()

    listed_relationships = relationships[relationships["company_name"].isin(listed["company_name"])].copy()
    listed_evidence = evidence[evidence["company_name"].isin(listed["company_name"])].copy()

    summary = pd.DataFrame(
        [
            {"metric": "All companies identified", "value": int(len(all_companies))},
            {"metric": "Listed companies / listed parents", "value": int(len(listed))},
            {"metric": "Listed company relationships", "value": int(len(listed_relationships))},
            {
                "metric": "Listed companies exposed to high or critical tier nodes",
                "value": int(listed["highest_exposed_risk"].isin(["high", "critical"]).sum()),
            },
            {"metric": "Private or unknown companies", "value": int((all_companies["listed_status"] == "private_or_unknown").sum())},
        ]
    )

    by_exchange = (
        listed.groupby("primary_exchange", dropna=False)
        .size()
        .reset_index(name="value")
        .rename(columns={"primary_exchange": "metric"})
    )
    by_exchange["metric"] = "Primary exchange - " + by_exchange["metric"].replace("", "unspecified").astype(str)
    summary = pd.concat([summary, by_exchange], ignore_index=True)

    notes = pd.DataFrame(
        [
            {"field": "generated_at_utc", "value": datetime.now(timezone.utc).isoformat()},
            {"field": "index_html_path", "value": str(index_path)},
            {"field": "dashboard_html_path", "value": str(dashboard_path) if dashboard_path.exists() else ""},
            {
                "field": "method",
                "value": "Companies extracted from EV supply-chain HTML files, then matched against a curated public-listing map inside this script.",
            },
            {
                "field": "caution",
                "value": "Listing status and tickers can change. Treat this as a reproducible research register, not live financial data.",
            },
        ]
    )

    return {
        "summary": summary,
        "listed_companies": listed.sort_values(["relationship_count", "company_name"], ascending=[False, True]),
        "listed_relationships": listed_relationships,
        "listed_evidence": listed_evidence,
        "all_companies_listing_status": all_companies.sort_values("company_name"),
        "notes": notes,
    }


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
    parser = argparse.ArgumentParser(description="Identify listed companies in the EV supply chain.")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX, help=f"Path to index.html. Default: {DEFAULT_INDEX}")
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD, help=f"Path to dashboard.html. Default: {DEFAULT_DASHBOARD}")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help=f"Output workbook. Default: {DEFAULT_OUTPUT}")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sheets = build_listed_company_sheets(args.index.resolve(), args.dashboard.resolve())
    write_workbook(sheets, args.output.resolve())
    print(f"Wrote {args.output.resolve()}")
    print(sheets["summary"].to_string(index=False))


if __name__ == "__main__":
    main()
