# Standard libraries
import logging
from pathlib import Path

# 3pps
import pytest

# Own modules
from degiro_tools.providers.holdings import fetch_holdings
from degiro_tools.providers.holdings.xtrackers import XtrackersParser
from tests.test_holdings_parser import write_xlsx

ISIN = "TEST_ISIN"

EXPECTED_ENGLISH_EQUITIES = 2

XTRACKERS_HEADER: list[object] = [
    None,
    "Name",
    "ISIN",
    "Country",
    "Currency",
    "Exchange",
    "Type of Security",
    "Rating",
    "Primary Listing",
    "Industry Classification",
    "Weighting",
]


def constituent_row(
    name: str,
    country: str,
    security_type: str,
    industry: str,
    weight: float,
) -> list[object]:
    """
    Build a single Xtrackers/DWS constituent row.

    Args:
        name: Holding name.
        country: Country column value.
        security_type: ``Type of Security`` column value.
        industry: ``Industry Classification`` column value.
        weight: Fractional weight as exported by DWS.

    Returns:
        List of eleven cell values ready to append to a worksheet.
    """

    return [
        1,
        name,
        "XS0000000000",
        country,
        "EUR",
        "-",
        security_type,
        "-",
        "-",
        industry,
        weight,
    ]


@pytest.fixture
def xtrackers_english_file(tmp_path: Path) -> Path:
    """
    Write an Xtrackers fixture using the English localization.

    DWS ships the same fund with ``Equities`` instead of ``Renta
    Variable`` and English sub-industry labels.
    """

    rows: list[list[object]] = [
        ["Although information in this ..."],
        XTRACKERS_HEADER,
        constituent_row(
            "NEXTERA ENERGY INC", "United States", "Equities", "Electric Utilities", 0.6
        ),
        constituent_row("IBERDROLA SA", "Spain", "Equities", "Multi-Utilities", 0.39),
        constituent_row("EUR CASH", "United States", "Cash", "unknown", 0.01),
    ]

    return write_xlsx(tmp_path / "Constituent_EN.xlsx", "2026-07-28", rows)


@pytest.fixture
def money_market_file(tmp_path: Path) -> Path:
    """
    Write a money-market fixture dominated by bond rows.

    Mirrors the Xtrackers EUR Overnight Rate Swap constituent export,
    whose only equity line is an incidental sliver of the fund.
    """

    rows: list[list[object]] = [
        ["Aunque la información contenida ..."],
        XTRACKERS_HEADER,
        constituent_row("ITALY (REPUBLIC OF)", "Italia", "Bono", "desconocido", 0.6),
        constituent_row(
            "BUNDESREPUB. DEUTSCHLAND",
            "Alemania",
            "Bono gubernamental",
            "desconocido",
            0.399,
        ),
        constituent_row("-", "Irlanda", "Renta Variable", "desconocido", 0.001),
    ]

    return write_xlsx(tmp_path / "Constituent_MM.xlsx", "2026-07-14", rows)


def test_english_export_is_parsed(xtrackers_english_file: Path) -> None:
    """
    The English ``Equities`` label is recognized as equity.

    Only accepting the Spanish ``Renta Variable`` silently dropped
    whole funds whose export happened to be localized in English.
    """

    holdings = XtrackersParser().parse(xtrackers_english_file, ISIN)

    assert len(holdings) == EXPECTED_ENGLISH_EQUITIES
    assert {h.name for h in holdings} == {"NEXTERA ENERGY INC", "IBERDROLA SA"}
    assert sum(h.weight_pct for h in holdings) == pytest.approx(99.0)


def test_english_sub_industries_normalize_to_utilities(
    xtrackers_english_file: Path,
) -> None:
    """
    English DWS sub-industries map to the canonical GICS sector.

    ``Electric Utilities`` and ``Multi-Utilities`` must both surface as
    ``Utilities`` so they aggregate with the rest of the portfolio.
    """

    holdings = fetch_holdings(ISIN, xtrackers_english_file)

    assert {h.sector for h in holdings} == {"Utilities"}


def test_money_market_fund_is_not_expanded(
    money_market_file: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    A bond fund is rejected instead of expanded into its equity sliver.

    Expanding it used to attribute the whole position to a single
    meaningless holding weighing 0.04%, silently erasing that position
    from every breakdown.
    """

    parser = XtrackersParser()

    with caplog.at_level(logging.WARNING):
        holdings = parser.parse(money_market_file, ISIN)

    assert holdings == []
    assert "no es un fondo de renta variable" in caplog.text
