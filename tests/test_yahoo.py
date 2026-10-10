# Standard libraries
import inspect

# 3pps
import pytest

# Own modules
from degiro_tools.analysis import holdings as analysis_holdings
from degiro_tools.domain.models import PriceUnavailable
from degiro_tools.providers import yahoo
from degiro_tools.providers.yahoo import (
    fetch_quote,
    get_price_eur,
    get_stock_sector_country,
    is_etf,
)


@pytest.mark.parametrize(
    "product_name",
    [
        "Global X Robotics & Artificial Intelligence UCITS ETF",
        "State Street SPDR S&P 500 UCITS ETF",
        "First Trust NASDAQ-100 Equal Weighted ETF",
        "J.P. Morgan Global Equity Premium Income UCITS ETF",
        "Goldman Sachs Physical Gold ETF",
        "Franklin Templeton FTSE India UCITS ETF",
    ],
)
def test_is_etf_detects_multi_word_brands(product_name: str) -> None:
    """
    Multi-word provider brands are recognized as ETFs.
    """

    assert is_etf(product_name)


@pytest.mark.parametrize(
    "product_name",
    [
        "iShares Core MSCI World UCITS ETF USD (Acc)",
        "VANGUARD FTSE All-World UCITS ETF",
        "Xtrackers MSCI World UCITS ETF 1C",
    ],
)
def test_is_etf_detects_single_word_brands(product_name: str) -> None:
    """
    Single-word provider brands are still recognized as ETFs.
    """

    assert is_etf(product_name)


def test_is_etf_does_not_match_plain_stock() -> None:
    """
    A genuine stock name must not be flagged as an ETF.
    """

    assert not is_etf("INDRA SISTEMAS SA")


class StubTicker:
    """
    Minimal yfinance.Ticker stand-in for deterministic tests.
    """

    def __init__(
        self,
        fast_info: dict[str, object] | None = None,
        info: dict[str, object] | None = None,
        *,
        fast_raises: bool = False,
    ) -> None:
        """
        Store canned payloads and the optional fast_info failure flag.
        """

        self._fast_info = fast_info or {}
        self._info = info or {}
        self._fast_raises = fast_raises

    @property
    def fast_info(self) -> dict[str, object]:
        """
        Return canned fast_info or raise to trigger the fallback.
        """

        if self._fast_raises:
            raise KeyError("fast_info unavailable")

        return self._fast_info

    @property
    def info(self) -> dict[str, object]:
        """
        Return the canned full info payload.
        """

        return self._info


def test_get_usd_eur_rate_moved_to_the_fx_module() -> None:
    """
    The exchange rate no longer comes from Yahoo's fast_info.

    Reading it through ``fast_info`` is what turned a plain HTTP 429
    into a KeyError('exchangeTimezoneName') and dropped every
    USD-denominated position from the report. The rate now has its own
    module with a fallback source, so this one must not grow back.
    """

    assert not hasattr(yahoo, "get_usd_eur_rate")
    assert "fast_info" not in inspect.getsource(yahoo.fetch_quote)


def test_get_price_eur_converts_usd_with_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A USD price is converted to EUR using the supplied rate.
    """

    monkeypatch.setattr(yahoo, "get_ticker_from_isin", lambda _isin: "AAPL")
    monkeypatch.setattr(
        yahoo.yf,
        "Ticker",
        lambda _symbol: StubTicker({"lastPrice": 100.0, "currency": "USD"}),
    )

    assert get_price_eur("ISIN_USD", 0.9) == pytest.approx(90.0)


def test_get_price_eur_keeps_eur_price_unconverted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A EUR price is returned as-is, even without a conversion rate.
    """

    monkeypatch.setattr(yahoo, "get_ticker_from_isin", lambda _isin: "ASML.AS")
    monkeypatch.setattr(
        yahoo.yf,
        "Ticker",
        lambda _symbol: StubTicker({"lastPrice": 50.0, "currency": "EUR"}),
    )

    assert get_price_eur("ISIN_EUR", None) == pytest.approx(50.0)


def test_get_price_eur_usd_without_rate_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A USD price with no rate cannot be converted and yields None.
    """

    monkeypatch.setattr(yahoo, "get_ticker_from_isin", lambda _isin: "AAPL")
    monkeypatch.setattr(
        yahoo.yf,
        "Ticker",
        lambda _symbol: StubTicker({"lastPrice": 100.0, "currency": "USD"}),
    )

    assert get_price_eur("ISIN_USD", None) is None


def test_get_price_eur_returns_none_when_no_ticker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    An unresolved ISIN yields None instead of crashing.
    """

    monkeypatch.setattr(yahoo, "get_ticker_from_isin", lambda _isin: None)

    assert get_price_eur("ISIN_UNKNOWN", 0.9) is None


def test_get_price_eur_falls_back_to_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    When fast_info fails, the info payload provides the price.
    """

    monkeypatch.setattr(yahoo, "get_ticker_from_isin", lambda _isin: "X")
    monkeypatch.setattr(
        yahoo.yf,
        "Ticker",
        lambda _symbol: StubTicker(
            info={"regularMarketPreviousClose": 10.0, "currency": "EUR"},
            fast_raises=True,
        ),
    )

    assert get_price_eur("ISIN_FALLBACK", None) == pytest.approx(10.0)


def test_quote_without_rate_keeps_the_native_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A USD position with no rate keeps the price its market quotes.

    The euro figure is the only thing missing, so the quantity and the
    dollar price stay available for the table to show instead of the
    position disappearing from the report.
    """

    monkeypatch.setattr(yahoo, "get_ticker_from_isin", lambda _isin: "AAPL")
    monkeypatch.setattr(
        yahoo.yf,
        "Ticker",
        lambda _symbol: StubTicker({"lastPrice": 100.0, "currency": "USD"}),
    )

    quote = fetch_quote("ISIN_USD", None)

    assert quote.price_eur is None
    assert quote.native_price == pytest.approx(100.0)
    assert quote.currency == "USD"
    assert quote.unavailable is PriceUnavailable.NO_EXCHANGE_RATE


def test_quote_reports_an_unresolved_ticker(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    An ISIN with no ticker is reported as such, not as a zero.
    """

    monkeypatch.setattr(yahoo, "get_ticker_from_isin", lambda _isin: None)

    quote = fetch_quote("ISIN_UNKNOWN", 0.9)

    assert quote.price_eur is None
    assert quote.native_price is None
    assert quote.unavailable is PriceUnavailable.NO_TICKER


def test_quote_reports_a_missing_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    A resolved ticker with no quote is reported as such.
    """

    monkeypatch.setattr(yahoo, "get_ticker_from_isin", lambda _isin: "X")
    monkeypatch.setattr(
        yahoo.yf,
        "Ticker",
        lambda _symbol: StubTicker(info={"currency": "EUR"}),
    )

    quote = fetch_quote("ISIN_NO_PRICE", 0.9)

    assert quote.price_eur is None
    assert quote.unavailable is PriceUnavailable.NO_QUOTE


def test_quote_in_euro_carries_no_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    A resolved euro price leaves the unavailability reason empty.
    """

    monkeypatch.setattr(yahoo, "get_ticker_from_isin", lambda _isin: "ASML.AS")
    monkeypatch.setattr(
        yahoo.yf,
        "Ticker",
        lambda _symbol: StubTicker({"lastPrice": 50.0, "currency": "EUR"}),
    )

    quote = fetch_quote("ISIN_EUR", None)

    assert quote.price_eur == pytest.approx(50.0)
    assert quote.unavailable is None


def test_get_stock_sector_country_returns_the_raw_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The provider hands its caller what Yahoo published, unaltered.

    Normalization belongs to the domain boundary, not to the provider,
    so the raw Morningstar-style label has to reach it intact.
    """

    monkeypatch.setattr(yahoo, "get_ticker_from_isin", lambda _isin: "ITX.MC")
    monkeypatch.setattr(
        yahoo.yf,
        "Ticker",
        lambda _symbol: StubTicker(
            info={"sector": "Consumer Cyclical", "country": "Spain"}
        ),
    )

    assert get_stock_sector_country("ES0148396007") == ("Consumer Cyclical", "Spain")


def test_yahoo_enriched_stock_arrives_canonical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A stock enriched from Yahoo enters the analysis already canonical.

    This is the whole Yahoo path: the provider returns its own taxonomy
    and ``fetch_stock_holding`` routes it through the domain
    normalization, so no consumer ever sees ``Consumer Cyclical`` beside
    the GICS bucket that means the same thing.
    """

    monkeypatch.setattr(
        analysis_holdings,
        "get_stock_sector_country",
        lambda _isin: ("Consumer Cyclical", "Spain"),
    )

    holding = analysis_holdings.fetch_stock_holding(
        "INDUSTRIA DE DISENO TEXTIL SA", "ES0148396007"
    )

    assert holding.sector == "Consumer Discretionary"
    assert holding.location == "Spain"


def test_yahoo_enriched_stock_resolves_a_long_country_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A long-form country from Yahoo normalizes like any other provider's.

    Yahoo occasionally spells the domicile out in full, which would
    otherwise become a country bucket of its own.
    """

    monkeypatch.setattr(
        analysis_holdings,
        "get_stock_sector_country",
        lambda _isin: ("Healthcare", "United States of America"),
    )

    holding = analysis_holdings.fetch_stock_holding("ACME INC", "US0000000000")

    assert holding.sector == "Health Care"
    assert holding.location == "United States"
