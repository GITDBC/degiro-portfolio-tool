# Standard libraries
from collections import defaultdict, deque
from datetime import date
from decimal import Decimal

# 3pps
import pytest

# Own modules
from degiro_tools import calculate_fifo
from degiro_tools.calculation.wash_sale import (
    detect_wash_sales,
    shift_months,
    total_blocked_loss,
)
from degiro_tools.domain.models import Lot
from tests.conftest import make_buy, make_sell


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (date(2026, 3, 15), -2, date(2026, 1, 15)),
        (date(2026, 3, 15), 2, date(2026, 5, 15)),
        (date(2026, 1, 15), -2, date(2025, 11, 15)),
        (date(2026, 11, 15), 2, date(2027, 1, 15)),
        # The day is clamped when the target month is shorter, so the
        # window never lands on a date that does not exist.
        (date(2026, 4, 30), -2, date(2026, 2, 28)),
        (date(2026, 12, 31), 2, date(2027, 2, 28)),
    ],
)
def test_the_window_moves_by_whole_months(
    start: date, months: int, expected: date
) -> None:
    """
    Two months means the same day two months over, clamped to the month.
    """

    assert shift_months(start, months) == expected


def test_a_loss_with_a_repurchase_after_it_is_flagged() -> None:
    """
    Buying back within two months of a loss raises the rule.

    This is the textbook case of art. 33.5.f LIRPF: the loss is
    cristallized on paper while the position is kept.
    """

    operations = [
        make_buy("ES0000000001", 100, "-5000", "0", day=1),
        make_sell("ES0000000001", 100, "4000", "0", day=15),
        make_buy("ES0000000001", 100, "-4200", "0", day=20),
    ]
    sales, lots = calculate_fifo(operations)

    alerts = detect_wash_sales(sales, operations, lots)

    assert len(alerts) == 1
    assert alerts[0].isin == "ES0000000001"
    assert alerts[0].loss_eur == Decimal("-1000")
    assert alerts[0].repurchase_dates == (date(2026, 1, 20),)
    # 100 units were bought back and never sold, so the position lives on
    # and the deferred loss does not surface this year.
    assert alerts[0].position_closed is False
    assert total_blocked_loss(alerts) == Decimal("-1000")


def test_a_repurchase_outside_the_window_is_not_flagged() -> None:
    """
    A loss with no purchase inside the window is simply deductible.
    """

    operations = [
        make_buy("ES0000000001", 100, "-5000", "0", day=1),
        make_sell("ES0000000001", 100, "4000", "0", day=15),
    ]
    sales, lots = calculate_fifo(operations)

    assert detect_wash_sales(sales, operations, lots) == ()


def test_a_gain_is_never_flagged() -> None:
    """
    The rule blocks losses only; a profitable sale raises nothing.
    """

    operations = [
        make_buy("ES0000000001", 100, "-4000", "0", day=1),
        make_sell("ES0000000001", 100, "5000", "0", day=15),
        make_buy("ES0000000001", 100, "-5200", "0", day=20),
    ]
    sales, lots = calculate_fifo(operations)

    assert detect_wash_sales(sales, operations, lots) == ()


def test_a_loss_line_inside_a_winning_sale_is_flagged() -> None:
    """
    The rule is applied per transferred element, not per order.

    One sale can close in profit and still hold a line in loss whose lot
    was bought inside the window, which is what the declaration declares
    line by line.
    """

    operations = [
        make_buy("ES0000000001", 10, "-500", "0", day=1),
        make_buy("ES0000000001", 10, "-100", "0", day=10),
        make_sell("ES0000000001", 20, "800", "0", day=20),
        make_buy("ES0000000001", 5, "-200", "0", day=25),
    ]
    sales, lots = calculate_fifo(operations)

    alerts = detect_wash_sales(sales, operations, lots)

    # The whole sale gained 200 EUR, yet the first lot lost 100 of it.
    assert sales[0].gain_loss == Decimal("200")
    assert len(alerts) == 1
    assert alerts[0].acquisition_date == date(2026, 1, 1)
    assert alerts[0].loss_eur == Decimal("-100")


def test_a_closed_position_is_reported_as_closed() -> None:
    """
    Whether the position survived decides if the loss surfaces this year.

    A deferred loss is imputed when the repurchased securities are
    transmitted for good, so a position left with no lots brings it back
    into the same exercise.
    """

    operations = [
        make_buy("ES0000000001", 10, "-500", "0", day=1),
        make_buy("ES0000000001", 10, "-100", "0", day=10),
        make_sell("ES0000000001", 20, "800", "0", day=20),
    ]
    sales, _ = calculate_fifo(operations)
    empty: dict[str, deque[Lot]] = defaultdict(deque)

    alerts = detect_wash_sales(sales, operations, empty)

    assert len(alerts) == 1
    assert alerts[0].position_closed is True


def test_another_isin_bought_in_the_window_does_not_count() -> None:
    """
    Only homogeneous securities defer a loss, not any purchase.
    """

    operations = [
        make_buy("ES0000000001", 100, "-5000", "0", day=1),
        make_sell("ES0000000001", 100, "4000", "0", day=15),
        make_buy("ES0000000002", 100, "-4200", "0", day=20),
    ]
    sales, lots = calculate_fifo(operations)

    assert detect_wash_sales(sales, operations, lots) == ()
