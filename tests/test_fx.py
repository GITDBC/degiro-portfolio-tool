# Standard libraries
import logging
from datetime import date
from pathlib import Path

# 3pps
import pytest

# Own modules
from degiro_tools.providers import fx
from degiro_tools.providers.fx import (
    ECB_URL,
    SOURCE_ECB,
    SOURCE_YAHOO,
    YAHOO_URL,
    describe,
    fetch_ecb_rate,
    fetch_yahoo_rate,
    resolve_usd_eur_rate,
)

# The observation the ECB actually published, and the rate the codebase
# needs. The series is USD per EUR, so the conversion this project
# applies to a dollar price is its reciprocal.
ECB_USD_PER_EUR: float = 1.1605
ECB_USD_TO_EUR: float = 0.861698

# Header of the SDMX csvdata response, trimmed to the columns that are
# read plus enough neighbours to prove the parse is by name and not by
# position.
_ECB_HEADER: str = "KEY,FREQ,CURRENCY,CURRENCY_DENOM,TIME_PERIOD,OBS_VALUE,OBS_STATUS"


def ecb_csv(*observations: tuple[str, str]) -> bytes:
    """
    Build an SDMX csvdata body carrying the given observations.

    Args:
        *observations: Date and value pairs, oldest first, exactly as
            the columns spell them.

    Returns:
        The response body, encoded as the service sends it.
    """

    rows = [
        f"EXR.D.USD.EUR.SP00.A,D,USD,EUR,{when},{value},A"
        for when, value in observations
    ]

    return "\n".join([_ECB_HEADER, *rows]).encode("utf-8")


def yahoo_json(closes: str, timestamps: str = "[1787180400]") -> bytes:
    """
    Build a chart response carrying the given close series.

    Args:
        closes: JSON array of closes, gaps included as ``null``.
        timestamps: JSON array of epoch seconds parallel to the closes.

    Returns:
        The response body, encoded as the endpoint sends it.
    """

    body = (
        '{"chart":{"error":null,"result":[{"timestamp":'
        + timestamps
        + ',"indicators":{"quote":[{"close":'
        + closes
        + "}]}}]}}"
    )

    return body.encode("utf-8")


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """
    Point the shared cache at a temporary directory for every test.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        tmp_path: Temporary directory provided by pytest.

    Returns:
        None.
    """

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Fail loudly if a test reaches for the network.

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        None.
    """

    def forbidden(url: str) -> bytes | None:
        message = f"Un test intentó salir a la red: {url}"
        raise AssertionError(message)

    monkeypatch.setattr(fx, "_http_get", forbidden)


def stub_http(
    monkeypatch: pytest.MonkeyPatch, responses: dict[str, bytes | None]
) -> list[str]:
    """
    Replace the HTTP layer with canned responses per URL.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        responses: Body to answer per URL, or None to answer as a
            failure (a 429, a 5xx or an unreachable host all look the
            same to the caller).

    Returns:
        List the requested URLs are appended to, in call order.
    """

    calls: list[str] = []

    def fake_get(url: str) -> bytes | None:
        calls.append(url)
        return responses.get(url)

    monkeypatch.setattr(fx, "_http_get", fake_get)

    return calls


class TestYahooSource:
    """
    Reading the rate from Yahoo's chart endpoint.
    """

    def test_last_close_is_taken(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """
        The most recent close of the series is the rate.
        """

        stub_http(
            monkeypatch,
            {YAHOO_URL: yahoo_json("[0.8669,0.8553]", "[1787094000,1787180400]")},
        )

        rate = fetch_yahoo_rate()

        assert rate is not None
        assert rate.rate == pytest.approx(0.8553)
        assert rate.source == SOURCE_YAHOO
        assert rate.as_of == date(2026, 8, 19)

    def test_a_gap_in_the_series_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A null close is a session without data, not a rate of zero.

        Yahoo really does return nulls mid-series, so taking the last
        element blindly would value the whole portfolio at nothing.
        """

        stub_http(
            monkeypatch,
            {YAHOO_URL: yahoo_json("[0.8669,null]", "[1787094000,1787180400]")},
        )

        rate = fetch_yahoo_rate()

        assert rate is not None
        assert rate.rate == pytest.approx(0.8669)
        assert rate.as_of == date(2026, 8, 18)

    def test_a_rate_limit_yields_no_rate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """
        An HTTP failure is None, not an exception about a missing field.
        """

        stub_http(monkeypatch, {YAHOO_URL: None})

        assert fetch_yahoo_rate() is None

    @pytest.mark.parametrize(
        "body",
        [
            b"no soy json",
            b"{}",
            b'{"chart":{"error":"Too Many Requests","result":null}}',
            b'{"chart":{"error":null,"result":[]}}',
            b'{"chart":{"error":null,"result":[{"indicators":{"quote":[]}}]}}',
            b'{"chart":{"error":null,"result":[{"indicators":{"quote":[{"close":7}]}}]}}',
            b'{"chart":{"error":null,"result":[{"indicators":{"quote":[{"close":[]}]}}]}}',
            b'{"chart":{"error":null,"result":[{"indicators":{"quote":'
            b'[{"close":[null]}]}}]}}',
        ],
    )
    def test_a_malformed_payload_yields_no_rate(
        self, monkeypatch: pytest.MonkeyPatch, body: bytes
    ) -> None:
        """
        Every shape but the documented one is treated as no answer.
        """

        stub_http(monkeypatch, {YAHOO_URL: body})

        assert fetch_yahoo_rate() is None

    def test_a_missing_timestamp_is_a_rate_without_a_date(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A usable close with no timestamp is still a usable rate.
        """

        stub_http(monkeypatch, {YAHOO_URL: yahoo_json("[0.8553]", "[]")})

        rate = fetch_yahoo_rate()

        assert rate is not None
        assert rate.as_of is None


class TestEcbSource:
    """
    Reading the rate from the ECB reference series.
    """

    def test_the_published_rate_is_inverted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        1.1605 USD per EUR is 0.8617 EUR per USD.

        This is the assertion that matters most in the file. The series
        is dollars per euro and the report converts dollars into euro,
        so publishing it uninverted would leave every valuation off by
        35% while looking entirely plausible.
        """

        stub_http(monkeypatch, {ECB_URL: ecb_csv(("2026-08-19", str(ECB_USD_PER_EUR)))})

        rate = fetch_ecb_rate()

        assert rate is not None
        assert rate.rate == pytest.approx(ECB_USD_TO_EUR, abs=1e-6)
        assert rate.rate < 1.0
        assert rate.source == SOURCE_ECB
        assert rate.as_of == date(2026, 8, 19)

    def test_the_latest_observation_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """
        Five observations arrive oldest first; the newest is the rate.
        """

        stub_http(
            monkeypatch,
            {
                ECB_URL: ecb_csv(
                    ("2026-08-13", "1.1534"),
                    ("2026-08-18", "1.1576"),
                    ("2026-08-19", str(ECB_USD_PER_EUR)),
                )
            },
        )

        rate = fetch_ecb_rate()

        assert rate is not None
        assert rate.rate == pytest.approx(ECB_USD_TO_EUR, abs=1e-6)
        assert rate.as_of == date(2026, 8, 19)

    def test_a_stale_fixing_is_still_reported_with_its_date(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        The series skips weekends, so the rate can be days old.

        That is acceptable for valuing a portfolio as long as the date
        travels with it, which is what lets the report say so.
        """

        stub_http(monkeypatch, {ECB_URL: ecb_csv(("2026-08-14", str(ECB_USD_PER_EUR)))})

        rate = fetch_ecb_rate()

        assert rate is not None
        assert rate.as_of == date(2026, 8, 14)

    def test_unusable_rows_are_skipped_not_fatal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A gap row does not invalidate the observations around it.
        """

        stub_http(
            monkeypatch,
            {
                ECB_URL: ecb_csv(
                    ("2026-08-19", str(ECB_USD_PER_EUR)),
                    ("2026-08-20", ""),
                )
            },
        )

        rate = fetch_ecb_rate()

        assert rate is not None
        assert rate.as_of == date(2026, 8, 19)

    @pytest.mark.parametrize(
        "body",
        [
            b"",
            b"KEY,FREQ\nEXR.D.USD.EUR.SP00.A,D",
            b"KEY,TIME_PERIOD,OBS_VALUE\nEXR,2026-08-19,",
            b"KEY,TIME_PERIOD,OBS_VALUE\nEXR,2026-08-19,no-un-numero",
            b"KEY,TIME_PERIOD,OBS_VALUE\nEXR,2026-08-19,0",
            b"KEY,TIME_PERIOD,OBS_VALUE\nEXR,2026-08-19,-1.16",
            b"\xff\xfe not utf-8 \xff",
        ],
    )
    def test_a_malformed_payload_yields_no_rate(
        self, monkeypatch: pytest.MonkeyPatch, body: bytes
    ) -> None:
        """
        A response without a usable observation is treated as no answer.

        A zero or a negative rate is rejected on purpose: dividing by it
        would either raise or invert the sign of every valuation.
        """

        stub_http(monkeypatch, {ECB_URL: body})

        assert fetch_ecb_rate() is None

    def test_an_unreadable_date_is_a_rate_without_a_date(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A usable value with an unparseable date is still usable.
        """

        stub_http(monkeypatch, {ECB_URL: ecb_csv(("ayer", str(ECB_USD_PER_EUR)))})

        rate = fetch_ecb_rate()

        assert rate is not None
        assert rate.as_of is None


class TestResolutionChain:
    """
    Choosing between the sources, and remembering the answer.
    """

    def test_yahoo_answers_and_the_ecb_is_never_asked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        The chain stops at the first source that answers.
        """

        calls = stub_http(monkeypatch, {YAHOO_URL: yahoo_json("[0.8553]")})

        rate = resolve_usd_eur_rate()

        assert rate is not None
        assert rate.source == SOURCE_YAHOO
        assert calls == [YAHOO_URL]

    def test_a_429_from_yahoo_hands_over_to_the_ecb(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        This is the production failure, and the portfolio must survive it.

        Yahoo refusing every symbol used to drop every USD-denominated
        position from the report. Now the official reference rate takes
        over, and the report says which source it came from.
        """

        calls = stub_http(
            monkeypatch,
            {
                YAHOO_URL: None,
                ECB_URL: ecb_csv(("2026-08-19", str(ECB_USD_PER_EUR))),
            },
        )

        rate = resolve_usd_eur_rate()

        assert rate is not None
        assert rate.rate == pytest.approx(ECB_USD_TO_EUR, abs=1e-6)
        assert rate.source == SOURCE_ECB
        assert rate.as_of == date(2026, 8, 19)
        assert calls == [YAHOO_URL, ECB_URL]

    def test_yahoo_is_not_retried_after_refusing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A source that just refused is asked exactly once.
        """

        calls = stub_http(
            monkeypatch,
            {
                YAHOO_URL: None,
                ECB_URL: ecb_csv(("2026-08-19", str(ECB_USD_PER_EUR))),
            },
        )

        resolve_usd_eur_rate()

        assert calls.count(YAHOO_URL) == 1

    def test_both_sources_failing_yields_no_rate(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """
        With no source reachable the rate is genuinely unavailable.
        """

        stub_http(monkeypatch, {YAHOO_URL: None, ECB_URL: None})

        with caplog.at_level(logging.WARNING):
            assert resolve_usd_eur_rate() is None

        assert "tipo de cambio USD/EUR" in caplog.text

    def test_the_rate_is_fetched_once_per_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A second caller in the same run reads the cache, not the network.

        The valuation and the look-through both need the rate, and asking
        twice is how a report earns a rate limit.
        """

        calls = stub_http(monkeypatch, {YAHOO_URL: yahoo_json("[0.8553]")})

        first = resolve_usd_eur_rate()
        second = resolve_usd_eur_rate()

        assert first == second
        assert calls == [YAHOO_URL]

    def test_the_cached_rate_keeps_its_provenance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A rate served from the cache still names its original source.
        """

        stub_http(
            monkeypatch,
            {
                YAHOO_URL: None,
                ECB_URL: ecb_csv(("2026-08-19", str(ECB_USD_PER_EUR))),
            },
        )
        resolve_usd_eur_rate()

        cached = resolve_usd_eur_rate()

        assert cached is not None
        assert cached.source == SOURCE_ECB
        assert cached.as_of == date(2026, 8, 19)
        assert cached.rate == pytest.approx(ECB_USD_TO_EUR, abs=1e-6)

    def test_an_expired_entry_is_refetched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        An age of zero is how a forced refresh is expressed.
        """

        calls = stub_http(monkeypatch, {YAHOO_URL: yahoo_json("[0.8553]")})

        resolve_usd_eur_rate()
        resolve_usd_eur_rate(max_age_seconds=0)

        assert calls == [YAHOO_URL, YAHOO_URL]

    @pytest.mark.parametrize(
        "stored",
        [
            "no soy json",
            "[]",
            '{"source":"ecb_reference_rate"}',
            '{"rate":0,"source":"ecb_reference_rate"}',
            '{"rate":-1,"source":"ecb_reference_rate"}',
            '{"rate":0.86,"source":""}',
            '{"rate":"0.86","source":"ecb_reference_rate"}',
        ],
    )
    def test_a_corrupt_cache_entry_is_a_miss(
        self, monkeypatch: pytest.MonkeyPatch, stored: str
    ) -> None:
        """
        An unusable stored rate costs a fetch, never a wrong valuation.
        """

        fx.write_cache(fx._CACHE_ENTRY, stored)
        calls = stub_http(monkeypatch, {YAHOO_URL: yahoo_json("[0.8553]")})

        rate = resolve_usd_eur_rate()

        assert rate is not None
        assert rate.rate == pytest.approx(0.8553)
        assert calls == [YAHOO_URL]


class TestDescription:
    """
    Stating the rate, its source and its date in the report.
    """

    def test_a_yahoo_rate_names_yahoo_and_its_date(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        The reader must be able to see which rate produced the numbers.
        """

        stub_http(monkeypatch, {YAHOO_URL: yahoo_json("[0.8553]", "[1787180400]")})

        text = describe(fetch_yahoo_rate())

        assert "0.855300" in text
        assert "Yahoo" in text
        assert "2026-08-19" in text

    def test_an_ecb_rate_names_the_ecb_and_the_fixing_date(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A fallback rate is disclosed as such, not passed off as live.
        """

        stub_http(monkeypatch, {ECB_URL: ecb_csv(("2026-08-19", str(ECB_USD_PER_EUR)))})

        text = describe(fetch_ecb_rate())

        assert "0.861698" in text
        assert "BCE" in text
        assert "2026-08-19" in text

    def test_no_rate_reads_as_not_available(self) -> None:
        """
        An absent rate is n/d, the vocabulary the reports already use.
        """

        assert "n/d" in describe(None)
