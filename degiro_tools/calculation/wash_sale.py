# Standard libraries
import calendar
from collections import defaultdict, deque
from datetime import date
from decimal import Decimal
from typing import Final

# Own modules
from ..domain.models import Lot, Operation, OperationType, Sale, WashSaleAlert
from .declaration import declaration_lines

# Months before and after a transmission in which acquiring homogeneous
# securities defers the loss (art. 33.5.f LIRPF). Listed securities get
# two months; unlisted ones get a year, and this tool only sees listed
# ones because a Degiro statement holds nothing else.
WINDOW_MONTHS: Final[int] = 2


def shift_months(day: date, months: int) -> date:
    """
    Move a date by whole months, clamping the day to the month end.

    Args:
        day: Date to move.
        months: Months to add; negative moves backwards.

    Returns:
        The shifted date, with the day clamped when the target month is
        shorter (31 March back two months is 31 January, but 31 December
        forward two months is the last day of February).
    """

    total = day.month - 1 + months
    year = day.year + total // 12
    month = total % 12 + 1
    last_day = calendar.monthrange(year, month)[1]

    return date(year, month, min(day.day, last_day))


def purchase_dates_by_isin(operations: list[Operation]) -> dict[str, set[date]]:
    """
    Collect the dates on which each ISIN was bought.

    Args:
        operations: Normalized operations of the statement.

    Returns:
        Mapping of ISIN to the set of its purchase dates.
    """

    dates: dict[str, set[date]] = defaultdict(set)

    for operation in operations:
        if operation.operation_type == OperationType.BUY:
            dates[operation.isin].add(operation.date)

    return dates


def repurchases_in_window(
    purchase_dates: set[date], transfer_date: date, acquisition_date: date
) -> tuple[date, ...]:
    """
    Find purchases inside the window of a transmission.

    The acquisition the loss itself comes from is left out: it is the
    element transmitted, not a repurchase of it. Whether units of it
    remain is answered separately by the open lots.

    Args:
        purchase_dates: Dates the ISIN was bought on.
        transfer_date: Date of the transmission that closed in loss.
        acquisition_date: Acquisition date the loss is imputed to.

    Returns:
        Purchase dates inside the window, oldest first.
    """

    start = shift_months(transfer_date, -WINDOW_MONTHS)
    end = shift_months(transfer_date, WINDOW_MONTHS)

    return tuple(
        sorted(
            day
            for day in purchase_dates
            if start <= day <= end and day != acquisition_date
        )
    )


def detect_wash_sales(
    sales: list[Sale],
    operations: list[Operation],
    pending_lots: dict[str, deque[Lot]] | None = None,
) -> tuple[WashSaleAlert, ...]:
    """
    Find the losses that art. 33.5.f LIRPF may keep out of the year.

    Works on the declared lines rather than on whole sales, because the
    gain or loss arises per transferred element: one order can close in
    profit overall and still hold a line in loss whose lot was bought
    inside the window.

    The rule is reported, never applied: whether the loss is deferred
    depends on homogeneity across every broker the taxpayer uses and on
    what is still held afterwards, neither of which a single statement
    settles. What the statement does answer is whether the position was
    left open, which is what decides if a deferred loss surfaces in the
    same year.

    Args:
        sales: Sales matched by FIFO.
        operations: Normalized operations of the statement.
        pending_lots: Open portfolio after applying FIFO, used to tell
            whether the position survived the year.

    Returns:
        One alert per line in loss with a repurchase inside its window,
        in declaration order.
    """

    lots = pending_lots if pending_lots is not None else {}
    purchases = purchase_dates_by_isin(operations)
    alerts: list[WashSaleAlert] = []

    for sale, match in declaration_lines(sales):
        if match.gain_loss >= 0:
            continue

        repurchases = repurchases_in_window(
            purchases.get(sale.isin, set()), sale.date, match.acquisition_date
        )

        if not repurchases:
            continue

        alerts.append(
            WashSaleAlert(
                transfer_date=sale.date,
                acquisition_date=match.acquisition_date,
                isin=sale.isin,
                product=sale.product,
                quantity=match.quantity,
                loss_eur=match.gain_loss,
                repurchase_dates=repurchases,
                position_closed=not lots.get(sale.isin),
            )
        )

    return tuple(alerts)


def total_blocked_loss(alerts: tuple[WashSaleAlert, ...]) -> Decimal:
    """
    Add up the losses the two-month rule may defer.

    Args:
        alerts: Detected alerts.

    Returns:
        Sum of the affected losses, as a negative amount in EUR.
    """

    return sum((alert.loss_eur for alert in alerts), Decimal(0))
