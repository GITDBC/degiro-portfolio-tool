# Standard libraries
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

# 3pps
import pytest

# Own modules
from degiro_tools import Operation, OperationType, parse_account_xlsx
from degiro_tools.parsing.account_parser import amount_to_decimal, quantity_to_int
from tests.conftest import account_row, write_account_xlsx

EXPECTED_OPS_COUNT = 4
EXPECTED_EUR_BUY_QUANTITY = 10
EXPECTED_USD_BUY_QUANTITY = 10
EXPECTED_SELL_QUANTITY = 15
EXPECTED_PARTIAL_OPS = 2
PARTIAL_TOTAL_SHARES = 149
PARTIAL_MAJOR_SHARES = 143
PARTIAL_MINOR_SHARES = 6
THOUSANDS_PARTIAL_MAJOR_SHARES = 1500
THOUSANDS_PARTIAL_MINOR_SHARES = 500
THOUSANDS_PARTIAL_TOTAL_SHARES = 2000

ParsedAccount = tuple[list[Operation], Decimal]


def sample_rows() -> list[list[object]]:
    """
    Build rows mirroring the legacy CSV fixture (4 operations).

    Returns:
        Account-statement rows: one EUR buy, two USD buys, one USD
        sell, and one connectivity fee.
    """

    return [
        # USD sell via currency exchange (EUR leg is "Retirada").
        account_row(
            "Ingreso Cambio de Divisa",
            currency="USD",
            amount=450.0,
            isin="US0000000001",
            order_id="ORD-SELL-USD",
            date_str="10-06-2026",
        ),
        account_row(
            "Retirada Cambio de Divisa",
            currency="EUR",
            amount=450.0,
            isin="US0000000001",
            order_id="ORD-SELL-USD",
            date_str="10-06-2026",
        ),
        account_row(
            "Costes de transacción y/o externos de DEGIRO",
            currency="EUR",
            amount=-1.0,
            isin="US0000000001",
            order_id="ORD-SELL-USD",
            date_str="10-06-2026",
        ),
        account_row(
            "Venta 15 Fake USD ADR@30 USD (US0000000001)",
            currency="USD",
            amount=450.0,
            isin="US0000000001",
            order_id="ORD-SELL-USD",
            date_str="10-06-2026",
        ),
        # USD buy (second) via currency exchange (EUR leg is "Ingreso").
        account_row(
            "Retirada Cambio de Divisa",
            currency="USD",
            amount=-250.0,
            isin="US0000000001",
            order_id="ORD-BUY-USD2",
            date_str="05-03-2026",
        ),
        account_row(
            "Ingreso Cambio de Divisa",
            currency="EUR",
            amount=-250.0,
            isin="US0000000001",
            order_id="ORD-BUY-USD2",
            date_str="05-03-2026",
        ),
        account_row(
            "Costes de transacción y/o externos de DEGIRO",
            currency="EUR",
            amount=-1.0,
            isin="US0000000001",
            order_id="ORD-BUY-USD2",
            date_str="05-03-2026",
        ),
        account_row(
            "Compra 10 Fake USD ADR@25 USD (US0000000001)",
            currency="USD",
            amount=-250.0,
            isin="US0000000001",
            order_id="ORD-BUY-USD2",
            date_str="05-03-2026",
        ),
        # USD buy (first) via currency exchange.
        account_row(
            "Retirada Cambio de Divisa",
            currency="USD",
            amount=-200.0,
            isin="US0000000001",
            order_id="ORD-BUY-USD1",
            date_str="15-02-2026",
            time="09:00",
        ),
        account_row(
            "Ingreso Cambio de Divisa",
            currency="EUR",
            amount=-200.0,
            isin="US0000000001",
            order_id="ORD-BUY-USD1",
            date_str="15-02-2026",
            time="09:00",
        ),
        account_row(
            "Costes de transacción y/o externos de DEGIRO",
            currency="EUR",
            amount=-1.0,
            isin="US0000000001",
            order_id="ORD-BUY-USD1",
            date_str="15-02-2026",
            time="09:00",
        ),
        account_row(
            "Compra 10 Fake USD ADR@20 USD (US0000000001)",
            currency="USD",
            amount=-200.0,
            isin="US0000000001",
            order_id="ORD-BUY-USD1",
            date_str="15-02-2026",
            time="09:00",
        ),
        # Connectivity fee (no ISIN, no Order ID).
        account_row(
            "Comisión de conectividad con el mercado (XETRA) 2025",
            currency="EUR",
            amount=-2.5,
            date_str="10-02-2026",
            time="08:00",
        ),
        # Direct EUR buy.
        account_row(
            "Costes de transacción y/o externos de DEGIRO",
            currency="EUR",
            amount=-1.0,
            isin="IE0000000099",
            order_id="ORD-BUY-EUR",
            date_str="01-02-2026",
            time="08:00",
        ),
        account_row(
            "Compra 10 Fake EUR ETF@10 EUR (IE0000000099)",
            currency="EUR",
            amount=-100.0,
            isin="IE0000000099",
            order_id="ORD-BUY-EUR",
            date_str="01-02-2026",
            time="08:00",
        ),
    ]


@pytest.fixture(scope="module")
def parsed(tmp_path_factory: pytest.TempPathFactory) -> ParsedAccount:
    """
    Parse the sample XLSX once per module and reuse the result.

    Returns:
        Tuple ``(ops, connectivity_fees)`` from ``parse_account_xlsx``.
    """

    path = tmp_path_factory.mktemp("account") / "Account.xlsx"
    write_account_xlsx(path, sample_rows())

    statement = parse_account_xlsx(path)

    return statement.operations, statement.connectivity_fees


def test_parse_counts_operations(parsed: ParsedAccount) -> None:
    """
    Verify the parser detects 4 operations (1 EUR + 2 USD buy + 1 sell).
    """

    ops, _ = parsed

    assert len(ops) == EXPECTED_OPS_COUNT


def test_parse_chronological_order(parsed: ParsedAccount) -> None:
    """
    Verify the operations are returned sorted by date and time.
    """

    ops, _ = parsed
    dates = [o.date for o in ops]

    assert dates == sorted(dates)
    assert ops[0].date == date(2026, 2, 1)
    assert ops[-1].date == date(2026, 6, 10)


def test_parse_direct_eur_buy(parsed: ParsedAccount) -> None:
    """
    Verify a EUR buy is parsed with direct amount and fee.
    """

    ops, _ = parsed
    eur_buy = next(o for o in ops if o.isin == "IE0000000099")

    assert eur_buy.operation_type == OperationType.BUY
    assert eur_buy.quantity == EXPECTED_EUR_BUY_QUANTITY
    assert eur_buy.amount_eur == Decimal("-100.00")
    assert eur_buy.fee_eur == Decimal("-1.00")


def test_parse_usd_buy_uses_real_eur_amount(parsed: ParsedAccount) -> None:
    """
    Verify a USD buy uses the EUR amount from the currency exchange.
    """

    ops, _ = parsed
    usd_buy = next(
        o
        for o in ops
        if o.isin == "US0000000001"
        and o.operation_type == OperationType.BUY
        and o.quantity == EXPECTED_USD_BUY_QUANTITY
        and o.date == date(2026, 2, 15)
    )

    assert usd_buy.amount_eur == Decimal("-200.00")
    assert usd_buy.fee_eur == Decimal("-1.00")


def test_parse_usd_sell(parsed: ParsedAccount) -> None:
    """
    Verify a USD sell is parsed with its EUR amount and fee.
    """

    ops, _ = parsed
    sell = next(o for o in ops if o.operation_type == OperationType.SELL)

    assert sell.isin == "US0000000001"
    assert sell.quantity == EXPECTED_SELL_QUANTITY
    assert sell.amount_eur == Decimal("450.00")
    assert sell.fee_eur == Decimal("-1.00")


def test_parse_connectivity_fee_separate(parsed: ParsedAccount) -> None:
    """
    Verify connectivity fees are aggregated apart from operations.
    """

    _, connectivity_fees = parsed

    assert connectivity_fees == Decimal("2.50")


def test_parse_amount_exact_precision(tmp_path: Path) -> None:
    """
    Verify an XLSX amount keeps exact precision (no European stripping).
    """

    rows = [
        account_row(
            "Compra 1 Foo@1734.6 EUR (ESQUOTE000)",
            currency="EUR",
            amount=1734.6,
            isin="ESQUOTE000",
            order_id="ORD-PREC",
        )
    ]
    path = write_account_xlsx(tmp_path / "prec.xlsx", rows)

    ops = parse_account_xlsx(path).operations

    assert ops[0].amount_eur == Decimal("1734.6")
    # The European parser would strip the dot and yield 17346.
    assert ops[0].amount_eur != Decimal("17346")


def test_parse_subcent_amount_precision(tmp_path: Path) -> None:
    """
    Verify sub-cent XLSX amounts convert without float artifacts.
    """

    rows = [
        account_row(
            "Compra 1 Foo@1 EUR (ESQUOTE000)",
            currency="EUR",
            amount=-3577.7213,
            isin="ESQUOTE000",
            order_id="ORD-SUBCENT",
        )
    ]
    path = write_account_xlsx(tmp_path / "subcent.xlsx", rows)

    ops = parse_account_xlsx(path).operations

    assert ops[0].amount_eur == Decimal("-3577.7213")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", Decimal(0)), ("3166.05", Decimal("3166.05"))],
    ids=["empty-cell-is-zero", "dot-decimal-text"],
)
def test_amount_to_decimal_converts_valid_text(raw: str, expected: Decimal) -> None:
    """
    Verify an amount cell converts to the exact expected Decimal.

    A blank cell is zero, and dot-decimal text keeps its full precision
    instead of being read as European notation.
    """

    assert amount_to_decimal(raw) == expected


def test_amount_to_decimal_invalid_input_raises_valueerror() -> None:
    """
    Verify a non-numeric amount raises a chained ``ValueError``.
    """

    with pytest.raises(ValueError, match="Importe no numérico"):
        amount_to_decimal("not-a-number")


def test_parse_multiple_fee_rows_same_order(tmp_path: Path) -> None:
    """
    Verify several fee rows under one order are summed exactly.
    """

    rows = [
        account_row(
            "Compra 10 Foo@10 EUR (ESMULTI0001)",
            currency="EUR",
            amount=-100.0,
            isin="ESMULTI0001",
            order_id="ORD-FEES",
        ),
        account_row(
            "Spanish Transaction Tax",
            currency="EUR",
            amount=-1.5,
            isin="ESMULTI0001",
            order_id="ORD-FEES",
        ),
        account_row(
            "Spanish Transaction Tax",
            currency="EUR",
            amount=-2.5,
            isin="ESMULTI0001",
            order_id="ORD-FEES",
        ),
        account_row(
            "Costes de transacción y/o externos de DEGIRO",
            currency="EUR",
            amount=-1.0,
            isin="ESMULTI0001",
            order_id="ORD-FEES",
        ),
    ]
    path = write_account_xlsx(tmp_path / "fees.xlsx", rows)

    ops = parse_account_xlsx(path).operations

    assert len(ops) == 1
    assert ops[0].fee_eur == Decimal("-5.0")


def test_parse_partial_executions_proportional_fees(tmp_path: Path) -> None:
    """
    Verify partial executions split fees proportionally by quantity.
    """

    rows = [
        account_row(
            "Compra 143 Bar@2 EUR (ESPARTIAL01)",
            currency="EUR",
            amount=-286.0,
            isin="ESPARTIAL01",
            order_id="ORD-PARTIAL",
        ),
        account_row(
            "Compra 6 Bar@2 EUR (ESPARTIAL01)",
            currency="EUR",
            amount=-12.0,
            isin="ESPARTIAL01",
            order_id="ORD-PARTIAL",
        ),
        account_row(
            "Costes de transacción y/o externos de DEGIRO",
            currency="EUR",
            amount=-4.82,
            isin="ESPARTIAL01",
            order_id="ORD-PARTIAL",
        ),
    ]
    path = write_account_xlsx(tmp_path / "partial.xlsx", rows)

    ops = parse_account_xlsx(path).operations

    assert len(ops) == EXPECTED_PARTIAL_OPS
    by_qty = {o.quantity: o for o in ops}
    total_fee = Decimal("-4.82")

    major = by_qty[PARTIAL_MAJOR_SHARES]
    minor = by_qty[PARTIAL_MINOR_SHARES]

    assert major.amount_eur == Decimal("-286.0")
    assert minor.amount_eur == Decimal("-12.0")
    assert major.fee_eur == total_fee * Decimal(PARTIAL_MAJOR_SHARES) / Decimal(
        PARTIAL_TOTAL_SHARES
    )
    assert minor.fee_eur == total_fee * Decimal(PARTIAL_MINOR_SHARES) / Decimal(
        PARTIAL_TOTAL_SHARES
    )
    # Fees reconstruct exactly to the original total.
    assert major.fee_eur + minor.fee_eur == total_fee


def test_parse_row_wrap_orphan_discarded(tmp_path: Path) -> None:
    """
    Verify a description-wrap row without ISIN/Order ID is discarded.
    """

    rows = [
        account_row(
            "Compra 10 Foo@10 EUR (ESORPHAN001)",
            currency="EUR",
            amount=-100.0,
            isin="ESORPHAN001",
            order_id="ORD-WRAP",
        ),
        # Wrap artifact: only a description, no ISIN and no Order ID.
        account_row("Bank: 985,66 EUR"),
    ]
    path = write_account_xlsx(tmp_path / "wrap.xlsx", rows)

    ops = parse_account_xlsx(path).operations

    assert len(ops) == 1
    assert ops[0].isin == "ESORPHAN001"


def test_parse_single_header_not_double_counted(tmp_path: Path) -> None:
    """
    Verify the worksheet header is not parsed as an operation.
    """

    rows = [
        account_row(
            "Compra 10 Foo@10 EUR (ESHEADER001)",
            currency="EUR",
            amount=-100.0,
            isin="ESHEADER001",
            order_id="ORD-HDR",
        )
    ]
    path = write_account_xlsx(tmp_path / "header.xlsx", rows)

    ops = parse_account_xlsx(path).operations

    assert len(ops) == 1


def test_usd_operation_without_currency_exchange_is_excluded(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    A USD trade with no exchange row steps aside instead of aborting.

    That is what a Manual FX account looks like: the order settles
    against a balance already held in the currency, so the statement
    never says what it cost in euros. Raising threw away a whole report
    over one order, and guessing a rate would invent a figure for a tax
    return, so the ISIN is reported and left out.
    """

    rows = [
        account_row(
            "Compra 10 Fake@10 USD (US0000000099)",
            currency="USD",
            amount=-100.0,
            isin="US0000000099",
            order_id="ORD-1",
        ),
        account_row(
            "Compra 5 Real@10 EUR (ES0000000001)",
            currency="EUR",
            amount=-50.0,
            isin="ES0000000001",
            order_id="ORD-2",
        ),
    ]
    path = write_account_xlsx(tmp_path / "nofx.xlsx", rows)

    with caplog.at_level(logging.WARNING):
        statement = parse_account_xlsx(path)

    # The euro order survives; only the unvaluable ISIN is dropped.
    assert [operation.isin for operation in statement.operations] == ["ES0000000001"]
    assert [item.isin for item in statement.unvalued_operations] == ["US0000000099"]
    assert "sin contravalor en EUR" in caplog.text


def test_unvalued_trade_drops_the_whole_isin(tmp_path: Path) -> None:
    """
    One unvaluable order excludes every operation of its ISIN.

    Keeping the euro half would match sales against lots that are not
    all there, publishing a gain computed on an incomplete cost.
    """

    rows = [
        account_row(
            "Compra 10 Fake@10 EUR (US0000000099)",
            currency="EUR",
            amount=-100.0,
            isin="US0000000099",
            order_id="ORD-EUR",
        ),
        account_row(
            "Compra 10 Fake@10 USD (US0000000099)",
            currency="USD",
            amount=-100.0,
            isin="US0000000099",
            order_id="ORD-USD",
        ),
    ]
    path = write_account_xlsx(tmp_path / "mixed.xlsx", rows)

    statement = parse_account_xlsx(path)

    assert statement.operations == []
    assert len(statement.unvalued_operations) == 1


def test_parse_orphan_row_without_isin_or_order(tmp_path: Path) -> None:
    """
    Verify rows without ISIN or Order ID are discarded.
    """

    rows = [account_row("Texto sin clasificar", currency="EUR", amount=-1.0)]
    path = write_account_xlsx(tmp_path / "orphan.xlsx", rows)

    statement = parse_account_xlsx(path)
    ops, fees = statement.operations, statement.connectivity_fees

    assert ops == []
    assert fees == Decimal(0)


def test_parse_group_without_buy_sell_header(tmp_path: Path) -> None:
    """
    Verify a group without a main Buy/Sell row is discarded.
    """

    rows = [
        account_row(
            "Costes de transacción",
            currency="EUR",
            amount=-1.0,
            isin="IE0000000077",
            order_id="ORD-X",
        )
    ]
    path = write_account_xlsx(tmp_path / "feeonly.xlsx", rows)

    ops = parse_account_xlsx(path).operations

    assert ops == []


def test_parse_header_only(tmp_path: Path) -> None:
    """
    Verify an empty statement (header only) returns null lists/decimals.
    """

    path = write_account_xlsx(tmp_path / "empty.xlsx", [])

    statement = parse_account_xlsx(path)
    ops, fees = statement.operations, statement.connectivity_fees

    assert ops == []
    assert fees == Decimal(0)


def test_parse_malformed_amount_row_is_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    Verify a row with an unparseable amount is skipped with a warning.
    """

    rows = [
        account_row(
            "Compra 1 Foo@1 EUR (ESMALFORM01)",
            currency="EUR",
            amount="N/A",
            isin="ESMALFORM01",
            order_id="ORD-MAL",
        )
    ]
    path = write_account_xlsx(tmp_path / "malformed.xlsx", rows)

    with caplog.at_level(logging.WARNING):
        statement = parse_account_xlsx(path)
    ops, fees = statement.operations, statement.connectivity_fees

    assert ops == []
    assert fees == Decimal(0)
    assert "importe inválido" in caplog.text.lower()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10", 10),
        ("1.081", 1081),
        ("2.500", 2500),
        ("1234", 1234),
        ("1.234.567", 1234567),
    ],
    ids=[
        "plain-two-digits",
        "thousands-separated",
        "thousands-separated-round",
        "four-digits-unseparated",
        "millions-separated",
    ],
)
def test_quantity_to_int_reads_spanish_thousands(raw: str, expected: int) -> None:
    """
    Verify the quantity converts with the dot read as a separator.

    Degiro writes the quantity in the Spanish locale, so "1.081" is
    1081 units. The conversion removes the separators explicitly and
    never consults the environment locale.
    """

    assert quantity_to_int(raw) == expected


@pytest.mark.parametrize(
    ("description", "amount", "expected_quantity"),
    [
        (
            "Compra 1.081 Vanguard FTSE Global All-Cap UCITS ETF USD "
            "Acc@4,3995 EUR (IE000VAHT5T0)",
            -4755.8595,
            1081,
        ),
        (
            "Compra 2.500 Vanguard FTSE Global All-Cap UCITS ETF USD "
            "Acc@4,269 EUR (IE000VAHT5T0)",
            -10672.5,
            2500,
        ),
        ("Compra 10 Fake EUR ETF@10 EUR (IE000VAHT5T0)", -100.0, 10),
        ("Compra 1234 Fake EUR ETF@1 EUR (IE000VAHT5T0)", -1234.0, 1234),
        ("Compra 1.234.567 Fake EUR ETF@1 EUR (IE000VAHT5T0)", -1234567.0, 1234567),
        ("Venta 1.500 Fake EUR ETF@2 EUR (IE000VAHT5T0)", 3000.0, 1500),
    ],
    ids=[
        "buy-thousands-separated",
        "buy-thousands-separated-round",
        "buy-plain-quantity",
        "buy-four-digits-unseparated",
        "buy-millions-separated",
        "sell-thousands-separated",
    ],
)
def test_parse_quantity_with_thousands_separator(
    tmp_path: Path, description: str, amount: float, expected_quantity: int
) -> None:
    """
    Verify a quantity of 1000 units or more is parsed, not discarded.

    The quantity used to be matched as a bare run of digits, so every
    operation Degiro wrote with a thousands separator failed to parse
    and left the fiscal report without its cost of acquisition.
    """

    rows = [
        account_row(
            description,
            currency="EUR",
            amount=amount,
            isin="IE000VAHT5T0",
            order_id="ORD-QTY",
        )
    ]
    path = write_account_xlsx(tmp_path / "quantity.xlsx", rows)

    ops = parse_account_xlsx(path).operations

    assert len(ops) == 1
    assert ops[0].quantity == expected_quantity
    assert ops[0].amount_eur == Decimal(str(amount))


def test_parse_partial_executions_with_thousands_quantities(tmp_path: Path) -> None:
    """
    Verify separated quantities split the fees by their real units.

    Reading "1.500" as 1 would distribute the fees of a partial order
    by the wrong weights even once the row itself parsed. The two
    tranches are chosen so the split is exact in decimal, which is what
    makes the reconstruction of the original total meaningful.
    """

    rows = [
        account_row(
            "Compra 1.500 Bar@2 EUR (ESPARTIAL02)",
            currency="EUR",
            amount=-3000.0,
            isin="ESPARTIAL02",
            order_id="ORD-PARTIAL-K",
        ),
        account_row(
            "Compra 500 Bar@2 EUR (ESPARTIAL02)",
            currency="EUR",
            amount=-1000.0,
            isin="ESPARTIAL02",
            order_id="ORD-PARTIAL-K",
        ),
        account_row(
            "Costes de transacción y/o externos de DEGIRO",
            currency="EUR",
            amount=-4.0,
            isin="ESPARTIAL02",
            order_id="ORD-PARTIAL-K",
        ),
    ]
    path = write_account_xlsx(tmp_path / "partial_k.xlsx", rows)

    ops = parse_account_xlsx(path).operations

    assert len(ops) == EXPECTED_PARTIAL_OPS
    by_qty = {o.quantity: o for o in ops}
    total_fee = Decimal("-4.0")

    major = by_qty[THOUSANDS_PARTIAL_MAJOR_SHARES]
    minor = by_qty[THOUSANDS_PARTIAL_MINOR_SHARES]

    assert major.fee_eur == total_fee * Decimal(
        THOUSANDS_PARTIAL_MAJOR_SHARES
    ) / Decimal(THOUSANDS_PARTIAL_TOTAL_SHARES)
    assert minor.fee_eur == total_fee * Decimal(
        THOUSANDS_PARTIAL_MINOR_SHARES
    ) / Decimal(THOUSANDS_PARTIAL_TOTAL_SHARES)
    assert major.fee_eur + minor.fee_eur == total_fee


def test_parse_fractional_quantity_is_reported_not_rounded(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    Verify a fractional quantity is reported instead of rounded.

    ``Operation.quantity`` holds whole units, so a row buying half a
    unit cannot be represented. Naming it in an error the user can act
    on beats rounding it in silence inside a tax return.
    """

    rows = [
        account_row(
            "Compra 0,5 Fondo Fraccionado@10 EUR (ESFRAC00001)",
            currency="EUR",
            amount=-5.0,
            isin="ESFRAC00001",
            order_id="ORD-FRAC",
        )
    ]
    path = write_account_xlsx(tmp_path / "fractional.xlsx", rows)

    with caplog.at_level(logging.ERROR):
        ops = parse_account_xlsx(path).operations

    assert ops == []
    assert "ilegible" in caplog.text
    assert "ESFRAC00001" in caplog.text


def test_parse_unreadable_row_still_reported_after_quantity_fix(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    Verify a description with no ``@price`` marker is still an error.

    Widening the quantity group must not widen what counts as a
    readable operation: a row that is genuinely unparseable has to keep
    reaching the error log instead of entering the report half-read.
    """

    rows = [
        account_row(
            "Compra 1.081 Vanguard FTSE Global All-Cap UCITS ETF USD Acc",
            currency="EUR",
            amount=-4755.8595,
            isin="IE000VAHT5T0",
            order_id="ORD-NOPRICE",
        )
    ]
    path = write_account_xlsx(tmp_path / "noprice.xlsx", rows)

    with caplog.at_level(logging.ERROR):
        ops = parse_account_xlsx(path).operations

    assert ops == []
    assert "ilegible" in caplog.text
    assert "NO se" in caplog.text
    assert any(record.levelname == "ERROR" for record in caplog.records)


def test_parse_decimal_comma_price_survives_quantity_fix(tmp_path: Path) -> None:
    """
    Verify the ``@4,3995`` price fragment keeps its decimal comma.

    The price group shares the dot with the quantity group but means
    the opposite thing, so the two notations have to stay separate.
    """

    rows = [
        account_row(
            "Compra 1.081 Vanguard FTSE Global All-Cap UCITS ETF USD "
            "Acc@4,3995 EUR (IE000VAHT5T0)",
            currency="EUR",
            amount=-4755.8595,
            isin="IE000VAHT5T0",
            order_id="ORD-PRICE",
        )
    ]
    path = write_account_xlsx(tmp_path / "price.xlsx", rows)

    ops = parse_account_xlsx(path).operations

    assert len(ops) == 1
    # 1081 units at 4,3995 EUR each reconstruct the exact consideration.
    assert ops[0].amount_eur == Decimal(ops[0].quantity) * Decimal("4.3995") * -1
