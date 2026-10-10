# Standard libraries
from collections.abc import Sequence
from pathlib import Path

# Own modules
from ...domain.holdings import Holding
from ...parsing.excel import read_excel_frame


def parse_weight(value: str) -> float | None:
    """
    Parse a weight value in US or EU notation.

    Handles formats like "5.34", "5,34", "4,2547 %", "4.2547%" and
    plain fractions such as "0.0785247806".

    Args:
        value: Raw weight string from the file.

    Returns:
        Parsed float value, or None if unparseable.
    """

    cleaned = value.strip().replace("%", "").replace("\xa0", "").strip()

    if not cleaned:
        return None

    # European format: 1.234,56 -> remove dots, replace comma with dot
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")

    try:
        return float(cleaned)
    except ValueError:
        return None


def find_header_row_with(
    table: list[list[str]], required: frozenset[str]
) -> int | None:
    """
    Find the first row containing every required header label.

    Used by provider parsers to locate their header row via a
    distinctive set of column names (content-based detection).

    Args:
        table: List of rows, each row a list of cell strings.
        required: Header labels that must all be present in the row.

    Returns:
        Index of the matching row, or None if no row contains all
        required labels.
    """

    for i, cells in enumerate(table):
        if required <= {cell.strip() for cell in cells}:
            return i

    return None


def index_of(headers: Sequence[str], name: str) -> int | None:
    """
    Resolve the position of an exact (stripped) header name.

    Args:
        headers: Row of column header strings.
        name: Exact header label to locate.

    Returns:
        Column index, or None if the label is absent.
    """

    stripped = [header.strip() for header in headers]

    return stripped.index(name) if name in stripped else None


def read_xlsx_table(path: Path) -> list[list[str]]:
    """
    Read an XLSX file into a table of string cells.

    Reads the first sheet without header inference so that provider
    parsers can locate their own header row. Empty cells become
    empty strings.

    Args:
        path: Path to the .xlsx file.

    Returns:
        List of rows, each row a list of cell strings.
    """

    df_raw = read_excel_frame(path, has_header=False, infer_schema_length=0)

    return [
        ["" if value is None else str(value) for value in df_raw.row(i)]
        for i in range(len(df_raw))
    ]


def get_cell(cells: list[str], idx: int | None) -> str:
    """
    Safely extract a cell value by index.

    Args:
        cells: Row of string values.
        idx: Column index, or None if the column was not resolved.

    Returns:
        Cell value, or empty string if index is None or out of
        range.
    """

    if idx is not None and len(cells) > idx:
        return cells[idx]

    return ""


def is_non_equity(cells: list[str], asset_idx: int | None) -> bool:
    """
    Check whether a row should be excluded as non-equity.

    Args:
        cells: Row of string values.
        asset_idx: Index of the asset_class column, or None.

    Returns:
        True if the row has an asset class that is not Equity.
    """

    asset_val = get_cell(cells, asset_idx)

    return bool(asset_val and asset_val != "Equity")


def build_holdings_from_rows(
    table: list[list[str]],
    header_idx: int,
    indices: dict[str, int | None],
    isin: str,
    *,
    filter_equity: bool = False,
) -> list[Holding]:
    """
    Iterate data rows and build Holding objects.

    Shared logic for providers that expose an explicit weight
    column (iShares, Vanguard). Skips rows without a valid positive
    weight or without a name.

    Args:
        table: All rows as lists of strings.
        header_idx: Index of the header row (data starts at +1).
        indices: Dict mapping field names to column indices.
        isin: Source ETF ISIN for attribution.
        filter_equity: If True, skip rows where asset_class column
            is present and not "Equity".

    Returns:
        List of Holding objects.
    """

    weight_idx = indices["weight"]
    if weight_idx is None:
        return []

    name_idx = indices["name"]
    ticker_idx = indices.get("ticker")
    sector_idx = indices.get("sector")
    location_idx = indices.get("location")
    asset_idx = indices.get("asset_class")

    holdings: list[Holding] = []

    for cells in table[header_idx + 1 :]:
        if len(cells) <= weight_idx:
            continue

        if filter_equity and is_non_equity(cells, asset_idx):
            continue

        weight = parse_weight(cells[weight_idx])
        if weight is None or weight <= 0:
            continue

        name = get_cell(cells, name_idx)
        if not name or name == "null":
            continue

        holdings.append(
            Holding(
                name=name,
                ticker=get_cell(cells, ticker_idx),
                sector=get_cell(cells, sector_idx),
                weight_pct=weight,
                location=get_cell(cells, location_idx),
                source_isin=isin,
            )
        )

    return holdings
