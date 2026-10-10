# Standard libraries
from decimal import Decimal
from pathlib import Path

# Own modules
from degiro_tools import calculate_fifo, parse_account_xlsx
from degiro_tools.cli import main
from tests.conftest import account_row, write_account_xlsx


def test_full_pipeline_eur_buy_sell_gain(tmp_path: Path) -> None:
    """
    Verify a full EUR buy/sell pipeline yields the exact FIFO gain.
    """

    rows = [
        account_row(
            "Compra 10 Foo@10 EUR (IE0000000099)",
            currency="EUR",
            amount=-100.0,
            isin="IE0000000099",
            order_id="ORD-BUY",
            date_str="01-02-2026",
        ),
        account_row(
            "Costes de transacción y/o externos de DEGIRO",
            currency="EUR",
            amount=-1.0,
            isin="IE0000000099",
            order_id="ORD-BUY",
            date_str="01-02-2026",
        ),
        account_row(
            "Venta 10 Foo@15 EUR (IE0000000099)",
            currency="EUR",
            amount=150.0,
            isin="IE0000000099",
            order_id="ORD-SELL",
            date_str="01-03-2026",
        ),
        account_row(
            "Costes de transacción y/o externos de DEGIRO",
            currency="EUR",
            amount=-1.0,
            isin="IE0000000099",
            order_id="ORD-SELL",
            date_str="01-03-2026",
        ),
    ]
    path = write_account_xlsx(tmp_path / "eur.xlsx", rows)

    ops = parse_account_xlsx(path).operations
    sales, _ = calculate_fifo(ops)

    # acq = 10 * (101/10) = 101.00 ; tv = 150 - 1 = 149.00 ; gain = 48.00.
    assert len(sales) == 1
    assert sales[0].gain_loss == Decimal("48.00")


def test_full_pipeline_usd_buy_sell_uses_eur_legs(tmp_path: Path) -> None:
    """
    Verify a USD buy/sell pipeline uses the EUR currency-exchange legs.
    """

    rows = [
        account_row(
            "Compra 10 Foo@25 USD (US0000000001)",
            currency="USD",
            amount=-250.0,
            isin="US0000000001",
            order_id="ORD-UB",
            date_str="01-02-2026",
        ),
        account_row(
            "Ingreso Cambio de Divisa",
            currency="EUR",
            amount=-200.0,
            isin="US0000000001",
            order_id="ORD-UB",
            date_str="01-02-2026",
        ),
        account_row(
            "Costes de transacción y/o externos de DEGIRO",
            currency="EUR",
            amount=-1.0,
            isin="US0000000001",
            order_id="ORD-UB",
            date_str="01-02-2026",
        ),
        account_row(
            "Venta 10 Foo@30 USD (US0000000001)",
            currency="USD",
            amount=300.0,
            isin="US0000000001",
            order_id="ORD-US",
            date_str="01-03-2026",
        ),
        account_row(
            "Retirada Cambio de Divisa",
            currency="EUR",
            amount=250.0,
            isin="US0000000001",
            order_id="ORD-US",
            date_str="01-03-2026",
        ),
        account_row(
            "Costes de transacción y/o externos de DEGIRO",
            currency="EUR",
            amount=-1.0,
            isin="US0000000001",
            order_id="ORD-US",
            date_str="01-03-2026",
        ),
    ]
    path = write_account_xlsx(tmp_path / "usd.xlsx", rows)

    ops = parse_account_xlsx(path).operations
    sales, _ = calculate_fifo(ops)

    buy = next(o for o in ops if o.operation_type.value == "Compra")
    # EUR leg (-200) used, not the USD header (-250).
    assert buy.amount_eur == Decimal("-200.0")
    # acq = 201.00 ; tv = 250 - 1 = 249.00 ; gain = 48.00.
    assert sales[0].gain_loss == Decimal("48.00")


def test_full_pipeline_connectivity_fees(tmp_path: Path) -> None:
    """
    Verify connectivity fees accumulate exactly across XLSX rows.
    """

    rows = [
        account_row(
            "Comisión de conectividad con el mercado (XETRA) 2025",
            currency="EUR",
            amount=-2.5,
            date_str="01-02-2026",
        ),
        account_row(
            "Comisión de conectividad con el mercado (XETRA) 2025",
            currency="EUR",
            amount=-2.5,
            date_str="01-03-2026",
        ),
    ]
    path = write_account_xlsx(tmp_path / "conn.xlsx", rows)

    connectivity_fees = parse_account_xlsx(path).connectivity_fees

    assert connectivity_fees == Decimal("5.00")


def test_cli_tax_accepts_xlsx_path(tmp_path: Path) -> None:
    """
    Verify the ``tax`` subcommand accepts an Account.xlsx path.
    """

    rows = [
        account_row(
            "Compra 10 Foo@10 EUR (IE0000000099)",
            currency="EUR",
            amount=-100.0,
            isin="IE0000000099",
            order_id="ORD-BUY",
            date_str="01-02-2026",
        ),
        account_row(
            "Venta 10 Foo@15 EUR (IE0000000099)",
            currency="EUR",
            amount=150.0,
            isin="IE0000000099",
            order_id="ORD-SELL",
            date_str="01-03-2026",
        ),
    ]
    path = write_account_xlsx(tmp_path / "Account.xlsx", rows)

    assert main(["tax", str(path)]) == 0
    assert main(["tax", str(path), "--no-tax"]) == 0
