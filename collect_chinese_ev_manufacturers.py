"""
Collect Chinese EV manufacturer firm-level data into an Excel workbook.

The script creates a multi-sheet workbook for China-based EV manufacturers,
covering major vehicle OEMs, EV-only challengers, and selected battery /
component firms relevant to an EV supply-chain study.

Usage
-----
  python collect_chinese_ev_manufacturers.py
  python collect_chinese_ev_manufacturers.py --output results/chinese_ev_manufacturers.xlsx
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


RESULTS_DIR = Path(__file__).resolve().parent / "results"
DEFAULT_OUTPUT = RESULTS_DIR / "chinese_ev_manufacturers.xlsx"


SOURCES = [
    {
        "source_key": "byd_2025_sales",
        "title": "BYD 2025 new energy vehicle sales",
        "publisher": "EV Briefing / BYD sales reporting",
        "published": "2026-02-25",
        "url": "https://evbriefing.com/briefings/byd-4-6-million-ev-sales-2025-global-nev-leader",
        "notes": "Reports BYD 2025 sales of 4,602,400 NEVs and overseas sales above one million.",
    },
    {
        "source_key": "geely_2025_sales",
        "title": "Geely Auto exceeds 3.02 million vehicle sales in 2025",
        "publisher": "Geely Auto",
        "published": "2026-01-08",
        "url": "https://www.geely.com/en/news/2026/geely-auto-sales-2025",
        "notes": "Reports 2025 total sales of 3,024,567 vehicles and NEV sales of 1,687,767 vehicles.",
    },
    {
        "source_key": "saic_jan_nov_2025",
        "title": "SAIC Motor Jan-Nov 2025 sales",
        "publisher": "SAIC Motor",
        "published": "2025-12-04",
        "url": "https://www.saicmotor.com/english/latest_news/saic_motor/63291.shtml",
        "notes": "Reports Jan-Nov sales of 4.108 million vehicles and NEV sales of 1.499 million vehicles.",
    },
    {
        "source_key": "changan_2025_sales",
        "title": "Changan Automobile achieves record NEV and overseas sales in 2025",
        "publisher": "Changan Europe",
        "published": "2026-04-14",
        "url": "https://newsroom.changaneurope.com/changan-automobile-achieves-record-nev-and-overseas-sales-in-2025-accelerating-global-growth",
        "notes": "Reports 2025 total sales of 2.913 million vehicles and NEV sales of 1.11 million vehicles.",
    },
    {
        "source_key": "chery_2025_sales",
        "title": "Chery 2025 global sales",
        "publisher": "Cinco Dias / El Pais",
        "published": "2026-04-28",
        "url": "https://cincodias.elpais.com/companias/2026-04-28/chery-aspira-a-ser-como-toyota-mas-tesla-en-su-carrera-por-la-expansion-global.html",
        "notes": "Reports Chery global sales of 2.8 million vehicles in 2025.",
    },
    {
        "source_key": "gwm_2025_sales",
        "title": "Great Wall Motor 2025 global sales",
        "publisher": "paultan.org",
        "published": "2026-01-07",
        "url": "https://paultan.org/2026/01/07/gwm-sells-record-1323672-cars-globally-in-2025-506066-exports-403653-nevs-wey-sales-up-by-86/",
        "notes": "Reports GWM 2025 total sales of 1,323,672 vehicles and 403,653 NEVs.",
    },
    {
        "source_key": "gac_2025_sales",
        "title": "GAC Group 2025 sales",
        "publisher": "ChinaEVHome",
        "published": "2026-01-08",
        "url": "https://chinaevhome.com/2026/01/08/gac-group-2025-sales-1-72m-units-gac-toyota-sole-growth-engine/",
        "notes": "Reports GAC 2025 total sales of 1,721,489 vehicles and NEV sales of 433,634 vehicles.",
    },
    {
        "source_key": "nio_2025_deliveries",
        "title": "NIO December, Q4, and full-year 2025 deliveries",
        "publisher": "NIO",
        "published": "2026-01-01",
        "url": "https://www.nio.com/news/20260101001",
        "notes": "Reports 2025 deliveries of 326,028 vehicles and cumulative deliveries of 997,592.",
    },
    {
        "source_key": "xpeng_2025_deliveries",
        "title": "XPENG December and full-year 2025 deliveries",
        "publisher": "XPeng Inc.",
        "published": "2026-01-01",
        "url": "https://ir.xiaopeng.com/news-releases/news-release-details/xpeng-announces-vehicle-delivery-results-december-and-full-year",
        "notes": "Reports 2025 deliveries of 429,445 vehicles and overseas deliveries of 45,008 vehicles.",
    },
    {
        "source_key": "li_auto_2025_results",
        "title": "Li Auto fourth quarter and full-year 2025 financial results",
        "publisher": "Li Auto Inc.",
        "published": "2026-03-12",
        "url": "https://ir.lixiang.com/zh-hant/news-releases/news-release-details/li-auto-inc-announces-unaudited-fourth-quarter-and-full-year-4/",
        "notes": "Reports 2025 deliveries of 406,343 vehicles and full-year revenue of RMB112.3 billion.",
    },
    {
        "source_key": "leapmotor_2025_deliveries",
        "title": "Leapmotor 2025 delivery milestone",
        "publisher": "Gasgoo",
        "published": "2025-11-17",
        "url": "https://autonews.gasgoo.com/articles/ev/70039760",
        "notes": "Reports Leapmotor exceeded 500,000 deliveries year-to-date in November 2025.",
    },
    {
        "source_key": "xiaomi_2025_deliveries",
        "title": "Xiaomi Auto 2025 deliveries",
        "publisher": "EV Briefing",
        "published": "2026-01-12",
        "url": "https://evbriefing.com/briefings/xiaomi-auto-50k-december-2025-deliveries",
        "notes": "Reports Xiaomi Auto 2025 deliveries of 411,600 vehicles.",
    },
    {
        "source_key": "xiaomi_2025_financials",
        "title": "Xiaomi 2025 revenue and EV segment performance",
        "publisher": "Cinco Dias / El Pais",
        "published": "2026-03-24",
        "url": "https://cincodias.elpais.com/companias/2026-03-24/xiaomi-registra-ingresos-record-en-2025-gracias-a-los-coches-electricos-y-la-ia.html",
        "notes": "Reports Smart EV, AI and new initiatives revenue of RMB106.1bn in 2025.",
    },
    {
        "source_key": "catl_2025_annual_report",
        "title": "CATL releases 2025 annual report",
        "publisher": "CATL",
        "published": "2026-03-10",
        "url": "https://www.catl.com/en/news/6773.html",
        "notes": "Reports RMB423.7bn revenue, RMB72.2bn net profit, 661 GWh battery sales, and 772 GWh capacity.",
    },
]


MANUFACTURERS = [
    {
        "company_name": "BYD Company Limited",
        "trading_name": "BYD",
        "headquarters": "Shenzhen, Guangdong",
        "ownership_type": "Public",
        "stock_tickers": "002594.SZ; 1211.HK",
        "segment": "Mass-market and premium NEV vehicles",
        "ev_role": "Vehicle OEM; batteries; power electronics",
        "brands": "BYD; Denza; Fangchengbao; Yangwang",
        "powertrain_scope": "BEV; PHEV",
        "manufacturing_status": "Active high-volume NEV manufacturer",
        "latest_total_vehicle_sales_units": 4602400,
        "latest_nev_sales_or_delivery_units": 4602400,
        "metric_year": 2025,
        "metric_type": "Global NEV sales",
        "yoy_change_pct": 7.7,
        "overseas_or_export_units": 1049600,
        "revenue_rmb_bn": None,
        "net_profit_rmb_bn": None,
        "performance_note": "World-leading NEV scale; 2025 growth slowed amid intense China price competition.",
        "source_keys": "byd_2025_sales",
        "include_in_vehicle_maker_count": True,
    },
    {
        "company_name": "Geely Automobile Holdings Limited",
        "trading_name": "Geely Auto Group",
        "headquarters": "Hangzhou, Zhejiang",
        "ownership_type": "Public",
        "stock_tickers": "0175.HK",
        "segment": "Multi-brand passenger vehicles",
        "ev_role": "Vehicle OEM",
        "brands": "Geely Galaxy/Yinhe; Lynk & Co; ZEEKR",
        "powertrain_scope": "BEV; PHEV; hybrid; ICE",
        "manufacturing_status": "Active high-volume NEV manufacturer",
        "latest_total_vehicle_sales_units": 3024567,
        "latest_nev_sales_or_delivery_units": 1687767,
        "metric_year": 2025,
        "metric_type": "Global total sales and NEV sales",
        "yoy_change_pct": 39.0,
        "overseas_or_export_units": 420097,
        "revenue_rmb_bn": None,
        "net_profit_rmb_bn": None,
        "performance_note": "NEV sales rose 90% YoY; ZEEKR exceeded 220,000 annual sales within the group.",
        "source_keys": "geely_2025_sales",
        "include_in_vehicle_maker_count": True,
    },
    {
        "company_name": "SAIC Motor Corporation Limited",
        "trading_name": "SAIC Motor",
        "headquarters": "Shanghai",
        "ownership_type": "State-owned / public",
        "stock_tickers": "600104.SS",
        "segment": "Multi-brand passenger and commercial vehicles",
        "ev_role": "Vehicle OEM",
        "brands": "MG; Roewe; IM Motors; Maxus; Wuling JV products",
        "powertrain_scope": "BEV; PHEV; hybrid; ICE",
        "manufacturing_status": "Active high-volume NEV manufacturer",
        "latest_total_vehicle_sales_units": 4108000,
        "latest_nev_sales_or_delivery_units": 1499000,
        "metric_year": 2025,
        "metric_type": "Jan-Nov total sales and NEV sales",
        "yoy_change_pct": 16.4,
        "overseas_or_export_units": 969000,
        "revenue_rmb_bn": None,
        "net_profit_rmb_bn": None,
        "performance_note": "Jan-Nov 2025 sales already surpassed full-year 2024; full-year figure should be refreshed when available.",
        "source_keys": "saic_jan_nov_2025",
        "include_in_vehicle_maker_count": True,
    },
    {
        "company_name": "Changan Automobile Company Limited",
        "trading_name": "Changan Automobile",
        "headquarters": "Chongqing",
        "ownership_type": "State-owned / public",
        "stock_tickers": "000625.SZ",
        "segment": "Passenger and light commercial vehicles",
        "ev_role": "Vehicle OEM",
        "brands": "Changan Nevo; Deepal; Avatr; Changan",
        "powertrain_scope": "BEV; EREV; PHEV; hybrid; ICE",
        "manufacturing_status": "Active high-volume NEV manufacturer",
        "latest_total_vehicle_sales_units": 2913000,
        "latest_nev_sales_or_delivery_units": 1110000,
        "metric_year": 2025,
        "metric_type": "Global total sales and NEV sales",
        "yoy_change_pct": 8.54,
        "overseas_or_export_units": 637300,
        "revenue_rmb_bn": None,
        "net_profit_rmb_bn": None,
        "performance_note": "NEV sales rose 51.1% YoY; Deepal, Avatr, and Nevo are key electrification brands.",
        "source_keys": "changan_2025_sales",
        "include_in_vehicle_maker_count": True,
    },
    {
        "company_name": "Chery Automobile Company Limited",
        "trading_name": "Chery Group",
        "headquarters": "Wuhu, Anhui",
        "ownership_type": "State-owned / mixed",
        "stock_tickers": "",
        "segment": "Passenger vehicles and export-focused brands",
        "ev_role": "Vehicle OEM",
        "brands": "Chery; Exeed; Jetour; iCar; Luxeed; Omoda; Jaecoo",
        "powertrain_scope": "BEV; PHEV; hybrid; ICE",
        "manufacturing_status": "Active NEV manufacturer",
        "latest_total_vehicle_sales_units": 2800000,
        "latest_nev_sales_or_delivery_units": None,
        "metric_year": 2025,
        "metric_type": "Global total sales",
        "yoy_change_pct": None,
        "overseas_or_export_units": None,
        "revenue_rmb_bn": None,
        "net_profit_rmb_bn": None,
        "performance_note": "Large export-oriented OEM; full-year 2025 NEV figure should be refreshed from Chery disclosures when available.",
        "source_keys": "chery_2025_sales",
        "include_in_vehicle_maker_count": True,
    },
    {
        "company_name": "Great Wall Motor Company Limited",
        "trading_name": "GWM",
        "headquarters": "Baoding, Hebei",
        "ownership_type": "Public",
        "stock_tickers": "601633.SS; 2333.HK",
        "segment": "SUVs, pickups, and NEV brands",
        "ev_role": "Vehicle OEM",
        "brands": "Haval; Wey; Tank; Ora; Poer",
        "powertrain_scope": "BEV; PHEV; hybrid; ICE",
        "manufacturing_status": "Active NEV manufacturer",
        "latest_total_vehicle_sales_units": 1323672,
        "latest_nev_sales_or_delivery_units": 403653,
        "metric_year": 2025,
        "metric_type": "Global total sales and NEV sales",
        "yoy_change_pct": 7.33,
        "overseas_or_export_units": 506066,
        "revenue_rmb_bn": None,
        "net_profit_rmb_bn": None,
        "performance_note": "NEV sales grew 25.44% YoY; Ora brand declined while Wey grew strongly.",
        "source_keys": "gwm_2025_sales",
        "include_in_vehicle_maker_count": True,
    },
    {
        "company_name": "Guangzhou Automobile Group Company Limited",
        "trading_name": "GAC Group / GAC Aion",
        "headquarters": "Guangzhou, Guangdong",
        "ownership_type": "State-owned / public",
        "stock_tickers": "601238.SS; 2238.HK",
        "segment": "Passenger vehicles",
        "ev_role": "Vehicle OEM",
        "brands": "GAC; Aion; Hyptec; Trumpchi",
        "powertrain_scope": "BEV; PHEV; hybrid; ICE",
        "manufacturing_status": "Active NEV manufacturer",
        "latest_total_vehicle_sales_units": 1721489,
        "latest_nev_sales_or_delivery_units": 433634,
        "metric_year": 2025,
        "metric_type": "Group total sales and NEV sales",
        "yoy_change_pct": -14.06,
        "overseas_or_export_units": 130000,
        "revenue_rmb_bn": None,
        "net_profit_rmb_bn": None,
        "performance_note": "2025 group sales declined, but Aion and Hyptec remain major NEV product lines.",
        "source_keys": "gac_2025_sales",
        "include_in_vehicle_maker_count": True,
    },
    {
        "company_name": "NIO Inc.",
        "trading_name": "NIO",
        "headquarters": "Shanghai",
        "ownership_type": "Public",
        "stock_tickers": "NIO; 9866.HK; NIO.SI",
        "segment": "Premium smart EVs",
        "ev_role": "Vehicle OEM",
        "brands": "NIO; ONVO; Firefly",
        "powertrain_scope": "BEV",
        "manufacturing_status": "Active EV manufacturer",
        "latest_total_vehicle_sales_units": None,
        "latest_nev_sales_or_delivery_units": 326028,
        "metric_year": 2025,
        "metric_type": "Deliveries",
        "yoy_change_pct": 46.9,
        "overseas_or_export_units": None,
        "revenue_rmb_bn": None,
        "net_profit_rmb_bn": None,
        "performance_note": "Record 2025 deliveries; ONVO and Firefly broaden the product range beyond premium NIO models.",
        "source_keys": "nio_2025_deliveries",
        "include_in_vehicle_maker_count": True,
    },
    {
        "company_name": "XPeng Inc.",
        "trading_name": "XPENG",
        "headquarters": "Guangzhou, Guangdong",
        "ownership_type": "Public",
        "stock_tickers": "XPEV; 9868.HK",
        "segment": "Smart EVs",
        "ev_role": "Vehicle OEM; autonomous driving software",
        "brands": "XPENG; MONA",
        "powertrain_scope": "BEV",
        "manufacturing_status": "Active EV manufacturer",
        "latest_total_vehicle_sales_units": None,
        "latest_nev_sales_or_delivery_units": 429445,
        "metric_year": 2025,
        "metric_type": "Deliveries",
        "yoy_change_pct": 126.0,
        "overseas_or_export_units": 45008,
        "revenue_rmb_bn": None,
        "net_profit_rmb_bn": None,
        "performance_note": "Fast delivery growth and overseas expansion to 60 countries and regions by year-end 2025.",
        "source_keys": "xpeng_2025_deliveries",
        "include_in_vehicle_maker_count": True,
    },
    {
        "company_name": "Li Auto Inc.",
        "trading_name": "Li Auto",
        "headquarters": "Beijing",
        "ownership_type": "Public",
        "stock_tickers": "LI; 2015.HK",
        "segment": "Family SUVs and MPVs",
        "ev_role": "Vehicle OEM",
        "brands": "Li Auto",
        "powertrain_scope": "EREV; BEV",
        "manufacturing_status": "Active NEV manufacturer",
        "latest_total_vehicle_sales_units": None,
        "latest_nev_sales_or_delivery_units": 406343,
        "metric_year": 2025,
        "metric_type": "Deliveries",
        "yoy_change_pct": None,
        "overseas_or_export_units": None,
        "revenue_rmb_bn": 112.3,
        "net_profit_rmb_bn": None,
        "performance_note": "2025 deliveries fell from 2024, while full-year revenue reached RMB112.3bn.",
        "source_keys": "li_auto_2025_results",
        "include_in_vehicle_maker_count": True,
    },
    {
        "company_name": "Zhejiang Leapmotor Technology Company Limited",
        "trading_name": "Leapmotor",
        "headquarters": "Hangzhou, Zhejiang",
        "ownership_type": "Public",
        "stock_tickers": "9863.HK",
        "segment": "Mass-market smart EVs",
        "ev_role": "Vehicle OEM",
        "brands": "Leapmotor",
        "powertrain_scope": "BEV; EREV",
        "manufacturing_status": "Active NEV manufacturer",
        "latest_total_vehicle_sales_units": None,
        "latest_nev_sales_or_delivery_units": 500000,
        "metric_year": 2025,
        "metric_type": "YTD deliveries milestone, exceeded by Nov 2025",
        "yoy_change_pct": None,
        "overseas_or_export_units": None,
        "revenue_rmb_bn": None,
        "net_profit_rmb_bn": None,
        "performance_note": "Exceeded 500,000 YTD deliveries before year-end; final annual figure should be refreshed from annual results.",
        "source_keys": "leapmotor_2025_deliveries",
        "include_in_vehicle_maker_count": True,
    },
    {
        "company_name": "Xiaomi EV",
        "trading_name": "Xiaomi Auto",
        "headquarters": "Beijing",
        "ownership_type": "Subsidiary / public parent",
        "stock_tickers": "1810.HK",
        "segment": "Smart EVs",
        "ev_role": "Vehicle OEM",
        "brands": "Xiaomi SU7; Xiaomi YU7",
        "powertrain_scope": "BEV",
        "manufacturing_status": "Active EV manufacturer",
        "latest_total_vehicle_sales_units": None,
        "latest_nev_sales_or_delivery_units": 411600,
        "metric_year": 2025,
        "metric_type": "Deliveries",
        "yoy_change_pct": 200.0,
        "overseas_or_export_units": None,
        "revenue_rmb_bn": 106.1,
        "net_profit_rmb_bn": None,
        "performance_note": "Rapid scale-up from two models; Smart EV, AI and new initiatives segment generated RMB106.1bn revenue in 2025.",
        "source_keys": "xiaomi_2025_deliveries; xiaomi_2025_financials",
        "include_in_vehicle_maker_count": True,
    },
    {
        "company_name": "Contemporary Amperex Technology Company Limited",
        "trading_name": "CATL",
        "headquarters": "Ningde, Fujian",
        "ownership_type": "Public",
        "stock_tickers": "300750.SZ; 3750.HK",
        "segment": "EV batteries and energy storage",
        "ev_role": "Battery manufacturer",
        "brands": "CATL; Shenxing; Freevoy; Naxtra",
        "powertrain_scope": "Battery cells and systems",
        "manufacturing_status": "Active battery manufacturer",
        "latest_total_vehicle_sales_units": None,
        "latest_nev_sales_or_delivery_units": None,
        "metric_year": 2025,
        "metric_type": "Battery sales, GWh",
        "yoy_change_pct": 39.0,
        "overseas_or_export_units": None,
        "revenue_rmb_bn": 423.7,
        "net_profit_rmb_bn": 72.2,
        "performance_note": "Lithium-ion battery sales reached 661 GWh; global production capacity reached 772 GWh.",
        "source_keys": "catl_2025_annual_report",
        "include_in_vehicle_maker_count": False,
    },
]


def build_summary(manufacturers: pd.DataFrame) -> pd.DataFrame:
    vehicle_makers = manufacturers[manufacturers["include_in_vehicle_maker_count"] == True]  # noqa: E712
    supply_chain_firms = manufacturers[manufacturers["include_in_vehicle_maker_count"] == False]  # noqa: E712

    comparable_nev_units = vehicle_makers["latest_nev_sales_or_delivery_units"].fillna(0).sum()
    known_nev_count = vehicle_makers["latest_nev_sales_or_delivery_units"].notna().sum()

    rows = [
        {"metric": "Vehicle EV/NEV manufacturers in register", "value": int(len(vehicle_makers))},
        {"metric": "EV supply-chain component firms in register", "value": int(len(supply_chain_firms))},
        {"metric": "Total firms in workbook", "value": int(len(manufacturers))},
        {"metric": "Vehicle makers with 2025 NEV/delivery figure", "value": int(known_nev_count)},
        {
            "metric": "Sum of reported NEV sales/deliveries where available",
            "value": int(comparable_nev_units),
        },
    ]

    by_powertrain = (
        vehicle_makers.assign(primary_powertrain=vehicle_makers["powertrain_scope"].str.split(";").str[0].str.strip())
        .groupby("primary_powertrain", dropna=False)
        .size()
        .reset_index(name="value")
        .rename(columns={"primary_powertrain": "metric"})
    )
    by_powertrain["metric"] = "Vehicle makers - primary scope " + by_powertrain["metric"].astype(str)

    return pd.concat([pd.DataFrame(rows), by_powertrain], ignore_index=True)


def write_excel(manufacturers: pd.DataFrame, summary: pd.DataFrame, sources: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    notes = pd.DataFrame(
        [
            {
                "note": "Chinese automakers report mixed measures: wholesale sales, retail sales, group sales, deliveries, and NEV-only sales. Use metric_type before comparing rows.",
            },
            {
                "note": "The manufacturer count uses include_in_vehicle_maker_count=True. CATL is kept as an EV supply-chain firm and excluded from the vehicle OEM count.",
            },
            {
                "note": "NEV includes battery electric vehicles, plug-in hybrids, extended-range EVs, and in some Chinese reporting fuel-cell vehicles.",
            },
            {
                "note": f"Workbook generated at {datetime.now(timezone.utc).isoformat()} UTC.",
            },
        ]
    )

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="summary", index=False)
        manufacturers.to_excel(writer, sheet_name="manufacturers", index=False)
        sources.to_excel(writer, sheet_name="sources", index=False)
        notes.to_excel(writer, sheet_name="notes", index=False)

        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column_cells in worksheet.columns:
                max_length = max(len(str(cell.value or "")) for cell in column_cells)
                worksheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 12), 60)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Chinese EV manufacturer data into Excel.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help=f"Output workbook path. Default: {DEFAULT_OUTPUT}")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manufacturers = pd.DataFrame(MANUFACTURERS)
    sources = pd.DataFrame(SOURCES)
    summary = build_summary(manufacturers)
    write_excel(manufacturers, summary, sources, args.output)

    print(f"Wrote {args.output}")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
