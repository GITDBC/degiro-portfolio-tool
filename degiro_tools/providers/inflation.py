# Standard libraries
import csv
import io
import logging
from typing import Final

# Own modules
from ..domain.models import InflationIndex
from .cache import read_cached, write_cache
from .net import http_get

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

# ECB Data Portal, SDMX CSV, keyless. Split into a base and its query so
# neither half needs a line continuation, as ``fx.ECB_URL`` is.
_HICP_BASE: Final[str] = "https://data-api.ecb.europa.eu/service/data/HICP"
_HICP_QUERY: Final[str] = "format=csvdata"

# Monthly HICP index series per region, base 2025 = 100.
#
# The dataflow is HICP and the provider suffix is 4D0, never the older
# ICP dataflow and never Eurostat's prc_hicp_midx: both of those still
# answer HTTP 200 after the February 2026 methodology change, frozen at
# 2025-12. A source that fails by going stale while still returning 200
# is worse than one that errors, because nothing in the report would
# look wrong: it would simply deflate by an index a year out of date.
_SERIES: Final[dict[str, str]] = {
    "es": "M.ES.N.000000.4D0.INX",
    "ea": "M.U2.N.000000.4D0.INX",
}

# Region names as the report prints them, and the accepted flag values.
_REGION_LABELS: Final[dict[str, str]] = {
    "es": "España",
    "ea": "zona euro",
}
REGIONS: Final[tuple[str, ...]] = tuple(_SERIES)
DEFAULT_REGION: Final[str] = "es"

# How long a downloaded series stays usable. The index is monthly and
# published about six weeks in arrears, so a day is already far shorter
# than the rate at which the answer can change.
INFLATION_MAX_AGE_SECONDS: Final[float] = 24 * 3600

# Columns of the SDMX CSV this reads. The response carries thirty-odd
# columns of metadata; only these two are data.
_PERIOD_COL: Final[str] = "TIME_PERIOD"
_VALUE_COL: Final[str] = "OBS_VALUE"

# Shape of a monthly SDMX period: four digits, a dash, two digits.
_PERIOD_LENGTH: Final[int] = 7
_MONTHS: Final[int] = 12


def hicp_url(region: str) -> str:
    """
    Build the HICP series URL for a region.

    Args:
        region: Region code, one of ``REGIONS``.

    Returns:
        Absolute URL of the monthly index series as SDMX CSV.

    Raises:
        ValueError: If the region is not one this module publishes,
            which the CLI already prevents by restricting the choices.
    """

    series = _SERIES.get(region)

    if series is None:
        raise ValueError(
            f"región de inflación desconocida: {region}; "
            f"las disponibles son {', '.join(REGIONS)}"
        )

    return f"{_HICP_BASE}/{series}?{_HICP_QUERY}"


def region_label(region: str) -> str:
    """
    Name of a region as the report prints it.

    Args:
        region: Region code.

    Returns:
        The printable name, or the code itself when it is unknown.
    """

    return _REGION_LABELS.get(region, region)


def parse_period(value: str | None) -> str | None:
    """
    Validate a monthly SDMX period.

    Parsed by hand rather than with ``date.fromisoformat``, which
    rejects a bare ``YYYY-MM`` outright: measured on Python 3.11.15,
    ``date.fromisoformat("2026-08")`` raises ValueError. That matters
    because the ISO helper this package already had swallows ValueError
    into None, so reusing it here would have discarded every row and
    produced an empty series — no real return at all, and no error
    saying why. A period is a month, so it is read as one.

    Args:
        value: Cell content, possibly absent or malformed.

    Returns:
        The period as ``YYYY-MM``, or None when the cell does not hold
        one.
    """

    if value is None or len(value) != _PERIOD_LENGTH or value[4] != "-":
        return None

    year, month = value[:4], value[5:]

    if not year.isdigit() or not month.isdigit():
        return None

    if not 1 <= int(month) <= _MONTHS:
        return None

    return value


def parse_series(text: str) -> dict[str, float]:
    """
    Parse the usable observations out of an SDMX CSV response.

    Rows whose period or value cannot be read are skipped rather than
    failing the parse: the response is untrusted input and one unusable
    row does not invalidate the others.

    Args:
        text: Body of the CSV response.

    Returns:
        Index value per monthly period. Empty when the response carries
        no usable observation.
    """

    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None or _VALUE_COL not in reader.fieldnames:
        _LOGGER.debug("Serie HICP sin columna %s.", _VALUE_COL)
        return {}

    values: dict[str, float] = {}

    for row in reader:
        period = parse_period(row.get(_PERIOD_COL))

        if period is None:
            continue

        try:
            value = float(row[_VALUE_COL])
        except (TypeError, ValueError):
            continue

        if value > 0:
            values[period] = value

    return values


def _series_text(region: str, max_age_seconds: float) -> str | None:
    """
    Get the CSV series, from cache when it is still fresh.

    Args:
        region: Region code.
        max_age_seconds: Age above which the cached copy is discarded.
            Zero forces a fresh download.

    Returns:
        The response body, or None when it could not be fetched.
    """

    entry = f"hicp-{region}.csv.gz"

    if max_age_seconds > 0:
        cached = read_cached(entry, max_age_seconds)

        if cached is not None:
            return cached

    body = http_get(hicp_url(region))

    if body is None:
        return None

    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as err:
        _LOGGER.debug("Serie HICP de %s no es UTF-8: %s.", region, err)
        return None

    write_cache(entry, text)

    return text


def fetch_inflation_index(
    region: str = DEFAULT_REGION,
    max_age_seconds: float = INFLATION_MAX_AGE_SECONDS,
) -> InflationIndex | None:
    """
    Read the harmonised consumer price index of a region.

    The reference month is whichever the series ends at, not the current
    one: the index is published about six weeks late, and asserting a
    month that has not been published would be inventing data.

    Args:
        region: Region code, one of ``REGIONS``.
        max_age_seconds: Age above which the cached series is
            refetched. Zero forces a fresh download.

    Returns:
        The index with its reference month, or None when the series
        could not be obtained, in which case a warning is logged and the
        caller reports the nominal figures alone.
    """

    text = _series_text(region, max_age_seconds)

    if text is None:
        _LOGGER.warning(
            "No se pudo obtener el IPC armonizado de %s desde el BCE; "
            "el informe se queda con las cifras nominales.",
            region_label(region),
        )
        return None

    values = parse_series(text)

    if not values:
        _LOGGER.warning("La serie HICP de %s no trae observaciones.", region)
        return None

    reference = max(values)

    _LOGGER.info(
        "IPC de %s: %d meses, último publicado %s (%.2f).",
        region_label(region),
        len(values),
        reference,
        values[reference],
    )

    return InflationIndex(
        region=region,
        region_label=region_label(region),
        reference_period=reference,
        values=values,
    )
