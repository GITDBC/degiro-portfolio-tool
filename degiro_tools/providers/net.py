# Standard libraries
import logging
import urllib.error
import urllib.request
from typing import Final

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

# Every provider here reads a public HTTP endpoint and parses whatever
# comes back. Both halves of that are shared: a request that must never
# raise, and a JSON walk that must never index blindly. Keeping them in
# one place is what stops the next provider from re-inventing the
# failure mode this module exists to avoid.

# Seconds to wait for a provider before giving up on it. Short on
# purpose: the fallbacks are cheap and a hanging request is the failure
# mode that makes a report feel broken.
TIMEOUT_SECONDS: Final[float] = 10.0

# Responses are untrusted input, so the read is bounded rather than
# trusting Content-Length. The largest payload read through here is the
# HICP series at some 370 rows of CSV, comfortably inside this.
MAX_BYTES: Final[int] = 512 * 1024

# Several of these endpoints answer a bare urllib request with 429 and a
# challenge page, so they are asked with a browser-like agent.
USER_AGENT: Final[str] = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def http_get(
    url: str,
    timeout_seconds: float = TIMEOUT_SECONDS,
    max_bytes: int = MAX_BYTES,
) -> bytes | None:
    """
    Fetch a URL, returning None instead of raising on any failure.

    A rate limit or a server error is not retried: the caller has
    another source to try, and hammering the one that just refused is
    how a slow report becomes a blocked one.

    Args:
        url: Absolute HTTPS URL to read.
        timeout_seconds: Seconds to wait before giving up.
        max_bytes: Most bytes to read from the response body.

    Returns:
        Response body capped at the maximum accepted size, or None when
        the request failed for any reason.
    """

    request = urllib.request.Request(  # noqa: S310
        url, headers={"User-Agent": USER_AGENT}
    )

    try:
        with urllib.request.urlopen(  # noqa: S310
            request, timeout=timeout_seconds
        ) as response:
            body: bytes = response.read(max_bytes)
    except urllib.error.HTTPError as err:
        # 429 is what Yahoo answers when it has had enough; yfinance
        # reports it as "possibly delisted", which is what sent the
        # owner looking for a wrong symbol instead of a rate limit.
        _LOGGER.debug("%s respondió HTTP %s.", url, err.code)
        return None
    except OSError as err:
        # Covers URLError, timeouts and every transport-level failure.
        _LOGGER.debug("No se pudo leer %s: %s.", url, err)
        return None

    return body


def dig(payload: object, *path: str | int) -> object:
    """
    Walk a decoded JSON payload without trusting its shape.

    Every step checks the container before indexing it, so a rate limit
    page, an error envelope or a schema change yields None instead of an
    exception naming a field that never arrived. That exception is what
    made a plain HTTP 429 look like a delisted currency pair.

    Args:
        payload: Decoded JSON value to walk.
        *path: Mapping keys and list indices to follow, in order.

    Returns:
        The value at the end of the path, or None as soon as any step
        does not exist or does not have the expected container type.
    """

    current = payload

    for step in path:
        if isinstance(step, int):
            if not isinstance(current, list) or len(current) <= step:
                return None

            current = current[step]
        else:
            if not isinstance(current, dict) or step not in current:
                return None

            current = current[step]

    return current


def positive_number(value: object) -> float | None:
    """
    Read a payload field that must be a price-like positive number.

    Args:
        value: Raw value taken from a decoded payload.

    Returns:
        The value as a float, or None when it is absent, a boolean, not
        a number, or not strictly positive. A zero price is refused on
        purpose: no market quotes an instrument at nothing, so it is a
        parse failure rather than a datum.
    """

    if isinstance(value, bool) or not isinstance(value, int | float):
        return None

    if value <= 0:
        return None

    return float(value)
