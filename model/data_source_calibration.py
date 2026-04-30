"""
Data-source calibration layer.

This module converts the free data sources listed in ``index.html`` into small,
bounded behavioural nudges for the ABM agents.  The intent is not to replace
the engineering calibration in ``config.py``; it gives the model a transparent
place to use country-level manufacturing and technology indicators from the
World Bank registry, alongside the USGS/IEA concentration facts already used in
the tier definitions.

World Bank indicators mirrored from ``index.html``:
  - NV.IND.MANF.ZS: Manufacturing value added (% of GDP)
  - TX.VAL.TECH.MF.ZS: High-technology exports (% manufactured exports)
  - NY.GDP.MKTP.CD: GDP current US$
"""

from __future__ import annotations

from dataclasses import dataclass
from math import log10


DATA_SOURCE_REGISTRY: tuple[dict[str, object], ...] = (
    {
        "name": "World Bank Open Data API",
        "url": "https://datahelpdesk.worldbank.org/knowledgebase/articles/898581",
        "uses": (
            "country manufacturing value added",
            "high-technology export share",
            "GDP scale for shock absorption",
        ),
    },
    {
        "name": "IEA Global EV Outlook 2024",
        "url": "https://www.iea.org/reports/global-ev-outlook-2024",
        "uses": ("regional EV battery demand", "cell market shares"),
    },
    {
        "name": "USGS Mineral Commodity Summaries 2024",
        "url": "https://pubs.usgs.gov/periodicals/mcs2024/mcs2024.pdf",
        "uses": ("mineral country concentration", "criticality anchors"),
    },
    {
        "name": "IEA Critical Minerals Market Review 2023",
        "url": "https://www.iea.org/reports/critical-minerals-market-review-2023",
        "uses": ("critical mineral supply-demand balance", "processing concentration"),
    },
    {
        "name": "UN Comtrade Database",
        "url": "https://comtradeplus.un.org/",
        "uses": ("future component trade-flow routing",),
    },
)


@dataclass(frozen=True)
class CountryIndicator:
    country_code: str
    manufacturing_pct_gdp: float
    hightech_exports_pct_mfg: float
    gdp_usd_trn: float
    year: int
    source: str = "World Bank Open Data API"


@dataclass(frozen=True)
class DataSourceCalibration:
    recovery_multiplier: float = 1.0
    inventory_multiplier: float = 1.0
    growth_multiplier: float = 1.0
    shock_absorption: float = 0.0
    source_note: str = "default"


# Seed values for the countries explicitly listed in index.html's World Bank
# country cards.  They are deliberately rounded because the model uses them only
# as bounded behavioural multipliers, not as reported KPI outputs.
COUNTRY_INDICATORS: dict[str, CountryIndicator] = {
    "uk": CountryIndicator("GBR", 9.2, 22.0, 3.4, 2023),
    "united_kingdom": CountryIndicator("GBR", 9.2, 22.0, 3.4, 2023),
    "china": CountryIndicator("CHN", 26.2, 24.0, 17.8, 2023),
    "usa": CountryIndicator("USA", 10.3, 19.0, 27.4, 2023),
    "japan": CountryIndicator("JPN", 19.9, 17.0, 4.2, 2023),
    "south_korea": CountryIndicator("KOR", 25.6, 36.0, 1.7, 2023),
    "korea": CountryIndicator("KOR", 25.6, 36.0, 1.7, 2023),
    "germany": CountryIndicator("DEU", 18.5, 15.0, 4.5, 2023),
    "europe": CountryIndicator("DEU", 18.5, 15.0, 4.5, 2023),
    "eu": CountryIndicator("DEU", 18.5, 15.0, 4.5, 2023),
    "australia": CountryIndicator("AUS", 5.6, 13.0, 1.7, 2023),
    "chile": CountryIndicator("CHL", 9.5, 7.0, 0.3, 2023),
    "drc": CountryIndicator("COD", 18.0, 1.0, 0.07, 2023),
    "indonesia": CountryIndicator("IDN", 18.7, 8.0, 1.4, 2023),
    "mixed": CountryIndicator("MIX", 16.0, 15.0, 2.5, 2023),
    "others": CountryIndicator("OTH", 14.0, 10.0, 1.0, 2023),
}


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def calibration_for_country(country_or_region: str | None) -> DataSourceCalibration:
    key = (country_or_region or "mixed").lower().replace(" ", "_")
    ind = COUNTRY_INDICATORS.get(key, COUNTRY_INDICATORS["mixed"])

    manufacturing_score = _clip(ind.manufacturing_pct_gdp / 15.0, 0.55, 1.65)
    hightech_score = _clip(ind.hightech_exports_pct_mfg / 18.0, 0.35, 2.00)
    gdp_score = _clip((log10(max(ind.gdp_usd_trn, 0.05)) + 1.0) / 2.0, 0.35, 1.35)

    recovery = _clip(0.82 + 0.14 * manufacturing_score + 0.06 * hightech_score, 0.85, 1.16)
    inventory = _clip(0.88 + 0.08 * gdp_score + 0.06 * manufacturing_score, 0.90, 1.12)
    growth = _clip(0.82 + 0.08 * manufacturing_score + 0.10 * hightech_score, 0.85, 1.15)
    absorption = _clip(0.015 + 0.030 * gdp_score + 0.018 * hightech_score, 0.02, 0.10)

    note = (
        f"{ind.source} {ind.country_code} {ind.year}: "
        f"manufacturing {ind.manufacturing_pct_gdp:.1f}% GDP, "
        f"high-tech exports {ind.hightech_exports_pct_mfg:.1f}% manufactured exports"
    )
    return DataSourceCalibration(
        recovery_multiplier=recovery,
        inventory_multiplier=inventory,
        growth_multiplier=growth,
        shock_absorption=absorption,
        source_note=note,
    )


def apply_data_source_calibration(agent, country_or_region: str | None) -> None:
    """Apply bounded country-level calibration to an already constructed agent."""
    cal = calibration_for_country(country_or_region)
    agent.data_source_region = country_or_region or "mixed"
    agent.data_source_calibration = cal
    agent.data_source_note = cal.source_note
    agent.data_source_shock_absorption = cal.shock_absorption

    if hasattr(agent, "recovery_rate_wk"):
        agent.recovery_rate_wk *= cal.recovery_multiplier
    if hasattr(agent, "safety_stock_weeks"):
        agent.safety_stock_weeks *= cal.inventory_multiplier
    if hasattr(agent, "capacity_growth_wk"):
        agent.capacity_growth_wk *= cal.growth_multiplier

    # Re-align stock targets after safety-stock calibration.
    if hasattr(agent, "weekly_capacity") and hasattr(agent, "safety_stock_weeks"):
        if hasattr(agent, "target_inventory"):
            agent.target_inventory = agent.weekly_capacity * agent.safety_stock_weeks
        if hasattr(agent, "inventory_gwh"):
            agent.inventory_gwh = agent.weekly_capacity * agent.safety_stock_weeks
        if hasattr(agent, "inventory"):
            agent.inventory = agent.weekly_capacity * agent.safety_stock_weeks

    if hasattr(agent, "weekly_target") and hasattr(agent, "safety_stock_weeks"):
        target_inv = agent.weekly_target * agent.safety_stock_weeks
        if hasattr(agent, "target_inv"):
            agent.target_inv = target_inv
        if hasattr(agent, "inv"):
            agent.inv = {component: target_inv for component in agent.inv}
