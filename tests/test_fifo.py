# Standard libraries
from decimal import Decimal

# 3pps
import pytest

# Own modules
from degiro_tools import calculate_fifo
from tests.conftest import make_buy, make_sell

EXPECTED_PENDING_QUANTITY = 5


def test_fifo_partial_sale_two_lots() -> None:
    """
    Verify a sale consumes FIFO across two lots and leaves the rest
    pending.
    """

    # Buy 10 units, amount 100 EUR + fee 1 EUR -> unit_cost 10.10
    # Buy 10 units, amount 200 EUR + fee 1 EUR -> unit_cost 20.10
    # Sell 15 units, amount 450 EUR - fee 1 EUR -> transfer_value 449.00
    # FIFO: 10*10.10 + 5*20.10 = 101.00 + 100.50 = 201.50 cost
    # gain_loss = 449.00 - 201.50 = 247.50
    ops = [
        make_buy("X", 10, "-100", "-1", day=1),
        make_buy("X", 10, "-200", "-1", day=2),
        make_sell("X", 15, "450", "-1", day=3),
    ]

    sales, lots = calculate_fifo(ops)

    assert len(sales) == 1
    sale = sales[0]
    assert sale.acquisition_cost == Decimal("201.50")
    assert sale.transfer_value == Decimal("449.00")
    assert sale.gain_loss == Decimal("247.50")
    pending = list(lots["X"])
    assert len(pending) == 1
    assert pending[0].quantity == EXPECTED_PENDING_QUANTITY
    assert pending[0].unit_cost == Decimal("20.1")


def test_fifo_isolated_per_isin() -> None:
    """
    Verify sales of one ISIN do not consume lots of another ISIN.
    """

    ops = [
        make_buy("X", 5, "-50", "0", day=1),
        make_buy("Y", 5, "-100", "0", day=2),
        make_sell("X", 5, "75", "0", day=3),
    ]

    sales, lots = calculate_fifo(ops)

    assert sales[0].gain_loss == Decimal("25.00")
    assert len(lots["Y"]) == 1
    assert not lots["X"]


def test_fifo_not_enough_lots_raises_error() -> None:
    """
    Verify selling more units than bought raises ``ValueError``.
    """

    ops = [
        make_buy("X", 5, "-50", "0", day=1),
        make_sell("X", 10, "100", "0", day=2),
    ]

    with pytest.raises(ValueError, match="Faltan lotes de compra"):
        calculate_fifo(ops)


def test_fifo_sale_empties_lot_exactly() -> None:
    """
    Verify a sale consuming the whole lot removes it from the queue.
    """

    ops = [
        make_buy("X", 10, "-100", "0", day=1),
        make_sell("X", 10, "150", "0", day=2),
    ]

    sales, lots = calculate_fifo(ops)

    assert len(sales) == 1
    assert sales[0].gain_loss == Decimal("50.00")
    assert len(lots["X"]) == 0


def test_fifo_zero_quantity_raises_error() -> None:
    """
    Verify an operation with zero quantity raises ``ValueError``.
    """

    # A buy with quantity 0 divided by zero when computing the unit
    # cost, causing an opaque ZeroDivisionError.
    ops = [make_buy("X", 0, "-100", "-1", day=1)]

    with pytest.raises(ValueError, match="cantidad cero"):
        calculate_fifo(ops)


def test_fifo_purchase_fee_adds_to_cost() -> None:
    """
    Verify the purchase fee is capitalized into the lot unit cost.
    """

    # Buy 10 @ EUR 100 + fee 1 -> unit_cost (100 + 1) / 10 = 10.10.
    sales, lots = calculate_fifo([make_buy("X", 10, "-100", "-1", day=1)])

    assert sales == []
    assert lots["X"][0].unit_cost == Decimal("10.10")


def test_fifo_sale_fee_subtracts_from_transfer_value() -> None:
    """
    Verify the sale fee is deducted from the transfer value.
    """

    ops = [
        make_buy("X", 10, "-100", "0", day=1),
        make_sell("X", 10, "150", "-2", day=2),
    ]

    sales, _ = calculate_fifo(ops)

    assert sales[0].transfer_value == Decimal("148.00")


def test_fifo_single_buy_sell_with_gain() -> None:
    """
    Verify a single buy then sell produces the expected gain.
    """

    ops = [
        make_buy("X", 5, "-50", "0", day=1),
        make_sell("X", 5, "75", "0", day=2),
    ]

    sales, _ = calculate_fifo(ops)

    assert sales[0].gain_loss == Decimal("25.00")


def test_fifo_single_buy_sell_with_loss() -> None:
    """
    Verify a single buy then sell at a lower price produces a loss.
    """

    ops = [
        make_buy("X", 5, "-50", "0", day=1),
        make_sell("X", 5, "30", "0", day=2),
    ]

    sales, _ = calculate_fifo(ops)

    assert sales[0].gain_loss == Decimal("-20.00")


def test_fifo_quantization_only_at_sale_boundary() -> None:
    """
    Verify intermediate costs round to cents only at the Sale boundary.
    """

    # Buy 3 @ EUR 10 (no fee) -> unit_cost = 10/3 = 3.3333...
    # Sell 1 -> acquisition_cost = 3.3333... quantized to 3.33.
    ops = [
        make_buy("X", 3, "-10", "0", day=1),
        make_sell("X", 1, "5", "0", day=2),
    ]

    sales, _ = calculate_fifo(ops)

    assert sales[0].acquisition_cost == Decimal("3.33")
