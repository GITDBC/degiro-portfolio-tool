# Standard libraries
from dataclasses import dataclass
from decimal import Decimal

# Own modules
from ..domain.models import AcquisitionMatch, Sale


@dataclass(frozen=True)
class DeclarationTotals:
    """
    Aggregates of the lines typed into the tax return.

    Attributes:
        transfer_value: Transfer value added up across every line, in
            EUR.
        acquisition_cost: Acquisition value added up across every line,
            in EUR.
        gain_loss: Gain or loss implied by the lines
            (``transfer_value - acquisition_cost``).
        real_gain_loss: Gain or loss computed once per sale, which is
            the accurate figure.
        rounding_drift: Line total minus the real total, in EUR. Non
            zero when splitting a sale by acquisition date and rounding
            each line to the cent does not add back up.
        negative_lines: Lines that close in loss, which inside a
            profitable sale are an artefact of the split rather than a
            real loss.
        negative_total: Gain or loss added up across the negative
            lines, in EUR.
    """

    transfer_value: Decimal
    acquisition_cost: Decimal
    gain_loss: Decimal
    real_gain_loss: Decimal
    rounding_drift: Decimal
    negative_lines: tuple[AcquisitionMatch, ...]
    negative_total: Decimal


def declaration_lines(sales: list[Sale]) -> list[tuple[Sale, AcquisitionMatch]]:
    """
    Flatten the sales into one line per acquisition date.

    The Spanish return declares every transferred element with its own
    acquisition date and value, so a sale that consumed lots bought on
    different days yields several lines even though the broker executed
    a single order.

    Args:
        sales: Sales matched by FIFO.

    Returns:
        Pairs of sale and the acquisition it was imputed to, in FIFO
        order.
    """

    return [(sale, match) for sale in sales for match in sale.matches]


def declaration_totals(sales: list[Sale]) -> DeclarationTotals:
    """
    Add up the declared lines and measure how far they drift.

    Args:
        sales: Sales matched by FIFO.

    Returns:
        ``DeclarationTotals`` with the line totals, the accurate total
        computed per sale, the drift between them and the lines that
        close in loss.
    """

    lines = declaration_lines(sales)

    transfer_value = sum((match.transfer_value for _, match in lines), Decimal(0))
    acquisition_cost = sum((match.acquisition_cost for _, match in lines), Decimal(0))
    gain_loss = transfer_value - acquisition_cost
    real_gain_loss = sum((sale.gain_loss for sale in sales), Decimal(0))

    negative = tuple(match for _, match in lines if match.gain_loss < 0)

    return DeclarationTotals(
        transfer_value=transfer_value,
        acquisition_cost=acquisition_cost,
        gain_loss=gain_loss,
        real_gain_loss=real_gain_loss,
        rounding_drift=gain_loss - real_gain_loss,
        negative_lines=negative,
        negative_total=sum((match.gain_loss for match in negative), Decimal(0)),
    )
