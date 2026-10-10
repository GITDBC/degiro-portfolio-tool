# Standard libraries
from decimal import Decimal

# Own modules
from degiro_tools.calculation.yearly import compute_yearly_results

# Own modules
from .conftest import make_dividend, make_sale


def test_empty_statement_yields_no_years() -> None:
    """
    Without sales or dividends there is nothing to break down.
    """

    assert compute_yearly_results([], []) == []


def test_single_year_aggregates_sales_and_dividends() -> None:
    """
    Sales and dividends of one year add up into a single row.
    """

    results = compute_yearly_results(
        [make_sale(2026, "100.50"), make_sale(2026, "-40.50")],
        [make_dividend(2026, "25.02", "4.75")],
    )

    assert len(results) == 1
    year = results[0]
    assert year.year == 2026
    assert year.sale_count == 2
    assert year.gain_loss == Decimal("60.00")
    assert year.dividend_income == Decimal("25.02")
    assert year.withholdings == Decimal("4.75")
    assert year.total == Decimal("85.02")
    assert year.cumulative_total == Decimal("85.02")


def test_years_are_ordered_and_accumulated() -> None:
    """
    Years come out oldest first with a running cumulative total.
    """

    results = compute_yearly_results(
        [
            make_sale(2026, "300.00"),
            make_sale(2024, "100.00"),
            make_sale(2025, "50.00"),
        ],
        [make_dividend(2025, "10.00")],
    )

    assert [result.year for result in results] == [2024, 2025, 2026]
    assert [result.gain_loss for result in results] == [
        Decimal("100.00"),
        Decimal("50.00"),
        Decimal("300.00"),
    ]
    assert [result.cumulative_gain_loss for result in results] == [
        Decimal("100.00"),
        Decimal("150.00"),
        Decimal("450.00"),
    ]
    assert [result.cumulative_total for result in results] == [
        Decimal("100.00"),
        Decimal("160.00"),
        Decimal("460.00"),
    ]


def test_a_losing_year_reduces_the_running_total() -> None:
    """
    A negative year lowers the accumulated result instead of resetting.
    """

    results = compute_yearly_results(
        [make_sale(2024, "500.00"), make_sale(2025, "-200.00")], []
    )

    assert results[1].gain_loss == Decimal("-200.00")
    assert results[1].cumulative_gain_loss == Decimal("300.00")
    assert results[1].cumulative_total == Decimal("300.00")


def test_dividend_only_year_is_reported() -> None:
    """
    A year with dividends but no sales still gets its own row.
    """

    results = compute_yearly_results(
        [make_sale(2026, "10.00")], [make_dividend(2025, "7.00")]
    )

    assert [result.year for result in results] == [2025, 2026]
    assert results[0].sale_count == 0
    assert results[0].gain_loss == Decimal(0)
    assert results[0].dividend_income == Decimal("7.00")
    assert results[1].dividend_income == Decimal(0)
    assert results[1].cumulative_dividend_income == Decimal("7.00")


def test_cumulative_matches_the_sum_of_every_year() -> None:
    """
    The last cumulative value equals the total of all the years.
    """

    sales = [
        make_sale(2023, "10.00"),
        make_sale(2024, "-5.00"),
        make_sale(2025, "8.50"),
    ]
    dividends = [make_dividend(2024, "1.25"), make_dividend(2025, "2.75")]

    results = compute_yearly_results(sales, dividends)

    expected = sum((result.total for result in results), Decimal(0))
    assert results[-1].cumulative_total == expected
    assert results[-1].cumulative_total == Decimal("17.50")
