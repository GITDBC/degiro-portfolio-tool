# Standard libraries
from datetime import date
from decimal import Decimal

# Own modules
from degiro_tools import calculate_fifo
from tests.conftest import make_buy, make_sell

EXPECTED_LINES = 3
MERGED_LINES = 1


def test_sale_splits_into_one_line_per_acquisition_date() -> None:
    """
    A sale consuming three purchase days yields three declared lines.

    The return asks for the acquisition date of every transferred
    element, so the detail cannot be collapsed into a single cost.
    """

    ops = [
        make_buy("ES0000000001", 10, "-100.00", "-1.00", day=1),
        make_buy("ES0000000001", 10, "-120.00", "-1.00", day=5),
        make_buy("ES0000000001", 10, "-140.00", "-1.00", day=9),
        make_sell("ES0000000001", 30, "450.00", "-3.00", day=20),
    ]

    sales, _ = calculate_fifo(ops)
    matches = sales[0].matches

    assert len(matches) == EXPECTED_LINES
    assert [match.acquisition_date for match in matches] == [
        date(2026, 1, 1),
        date(2026, 1, 5),
        date(2026, 1, 9),
    ]
    assert [match.quantity for match in matches] == [10, 10, 10]


def test_same_day_purchases_merge_into_one_line() -> None:
    """
    Two executions of the same day become a single declared line.

    Degiro splits a partially filled order into several rows, but the
    acquisition date is the same, so the return needs only one line.
    """

    ops = [
        make_buy("ES0000000001", 10, "-100.00", "-1.00", day=1),
        make_buy("ES0000000001", 5, "-50.00", "-1.00", day=1),
        make_sell("ES0000000001", 15, "200.00", "-2.00", day=20),
    ]

    sales, _ = calculate_fifo(ops)
    matches = sales[0].matches

    assert len(matches) == MERGED_LINES
    assert matches[0].quantity == 15
    assert matches[0].acquisition_cost == Decimal("152.00")


def test_declared_lines_reconcile_with_the_sale_totals() -> None:
    """
    The lines add up to the sale, so no cent is created or lost.

    Rounding each line separately could drift from the sale total, so
    the sale keeps the exact cost and only the lines are rounded.
    """

    ops = [
        make_buy("ES0000000001", 3, "-10.00", "-0.01", day=1),
        make_buy("ES0000000001", 3, "-10.00", "-0.01", day=2),
        make_buy("ES0000000001", 3, "-10.00", "-0.01", day=3),
        make_sell("ES0000000001", 9, "33.33", "-0.01", day=20),
    ]

    sales, _ = calculate_fifo(ops)
    sale = sales[0]

    transfer_total = sum(match.transfer_value for match in sale.matches)
    acquisition_total = sum(match.acquisition_cost for match in sale.matches)

    assert sum(match.quantity for match in sale.matches) == sale.quantity
    assert abs(transfer_total - sale.transfer_value) <= Decimal("0.02")
    assert abs(acquisition_total - sale.acquisition_cost) <= Decimal("0.02")


def test_line_gain_loss_is_derived_from_its_own_values() -> None:
    """
    Each line reports the gain of its own acquisition date.

    A cheap lot and an expensive one bought for the same sale can give
    opposite signs, which is what triggers the two-month review.
    """

    ops = [
        make_buy("ES0000000001", 10, "-200.00", "0.00", day=1),
        make_buy("ES0000000001", 10, "-100.00", "0.00", day=5),
        make_sell("ES0000000001", 20, "320.00", "0.00", day=20),
    ]

    sales, _ = calculate_fifo(ops)
    first, second = sales[0].matches

    assert first.gain_loss == Decimal("-40.00")
    assert second.gain_loss == Decimal("60.00")
    assert sales[0].gain_loss == Decimal("20.00")
