# 3pps
import polars as pl
import pytest

# Own modules
from degiro_tools.analysis.coverage import UNCLASSIFIED_LABEL
from degiro_tools.analysis.geography import compute_geography
from degiro_tools.analysis.holdings import compute_top_holdings
from degiro_tools.analysis.overlap import compute_overlap, normalize_name
from degiro_tools.domain.constants import CONTINENT_MAP, COUNTRY_MAP

EXPECTED_ETF_COUNT = 2


def holdings_frame(rows: list[tuple[str, str, str, str, float]]) -> pl.DataFrame:
    """
    Build a look-through holdings frame from compact tuples.

    Args:
        rows: Tuples of name, sector, location, source_isin and
            effective_pct.

    Returns:
        DataFrame with the columns the analysis functions consume.
    """

    return pl.DataFrame(
        {
            "name": [row[0] for row in rows],
            "sector": [row[1] for row in rows],
            "location": [row[2] for row in rows],
            "source_isin": [row[3] for row in rows],
            "effective_pct": [row[4] for row in rows],
        }
    )


def test_same_issuer_spelled_differently_is_one_overlap_row() -> None:
    """
    Case and legal-form differences do not split a real overlap.

    Providers disagree on capitalization, so "ASML Holding NV" and
    "ASML HOLDING NV" describe the same issuer and must aggregate.
    """

    df = holdings_frame(
        [
            ("ASML Holding NV", "Information Technology", "Netherlands", "ETF_A", 0.39),
            ("ASML HOLDING NV", "Information Technology", "Netherlands", "ETF_B", 0.37),
        ]
    )

    result = compute_overlap(df)

    assert result.height == 1
    assert result["etf_count"][0] == EXPECTED_ETF_COUNT
    assert result["effective_pct"][0] == pytest.approx(0.76)


def test_same_name_in_different_countries_is_not_an_overlap() -> None:
    """
    Siemens Ltd (India) is not Siemens AG (Germany).

    Stripping the legal form alone conflated same-named issuers from
    different markets into fake overlaps.
    """

    df = holdings_frame(
        [
            ("SIEMENS LTD", "Industrials", "India", "ETF_A", 0.12),
            ("Siemens AG", "Industrials", "Germany", "ETF_B", 0.20),
        ]
    )

    assert compute_overlap(df).height == 0


def test_holdings_company_is_not_merged_with_its_namesake() -> None:
    """
    ``X Corp`` and ``X Holdings Corp`` stay separate issuers.

    Stripping HOLDINGS merged distinct listed companies such as PSK Inc
    with PSK Holdings Inc.
    """

    assert normalize_name("PSK INC") != normalize_name("PSK HOLDINGS INC")
    assert normalize_name("ORION CORP") != normalize_name("ORION HOLDINGS CORP")
    assert normalize_name("ALPHABET INC CLASS A") != normalize_name(
        "ALPHABET INC CLASS C"
    )


def test_legal_form_is_still_stripped() -> None:
    """
    The trailing incorporation form is ignored when comparing names.

    Providers disagree on whether to spell it, so keeping it would miss
    genuine matches.
    """

    assert normalize_name("ALLIANZ") == normalize_name("Allianz SE")
    assert normalize_name("NESTLE SA") == normalize_name("Nestle SA")


def test_top_holdings_aggregate_the_same_issuer() -> None:
    """
    The holdings ranking groups by issuer, not by raw provider name.

    Grouping by the raw name split a company across two smaller rows,
    pushing it artificially down the ranking.
    """

    df = holdings_frame(
        [
            ("ASML Holding NV", "Information Technology", "Netherlands", "ETF_A", 40.0),
            ("ASML HOLDING NV", "Information Technology", "Netherlands", "ETF_B", 35.0),
            ("APPLE INC", "Information Technology", "United States", "ETF_A", 25.0),
        ]
    )

    result = compute_top_holdings(df)

    assert result.height == 2
    assert result["name"][0] == "ASML Holding NV"
    assert result["effective_pct"][0] == pytest.approx(75.0)


def test_top_holdings_expose_the_uncovered_weight() -> None:
    """
    The ranking shows the share of the portfolio it cannot explain.

    Positions that could not be looked through would otherwise vanish
    from the table without any trace.
    """

    df = holdings_frame(
        [("APPLE INC", "Information Technology", "United States", "ETF_A", 60.0)]
    )

    result = compute_top_holdings(df)
    weights = dict(zip(result["name"], result["effective_pct"], strict=True))

    assert weights[UNCLASSIFIED_LABEL] == pytest.approx(40.0)


def test_south_korea_maps_to_asia() -> None:
    """
    The iShares ``Korea (South)`` label resolves to a continent.

    Left unmapped it dumped the whole Korean exposure into the "Other"
    continent bucket.
    """

    canonical = COUNTRY_MAP["Korea (South)"]

    assert canonical == "South Korea"
    assert CONTINENT_MAP[canonical] == "Asia"


def test_every_mapped_country_has_a_continent() -> None:
    """
    No canonical country may be missing from CONTINENT_MAP.

    A country without a continent silently falls into "Other", which
    misstates the geographic allocation.
    """

    missing = sorted(set(COUNTRY_MAP.values()) - set(CONTINENT_MAP))

    assert missing == []


def test_geography_breakdowns_add_up_to_one_hundred() -> None:
    """
    Both geographic breakdowns account for the whole portfolio.

    Holdings without a location and positions that could not be
    expanded surface as an explicit ``Unclassified`` row.
    """

    df = holdings_frame(
        [
            ("APPLE INC", "Information Technology", "United States", "ETF_A", 50.0),
            ("NO LOCATION CORP", "Industrials", "", "ETF_A", 10.0),
        ]
    )

    df_country, df_continent = compute_geography(df)

    assert df_country["effective_pct"].sum() == pytest.approx(100.0)
    assert df_continent["effective_pct"].sum() == pytest.approx(100.0)
    assert UNCLASSIFIED_LABEL in df_country["location"].to_list()
    assert UNCLASSIFIED_LABEL in df_continent["continent"].to_list()
