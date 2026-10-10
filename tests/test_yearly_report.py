# Standard libraries
from collections import deque
from datetime import date
from decimal import Decimal

# Own modules
from degiro_tools import (
    DividendIncome,
    Lot,
    Sale,
    build_report_data,
    print_report,
)

# Own modules
from .conftest import make_console, make_sale, make_statement


def render(sales: list[Sale], dividends: list[DividendIncome] | None = None) -> str:
    """
    Render the fiscal report to plain text.

    Args:
        sales: Sales matched by FIFO.
        dividends: Dividends collected during the period.

    Returns:
        The rendered report as plain text.
    """

    console, buffer = make_console()
    lots: dict[str, deque[Lot]] = {}

    print_report(
        build_report_data(
            sales,
            lots,
            make_statement(dividends=dividends),
            include_tax=True,
        ),
        console=console,
    )

    return buffer.getvalue()


def test_a_single_year_needs_no_per_year_block() -> None:
    """
    One exercise gets no per-year table.

    Every figure in it is already above and the running total would
    equal the year itself, so the block would only repeat the report.
    """

    out = render([make_sale(2026, "910.81")])

    assert "Resultado realizado por año" not in out
    assert "abarca varios ejercicios" not in out
    # The result is still there, in the total panel.
    assert "910.81" in out


def test_several_years_get_the_block_and_the_caveat() -> None:
    """
    A multi-year statement warns the IRPF blocks lump the years.

    Each year is settled on its own, so aggregating them into a single
    quota would misstate the tax; the notice makes that visible instead
    of leaving the reader to assume the figure is declarable.
    """

    out = render([make_sale(2024, "500.00"), make_sale(2026, "300.00")])

    assert "Resultado realizado por año" in out
    assert "abarca varios ejercicios" in out
    assert "2024" in out
    assert "2026" in out


def test_running_total_is_shown_across_years() -> None:
    """
    The cumulative column carries the result from year to year.
    """

    out = render(
        [make_sale(2024, "1000.00"), make_sale(2025, "-400.00")],
        [
            DividendIncome(
                date=date(2025, 7, 1),
                isin="IE0000000099",
                product="TEST",
                gross_eur=Decimal("50.00"),
                withholding_eur=Decimal("9.50"),
            )
        ],
    )

    # 2024 closes at 1000.00 and 2025 adds -400.00 plus 50.00 of
    # dividends, so the running total lands on 650.00.
    assert "1000.00" in out
    assert "-400.00" in out
    assert "650.00" in out


def test_report_without_operations_states_it() -> None:
    """
    With nothing realized the sales block says so, and no year appears.
    """

    out = render([])

    assert "Sin ventas en el ejercicio" in out
    assert "Resultado realizado por año" not in out
