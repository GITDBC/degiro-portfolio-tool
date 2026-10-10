# 3pps
import polars as pl

# Own modules
from ..domain.vocabulary import SECTOR_ES
from .columns import SECTOR_COL
from .coverage import breakdown_by_label


def compute_sectors(df: pl.DataFrame) -> pl.DataFrame:
    """
    Aggregate effective portfolio weight by sector.

    Thin wrapper over the shared breakdown, which is where the residual
    row and the translation to Spanish are justified. Sectors reach this
    point already canonical (GICS names, in English) from the providers
    boundary, so no lookup has to run before the labels are translated.

    Args:
        df: Holdings DataFrame with columns sector, effective_pct.

    Returns:
        DataFrame with columns: sector, effective_pct. Sorted by
        weight descending.
    """

    return breakdown_by_label(df, SECTOR_COL, SECTOR_ES)
