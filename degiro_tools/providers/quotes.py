# Standard libraries
import json
import logging
from typing import Final

# Own modules
from ..domain.models import PriceQuote, PriceSource, PriceUnavailable
from .cache import read_cached, write_cache
from .justetf import QUOTE_MAX_AGE_SECONDS, fetch_eur_quote
from .yahoo import fetch_quote as fetch_yahoo_quote

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

# Characters an ISIN is allowed to contribute to a cache file name. The
# value comes from a spreadsheet, so anything else means the cache is
# skipped rather than a path being built from unvalidated input.
_ISIN_ALPHABET: Final[frozenset[str]] = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
)

# Length of a well-formed ISIN. Used only to reject a value before it
# reaches a file name, never to refuse the lookup itself.
_ISIN_LENGTH: Final[int] = 12

# Provenance labels a cache entry may legitimately carry.
_SOURCE_VALUES: Final[frozenset[str]] = frozenset(str(source) for source in PriceSource)


def _cache_entry(isin: str) -> str | None:
    """
    Cache file name holding the resolved quote of one ISIN.

    Args:
        isin: ISIN code of the instrument.

    Returns:
        The file name, or None when the value is not a plain ISIN and
        must therefore not become part of a path.
    """

    if len(isin) != _ISIN_LENGTH or not set(isin) <= _ISIN_ALPHABET:
        return None

    return f"quote-{isin}.json.gz"


def _from_cache(isin: str, max_age_seconds: float) -> PriceQuote | None:
    """
    Read a previously resolved quote from the shared cache.

    Args:
        isin: ISIN code of the instrument.
        max_age_seconds: Age above which the stored quote is ignored.

    Returns:
        The stored quote, or None when there is nothing fresh and usable.
    """

    entry = _cache_entry(isin)

    if entry is None or max_age_seconds <= 0:
        return None

    body = read_cached(entry, max_age_seconds)

    if body is None:
        return None

    try:
        stored = json.loads(body)
    except json.JSONDecodeError:
        return None

    return _restored(isin, stored)


def _restored(isin: str, stored: object) -> PriceQuote | None:
    """
    Rebuild a quote from what the cache holds, validating every field.

    The cache is a file on disk that another run wrote, so it is read as
    untrusted input: a truncated or hand-edited entry yields None and
    the price is fetched again.

    Args:
        isin: ISIN code the entry was stored under.
        stored: Decoded cache payload.

    Returns:
        The quote, or None when the payload does not hold a usable one.
    """

    if not isinstance(stored, dict):
        return None

    price = stored.get("price_eur")
    source = stored.get("source")

    if isinstance(price, bool) or not isinstance(price, int | float) or price <= 0:
        return None

    if not isinstance(source, str) or source not in _SOURCE_VALUES:
        return None

    return PriceQuote(
        isin=isin,
        price_eur=float(price),
        native_price=float(price),
        currency=str(stored.get("currency", "")),
        unavailable=None,
        source=PriceSource(source),
    )


def _to_cache(quote: PriceQuote) -> None:
    """
    Store a resolved quote for the rest of the run and the next ones.

    Only a quote with a euro price is stored: a failure is not worth
    remembering, since the whole point of the fallback chain is that the
    next attempt may well succeed.

    Args:
        quote: Quote to store, provenance included.

    Returns:
        None.
    """

    entry = _cache_entry(quote.isin)

    if entry is None or quote.price_eur is None or quote.source is None:
        return

    write_cache(
        entry,
        json.dumps(
            {
                "price_eur": quote.price_eur,
                "currency": quote.currency,
                "source": str(quote.source),
            }
        ),
    )


def resolve_quote(
    isin: str,
    eur_rate: float | None = None,
    max_age_seconds: float = QUOTE_MAX_AGE_SECONDS,
) -> PriceQuote:
    """
    Resolve the price of an ISIN from the first source that answers.

    Tries the cache, then justETF in euros, then Yahoo, and stops at the
    first success. justETF leads because it quotes in euros directly: no
    exchange rate is involved, so a position cannot lose its valuation
    to a rate-limited currency pair. Yahoo stays behind it because it
    covers instruments justETF does not, and because two independent
    sources is what keeps one provider's bad afternoon from amputating
    positions from the report.

    Every failure path still yields a quote rather than raising, with
    ``unavailable`` saying why the euro price is missing: a position the
    account holds is a position even when it cannot be valued.

    Args:
        isin: ISIN code of the instrument.
        eur_rate: USD to EUR conversion rate for the Yahoo fallback, or
            None when none could be retrieved.
        max_age_seconds: Age above which a cached quote is refetched.
            Zero forces a fresh request.

    Returns:
        The quote, with ``price_eur`` and ``source`` set when a price
        could be resolved.
    """

    cached = _from_cache(isin, max_age_seconds)

    if cached is not None:
        return cached

    quote = fetch_eur_quote(isin)

    if quote is None:
        quote = fetch_yahoo_quote(isin, eur_rate)

    if quote.price_eur is None:
        _LOGGER.warning(
            "Ninguna fuente (justETF, Yahoo) dio precio para %s: %s.",
            isin,
            quote.unavailable or PriceUnavailable.NO_QUOTE,
        )
        return quote

    _to_cache(quote)

    return quote
