# Standard libraries
from typing import Final

# 3pps
import polars as pl

# Own modules
from ..domain.vocabulary import UNCLASSIFIED_LABEL
from .columns import EFFECTIVE_PCT_COL

# Full portfolio weight every breakdown must add up to.
FULL_PCT: Final[float] = 100.0

# Residuals below this many percentage points are rounding noise and are
# not worth a dedicated row.
COVERAGE_TOLERANCE: Final[float] = 0.01


def coverage_pct(df: pl.DataFrame) -> float:
    """
    Total portfolio weight actually covered by the holdings data.

    Falls below 100% whenever a position could not be expanded into
    holdings (missing or non-equity provider file) or whenever the
    provider's own weights do not add up to 100% (cash, futures and
    other sleeves excluded from the equity list).

    Args:
        df: Holdings DataFrame with an ``effective_pct`` column.

    Returns:
        Covered percentage of the priced portfolio.
    """

    if df.height == 0:
        return 0.0

    return float(df[EFFECTIVE_PCT_COL].sum())


def breakdown_by_label(
    df: pl.DataFrame,
    label_col: str,
    translations: dict[str, str],
) -> pl.DataFrame:
    """
    Aggregate effective portfolio weight by a label column.

    Every breakdown of the look-through is built this way and for the
    same reasons, so the criterion lives here once instead of being
    restated per table. Holdings carrying no label are dropped rather
    than grouped under an empty string, and the weight they hold comes
    back as the explicit residual row ``with_unclassified`` appends,
    which is what keeps the table adding up to 100% instead of quietly
    falling short. Labels are translated last, after the sort: the
    providers publish their vocabularies in English, the table is
    ordered by weight, and any lookup keyed by the English name has to
    happen before this call.

    Args:
        df: Holdings frame carrying ``label_col`` and ``effective_pct``.
        label_col: Column whose values label the breakdown.
        translations: Canonical label to its Spanish name. Labels absent
            from the mapping pass through unchanged.

    Returns:
        DataFrame with ``label_col`` and ``effective_pct``, sorted by
        weight descending, plus the residual row when there is a gap.
    """

    df_breakdown = (
        df.filter(pl.col(label_col) != "")
        .group_by(label_col)
        .agg(pl.col(EFFECTIVE_PCT_COL).sum())
        .sort(EFFECTIVE_PCT_COL, descending=True)
        .with_columns(pl.col(label_col).replace(translations))
    )

    return with_unclassified(df_breakdown, label_col)


def with_unclassified(df_breakdown: pl.DataFrame, label_col: str) -> pl.DataFrame:
    """
    Append the residual weight as an explicit unclassified row.

    Breakdowns are built from the holdings that could be classified,
    so they silently add up to less than 100% when data is missing.
    Materializing the gap as a row keeps the table honest and makes
    every percentage comparable against the whole portfolio.

    Columns other than ``label_col`` and ``effective_pct`` are blanked:
    text columns with an empty string and any other type with a null,
    because a count or a ratio has no meaning for weight that could not
    be attributed to a holding.

    Args:
        df_breakdown: Aggregated breakdown with a label column and the
            ``effective_pct`` column.
        label_col: Name of the label column (sector, location, ...).

    Returns:
        The breakdown with an extra residual row when the gap exceeds
        ``COVERAGE_TOLERANCE``, sorted by weight descending.
    """

    residual = FULL_PCT - float(df_breakdown[EFFECTIVE_PCT_COL].sum())

    if residual <= COVERAGE_TOLERANCE:
        return df_breakdown

    values: dict[str, list[object]] = {
        name: [_blank_for(name, label_col, dtype)]
        for name, dtype in df_breakdown.schema.items()
        if name != EFFECTIVE_PCT_COL
    }
    values[EFFECTIVE_PCT_COL] = [residual]

    row = pl.DataFrame(values, schema=df_breakdown.schema)

    return pl.concat([df_breakdown, row]).sort(EFFECTIVE_PCT_COL, descending=True)


def _blank_for(name: str, label_col: str, dtype: pl.DataType) -> object:
    """
    Value filling one cell of the residual row.

    Args:
        name: Column being filled.
        label_col: Name of the label column, which gets the label.
        dtype: Declared type of the column.

    Returns:
        The label for the label column, an empty string for text and
        None for anything else.
    """

    if name == label_col:
        return UNCLASSIFIED_LABEL

    return "" if dtype == pl.String else None
