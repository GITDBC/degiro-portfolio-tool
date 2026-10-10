# Standard libraries
from collections import defaultdict, deque
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

# Own modules
from ..domain.constants import CENT_QUANTIZE
from ..domain.models import AcquisitionMatch, Lot, Operation, OperationType, Sale


def consume_lots(
    lots_queue: deque[Lot], quantity: int, unit_transfer_value: Decimal, label: str
) -> tuple[list[AcquisitionMatch], Decimal]:
    """
    Take units FIFO from the queue and group them by acquisition date.

    Several consecutive lots may share an acquisition date when the day
    had more than one execution; they are merged so the declaration
    needs a single line per date.

    Args:
        lots_queue: Pending lots of the ISIN, oldest first.
        quantity: Units to consume.
        unit_transfer_value: Transfer value per unit, net of the sale
            fee.
        label: Human readable sale reference used in error messages.

    Returns:
        Tuple containing:
            - Matches per acquisition date, in FIFO order, rounded to
              the cent for display.
            - Exact unrounded acquisition cost of the whole sale, so
              the sale total does not accumulate the per-line rounding.

    Raises:
        ValueError: If the queue does not hold enough units.
    """

    quantities: dict[date, int] = {}
    costs: dict[date, Decimal] = defaultdict(Decimal)
    remaining = quantity

    while remaining > 0:
        if not lots_queue:
            error_message = (
                f"Faltan lotes de compra para casar la venta de {label}: el "
                f"extracto no cubre las adquisiciones anteriores. Exporta el "
                f"Estado de cuenta desde la apertura de la cuenta, no solo el "
                f"ejercicio, porque el FIFO necesita todas las compras previas."
            )
            raise ValueError(error_message)

        lot = lots_queue[0]
        used = min(remaining, lot.quantity)
        quantities[lot.date] = quantities.get(lot.date, 0) + used
        costs[lot.date] += Decimal(used) * lot.unit_cost
        lot.quantity -= used
        remaining -= used

        if lot.quantity == 0:
            lots_queue.popleft()

    matches = [
        AcquisitionMatch(
            acquisition_date=acquisition_date,
            quantity=used_quantity,
            acquisition_cost=costs[acquisition_date].quantize(
                CENT_QUANTIZE, rounding=ROUND_HALF_UP
            ),
            transfer_value=(unit_transfer_value * used_quantity).quantize(
                CENT_QUANTIZE, rounding=ROUND_HALF_UP
            ),
        )
        for acquisition_date, used_quantity in quantities.items()
    ]

    return matches, sum(costs.values(), Decimal(0))


def calculate_fifo(ops: list[Operation]) -> tuple[list[Sale], dict[str, deque[Lot]]]:
    """
    Consume lots FIFO per ISIN and match sales to their cost.

    Purchase fees add to the cost (art. 35.1.b LIRPF) and sale fees
    subtract from the transfer value (art. 35.2 LIRPF).

    Args:
        ops: Normalized operations sorted chronologically.

    Returns:
        Tuple containing:
            - List of matched sales with computed gain/loss.
            - Dict ISIN -> queue of pending lots (open portfolio).

    Raises:
        ValueError: If an operation has zero quantity or if a sale
            does not have enough lots to consume.
    """

    lots: dict[str, deque[Lot]] = defaultdict(deque)

    sales: list[Sale] = []
    for op in ops:
        if op.quantity == 0:
            # A zero-quantity buy would divide by zero when computing the
            # unit cost, so reject it explicitly instead of raising a
            # cryptic ZeroDivisionError.
            error_message = (
                f"Operación con cantidad cero en {op.date} {op.isin}: no se "
                f"puede repartir su coste entre cero títulos. Revisa esa fila "
                f"del extracto."
            )
            raise ValueError(error_message)

        if op.operation_type == OperationType.BUY:
            total_cost = abs(op.amount_eur) + abs(op.fee_eur)

            lots[op.isin].append(
                Lot(
                    quantity=op.quantity,
                    unit_cost=total_cost / op.quantity,
                    date=op.date,
                )
            )

            continue

        transfer_value = op.amount_eur - abs(op.fee_eur)
        # The tax declaration needs one line per acquisition date, so
        # the per-date detail from consume_lots is kept rather than
        # collapsed into a single cost.
        matches, acquisition_cost = consume_lots(
            lots[op.isin],
            op.quantity,
            transfer_value / op.quantity,
            f"{op.date} {op.isin}",
        )

        sales.append(
            Sale(
                date=op.date,
                isin=op.isin,
                product=op.product,
                quantity=op.quantity,
                acquisition_cost=acquisition_cost.quantize(
                    CENT_QUANTIZE, rounding=ROUND_HALF_UP
                ),
                transfer_value=transfer_value.quantize(
                    CENT_QUANTIZE, rounding=ROUND_HALF_UP
                ),
                gain_loss=(transfer_value - acquisition_cost).quantize(
                    CENT_QUANTIZE, rounding=ROUND_HALF_UP
                ),
                matches=tuple(matches),
            )
        )

    return sales, lots
