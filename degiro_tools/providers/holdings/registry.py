# Standard libraries
import json
import logging
from pathlib import Path
from typing import Final

# 3pps
from fastexcel import CalamineError

# Own modules
from ...domain.holdings import Holding, HoldingsConfig, normalize_holding
from ...domain.portfolio import AssetClass
from .base import HoldingsParser
from .ishares import ISharesParser
from .vanguard import VanguardParser
from .xtrackers import XtrackersParser

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

# Sections of the holdings config. Equity funds declare a constituents
# file because they are expanded; every other class declares only the
# ISIN.
_EQUITY_SECTION: Final[str] = "renta_variable"
_FIXED_INCOME_SECTION: Final[str] = "renta_fija"
_CRYPTO_SECTION: Final[str] = "criptomonedas"

# Sections shaped as a plain ISIN list, each mapped to the class it
# declares. A position of any of these classes enters the look-through
# whole, so it needs no constituents file and they all validate the
# same way: declaring a further class is one entry here.
_CLASS_SECTIONS: Final[dict[str, AssetClass]] = {
    _FIXED_INCOME_SECTION: AssetClass.FIXED_INCOME,
    _CRYPTO_SECTION: AssetClass.CRYPTO,
}

_KNOWN_SECTIONS: Final[frozenset[str]] = frozenset({_EQUITY_SECTION, *_CLASS_SECTIONS})

# Failures that mean the constituents file is unusable, not that this
# package is wrong. A file corrupted on disk or left half downloaded is
# reported as a position that cannot be broken down, exactly like a
# missing one, instead of aborting the whole report.
#
# The classes are the ones the readers actually raise, checked on
# truncated copies of the real files rather than assumed:
# ``CalamineError`` (fastexcel, via ``polars.read_excel``) for both
# formats the providers publish, .xlsx and .xls; ``UnicodeDecodeError``
# for an iShares SpreadsheetML export cut mid character, which is read
# as text and not through the spreadsheet reader; and ``OSError`` for a
# file that stops being readable after the existence check.
#
# Narrow on purpose. Every other reader error - wrong parameters, a
# column that does not exist, a workbook answering with several sheets -
# is a defect in this package, and turning it into "fondo no
# desglosable" would hide it behind a plausible warning.
_UNREADABLE_FILE_ERRORS: Final[tuple[type[Exception], ...]] = (
    CalamineError,
    UnicodeDecodeError,
    OSError,
)


def build_parsers() -> tuple[HoldingsParser, ...]:
    """
    Build the ordered tuple of provider parsers.

    Each parser logs through its own module logger, which descends from
    the application logger, so its diagnostics stay traceable without
    anything being handed to it.

    Returns:
        Tuple of parser instances evaluated in detection order.
    """

    return (
        ISharesParser(),
        VanguardParser(),
        XtrackersParser(),
    )


# Registered parsers, evaluated in order for content-based dispatch.
PARSERS: Final[tuple[HoldingsParser, ...]] = build_parsers()


def parse_holdings_file(path: Path, isin: str) -> list[Holding]:
    """
    Parse a holdings file using the first matching provider parser.

    Selects the parser via content-based detection (header
    signatures and file markers) rather than file extension alone,
    then delegates parsing to it.

    Detection and parsing sit inside the same read guard because both
    read the file: ``can_parse`` opens the workbook to look for its
    provider's header, so an unreadable file fails there first, and a
    file corrupted halfway through fails in either of the two. Neither
    is a reason to lose the whole report, so the outcome is the same as
    for a missing file: no holdings, and the weight of the position
    surfaces as unclassified in the breakdowns.

    Args:
        path: Path to the holdings file (.xls or .xlsx).
        isin: Source ETF ISIN for attribution.

    Returns:
        List of Holding objects. Empty list if no parser recognizes
        the file or if the file cannot be read.
    """

    try:
        return _dispatch_to_parser(path, isin)
    except _UNREADABLE_FILE_ERRORS as err:
        _LOGGER.warning(
            "El fichero de holdings de %s existe pero no se puede leer (%s); "
            "está corrupto o la descarga quedó a medias. Descárgalo de nuevo "
            "del proveedor.",
            isin,
            path.name,
        )
        # The reader's own message names the format and the byte that
        # broke it, which is diagnosis rather than something the user
        # has to read to know what to do about it.
        _LOGGER.debug("Fallo al leer %s: %s", path, err)
        return []


def _dispatch_to_parser(path: Path, isin: str) -> list[Holding]:
    """
    Run the detection loop and parse with the first matching parser.

    Split out of ``parse_holdings_file`` so its read guard wraps the
    loop as a whole, detection included. The module-level ``PARSERS``
    tuple is iterated directly: the parsers are stateless, so building a
    fresh set per call would buy nothing.

    Args:
        path: Path to the holdings file (.xls or .xlsx).
        isin: Source ETF ISIN for attribution.

    Returns:
        List of Holding objects. Empty list if no parser recognizes
        the file.
    """

    for parser in PARSERS:
        if parser.can_parse(path):
            _LOGGER.debug("Se usa el parser %s para %s.", parser.name, path)
            return parser.parse(path, isin)

    _LOGGER.warning("Ningún parser de holdings reconoce %s.", path)
    return []


def load_holdings_config(config_path: Path) -> HoldingsConfig:
    """
    Load the per-ISIN holdings declarations from a JSON config.

    The JSON is an object whose sections are all optional.
    ``renta_variable`` maps ISIN to the constituents file that expands
    it (.xls or .xlsx), with relative paths resolved against the config
    file's directory. Every other section is a plain list of ISINs and
    takes no file: a position that is not equity is never expanded, so a
    file for it would never be read.

    An ISIN may be declared once only. Appearing in two sections is
    rejected rather than resolved, because the two declarations
    contradict each other and any silent winner would depend on section
    order, which is an implementation detail the config does not state.

    Args:
        config_path: Path to the JSON configuration file.

    Returns:
        The declared files and asset classes.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If the JSON is not an object, still uses the old
            flat ISIN-to-path shape, declares a section with the wrong
            type, or repeats an ISIN across sections.
    """

    if not config_path.exists():
        error_message = (
            f"Holdings config not found: {config_path}\n"
            f"Create a JSON file with the sections "
            f"{', '.join(sorted(_KNOWN_SECTIONS))}, e.g.:\n"
            f'{{"{_EQUITY_SECTION}": {{"IE00B4L5Y983": '
            f'"holdings/msci_world.xls"}}, '
            f'"{_FIXED_INCOME_SECTION}": ["LU0290358497"]}}'
        )
        raise FileNotFoundError(error_message)

    raw = config_path.read_text(encoding="utf-8")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as err:
        error_message = f"Holdings config is not valid JSON: {config_path}"
        raise ValueError(error_message) from err

    if not isinstance(data, dict):
        error_message = (
            f"Holdings config must be a JSON object with the sections "
            f"{', '.join(sorted(_KNOWN_SECTIONS))}: {config_path}"
        )
        raise ValueError(error_message)

    _reject_flat_shape(data, config_path)

    files = _equity_files(data.get(_EQUITY_SECTION, {}), config_path)
    listed = {
        section: _class_section_isins(section, data.get(section, []), config_path)
        for section in _CLASS_SECTIONS
    }

    _reject_repeated_isins({_EQUITY_SECTION: tuple(files), **listed}, config_path)

    return HoldingsConfig(
        files=files,
        classes={
            isin: _CLASS_SECTIONS[section]
            for section, isins in listed.items()
            for isin in isins
        },
    )


def _reject_flat_shape(data: dict[str, object], config_path: Path) -> None:
    """
    Refuse a config still written in the old flat ISIN-to-path shape.

    The flat shape is no longer read, and silently ignoring it would
    drop every declaration the user wrote, so it is named instead.

    Args:
        data: Parsed top-level JSON object.
        config_path: Path to the config file, for the message.

    Returns:
        None.

    Raises:
        ValueError: If any key is not one of the known sections.
    """

    unknown = sorted(set(data) - _KNOWN_SECTIONS)

    if not unknown:
        return

    error_message = (
        f"Holdings config uses the old flat format: {config_path}\n"
        f"Wrap the ISIN-to-file entries in a {_EQUITY_SECTION!r} section and "
        f"list the ISINs of every other class in its own section "
        f"({', '.join(sorted(_CLASS_SECTIONS))}), e.g.:\n"
        f'{{"{_EQUITY_SECTION}": {{"{unknown[0]}": "holdings/fund.xls"}}, '
        f'"{_FIXED_INCOME_SECTION}": ["LU0290358497"]}}'
    )
    raise ValueError(error_message)


def _reject_repeated_isins(
    sections: dict[str, tuple[str, ...]],
    config_path: Path,
) -> None:
    """
    Refuse an ISIN declared in more than one section.

    One position has one asset class, so two declarations for the same
    ISIN are a contradiction the config has to resolve itself: reading
    one and dropping the other would make the outcome depend on section
    order and would report a class the user never picked.

    Args:
        sections: ISINs declared per section name.
        config_path: Path to the config file, for the message.

    Returns:
        None.

    Raises:
        ValueError: If the same ISIN appears under two sections.
    """

    origin: dict[str, str] = {}

    for section, isins in sections.items():
        for isin in isins:
            previous = origin.setdefault(isin, section)

            if previous != section:
                error_message = (
                    f"ISIN {isin} is declared in both {previous!r} and "
                    f"{section!r}: a position has one asset class, so declare "
                    f"it in one section only: {config_path}"
                )
                raise ValueError(error_message)


def _equity_files(section: object, config_path: Path) -> dict[str, Path]:
    """
    Resolve the equity section into ISIN-to-file paths.

    Args:
        section: Raw value of the ``renta_variable`` section.
        config_path: Path to the config file, whose directory relative
            paths are resolved against.

    Returns:
        Dict mapping ISIN to the resolved Path of its holdings file.

    Raises:
        ValueError: If the section is not an object of ISIN to file
            path strings.
    """

    if not isinstance(section, dict):
        error_message = (
            f"Section {_EQUITY_SECTION!r} must be an object mapping ISIN to "
            f"its holdings file: {config_path}"
        )
        raise ValueError(error_message)

    base_dir = config_path.parent
    files: dict[str, Path] = {}

    for isin, file_path in section.items():
        if not isinstance(file_path, str):
            error_message = (
                f"Section {_EQUITY_SECTION!r} maps {isin} to something that is "
                f"not a file path: {config_path}"
            )
            raise ValueError(error_message)

        files[isin] = base_dir / file_path

    return files


def _class_section_isins(
    section: str,
    raw_section: object,
    config_path: Path,
) -> tuple[str, ...]:
    """
    Validate one ISIN-list section and return what it declares.

    Shared by every section that declares a class other than equity, so
    a new class inherits the validation instead of restating it.

    Args:
        section: Name of the section, for the message.
        raw_section: Raw value of that section in the parsed JSON.
        config_path: Path to the config file, for the message.

    Returns:
        The ISINs listed in the section, in declaration order.

    Raises:
        ValueError: If the section is not a list of ISIN strings.
    """

    if not isinstance(raw_section, list) or not all(
        isinstance(isin, str) for isin in raw_section
    ):
        error_message = (
            f"Section {section!r} must be a list of ISIN, with no files: a "
            f"position declared there is never expanded, so its constituents "
            f"file would never be read: {config_path}"
        )
        raise ValueError(error_message)

    return tuple(raw_section)


def fetch_holdings(isin: str, file_path: Path) -> list[Holding]:
    """
    Load holdings for an ETF from its local file.

    Parses the file via the matching provider parser and applies the
    single normalization step at this providers boundary, so every
    consumer receives already-canonical holdings (sector and location
    mapped to the project vocabulary).

    Args:
        isin: ISIN of the ETF.
        file_path: Path to the holdings file.

    Returns:
        List of canonicalized Holding objects. Empty list if the file
        is missing or unparseable.
    """

    if not file_path.exists():
        _LOGGER.warning("No se encuentra el fichero de holdings: %s.", file_path)
        return []

    holdings = parse_holdings_file(file_path, isin)

    return [normalize_holding(holding) for holding in holdings]
