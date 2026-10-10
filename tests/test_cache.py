# Standard libraries
import gzip
import os
import time
from pathlib import Path

# 3pps
import pytest

# Own modules
from degiro_tools.providers import cache
from degiro_tools.providers.cache import (
    cache_age_seconds,
    cache_dir,
    describe_age,
    read_cached,
    write_cache,
)

ENTRY = "payload.json.gz"


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """
    Point the cache at a temporary directory for every test.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        tmp_path: Temporary directory provided by pytest.

    Returns:
        None.
    """

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))


def test_cache_dir_follows_xdg(tmp_path: Path) -> None:
    """
    The location is configurable through the environment.
    """

    assert cache_dir() == tmp_path / "degiro-tools"


def test_roundtrip_returns_the_same_text() -> None:
    """
    What goes in comes out, compressed in between.
    """

    body = '{"data": [{"isin": "IE00B4L5Y983"}]}'

    write_cache(ENTRY, body)

    assert read_cached(ENTRY, 3600) == body


def test_payload_is_actually_compressed() -> None:
    """
    A screener response is JSON, so it must not be stored raw.

    Four megabytes per run of report history is worth the gzip call.
    """

    body = '{"data": []}' + " " * 100_000

    write_cache(ENTRY, body)
    stored = (cache_dir() / ENTRY).stat().st_size

    assert stored < len(body) / 10

    with gzip.open(cache_dir() / ENTRY, "rt", encoding="utf-8") as handle:
        assert handle.read() == body


def test_missing_entry_is_a_miss() -> None:
    """
    Nothing stored yet reads as None, not as an error.
    """

    assert read_cached("nunca-escrito.gz", 3600) is None
    assert cache_age_seconds("nunca-escrito.gz") is None


def test_expired_entry_is_ignored() -> None:
    """
    Past the maximum age the caller must download again.

    This is what keeps a stale TER from being reported as current.
    """

    write_cache(ENTRY, "viejo")

    stale = time.time() - 7200
    os.utime(cache_dir() / ENTRY, (stale, stale))

    assert read_cached(ENTRY, 3600) is None
    assert read_cached(ENTRY, 10800) == "viejo"


def test_zero_max_age_never_serves_the_cache() -> None:
    """
    An age of zero is how a forced refresh is expressed.
    """

    write_cache(ENTRY, "reciente")

    assert read_cached(ENTRY, 0) is None


def test_corrupt_entry_is_a_miss_not_a_crash() -> None:
    """
    A truncated file must cost a download, never the run.
    """

    write_cache(ENTRY, "algo")
    (cache_dir() / ENTRY).write_bytes(b"esto no es gzip")

    assert read_cached(ENTRY, 3600) is None


def test_unwritable_cache_is_silent() -> None:
    """
    A read-only cache directory must not break the report.

    The data is already in hand by the time it is stored, so failing to
    keep it is not worth an error.
    """

    directory = cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o500)

    try:
        write_cache(ENTRY, "contenido")

        assert read_cached(ENTRY, 3600) is None
    finally:
        directory.chmod(0o700)


def test_no_partial_files_are_left_behind() -> None:
    """
    Writing goes through a temporary file and leaves nothing extra.
    """

    write_cache(ENTRY, "contenido")

    assert [path.name for path in cache_dir().iterdir()] == [ENTRY]


def test_age_is_described_in_human_terms() -> None:
    """
    The log line has to read like a sentence, not like seconds.
    """

    assert describe_age(10) == "hace menos de un minuto"
    assert describe_age(90) == "hace 1 min"
    assert describe_age(45 * 60) == "hace 45 min"
    assert describe_age(2 * 3600) == "hace 2 h"
    assert describe_age(25 * 3600) == "hace 25 h"


def test_module_exposes_its_helpers() -> None:
    """
    The provider reaches the cache through these names.
    """

    for name in ("read_cached", "write_cache", "cache_age_seconds", "describe_age"):
        assert hasattr(cache, name)
