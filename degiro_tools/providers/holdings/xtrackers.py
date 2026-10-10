# Standard libraries
import logging
from pathlib import Path
from typing import Final

# Own modules
from ...domain.holdings import Holding
from .helpers import (
    find_header_row_with,
    get_cell,
    index_of,
    parse_weight,
    read_xlsx_table,
)

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

# Distinctive header labels that identify an Xtrackers/DWS export.
_SIGNATURE: Final[frozenset[str]] = frozenset({"Name", "ISIN", "Type of Security"})

# Values of the "Type of Security" column that mark an equity row. DWS
# ships the same fund in localized flavours, so the Spanish and English
# labels must both be recognized.
_EQUITY_TYPES: Final[frozenset[str]] = frozenset({"Renta Variable", "Equities"})

# Minimum share of the fund's total weight that must sit in equity rows
# for the file to be treated as an expandable equity fund.
_MIN_EQUITY_SHARE: Final[float] = 0.5

# Factor to turn the fractional "Weighting" column into a percentage.
_FRACTION_TO_PCT: Final[float] = 100.0


class XtrackersParser:
    """
    Parser for Xtrackers/DWS constituent XLSX exports.

    DWS constituent files start with a disclaimer row, followed by a
    header whose first cell is an empty index column. Only equity rows
    (``Type of Security`` equal to "Renta Variable" or "Equities") are
    kept; cash, bonds, rights and futures are discarded.

    Non-equity funds are rejected outright: when equities account for
    less than ``_MIN_EQUITY_SHARE`` of the fund's weight the file
    describes something else (a money-market or bond fund), expanding
    it as equity would be meaningless, and an empty list is returned so
    the caller can report the position as not expanded.

    Weight handling: when the fractional ``Weighting`` column is
    present its values are converted to percentages (multiplied by
    100). When no usable weight is available, each equity is
    assigned an equal weight (``100 / number_of_equities``) so that
    downstream overlap, sector and geography analysis still works.

    Country names are emitted in their raw provider form; the
    ``COUNTRY_MAP`` normalization applied downstream maps them to the
    canonical English name. The sector is read from the ``Industry
    Classification`` column (whitespace-stripped) as a raw
    sub-industry label; the ``SECTOR_MAP`` normalization applied
    downstream maps it to the canonical GICS top-level sector.

    Attributes:
        name: Provider identifier.
    """

    name: Final[str] = "xtrackers"

    def can_parse(self, path: Path) -> bool:
        """
        Detect an Xtrackers/DWS export by its header signature.

        Args:
            path: Path to the candidate holdings file.

        Returns:
            True if the file is an XLSX with the Xtrackers header.
        """

        if path.suffix.lower() != ".xlsx":
            return False

        table = read_xlsx_table(path)

        return find_header_row_with(table, _SIGNATURE) is not None

    def parse(self, path: Path, isin: str) -> list[Holding]:
        """
        Parse an Xtrackers/DWS XLSX file into equity holdings.

        Args:
            path: Path to the .xlsx file.
            isin: Source ETF ISIN for attribution.

        Returns:
            List of equity Holding objects. Empty when the file holds
            no equities or describes a non-equity fund.
        """

        table = read_xlsx_table(path)

        header_idx = find_header_row_with(table, _SIGNATURE)
        if header_idx is None:
            _LOGGER.warning("No se encontró la cabecera de Xtrackers en %s.", path)
            return []

        headers = table[header_idx]
        name_idx = index_of(headers, "Name")
        country_idx = index_of(headers, "Country")
        sector_idx = index_of(headers, "Industry Classification")
        type_idx = index_of(headers, "Type of Security")
        weight_idx = index_of(headers, "Weighting")

        data_rows = [
            cells for cells in table[header_idx + 1 :] if get_cell(cells, name_idx)
        ]
        equity_rows = [
            cells
            for cells in data_rows
            if get_cell(cells, type_idx).strip() in _EQUITY_TYPES
        ]

        if not equity_rows:
            _LOGGER.warning("No hay filas de renta variable en %s.", path)
            return []

        if not self.is_equity_fund(data_rows, equity_rows, weight_idx):
            _LOGGER.warning(
                "%s no es un fondo de renta variable (las acciones son una "
                "parte marginal de su peso); no se puede desglosar.",
                path,
            )
            return []

        weights = self.resolve_weights(equity_rows, weight_idx)

        return [
            Holding(
                name=get_cell(cells, name_idx),
                ticker="",
                sector=get_cell(cells, sector_idx).strip(),
                weight_pct=weight,
                location=get_cell(cells, country_idx),
                source_isin=isin,
            )
            for cells, weight in zip(equity_rows, weights, strict=True)
        ]

    @staticmethod
    def is_equity_fund(
        data_rows: list[list[str]],
        equity_rows: list[list[str]],
        weight_idx: int | None,
    ) -> bool:
        """
        Decide whether the file describes an expandable equity fund.

        Compares the weight held in equity rows against the weight of
        the whole file. A money-market or bond fund exposes a handful
        of incidental equity lines whose weight is negligible; treating
        those as the fund's composition would attribute the entire
        position to a couple of meaningless holdings.

        Args:
            data_rows: Every named row of the file.
            equity_rows: Rows classified as equities.
            weight_idx: Index of the Weighting column, or None.

        Returns:
            True when equities dominate the fund, or when there is no
            weight column to decide with.
        """

        if weight_idx is None:
            return True

        total_weight = XtrackersParser.sum_weights(data_rows, weight_idx)
        if total_weight <= 0:
            return True

        equity_weight = XtrackersParser.sum_weights(equity_rows, weight_idx)

        return equity_weight / total_weight >= _MIN_EQUITY_SHARE

    @staticmethod
    def sum_weights(rows: list[list[str]], weight_idx: int) -> float:
        """
        Sum the parseable positive weights of a set of rows.

        Args:
            rows: Rows to add up.
            weight_idx: Index of the Weighting column.

        Returns:
            Sum of the positive parseable weights, in the file's own
            units (fractions of one).
        """

        weights = (parse_weight(get_cell(cells, weight_idx)) for cells in rows)

        return sum(weight for weight in weights if weight is not None and weight > 0)

    @staticmethod
    def resolve_weights(
        equity_rows: list[list[str]], weight_idx: int | None
    ) -> list[float]:
        """
        Resolve equity weights with an all-or-nothing file decision.

        The choice between real and equal weights is made once for
        the whole file, never per row. Real weights are used only
        when the ``Weighting`` column is present and every equity
        row carries a parseable positive value; otherwise every
        equity gets the same equal weight (``100 / n``).

        Args:
            equity_rows: Equity rows kept from the file.
            weight_idx: Index of the Weighting column, or None.

        Returns:
            List of weight percentages aligned with ``equity_rows``.
        """

        # Real weights and equal weights are computed on different
        # scales (fraction vs 100/n), so the choice between them is
        # made once for the whole file rather than per row to avoid
        # mixing the two and producing weights far above 100%.
        equal_weight = _FRACTION_TO_PCT / len(equity_rows)

        if weight_idx is None:
            return [equal_weight] * len(equity_rows)

        raw_weights = [
            parse_weight(get_cell(cells, weight_idx)) for cells in equity_rows
        ]

        if all(weight is not None and weight > 0 for weight in raw_weights):
            return [
                weight * _FRACTION_TO_PCT
                for weight in raw_weights
                if weight is not None
            ]

        return [equal_weight] * len(equity_rows)
