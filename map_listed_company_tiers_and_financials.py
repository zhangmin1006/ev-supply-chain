"""
Map listed EV supply-chain companies to the four-tier model and collect
five years of financial performance.

Four-tier model used here
-------------------------
  1. Raw & Processed Materials
  2. Core Components
  3. Subsystems / Tier-1 Integration
  4. OEM Assembly & Vehicle Integration

The script reuses the listed-company register generated from index.html and
dashboard.html, then fetches annual financial statement items from Yahoo
Finance's public fundamentals-timeseries endpoint.

Usage
-----
  python map_listed_company_tiers_and_financials.py
  python map_listed_company_tiers_and_financials.py --skip-financials
  python map_listed_company_tiers_and_financials.py --years 5 --output listed_company_tiers_financials.xlsx
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import pandas as pd

from identify_listed_ev_supply_chain_companies import (
    DEFAULT_DASHBOARD,
    DEFAULT_INDEX,
    build_listed_company_sheets,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "listed_company_tiers_financials.xlsx"

FOUR_TIER_LABELS = {
    "tier_1": "Tier 1 - Raw & Processed Materials",
    "tier_2": "Tier 2 - Core Components",
    "tier_3": "Tier 3 - Subsystems / Tier-1 Integration",
    "tier_4": "Tier 4 - OEM Assembly & Vehicle Integration",
}

FINANCIAL_TYPES = [
    "annualTotalRevenue",
    "annualGrossProfit",
    "annualOperatingIncome",
    "annualNetIncome",
    "annualEBITDA",
    "annualTotalAssets",
    "annualTotalDebt",
    "annualOperatingCashFlow",
    "annualFreeCashFlow",
    "annualCapitalExpenditure",
]

METRIC_NAMES = {
    "annualTotalRevenue": "Revenue",
    "annualGrossProfit": "Gross profit",
    "annualOperatingIncome": "Operating income",
    "annualNetIncome": "Net income",
    "annualEBITDA": "EBITDA",
    "annualTotalAssets": "Total assets",
    "annualTotalDebt": "Total debt",
    "annualOperatingCashFlow": "Operating cash flow",
    "annualFreeCashFlow": "Free cash flow",
    "annualCapitalExpenditure": "Capital expenditure",
}

YAHOO_SYMBOL_OVERRIDES = {
    "BRE": "BRE.MI",
    "FR.PA": "FR.PA",
    "002594.SZ": "002594.SZ",
    "300750.SZ": "300750.SZ",
    "002050.SZ": "002050.SZ",
    "600104.SS": "600104.SS",
    "0175.HK": "0175.HK",
    "3931.HK": "3931.HK",
}


def yahoo_symbol(ticker: str) -> str:
    ticker = str(ticker or "").strip()
    return YAHOO_SYMBOL_OVERRIDES.get(ticker, ticker)


def four_tier_from_relationship(row: pd.Series) -> str:
    relationship_type = str(row.get("relationship_type", ""))
    tier_label = str(row.get("tier_label", ""))
    tier_index = row.get("tier_index", "")
    category = str(row.get("category", ""))
    node_id = str(row.get("node_id", ""))

    if relationship_type == "model_cell_supplier_to_oem":
        return FOUR_TIER_LABELS["tier_4"]
    if relationship_type == "model_cell_maker":
        return FOUR_TIER_LABELS["tier_2"]

    try:
        tier_number = int(float(tier_index))
    except (TypeError, ValueError):
        tier_number = None

    if tier_number in {0, 1} or "Material Processing" in tier_label:
        return FOUR_TIER_LABELS["tier_1"]
    if tier_number == 2 or "Component Mfg" in tier_label:
        return FOUR_TIER_LABELS["tier_2"]
    if tier_number == 3 or "Sub-Systems" in tier_label:
        return FOUR_TIER_LABELS["tier_3"]
    if tier_number == 4 or "OEM" in tier_label or node_id.startswith("oem_"):
        return FOUR_TIER_LABELS["tier_4"]

    if category in {"battery", "electronics", "powertrain", "charging", "thermal", "software", "chassis"}:
        return FOUR_TIER_LABELS["tier_2"]

    return "Unmapped"


def build_four_tier_mapping(listed_companies: pd.DataFrame, listed_relationships: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    tier_relationships = listed_relationships.copy()
    tier_relationships["four_tier"] = tier_relationships.apply(four_tier_from_relationship, axis=1)

    rows: list[dict[str, Any]] = []
    for _, company in listed_companies.iterrows():
        rels = tier_relationships[tier_relationships["company_name"] == company["company_name"]]
        tiers = sorted(set(rels["four_tier"]) - {"Unmapped"})
        tier_counts = rels["four_tier"].value_counts().to_dict()
        primary_tier = tiers[0] if len(tiers) == 1 else max(tiers, key=lambda tier: tier_counts.get(tier, 0), default="")

        row = company.to_dict()
        row.update(
            {
                "primary_four_tier": primary_tier,
                "all_four_tiers": "; ".join(tiers),
                "tier_1_relationships": int(tier_counts.get(FOUR_TIER_LABELS["tier_1"], 0)),
                "tier_2_relationships": int(tier_counts.get(FOUR_TIER_LABELS["tier_2"], 0)),
                "tier_3_relationships": int(tier_counts.get(FOUR_TIER_LABELS["tier_3"], 0)),
                "tier_4_relationships": int(tier_counts.get(FOUR_TIER_LABELS["tier_4"], 0)),
            }
        )
        rows.append(row)

    return pd.DataFrame(rows), tier_relationships


def fetch_yahoo_fundamentals(symbol: str, years: int, pause_seconds: float = 0.2) -> tuple[pd.DataFrame, str]:
    yf_symbol = yahoo_symbol(symbol)
    if not yf_symbol:
        return pd.DataFrame(), "missing ticker"

    period2 = int(datetime.now(timezone.utc).timestamp())
    period1 = int((datetime.now(timezone.utc) - timedelta(days=365 * (years + 2))).timestamp())
    params = {
        "symbol": yf_symbol,
        "type": ",".join(FINANCIAL_TYPES),
        "period1": str(period1),
        "period2": str(period2),
    }
    url = f"https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{quote(yf_symbol)}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})

    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return pd.DataFrame(), f"HTTP {exc.code}"
    except URLError as exc:
        return pd.DataFrame(), f"network error: {exc.reason}"
    except TimeoutError:
        return pd.DataFrame(), "timeout"
    finally:
        time.sleep(pause_seconds)

    result = payload.get("timeseries", {}).get("result", [])
    records: list[dict[str, Any]] = []
    for item in result:
        metric_type = item.get("meta", {}).get("type", [""])[0]
        for point in item.get(metric_type, []):
            as_of = point.get("asOfDate")
            raw_value = point.get("reportedValue", {}).get("raw")
            currency = point.get("currencyCode")
            if as_of and raw_value is not None:
                records.append(
                    {
                        "yahoo_symbol": yf_symbol,
                        "fiscal_year": int(as_of[:4]),
                        "as_of_date": as_of,
                        "metric_code": metric_type,
                        "metric": METRIC_NAMES.get(metric_type, metric_type),
                        "value": raw_value,
                        "currency": currency,
                        "source": "Yahoo Finance fundamentals-timeseries",
                    }
                )

    if not records:
        return pd.DataFrame(), "no financial statement records returned"

    df = pd.DataFrame(records)
    latest_years = sorted(df["fiscal_year"].unique(), reverse=True)[:years]
    return df[df["fiscal_year"].isin(latest_years)].sort_values(["fiscal_year", "metric"]), ""


def collect_financials(companies: pd.DataFrame, years: int, limit: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    financial_rows: list[pd.DataFrame] = []
    status_rows: list[dict[str, Any]] = []
    selected = companies.head(limit) if limit else companies

    for _, company in selected.iterrows():
        ticker = company.get("primary_ticker", "")
        company_name = company.get("company_name", "")
        df, error = fetch_yahoo_fundamentals(str(ticker), years)
        if not df.empty:
            df.insert(0, "primary_ticker", ticker)
            df.insert(0, "company_name", company_name)
            financial_rows.append(df)

        status_rows.append(
            {
                "company_name": company_name,
                "primary_ticker": ticker,
                "yahoo_symbol": yahoo_symbol(str(ticker)),
                "financial_records": int(len(df)),
                "status": "ok" if error == "" else error,
            }
        )

    financials = pd.concat(financial_rows, ignore_index=True) if financial_rows else pd.DataFrame()
    return financials, pd.DataFrame(status_rows)


def build_financial_pivot(financials: pd.DataFrame) -> pd.DataFrame:
    if financials.empty:
        return pd.DataFrame()
    pivot = financials.pivot_table(
        index=["company_name", "primary_ticker", "yahoo_symbol", "fiscal_year", "currency"],
        columns="metric",
        values="value",
        aggfunc="first",
    ).reset_index()
    pivot.columns.name = None
    return pivot


def build_summary(mapped_companies: pd.DataFrame, financial_status: pd.DataFrame) -> pd.DataFrame:
    rows = [
        {"metric": "Listed companies mapped", "value": int(len(mapped_companies))},
        {
            "metric": "Companies with Tier 1 exposure",
            "value": int((mapped_companies["tier_1_relationships"] > 0).sum()),
        },
        {
            "metric": "Companies with Tier 2 exposure",
            "value": int((mapped_companies["tier_2_relationships"] > 0).sum()),
        },
        {
            "metric": "Companies with Tier 3 exposure",
            "value": int((mapped_companies["tier_3_relationships"] > 0).sum()),
        },
        {
            "metric": "Companies with Tier 4 exposure",
            "value": int((mapped_companies["tier_4_relationships"] > 0).sum()),
        },
        {
            "metric": "Companies with financial records collected",
            "value": int((financial_status["financial_records"] > 0).sum()) if not financial_status.empty else 0,
        },
    ]
    return pd.DataFrame(rows)


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
    parser = argparse.ArgumentParser(description="Map listed EV supply-chain companies to four tiers and collect financials.")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX, help=f"Path to index.html. Default: {DEFAULT_INDEX}")
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD, help=f"Path to dashboard.html. Default: {DEFAULT_DASHBOARD}")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help=f"Output workbook. Default: {DEFAULT_OUTPUT}")
    parser.add_argument("--years", type=int, default=5, help="Number of fiscal years to collect. Default: 5")
    parser.add_argument("--skip-financials", action="store_true", help="Only build tier mapping; do not call Yahoo Finance.")
    parser.add_argument("--limit", type=int, default=None, help="Optional limit for testing financial collection.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    listed_sheets = build_listed_company_sheets(args.index.resolve(), args.dashboard.resolve())
    mapped_companies, tier_relationships = build_four_tier_mapping(
        listed_sheets["listed_companies"],
        listed_sheets["listed_relationships"],
    )

    if args.skip_financials:
        financials = pd.DataFrame()
        financial_pivot = pd.DataFrame()
        financial_status = pd.DataFrame(
            [
                {
                    "company_name": row["company_name"],
                    "primary_ticker": row["primary_ticker"],
                    "yahoo_symbol": yahoo_symbol(row["primary_ticker"]),
                    "financial_records": 0,
                    "status": "skipped",
                }
                for _, row in mapped_companies.iterrows()
            ]
        )
    else:
        financials, financial_status = collect_financials(mapped_companies, args.years, args.limit)
        financial_pivot = build_financial_pivot(financials)

    notes = pd.DataFrame(
        [
            {"field": "generated_at_utc", "value": datetime.now(timezone.utc).isoformat()},
            {"field": "four_tier_model", "value": "; ".join(FOUR_TIER_LABELS.values())},
            {
                "field": "financial_source",
                "value": "Yahoo Finance public fundamentals-timeseries endpoint; values are in reported company currency.",
            },
            {
                "field": "caution",
                "value": "Ticker mappings and financial data availability vary by exchange. Verify figures against annual reports before publication.",
            },
        ]
    )

    sheets = {
        "summary": build_summary(mapped_companies, financial_status),
        "listed_company_tier_map": mapped_companies,
        "tier_relationships": tier_relationships,
        "financials_long": financials,
        "financials_by_year": financial_pivot,
        "financial_fetch_status": financial_status,
        "notes": notes,
    }
    write_workbook(sheets, args.output.resolve())
    print(f"Wrote {args.output.resolve()}")
    print(sheets["summary"].to_string(index=False))


if __name__ == "__main__":
    main()
