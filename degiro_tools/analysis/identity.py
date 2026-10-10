# Standard libraries
import re
from typing import Final

# 3pps
import polars as pl

# Own modules
from .columns import IDENTITY_COL, LOCATION_COL, NAME_COL, NORMALIZED_NAME_COL

# Legal forms stripped when comparing company names across providers.
# Only the trailing incorporation form is removed: providers disagree on
# whether to spell it ("ALLIANZ" vs "Allianz SE"), so keeping it would
# miss real matches.
#
# Words that are part of the company identity are deliberately NOT
# stripped. Removing GROUP, HOLDINGS or CLASS <X> used to merge distinct
# issuers such as PSK INC with PSK HOLDINGS INC, ORION CORP with ORION
# HOLDINGS CORP, or Alphabet's A and C share classes.
CORP_SUFFIXES: Final[re.Pattern[str]] = re.compile(
    r"\b(INC|CORP|LTD|PLC|SA|AG|NV|SE|CO)\b"
)


def normalize_name(name: str) -> str:
    """
    Normalize a company name for deduplication across data sources.

    Uppercases and strips the trailing legal form (INC, CORP, LTD,
    PLC, ...) so the same company matches across ETFs that spell it
    differently ("NESTLE SA" vs "Nestle SA", "ALLIANZ" vs "Allianz
    SE").

    Args:
        name: Raw company name from holdings data.

    Returns:
        Cleaned uppercase name with the legal form removed.
    """

    cleaned = CORP_SUFFIXES.sub("", name.upper().strip())

    return " ".join(cleaned.split())


def with_identity(df: pl.DataFrame) -> pl.DataFrame:
    """
    Add the issuer identity column used to group holdings.

    The identity pairs the normalized name with the domicile country.
    The country is part of the key because stripping the legal form
    alone conflates same-named issuers from different markets, such as
    Siemens Ltd (India) with Siemens AG (Germany) or Continental AG
    (Germany) with Continental Holdings Corp (South Korea).

    ISIN is not part of the key on purpose: the iShares and Vanguard
    holdings exports carry no ISIN column at all, so keying on it would
    split every genuine cross-provider match instead of improving it.

    Args:
        df: Holdings DataFrame with name and location columns.

    Returns:
        The same DataFrame with an extra ``identity`` column.
    """

    normalized = (
        pl.col(NAME_COL)
        .map_elements(normalize_name, return_dtype=pl.Utf8)
        .alias(NORMALIZED_NAME_COL)
    )

    return df.with_columns(normalized).with_columns(
        pl.concat_str(
            [pl.col(NORMALIZED_NAME_COL), pl.col(LOCATION_COL)], separator="|"
        ).alias(IDENTITY_COL)
    )
