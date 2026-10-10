# Standard libraries
from collections import deque
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

# Own modules
from ..domain.constants import CENT_QUANTIZE
from ..domain.models import (
    Lot,
    MarketSnapshot,
    PerformanceSummary,
    PositionPerformance,
)

# Days in a year used to turn holding periods into fractions of a year.
_DAYS_PER_YEAR: Final[Decimal] = Decimal(365)

# Shortest cost-weighted holding period worth annualizing. Below this,
# extrapolating a few weeks to a full year turns noise into a headline:
# a position bought last week and up 5% is not earning 1200% a year.
MIN_DAYS_TO_ANNUALIZE: Final[int] = 90

# Bracket and precision of the IRR search. Purchases are all negative
# flows followed by a single positive valuation, so the net present
# value crosses zero exactly once and bisection always converges.
_IRR_LOWER: Final[float] = -0.9999
_IRR_UPPER: Final[float] = 100.0
_IRR_ITERATIONS: Final[int] = 200


def _compounded_cost_gap(
    rate: float, purchases: list[tuple[float, float]], market_value: float
) -> float:
    """
    Gap between the cost compounded to today and the current value.

    Each purchase is carried forward at the candidate rate over the time
    it has been held; the annual return is the rate that makes the two
    sides meet.

    Args:
        rate: Candidate annual rate as a fraction.
        purchases: Purchases as tuples of years held and amount paid.
        market_value: Current market value of the position.

    Returns:
        Compounded cost minus market value. Negative below the true
        rate and positive above it, since it grows with the rate.
    """

    compounded = sum(amount * (1.0 + rate) ** years for years, amount in purchases)

    return float(compounded) - market_value


def money_weighted_return(
    purchases: list[tuple[date, Decimal]], market_value: Decimal, today: date
) -> Decimal | None:
    """
    Annual return of a position weighted by when money went in.

    Solves for the rate that grows every purchase, over the time it has
    actually been held, into the current market value. That is the
    return the invested euros have earned, which is not the same as the
    price change when the money went in on different dates.

    Args:
        purchases: Purchase date and amount paid, as positive amounts.
        market_value: Current market value of the position in EUR.
        today: Valuation date.

    Returns:
        Annual rate as a percentage, or ``None`` when the position
        cannot be annualized: no cost, no value, or purchases dated in
        the future.
    """

    if market_value <= 0 or not purchases:
        return None

    flows: list[tuple[float, float]] = []

    for purchase_date, amount in purchases:
        years = (today - purchase_date).days / float(_DAYS_PER_YEAR)

        if years < 0:
            return None

        flows.append((years, float(amount)))

    value = float(market_value)
    low, high = _IRR_LOWER, _IRR_UPPER

    if (
        _compounded_cost_gap(low, flows, value)
        * _compounded_cost_gap(high, flows, value)
        > 0
    ):
        return None

    for _ in range(_IRR_ITERATIONS):
        middle = (low + high) / 2.0

        if _compounded_cost_gap(middle, flows, value) < 0:
            low = middle
        else:
            high = middle

    rate = (Decimal(str((low + high) / 2.0)) * 100).quantize(
        CENT_QUANTIZE, rounding=ROUND_HALF_UP
    )

    # Bisection can land a hair below zero on a flat position, and a
    # signed negative zero would be rendered as a loss.
    return Decimal("0.00") if rate == 0 else rate


def weighted_holding_days(
    purchases: list[tuple[date, Decimal]], cost_total: Decimal, today: date
) -> int:
    """
    Cost-weighted average holding period of a position.

    Args:
        purchases: Purchase date and amount paid.
        cost_total: Total cost of the purchases in EUR.
        today: Valuation date.

    Returns:
        Average number of days held, weighted by the money committed on
        each date. Zero when there is no cost.
    """

    if cost_total == 0:
        return 0

    weighted = sum(
        (
            Decimal((today - purchase_date).days) * amount
            for purchase_date, amount in purchases
        ),
        Decimal(0),
    )

    return int(weighted / cost_total)


def build_position(
    isin: str,
    product: str,
    lots: deque[Lot],
    unit_price: Decimal,
    today: date,
) -> PositionPerformance | None:
    """
    Value one open position against the cost of its remaining lots.

    Args:
        isin: ISIN identifier of the instrument.
        product: Descriptive product name.
        lots: Purchase lots still open for that ISIN.
        unit_price: Current price per unit in EUR.
        today: Valuation date.

    Returns:
        The valued position, or ``None`` when no units remain, which is
        the case for an ISIN fully sold.
    """

    purchases = [(lot.date, lot.quantity * lot.unit_cost) for lot in lots]
    quantity = sum(lot.quantity for lot in lots)

    if quantity == 0:
        return None

    cost_total = sum((amount for _, amount in purchases), Decimal(0))
    market_value = quantity * unit_price
    holding_days = weighted_holding_days(purchases, cost_total, today)

    annualized = (
        money_weighted_return(purchases, market_value, today)
        if holding_days >= MIN_DAYS_TO_ANNUALIZE
        else None
    )

    return PositionPerformance(
        isin=isin,
        product=product,
        quantity=quantity,
        cost_total=cost_total,
        market_value=market_value,
        holding_days=holding_days,
        annualized_pct=annualized,
    )


def compute_performance(
    lots: dict[str, deque[Lot]],
    snapshot: MarketSnapshot,
    products: dict[str, str],
    *,
    realized_gain_loss: Decimal,
    dividend_income: Decimal,
) -> PerformanceSummary:
    """
    Combine the unrealized result with what has already been realized.

    Values every open position at the prices of the snapshot and reports
    apart the ISINs with no price available, so a position missing from
    the table is never read as a position worth zero.

    Args:
        lots: Open purchase lots per ISIN, as left by FIFO matching.
        snapshot: Prices per ISIN and the date they are valued at.
        products: Descriptive product name per ISIN.
        realized_gain_loss: Net capital gains already realized in EUR.
        dividend_income: Gross dividends collected in EUR.

    Returns:
        ``PerformanceSummary`` with one entry per priced open position,
        sorted by market value descending.
    """

    valuation_date = snapshot.as_of

    positions: list[PositionPerformance] = []
    unpriced: list[str] = []

    for isin in sorted(lots):
        open_lots = lots[isin]

        if not any(lot.quantity for lot in open_lots):
            continue

        price = snapshot.prices.get(isin)

        if price is None:
            unpriced.append(isin)
            continue

        position = build_position(
            isin, products.get(isin, isin), open_lots, price, valuation_date
        )

        if position is not None:
            positions.append(position)

    positions.sort(key=lambda position: position.market_value, reverse=True)

    return PerformanceSummary(
        positions=positions,
        realized_gain_loss=realized_gain_loss,
        dividend_income=dividend_income,
        priced_isins=tuple(position.isin for position in positions),
        unpriced_isins=tuple(unpriced),
    )
