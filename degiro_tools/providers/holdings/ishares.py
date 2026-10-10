# Standard libraries
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path
from typing import Final

# 3pps
import defusedxml.ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

# Own modules
from ...domain.holdings import Holding
from .helpers import build_holdings_from_rows

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

# Number of leading bytes read and inspected for content-based detection.
_SNIFF_BYTES: Final[int] = 4096

_SPREADSHEETML_NS: Final[str] = "urn:schemas-microsoft-com:office:spreadsheet"

_COLUMN_ALIASES: Final[dict[str, list[str]]] = {
    "name": [
        "Name",
        "Nombre",
        "Nombre de emisor",
        "Holding name",
        "Security",
    ],
    "ticker": ["Ticker", "Símbolo", "Symbol", "Code"],
    "sector": ["Sector", "Industry"],
    "weight": [
        "Weight (%)",
        "Peso (%)",
        "% de valor de mercado",
        "Weight",
    ],
    "location": ["Location", "Localización", "Región", "Country"],
    "asset_class": ["Asset Class", "Clase de activo"],
}

# Fields resolved from the iShares header via the alias catalogue.
_FIELDS: Final[list[str]] = [
    "name",
    "ticker",
    "sector",
    "weight",
    "location",
    "asset_class",
]


def _resolve_column(headers: Sequence[str], field: str) -> str | None:
    """
    Find the actual column name matching a logical field.

    Checks the header list against known aliases for each field.
    Returns the first match found.

    Args:
        headers: List of column names from the file.
        field: Logical field name to resolve.

    Returns:
        Matching column name from headers, or None if not found.
    """

    aliases = _COLUMN_ALIASES.get(field, [])

    for alias in aliases:
        if alias in headers:
            return alias

    return None


def _find_header_row(table: list[list[str]]) -> int | None:
    """
    Find the header row index using the generic alias catalogue.

    Scans each row looking for any cell that matches a known column
    alias from ``_COLUMN_ALIASES``.

    Args:
        table: List of rows, each row a list of cell strings.

    Returns:
        Index of the header row, or None if not found.
    """

    all_aliases = [
        alias for alias_list in _COLUMN_ALIASES.values() for alias in alias_list
    ]

    for i, cells in enumerate(table):
        if any(alias in cells for alias in all_aliases):
            return i

    return None


def _resolve_indices(
    headers: list[str],
    fields: list[str],
) -> dict[str, int | None]:
    """
    Resolve column indices for multiple logical fields.

    Args:
        headers: Row of column header strings.
        fields: Logical field names to resolve.

    Returns:
        Dict mapping field name to column index (or None if the
        field was not found in headers).
    """

    result: dict[str, int | None] = {}

    for field in fields:
        col = _resolve_column(headers, field)
        result[field] = headers.index(col) if col else None

    return result


def _extract_xml_rows(root: ET.Element) -> list[list[str]]:
    """
    Extract all rows from an XML SpreadsheetML document.

    Args:
        root: Parsed XML root element.

    Returns:
        List of rows, each row a list of cell text values.
    """

    table: list[list[str]] = []

    for row in root.findall(f".//{{{_SPREADSHEETML_NS}}}Row"):
        cells: list[str] = []
        for cell in row.findall(f"{{{_SPREADSHEETML_NS}}}Cell"):
            data = cell.find(f"{{{_SPREADSHEETML_NS}}}Data")
            cells.append(data.text if data is not None and data.text else "")
        table.append(cells)

    return table


class ISharesParser:
    """
    Parser for iShares XML SpreadsheetML exports (.xls).

    iShares exports .xls files that are actually XML in Microsoft's
    SpreadsheetML format. The header is located via the generic
    alias catalogue and only equity rows are kept.

    Attributes:
        name: Provider identifier.
    """

    name: Final[str] = "ishares"

    def can_parse(self, path: Path) -> bool:
        """
        Detect SpreadsheetML content by sniffing the file header.

        Only the leading ``_SNIFF_BYTES`` bytes are read from disk, so
        detection stays cheap even for multi-megabyte exports.

        Args:
            path: Path to the candidate holdings file.

        Returns:
            True if the file looks like a SpreadsheetML document.
        """

        if path.suffix.lower() != ".xls":
            return False

        try:
            with path.open("rb") as handle:
                head = handle.read(_SNIFF_BYTES).lower()
        except OSError:
            return False

        return b"spreadsheet" in head and b"<?xml" in head

    def parse(self, path: Path, isin: str) -> list[Holding]:
        """
        Parse an iShares SpreadsheetML file into holdings.

        Args:
            path: Path to the .xls file.
            isin: Source ETF ISIN for attribution.

        Returns:
            List of equity Holding objects.
        """

        content = path.read_text(encoding="utf-8-sig").lstrip("\ufeff")

        # Fix common XML issues in iShares exports
        content = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", "&amp;", content)

        try:
            # defusedxml hardens against XXE / billion-laughs entity
            # attacks on externally downloaded files (Bandit B314).
            root = DefusedET.fromstring(content)
        except (ET.ParseError, DefusedXmlException):
            _LOGGER.warning("No se pudo leer el XML de %s.", path)
            return []

        table = _extract_xml_rows(root)

        header_idx = _find_header_row(table)
        if header_idx is None:
            _LOGGER.warning("No se encontró la cabecera en el XML %s.", path)
            return []

        headers = table[header_idx]
        indices = _resolve_indices(headers, _FIELDS)

        if indices["name"] is None or indices["weight"] is None:
            _LOGGER.warning("Faltan las columnas de nombre/peso en %s.", path)
            return []

        return build_holdings_from_rows(
            table, header_idx, indices, isin, filter_equity=True
        )
