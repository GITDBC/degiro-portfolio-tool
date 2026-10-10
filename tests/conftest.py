# Standard libraries
import io
from datetime import date
from decimal import Decimal
from pathlib import Path

# 3pps
import pytest
from openpyxl import Workbook
from rich.console import Console

# Own modules
from degiro_tools import Operation, OperationType
from degiro_tools.domain.models import (
    AccountStatement,
    CurrencyConversion,
    DividendIncome,
    OtherIncome,
    Sale,
    UnvaluedOperation,
)


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """
    Keep every test away from the real on-disk cache.

    Without this, a provider test that stubs a download to fail would
    quietly succeed by reading whatever the developer's last real run
    left behind, and the suite would depend on machine state.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        tmp_path: Temporary directory provided by pytest.

    Returns:
        None.
    """

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


# Header of the Degiro "Estado de cuenta" sheet. The two unlabeled
# columns (amount and balance) are written as empty cells, exactly as
# the real export does.
ACCOUNT_HEADER: list[str | None] = [
    "Fecha",
    "Hora",
    "Fecha valor",
    "Producto",
    "ISIN",
    "Descripción",
    "Tipo",
    "Variación",
    None,
    "Saldo",
    None,
    "ID Orden",
]


def account_row(  # noqa: PLR0913 - test row builder
    description: str,
    *,
    currency: str | None = None,
    amount: float | str | None = None,
    isin: str | None = None,
    order_id: str | None = None,
    date_str: str = "01-02-2026",
    time: str = "10:00",
    product: str = "PROD",
) -> list[object]:
    """
    Build a single Degiro account-statement row (12 columns).

    Amount is placed in the unlabeled column 8 as a native number,
    mirroring the real XLSX export.

    Args:
        description: Operation description (column 5).
        currency: Variation currency code (column 7).
        amount: Variation amount (column 8); native number or text.
        isin: ISIN identifier (column 4).
        order_id: Order ID (column 11).
        date_str: Operation date in ``dd-mm-yyyy`` format.
        time: Time in ``HH:MM`` format.
        product: Product name (column 3).

    Returns:
        List of twelve cell values ready to append to a worksheet.
    """

    return [
        date_str,
        time,
        date_str,
        product,
        isin,
        description,
        None,
        currency,
        amount,
        currency,
        0.0,
        order_id,
    ]


def write_account_xlsx(path: Path, rows: list[list[object]]) -> Path:
    """
    Write a Degiro Account.xlsx fixture with the given data rows.

    Args:
        path: Destination path for the workbook.
        rows: Data rows (each a list of twelve cell values).

    Returns:
        The path to the written workbook.
    """

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Estado de cuenta"
    sheet.append(ACCOUNT_HEADER)
    for row in rows:
        sheet.append(row)

    workbook.save(path)

    return path


def make_statement(  # noqa: PLR0913 - test builder
    *,
    operations: list[Operation] | None = None,
    dividends: list[DividendIncome] | None = None,
    connectivity_fees: Decimal = Decimal(0),
    interest_income: Decimal = Decimal(0),
    interest_expense: Decimal = Decimal(0),
    other_income: tuple[OtherIncome, ...] = (),
    currency_conversions: tuple[CurrencyConversion, ...] = (),
    unvalued_operations: tuple[UnvaluedOperation, ...] = (),
) -> AccountStatement:
    """
    Build an ``AccountStatement`` with only the parts a test cares about.

    Args:
        operations: Normalized operations of the statement.
        dividends: Dividends collected during the period.
        connectivity_fees: Absolute total of connectivity fees.
        interest_income: Credit interest collected.
        interest_expense: Debit interest charged.
        other_income: Promotions and rebates credited.
        currency_conversions: Exchanges with no trade attached.
        unvalued_operations: Trades with no euro consideration.

    Returns:
        The statement, with every unspecified concept left empty.
    """

    return AccountStatement(
        operations=operations if operations is not None else [],
        dividends=dividends if dividends is not None else [],
        connectivity_fees=connectivity_fees,
        interest_income=interest_income,
        interest_expense=interest_expense,
        other_income=other_income,
        currency_conversions=currency_conversions,
        unvalued_operations=unvalued_operations,
    )


def make(  # noqa: PLR0913 - test builder
    operation_type: OperationType,
    isin: str,
    *,
    quantity: int,
    amount: str,
    fee: str,
    day: int,
) -> Operation:
    """
    Build an ``Operation`` dated 2026-01-``day`` with Decimal money.
    """

    return Operation(
        date=date(2026, 1, day),
        time="10:00",
        isin=isin,
        product="TEST",
        operation_type=operation_type,
        quantity=quantity,
        amount_eur=Decimal(amount),
        fee_eur=Decimal(fee),
    )


def make_buy(
    isin: str, quantity: int, amount: str, fee: str, day: int = 1
) -> Operation:
    """
    Build a buy ``Operation`` for tests (date 2026-01-``day``).
    """

    return make(
        OperationType.BUY,
        isin,
        quantity=quantity,
        amount=amount,
        fee=fee,
        day=day,
    )


def make_sell(
    isin: str, quantity: int, amount: str, fee: str, day: int = 1
) -> Operation:
    """
    Build a sell ``Operation`` for tests (date 2026-01-``day``).
    """

    return make(
        OperationType.SELL,
        isin,
        quantity=quantity,
        amount=amount,
        fee=fee,
        day=day,
    )


def make_sale(  # noqa: PLR0913 - test builder
    year: int,
    gain_loss: str,
    *,
    month: int = 6,
    day: int = 15,
    isin: str = "ES0000000001",
    product: str = "PROD",
    quantity: int = 1,
    acquisition_cost: str = "100.00",
) -> Sale:
    """
    Build a ``Sale`` carrying the requested gain or loss.

    The transfer value is derived from the acquisition cost so the sale
    is always internally consistent.

    Args:
        year: Year of the sale.
        gain_loss: Gain or loss as a decimal string.
        month: Month of the sale.
        day: Day of the sale.
        isin: ISIN identifier.
        product: Descriptive product name.
        quantity: Units sold.
        acquisition_cost: FIFO acquisition cost as a decimal string.

    Returns:
        Sale with the given date, amounts and gain/loss.
    """

    gain = Decimal(gain_loss)
    cost = Decimal(acquisition_cost)

    return Sale(
        date=date(year, month, day),
        isin=isin,
        product=product,
        quantity=quantity,
        acquisition_cost=cost,
        transfer_value=cost + gain,
        gain_loss=gain,
    )


def make_dividend(year: int, gross: str, withholding: str = "0.00") -> DividendIncome:
    """
    Build a ``DividendIncome`` for a given year.

    Args:
        year: Year of the payment.
        gross: Gross amount as a decimal string.
        withholding: Withholding as a positive decimal string.

    Returns:
        DividendIncome with the given date and amounts.
    """

    return DividendIncome(
        date=date(year, 7, 10),
        isin="ES0000000001",
        product="PROD",
        gross_eur=Decimal(gross),
        withholding_eur=Decimal(withholding),
    )


def make_console() -> tuple[Console, io.StringIO]:
    """
    Create a colorless rich console captured in a StringIO.

    The width is pinned so the substring assertions of the report tests
    never depend on the terminal running them.

    Returns:
        The console and the buffer holding everything it renders.
    """

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, no_color=True, width=200)

    return console, buf
