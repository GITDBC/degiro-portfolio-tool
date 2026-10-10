# 3pps
import polars as pl
import pytest

# Own modules
from degiro_tools.analysis.coverage import UNCLASSIFIED_LABEL, with_unclassified
from degiro_tools.analysis.sectors import compute_sectors
from degiro_tools.domain.constants import SECTOR_MAP
from degiro_tools.domain.vocabulary import SECTOR_ES

CANONICAL_IT: str = "Information Technology"

# The sector taxonomy Yahoo Finance publishes, spelled out here on
# purpose: it is an external contract, so the test must break when the
# project's own maps stop covering it, not follow a project constant
# that could be edited alongside them.
YAHOO_SECTORS: tuple[str, ...] = (
    "Basic Materials",
    "Communication Services",
    "Consumer Cyclical",
    "Consumer Defensive",
    "Energy",
    "Financial Services",
    "Healthcare",
    "Industrials",
    "Real Estate",
    "Technology",
    "Utilities",
)


def test_sector_map_normalizes_aliases_to_canonical_it() -> None:
    """
    SECTOR_MAP unifies Technology aliases to the GICS canonical.

    Both the Vanguard/Morningstar ``Technology`` alias and the
    Spanish iShares ``Tecnología de la Información`` value must
    normalize to ``Information Technology`` so that providers share a
    single sector taxonomy.
    """

    assert SECTOR_MAP["Technology"] == CANONICAL_IT
    assert SECTOR_MAP["Tecnología de la Información"] == CANONICAL_IT


def test_compute_sectors_merges_mixed_provider_it_buckets() -> None:
    """
    compute_sectors merges normalized IT holdings into one row.

    Synthesizes mixed-provider holdings whose raw sector labels are
    normalized via SECTOR_MAP and whose weights already cover the whole
    portfolio, then verifies a single aggregated Information Technology
    row with no residual ``Technology`` bucket.
    """

    raw_sectors = [
        "Technology",
        "Tecnología de la Información",
        "Information Technology",
    ]
    df = pl.DataFrame(
        {
            "sector": [SECTOR_MAP.get(s, s) for s in raw_sectors],
            "effective_pct": [10.0, 20.0, 70.0],
        }
    )

    result = compute_sectors(df)
    sectors = result["sector"].to_list()

    # The canonical bucket is shown translated, so the expected label
    # comes from the vocabulary instead of being hardcoded twice.
    expected = SECTOR_ES[CANONICAL_IT]

    assert sectors == [expected]
    assert "Technology" not in sectors
    assert "Information Technology" not in sectors
    assert result.filter(pl.col("sector") == expected)["effective_pct"][0] == 100.0


def test_compute_sectors_reports_uncovered_weight_as_unclassified() -> None:
    """
    compute_sectors materializes the weight no sector accounts for.

    A portfolio whose look-through only covers part of its value must
    not present percentages that quietly add up to less than 100%: the
    gap is reported as an explicit ``Unclassified`` row.
    """

    df = pl.DataFrame(
        {
            "sector": [CANONICAL_IT, "Financials", ""],
            "effective_pct": [40.0, 20.0, 5.0],
        }
    )

    result = compute_sectors(df)
    weights = dict(zip(result["sector"], result["effective_pct"], strict=True))

    assert weights[UNCLASSIFIED_LABEL] == pytest.approx(40.0)
    assert sum(weights.values()) == pytest.approx(100.0)


def test_residual_row_tolerates_non_text_columns() -> None:
    """
    The unclassified row blanks a count with a null, not with text.

    The holdings breakdown carries an integer ETF count, so filling
    every non-weight column with an empty string used to raise.
    """

    df = pl.DataFrame(
        {
            "name": ["APPLE INC"],
            "etf_count": [2],
            "effective_pct": [60.0],
        }
    )

    result = with_unclassified(df, "name")

    assert result.height == 2
    residual = result.filter(pl.col("name") == UNCLASSIFIED_LABEL)
    assert residual["effective_pct"][0] == pytest.approx(40.0)
    assert residual["etf_count"][0] is None


def test_yahoo_taxonomy_folds_into_the_gics_vocabulary() -> None:
    """
    Every Yahoo sector normalizes to a sector the reports can show.

    Yahoo publishes a Morningstar-derived taxonomy, not GICS, and it is
    the source for individual stock positions. A label left unmapped
    becomes a sector bucket of its own next to the GICS bucket that
    means the same thing, so the breakdown reports one concept twice.
    """

    unmapped = sorted(
        label
        for label in YAHOO_SECTORS
        if SECTOR_MAP.get(label, label) not in SECTOR_ES
    )

    assert unmapped == []


def test_yahoo_consumer_cyclical_is_consumer_discretionary() -> None:
    """
    The Yahoo label of the sector Inditex trades in is the GICS one.

    Named explicitly because this is the pair that showed up twice in
    the portfolio breakdown.
    """

    assert SECTOR_MAP["Consumer Cyclical"] == "Consumer Discretionary"
    assert SECTOR_MAP["Financial Services"] == "Financials"
    assert SECTOR_MAP["Healthcare"] == "Health Care"


def test_compute_sectors_merges_yahoo_and_gics_buckets() -> None:
    """
    compute_sectors reports one row for a sector named two ways.

    A Yahoo-enriched stock and an ETF constituent in the same sector
    must aggregate into a single translated row, with no residual
    ``Consumer Cyclical`` bucket beside it.
    """

    raw_sectors = ["Consumer Cyclical", "Consumer Discretionary"]
    df = pl.DataFrame(
        {
            "sector": [SECTOR_MAP.get(s, s) for s in raw_sectors],
            "effective_pct": [30.0, 70.0],
        }
    )

    result = compute_sectors(df)
    sectors = result["sector"].to_list()
    expected = SECTOR_ES["Consumer Discretionary"]

    assert sectors == [expected]
    assert "Consumer Cyclical" not in sectors
    assert result["effective_pct"][0] == pytest.approx(100.0)
