# 3pps
import polars as pl

# Own modules
from .columns import (
    EFFECTIVE_PCT_COL,
    ETF_COUNT_COL,
    IDENTITY_COL,
    NAME_COL,
    SOURCE_ISIN_COL,
)
from .identity import normalize_name, with_identity

__all__ = ["compute_overlap", "normalize_name"]


def compute_overlap(df: pl.DataFrame) -> pl.DataFrame:
    """
    Identify securities held through multiple ETFs.

    Groups holdings by issuer identity (normalized name plus domicile
    country) and returns only those appearing in 2+ distinct source
    funds, sorted by total effective portfolio exposure descending.

    Args:
        df: Holdings DataFrame with columns name, location,
            source_isin, effective_pct.

    Returns:
        DataFrame with columns: name, etf_count, effective_pct.
    """

    return (
        with_identity(df)
        .group_by(IDENTITY_COL)
        .agg(
            pl.col(NAME_COL).first().alias(NAME_COL),
            pl.col(SOURCE_ISIN_COL).n_unique().alias(ETF_COUNT_COL),
            pl.col(EFFECTIVE_PCT_COL).sum().alias(EFFECTIVE_PCT_COL),
        )
        .filter(pl.col(ETF_COUNT_COL) > 1)
        .sort(EFFECTIVE_PCT_COL, descending=True)
        .drop(IDENTITY_COL)
    )
