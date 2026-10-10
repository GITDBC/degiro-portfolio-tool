# Standard libraries
from collections import deque
from datetime import date
from decimal import Decimal

# Own modules
from ..domain.models import (
    InflationIndex,
    Lot,
    MarketSnapshot,
    RealPositionReturn,
    RealReturnSummary,
)
from .performance import (
    MIN_DAYS_TO_ANNUALIZE,
    money_weighted_return,
    weighted_holding_days,
)


def deflated_purchases(
    lots: deque[Lot], index: InflationIndex
) -> tuple[list[tuple[date, Decimal]], bool] | None:
    """
    Restate every purchase of a position in reference-month euros.

    Each amount is multiplied by the ratio of the latest published index
    to the index of the month it was paid in, which is what puts euros
    spent years apart on one scale.

    Args:
        lots: Purchase lots still open for the ISIN.
        index: Published price index to deflate with.

    Returns:
        Tuple of the restated purchases and whether any of them fell
        past the last published month, or None when the index covers no
        month of some purchase, in which case the position has no real
        figure at all rather than a partly deflated one.
    """

    restated: list[tuple[date, Decimal]] = []
    carried = False

    for lot in lots:
        if lot.quantity == 0:
            continue

        factor = index.factor_for(lot.date)

        if factor is None:
            return None

        carried = carried or index.is_after_reference(lot.date)
        restated.append((lot.date, lot.quantity * lot.unit_cost * factor))

    return restated, carried


def build_real_position(
    isin: str,
    lots: deque[Lot],
    unit_price: Decimal,
    today: date,
    index: InflationIndex,
) -> RealPositionReturn | None:
    """
    Value one position against its cost restated in today's euros.

    The annual figure comes from the same money-weighted solver the
    nominal one uses, fed the deflated purchases: the real return is the
    same question asked in constant euros, not a different calculation.
    It is withheld under the same holding-period gate, measured on the
    nominal amounts so both columns are annualized or blank together.

    Args:
        isin: ISIN identifier of the instrument.
        lots: Purchase lots still open for that ISIN.
        unit_price: Current price per unit in EUR.
        today: Valuation date.
        index: Published price index to deflate with.

    Returns:
        The real result of the position, or None when no units remain or
        the index does not cover one of its purchase months.
    """

    quantity = sum(lot.quantity for lot in lots)

    if quantity == 0:
        return None

    restated = deflated_purchases(lots, index)

    if restated is None:
        return None

    purchases, carried = restated
    nominal = [(lot.date, lot.quantity * lot.unit_cost) for lot in lots]
    cost_nominal = sum((amount for _, amount in nominal), Decimal(0))
    cost_real = sum((amount for _, amount in purchases), Decimal(0))
    market_value = quantity * unit_price
    holding_days = weighted_holding_days(nominal, cost_nominal, today)

    annualized = (
        money_weighted_return(purchases, market_value, today)
        if holding_days >= MIN_DAYS_TO_ANNUALIZE
        else None
    )

    return RealPositionReturn(
        isin=isin,
        cost_real=cost_real,
        market_value=market_value,
        annualized_pct=annualized,
        carried_forward=carried,
    )


def compute_real_return(
    lots: dict[str, deque[Lot]],
    snapshot: MarketSnapshot,
    index: InflationIndex,
    priced_isins: tuple[str, ...],
) -> RealReturnSummary:
    """
    Take inflation out of the unrealized result of the open portfolio.

    Restates the acquisition cost of every open position in the euros of
    the last published index month and values it against the same
    prices the nominal figures use, so the two views differ only in the
    unit the cost is expressed in.

    A position the index cannot cover is named apart instead of being
    deflated by a guess: the real total is then a partial total, which
    the report discloses, mirroring how a partial weight base is
    disclosed rather than voided.

    Args:
        lots: Open purchase lots per ISIN, as left by FIFO matching.
        snapshot: Prices per ISIN and the date they are valued at.
        index: Published price index to deflate with.
        priced_isins: ISINs the nominal summary reported, in its own
            display order, so both tables list positions alike.

    Returns:
        The real result per covered position plus the ISINs left out.
    """

    positions: list[RealPositionReturn] = []
    uncovered: list[str] = []

    for isin in priced_isins:
        price = snapshot.prices.get(isin)
        open_lots = lots.get(isin)

        if price is None or open_lots is None:
            uncovered.append(isin)
            continue

        position = build_real_position(isin, open_lots, price, snapshot.as_of, index)

        if position is None:
            uncovered.append(isin)
            continue

        positions.append(position)

    return RealReturnSummary(
        positions=tuple(positions),
        region=index.region,
        region_label=index.region_label,
        reference_period=index.reference_period,
        uncovered_isins=tuple(uncovered),
    )
