# Standard libraries
from collections import defaultdict, deque
from dataclasses import replace
from datetime import date
from decimal import Decimal

# 3pps
import pytest

# Own modules
from degiro_tools import (
    AcquisitionMatch,
    DividendIncome,
    Lot,
    Operation,
    OperationType,
    Sale,
    build_report_data,
    print_report,
)
from degiro_tools.domain.models import (
    CurrencyConversion,
    OtherIncome,
    UnvaluedOperation,
)

# Own modules
from .conftest import make_buy, make_console, make_sale, make_statement


@pytest.fixture
def simple_sales() -> list[Sale]:
    """
    Return a synthetic sale with a positive gain/loss of 50 EUR.
    """

    return [
        make_sale(
            2026,
            "50.00",
            month=3,
            day=1,
            isin="IE0000000099",
            product="TEST",
            quantity=10,
        )
    ]


@pytest.fixture
def losing_sales() -> list[Sale]:
    """
    Return a synthetic sale closing at a 50 EUR loss.
    """

    return [
        make_sale(
            2026,
            "-50.00",
            month=3,
            day=1,
            isin="IE0000000099",
            product="TEST",
            quantity=10,
            acquisition_cost="200.00",
        )
    ]


@pytest.fixture
def one_pending_lot() -> dict[str, deque[Lot]]:
    """
    Portfolio with a single pending lot.
    """

    lots: dict[str, deque[Lot]] = defaultdict(deque)
    lots["IE0000000099"].append(
        Lot(quantity=5, unit_cost=Decimal("10.00"), date=date(2026, 1, 15))
    )
    return lots


def render(  # noqa: PLR0913 - test render helper
    sales: list[Sale],
    lots: dict[str, deque[Lot]],
    *,
    connectivity_fees: Decimal = Decimal(0),
    include_tax: bool = True,
    dividends: list[DividendIncome] | None = None,
    operations: list[Operation] | None = None,
) -> str:
    """
    Build the report aggregates and render them to a string.

    Args:
        sales: Sales matched by FIFO.
        lots: Pending lots per ISIN.
        connectivity_fees: Total connectivity fees for the period.
        include_tax: Whether to render the IRPF block.
        dividends: Dividends collected during the period.
        operations: Operations behind the sales, needed by the
            two-month rule.

    Returns:
        The rendered report as plain text.
    """

    console, buf = make_console()
    print_report(
        build_report_data(
            sales,
            lots,
            make_statement(
                operations=operations,
                dividends=dividends,
                connectivity_fees=connectivity_fees,
            ),
            include_tax=include_tax,
        ),
        console=console,
    )

    return buf.getvalue()


def test_print_report_includes_sections_with_tax(
    simple_sales: list[Sale],
    one_pending_lot: dict[str, deque[Lot]],
) -> None:
    """
    Verify that with tax all the key sections are printed.
    """

    out = render(simple_sales, one_pending_lot, connectivity_fees=Decimal("5.00"))

    assert "Ventas casadas" in out
    assert "Detalle para la declaración" in out
    assert "TOTAL GANANCIA/PÉRDIDA PATRIMONIAL" in out
    assert "50.00" in out
    assert "Estimación cuota IRPF" in out
    assert "CUOTA ESTIMADA TOTAL" in out
    assert "9.50" in out
    assert "RENTABILIDAD NETA REAL" in out
    assert "Cartera abierta" in out
    assert "IE0000000099" in out


def test_declaration_lines_show_the_acquisition_date(
    simple_sales: list[Sale],
) -> None:
    """
    The declaration block lists the acquisition date of each line.

    Without it the user cannot fill the return, which asks for the
    acquisition date of every transferred element.
    """

    sale = simple_sales[0]
    detailed = [
        replace(
            sale,
            matches=(
                AcquisitionMatch(
                    acquisition_date=date(2026, 1, 15),
                    quantity=10,
                    acquisition_cost=Decimal("100.00"),
                    transfer_value=Decimal("150.00"),
                ),
            ),
        )
    ]

    out = render(detailed, defaultdict(deque))

    assert "Adquis." in out
    assert "15-01-2026" in out
    assert "Valor t." in out


def test_a_loss_line_with_a_repurchase_raises_the_two_month_block(
    simple_sales: list[Sale],
) -> None:
    """
    A line in loss plus a purchase inside the window is reported.

    Art. 33.5.f LIRPF defers such a loss, so the report names the
    repurchase dates instead of leaving the deduction unquestioned.
    """

    sale = simple_sales[0]
    detailed = [
        replace(
            sale,
            matches=(
                AcquisitionMatch(
                    acquisition_date=date(2026, 1, 15),
                    quantity=5,
                    acquisition_cost=Decimal("80.00"),
                    transfer_value=Decimal("75.00"),
                ),
                AcquisitionMatch(
                    acquisition_date=date(2026, 2, 20),
                    quantity=5,
                    acquisition_cost=Decimal("20.00"),
                    transfer_value=Decimal("75.00"),
                ),
            ),
        )
    ]
    operations = [
        make_buy("IE0000000099", 5, "-80.00", "0.00", day=15),
        Operation(
            date=date(2026, 2, 20),
            time="10:00",
            isin="IE0000000099",
            product="TEST",
            operation_type=OperationType.BUY,
            quantity=5,
            amount_eur=Decimal("-20.00"),
            fee_eur=Decimal("0.00"),
        ),
    ]

    out = render(detailed, defaultdict(deque), operations=operations)

    assert "regla de los dos meses" in out
    assert "20-02-2026" in out
    assert "-5.00" in out


def test_a_loss_line_without_repurchases_is_left_alone(
    simple_sales: list[Sale],
) -> None:
    """
    No two-month block appears when nothing was bought in the window.

    The acquisition the loss comes from is the element transmitted, not
    a repurchase of it, so on its own it raises nothing.
    """

    sale = simple_sales[0]
    detailed = [
        replace(
            sale,
            matches=(
                AcquisitionMatch(
                    acquisition_date=date(2026, 1, 15),
                    quantity=10,
                    acquisition_cost=Decimal("160.00"),
                    transfer_value=Decimal("150.00"),
                ),
            ),
        )
    ]
    operations = [make_buy("IE0000000099", 10, "-160.00", "0.00", day=15)]

    out = render(detailed, defaultdict(deque), operations=operations)

    assert "regla de los dos meses" not in out


def test_a_foreign_withholding_is_not_subtracted_from_the_quota(
    simple_sales: list[Sale],
) -> None:
    """
    The report sends a foreign withholding to the deduction, not the quota.

    Treating it as a payment on account would refund a tax Spain never
    collected.
    """

    dividends = [
        DividendIncome(
            date=date(2026, 6, 18),
            isin="US0378331005",
            product="APPLE INC",
            gross_eur=Decimal("100.00"),
            withholding_eur=Decimal("15.00"),
            domestic=False,
        )
    ]

    out = render(simple_sales, defaultdict(deque), dividends=dividends)

    assert "Extranjera" in out
    assert "doble imposición" in out
    assert "Retenciones ya practicadas" not in out


def test_interest_reaches_the_base_and_its_own_block() -> None:
    """
    Credit interest is shown and integrated into the savings base.
    """

    console, buf = make_console()
    print_report(
        build_report_data(
            [],
            defaultdict(deque),
            make_statement(interest_income=Decimal("12.34")),
            include_tax=True,
        ),
        console=console,
    )
    out = buf.getvalue()

    assert "Intereses de la cuenta" in out
    assert "12.34" in out


def test_debit_interest_is_shown_as_a_non_attributable_cost() -> None:
    """
    Interest charged on the balance reduces the real return only.

    It is neither a FIFO cost nor a deductible expense of the returns.
    """

    console, buf = make_console()
    print_report(
        build_report_data(
            [],
            defaultdict(deque),
            make_statement(interest_expense=Decimal("3.00")),
            include_tax=True,
        ),
        console=console,
    )

    out = buf.getvalue()

    assert "Gastos no imputados a operaciones" in out
    assert "intereses deudores 3.00 EUR" in out


def test_a_loose_conversion_gets_its_own_block() -> None:
    """
    A currency swap with no trade is surfaced for casilla 1634.
    """

    conversions = (
        CurrencyConversion(
            date=date(2026, 1, 16),
            time="06:35",
            eur_amount=Decimal("0.01"),
            foreign_amount=Decimal("-0.01"),
            foreign_currency="USD",
        ),
    )
    console, buf = make_console()
    print_report(
        build_report_data(
            [],
            defaultdict(deque),
            make_statement(currency_conversions=conversions),
            include_tax=True,
        ),
        console=console,
    )
    out = buf.getvalue()

    assert "Cambios de divisa sin operación asociada" in out
    assert "1634" in out


def test_excluded_trades_are_named_in_red() -> None:
    """
    A trade with no euro value is listed, not quietly missing.
    """

    unvalued = (
        UnvaluedOperation(
            date=date(2026, 1, 15),
            isin="US0000000099",
            product="FAKE",
            description="Compra 10 Fake@10 USD (US0000000099)",
            amount=Decimal("-100.00"),
            currency="USD",
        ),
    )
    console, buf = make_console()
    print_report(
        build_report_data(
            [],
            defaultdict(deque),
            make_statement(unvalued_operations=unvalued),
            include_tax=True,
        ),
        console=console,
    )
    out = buf.getvalue()

    assert "Operaciones excluidas por no tener contravalor en EUR" in out
    assert "US0000000099" in out


def test_promotions_are_shown_outside_the_base() -> None:
    """
    A promotion is reported without being integrated into any base.
    """

    other = (
        OtherIncome(
            date=date(2026, 4, 3),
            description="Promoción DEGIRO reembolso",
            amount_eur=Decimal("27.00"),
        ),
    )
    console, buf = make_console()
    print_report(
        build_report_data(
            [],
            defaultdict(deque),
            make_statement(other_income=other),
            include_tax=True,
        ),
        console=console,
    )
    out = buf.getvalue()

    assert "Otros importes abonados" in out
    assert "27.00" in out
    # Nothing to tax: the amount stays out of the savings base.
    assert "Sin cuota" in out


def test_print_report_no_tax_omits_fiscal_blocks(
    simple_sales: list[Sale],
    one_pending_lot: dict[str, deque[Lot]],
) -> None:
    """
    Verify that ``--no-tax`` omits the IRPF quota and net return.
    """

    out = render(simple_sales, one_pending_lot, include_tax=False)

    assert "Estimación cuota IRPF" not in out
    assert "RENTABILIDAD NETA" not in out
    assert "TOTAL GANANCIA/PÉRDIDA PATRIMONIAL" in out


def test_print_report_without_sales() -> None:
    """
    Verify that without sales it prints total 0 and no net return.
    """

    out = render([], defaultdict(deque))

    assert "TOTAL GANANCIA/PÉRDIDA PATRIMONIAL" in out
    assert "0.00" in out
    assert "Sin ventas en el ejercicio." in out
    assert "RENTABILIDAD NETA" not in out
    assert "Sin cuota" in out


def test_print_report_empty_portfolio(simple_sales: list[Sale]) -> None:
    """
    Verify the message when no pending lots remain.
    """

    assert "Sin cartera abierta." in render(simple_sales, defaultdict(deque))


def test_print_report_connectivity_fees_hidden_when_zero(
    simple_sales: list[Sale],
) -> None:
    """
    Verify the connectivity block is omitted when there are no fees.
    """

    out = render(simple_sales, defaultdict(deque))

    assert "Comisiones de conectividad" not in out


def test_print_report_loss_hides_net_return(
    losing_sales: list[Sale],
    one_pending_lot: dict[str, deque[Lot]],
) -> None:
    """
    Verify that a loss alone does not produce a net return computation.
    """

    out = render(losing_sales, one_pending_lot)

    assert "RENTABILIDAD NETA" not in out
    assert "Sin cuota" in out


def test_print_report_without_dividends_states_it() -> None:
    """
    The dividend section is always rendered, empty when there are none.

    Keeping the section visible tells the user the concept was
    considered rather than silently skipped.
    """

    out = render([], defaultdict(deque))

    assert "Dividendos cobrados" in out
    assert "Sin dividendos en el ejercicio." in out


def test_print_report_shows_dividends_and_withholdings(
    simple_sales: list[Sale],
) -> None:
    """
    Dividends, their withholding and the resulting net are rendered.

    The withholding is a payment on account, so it must be visible
    apart from the gross amount and subtracted from the quota.
    """

    dividends = [
        DividendIncome(
            date=date(2026, 7, 10),
            isin="ES0118594417",
            product="INDRA SISTEMAS SA",
            gross_eur=Decimal("9.00"),
            withholding_eur=Decimal("1.71"),
        )
    ]

    out = render(simple_sales, defaultdict(deque), dividends=dividends)

    assert "Dividendos cobrados" in out
    assert "INDRA SISTEMAS SA" in out
    assert "9.00" in out
    assert "-1.71" in out
    assert "7.29" in out

    # Savings base = 50.00 gains + 9.00 dividends; quota = 19% = 11.21.
    assert "BASE DEL AHORRO" in out
    assert "59.00" in out
    assert "11.21" in out
    assert "Retenciones ya practicadas" in out
    assert "A ingresar tras retenciones" in out


def test_withholdings_above_the_quota_yield_a_refund() -> None:
    """
    An excess withholding is reported as an amount to be refunded.

    The withholding is 19% of the gross dividend, which matches the
    first bracket, so any extra retained must come back.
    """

    dividends = [
        DividendIncome(
            date=date(2026, 7, 10),
            isin="ES0118594417",
            product="INDRA SISTEMAS SA",
            gross_eur=Decimal("100.00"),
            withholding_eur=Decimal("30.00"),
        )
    ]

    out = render([], defaultdict(deque), dividends=dividends)

    assert "A devolver tras retenciones" in out
    assert "11.00" in out


def test_losses_offset_dividends_up_to_the_legal_limit(
    losing_sales: list[Sale],
) -> None:
    """
    A negative gains balance only offsets 25% of the dividend balance.

    The rest is not lost: it is reported as carried forward to the next
    four years (art. 49 LIRPF).
    """

    dividends = [
        DividendIncome(
            date=date(2026, 7, 10),
            isin="ES0118594417",
            product="INDRA SISTEMAS SA",
            gross_eur=Decimal("100.00"),
            withholding_eur=Decimal("19.00"),
        )
    ]

    out = render(losing_sales, defaultdict(deque), dividends=dividends)

    # Losses of 50.00 may only offset 25.00 (25% of 100.00 dividends).
    assert "Compensación de pérdidas" in out
    assert "-25.00" in out
    assert "Pérdidas pendientes de compensar" in out
