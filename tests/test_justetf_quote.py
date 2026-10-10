# Standard libraries
import json

# 3pps
import pytest

# Own modules
from degiro_tools.domain.models import PriceQuote, PriceSource, PriceUnavailable
from degiro_tools.providers import justetf, quotes
from degiro_tools.providers.justetf import fetch_eur_quote, quote_url
from degiro_tools.providers.quotes import resolve_quote

# The payload the endpoint really answers with, measured against a live
# position. Kept verbatim rather than trimmed to the fields that are
# read: the parsing has to survive the whole document, extra keys
# included, because the endpoint is undocumented and may add more.
WORLD_QUOTE: dict[str, object] = {
    "latestQuote": {"raw": 46.71, "localized": "46.71"},
    "latestQuoteDate": "2026-09-15",
    "previousQuote": {"raw": 46.92, "localized": "46.92"},
    "previousQuoteDate": "2026-09-14",
    "dtdPrc": {"raw": -0.45, "localized": "-0.45"},
    "dtdAmt": {"raw": -0.21, "localized": "-0.21"},
    "quoteTradingVenue": "XETRA",
    "quoteLowHigh": {
        "low": {"raw": 36.27, "localized": "36.27"},
        "high": {"raw": 50.12, "localized": "50.12"},
    },
}

ISIN: str = "IE00BKM4GZ66"


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

    monkeypatch.setattr(justetf, "http_get", forbidden)


def stub_http(monkeypatch: pytest.MonkeyPatch, body: bytes | None) -> list[str]:
    """
    Replace the transport with one canned response.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        body: Body to answer with, or None to answer as a failure (a
            404, a 429 and an unreachable host all look the same here).

    Returns:
        List the requested URLs are appended to, in call order.
    """

    calls: list[str] = []

    def fake_get(url: str) -> bytes | None:
        calls.append(url)
        return body

    monkeypatch.setattr(justetf, "http_get", fake_get)

    return calls


def payload(**overrides: object) -> bytes:
    """
    Encode the measured payload with selected fields replaced.

    Args:
        **overrides: Top-level fields to replace or add.

    Returns:
        The document as a UTF-8 encoded JSON body.
    """

    document = dict(WORLD_QUOTE)
    document.update(overrides)

    return json.dumps(document).encode("utf-8")


class TestQuoteUrl:
    """
    Building the quote endpoint URL.
    """

    def test_the_isin_travels_in_the_path_and_the_query(self) -> None:
        """
        Both places the endpoint expects the ISIN carry it.

        The endpoint answers the path form alone but the owner measured
        it with both, so both are sent rather than discovering later
        which one it actually honours.
        """

        url = quote_url(ISIN)

        assert f"/etfs/{ISIN}/quote" in url
        assert f"isin={ISIN}" in url

    def test_the_euro_currency_is_requested_explicitly(self) -> None:
        """
        The price must arrive already converted, not in fund currency.

        This is the whole reason the provider leads the chain: asking
        for euros removes the exchange-rate step, and with it the
        failure mode where a rate limit left positions unvalued.
        """

        assert "currency=EUR" in quote_url(ISIN)

    def test_an_isin_from_a_spreadsheet_cannot_escape_the_path(self) -> None:
        """
        A value that is not an ISIN is encoded, not concatenated.

        The ISIN comes from a workbook the tool did not write, so it is
        untrusted input on its way into a URL.
        """

        url = quote_url("../../secret?x=1")

        assert "../.." not in url
        assert "?x=1" not in url


class TestJustETFQuote:
    """
    Reading one price from the quote endpoint.
    """

    def test_the_latest_quote_becomes_a_euro_price(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        The measured payload yields the price the owner measured.
        """

        stub_http(monkeypatch, payload())

        quote = fetch_eur_quote(ISIN)

        assert quote is not None
        assert quote.price_eur == pytest.approx(46.71)
        assert quote.currency == "EUR"
        assert quote.unavailable is None
        assert quote.source is PriceSource.JUSTETF

    def test_the_native_price_is_the_euro_price(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        No conversion happened, so both prices are the same number.

        Guards the claim the chain rests on: if these ever diverged, a
        rate would have crept back into a path that must not need one.
        """

        stub_http(monkeypatch, payload())

        quote = fetch_eur_quote(ISIN)

        assert quote is not None
        assert quote.native_price == quote.price_eur

    @pytest.mark.parametrize(
        "latest",
        [
            None,
            {},
            {"raw": None},
            {"raw": 0},
            {"raw": -12.5},
            {"raw": "46.71"},
            {"raw": True},
            "46.71",
        ],
        ids=[
            "field-absent",
            "empty-object",
            "null-price",
            "zero-price",
            "negative-price",
            "price-as-text",
            "price-as-boolean",
            "object-replaced-by-text",
        ],
    )
    def test_an_unusable_price_yields_no_quote(
        self, monkeypatch: pytest.MonkeyPatch, latest: object
    ) -> None:
        """
        Every malformed shape degrades to None instead of raising.

        The endpoint is undocumented, so the parse must treat its own
        expectations as assumptions. A crash here would take down a
        report that Yahoo could still have completed.
        """

        stub_http(monkeypatch, payload(latestQuote=latest))

        assert fetch_eur_quote(ISIN) is None

    def test_an_html_challenge_page_yields_no_quote(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A non-JSON body is reported as unusable, not decoded blindly.
        """

        stub_http(monkeypatch, b"<html><body>Are you a robot?</body></html>")

        assert fetch_eur_quote(ISIN) is None

    def test_a_transport_failure_yields_no_quote(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A 404 or a timeout is a miss, which the chain answers by falling
        back.
        """

        calls = stub_http(monkeypatch, None)

        assert fetch_eur_quote(ISIN) is None
        assert len(calls) == 1


def stub_chain(
    monkeypatch: pytest.MonkeyPatch,
    justetf_quote: PriceQuote | None,
    yahoo_quote: PriceQuote | None = None,
) -> dict[str, int]:
    """
    Replace both providers of the chain with canned answers.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        justetf_quote: What justETF answers, or None for a miss.
        yahoo_quote: What Yahoo answers. Defaults to a quote carrying no
            price, which is what Yahoo returns when it cannot resolve
            the ISIN either.

    Returns:
        Call counter per provider, so a test can assert one was never
        reached.
    """

    if yahoo_quote is None:
        yahoo_quote = PriceQuote(
            isin=ISIN,
            price_eur=None,
            native_price=None,
            currency="",
            unavailable=PriceUnavailable.NO_TICKER,
        )

    calls = {"justetf": 0, "yahoo": 0}

    def fake_justetf(_isin: str) -> PriceQuote | None:
        calls["justetf"] += 1
        return justetf_quote

    def fake_yahoo(_isin: str, _rate: float | None) -> PriceQuote:
        calls["yahoo"] += 1
        return yahoo_quote

    monkeypatch.setattr(quotes, "fetch_eur_quote", fake_justetf)
    monkeypatch.setattr(quotes, "fetch_yahoo_quote", fake_yahoo)

    return calls


def justetf_priced(price: float = 46.71) -> PriceQuote:
    """
    Build the quote justETF returns on success.

    Args:
        price: Price in euro.

    Returns:
        A euro quote attributed to justETF.
    """

    return PriceQuote(
        isin=ISIN,
        price_eur=price,
        native_price=price,
        currency="EUR",
        unavailable=None,
        source=PriceSource.JUSTETF,
    )


def yahoo_priced(price: float = 46.50) -> PriceQuote:
    """
    Build the quote Yahoo returns on success.

    Args:
        price: Price in euro.

    Returns:
        A euro quote attributed to Yahoo.
    """

    return PriceQuote(
        isin=ISIN,
        price_eur=price,
        native_price=price,
        currency="EUR",
        unavailable=None,
        source=PriceSource.YAHOO,
    )


class TestResolutionChain:
    """
    Cache, then justETF, then Yahoo, stopping at the first answer.
    """

    def test_justetf_wins_and_yahoo_is_never_asked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        The preferred source short-circuits the rest of the chain.
        """

        calls = stub_chain(monkeypatch, justetf_priced())

        quote = resolve_quote(ISIN, 0.86)

        assert quote.price_eur == pytest.approx(46.71)
        assert quote.source is PriceSource.JUSTETF
        assert calls == {"justetf": 1, "yahoo": 0}

    def test_yahoo_covers_what_justetf_does_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A justETF miss falls through instead of losing the position.

        This is the property that matters more than which source is
        faster: a provider having a bad afternoon must not amputate rows
        from the report.
        """

        calls = stub_chain(monkeypatch, None, yahoo_priced())

        quote = resolve_quote(ISIN, 0.86)

        assert quote.price_eur == pytest.approx(46.50)
        assert quote.source is PriceSource.YAHOO
        assert calls == {"justetf": 1, "yahoo": 1}

    def test_both_failing_still_yields_a_quote(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        With no source left the position is reported, not dropped.

        A quote with no price and a stated reason is what keeps the row
        in the table reading ``n/d`` rather than vanishing.
        """

        stub_chain(monkeypatch, None)

        quote = resolve_quote(ISIN, None)

        assert quote.price_eur is None
        assert quote.unavailable is PriceUnavailable.NO_TICKER

    def test_the_second_lookup_is_served_from_the_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A repeated request inside the window costs no provider call.

        Fifteen minutes is short enough that the cache adds no
        meaningful staleness to an already delayed feed, and long enough
        that re-running a report is free.
        """

        calls = stub_chain(monkeypatch, justetf_priced())

        first = resolve_quote(ISIN, 0.86)
        second = resolve_quote(ISIN, 0.86)

        assert calls == {"justetf": 1, "yahoo": 0}
        assert second.price_eur == first.price_eur

    def test_the_cached_quote_keeps_its_provenance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Provenance survives the round trip through the cache file.

        Without this the table would attribute a cached Yahoo price to
        justETF, which is exactly the sort of quiet mislabelling the
        provenance line exists to prevent.
        """

        stub_chain(monkeypatch, None, yahoo_priced())
        resolve_quote(ISIN, 0.86)

        stub_chain(monkeypatch, None)

        assert resolve_quote(ISIN, 0.86).source is PriceSource.YAHOO

    def test_refresh_bypasses_the_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """
        A zero window forces a fresh request, which is what --refresh does.
        """

        calls = stub_chain(monkeypatch, justetf_priced())

        resolve_quote(ISIN, 0.86)
        resolve_quote(ISIN, 0.86, max_age_seconds=0)

        assert calls == {"justetf": 2, "yahoo": 0}

    def test_a_failure_is_not_remembered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """
        Only a resolved price is cached, so the next run retries.

        Caching a miss would turn one bad afternoon into fifteen minutes
        of a position the report cannot value.
        """

        stub_chain(monkeypatch, None)
        resolve_quote(ISIN, None)

        calls = stub_chain(monkeypatch, justetf_priced())

        assert resolve_quote(ISIN, None).price_eur == pytest.approx(46.71)
        assert calls["justetf"] == 1

    def test_an_isin_unfit_for_a_file_name_skips_the_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A malformed identifier is looked up but never becomes a path.

        The value arrives from a workbook, so it is not allowed to
        decide where the tool writes.
        """

        calls = stub_chain(monkeypatch, justetf_priced())

        resolve_quote("../../etc/passwd", None)
        resolve_quote("../../etc/passwd", None)

        assert calls["justetf"] == 2
