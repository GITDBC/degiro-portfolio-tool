# 3pps
import polars as pl
import pytest

# Own modules
from degiro_tools.calculation.portfolio import compute_portfolio_percentage
from degiro_tools.domain.portfolio import (
    OUTPUT_PERCENTAGE_COL,
    OUTPUT_TOTAL_COL,
    OUTPUT_VALUE_COL,
)


def test_compute_portfolio_percentage_zero_total() -> None:
    """
    A zero total yields 0% rows instead of NaN/inf.
    """

    df_input = pl.DataFrame({OUTPUT_VALUE_COL: [0.0, 0.0]})

    result = compute_portfolio_percentage(df_input)

    assert result[OUTPUT_TOTAL_COL].to_list() == [0.0, 0.0]
    assert result[OUTPUT_PERCENTAGE_COL].to_list() == [0.0, 0.0]


def test_compute_portfolio_percentage_normal_split() -> None:
    """
    Non-zero totals produce proportional percentages.
    """

    df_input = pl.DataFrame({OUTPUT_VALUE_COL: [30.0, 70.0]})

    result = compute_portfolio_percentage(df_input)

    assert result[OUTPUT_TOTAL_COL].to_list() == [100.0, 100.0]
    percentages = result[OUTPUT_PERCENTAGE_COL].to_list()
    assert percentages[0] == pytest.approx(30.0)
    assert percentages[1] == pytest.approx(70.0)
