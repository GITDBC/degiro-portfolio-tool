# Standard libraries
import logging
import os
import time
from pathlib import Path

# 3pps
import openpyxl
import polars as pl
import pytest

# Own modules
from degiro_tools.analysis.geography import compute_geography
from degiro_tools.analysis.holdings import (
    build_holdings_dataframe,
    collect_holdings,
    compute_portfolio_weights,
    compute_top_holdings,
)
from degiro_tools.analysis.identity import with_identity
from degiro_tools.analysis.sectors import compute_sectors
from degiro_tools.domain.holdings import Holding, HoldingsConfig, normalize_holding
from degiro_tools.domain.portfolio import AssetClass
from degiro_tools.domain.vocabulary import OTHER_LABEL, UNCLASSIFIED_LABEL

EXPECTED_TOTAL_PCT = 100.0

# Seconds in a day, to age a file by a number of days.
SECONDS_PER_DAY = 86_400

# ISIN of the fund whose constituents file the freshness tests age.
STALE_ISIN = "IE00B4L5Y983"

# ISIN of the fund whose constituents file is truncated on purpose.
CORRUPT_ISIN = "IE00BM67HQ30"

# ISIN of a money-market position, declared as fixed income so it enters
# the look-through whole and keeps the frame non-empty without a network
# lookup.
MONETARY_ISIN = "LU0290358497"

# The look-through frame contract, spelled out as literal strings on
# purpose. The production code reads the constants in
# ``analysis.columns``, so asserting against those constants would pass
# through any rename of their values; these literals are what downstream
# consumers and the exported reports actually see.
EXPECTED_LOOKTHROUGH_COLUMNS = [
    "name",
    "ticker",
    "sector",
    "location",
    "source_isin",
    "weight_pct",
    "portfolio_weight",
    "effective_pct",
]

# Grouping keys ``with_identity`` appends, in the order it appends them.
EXPECTED_IDENTITY_COLUMNS = ["normalized_name", "identity"]

# Columns the top-holdings aggregation projects, which is where
# ``etf_count`` enters.
EXPECTED_TOP_HOLDINGS_COLUMNS = [
    "name",
    "sector",
    "location",
    "etf_count",
    "effective_pct",
]


def holding(source_isin: str, name: str = "ACME") -> Holding:
    """
    Build a canonical single-holding ETF stand-in for tests.
    """

    return Holding(
        name=name,
        ticker="",
        sector="Information Technology",
        weight_pct=100.0,
        location="United States",
        source_isin=source_isin,
    )


def test_build_holdings_dataframe_raises_on_empty() -> None:
    """
    No loadable holdings raises a clear ValueError, not a cryptic
    ColumnNotFoundError downstream.
    """

    with pytest.raises(ValueError, match="No holdings could be loaded"):
        build_holdings_dataframe([], {})


def test_build_holdings_dataframe_excludes_unpriced_positions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    Unpriced source positions are dropped and reported, and the
    effective weights of the priced holdings still sum to ~100%.
    """

    holdings = [holding("ISIN_PRICED", "Priced"), holding("ISIN_MISSING", "Missing")]
    weights = {"ISIN_PRICED": 1.0}

    with caplog.at_level(logging.WARNING):
        df = build_holdings_dataframe(holdings, weights)

    assert df["source_isin"].to_list() == ["ISIN_PRICED"]
    assert df["effective_pct"].sum() == pytest.approx(EXPECTED_TOTAL_PCT)
    assert "ISIN_MISSING" in caplog.text
    assert "posiciones con precio" in caplog.text


def test_build_holdings_dataframe_weights_relative_to_priced_base() -> None:
    """
    With two priced ETFs, each effective weight reflects its share of
    the priced portfolio value.
    """

    holdings = [holding("ISIN_A", "A"), holding("ISIN_B", "B")]
    weights = {"ISIN_A": 0.3, "ISIN_B": 0.7}

    df = build_holdings_dataframe(holdings, weights)

    by_isin = dict(
        zip(
            df["source_isin"].to_list(),
            df["effective_pct"].to_list(),
            strict=True,
        )
    )

    assert by_isin["ISIN_A"] == pytest.approx(30.0)
    assert by_isin["ISIN_B"] == pytest.approx(70.0)


def test_lookthrough_frame_column_contract() -> None:
    """
    The assembled frame carries exactly the documented columns, in
    order.

    This is the frame every breakdown and the whole reporting layer read
    by name, so a rename shows up here as a failing test instead of
    having to be spotted in a diff.
    """

    df = build_holdings_dataframe([holding("ISIN_A")], {"ISIN_A": 1.0})

    assert df.columns == EXPECTED_LOOKTHROUGH_COLUMNS


def test_lookthrough_columns_survive_an_empty_join() -> None:
    """
    The column set holds when no holding matches a priced position.

    That is what the explicit schema is for: an empty join must still
    yield the declared columns so downstream consumers hit no
    ColumnNotFoundError.
    """

    df = build_holdings_dataframe([holding("ISIN_A")], {"ISIN_B": 1.0})

    assert df.height == 0
    assert df.columns == EXPECTED_LOOKTHROUGH_COLUMNS


def test_derived_breakdown_column_contract() -> None:
    """
    The columns the aggregations add on top of the frame are pinned too.

    Covers the four names that are not on the frame itself: the two
    grouping keys, the ETF count and the continent label.
    """

    df = build_holdings_dataframe([holding("ISIN_A")], {"ISIN_A": 1.0})
    df_country, df_continent = compute_geography(df)

    assert (
        with_identity(df).columns
        == EXPECTED_LOOKTHROUGH_COLUMNS + EXPECTED_IDENTITY_COLUMNS
    )
    assert compute_top_holdings(df).columns == EXPECTED_TOP_HOLDINGS_COLUMNS
    assert df_country.columns == ["location", "effective_pct"]
    assert df_continent.columns == ["continent", "effective_pct"]


def test_compute_portfolio_weights_normalizes_over_priced() -> None:
    """
    Weights are fractions of the summed priced market values.
    """

    weights = compute_portfolio_weights({"ISIN_A": 30.0, "ISIN_B": 70.0})

    assert weights["ISIN_A"] == pytest.approx(0.3)
    assert weights["ISIN_B"] == pytest.approx(0.7)


def test_compute_portfolio_weights_empty_when_nothing_priced() -> None:
    """
    No priced positions yields an empty weight mapping.
    """

    assert compute_portfolio_weights({}) == {}


def located_holding(location: str, source_isin: str, name: str) -> Holding:
    """
    Build a canonical holding domiciled in a given country.
    """

    return normalize_holding(
        Holding(
            name=name,
            ticker="",
            sector="Materials",
            weight_pct=100.0,
            location=location,
            source_isin=source_isin,
        )
    )


@pytest.mark.parametrize(
    ("code", "spanish_name", "expected"),
    [("CA", "Canadá", "Canadá"), ("AU", "Australia", "Australia")],
)
def test_country_breakdown_merges_iso_code_and_named_country(
    code: str, spanish_name: str, expected: str
) -> None:
    """
    A country named by code and by name aggregates into one row.

    Vanguard reports the domicile as an ISO-2 code while iShares
    reports it in Spanish; both must normalize to the same canonical
    country or the breakdown lists the country twice, each with part of
    its weight.
    """

    holdings = [
        located_holding(code, "ISIN_VANGUARD", "From Vanguard"),
        located_holding(spanish_name, "ISIN_ISHARES", "From iShares"),
    ]
    df = build_holdings_dataframe(holdings, {"ISIN_VANGUARD": 0.4, "ISIN_ISHARES": 0.6})

    df_country, _ = compute_geography(df)
    weights = dict(
        zip(df_country["location"], df_country["effective_pct"], strict=True)
    )

    assert code not in weights
    assert weights[expected] == pytest.approx(EXPECTED_TOTAL_PCT)


@pytest.mark.parametrize(
    ("code", "continent"),
    [
        ("JP", "Asia"),
        ("TW", "Asia"),
        ("KR", "Asia"),
        ("HK", "Asia"),
        ("IN", "Asia"),
        ("AU", "Oceanía"),
        ("BR", "Sudamérica"),
        ("ZA", "África"),
    ],
)
def test_continent_breakdown_resolves_non_european_iso_codes(
    code: str, continent: str
) -> None:
    """
    A non-European ISO-2 domicile lands on its real continent.

    Unresolved codes used to fall through CONTINENT_MAP's ``Other``
    default, which inflated the residual continent bucket and left Asia
    reporting a fraction of the exposure actually held.
    """

    df = build_holdings_dataframe(
        [located_holding(code, "ISIN_A", "ACME")], {"ISIN_A": 1.0}
    )

    _, df_continent = compute_geography(df)
    weights = dict(
        zip(df_continent["continent"], df_continent["effective_pct"], strict=True)
    )

    assert weights[continent] == pytest.approx(EXPECTED_TOTAL_PCT)
    assert OTHER_LABEL not in weights


def unlocated_holding(source_isin: str, name: str) -> Holding:
    """
    Build a canonical holding the provider gives no country for.
    """

    return normalize_holding(
        Holding(
            name=name,
            ticker="",
            sector="Materials",
            weight_pct=100.0,
            location="",
            source_isin=source_isin,
        )
    )


def test_geography_drops_unlocated_weight_into_the_residual() -> None:
    """
    An unlocated holding leaves both geographic tables adding to 100%.

    Pins the whole contract of the shared breakdown on the geographic
    side, which is the one with an extra step: the unlocated weight must
    not group under an empty country, must never reach CONTINENT_MAP as
    an unmapped country, and must come back as the explicit residual
    row. The located half pins the order the continent depends on, since
    resolving it after translating the country would send it to
    ``Otros``. The sector side of the same contract is already covered
    by ``test_compute_sectors_merges_mixed_provider_it_buckets``
    (translation) and
    ``test_compute_sectors_reports_uncovered_weight_as_unclassified``
    (empty label filtered, residual row).
    """

    holdings = [
        located_holding("United States", "ISIN_LOCATED", "ACME"),
        unlocated_holding("ISIN_UNLOCATED", "Sin país"),
    ]
    df = build_holdings_dataframe(
        holdings, {"ISIN_LOCATED": 0.6, "ISIN_UNLOCATED": 0.4}
    )

    df_country, df_continent = compute_geography(df)
    countries = dict(
        zip(df_country["location"], df_country["effective_pct"], strict=True)
    )
    continents = dict(
        zip(df_continent["continent"], df_continent["effective_pct"], strict=True)
    )

    assert countries == {
        "Estados Unidos": pytest.approx(60.0),
        UNCLASSIFIED_LABEL: pytest.approx(40.0),
    }
    assert continents == {
        "Norteamérica": pytest.approx(60.0),
        UNCLASSIFIED_LABEL: pytest.approx(40.0),
    }
    assert OTHER_LABEL not in continents


def constituents_file(tmp_path: Path, age_days: float) -> Path:
    """
    Write a stand-in constituents file aged a given number of days.

    A valid workbook with no provider header: no parser recognizes it,
    so the position is reported as not expandable and only the freshness
    check is under test. The age is set on the file itself instead of
    patching the clock, which is what the production code reads.

    Args:
        tmp_path: Temporary directory provided by pytest.
        age_days: How long ago the file should look written.

    Returns:
        Path to the written file.
    """

    path = tmp_path / f"Constituent_{STALE_ISIN}.xlsx"

    workbook = openpyxl.Workbook()
    workbook.active.append(["Sin cabecera de proveedor"])
    workbook.save(path)

    aged = time.time() - age_days * SECONDS_PER_DAY
    os.utime(path, (aged, aged))

    return path


def collect_with_constituents_file(path: Path) -> None:
    """
    Run the look-through collection over one configured equity fund.

    Args:
        path: Constituents file declared for the position.

    Returns:
        None.
    """

    df_portfolio = pl.DataFrame(
        {
            "Producto": ["AGED FUND"],
            "Symbol/ISIN": [STALE_ISIN],
            "Cantidad": [1],
        }
    )

    collect_holdings(df_portfolio, HoldingsConfig(files={STALE_ISIN: path}))


def test_recent_constituents_file_is_not_reported_as_stale(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    A file downloaded days ago raises no freshness warning.
    """

    with caplog.at_level(logging.WARNING):
        collect_with_constituents_file(constituents_file(tmp_path, age_days=1.0))

    assert "constituyentes" not in caplog.text


def test_aged_constituents_file_is_reported_as_stale(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    A file older than the threshold names itself, its age and the ISIN.

    Otherwise a two-month-old snapshot of constituents is combined with
    today's prices and nothing in the report says so.
    """

    with caplog.at_level(logging.WARNING):
        collect_with_constituents_file(constituents_file(tmp_path, age_days=60.0))

    assert "constituyentes" in caplog.text
    assert STALE_ISIN in caplog.text
    assert "60 días" in caplog.text


@pytest.mark.parametrize(
    ("weight_fraction", "expected_excess"),
    [(1.00005, None), (1.0006, "100.06%")],
)
def test_source_weights_above_one_hundred_are_reported(
    weight_fraction: float,
    expected_excess: str | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    Excess coverage warns past the tolerance and stays quiet inside it.

    Provider files do not always add up to 100% (Vanguard FTSE Global
    All-Cap publishes 100.1191%), and an excess in one fund can offset
    weight genuinely missing from another, so it cannot pass unnoticed.
    Inside the tolerance the deviation is arithmetic noise and saying
    anything would be noise too.
    """

    with caplog.at_level(logging.WARNING):
        build_holdings_dataframe([holding("ISIN_A")], {"ISIN_A": weight_fraction})

    if expected_excess is None:
        assert "por encima del 100%" not in caplog.text
    else:
        assert expected_excess in caplog.text
        assert "no se renormaliza" in caplog.text


def test_coverage_short_of_the_minimum_is_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    A look-through covering half the portfolio warns about the rest.

    The low side of the same check as above, kept explicit so the two
    thresholds are pinned by a test each.
    """

    with caplog.at_level(logging.WARNING):
        build_holdings_dataframe([holding("ISIN_A")], {"ISIN_A": 0.5})

    assert "cubre solo el 50.00%" in caplog.text


def truncated_constituents_file(tmp_path: Path) -> Path:
    """
    Write a constituents file cut in half, as a dropped download leaves.

    Args:
        tmp_path: Temporary directory provided by pytest.

    Returns:
        Path to the truncated workbook.
    """

    valid = constituents_file(tmp_path, age_days=0.0)
    corrupt = tmp_path / f"Constituent_{CORRUPT_ISIN}.xlsx"
    corrupt.write_bytes(valid.read_bytes()[: valid.stat().st_size // 2])

    return corrupt


def test_unreadable_constituents_file_leaves_its_weight_unclassified(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    A corrupt file costs one position's breakdown, not the whole report.

    The reader's exception used to escape the provider registry and end
    the run in a traceback, so a single half-downloaded file took the
    overview with it. The position must instead follow the path a
    missing file already follows: no holdings, a warning naming the
    file, and its weight showing up in the unclassified residual of the
    breakdowns.
    """

    df_portfolio = pl.DataFrame(
        {
            "Producto": ["FONDO CORRUPTO", "FONDO MONETARIO"],
            "Symbol/ISIN": [CORRUPT_ISIN, MONETARY_ISIN],
            "Cantidad": [1, 1],
        }
    )
    config = HoldingsConfig(
        files={CORRUPT_ISIN: truncated_constituents_file(tmp_path)},
        classes={MONETARY_ISIN: AssetClass.FIXED_INCOME},
    )

    with caplog.at_level(logging.WARNING):
        holdings = collect_holdings(df_portfolio, config)
        df = build_holdings_dataframe(holdings, {CORRUPT_ISIN: 0.4, MONETARY_ISIN: 0.6})

    df_sectors = compute_sectors(df)
    sectors = dict(zip(df_sectors["sector"], df_sectors["effective_pct"], strict=True))

    assert sectors[UNCLASSIFIED_LABEL] == pytest.approx(40.0)
    assert sum(sectors.values()) == pytest.approx(EXPECTED_TOTAL_PCT)
    assert "no se puede leer" in caplog.text
    assert "no se pudo desglosar" in caplog.text
