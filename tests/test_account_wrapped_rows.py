# Standard libraries
import logging
from decimal import Decimal
from pathlib import Path

# 3pps
import pytest

# Own modules
from degiro_tools import parse_account_xlsx
from tests.conftest import account_row, write_account_xlsx

WRAPPED_QUANTITY = 100
WRAPPED_AMOUNT = Decimal("1840.2")
WRAPPED_THOUSANDS_QUANTITY = 2500
WRAPPED_THOUSANDS_AMOUNT = Decimal("46005.0")


def wrapped_description_rows() -> list[list[object]]:
    """
    Build a sale whose description Degiro wrapped onto a second row.

    The first row keeps the leading text and the amount; the tail with
    the ``@price`` fragment lands on a row with every other column
    empty, exactly as the real export does for long product names.

    Returns:
        Account-statement rows: the fee, the truncated sale row and its
        continuation row.
    """

    continuation: list[object] = [None] * 12
    continuation[5] = "USD A@18,402 EUR (IE000I8KRLL9)"

    return [
        account_row(
            "Costes de transacción y/o externos de DEGIRO",
            currency="EUR",
            amount=-1,
            isin="IE000I8KRLL9",
            order_id="ORDER1",
        ),
        account_row(
            "Venta 100 iShares MSCI Global Semiconductors UCITS ETF ",
            currency="EUR",
            amount=float(WRAPPED_AMOUNT),
            isin="IE000I8KRLL9",
            order_id="ORDER1",
        ),
        continuation,
    ]


def test_wrapped_description_is_parsed_as_an_operation(tmp_path: Path) -> None:
    """
    A description wrapped onto a second row still yields an operation.

    The tail row carries the ``@price`` fragment the operation regex
    needs. Without folding it back the sale used to be dropped without
    trace, understating the reported capital gain.
    """

    path = write_account_xlsx(tmp_path / "wrapped.xlsx", wrapped_description_rows())

    ops = parse_account_xlsx(path).operations

    assert len(ops) == 1

    operation = ops[0]

    assert operation.isin == "IE000I8KRLL9"
    assert operation.quantity == WRAPPED_QUANTITY
    assert operation.amount_eur == WRAPPED_AMOUNT
    assert operation.fee_eur == Decimal(-1)
    assert operation.product == "iShares MSCI Global Semiconductors UCITS ETF USD A"


def test_unparseable_operation_row_is_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    An operation row the regex cannot parse is logged as an error.

    Silently dropping such a row removes a real trade from the fiscal
    report, so the discrepancy must be visible to the user.
    """

    rows = [
        account_row(
            "Venta 100 Something With No Price Marker",
            currency="EUR",
            amount=1840.2,
            isin="IE000I8KRLL9",
            order_id="ORDER1",
        ),
    ]
    path = write_account_xlsx(tmp_path / "broken.xlsx", rows)

    with caplog.at_level(logging.ERROR):
        statement = parse_account_xlsx(path)
        ops = statement.operations

    assert ops == []
    assert "ilegible" in caplog.text
    assert "NO se" in caplog.text
    assert "IE000I8KRLL9" in caplog.text
    assert any(r.levelname == "ERROR" for r in caplog.records)


def test_continuation_row_does_not_start_a_new_group(tmp_path: Path) -> None:
    """
    A continuation row never becomes an operation of its own.

    It carries no date, ISIN nor amount, so it must be folded into the
    preceding row instead of being grouped and parsed separately.
    """

    path = write_account_xlsx(tmp_path / "wrapped.xlsx", wrapped_description_rows())

    statement = parse_account_xlsx(path)
    ops, fees = statement.operations, statement.connectivity_fees

    assert len(ops) == 1
    assert fees == Decimal(0)


def test_wrapped_description_with_thousands_quantity(tmp_path: Path) -> None:
    """
    A wrapped row whose quantity carries a thousands separator parses.

    Both defects hit the same description: the tail row supplies the
    ``@price`` fragment and the head row the Spanish-notation quantity,
    so a real 2500-unit trade needs the two fixes at once to reach the
    fiscal report.
    """

    continuation: list[object] = [None] * 12
    continuation[5] = "USD A@18,402 EUR (IE000I8KRLL9)"

    rows = [
        account_row(
            "Venta 2.500 iShares MSCI Global Semiconductors UCITS ETF ",
            currency="EUR",
            amount=float(WRAPPED_THOUSANDS_AMOUNT),
            isin="IE000I8KRLL9",
            order_id="ORDER-K",
        ),
        continuation,
    ]
    path = write_account_xlsx(tmp_path / "wrapped_k.xlsx", rows)

    ops = parse_account_xlsx(path).operations

    assert len(ops) == 1
    assert ops[0].quantity == WRAPPED_THOUSANDS_QUANTITY
    assert ops[0].amount_eur == WRAPPED_THOUSANDS_AMOUNT
    assert ops[0].product == "iShares MSCI Global Semiconductors UCITS ETF USD A"
