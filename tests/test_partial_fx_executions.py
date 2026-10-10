# Standard libraries
from decimal import Decimal
from pathlib import Path

# Own modules
from degiro_tools import parse_account_xlsx
from tests.conftest import account_row, write_account_xlsx

EXPECTED_EXECUTIONS = 2
FIRST_QUANTITY = 30
SECOND_QUANTITY = 10
TOTAL_QUANTITY = 40

# EUR legs of the two currency exchanges of the same order.
FIRST_LEG = Decimal("-300")
SECOND_LEG = Decimal("-100")


def partial_usd_order_rows() -> list[list[object]]:
    """
    Build a USD order filled in two tranches, each with its own FX pair.

    Degiro emits one ``Cambio de Divisa`` pair per execution, so the
    order carries two EUR legs that must both be counted.

    Returns:
        Account-statement rows of the order.
    """

    return [
        account_row(
            "Retirada Cambio de Divisa",
            currency="EUR",
            amount=float(FIRST_LEG),
            isin="CA73044W3021",
            order_id="ORDER-FX",
        ),
        account_row(
            "Ingreso Cambio de Divisa",
            currency="USD",
            amount=330.0,
            isin="CA73044W3021",
            order_id="ORDER-FX",
        ),
        account_row(
            "Retirada Cambio de Divisa",
            currency="EUR",
            amount=float(SECOND_LEG),
            isin="CA73044W3021",
            order_id="ORDER-FX",
        ),
        account_row(
            "Ingreso Cambio de Divisa",
            currency="USD",
            amount=110.0,
            isin="CA73044W3021",
            order_id="ORDER-FX",
        ),
        account_row(
            "Costes de transacción y/o externos de DEGIRO",
            currency="EUR",
            amount=-2.0,
            isin="CA73044W3021",
            order_id="ORDER-FX",
        ),
        account_row(
            "Compra 30 POET Technologies Inc@11 USD (CA73044W3021)",
            currency="USD",
            amount=-330.0,
            isin="CA73044W3021",
            order_id="ORDER-FX",
        ),
        account_row(
            "Compra 10 POET Technologies Inc@11 USD (CA73044W3021)",
            currency="USD",
            amount=-110.0,
            isin="CA73044W3021",
            order_id="ORDER-FX",
        ),
    ]


def test_every_fx_leg_of_a_partial_order_is_counted(tmp_path: Path) -> None:
    """
    All EUR exchange legs of a multi-tranche order are added up.

    Taking only the first leg understated the consideration of foreign
    orders filled in several executions.
    """

    path = write_account_xlsx(tmp_path / "partial_fx.xlsx", partial_usd_order_rows())

    ops = parse_account_xlsx(path).operations

    assert len(ops) == EXPECTED_EXECUTIONS

    total_amount = sum(operation.amount_eur for operation in ops)

    assert total_amount == FIRST_LEG + SECOND_LEG


def test_partial_fx_amounts_are_prorated_by_quantity(tmp_path: Path) -> None:
    """
    The pooled EUR consideration is split by share quantity.
    """

    path = write_account_xlsx(tmp_path / "partial_fx.xlsx", partial_usd_order_rows())

    by_quantity = {
        operation.quantity: operation
        for operation in parse_account_xlsx(path).operations
    }

    expected_first = (FIRST_LEG + SECOND_LEG) * Decimal(FIRST_QUANTITY) / TOTAL_QUANTITY
    expected_second = (
        (FIRST_LEG + SECOND_LEG) * Decimal(SECOND_QUANTITY) / TOTAL_QUANTITY
    )

    assert by_quantity[FIRST_QUANTITY].amount_eur == expected_first
    assert by_quantity[SECOND_QUANTITY].amount_eur == expected_second
    assert by_quantity[FIRST_QUANTITY].fee_eur == Decimal(-2) * Decimal("0.75")
