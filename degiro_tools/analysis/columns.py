# Standard libraries
from typing import Final

# Column names of the look-through holdings frame assembled by
# ``build_holdings_dataframe`` and of the breakdowns derived from it.
# They are the vocabulary of that frame only: the raw Degiro statement
# sheet is described by the integer indices in ``parsing/columns.py``,
# and the provider exports bring their own headers, mapped by
# ``COLUMN_ALIASES`` in ``providers/holdings/helpers.py``. Those are
# separate vocabularies on purpose and must not be merged into this one.

# Columns carried over from each Holding.
NAME_COL: Final[str] = "name"
TICKER_COL: Final[str] = "ticker"
SECTOR_COL: Final[str] = "sector"
LOCATION_COL: Final[str] = "location"
SOURCE_ISIN_COL: Final[str] = "source_isin"
WEIGHT_PCT_COL: Final[str] = "weight_pct"

# Columns added when the holdings are joined with the portfolio weights:
# the weight of the source position, and the share of the whole account
# the holding accounts for through it.
PORTFOLIO_WEIGHT_COL: Final[str] = "portfolio_weight"
EFFECTIVE_PCT_COL: Final[str] = "effective_pct"

# Columns added by the aggregations rather than present on the frame.
# ``identity`` and ``normalized_name`` are the grouping keys built by
# ``with_identity``; ``etf_count`` and ``continent`` are produced by the
# top-holdings, overlap and geography breakdowns.
ETF_COUNT_COL: Final[str] = "etf_count"
CONTINENT_COL: Final[str] = "continent"
IDENTITY_COL: Final[str] = "identity"
NORMALIZED_NAME_COL: Final[str] = "normalized_name"
