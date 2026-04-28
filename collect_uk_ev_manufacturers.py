"""
Collect UK EV manufacturer firm-level data into an Excel workbook.

The script builds a research-ready workbook from a curated UK EV manufacturer
register and, when available, enriches firm-level records from Companies House.

Usage
-----
  python collect_uk_ev_manufacturers.py
  python collect_uk_ev_manufacturers.py --output results/uk_ev_manufacturers.xlsx

Optional Companies House enrichment
-----------------------------------
Create an API key at https://developer.company-information.service.gov.uk/
then set it before running:

  PowerShell:  $env:COMPANIES_HOUSE_API_KEY="your-key"
  Bash:        export COMPANIES_HOUSE_API_KEY="your-key"
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


RESULTS_DIR = Path(__file__).resolve().parent / "results"
DEFAULT_OUTPUT = RESULTS_DIR / "uk_ev_manufacturers.xlsx"


SOURCES = [
    {
        "source_key": "gov_nissan_leaf_2025",
        "title": "Nissan launches next-generation LEAF in Sunderland",
        "publisher": "UK Government / Department for Business and Trade",
        "published": "2025-12-16",
        "url": "https://www.gov.uk/government/news/nissan-launches-450m-next-generation-leafin-major-vote-of-confidence-in-uks-industrial-strategy",
        "notes": "Confirms start of next-generation LEAF production at Sunderland and 6,000 supported jobs.",
    },
    {
        "source_key": "stellantis_ellesmere_2023",
        "title": "Stellantis announces start of EV production at Ellesmere Port",
        "publisher": "Stellantis",
        "published": "2023-09-07",
        "url": "https://o.media.stellantis.com/uk-en/corporate-communications/press/stellantis-stellantis-announces-start-of-electric-vehicle-production-at-ellesmere-port-the-uk-s-first-ev-only-manufacturing-plant",
        "notes": "Confirms Ellesmere Port as an EV-only plant and lists electric van models.",
    },
    {
        "source_key": "smmt_vehicle_output_2025",
        "title": "UK vehicle production by manufacturer, 2025",
        "publisher": "SMMT figures republished by Car Dealer Magazine",
        "published": "2026-02-01",
        "url": "https://cardealermagazine.co.uk/toughest-year-for-uk-vehicle-manufacturing-in-a-generation-as-production-slips-15/321665",
        "notes": "Provides 2024 and 2025 UK output by manufacturer/group.",
    },
    {
        "source_key": "companies_house_api",
        "title": "Companies House Public Data API",
        "publisher": "Companies House",
        "published": "",
        "url": "https://developer.company-information.service.gov.uk/get-started",
        "notes": "Optional live enrichment for company number, status, SIC codes, accounts dates, and registered office.",
    },
    {
        "source_key": "wired_jlr_halewood_2024",
        "title": "JLR Halewood electric vehicle factory transformation",
        "publisher": "Wired",
        "published": "2024-11-01",
        "url": "https://www.wired.com/story/jlr-jaguar-land-rover-electric-vehicle-factory-halewood",
        "notes": "Describes Halewood EV conversion, battery fitting automation, and pre-production timing.",
    },
    {
        "source_key": "wrightbus_zero_emission_2025",
        "title": "Wrightbus zero-emission bus production plans",
        "publisher": "The Times",
        "published": "2025-04-10",
        "url": "https://www.thetimes.co.uk/article/wrightbus-to-build-1000-zero-emission-buses-bffn35b60",
        "notes": "Reports Wrightbus zero-emission bus pipeline, employment growth, and R&D investment.",
    },
]


MANUFACTURERS = [
    {
        "company_name": "Nissan Motor Manufacturing (UK) Limited",
        "trading_name": "Nissan Sunderland",
        "segment": "Passenger cars",
        "uk_site": "Sunderland",
        "region": "North East England",
        "ev_role": "Vehicle assembly",
        "manufacturing_status": "Active EV production",
        "ev_models_or_products": "Nissan LEAF; EV36Zero future models",
        "parent_group": "Nissan Motor Co.",
        "ownership_country": "Japan",
        "estimated_uk_employment": 6000,
        "latest_public_output_units": 273322,
        "output_year": 2025,
        "output_metric": "All UK vehicle output by manufacturer/group",
        "performance_note": "2025 UK output down 3.1% versus 2024; next-generation LEAF production launched in Sunderland.",
        "source_keys": "gov_nissan_leaf_2025; smmt_vehicle_output_2025",
        "include_in_ev_manufacturer_count": True,
    },
    {
        "company_name": "Jaguar Land Rover Limited",
        "trading_name": "JLR",
        "segment": "Passenger cars",
        "uk_site": "Halewood, Solihull, Castle Bromwich",
        "region": "Merseyside / West Midlands",
        "ev_role": "Vehicle assembly",
        "manufacturing_status": "EV ramp-up / pre-production",
        "ev_models_or_products": "Range Rover Electric; future electric SUVs at Halewood",
        "parent_group": "Tata Motors",
        "ownership_country": "India",
        "estimated_uk_employment": None,
        "latest_public_output_units": 201283,
        "output_year": 2025,
        "output_metric": "All UK vehicle output by manufacturer/group",
        "performance_note": "2025 UK output down 21.7% versus 2024; Halewood being converted for EV production.",
        "source_keys": "wired_jlr_halewood_2024; smmt_vehicle_output_2025",
        "include_in_ev_manufacturer_count": True,
    },
    {
        "company_name": "BMW (UK) Manufacturing Limited",
        "trading_name": "MINI Plant Oxford",
        "segment": "Passenger cars",
        "uk_site": "Oxford",
        "region": "South East England",
        "ev_role": "Vehicle assembly",
        "manufacturing_status": "EV programme / investment",
        "ev_models_or_products": "MINI electric models / Oxford EV programme",
        "parent_group": "BMW Group",
        "ownership_country": "Germany",
        "estimated_uk_employment": None,
        "latest_public_output_units": 124271,
        "output_year": 2025,
        "output_metric": "All UK vehicle output by manufacturer/group",
        "performance_note": "2025 UK output up 12.2% versus 2024.",
        "source_keys": "smmt_vehicle_output_2025",
        "include_in_ev_manufacturer_count": True,
    },
    {
        "company_name": "Vauxhall Motors Limited",
        "trading_name": "Stellantis Ellesmere Port",
        "segment": "Light commercial vehicles",
        "uk_site": "Ellesmere Port",
        "region": "North West England",
        "ev_role": "Vehicle assembly",
        "manufacturing_status": "Active EV-only production",
        "ev_models_or_products": "Vauxhall Combo Electric; Opel Combo Electric; Peugeot e-Partner; Citroen e-Berlingo; Fiat E-Doblo",
        "parent_group": "Stellantis",
        "ownership_country": "Netherlands / multinational",
        "estimated_uk_employment": None,
        "latest_public_output_units": 31048,
        "output_year": 2025,
        "output_metric": "All UK Stellantis output by UK plants/group",
        "performance_note": "2025 UK output down 70.6% versus 2024; Ellesmere Port is reported as the UK's first EV-only plant.",
        "source_keys": "stellantis_ellesmere_2023; smmt_vehicle_output_2025",
        "include_in_ev_manufacturer_count": True,
    },
    {
        "company_name": "London EV Company Limited",
        "trading_name": "LEVC",
        "segment": "Taxis and vans",
        "uk_site": "Ansty, Coventry",
        "region": "West Midlands",
        "ev_role": "Vehicle assembly",
        "manufacturing_status": "Active range-extended EV production",
        "ev_models_or_products": "TX electric taxi; VN5 electric van",
        "parent_group": "Geely",
        "ownership_country": "China",
        "estimated_uk_employment": None,
        "latest_public_output_units": None,
        "output_year": None,
        "output_metric": "",
        "performance_note": "Included in SMMT 'Others' category rather than reported as a named high-volume producer.",
        "source_keys": "smmt_vehicle_output_2025",
        "include_in_ev_manufacturer_count": True,
    },
    {
        "company_name": "Alexander Dennis Limited",
        "trading_name": "Alexander Dennis",
        "segment": "Buses",
        "uk_site": "Larbert / Falkirk and UK operations",
        "region": "Scotland",
        "ev_role": "Vehicle assembly",
        "manufacturing_status": "Active zero-emission bus production",
        "ev_models_or_products": "Battery-electric single and double deck buses",
        "parent_group": "NFI Group",
        "ownership_country": "Canada",
        "estimated_uk_employment": 1950,
        "latest_public_output_units": None,
        "output_year": None,
        "output_metric": "",
        "performance_note": "Specialist bus manufacturer; output is usually not separated in headline car/van manufacturing tables.",
        "source_keys": "smmt_vehicle_output_2025",
        "include_in_ev_manufacturer_count": True,
    },
    {
        "company_name": "Wrightbus Limited",
        "trading_name": "Wrightbus",
        "segment": "Buses",
        "uk_site": "Ballymena",
        "region": "Northern Ireland",
        "ev_role": "Vehicle assembly",
        "manufacturing_status": "Active zero-emission bus production",
        "ev_models_or_products": "Battery-electric and hydrogen fuel-cell buses",
        "parent_group": "Bamford Bus Company",
        "ownership_country": "United Kingdom",
        "estimated_uk_employment": 1500,
        "latest_public_output_units": 1000,
        "output_year": 2025,
        "output_metric": "Reported zero-emission bus supply plan / pipeline",
        "performance_note": "Reported plan to supply up to 1,000 zero-emission buses and expand workforce.",
        "source_keys": "wrightbus_zero_emission_2025",
        "include_in_ev_manufacturer_count": True,
    },
    {
        "company_name": "Switch Mobility Limited",
        "trading_name": "Switch Mobility",
        "segment": "Buses and vans",
        "uk_site": "Sherburn-in-Elmet / Leeds area",
        "region": "Yorkshire and the Humber",
        "ev_role": "Vehicle assembly / engineering",
        "manufacturing_status": "Active EV manufacturer",
        "ev_models_or_products": "Electric buses and commercial vehicles",
        "parent_group": "Ashok Leyland / Hinduja Group",
        "ownership_country": "India",
        "estimated_uk_employment": None,
        "latest_public_output_units": None,
        "output_year": None,
        "output_metric": "",
        "performance_note": "Specialist EV manufacturer; detailed UK output not in headline SMMT manufacturer table.",
        "source_keys": "smmt_vehicle_output_2025",
        "include_in_ev_manufacturer_count": True,
    },
    {
        "company_name": "Tevva Motors Limited",
        "trading_name": "Tevva",
        "segment": "Trucks",
        "uk_site": "Tilbury",
        "region": "East of England",
        "ev_role": "Vehicle assembly",
        "manufacturing_status": "Specialist EV truck manufacturer",
        "ev_models_or_products": "Battery-electric trucks",
        "parent_group": "Tevva",
        "ownership_country": "United Kingdom",
        "estimated_uk_employment": None,
        "latest_public_output_units": None,
        "output_year": None,
        "output_metric": "",
        "performance_note": "Included for supply-chain mapping; verify current production status before using for official counts.",
        "source_keys": "companies_house_api",
        "include_in_ev_manufacturer_count": True,
    },
    {
        "company_name": "YASA Limited",
        "trading_name": "YASA",
        "segment": "EV components",
        "uk_site": "Oxford / Kidlington",
        "region": "South East England",
        "ev_role": "Electric motor manufacturing",
        "manufacturing_status": "Active EV component manufacturer",
        "ev_models_or_products": "Axial-flux electric motors",
        "parent_group": "Mercedes-Benz Group",
        "ownership_country": "Germany",
        "estimated_uk_employment": None,
        "latest_public_output_units": None,
        "output_year": None,
        "output_metric": "",
        "performance_note": "Component manufacturer, not a vehicle OEM; excluded from vehicle manufacturer count by default if required.",
        "source_keys": "companies_house_api",
        "include_in_ev_manufacturer_count": False,
    },
]


def companies_house_get(path: str, api_key: str, params: dict[str, str] | None = None) -> dict[str, Any] | None:
    query = f"?{urlencode(params)}" if params else ""
    url = f"https://api.company-information.service.gov.uk{path}{query}"
    token = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("ascii")
    req = Request(url, headers={"Authorization": f"Basic {token}", "Accept": "application/json"})

    try:
        with urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        print(f"Companies House HTTP {exc.code} for {path}")
    except URLError as exc:
        print(f"Companies House network error for {path}: {exc.reason}")
    except TimeoutError:
        print(f"Companies House timeout for {path}")
    return None


def enrich_from_companies_house(manufacturers: pd.DataFrame, api_key: str | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    enrichment_rows: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []

    if not api_key:
        manufacturers["companies_house_enriched"] = False
        manufacturers["companies_house_note"] = "Set COMPANIES_HOUSE_API_KEY to enrich this row."
        return manufacturers, pd.DataFrame(raw_rows)

    for _, row in manufacturers.iterrows():
        search = companies_house_get("/search/companies", api_key, {"q": row["company_name"], "items_per_page": "1"})
        time.sleep(0.2)

        item = (search or {}).get("items", [{}])[0] if (search or {}).get("items") else {}
        company_number = item.get("company_number")
        profile = companies_house_get(f"/company/{company_number}", api_key) if company_number else None
        time.sleep(0.2)

        raw_rows.append(
            {
                "seed_company_name": row["company_name"],
                "search_result": json.dumps(item, ensure_ascii=True),
                "profile": json.dumps(profile or {}, ensure_ascii=True),
            }
        )

        accounts = (profile or {}).get("accounts", {})
        registered_office = (profile or {}).get("registered_office_address", {})
        enrichment_rows.append(
            {
                "company_name": row["company_name"],
                "companies_house_enriched": bool(profile),
                "companies_house_note": "" if profile else "No Companies House profile returned.",
                "company_number": company_number,
                "matched_company_name": (profile or item).get("company_name") or item.get("title"),
                "company_status": (profile or {}).get("company_status"),
                "company_type": (profile or {}).get("type"),
                "incorporated_on": (profile or {}).get("date_of_creation"),
                "sic_codes": "; ".join((profile or {}).get("sic_codes", [])),
                "accounts_next_due": accounts.get("next_due"),
                "accounts_last_made_up_to": accounts.get("last_accounts", {}).get("made_up_to"),
                "registered_office_locality": registered_office.get("locality"),
                "registered_office_region": registered_office.get("region"),
                "registered_office_postal_code": registered_office.get("postal_code"),
            }
        )

    enrichment = pd.DataFrame(enrichment_rows)
    manufacturers = manufacturers.merge(enrichment, on="company_name", how="left")
    return manufacturers, pd.DataFrame(raw_rows)


def build_summary(manufacturers: pd.DataFrame) -> pd.DataFrame:
    vehicle_makers = manufacturers[manufacturers["include_in_ev_manufacturer_count"] == True]  # noqa: E712
    component_makers = manufacturers[manufacturers["include_in_ev_manufacturer_count"] == False]  # noqa: E712

    rows = [
        {"metric": "Vehicle EV manufacturers in register", "value": int(len(vehicle_makers))},
        {"metric": "EV component manufacturers in register", "value": int(len(component_makers))},
        {"metric": "Total firms in workbook", "value": int(len(manufacturers))},
        {
            "metric": "Named firms with public output or pipeline figure",
            "value": int(manufacturers["latest_public_output_units"].notna().sum()),
        },
        {
            "metric": "Latest public output / pipeline units, summed where comparable",
            "value": int(manufacturers["latest_public_output_units"].fillna(0).sum()),
        },
    ]

    by_segment = (
        vehicle_makers.groupby("segment", dropna=False)
        .size()
        .reset_index(name="value")
        .rename(columns={"segment": "metric"})
    )
    by_segment["metric"] = "Vehicle EV manufacturers - " + by_segment["metric"].astype(str)

    return pd.concat([pd.DataFrame(rows), by_segment], ignore_index=True)


def write_excel(
    manufacturers: pd.DataFrame,
    summary: pd.DataFrame,
    sources: pd.DataFrame,
    raw_companies_house: pd.DataFrame,
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="summary", index=False)
        manufacturers.to_excel(writer, sheet_name="manufacturers", index=False)
        sources.to_excel(writer, sheet_name="sources", index=False)
        raw_companies_house.to_excel(writer, sheet_name="companies_house_raw", index=False)

        notes = pd.DataFrame(
            [
                {
                    "note": "The vehicle manufacturer count uses include_in_ev_manufacturer_count=True. YASA is retained as an EV supply-chain component maker but excluded from the vehicle OEM count.",
                },
                {
                    "note": "latest_public_output_units mixes all-vehicle output, EV-only plant output, and specialist pipeline figures; use output_metric before summing or comparing firms.",
                },
                {
                    "note": f"Workbook generated at {datetime.now(timezone.utc).isoformat()} UTC.",
                },
            ]
        )
        notes.to_excel(writer, sheet_name="notes", index=False)

        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column_cells in worksheet.columns:
                max_length = max(len(str(cell.value or "")) for cell in column_cells)
                worksheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 12), 60)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect UK EV manufacturer data into Excel.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help=f"Output workbook path. Default: {DEFAULT_OUTPUT}")
    parser.add_argument(
        "--no-companies-house",
        action="store_true",
        help="Skip Companies House enrichment even if COMPANIES_HOUSE_API_KEY is set.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api_key = None if args.no_companies_house else os.getenv("COMPANIES_HOUSE_API_KEY")

    manufacturers = pd.DataFrame(MANUFACTURERS)
    sources = pd.DataFrame(SOURCES)
    manufacturers, raw_companies_house = enrich_from_companies_house(manufacturers, api_key)
    summary = build_summary(manufacturers)
    write_excel(manufacturers, summary, sources, raw_companies_house, args.output)

    print(f"Wrote {args.output}")
    print(summary.to_string(index=False))
    if not api_key and not args.no_companies_house:
        print("Tip: set COMPANIES_HOUSE_API_KEY for live Companies House enrichment.")


if __name__ == "__main__":
    main()
