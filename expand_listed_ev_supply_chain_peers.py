"""
Find additional similar listed companies for each EV supply-chain tier.

The script uses the listed companies already identified from index.html and
dashboard.html as seed firms, then adds a curated peer set by tier. It can also
validate candidate tickers with Yahoo Finance's quote endpoint.

Usage
-----
  python expand_listed_ev_supply_chain_peers.py
  python expand_listed_ev_supply_chain_peers.py --skip-validation
  python expand_listed_ev_supply_chain_peers.py --output expanded_listed_ev_supply_chain_peers.xlsx
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd

from identify_listed_ev_supply_chain_companies import DEFAULT_DASHBOARD, DEFAULT_INDEX, build_listed_company_sheets
from map_listed_company_tiers_and_financials import (
    FOUR_TIER_LABELS,
    build_four_tier_mapping,
    yahoo_symbol,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "expanded_listed_ev_supply_chain_peers.xlsx"


PEER_CANDIDATES = [
    # Tier 1 - raw and processed materials
    {"company_name": "Albemarle", "primary_ticker": "ALB", "primary_exchange": "NYSE", "home_country": "United States", "four_tier": FOUR_TIER_LABELS["tier_1"], "supply_chain_role": "Lithium chemicals", "similar_seed_companies": "CATL; BYD; LG Energy Solution", "rationale": "Major listed lithium supplier for battery cathode supply chains."},
    {"company_name": "SQM", "primary_ticker": "SQM", "primary_exchange": "NYSE", "home_country": "Chile", "four_tier": FOUR_TIER_LABELS["tier_1"], "supply_chain_role": "Lithium chemicals", "similar_seed_companies": "Albemarle", "rationale": "Large lithium carbonate/hydroxide producer."},
    {"company_name": "Ganfeng Lithium", "primary_ticker": "002460.SZ", "primary_exchange": "Shenzhen Stock Exchange", "home_country": "China", "four_tier": FOUR_TIER_LABELS["tier_1"], "supply_chain_role": "Lithium compounds", "similar_seed_companies": "CATL; BYD", "rationale": "Chinese listed lithium processor linked to battery supply chains."},
    {"company_name": "Tianqi Lithium", "primary_ticker": "002466.SZ", "primary_exchange": "Shenzhen Stock Exchange", "home_country": "China", "four_tier": FOUR_TIER_LABELS["tier_1"], "supply_chain_role": "Lithium compounds", "similar_seed_companies": "Ganfeng Lithium", "rationale": "Chinese listed lithium producer and processor."},
    {"company_name": "Pilbara Minerals", "primary_ticker": "PLS.AX", "primary_exchange": "ASX", "home_country": "Australia", "four_tier": FOUR_TIER_LABELS["tier_1"], "supply_chain_role": "Lithium mining", "similar_seed_companies": "Albemarle; SQM", "rationale": "Listed hard-rock lithium miner feeding cathode supply chains."},
    {"company_name": "Mineral Resources", "primary_ticker": "MIN.AX", "primary_exchange": "ASX", "home_country": "Australia", "four_tier": FOUR_TIER_LABELS["tier_1"], "supply_chain_role": "Lithium and mining services", "similar_seed_companies": "Pilbara Minerals", "rationale": "Listed Australian lithium producer."},
    {"company_name": "Glencore", "primary_ticker": "GLEN.L", "primary_exchange": "London Stock Exchange", "home_country": "Switzerland / United Kingdom", "four_tier": FOUR_TIER_LABELS["tier_1"], "supply_chain_role": "Cobalt, nickel, copper", "similar_seed_companies": "Umicore; Sumitomo Electric", "rationale": "Major listed supplier of cobalt, nickel, and copper inputs."},
    {"company_name": "CMOC Group", "primary_ticker": "3993.HK", "primary_exchange": "Hong Kong Stock Exchange", "home_country": "China", "four_tier": FOUR_TIER_LABELS["tier_1"], "supply_chain_role": "Cobalt and copper mining", "similar_seed_companies": "Glencore", "rationale": "Large listed cobalt/copper producer with DRC exposure."},
    {"company_name": "MP Materials", "primary_ticker": "MP", "primary_exchange": "NYSE", "home_country": "United States", "four_tier": FOUR_TIER_LABELS["tier_1"], "supply_chain_role": "Rare earth mining and magnets", "similar_seed_companies": "China Northern Rare Earth", "rationale": "Listed rare earth supplier relevant to PMSM motor supply chains."},
    {"company_name": "Lynas Rare Earths", "primary_ticker": "LYC.AX", "primary_exchange": "ASX", "home_country": "Australia", "four_tier": FOUR_TIER_LABELS["tier_1"], "supply_chain_role": "Rare earth processing", "similar_seed_companies": "MP Materials", "rationale": "Listed non-China rare earth supplier."},
    {"company_name": "Umicore", "primary_ticker": "UMI.BR", "primary_exchange": "Euronext Brussels", "home_country": "Belgium", "four_tier": FOUR_TIER_LABELS["tier_1"], "supply_chain_role": "Cathode materials and recycling", "similar_seed_companies": "L&F; POSCO Holdings", "rationale": "Listed cathode-material and battery recycling peer."},
    {"company_name": "POSCO Holdings", "primary_ticker": "005490.KS", "primary_exchange": "Korea Exchange", "home_country": "South Korea", "four_tier": FOUR_TIER_LABELS["tier_1"], "supply_chain_role": "Cathode/anode materials and lithium", "similar_seed_companies": "L&F; LG Energy Solution", "rationale": "Listed Korean materials group active in battery materials."},
    {"company_name": "Syrah Resources", "primary_ticker": "SYR.AX", "primary_exchange": "ASX", "home_country": "Australia", "four_tier": FOUR_TIER_LABELS["tier_1"], "supply_chain_role": "Natural graphite and anode material", "similar_seed_companies": "CATL; BYD", "rationale": "Listed graphite supplier for anode supply chains."},

    # Tier 2 - core components
    {"company_name": "EVE Energy", "primary_ticker": "300014.SZ", "primary_exchange": "Shenzhen Stock Exchange", "home_country": "China", "four_tier": FOUR_TIER_LABELS["tier_2"], "supply_chain_role": "Battery cells", "similar_seed_companies": "CATL; BYD; CALB", "rationale": "Listed Chinese battery-cell peer."},
    {"company_name": "Gotion High-Tech", "primary_ticker": "002074.SZ", "primary_exchange": "Shenzhen Stock Exchange", "home_country": "China", "four_tier": FOUR_TIER_LABELS["tier_2"], "supply_chain_role": "Battery cells and packs", "similar_seed_companies": "CATL; CALB", "rationale": "Listed battery cell and pack manufacturer."},
    {"company_name": "Farasis Energy", "primary_ticker": "688567.SS", "primary_exchange": "Shanghai Stock Exchange", "home_country": "China", "four_tier": FOUR_TIER_LABELS["tier_2"], "supply_chain_role": "Battery cells", "similar_seed_companies": "CATL; CALB", "rationale": "Listed Chinese EV battery-cell supplier."},
    {"company_name": "Sunwoda Electronic", "primary_ticker": "300207.SZ", "primary_exchange": "Shenzhen Stock Exchange", "home_country": "China", "four_tier": FOUR_TIER_LABELS["tier_2"], "supply_chain_role": "Battery packs and cells", "similar_seed_companies": "CATL; BYD", "rationale": "Listed battery systems peer."},
    {"company_name": "Microchip Technology", "primary_ticker": "MCHP", "primary_exchange": "NASDAQ", "home_country": "United States", "four_tier": FOUR_TIER_LABELS["tier_2"], "supply_chain_role": "Automotive MCUs", "similar_seed_companies": "NXP Semiconductors; Texas Instruments; Renesas Electronics", "rationale": "Listed automotive microcontroller peer."},
    {"company_name": "Rohm", "primary_ticker": "6963.T", "primary_exchange": "Tokyo Stock Exchange", "home_country": "Japan", "four_tier": FOUR_TIER_LABELS["tier_2"], "supply_chain_role": "SiC power semiconductors", "similar_seed_companies": "Infineon Technologies; ON Semiconductor; STMicroelectronics", "rationale": "Listed SiC and automotive semiconductor supplier."},
    {"company_name": "Fuji Electric", "primary_ticker": "6504.T", "primary_exchange": "Tokyo Stock Exchange", "home_country": "Japan", "four_tier": FOUR_TIER_LABELS["tier_2"], "supply_chain_role": "Power semiconductors", "similar_seed_companies": "Infineon Technologies; Mitsubishi Electric", "rationale": "Listed power-electronics component supplier."},
    {"company_name": "Mitsubishi Electric", "primary_ticker": "6503.T", "primary_exchange": "Tokyo Stock Exchange", "home_country": "Japan", "four_tier": FOUR_TIER_LABELS["tier_2"], "supply_chain_role": "Power semiconductors and traction components", "similar_seed_companies": "Denso; Infineon Technologies", "rationale": "Listed EV power semiconductor and drive component peer."},
    {"company_name": "Kyocera", "primary_ticker": "6971.T", "primary_exchange": "Tokyo Stock Exchange", "home_country": "Japan", "four_tier": FOUR_TIER_LABELS["tier_2"], "supply_chain_role": "Passive and ceramic components", "similar_seed_companies": "Murata Manufacturing; TDK; Taiyo Yuden", "rationale": "Listed passive component peer."},
    {"company_name": "Yageo", "primary_ticker": "2327.TW", "primary_exchange": "Taiwan Stock Exchange", "home_country": "Taiwan", "four_tier": FOUR_TIER_LABELS["tier_2"], "supply_chain_role": "Passive components", "similar_seed_companies": "TDK; Murata Manufacturing", "rationale": "Listed passive component supplier."},
    {"company_name": "Melexis", "primary_ticker": "MELE.BR", "primary_exchange": "Euronext Brussels", "home_country": "Belgium", "four_tier": FOUR_TIER_LABELS["tier_2"], "supply_chain_role": "Automotive sensors", "similar_seed_companies": "Sensata Technologies; NXP Semiconductors", "rationale": "Listed automotive sensor peer."},

    # Tier 3 - subsystems and Tier-1 integration
    {"company_name": "Magna International", "primary_ticker": "MGA", "primary_exchange": "NYSE", "home_country": "Canada", "four_tier": FOUR_TIER_LABELS["tier_3"], "supply_chain_role": "eDrive, body, and vehicle systems", "similar_seed_companies": "BorgWarner; Aptiv; Denso", "rationale": "Large listed Tier-1 supplier active in EV systems."},
    {"company_name": "Schaeffler", "primary_ticker": "SHA0.DE", "primary_exchange": "Xetra", "home_country": "Germany", "four_tier": FOUR_TIER_LABELS["tier_3"], "supply_chain_role": "eAxle and motion technology", "similar_seed_companies": "BorgWarner; ZF Friedrichshafen", "rationale": "Listed drivetrain and e-mobility systems peer."},
    {"company_name": "Forvia", "primary_ticker": "FRVIA.PA", "primary_exchange": "Euronext Paris", "home_country": "France", "four_tier": FOUR_TIER_LABELS["tier_3"], "supply_chain_role": "Vehicle systems and electronics", "similar_seed_companies": "Valeo; Continental", "rationale": "Listed Tier-1 systems supplier."},
    {"company_name": "Autoliv", "primary_ticker": "ALV", "primary_exchange": "NYSE", "home_country": "Sweden", "four_tier": FOUR_TIER_LABELS["tier_3"], "supply_chain_role": "Safety systems", "similar_seed_companies": "Aptiv; Sensata Technologies", "rationale": "Listed safety systems supplier relevant to vehicle integration."},
    {"company_name": "Garrett Motion", "primary_ticker": "GTX", "primary_exchange": "NASDAQ", "home_country": "Switzerland", "four_tier": FOUR_TIER_LABELS["tier_3"], "supply_chain_role": "Electric boosting and powertrain systems", "similar_seed_companies": "BorgWarner", "rationale": "Listed electrified powertrain systems peer."},
    {"company_name": "Gentex", "primary_ticker": "GNTX", "primary_exchange": "NASDAQ", "home_country": "United States", "four_tier": FOUR_TIER_LABELS["tier_3"], "supply_chain_role": "ADAS mirrors and sensing systems", "similar_seed_companies": "Mobileye; Luminar Technologies", "rationale": "Listed automotive sensing and electronics peer."},
    {"company_name": "Ambarella", "primary_ticker": "AMBA", "primary_exchange": "NASDAQ", "home_country": "United States", "four_tier": FOUR_TIER_LABELS["tier_3"], "supply_chain_role": "ADAS vision processors", "similar_seed_companies": "Mobileye; NVIDIA", "rationale": "Listed edge AI/vision semiconductor peer for ADAS systems."},
    {"company_name": "Hella", "primary_ticker": "HLE.DE", "primary_exchange": "Xetra", "home_country": "Germany", "four_tier": FOUR_TIER_LABELS["tier_3"], "supply_chain_role": "Lighting and electronics systems", "similar_seed_companies": "Forvia; Valeo", "rationale": "Listed/parent-controlled automotive electronics systems supplier."},

    # Tier 4 - OEM assembly
    {"company_name": "General Motors", "primary_ticker": "GM", "primary_exchange": "NYSE", "home_country": "United States", "four_tier": FOUR_TIER_LABELS["tier_4"], "supply_chain_role": "Vehicle OEM", "similar_seed_companies": "Tesla; SAIC", "rationale": "Listed EV OEM peer with Ultium platform."},
    {"company_name": "Ford Motor", "primary_ticker": "F", "primary_exchange": "NYSE", "home_country": "United States", "four_tier": FOUR_TIER_LABELS["tier_4"], "supply_chain_role": "Vehicle OEM", "similar_seed_companies": "Tesla; General Motors", "rationale": "Listed EV OEM peer with electric pickup and van platforms."},
    {"company_name": "Rivian", "primary_ticker": "RIVN", "primary_exchange": "NASDAQ", "home_country": "United States", "four_tier": FOUR_TIER_LABELS["tier_4"], "supply_chain_role": "EV OEM", "similar_seed_companies": "Tesla; NIO; XPeng", "rationale": "Listed pure-play EV manufacturer."},
    {"company_name": "Lucid Group", "primary_ticker": "LCID", "primary_exchange": "NASDAQ", "home_country": "United States", "four_tier": FOUR_TIER_LABELS["tier_4"], "supply_chain_role": "EV OEM", "similar_seed_companies": "Tesla; NIO", "rationale": "Listed premium EV manufacturer."},
    {"company_name": "Volkswagen", "primary_ticker": "VOW3.DE", "primary_exchange": "Xetra", "home_country": "Germany", "four_tier": FOUR_TIER_LABELS["tier_4"], "supply_chain_role": "Vehicle OEM", "similar_seed_companies": "BMW; Mercedes-Benz; SAIC", "rationale": "Listed global OEM with major EV platform investment."},
    {"company_name": "BMW", "primary_ticker": "BMW.DE", "primary_exchange": "Xetra", "home_country": "Germany", "four_tier": FOUR_TIER_LABELS["tier_4"], "supply_chain_role": "Vehicle OEM", "similar_seed_companies": "Volkswagen; Mercedes-Benz", "rationale": "Listed premium OEM with EV production."},
    {"company_name": "Mercedes-Benz Group", "primary_ticker": "MBG.DE", "primary_exchange": "Xetra", "home_country": "Germany", "four_tier": FOUR_TIER_LABELS["tier_4"], "supply_chain_role": "Vehicle OEM", "similar_seed_companies": "BMW; Volkswagen", "rationale": "Listed premium OEM with EV portfolio."},
    {"company_name": "Stellantis", "primary_ticker": "STLA", "primary_exchange": "NYSE / Euronext Milan / Euronext Paris", "home_country": "Netherlands", "four_tier": FOUR_TIER_LABELS["tier_4"], "supply_chain_role": "Vehicle OEM", "similar_seed_companies": "Volkswagen; Ford Motor", "rationale": "Listed multi-brand OEM with EV vans and passenger vehicles."},
    {"company_name": "Hyundai Motor", "primary_ticker": "005380.KS", "primary_exchange": "Korea Exchange", "home_country": "South Korea", "four_tier": FOUR_TIER_LABELS["tier_4"], "supply_chain_role": "Vehicle OEM", "similar_seed_companies": "Kia; Toyota", "rationale": "Listed OEM with E-GMP EV platform."},
    {"company_name": "Kia", "primary_ticker": "000270.KS", "primary_exchange": "Korea Exchange", "home_country": "South Korea", "four_tier": FOUR_TIER_LABELS["tier_4"], "supply_chain_role": "Vehicle OEM", "similar_seed_companies": "Hyundai Motor", "rationale": "Listed EV OEM within Hyundai Motor Group."},
    {"company_name": "Toyota Motor", "primary_ticker": "7203.T", "primary_exchange": "Tokyo Stock Exchange", "home_country": "Japan", "four_tier": FOUR_TIER_LABELS["tier_4"], "supply_chain_role": "Vehicle OEM", "similar_seed_companies": "Honda; Nissan; Hyundai Motor", "rationale": "Listed global OEM with BEV and hybrid electrification strategy."},
    {"company_name": "Honda Motor", "primary_ticker": "7267.T", "primary_exchange": "Tokyo Stock Exchange", "home_country": "Japan", "four_tier": FOUR_TIER_LABELS["tier_4"], "supply_chain_role": "Vehicle OEM", "similar_seed_companies": "Toyota Motor; Nissan", "rationale": "Listed OEM scaling EV programmes."},
    {"company_name": "Nissan Motor", "primary_ticker": "7201.T", "primary_exchange": "Tokyo Stock Exchange", "home_country": "Japan", "four_tier": FOUR_TIER_LABELS["tier_4"], "supply_chain_role": "Vehicle OEM", "similar_seed_companies": "Toyota Motor; Honda Motor", "rationale": "Listed OEM with LEAF/Ariya EV platforms."},
    {"company_name": "Leapmotor", "primary_ticker": "9863.HK", "primary_exchange": "Hong Kong Stock Exchange", "home_country": "China", "four_tier": FOUR_TIER_LABELS["tier_4"], "supply_chain_role": "EV OEM", "similar_seed_companies": "NIO; XPeng; Li Auto", "rationale": "Listed Chinese EV/EREV manufacturer."},
]


def validate_quote(symbol: str, pause_seconds: float = 0.15) -> dict[str, Any]:
    yf_symbol = yahoo_symbol(symbol)
    if not yf_symbol:
        return {"validation_status": "missing ticker"}
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(yf_symbol)}?range=5d&interval=1d"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return {"validation_status": f"HTTP {exc.code}"}
    except URLError as exc:
        return {"validation_status": f"network error: {exc.reason}"}
    except TimeoutError:
        return {"validation_status": "timeout"}
    finally:
        time.sleep(pause_seconds)

    result = payload.get("chart", {}).get("result", [])
    if not result:
        return {"validation_status": "not found", "yahoo_symbol": yf_symbol}
    meta = result[0].get("meta", {})
    return {
        "validation_status": "ok",
        "yahoo_symbol": yf_symbol,
        "yahoo_short_name": meta.get("shortName") or meta.get("longName"),
        "quote_type": meta.get("instrumentType"),
        "yahoo_exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or meta.get("exchange"),
        "currency": meta.get("currency"),
        "market_cap": "",
        "regular_market_price": meta.get("regularMarketPrice"),
    }


def build_seed_sheets(index_path: Path, dashboard_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    listed_sheets = build_listed_company_sheets(index_path, dashboard_path)
    seed_companies, seed_relationships = build_four_tier_mapping(
        listed_sheets["listed_companies"],
        listed_sheets["listed_relationships"],
    )
    seed_companies["record_type"] = "seed_from_html"
    return seed_companies, seed_relationships


def expand_peers(seed_companies: pd.DataFrame, skip_validation: bool) -> pd.DataFrame:
    peers = pd.DataFrame(PEER_CANDIDATES)
    seed_names = set(seed_companies["company_name"])
    peers["already_in_seed_list"] = peers["company_name"].isin(seed_names)
    peers["record_type"] = "expanded_peer_candidate"

    if skip_validation:
        peers["validation_status"] = "skipped"
        return peers

    validations = [validate_quote(str(row["primary_ticker"])) for _, row in peers.iterrows()]
    return pd.concat([peers.reset_index(drop=True), pd.DataFrame(validations)], axis=1)


def build_expanded_map(seed_companies: pd.DataFrame, peers: pd.DataFrame) -> pd.DataFrame:
    seed_cols = [
        "company_name",
        "primary_ticker",
        "primary_exchange",
        "home_country",
        "primary_four_tier",
        "all_four_tiers",
        "record_type",
    ]
    seeds = seed_companies[seed_cols].copy()
    seeds = seeds.rename(columns={"primary_four_tier": "four_tier"})
    seeds["supply_chain_role"] = seeds.get("firm_type", "")
    seeds["rationale"] = "Original listed company identified from index.html/dashboard.html."
    seeds["similar_seed_companies"] = ""
    seeds["already_in_seed_list"] = True

    peer_cols = [
        "company_name",
        "primary_ticker",
        "primary_exchange",
        "home_country",
        "four_tier",
        "record_type",
        "supply_chain_role",
        "rationale",
        "similar_seed_companies",
        "already_in_seed_list",
    ]
    expanded = pd.concat([seeds[peer_cols], peers[peer_cols]], ignore_index=True)
    expanded = expanded.drop_duplicates(subset=["company_name", "primary_ticker"], keep="first")
    return expanded.sort_values(["four_tier", "record_type", "company_name"])


def build_summary(seed_companies: pd.DataFrame, peers: pd.DataFrame, expanded: pd.DataFrame) -> pd.DataFrame:
    rows = [
        {"metric": "Seed listed companies", "value": int(len(seed_companies))},
        {"metric": "Peer candidates added", "value": int(len(peers))},
        {"metric": "Peer candidates not already in seed list", "value": int((~peers["already_in_seed_list"]).sum())},
        {"metric": "Expanded unique listed companies", "value": int(len(expanded))},
    ]
    if "validation_status" in peers.columns:
        rows.append({"metric": "Peer candidates validated ok", "value": int((peers["validation_status"] == "ok").sum())})
    tier_counts = expanded.groupby("four_tier").size().reset_index(name="value").rename(columns={"four_tier": "metric"})
    tier_counts["metric"] = "Expanded companies - " + tier_counts["metric"].astype(str)
    return pd.concat([pd.DataFrame(rows), tier_counts], ignore_index=True)


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
    parser = argparse.ArgumentParser(description="Expand listed EV supply-chain companies by tier using seed companies.")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX, help=f"Path to index.html. Default: {DEFAULT_INDEX}")
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD, help=f"Path to dashboard.html. Default: {DEFAULT_DASHBOARD}")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help=f"Output workbook. Default: {DEFAULT_OUTPUT}")
    parser.add_argument("--skip-validation", action="store_true", help="Do not validate ticker symbols through Yahoo Finance.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    seed_companies, seed_relationships = build_seed_sheets(args.index.resolve(), args.dashboard.resolve())
    peers = expand_peers(seed_companies, args.skip_validation)
    expanded = build_expanded_map(seed_companies, peers)
    notes = pd.DataFrame(
        [
            {"field": "generated_at_utc", "value": datetime.now(timezone.utc).isoformat()},
            {"field": "method", "value": "Seed companies come from index.html/dashboard.html. Peer candidates are a curated listed-company expansion by analogous supply-chain role and four-tier position."},
            {"field": "caution", "value": "Peer list is intentionally broader than the original dashboard and should be reviewed before use in formal empirical analysis."},
        ]
    )
    sheets = {
        "summary": build_summary(seed_companies, peers, expanded),
        "seed_listed_companies": seed_companies,
        "seed_tier_relationships": seed_relationships,
        "peer_candidates": peers,
        "expanded_company_tier_map": expanded,
        "notes": notes,
    }
    write_workbook(sheets, args.output.resolve())
    print(f"Wrote {args.output.resolve()}")
    print(sheets["summary"].to_string(index=False))


if __name__ == "__main__":
    main()
