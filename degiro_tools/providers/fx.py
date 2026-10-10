# Standard libraries
import csv
import io
import json
import logging
from datetime import UTC, date, datetime
from typing import Any, Final

# Own modules
from ..domain.models import ExchangeRate
from .cache import read_cached, write_cache
from .net import dig, http_get

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

# Yahoo's documented chart endpoint for the USD/EUR pair. Read instead of
# ``Ticker.fast_info`` because fast_info scrapes a metadata blob and
# indexes it blindly: on an error response it raises KeyError naming a
# field that never arrived ('exchangeTimezoneName'), which reads as a
# bug in the pair rather than as the rate limit it actually is.
YAHOO_URL: Final[str] = (
    "https://query1.finance.yahoo.com/v8/finance/chart/USDEUR=X?range=5d&interval=1d"
)

# ECB reference rate for the same pair: official, keyless and stable.
# Published as SDMX CSV, one observation per business day. Note the
# direction: the D.USD.EUR series is US dollars per euro, the reciprocal
# of what a USD price has to be multiplied by to become EUR.
ECB_URL: Final[str] = (
    "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A"
    "?lastNObservations=5&format=csvdata"
)

# Provenance labels published with the rate. Stable strings: a consumer
# reconciling a valuation needs to know which source produced it.
SOURCE_YAHOO: Final[str] = "yahoo_chart"
SOURCE_ECB: Final[str] = "ecb_reference_rate"

# Cache entry holding the resolved rate, and how long it stays usable.
# A few hours is the right order: intraday drift on a major pair is well
# below the precision a portfolio report is read at, and it keeps one
# run (and the runs of an afternoon) to a single request.
_CACHE_ENTRY: Final[str] = "fx-usd-eur.json.gz"
FX_MAX_AGE_SECONDS: Final[float] = 6 * 3600

# Columns of the SDMX CSV this reads. The response carries thirty-odd
# columns of metadata; only these two are data.
_ECB_DATE_COL: Final[str] = "TIME_PERIOD"
_ECB_VALUE_COL: Final[str] = "OBS_VALUE"


def _http_get(url: str) -> bytes | None:
    """
    Fetch a URL through the shared transport.

    Kept as a function of this module rather than calling ``http_get``
    at each site: it is the single seam the exchange-rate tests replace
    to stub both providers and to forbid the network outright.

    Args:
        url: Absolute HTTPS URL to read.

    Returns:
        Response body, or None when the request failed for any reason.
    """

    return http_get(url)


def _first_number(values: list[Any]) -> tuple[int, float] | None:
    """
    Find the last usable number in a series of observations.

    Both sources leave gaps: Yahoo reports a null close for a session it
    has no data for, and the ECB publishes only business days. The most
    recent real number is the one wanted, so the series is walked from
    the end.

    Args:
        values: Observations as the payload delivered them, gaps
            included.

    Returns:
        Index and value of the last numeric observation, or None when
        the series holds no number at all.
    """

    for index in range(len(values) - 1, -1, -1):
        value = values[index]

        if isinstance(value, bool) or not isinstance(value, int | float):
            continue

        if value > 0:
            return index, float(value)

    return None


def fetch_yahoo_rate() -> ExchangeRate | None:
    """
    Read the USD to EUR rate from Yahoo's chart endpoint.

    Parses the documented payload and validates its shape at every step
    rather than indexing into it, so an error response, a rate limit or
    a change in the schema yields None with a debug line instead of an
    exception naming a field nobody asked for.

    Returns:
        The rate with its source and date, or None when Yahoo did not
        answer with a usable quote.
    """

    body = _http_get(YAHOO_URL)

    if body is None:
        return None

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        _LOGGER.debug("Respuesta de Yahoo no es JSON válido: %s.", err)
        return None

    closes, timestamps = _yahoo_series(payload)

    if closes is None:
        return None

    found = _first_number(closes)

    if found is None:
        _LOGGER.debug("Yahoo no devolvió ningún cierre utilizable para USD/EUR.")
        return None

    index, rate = found

    return ExchangeRate(
        rate=rate, source=SOURCE_YAHOO, as_of=_yahoo_date(timestamps, index)
    )


def _dig(payload: object, *path: str | int) -> object:
    """
    Walk a decoded JSON payload without trusting its shape.

    Thin alias over the shared helper, kept so the Yahoo parsing below
    reads the same as it did when this module owned the walk.

    Args:
        payload: Decoded JSON value to walk.
        *path: Mapping keys and list indices to follow, in order.

    Returns:
        The value at the end of the path, or None as soon as any step
        does not exist or does not have the expected container type.
    """

    return dig(payload, *path)


def _yahoo_series(payload: object) -> tuple[list[Any] | None, list[Any]]:
    """
    Extract the close series and its timestamps from a chart payload.

    Args:
        payload: Decoded JSON body of the chart endpoint.

    Returns:
        Tuple of the close series and its timestamps. The closes are
        None when the payload does not have the documented shape, in
        which case the timestamps are empty.
    """

    error = _dig(payload, "chart", "error")

    if error is not None:
        _LOGGER.debug("Yahoo devolvió un error para USD/EUR: %s.", error)
        return None, []

    closes = _dig(payload, "chart", "result", 0, "indicators", "quote", 0, "close")

    if not isinstance(closes, list):
        _LOGGER.debug("Respuesta de Yahoo sin serie de cierres para USD/EUR.")
        return None, []

    timestamps = _dig(payload, "chart", "result", 0, "timestamp")

    return closes, timestamps if isinstance(timestamps, list) else []


def _yahoo_date(timestamps: list[Any], index: int) -> date | None:
    """
    Date the chosen close belongs to.

    Args:
        timestamps: Epoch seconds parallel to the close series.
        index: Position of the close that was taken.

    Returns:
        The date of that observation, or None when the payload carries
        no usable timestamp for it.
    """

    if index >= len(timestamps):
        return None

    stamp = timestamps[index]

    if isinstance(stamp, bool) or not isinstance(stamp, int | float):
        return None

    return datetime.fromtimestamp(stamp, tz=UTC).date()


def fetch_ecb_rate() -> ExchangeRate | None:
    """
    Read the USD to EUR rate from the ECB reference series.

    The published series is US dollars per euro, so the value is
    inverted: a USD price must be multiplied by the reciprocal to become
    EUR. Getting that direction wrong would leave every valuation
    plausible and wrong, which is why the inversion is the one thing
    this function is explicit about.

    The series is a daily fixing published on business days, so the
    latest observation can be a day or two old. That is acceptable for
    valuing a portfolio, and the observation date travels with the rate
    so the report can say which day it is quoting.

    Returns:
        The inverted rate with its source and observation date, or None
        when the ECB did not answer with a usable observation.
    """

    body = _http_get(ECB_URL)

    if body is None:
        return None

    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as err:
        _LOGGER.debug("Respuesta del BCE no es UTF-8: %s.", err)
        return None

    observations = _ecb_observations(text)

    if not observations:
        return None

    as_of, usd_per_eur = observations[-1]

    # The series is USD per EUR; the report converts USD into EUR.
    return ExchangeRate(rate=1.0 / usd_per_eur, source=SOURCE_ECB, as_of=as_of)


def _ecb_observations(text: str) -> list[tuple[date | None, float]]:
    """
    Parse the usable observations out of an SDMX CSV response.

    Rows whose value or date cannot be read are skipped rather than
    failing the parse: the response is untrusted input and one unusable
    row does not invalidate the others.

    Args:
        text: Body of the CSV response.

    Returns:
        Observation date and value per usable row, oldest first. Empty
        when the response carries no usable observation.
    """

    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None or _ECB_VALUE_COL not in reader.fieldnames:
        _LOGGER.debug("Respuesta del BCE sin columna %s.", _ECB_VALUE_COL)
        return []

    observations: list[tuple[date | None, float]] = []

    for row in reader:
        try:
            value = float(row[_ECB_VALUE_COL])
        except (TypeError, ValueError):
            continue

        if value <= 0:
            continue

        observations.append((_parse_iso_date(row.get(_ECB_DATE_COL)), value))

    if not observations:
        _LOGGER.debug("El BCE no devolvió ninguna observación utilizable.")

    return observations


def _parse_iso_date(value: str | None) -> date | None:
    """
    Read an ISO date from a CSV cell.

    Args:
        value: Cell content, possibly absent or malformed.

    Returns:
        The date, or None when the cell does not hold one.
    """

    if not value:
        return None

    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _from_cache(max_age_seconds: float) -> ExchangeRate | None:
    """
    Read a previously resolved rate from the shared cache.

    Args:
        max_age_seconds: Age above which the stored rate is ignored.

    Returns:
        The stored rate, or None when there is nothing fresh and usable.
    """

    body = read_cached(_CACHE_ENTRY, max_age_seconds)

    if body is None:
        return None

    try:
        stored = json.loads(body)
    except json.JSONDecodeError:
        return None

    if not isinstance(stored, dict):
        return None

    rate = stored.get("rate")
    source = stored.get("source")

    if not isinstance(rate, int | float) or isinstance(rate, bool) or rate <= 0:
        return None

    if not isinstance(source, str) or not source:
        return None

    return ExchangeRate(
        rate=float(rate),
        source=source,
        as_of=_parse_iso_date(stored.get("as_of")),
    )


def _to_cache(rate: ExchangeRate) -> None:
    """
    Store a resolved rate for the rest of the run and the next ones.

    Args:
        rate: Rate to store, provenance included.

    Returns:
        None.
    """

    body = json.dumps(
        {
            "rate": rate.rate,
            "source": rate.source,
            "as_of": None if rate.as_of is None else rate.as_of.isoformat(),
        }
    )

    write_cache(_CACHE_ENTRY, body)


def resolve_usd_eur_rate(
    max_age_seconds: float = FX_MAX_AGE_SECONDS,
) -> ExchangeRate | None:
    """
    Resolve the USD to EUR rate from the first source that answers.

    Tries the cache, then Yahoo's chart endpoint, then the ECB reference
    series, and stops at the first success. Two independent sources is
    what keeps one provider's rate limit from quietly amputating every
    USD-denominated position from the report, which is what a single
    fragile call used to do.

    Args:
        max_age_seconds: Age above which a cached rate is refetched.

    Returns:
        The rate with its source and date, or None when neither source
        could be reached, in which case a warning is logged and callers
        report the affected positions as unpriced.
    """

    cached = _from_cache(max_age_seconds)

    if cached is not None:
        return cached

    for fetch in (fetch_yahoo_rate, fetch_ecb_rate):
        resolved = fetch()

        if resolved is not None:
            _to_cache(resolved)
            return resolved

    _LOGGER.warning(
        "No se pudo obtener el tipo de cambio USD/EUR de ninguna fuente "
        "(Yahoo, BCE); las posiciones en USD quedarán sin valorar."
    )

    return None


def describe(rate: ExchangeRate | None) -> str:
    """
    Render a resolved rate the way the report states it.

    Args:
        rate: Resolved rate, or None when none could be obtained.

    Returns:
        Short phrase naming the rate, its source and its date, or the
        ``n/d`` notice when there is no rate.
    """

    if rate is None:
        return "Tipo de cambio USD/EUR: n/d (ninguna fuente disponible)"

    origin = "Yahoo" if rate.source == SOURCE_YAHOO else "BCE"
    when = "sin fecha" if rate.as_of is None else rate.as_of.isoformat()

    return f"Tipo de cambio USD/EUR: {rate.rate:.6f} ({origin}, {when})"
