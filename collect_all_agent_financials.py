"""
Collect Yahoo Finance financial data for all peer companies referenced in the
agent calibration map and write to all_peer_financials.xlsx.

The output workbook has the same "financials_by_year" sheet format as
listed_company_tiers_financials.xlsx, so financial_profiles.py reads it
transparently.  Run this script periodically to keep calibration data fresh.

Usage
-----
  python collect_all_agent_financials.py
  python collect_all_agent_financials.py --years 5
  python collect_all_agent_financials.py --agent-ids cell_catl t1_inverter
  python collect_all_agent_financials.py --companies "Tesla" "BYD" "CATL"
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

from model.financial_profiles import (
    AGENT_FINANCIAL_PEERS,
    AGENT_PEER_TICKERS,
    FOUR_TIER_AGENT_GROUPS,
    load_company_profiles,
    coverage_report,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "all_peer_financials.xlsx"

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
    "annualTotalRevenue":      "Revenue",
    "annualGrossProfit":       "Gross profit",
    "annualOperatingIncome":   "Operating income",
    "annualNetIncome":         "Net income",
    "annualEBITDA":            "EBITDA",
    "annualTotalAssets":       "Total assets",
    "annualTotalDebt":         "Total debt",
    "annualOperatingCashFlow": "Operating cash flow",
    "annualFreeCashFlow":      "Free cash flow",
    "annualCapitalExpenditure":"Capital expenditure",
}


def _fetch_yahoo_fundamentals(
    company_name: str,
    ticker: str,
    years: int,
    pause: float = 0.25,
) -> tuple[pd.DataFrame, str]:
    if not ticker:
        return pd.DataFrame(), "missing ticker"

    period2 = int(datetime.now(timezone.utc).timestamp())
    period1 = int((datetime.now(timezone.utc) - timedelta(days=365 * (years + 2))).timestamp())
    params  = {
        "symbol":  ticker,
        "type":    ",".join(FINANCIAL_TYPES),
        "period1": str(period1),
        "period2": str(period2),
    }
    url     = (
        f"https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/"
        f"timeseries/{quote(ticker)}?{urlencode(params)}"
    )
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})

    try:
        with urlopen(request, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        return pd.DataFrame(), f"HTTP {exc.code}"
    except URLError as exc:
        return pd.DataFrame(), f"network error: {exc.reason}"
    except TimeoutError:
        return pd.DataFrame(), "timeout"
    finally:
        time.sleep(pause)

    result  = payload.get("timeseries", {}).get("result", [])
    records: list[dict[str, Any]] = []
    for item in result:
        metric_type = item.get("meta", {}).get("type", [""])[0]
        for point in item.get(metric_type, []):
            as_of     = point.get("asOfDate")
            raw_value = point.get("reportedValue", {}).get("raw")
            currency  = point.get("currencyCode")
            if as_of and raw_value is not None:
                records.append({
                    "company_name": company_name,
                    "primary_ticker": ticker,
                    "yahoo_symbol":   ticker,
                    "fiscal_year":    int(as_of[:4]),
                    "as_of_date":     as_of,
                    "metric_code":    metric_type,
                    "metric":         METRIC_NAMES.get(metric_type, metric_type),
                    "value":          raw_value,
                    "currency":       currency,
                    "source":         "Yahoo Finance fundamentals-timeseries",
                })

    if not records:
        return pd.DataFrame(), "no financial records returned"

    df = pd.DataFrame(records)
    latest_years = sorted(df["fiscal_year"].unique(), reverse=True)[:years]
    return df[df["fiscal_year"].isin(latest_years)].sort_values(["fiscal_year", "metric"]), ""


def _build_pivot(financials: pd.DataFrame) -> pd.DataFrame:
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


def _build_tier_map() -> pd.DataFrame:
    """Return a DataFrame mapping each peer company to its model tier(s)."""
    rows: list[dict[str, Any]] = []
    for tier_label, agent_ids in FOUR_TIER_AGENT_GROUPS.items():
        for aid in agent_ids:
            for company in AGENT_FINANCIAL_PEERS.get(aid, ()):
                rows.append({
                    "company_name": company,
                    "agent_id":     aid,
                    "four_tier":    tier_label,
                    "ticker":       AGENT_PEER_TICKERS.get(company, ""),
                })
    return pd.DataFrame(rows).drop_duplicates()


def collect(
    company_ticker_pairs: list[tuple[str, str]],
    years: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch financials for a list of (company_name, ticker) pairs."""
    financial_dfs: list[pd.DataFrame] = []
    status_rows:   list[dict[str, Any]] = []

    total = len(company_ticker_pairs)
    for i, (name, ticker) in enumerate(company_ticker_pairs, 1):
        print(f"  [{i:>{len(str(total))}}/{total}] {name} ({ticker}) ...", end=" ", flush=True)
        df, error = _fetch_yahoo_fundamentals(name, ticker, years)
        status = "ok" if not error else error
        print(status)
        if not df.empty:
            financial_dfs.append(df)
        status_rows.append({
            "company_name":      name,
            "ticker":            ticker,
            "fiscal_years_collected": int(df["fiscal_year"].nunique()) if not df.empty else 0,
            "status":            status,
        })

    financials = pd.concat(financial_dfs, ignore_index=True) if financial_dfs else pd.DataFrame()
    return financials, pd.DataFrame(status_rows)


def write_workbook(
    financials: pd.DataFrame,
    pivot: pd.DataFrame,
    tier_map: pd.DataFrame,
    status: pd.DataFrame,
    output_path: Path,
) -> None:
    notes = pd.DataFrame([
        {"field": "generated_at_utc", "value": datetime.now(timezone.utc).isoformat()},
        {"field": "source", "value": "Yahoo Finance public fundamentals-timeseries endpoint"},
        {"field": "workbook_role",
         "value": (
             "Comprehensive peer-company financials for ABM agent calibration. "
             "model/financial_profiles.py reads this workbook (financials_by_year sheet) "
             "to compute recovery_multiplier, inventory_multiplier, growth_multiplier, "
             "and shock_absorption for each agent."
         )},
        {"field": "caution",
         "value": (
             "Values are in reported company currency. Multipliers are bounded ratios, "
             "not absolute financial comparisons. Re-run annually to refresh calibration."
         )},
    ])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for sheet_name, df in [
            ("financials_by_year", pivot),
            ("financials_long",    financials),
            ("agent_tier_map",     tier_map),
            ("fetch_status",       status),
            ("notes",              notes),
        ]:
            if df is not None and not df.empty:
                df.to_excel(writer, sheet_name=sheet_name[:31], index=False)

        for ws in writer.book.worksheets:
            ws.freeze_panes = "A2"
            for col_cells in ws.columns:
                max_len = max(len(str(c.value or "")) for c in col_cells)
                ws.column_dimensions[col_cells[0].column_letter].width = min(max(max_len + 2, 12), 70)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect Yahoo Finance financials for all EV supply-chain agent peer companies."
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help=f"Output workbook. Default: {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--years", type=int, default=5,
        help="Number of fiscal years to collect per company. Default: 5",
    )
    parser.add_argument(
        "--agent-ids", nargs="+", default=None,
        help="Only collect peers for these agent IDs (e.g. cell_catl t1_inverter).",
    )
    parser.add_argument(
        "--companies", nargs="+", default=None,
        help="Only collect these specific company names.",
    )
    parser.add_argument(
        "--coverage", action="store_true",
        help="Print coverage report for existing data without collecting.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.coverage:
        # Invalidate cache so we see the latest on-disk state
        load_company_profiles.cache_clear()
        print(coverage_report().to_string(index=False))
        return

    # Build the set of (company_name, ticker) pairs to collect
    if args.companies:
        pairs = [(name, AGENT_PEER_TICKERS.get(name, "")) for name in args.companies]
    elif args.agent_ids:
        companies: dict[str, str] = {}
        for aid in args.agent_ids:
            for cname in AGENT_FINANCIAL_PEERS.get(aid, ()):
                companies[cname] = AGENT_PEER_TICKERS.get(cname, "")
        pairs = list(companies.items())
    else:
        # All peer companies
        pairs = [(name, ticker) for name, ticker in AGENT_PEER_TICKERS.items()]

    # Filter out entries with no ticker
    valid   = [(n, t) for n, t in pairs if t]
    skipped = [(n, t) for n, t in pairs if not t]
    if skipped:
        print(f"Skipping {len(skipped)} companies with no ticker: {[n for n, _ in skipped]}")

    print(f"\nCollecting {args.years}-year financials for {len(valid)} companies …\n")
    financials, status = collect(valid, args.years)

    ok    = (status["status"] == "ok").sum()
    total = len(status)
    print(f"\n{ok}/{total} companies successfully collected.")

    pivot    = _build_pivot(financials)
    tier_map = _build_tier_map()

    write_workbook(financials, pivot, tier_map, status, args.output.resolve())
    print(f"\nWrote {args.output.resolve()}")

    # Print per-tier summary
    if not status.empty:
        tier_map_idx = tier_map.set_index("company_name")["four_tier"].to_dict()
        status["four_tier"] = status["company_name"].map(tier_map_idx).fillna("(other)")
        print("\nCollection summary by tier:")
        print(
            status.groupby("four_tier").agg(
                total=("company_name", "count"),
                ok=("status", lambda s: (s == "ok").sum()),
            ).to_string()
        )


if __name__ == "__main__":
    main()
