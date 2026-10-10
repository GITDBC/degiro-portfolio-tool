# 3pps
import polars as pl

# Own modules
from ..domain.constants import CONTINENT_MAP
from ..domain.vocabulary import CONTINENT_ES, COUNTRY_ES
from .columns import CONTINENT_COL, EFFECTIVE_PCT_COL, LOCATION_COL
from .coverage import breakdown_by_label


def compute_geography(df: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """
    Aggregate effective portfolio weight by country and continent.

    Both tables are the shared breakdown applied to a different label,
    so the residual row and the translation to Spanish are justified
    there. What is specific to geography is the continent column: it is
    derived here, before the breakdown runs, because CONTINENT_MAP is
    keyed by the English country name and the breakdown translates the
    labels on its way out. Holdings with no location are dropped once,
    up front, so an unlocated holding cannot reach CONTINENT_MAP and be
    counted as an unmapped country.

    Args:
        df: Holdings DataFrame with columns location, effective_pct.

    Returns:
        Tuple of (by_country, by_continent) DataFrames. Each has
        columns location/continent and effective_pct, sorted by
        weight descending.
    """

    df_geo = df.filter(pl.col(LOCATION_COL) != "")

    df_continents = df_geo.select(
        pl.col(LOCATION_COL)
        .replace_strict(CONTINENT_MAP, default="Other")
        .alias(CONTINENT_COL),
        pl.col(EFFECTIVE_PCT_COL),
    )

    return (
        breakdown_by_label(df_geo, LOCATION_COL, COUNTRY_ES),
        breakdown_by_label(df_continents, CONTINENT_COL, CONTINENT_ES),
    )
