# Standard libraries
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final

# Own modules
from ..domain.etf import Agreement, ETFCrossCheck, ETFProfile, YahooETFData
from ..providers.yahoo import fetch_etf_data

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)


@dataclass(frozen=True)
class ETFSectionData:
    """
    Everything the ETF section needs, independent of output format.

    Attributes:
        crosschecks: One cross-check per requested ISIN, in request
            order.
        missing: ISINs neither source covers, so a silent omission is
            never mistaken for an absent position.
        uncovered: ISINs the justETF screener does not list, whether or
            not Yahoo Finance resolved them.
    """

    crosschecks: list[ETFCrossCheck]
    missing: list[str] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)


def build_crosschecks(
    isins: Iterable[str],
    profiles: Sequence[ETFProfile],
    yahoo_rows: Sequence[YahooETFData],
) -> list[ETFCrossCheck]:
    """
    Pair each ISIN with whatever each source published for it.

    An ISIN missing from one source still produces a row, with that
    side left as None, so a fund only one provider knows about stays
    visible instead of dropping out of the comparison.

    Args:
        isins: ISINs to compare, in the order they should appear.
        profiles: Profiles returned by the justETF screener.
        yahoo_rows: Figures returned by Yahoo Finance.

    Returns:
        One cross-check per requested ISIN, in the given order.
    """

    by_isin_justetf = {profile.isin: profile for profile in profiles}
    by_isin_yahoo = {row.isin: row for row in yahoo_rows}

    return [
        ETFCrossCheck(
            isin=isin,
            justetf=by_isin_justetf.get(isin),
            yahoo=by_isin_yahoo.get(isin),
        )
        for isin in isins
    ]


def fetch_yahoo_rows(isins: Sequence[str]) -> list[YahooETFData]:
    """
    Read the Yahoo figures for several ISINs, skipping failures.

    Args:
        isins: ISINs to look up.

    Returns:
        The rows that could be retrieved; ISINs Yahoo does not resolve
        are simply absent.
    """

    rows = [fetch_etf_data(isin) for isin in isins]

    return [row for row in rows if row is not None]


def count_agreements(crosschecks: Iterable[ETFCrossCheck]) -> dict[Agreement, int]:
    """
    Tally how the two sources line up on the TER, verdict by verdict.

    Args:
        crosschecks: Cross-checks to inspect.

    Returns:
        Mapping of every ``Agreement`` value to the number of funds it
        applies to, with zero for the verdicts that did not occur.
    """

    counts: dict[Agreement, int] = dict.fromkeys(Agreement, 0)

    for check in crosschecks:
        counts[check.ter_agreement] += 1

    return counts
