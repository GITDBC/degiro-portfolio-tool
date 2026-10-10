# Standard libraries
from dataclasses import dataclass, field, replace
from pathlib import Path

# Own modules
from .constants import COUNTRY_MAP, SECTOR_MAP
from .portfolio import AssetClass


@dataclass(frozen=True)
class HoldingsConfig:
    """
    Per-ISIN facts the Degiro export does not carry.

    Two independent declarations rather than one table: an equity fund
    needs a constituents file to be expanded, while a position of any
    other class is never expanded, so a file for it would never be read.

    Attributes:
        files: ISIN to the constituents file that expands it.
        classes: ISIN to its declared asset class. Absent means
            ``AssetClass.EQUITY``, which is what the look-through
            already assumes.
    """

    files: dict[str, Path] = field(default_factory=dict)
    classes: dict[str, AssetClass] = field(default_factory=dict)

    def asset_class(self, isin: str) -> AssetClass:
        """
        Asset class declared for an ISIN.

        Args:
            isin: ISIN of the position.

        Returns:
            The declared class, or ``AssetClass.EQUITY`` when the ISIN
            is not declared.
        """

        return self.classes.get(isin, AssetClass.EQUITY)


@dataclass(frozen=True)
class Holding:
    """
    Single equity holding within an ETF or standalone stock position.

    Attributes:
        name: Company or instrument name.
        ticker: Ticker symbol of the holding.
        sector: Sector classification (e.g. "Information Technology").
        weight_pct: Weight within the parent fund as percentage (0-100).
        location: Country where the company is domiciled.
        source_isin: ISIN of the parent ETF (or self for stocks).
    """

    name: str
    ticker: str
    sector: str
    weight_pct: float
    location: str
    source_isin: str


def normalize_holding(holding: Holding) -> Holding:
    """
    Canonicalize a holding's sector and location labels.

    Maps raw provider sector and country values to the project's
    canonical vocabulary via SECTOR_MAP and COUNTRY_MAP. Values that
    are already canonical (or absent from the maps) are returned
    unchanged. This is the single normalization step applied at the
    providers boundary so that every consumer receives canonical
    holdings, regardless of the originating provider.

    Args:
        holding: Holding carrying raw provider sector and location
            values.

    Returns:
        New Holding with normalized sector and location fields; all
        other fields are preserved.
    """

    return replace(
        holding,
        sector=SECTOR_MAP.get(holding.sector, holding.sector),
        location=COUNTRY_MAP.get(holding.location, holding.location),
    )
