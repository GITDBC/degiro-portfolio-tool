# Standard libraries
import logging
from decimal import Decimal
from typing import Final

# 3pps
import yfinance as yf
from yfinance.exceptions import YFException

# Own modules
from ..domain.constants import ETF_LIST_NAME, ISIN_MAPPING
from ..domain.etf import YahooETFData
from ..domain.models import PriceQuote, PriceSource, PriceUnavailable

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

# Errors yfinance may raise on missing response fields or on network /
# HTTP failures. OSError is the common base of ConnectionError,
# TimeoutError, requests.RequestException and curl_cffi.RequestsError,
# so it covers every transport-level failure; KeyError / AttributeError
# cover missing payload fields and YFException covers yfinance-specific
# errors. Catching these lets callers degrade gracefully instead of
# crashing on real network problems.
_YAHOO_ERRORS: Final[tuple[type[Exception], ...]] = (
    KeyError,
    AttributeError,
    OSError,
    YFException,
)

# Currency code whose prices must be converted to EUR.
_USD: Final[str] = "USD"


def get_ticker_from_isin(isin: str) -> str | None:
    """
    Resolve a Yahoo Finance ticker symbol from an ISIN.

    Applies manual overrides from ISIN_MAPPING first, then falls
    back to yfinance search. Network or HTTP failures are logged and
    yield None instead of propagating.

    Args:
        isin: ISIN code of the instrument.

    Returns:
        Ticker symbol, or None if not found or the lookup failed.
    """

    resolved_isin = ISIN_MAPPING.get(isin, isin)

    try:
        search = yf.Search(resolved_isin, max_results=1, news_count=0)
    except _YAHOO_ERRORS as err:
        _LOGGER.warning("Falló la búsqueda de ticker para %s: %s.", isin, err)
        return None

    if search.quotes:
        result: str = search.quotes[0]["symbol"]
        return result

    return None


def fetch_price_and_currency(ticker: str) -> tuple[float | None, str]:
    """
    Fetch the last price and currency for a Yahoo ticker.

    Tries the lightweight ``fast_info`` endpoint first and falls back
    to the fuller ``info`` payload (using the previous regular-market
    close) when fast data is unavailable. All failures are logged.

    Args:
        ticker: Yahoo Finance ticker symbol.

    Returns:
        Tuple of last price and currency code. The price is None when
        no quote could be retrieved; the currency is an empty string
        in that case.
    """

    ticker_data = yf.Ticker(ticker)

    try:
        fast_info = ticker_data.fast_info
        return fast_info["lastPrice"], fast_info["currency"]
    except _YAHOO_ERRORS as err:
        _LOGGER.debug(
            "fast_info no disponible para %s (%s); se recurre a info.",
            ticker,
            err,
        )

    try:
        info = ticker_data.info
        return info.get("regularMarketPreviousClose"), info.get("currency", "")
    except _YAHOO_ERRORS as err:
        _LOGGER.warning("No se pudo obtener el precio de %s: %s.", ticker, err)
        return None, ""


def fetch_quote(isin: str, eur_rate: float | None) -> PriceQuote:
    """
    Resolve an ISIN to its last price, in EUR when that is possible.

    Resolves the Yahoo ticker for the ISIN, fetches its last price and
    currency, and converts USD-denominated prices to EUR using
    ``eur_rate``. Every failure path yields a quote whose euro price is
    None and whose ``unavailable`` field says why, keeping the native
    price and its currency when those were retrieved: a position the
    account holds is still a position even when it cannot be valued in
    euro, and dropping its row is worse than showing what is known.

    Args:
        isin: ISIN code of the instrument.
        eur_rate: USD to EUR conversion rate, or None when it could
            not be retrieved.

    Returns:
        The quote, with ``price_eur`` set when the EUR price could be
        resolved and ``unavailable`` set when it could not.
    """

    ticker = get_ticker_from_isin(isin)

    if ticker is None:
        _LOGGER.warning("No se encontró ticker para %s.", isin)
        return PriceQuote(
            isin=isin,
            price_eur=None,
            native_price=None,
            currency="",
            unavailable=PriceUnavailable.NO_TICKER,
        )

    price, currency = fetch_price_and_currency(ticker)

    if price is None:
        _LOGGER.warning("Precio no disponible para %s.", isin)
        return PriceQuote(
            isin=isin,
            price_eur=None,
            native_price=None,
            currency=currency,
            unavailable=PriceUnavailable.NO_QUOTE,
        )

    if currency == _USD and eur_rate is None:
        _LOGGER.warning(
            "El precio de %s está en USD y no hay tipo de cambio USD/EUR "
            "disponible; la posición se reporta sin valor en euros.",
            isin,
        )
        return PriceQuote(
            isin=isin,
            price_eur=None,
            native_price=price,
            currency=currency,
            unavailable=PriceUnavailable.NO_EXCHANGE_RATE,
        )

    price_eur = price * eur_rate if currency == _USD and eur_rate is not None else price

    return PriceQuote(
        isin=isin,
        price_eur=price_eur,
        native_price=price,
        currency=currency,
        unavailable=None,
        source=PriceSource.YAHOO,
    )


def get_price_eur(isin: str, eur_rate: float | None) -> float | None:
    """
    Resolve an ISIN to its last price expressed in EUR.

    Thin wrapper over ``fetch_quote`` for the callers that only need the
    euro figure and have nothing to say about why it might be missing.

    Args:
        isin: ISIN code of the instrument.
        eur_rate: USD to EUR conversion rate, or None when it could
            not be retrieved.

    Returns:
        Last price in EUR, or None if the price (or a required
        conversion rate) could not be obtained.
    """

    return fetch_quote(isin, eur_rate).price_eur


def get_stock_sector_country(isin: str) -> tuple[str, str]:
    """
    Fetch the sector and domicile country for a single stock.

    Resolves the Yahoo ticker for the ISIN and reads its ``info``
    payload. Network or HTTP failures are logged and yield empty
    strings so enrichment never crashes the caller.

    Both values are returned exactly as Yahoo publishes them. Its
    sector taxonomy is Morningstar-derived rather than GICS
    ("Consumer Cyclical", "Financial Services", "Healthcare"), and its
    country field occasionally uses a long form ("United States of
    America"); the ``SECTOR_MAP`` and ``COUNTRY_MAP`` normalization
    applied downstream in ``domain.holdings.normalize_holding`` folds
    both into the canonical vocabulary, so no caller sees the raw
    labels.

    Args:
        isin: ISIN code of the stock.

    Returns:
        Tuple of raw Yahoo sector and country. Both are empty strings
        when the data could not be retrieved.
    """

    ticker = get_ticker_from_isin(isin)

    if ticker is None:
        _LOGGER.warning("No se encontró ticker para %s.", isin)
        return "", ""

    try:
        info = yf.Ticker(ticker).info
        return info.get("sector", ""), info.get("country", "")
    except _YAHOO_ERRORS as err:
        _LOGGER.warning("No se pudo enriquecer el valor %s: %s.", isin, err)
        return "", ""


def is_etf(product_name: str) -> bool:
    """
    Determine if a product is an ETF based on its provider name.

    Checks whether the normalized (lowercased) product name starts
    with, or contains as a whitespace-delimited phrase, any known
    ETF provider in ETF_LIST_NAME. This correctly handles
    multi-word brands such as "global x" or "goldman sachs", which
    a first-token comparison would miss.

    Args:
        product_name: Product name from the portfolio.

    Returns:
        True if the product matches a known ETF provider name.
    """

    normalized = product_name.lower().strip()
    padded = f" {normalized} "

    return any(
        normalized.startswith(brand) or f" {brand} " in padded
        for brand in ETF_LIST_NAME
    )


def _as_decimal(value: object) -> Decimal | None:
    """
    Convert a Yahoo payload number into a Decimal.

    Yahoo returns floats built from JSON, so the value is routed
    through ``str`` to avoid carrying binary float noise into a
    figure that will be shown to two decimals.

    Args:
        value: Raw value from the ``info`` payload.

    Returns:
        The value as Decimal, or None when it is absent or not a
        number.
    """

    if not isinstance(value, int | float) or isinstance(value, bool):
        return None

    return Decimal(str(value))


def fetch_etf_data(isin: str) -> YahooETFData | None:
    """
    Read the comparable ETF figures for an ISIN from Yahoo Finance.

    Fetches only the net expense ratio and the identity of the share
    class, which is all justETF can be contrasted against on equal
    terms. Returns are left out because Yahoo reports them in the
    fund's currency and annualizes the multi-year windows, and fund
    size because Yahoo measures the whole fund where justETF measures
    the share class.

    Args:
        isin: ISIN code of the fund.

    Returns:
        The Yahoo figures, or None when the ticker or its payload could
        not be retrieved.
    """

    ticker = get_ticker_from_isin(isin)

    if ticker is None:
        _LOGGER.warning("No se encontró ticker para %s.", isin)
        return None

    try:
        info = yf.Ticker(ticker).info
    except _YAHOO_ERRORS as err:
        _LOGGER.warning("No se pudieron leer los datos de %s en Yahoo: %s.", isin, err)
        return None

    return YahooETFData(
        isin=isin,
        ticker=ticker,
        name=str(info.get("longName") or info.get("shortName") or ""),
        ter_pct=_as_decimal(info.get("netExpenseRatio")),
        currency=str(info.get("currency", "")),
    )
