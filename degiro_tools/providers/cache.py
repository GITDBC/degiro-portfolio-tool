# Standard libraries
import gzip
import logging
import os
import time
from pathlib import Path
from typing import Final

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

# Directory name under the user cache root.
_APP_DIR: Final[str] = "degiro-tools"

# Compression level: the payloads are JSON, which compresses an order of
# magnitude, and a higher level would cost more than the bytes it saves.
_COMPRESS_LEVEL: Final[int] = 6


def cache_dir() -> Path:
    """
    Directory where downloaded payloads are kept between runs.

    Honours ``XDG_CACHE_HOME`` when set, so the location can be moved
    without touching the code, and falls back to the conventional
    ``~/.cache``.

    Returns:
        Path of the application cache directory. It is not created.
    """

    root = os.environ.get("XDG_CACHE_HOME")

    return (Path(root) if root else Path.home() / ".cache") / _APP_DIR


def cache_age_seconds(name: str) -> float | None:
    """
    How long ago a cached payload was written.

    Args:
        name: File name of the entry.

    Returns:
        Age in seconds, or None when the entry does not exist.
    """

    path = cache_dir() / name

    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return None


def read_cached(name: str, max_age_seconds: float) -> str | None:
    """
    Read a cached payload if it is still fresh enough to be used.

    A cache miss, an expired entry and an unreadable file are all the
    same answer, None, so the caller simply downloads again. Nothing
    here can make the program fail: the worst case is paying for the
    request the cache was meant to save.

    Args:
        name: File name of the entry.
        max_age_seconds: Age above which the entry is ignored.

    Returns:
        The stored text, or None when there is nothing usable.
    """

    age = cache_age_seconds(name)

    if age is None or age > max_age_seconds:
        return None

    try:
        with gzip.open(cache_dir() / name, "rt", encoding="utf-8") as handle:
            body: str = handle.read()
    except (OSError, EOFError, gzip.BadGzipFile) as err:
        _LOGGER.debug("Caché ilegible en %s (%s); se descarga de nuevo.", name, err)
        return None

    return body


def write_cache(name: str, body: str) -> None:
    """
    Store a payload for the next runs.

    The write goes to a temporary file that is then moved into place, so
    an interrupted run cannot leave a half-written entry that a later
    run would read as valid.

    Args:
        name: File name of the entry.
        body: Text to store.

    Returns:
        None.
    """

    target = cache_dir() / name
    temporary = target.with_suffix(f"{target.suffix}.{os.getpid()}.part")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)

        with gzip.open(
            temporary, "wt", encoding="utf-8", compresslevel=_COMPRESS_LEVEL
        ) as handle:
            handle.write(body)

        temporary.replace(target)
    except OSError as err:
        # A read-only or full disk must not break a report that already
        # has the data it needs.
        _LOGGER.debug("No se pudo escribir la caché %s (%s).", name, err)
        temporary.unlink(missing_ok=True)


def describe_age(seconds: float) -> str:
    """
    Render a cache age the way a person would say it.

    Args:
        seconds: Age of the entry.

    Returns:
        Short human phrase such as "hace 2 h" or "hace 15 min".
    """

    minutes = int(seconds // 60)

    if minutes < 1:
        return "hace menos de un minuto"

    if minutes < 60:
        return f"hace {minutes} min"

    return f"hace {minutes // 60} h"
