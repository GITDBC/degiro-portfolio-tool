# Standard libraries
import http.cookiejar
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Final

# Own modules
from ..domain.etf import ETFProfile
from ..domain.models import PriceQuote, PriceSource
from .cache import cache_age_seconds, describe_age, read_cached, write_cache
from .net import dig, http_get, positive_number

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

# Currency the quote endpoint is asked for and answers in.
_EUR: Final[str] = "EUR"

_SEARCH_URL: Final[str] = "https://www.justetf.com/en/search.html"

# Quote endpoint, undocumented but keyless and stable in shape. Split
# into a base and its query the way ``fx.ECB_URL`` is, so neither half
# needs a line continuation to stay inside the line length.
_QUOTE_BASE: Final[str] = "https://www.justetf.com/api/etfs"
_QUOTE_QUERY: Final[str] = "locale=en&currency=EUR"

# How long a fetched quote stays usable. Fifteen minutes: the feed
# behind it is already delayed, so a cache this short adds no
# meaningful staleness to what the reader is shown, while keeping the
# repeated runs of one session to a single request per position.
QUOTE_MAX_AGE_SECONDS: Final[float] = 15 * 60

# Wicket component path of the screener table. It is part of the page
# markup and may change when justETF redeploys; a change surfaces as a
# non-JSON response, which is reported as such instead of crashing.
_TABLE_COMPONENT: Final[str] = (
    "0-1.0-container-tabsContentContainer-tabsContentRepeater-1"
    "-container-content-etfsTablePanel"
)

_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# The screener answers with HTML instead of JSON unless the request
# carries the session cookie handed out by the search page, so the
# catalog download is always a GET followed by a POST on the same
# opener.
_QUERY: Final[dict[str, str | int]] = {
    "draw": 1,
    "start": 0,
    "length": -1,
    "lang": "en",
    "country": "DE",
    "universeType": "private",
    "defaultCurrency": "EUR",
}

_TIMEOUT_SECONDS: Final[int] = 90

# File the screener response is kept in between runs.
_CACHE_FILE: Final[str] = "justetf-screener.json.gz"

# How long a stored catalog stays usable. justETF recomputes returns and
# volatility once a day, so half a day keeps the report current while
# turning repeated runs into zero requests.
CATALOG_MAX_AGE_SECONDS: Final[float] = 12 * 60 * 60

# Network and transport failures worth reporting as a fetch error.
_FETCH_ERRORS: Final[tuple[type[Exception], ...]] = (
    OSError,
    urllib.error.URLError,
    UnicodeDecodeError,
)

# justETF field name for each attribute of ETFProfile.
_DECIMAL_FIELDS: Final[dict[str, str]] = {
    "ter_pct": "ter",
    "fund_size_meur": "fundSize",
    "return_ytd_pct": "ytdReturnCUR",
    "return_1y_pct": "yearReturnCUR",
    "return_3y_pct": "threeYearReturnCUR",
    "return_5y_pct": "fiveYearReturnCUR",
    "volatility_1y_pct": "yearVolatilityCUR",
    "volatility_3y_pct": "threeYearVolatilityCUR",
    "max_drawdown_3y_pct": "threeYearMaxDrawdownCUR",
    "return_per_risk_3y": "threeYearReturnPerRiskCUR",
}


class JustETFError(RuntimeError):
    """
    Raised when the justETF catalog cannot be downloaded or parsed.
    """


def _text(row: dict[str, object], key: str) -> str:
    """
    Read a field of a screener row as clean text.

    Args:
        row: Raw screener row.
        key: justETF field name.

    Returns:
        The trimmed value, with the ``<br />`` separators the source
        embeds replaced by spaces, or an empty string when the field is
        missing or not a string.
    """

    value = row.get(key)

    if not isinstance(value, str):
        return ""

    return " ".join(value.replace("<br />", " ").split())


def _decimal(row: dict[str, object], key: str) -> Decimal | None:
    """
    Read a numeric field of a screener row as a Decimal.

    justETF formats numbers for humans: percentages carry a ``%``,
    thousands a comma, and unavailable cells a dash.

    Args:
        row: Raw screener row.
        key: justETF field name.

    Returns:
        The parsed value, or ``None`` when the cell is empty, a dash or
        not a number.
    """

    raw = _text(row, key).replace("%", "").replace(",", "")

    if not raw or raw == "-":
        return None

    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _lending(row: dict[str, object]) -> bool | None:
    """
    Read whether the fund lends its securities.

    Args:
        row: Raw screener row.

    Returns:
        ``True`` or ``False`` when stated, ``None`` otherwise.
    """

    flag = _text(row, "hasSecuritiesLending").lower()

    if flag == "yes":
        return True

    if flag == "no":
        return False

    return None


# Closed vocabularies justETF publishes in English. Translated at the
# provider boundary so the domain, the tables and the CSV all speak one
# language. A value not listed here passes through untouched, which is
# the right failure mode if the source adds a category.
_VOCABULARY: Final[dict[str, str]] = {
    # Replication method.
    "Full replication": "Réplica física total",
    "Sampling": "Muestreo",
    "Optimized sampling": "Muestreo optimizado",
    "Physically backed": "Respaldo físico",
    "Swap based": "Basado en swap",
    "Swap based Unfunded": "Swap no financiado",
    "Physical & Synthetic": "Física y sintética",
    # Distribution policy.
    "Accumulating": "Acumulación",
    "Distributing": "Reparto",
    # Domicile.
    "Ireland": "Irlanda",
    "Luxembourg": "Luxemburgo",
    "Germany": "Alemania",
    "Switzerland": "Suiza",
    "France": "Francia",
    "Netherlands": "Países Bajos",
    "Sweden": "Suecia",
    "Bulgaria": "Bulgaria",
    "Austria": "Austria",
    "Belgium": "Bélgica",
    "Finland": "Finlandia",
    "Italy": "Italia",
    "Spain": "España",
    "Norway": "Noruega",
    "Denmark": "Dinamarca",
    "United Kingdom": "Reino Unido",
}


def _translated(value: str) -> str:
    """
    Translate a justETF vocabulary term into Spanish.

    Args:
        value: Term as published by the source.

    Returns:
        The Spanish term, or the original value when it is not a known
        category.
    """

    return _VOCABULARY.get(value, value)


def build_profile(row: dict[str, object]) -> ETFProfile | None:
    """
    Convert one raw screener row into an ``ETFProfile``.

    Args:
        row: Raw screener row as published by justETF.

    Returns:
        The parsed profile, or ``None`` when the row carries no ISIN
        and therefore cannot be matched to a position.
    """

    isin = _text(row, "isin")

    if not isin:
        return None

    numbers = {name: _decimal(row, key) for name, key in _DECIMAL_FIELDS.items()}

    return ETFProfile(
        isin=isin,
        name=_text(row, "name"),
        ticker=_text(row, "ticker"),
        replication=_translated(_text(row, "replicationMethod")),
        distribution_policy=_translated(_text(row, "distributionPolicy")),
        domicile=_translated(_text(row, "domicileCountry")),
        securities_lending=_lending(row),
        **numbers,
    )


def _open_session() -> urllib.request.OpenerDirector:
    """
    Build an HTTP opener that keeps the justETF session cookie.

    Returns:
        Opener with a cookie jar and a browser user agent, which the
        site requires to answer with JSON.
    """

    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    opener.addheaders = [
        ("User-Agent", _USER_AGENT),
        ("Accept-Language", "en-US,en;q=0.9"),
    ]

    return opener


def _download_catalog() -> str:
    """
    Download the raw screener payload from justETF.

    Returns:
        Response body as text.

    Raises:
        JustETFError: If any request fails at the transport level.
    """

    opener = _open_session()
    referer = f"{_SEARCH_URL}?search=ETFS"
    endpoint = f"{_SEARCH_URL}?{_TABLE_COMPONENT}&search=ETFS&_wicket=1"

    request = urllib.request.Request(
        endpoint,
        data=urllib.parse.urlencode(_QUERY).encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": referer,
        },
    )

    try:
        with opener.open(referer, timeout=_TIMEOUT_SECONDS) as session_response:
            session_response.read()

        with opener.open(request, timeout=_TIMEOUT_SECONDS) as response:
            body: str = response.read().decode("utf-8")
    except _FETCH_ERRORS as err:
        raise JustETFError(f"no se pudo consultar justETF: {err}") from err

    return body


def _catalog_payload(max_age_seconds: float) -> tuple[str, float | None]:
    """
    Get the screener payload, from cache when it is still fresh.

    The whole screener is one response and there is no way to ask it for
    a few ISINs: the endpoint honours a row limit but ignores every
    search parameter, and the positions wanted can sit anywhere in the
    table. Since the figures behind it are recomputed daily, keeping the
    answer on disk is what stops a repeated report from re-downloading
    several megabytes to read seven rows.

    Args:
        max_age_seconds: Age above which the cached copy is discarded.
            Zero forces a fresh download.

    Returns:
        Tuple of the payload and the age of the cached copy in seconds,
        which is None when it was just downloaded.
    """

    if max_age_seconds > 0:
        cached = read_cached(_CACHE_FILE, max_age_seconds)

        if cached is not None:
            return cached, cache_age_seconds(_CACHE_FILE)

    body = _download_catalog()
    write_cache(_CACHE_FILE, body)

    return body, None


def _parse_rows(body: str) -> list[object]:
    """
    Extract the list of screener rows from a raw payload.

    Args:
        body: Response body as text.

    Returns:
        The raw rows, still untyped.

    Raises:
        JustETFError: If the payload is not the expected JSON document,
            which is what happens when justETF changes its endpoint or
            answers with an HTML page.
    """

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as err:
        raise JustETFError(
            "justETF no devolvió JSON; probablemente haya cambiado su "
            "endpoint de búsqueda"
        ) from err

    if not isinstance(payload, dict):
        raise JustETFError("respuesta de justETF con formato inesperado")

    rows = payload.get("data")

    if not isinstance(rows, list):
        raise JustETFError("la respuesta de justETF no contiene la tabla de ETFs")

    return rows


def fetch_etf_catalog(
    max_age_seconds: float = CATALOG_MAX_AGE_SECONDS,
) -> dict[str, ETFProfile]:
    """
    Get the whole justETF screener indexed by ISIN.

    One response brings every fund the screener publishes, so looking up
    several positions costs a single download, and the copy is kept on
    disk so repeated reports cost none.

    Args:
        max_age_seconds: Age above which the cached copy is discarded.
            Zero forces a fresh download.

    Returns:
        Mapping ISIN to its profile.

    Raises:
        JustETFError: If the catalog cannot be downloaded or parsed.
    """

    body, age = _catalog_payload(max_age_seconds)

    catalog: dict[str, ETFProfile] = {}

    for row in _parse_rows(body):
        if not isinstance(row, dict):
            continue

        profile = build_profile(row)

        if profile is not None:
            catalog[profile.isin] = profile

    if not catalog:
        raise JustETFError("justETF devolvió un catálogo vacío")

    origin = "descargados" if age is None else f"en caché, {describe_age(age)}"
    _LOGGER.info("justETF: %d ETFs (%s).", len(catalog), origin)

    return catalog


def fetch_etf_profiles(
    isins: list[str],
    max_age_seconds: float = CATALOG_MAX_AGE_SECONDS,
) -> tuple[list[ETFProfile], list[str]]:
    """
    Look up the profiles of the given ISINs in the justETF catalog.

    Args:
        isins: ISINs to look up, in the order they should be reported.
        max_age_seconds: Age above which the cached catalog is
            discarded. Zero forces a fresh download.

    Returns:
        Tuple of the profiles found, in the requested order, and the
        ISINs the catalog does not cover. Individual securities, ETCs
        outside the screener and delisted share classes fall in the
        second list rather than aborting the lookup.

    Raises:
        JustETFError: If the catalog cannot be downloaded or parsed.
    """

    catalog = fetch_etf_catalog(max_age_seconds=max_age_seconds)

    found: list[ETFProfile] = []
    missing: list[str] = []

    for isin in isins:
        profile = catalog.get(isin)

        if profile is None:
            missing.append(isin)
            _LOGGER.warning("justETF no cubre el ISIN %s.", isin)
            continue

        found.append(profile)

    return found, missing


def quote_url(isin: str) -> str:
    """
    Build the quote endpoint URL for one ISIN.

    The ISIN is percent-encoded even though a valid one carries nothing
    that needs it: the value arrives from a spreadsheet, and a URL built
    by concatenating unvalidated input is how a path turns into
    something else.

    Args:
        isin: ISIN code of the instrument.

    Returns:
        Absolute URL asking for the EUR quote of that ISIN.
    """

    quoted = urllib.parse.quote(isin, safe="")

    return f"{_QUOTE_BASE}/{quoted}/quote?{_QUOTE_QUERY}&isin={quoted}"


def fetch_eur_quote(isin: str) -> PriceQuote | None:
    """
    Read the last price of an ISIN from justETF, already in euros.

    The endpoint honours the requested currency, so the price comes back
    converted and no exchange rate enters the valuation. That removes a
    step, and with it the failure mode where a rate-limited rate left
    every non-euro position unvalued.

    The payload is undocumented, so every field is read through the
    defensive walk rather than indexed: a redesign, an error envelope or
    an HTML challenge page must degrade to the next provider instead of
    raising.

    Args:
        isin: ISIN code of the instrument.

    Returns:
        The quote in EUR, or None when justETF did not answer with a
        usable one, in which case the caller falls back to Yahoo.
    """

    body = http_get(quote_url(isin))

    if body is None:
        return None

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        _LOGGER.debug("Respuesta de justETF para %s no es JSON: %s.", isin, err)
        return None

    price = positive_number(dig(payload, "latestQuote", "raw"))

    if price is None:
        _LOGGER.debug("justETF no devolvió precio utilizable para %s.", isin)
        return None

    _LOGGER.debug(
        "justETF: %s a %.4f EUR (%s, %s).",
        isin,
        price,
        dig(payload, "quoteTradingVenue") or "sin mercado",
        dig(payload, "latestQuoteDate") or "sin fecha",
    )

    return PriceQuote(
        isin=isin,
        price_eur=price,
        native_price=price,
        currency=_EUR,
        unavailable=None,
        source=PriceSource.JUSTETF,
    )
