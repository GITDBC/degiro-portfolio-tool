# Standard libraries
from pathlib import Path

# 3pps
import polars as pl

# Own modules
from ..domain.portfolio import (
    INPUT_CASH_VALUE_COL,
    INPUT_ISIN_COL,
    INPUT_PRODUCT_COL,
    INPUT_QUANTITY_COL,
)
from .excel import read_excel_frame


def parse_portfolio_xlsx(path: Path) -> pl.DataFrame:
    """
    Read a Degiro Portfolio XLSX and return the active positions.

    Selects only the product, ISIN and quantity columns, discarding
    rows without a quantity (closed or empty positions).

    Args:
        path: Path to the Portfolio.xlsx file exported from Degiro.

    Returns:
        DataFrame with columns Producto, Symbol/ISIN and Cantidad,
        filtered to positions with a non-null quantity.
    """

    return (
        read_excel_frame(path)
        .select([INPUT_PRODUCT_COL, INPUT_ISIN_COL, INPUT_QUANTITY_COL])
        .filter(pl.col(INPUT_QUANTITY_COL).is_not_null())
    )


def parse_portfolio_cash(path: Path) -> float:
    """
    Read the uninvested cash balance from a Degiro Portfolio XLSX.

    Degiro reports cash as a row with neither ISIN nor quantity (for
    example ``CASH & CASH FUND & FTX CASH (EUR)``), which the position
    parser discards. Reading it separately lets the valuation report
    show the full account balance instead of only the invested part.

    Args:
        path: Path to the Portfolio.xlsx file exported from Degiro.

    Returns:
        Cash balance in EUR, or 0.0 when the export carries no cash row
        or no EUR value column.
    """

    frame = read_excel_frame(path)

    if INPUT_CASH_VALUE_COL not in frame.columns:
        return 0.0

    cash = frame.filter(
        pl.col(INPUT_QUANTITY_COL).is_null() & pl.col(INPUT_ISIN_COL).is_null()
    )[INPUT_CASH_VALUE_COL].sum()

    return float(cash or 0.0)
