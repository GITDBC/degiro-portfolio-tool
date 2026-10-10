# Standard libraries
import ast
import inspect
import textwrap
from datetime import date
from decimal import Decimal

# 3pps
import pytest

# Own modules
from degiro_tools.domain.models import InflationIndex
from degiro_tools.providers import inflation
from degiro_tools.providers.inflation import (
    REGIONS,
    fetch_inflation_index,
    hicp_url,
    parse_period,
    parse_series,
)

# Header of the SDMX CSV response, verbatim from the live endpoint. The
# two columns that carry data sit among thirty-odd of metadata, which is
# why the parse reads them by name and not by position.
CSV_HEADER: str = (
    "KEY,FREQ,REF_AREA,ADJUSTMENT,ICP_ITEM,DATA_PROVIDER,ICP_SUFFIX,"
    "TIME_PERIOD,OBS_VALUE,EMBARGO_TIME,COMMENT_OBS,PRE_BREAK_VALUE,"
    "CONF_STATUS,OBS_STATUS,UNIT_INDEX_BASE"
)


def csv_body(*observations: tuple[str, str]) -> str:
    """
    Build an SDMX CSV response carrying the given observations.

    Args:
        *observations: Period and value per row, as the endpoint writes
            them.

    Returns:
        The response body as text.
    """

    rows = [
        f"HICP.M.ES.N.000000.4D0.INX,M,ES,N,000000,4D0,INX,"
        f"{period},{value},,,,F,A,2025 = 100"
        for period, value in observations
    ]

    return "\n".join([CSV_HEADER, *rows])


def calls_fromisoformat(source: str) -> bool:
    """
    Whether a piece of source actually calls ``fromisoformat``.

    Checked structurally rather than by searching the text, so the
    prose explaining why the call is avoided does not itself trip the
    guard, and so a call cannot hide behind different spacing.

    Args:
        source: Python source to inspect.

    Returns:
        True when the source contains a call to any attribute named
        ``fromisoformat``.
    """

    tree = ast.parse(textwrap.dedent(source))

    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "fromisoformat"
        for node in ast.walk(tree)
    )


class TestSeriesIdentity:
    """
    Which series is requested, which is the whole correctness question.
    """

    @pytest.mark.parametrize("region", REGIONS)
    def test_the_url_names_the_hicp_dataflow_and_the_4d0_suffix(
        self, region: str
    ) -> None:
        """
        Only HICP with the 4D0 provider suffix is still maintained.

        Both of the alternatives answer HTTP 200 while frozen at
        2025-12 after the February 2026 methodology change. A source
        that fails by going stale rather than by erroring is the worst
        kind here: every figure downstream would look perfectly normal
        and be deflated by an index a year out of date.
        """

        url = hicp_url(region)

        assert "/data/HICP/" in url
        assert ".4D0." in url
        assert "format=csvdata" in url

    @pytest.mark.parametrize("region", REGIONS)
    def test_the_url_never_uses_the_frozen_sources(self, region: str) -> None:
        """
        The ICP dataflow and Eurostat's index must not come back.
        """

        url = hicp_url(region)

        assert "/data/ICP/" not in url
        assert "prc_hicp_midx" not in url

    def test_the_regions_are_spain_and_the_euro_area(self) -> None:
        """
        The two series the CLI offers point at the intended areas.
        """

        assert "M.ES.N.000000.4D0.INX" in hicp_url("es")
        assert "M.U2.N.000000.4D0.INX" in hicp_url("ea")

    def test_an_unknown_region_is_refused(self) -> None:
        """
        A region outside the published set raises rather than guessing.
        """

        with pytest.raises(ValueError, match="desconocida"):
            hicp_url("us")


class TestPeriodParsing:
    """
    Reading the monthly period the observations are keyed by.
    """

    def test_no_period_is_ever_parsed_as_a_date(self) -> None:
        """
        ``date.fromisoformat`` must not touch a ``YYYY-MM`` period.

        Measured on Python 3.11.15, that call rejects a bare ``2026-08``
        with ValueError. The danger is not the exception but where it
        would land: the ISO helper this package already had turns
        ValueError into None, so a period read through it would discard
        the row, the series would come back empty, and the real return
        would simply be absent with nothing saying why. Both halves of
        the period handling are pinned by inspection because that
        failure leaves no trace in the output.
        """

        assert not calls_fromisoformat(inspect.getsource(inflation))
        assert not calls_fromisoformat(inspect.getsource(InflationIndex))

    def test_the_standard_parser_really_does_refuse_a_period(self) -> None:
        """
        Pin the behaviour the hand-written parser exists because of.

        Asserted rather than assumed: if a future Python starts
        accepting ``YYYY-MM`` and returning the first of the month, the
        reasoning above changes from "fails closed" to "silently wrong"
        and this test is where that shows up.
        """

        with pytest.raises(ValueError, match="Invalid isoformat string"):
            date.fromisoformat("2026-08")

    @pytest.mark.parametrize(
        "value", ["1996-01", "2026-08", "2025-12", "2026-12", "2000-10"]
    )
    def test_a_monthly_period_is_accepted(self, value: str) -> None:
        """
        The shape the endpoint publishes is read back unchanged.
        """

        assert parse_period(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "2026",
            "2026-8",
            "2026-13",
            "2026-00",
            "2026-08-01",
            "ayer",
            "20xx-08",
            "2026/08",
        ],
        ids=[
            "absent",
            "empty",
            "year-only",
            "unpadded-month",
            "month-thirteen",
            "month-zero",
            "a-full-date",
            "plain-text",
            "letters-in-year",
            "wrong-separator",
        ],
    )
    def test_anything_else_is_refused(self, value: str | None) -> None:
        """
        A period that is not a valid month yields None, not a guess.
        """

        assert parse_period(value) is None


class TestSeriesParsing:
    """
    Turning the CSV response into an index per month.
    """

    def test_the_observations_are_read_by_column_name(self) -> None:
        """
        Both real rows of the measured response are picked up.
        """

        values = parse_series(csv_body(("1996-01", "50.5"), ("2026-08", "104.71")))

        assert values == {"1996-01": 50.5, "2026-08": 104.71}

    def test_an_unusable_row_does_not_invalidate_the_others(self) -> None:
        """
        One bad row is skipped; the response is untrusted input.
        """

        values = parse_series(
            csv_body(
                ("2026-06", "104.0"),
                ("2026-13", "104.2"),
                ("2026-07", "sin dato"),
                ("2026-08", "104.71"),
            )
        )

        assert values == {"2026-06": 104.0, "2026-08": 104.71}

    def test_a_response_without_the_value_column_yields_nothing(self) -> None:
        """
        A redesigned response is reported as empty, not misread.
        """

        assert parse_series("KEY,FREQ,TIME_PERIOD\nHICP.M.ES,M,2026-08") == {}

    def test_a_non_positive_index_is_refused(self) -> None:
        """
        An index of zero would divide by nothing downstream.
        """

        assert parse_series(csv_body(("2026-08", "0"))) == {}


class TestFetch:
    """
    Resolving the index end to end, transport stubbed.
    """

    def stub(self, monkeypatch: pytest.MonkeyPatch, body: bytes | None) -> list[str]:
        """
        Replace the transport with one canned response.

        Args:
            monkeypatch: pytest monkeypatch fixture.
            body: Response body, or None to answer as a failure.

        Returns:
            List the requested URLs are appended to.
        """

        calls: list[str] = []

        def fake_get(url: str) -> bytes | None:
            calls.append(url)
            return body

        monkeypatch.setattr(inflation, "http_get", fake_get)

        return calls

    def test_the_reference_month_is_the_last_published_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        The series decides the reference month, never the clock.

        The index is published about six weeks late, so asserting the
        current month would be asserting a figure nobody has published.
        """

        self.stub(
            monkeypatch,
            csv_body(("2026-06", "104.0"), ("2026-08", "104.71")).encode("utf-8"),
        )

        index = fetch_inflation_index("es")

        assert index is not None
        assert index.reference_period == "2026-08"
        assert index.reference_value == pytest.approx(104.71)
        assert index.region_label == "España"

    def test_a_transport_failure_yields_no_index(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        With no series the caller keeps the nominal figures alone.
        """

        self.stub(monkeypatch, None)

        assert fetch_inflation_index("es") is None

    def test_an_empty_series_yields_no_index(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A response with no usable observation is not half an index.
        """

        self.stub(monkeypatch, CSV_HEADER.encode("utf-8"))

        assert fetch_inflation_index("es") is None

    def test_the_series_is_cached_between_calls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A monthly series is not downloaded twice in one day.
        """

        calls = self.stub(monkeypatch, csv_body(("2026-08", "104.71")).encode("utf-8"))

        fetch_inflation_index("es")
        fetch_inflation_index("es")

        assert len(calls) == 1

    def test_refresh_bypasses_the_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """
        A zero window forces a fresh download, which is what --refresh does.
        """

        calls = self.stub(monkeypatch, csv_body(("2026-08", "104.71")).encode("utf-8"))

        fetch_inflation_index("es")
        fetch_inflation_index("es", max_age_seconds=0)

        assert len(calls) == 2

    def test_each_region_is_cached_apart(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """
        Asking for the euro area must not return the Spanish series.
        """

        calls = self.stub(monkeypatch, csv_body(("2026-08", "104.71")).encode("utf-8"))

        fetch_inflation_index("es")
        fetch_inflation_index("ea")

        assert len(calls) == 2
        assert "M.ES." in calls[0]
        assert "M.U2." in calls[1]


def index(**values: float) -> InflationIndex:
    """
    Build an index from periods written as keyword arguments.

    Args:
        **values: Index value per period, with the dash of the period
            written as an underscore (``y2026_08``).

    Returns:
        The index, with the newest period as its reference.
    """

    series = {key[1:].replace("_", "-"): value for key, value in values.items()}

    return InflationIndex(
        region="es",
        region_label="España",
        reference_period=max(series),
        values=series,
    )


class TestDeflator:
    """
    Turning euros of one month into euros of another.
    """

    def test_a_past_purchase_is_scaled_up_by_the_index_ratio(self) -> None:
        """
        The factor is the published ratio, nothing else.

        100 in a month at 80 is 130 in a month at 104: the arithmetic is
        the whole method, so it is asserted exactly.
        """

        factor = index(y2019_06=80.0, y2026_08=104.0).factor_for(date(2019, 6, 15))

        assert factor == Decimal("104.0") / Decimal("80.0")

    def test_the_reference_month_itself_is_unchanged(self) -> None:
        """
        Money paid in the reference month is already in its euros.
        """

        assert index(y2026_08=104.0).factor_for(date(2026, 8, 3)) == Decimal(1)

    def test_the_day_of_the_month_does_not_matter(self) -> None:
        """
        The index is monthly, so every day of a month shares its factor.
        """

        published = index(y2019_06=80.0, y2026_08=104.0)

        assert published.factor_for(date(2019, 6, 1)) == published.factor_for(
            date(2019, 6, 30)
        )

    def test_a_purchase_newer_than_the_index_carries_it_forward(self) -> None:
        """
        A month not yet published is deflated by exactly one.

        The index runs about six weeks behind, so recent purchases
        genuinely have no month of their own. Carrying the last one
        forward declines to invent inflation instead of extrapolating
        it, and the report says so beside the figure.
        """

        published = index(y2026_08=104.0)

        assert published.factor_for(date(2026, 9, 10)) == Decimal(1)
        assert published.is_after_reference(date(2026, 9, 10))

    def test_a_purchase_older_than_the_series_has_no_factor(self) -> None:
        """
        A month the series does not cover yields None, not a guess.
        """

        assert index(y1996_01=50.5, y2026_08=104.0).factor_for(date(1990, 5, 1)) is None

    def test_a_gap_inside_the_series_has_no_factor(self) -> None:
        """
        A month missing mid-series is absent, not interpolated.
        """

        assert index(y2019_06=80.0, y2026_08=104.0).factor_for(date(2020, 4, 1)) is None

    @pytest.mark.parametrize(
        ("day", "expected"),
        [
            (date(2026, 8, 1), "2026-08"),
            (date(1996, 1, 31), "1996-01"),
            (date(2026, 12, 25), "2026-12"),
            (date(999, 3, 4), "0999-03"),
        ],
    )
    def test_a_date_maps_to_its_zero_padded_period(
        self, day: date, expected: str
    ) -> None:
        """
        The period is built from the date, never parsed back from text.

        One-way conversion is what keeps the ``YYYY-MM`` ambiguity out:
        a single-digit month has to pad to match the published keys, and
        a period is never read as a date.
        """

        assert InflationIndex.period_of(day) == expected
