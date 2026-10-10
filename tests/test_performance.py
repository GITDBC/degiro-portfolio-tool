# Standard libraries
from collections import deque
from datetime import date, timedelta
from decimal import Decimal

# 3pps
import pytest

# Own modules
from degiro_tools.calculation.performance import (
    build_position,
    compute_performance,
    money_weighted_return,
)
from degiro_tools.domain.models import Lot, MarketSnapshot

TODAY = date(2026, 8, 14)


def days_ago(days: int) -> date:
    """
    Return the date the given number of days before the fixed today.

    Args:
        days: Number of days to go back.

    Returns:
        The resulting date.
    """

    return TODAY - timedelta(days=days)


@pytest.mark.parametrize(
    ("purchases", "value", "expected"),
    [
        ([(days_ago(365), Decimal("100"))], Decimal("110"), Decimal("10.00")),
        # 121 from 100 in two years is 10% a year, not 10.5%. Guards the
        # direction of the compounding: discounting instead of
        # compounding would report a negative rate on a winning
        # position.
        ([(days_ago(730), Decimal("100"))], Decimal("121"), Decimal("10.00")),
        # At 10% the two lots grow to 121 and 110, which sum to 231, so
        # the money-weighted rate is analytically known.
        (
            [(days_ago(730), Decimal("100")), (days_ago(365), Decimal("100"))],
            Decimal("231"),
            Decimal("10.00"),
        ),
        ([(days_ago(365), Decimal("100"))], Decimal("90"), Decimal("-10.00")),
    ],
    ids=[
        "ten-percent-over-one-exact-year",
        "compounds-over-two-years",
        "two-lots-with-an-exact-solution",
        "negative-on-a-loss",
    ],
)
def test_annual_return_matches_the_analytic_rate(
    purchases: list[tuple[date, Decimal]], value: Decimal, expected: Decimal
) -> None:
    """
    The money-weighted rate equals the analytically known one.

    Every case here has a closed-form answer, so the assertion is exact
    equality rather than a range.
    """

    assert money_weighted_return(purchases, value, TODAY) == expected


def test_annual_return_annualizes_a_half_year() -> None:
    """
    Ten percent in half a year annualizes above ten percent.
    """

    rate = money_weighted_return(
        [(days_ago(182), Decimal("100"))], Decimal("110"), TODAY
    )

    assert rate is not None
    assert Decimal("20") < rate < Decimal("22")


def test_flat_position_is_not_reported_as_a_loss() -> None:
    """
    A position worth what it cost yields a plain zero, not -0.00.
    """

    rate = money_weighted_return(
        [(days_ago(365), Decimal("100"))], Decimal("100"), TODAY
    )

    assert rate == Decimal("0.00")
    assert not str(rate).startswith("-")


def test_annual_return_refuses_impossible_inputs() -> None:
    """
    No purchases, no value or a future purchase cannot be annualized.
    """

    assert money_weighted_return([], Decimal("110"), TODAY) is None
    assert (
        money_weighted_return([(days_ago(365), Decimal("100"))], Decimal("0"), TODAY)
        is None
    )
    assert (
        money_weighted_return(
            [(TODAY + timedelta(days=10), Decimal("100"))], Decimal("110"), TODAY
        )
        is None
    )


def test_position_values_lots_against_the_price() -> None:
    """
    Cost comes from the lots and value from the current price.
    """

    lots = deque(
        [
            Lot(quantity=10, unit_cost=Decimal("100.00"), date=days_ago(400)),
            Lot(quantity=10, unit_cost=Decimal("120.00"), date=days_ago(200)),
        ]
    )

    position = build_position("IE0001", "FONDO", lots, Decimal("130.00"), TODAY)

    assert position is not None
    assert position.quantity == 20
    assert position.cost_total == Decimal("2200.00")
    assert position.market_value == Decimal("2600.00")
    assert position.gain_loss == Decimal("400.00")
    assert position.unit_cost == Decimal("110.00")
    # 400 over a 2200 cost is 18.18%.
    assert position.gain_loss_pct.quantize(Decimal("0.01")) == Decimal("18.18")


def test_holding_days_are_weighted_by_money_committed() -> None:
    """
    The average holding period leans towards the bigger purchase.

    900 EUR held 400 days and 100 EUR held 100 days average 370 days,
    not the 250 a plain mean would give.
    """

    lots = deque(
        [
            Lot(quantity=9, unit_cost=Decimal("100.00"), date=days_ago(400)),
            Lot(quantity=1, unit_cost=Decimal("100.00"), date=days_ago(100)),
        ]
    )

    position = build_position("IE0001", "FONDO", lots, Decimal("100.00"), TODAY)

    assert position is not None
    assert position.holding_days == 370


def test_young_position_is_not_annualized() -> None:
    """
    Under 90 days the annual figure is withheld instead of inflated.
    """

    lots = deque([Lot(quantity=10, unit_cost=Decimal("100.00"), date=days_ago(10))])

    position = build_position("IE0001", "FONDO", lots, Decimal("105.00"), TODAY)

    assert position is not None
    assert position.holding_days == 10
    assert position.annualized_pct is None
    # The plain unrealized result is still reported.
    assert position.gain_loss == Decimal("50.00")


def test_position_fully_sold_is_skipped() -> None:
    """
    An ISIN with no units left is not an open position.
    """

    assert build_position("IE0001", "FONDO", deque(), Decimal("100.00"), TODAY) is None


def test_summary_adds_realized_and_unrealized() -> None:
    """
    The total is the unrealized result plus gains plus dividends.
    """

    lots = {
        "IE0001": deque(
            [Lot(quantity=10, unit_cost=Decimal("100.00"), date=days_ago(400))]
        ),
        "IE0002": deque(
            [Lot(quantity=5, unit_cost=Decimal("50.00"), date=days_ago(400))]
        ),
    }
    prices = {"IE0001": Decimal("110.00"), "IE0002": Decimal("40.00")}
    products = {"IE0001": "GANADOR", "IE0002": "PERDEDOR"}

    summary = compute_performance(
        lots,
        MarketSnapshot(prices, TODAY),
        products,
        realized_gain_loss=Decimal("910.81"),
        dividend_income=Decimal("25.02"),
    )

    assert summary.cost_total == Decimal("1250.00")
    assert summary.market_value == Decimal("1300.00")
    assert summary.unrealized_gain_loss == Decimal("50.00")
    assert summary.unrealized_pct == Decimal("4")
    assert summary.total_result == Decimal("985.83")
    # Sorted by market value, so the winner leads.
    assert [position.product for position in summary.positions] == [
        "GANADOR",
        "PERDEDOR",
    ]


def test_unpriced_positions_are_reported_not_counted() -> None:
    """
    A position with no price is named apart and left out of the totals.

    Counting it at zero would silently understate the portfolio.
    """

    lots = {
        "IE0001": deque(
            [Lot(quantity=10, unit_cost=Decimal("100.00"), date=days_ago(400))]
        ),
        "ES9999": deque(
            [Lot(quantity=10, unit_cost=Decimal("999.00"), date=days_ago(400))]
        ),
    }

    summary = compute_performance(
        lots,
        MarketSnapshot({"IE0001": Decimal("110.00")}, TODAY),
        {"IE0001": "FONDO"},
        realized_gain_loss=Decimal("0"),
        dividend_income=Decimal("0"),
    )

    assert summary.unpriced_isins == ("ES9999",)
    assert summary.priced_isins == ("IE0001",)
    assert summary.cost_total == Decimal("1000.00")


def test_empty_portfolio_yields_zeroes_not_errors() -> None:
    """
    With nothing open the summary is all zeros and does not divide.
    """

    summary = compute_performance(
        {},
        MarketSnapshot({}, TODAY),
        {},
        realized_gain_loss=Decimal("0"),
        dividend_income=Decimal("0"),
    )

    assert summary.positions == []
    assert summary.cost_total == Decimal(0)
    assert summary.unrealized_pct == Decimal(0)
    assert summary.total_result == Decimal(0)


def test_snapshot_without_a_date_values_at_today() -> None:
    """
    The default valuation date is today, which is what the CLI relies on.

    Pins the default because the CLI builds the snapshot from live
    prices without naming a date: moving it would silently shift every
    holding period and every annualized return in the report.
    """

    lots = {
        "IE0001": deque(
            [Lot(quantity=10, unit_cost=Decimal("100.00"), date=days_ago(400))]
        )
    }
    today = date.today()

    summary = compute_performance(
        lots,
        MarketSnapshot({"IE0001": Decimal("110.00")}),
        {"IE0001": "FONDO"},
        realized_gain_loss=Decimal("0"),
        dividend_income=Decimal("0"),
    )

    assert MarketSnapshot({}).as_of == today
    assert summary.positions[0].holding_days == (today - days_ago(400)).days
