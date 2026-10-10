# Standard libraries
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

# 3pps
import pytest

# Own modules
from degiro_tools import parse_account_xlsx
from degiro_tools.domain.models import StatementRowKind
from degiro_tools.parsing.account_parser import classify_description
from tests.conftest import account_row, write_account_xlsx

# A buy that needs no exchange row, used as the surrounding noise a real
# statement always has.
EUR_BUY: list[object] = account_row(
    "Compra 10 Foo@10 EUR (ES0000000001)",
    currency="EUR",
    amount=-100.0,
    isin="ES0000000001",
    order_id="ORD-EUR",
)


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Compra 10 Foo@10 EUR (ES0000000001)", StatementRowKind.OPERATION),
        ("Venta 10 Foo@10 EUR (ES0000000001)", StatementRowKind.OPERATION),
        ("Costes de transacción y/o externos de DEGIRO", StatementRowKind.FEE),
        ("Spanish Transaction Tax", StatementRowKind.FEE),
        (
            "Comisión de conectividad con el mercado 2026 (Nasdaq - NDQ)",
            StatementRowKind.CONNECTIVITY_FEE,
        ),
        ("Ingreso Cambio de Divisa", StatementRowKind.CURRENCY_EXCHANGE),
        ("Retirada Cambio de Divisa", StatementRowKind.CURRENCY_EXCHANGE),
        ("Dividendo", StatementRowKind.DIVIDEND),
        ("Dividendo (ajuste)", StatementRowKind.DIVIDEND),
        ("Reparto de dividendo", StatementRowKind.DIVIDEND),
        ("Retención del dividendo", StatementRowKind.DIVIDEND_WITHHOLDING),
        ("Impuesto sobre dividendo", StatementRowKind.DIVIDEND_WITHHOLDING),
        ("Flatex Interest Income", StatementRowKind.INTEREST),
        ("Promoción DEGIRO reembolso", StatementRowKind.OTHER_INCOME),
        ("Degiro Cash Sweep Transfer", StatementRowKind.CASH_MOVEMENT),
        ("flatex Deposit", StatementRowKind.CASH_MOVEMENT),
        (
            "Transferir a su Cuenta de Efectivo en flatexDEGIRO Bank: 100 EUR",
            StatementRowKind.CASH_MOVEMENT,
        ),
        ("Buy 10 Foo@10 EUR (ES0000000001)", StatementRowKind.UNKNOWN),
        ("Algo que Degiro invente mañana", StatementRowKind.UNKNOWN),
    ],
)
def test_every_description_lands_on_a_kind(
    description: str, expected: StatementRowKind
) -> None:
    """
    Each description of a real statement resolves to what it means.

    The narrow concepts win over the broad ones: a withholding names a
    dividend, a connectivity fee is worded like a fee, and an exchange
    leg like a deposit.
    """

    assert classify_description(description) is expected


def test_unrecognized_rows_are_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    A description the parser does not know is warned about.

    Dropping it in silence is how an export in another language yields a
    report that declares nothing and looks like a quiet year.
    """

    rows = [
        EUR_BUY,
        account_row("Movimiento inventado", currency="EUR", amount=42.0),
    ]
    path = write_account_xlsx(tmp_path / "unknown.xlsx", rows)

    with caplog.at_level(logging.WARNING):
        statement = parse_account_xlsx(path)

    assert len(statement.unclassified_rows) == 1
    assert statement.unclassified_rows[0].description == "Movimiento inventado"
    assert "no se han podido clasificar" in caplog.text
    assert "1 fila(s)" in caplog.text
    warning_records = [
        record for record in caplog.records if record.levelname == "WARNING"
    ]
    assert (
        sum(
            "no se han podido clasificar" in record.message
            for record in warning_records
        )
        == 1
    ), "the unclassified count must be logged once, not duplicated per row"


def test_a_statement_with_nothing_recognizable_says_so(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    An unparseable export warns instead of reporting an empty year.

    This is what an English statement looks like, and an empty report is
    indistinguishable from a year with no activity.
    """

    rows = [
        account_row(
            "Buy 10 Foo@10 EUR (ES0000000001)",
            currency="EUR",
            amount=-100.0,
            isin="ES0000000001",
            order_id="ORD-1",
        ),
        account_row("Dividend", currency="EUR", amount=9.0, isin="ES0118594417"),
    ]
    path = write_account_xlsx(tmp_path / "english.xlsx", rows)

    with caplog.at_level(logging.WARNING):
        statement = parse_account_xlsx(path)

    assert statement.operations == []
    assert statement.dividends == []
    # Belonging to an order is not being read: both rows are named.
    assert len(statement.unclassified_rows) == 2
    assert "no se han podido clasificar" in caplog.text
    assert "No se ha reconocido ninguna compra" in caplog.text


def test_interest_is_collected_by_sign(tmp_path: Path) -> None:
    """
    Credit interest is income and debit interest is a cost.

    Both used to be discarded, so real capital income never reached the
    savings base.
    """

    rows = [
        EUR_BUY,
        account_row("Flatex Interest Income", currency="EUR", amount=4.5),
        account_row("Flatex Interest Income", currency="EUR", amount=-1.25),
    ]
    path = write_account_xlsx(tmp_path / "interest.xlsx", rows)

    statement = parse_account_xlsx(path)

    assert statement.interest_income == Decimal("4.5")
    assert statement.interest_expense == Decimal("1.25")


def test_promotions_are_collected_verbatim(tmp_path: Path) -> None:
    """
    A promotion is kept with its own wording, not integrated.

    What it is depends on why the broker paid it, which the statement
    does not say, so the report shows it and lets the reader decide.
    """

    rows = [
        EUR_BUY,
        account_row(
            "Promoción DEGIRO reembolso",
            currency="EUR",
            amount=27.0,
            date_str="03-04-2026",
        ),
    ]
    path = write_account_xlsx(tmp_path / "promo.xlsx", rows)

    statement = parse_account_xlsx(path)

    assert len(statement.other_income) == 1
    assert statement.other_income[0].amount_eur == Decimal("27")
    assert statement.other_income[0].date == date(2026, 4, 3)
    assert statement.other_income[0].description == "Promoción DEGIRO reembolso"


def test_a_conversion_with_no_trade_is_reported(tmp_path: Path) -> None:
    """
    A bare currency swap travels apart, for casilla 1634.

    It alters the composition of the patrimony on its own (art. 37.1.h
    LIRPF) and has no acquisition or transfer value to hide inside.
    """

    rows = [
        EUR_BUY,
        account_row(
            "Ingreso Cambio de Divisa",
            currency="EUR",
            amount=0.01,
            date_str="16-01-2026",
            time="06:35",
        ),
        account_row(
            "Retirada Cambio de Divisa",
            currency="USD",
            amount=-0.01,
            date_str="16-01-2026",
            time="06:35",
        ),
    ]
    path = write_account_xlsx(tmp_path / "loose_fx.xlsx", rows)

    statement = parse_account_xlsx(path)

    assert len(statement.currency_conversions) == 1

    conversion = statement.currency_conversions[0]

    assert conversion.date == date(2026, 1, 16)
    assert conversion.eur_amount == Decimal("0.01")
    assert conversion.foreign_amount == Decimal("-0.01")
    assert conversion.foreign_currency == "USD"


def test_a_conversion_that_settles_a_trade_is_not_reported_apart(
    tmp_path: Path,
) -> None:
    """
    The exchange of a purchase is already inside its acquisition value.

    Reporting it again as a standalone swap would invite declaring the
    same alteration twice.
    """

    rows = [
        account_row(
            "Compra 1 Fake@18.92 USD (US0000000099)",
            currency="USD",
            amount=-18.92,
            isin="US0000000099",
            order_id="ORD-FX",
        ),
        account_row(
            "Ingreso Cambio de Divisa",
            currency="USD",
            amount=18.92,
            isin="US0000000099",
            order_id="ORD-FX",
        ),
        account_row(
            "Retirada Cambio de Divisa",
            currency="EUR",
            amount=-16.24,
            isin="US0000000099",
            order_id="ORD-FX",
        ),
    ]
    path = write_account_xlsx(tmp_path / "trade_fx.xlsx", rows)

    statement = parse_account_xlsx(path)

    assert statement.currency_conversions == ()
    assert statement.operations[0].amount_eur == Decimal("-16.24")
