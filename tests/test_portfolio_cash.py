# Standard libraries
from pathlib import Path

# 3pps
import pytest
from openpyxl import Workbook

# Own modules
from degiro_tools.parsing import parse_portfolio_cash, parse_portfolio_xlsx

CASH_EUR = 1682.95
EXPECTED_POSITIONS = 2

PORTFOLIO_HEADER: list[str | None] = [
    "Producto",
    "Symbol/ISIN",
    "Cantidad",
    "Precio de",
    "Valor local",
    None,
    "Valor en EUR",
]


@pytest.fixture
def portfolio_file(tmp_path: Path) -> Path:
    """
    Write a Degiro Portfolio.xlsx fixture with a cash row.

    Degiro reports cash as a row with neither ISIN nor quantity, which
    the position parser must keep discarding.
    """

    rows: list[list[object]] = [
        [
            "CASH & CASH FUND & FTX CASH (EUR)",
            None,
            None,
            None,
            "EUR",
            CASH_EUR,
            CASH_EUR,
        ],
        [
            "ISHARES CORE MSCI WORLD",
            "IE00B4L5Y983",
            117,
            124.855,
            "EUR",
            14608.0,
            14608.0,
        ],
        [
            "VANGUARD FTSE DEVELOPED EUROPE",
            "IE00BK5BQX27",
            35,
            60.61,
            "EUR",
            2121.35,
            2121.35,
        ],
    ]

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(PORTFOLIO_HEADER)
    for row in rows:
        sheet.append(row)

    path = tmp_path / "Portfolio.xlsx"
    workbook.save(path)

    return path


def test_positions_exclude_the_cash_row(portfolio_file: Path) -> None:
    """
    The cash row is not a position and stays out of the holdings flow.

    It carries no ISIN, so pricing or looking it through is impossible.
    """

    df = parse_portfolio_xlsx(portfolio_file)

    assert df.height == EXPECTED_POSITIONS
    assert "IE00B4L5Y983" in df["Symbol/ISIN"].to_list()


def test_cash_balance_is_read_separately(portfolio_file: Path) -> None:
    """
    The cash balance is recovered so the report can disclose it.

    Dropping it silently made the valuation understate the account
    total and overstate every position weight.
    """

    assert parse_portfolio_cash(portfolio_file) == pytest.approx(CASH_EUR)


def test_cash_is_zero_without_a_value_column(tmp_path: Path) -> None:
    """
    An export without the EUR value column yields no cash.

    The parser must degrade gracefully instead of raising.
    """

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Producto", "Symbol/ISIN", "Cantidad"])
    sheet.append(["CASH", None, None])
    path = tmp_path / "NoValue.xlsx"
    workbook.save(path)

    assert parse_portfolio_cash(path) == 0.0
