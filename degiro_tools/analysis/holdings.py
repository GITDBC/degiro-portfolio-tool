# Standard libraries
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

# 3pps
import polars as pl

# Own modules
from ..domain.holdings import Holding, HoldingsConfig, normalize_holding
from ..domain.portfolio import AssetClass
from ..domain.vocabulary import COUNTRY_ES, SECTOR_ES
from ..parsing.portfolio_parser import parse_portfolio_xlsx
from ..providers.holdings import fetch_holdings, load_holdings_config
from ..providers.yahoo import (
    get_stock_sector_country,
    is_etf,
)
from .columns import (
    EFFECTIVE_PCT_COL,
    ETF_COUNT_COL,
    IDENTITY_COL,
    LOCATION_COL,
    NAME_COL,
    PORTFOLIO_WEIGHT_COL,
    SECTOR_COL,
    SOURCE_ISIN_COL,
    TICKER_COL,
    WEIGHT_PCT_COL,
)
from .coverage import FULL_PCT, coverage_pct, with_unclassified
from .identity import with_identity

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

# Coverage below this percentage of the priced portfolio is reported as a
# warning: the breakdowns are still correct but a material share of the
# portfolio is missing from them.
_MIN_COVERAGE_PCT: Final[float] = 99.0

# Coverage above 100% by more than this many points is reported too.
# Tighter than the low side on purpose: a gap below 100% is already
# visible in every table as the unclassified row, while an excess shows
# up nowhere, and the compensation between funds (one publishing 100.12%
# and another 99.74%) shrinks it precisely when it is hiding something.
# Summing a few thousand float64 weights drifts by ~1e-10, so anything
# above a hundredth of a point comes from the provider's list, not from
# the arithmetic.
_MAX_COVERAGE_EXCESS_PCT: Final[float] = 0.01

# Constituents files are downloaded by hand from each provider, which
# republishes them monthly. Past a month the snapshot is guaranteed to
# describe a different portfolio than the prices it is combined with, so
# a month is where the breakdowns stop describing today's holdings.
_MAX_CONSTITUENTS_AGE_DAYS: Final[int] = 30

# Explicit schema for the assembled holdings DataFrame. Declaring it
# keeps column names and dtypes stable even when the join with the
# weights frame yields no rows (e.g. nothing could be priced), so
# downstream consumers never hit a schema-less ColumnNotFoundError.
_HOLDINGS_SCHEMA: Final[dict[str, pl.DataType]] = {
    NAME_COL: pl.Utf8(),
    TICKER_COL: pl.Utf8(),
    SECTOR_COL: pl.Utf8(),
    LOCATION_COL: pl.Utf8(),
    SOURCE_ISIN_COL: pl.Utf8(),
    WEIGHT_PCT_COL: pl.Float64(),
}


def fetch_stock_holding(product: str, isin: str) -> Holding:
    """
    Create a passthrough holding for an individual stock position.

    Enriches the stock with sector and country data from Yahoo
    Finance and canonicalizes it through the shared normalization
    step. If enrichment fails, the holding keeps empty sector and
    location fields.

    Args:
        product: Product name from Degiro.
        isin: ISIN of the stock.

    Returns:
        Canonical Holding with weight_pct=100 representing the stock
        itself.
    """

    sector, country = get_stock_sector_country(isin)

    return normalize_holding(
        Holding(
            name=product,
            ticker=isin,
            sector=sector,
            weight_pct=FULL_PCT,
            location=country,
            source_isin=isin,
        )
    )


def compute_portfolio_weights(position_values: dict[str, float]) -> dict[str, float]:
    """
    Compute each position's share of the priced portfolio value.

    Market values are normalized into weight fractions over the
    successfully-priced positions only. Positions whose price could not
    be retrieved never reach this mapping, so they are excluded from
    the weight base and the returned fractions sum to ~1.0 across
    priced positions (or the mapping is empty when nothing could be
    priced).

    Args:
        position_values: Market value in EUR per ISIN, as valued by the
            caller. Taking the snapshot as input keeps the valuation
            and the analysis on the same prices.

    Returns:
        Dict mapping ISIN to its weight fraction of the priced
        portfolio value. Empty when no position could be priced.
    """

    total_value = sum(position_values.values())

    if total_value <= 0:
        return {}

    return {isin: value / total_value for isin, value in position_values.items()}


def declared_class_holding(
    product: str,
    isin: str,
    asset_class: AssetClass,
) -> Holding:
    """
    Create the passthrough holding of a position with a declared class.

    A position declared as anything other than equity has no equity
    constituents to expand into, so it enters the look-through as a
    single holding carrying its own asset class as its sector. That is
    what puts its weight in the sector table instead of in the
    unclassified residual, and what keeps it out of the geographic
    breakdown: neither the domicile of a bond or money-market sleeve nor
    that of a crypto ETP is a country the account is exposed to.

    Args:
        product: Product name from Degiro.
        isin: ISIN of the position.
        asset_class: Class declared for the position, used as its
            sector.

    Returns:
        Canonical Holding with weight_pct=100 representing the position
        itself.
    """

    return normalize_holding(
        Holding(
            name=product.strip(),
            ticker=isin,
            sector=asset_class.value,
            weight_pct=FULL_PCT,
            location="",
            source_isin=isin,
        )
    )


def collect_holdings(
    df_portfolio: pl.DataFrame,
    config: HoldingsConfig,
) -> list[Holding]:
    """
    Build the flat list of underlying holdings for every position.

    A position declared as any class other than equity emits one
    passthrough holding and is never expanded, which is checked first:
    it needs no constituents file, must not be mistaken for a single
    stock, and must not be reported as an ETF whose file is missing.
    Configured equity funds expand into their underlying holdings.
    Individual stocks pass through with weight_pct=100%, and how many
    positions were taken as stocks is reported once in aggregate rather
    than per position. Equity funds without a config entry emit a
    warning and are skipped, as do configured positions whose file
    yields no holdings (missing, unreadable, or a fund that is not
    equity): they cannot be broken down and their weight surfaces as
    unclassified in the breakdowns. All returned holdings are canonical
    (normalization is applied at the providers boundary).

    Args:
        df_portfolio: DataFrame with columns Producto, Symbol/ISIN,
            Cantidad.
        config: Declared holdings files and asset classes per ISIN.

    Returns:
        List of Holding objects across all expandable positions.
    """

    all_holdings: list[Holding] = []
    single_stocks: list[str] = []

    for product, isin, _qty in df_portfolio.iter_rows():
        asset_class = config.asset_class(isin)

        if asset_class is not AssetClass.EQUITY:
            all_holdings.append(declared_class_holding(product, isin, asset_class))
        elif isin in config.files:
            all_holdings.extend(_expand_position(product, isin, config.files[isin]))
        elif is_etf(product):
            _LOGGER.warning(
                "Sin fichero de holdings configurado para el ETF %s (%s).",
                product.strip(),
                isin,
            )
        else:
            single_stocks.append(isin)
            all_holdings.append(fetch_stock_holding(product, isin))

    if single_stocks:
        _LOGGER.info(
            "%d posición(es) sin declarar en la configuración se toman como "
            "acciones individuales, con el sector y el país que Yahoo da a su "
            "propio ticker: %s. Si alguna es un fondo, declárala.",
            len(single_stocks),
            ", ".join(single_stocks),
        )

    return all_holdings


def _expand_position(
    product: str,
    isin: str,
    file_path: Path,
) -> list[Holding]:
    """
    Expand one configured equity position into its holdings.

    Args:
        product: Product name from Degiro.
        isin: ISIN of the position.
        file_path: Path to its constituents file.

    Returns:
        The canonical holdings of the fund, empty when the file is
        missing, unreadable or not an equity fund.
    """

    _warn_if_stale(isin, file_path)

    holdings = fetch_holdings(isin, file_path)

    if not holdings:
        _LOGGER.warning(
            "La posición %s (%s) no se pudo desglosar en renta variable; su "
            "peso se reporta como no clasificado en la distribución sectorial "
            "y geográfica.",
            product.strip(),
            isin,
        )

    return holdings


def _file_age_days(file_path: Path) -> float | None:
    """
    How long ago a file was last written, in days.

    Reads the modification time, the same signal the download cache uses
    to decide whether a payload is still fresh, so both parts of the
    program measure age the same way. A file that cannot be inspected is
    not an error here: the caller that needs it reports it.

    Args:
        file_path: File to inspect.

    Returns:
        Age in days, or None when the file cannot be stat'ed.
    """

    try:
        modified = datetime.fromtimestamp(file_path.stat().st_mtime, tz=UTC)
    except OSError:
        return None

    return (datetime.now(tz=UTC) - modified) / timedelta(days=1)


def _warn_if_stale(isin: str, file_path: Path) -> None:
    """
    Report a constituents file too old to describe today's holdings.

    The look-through combines the constituents published at download
    time with the prices of the current run. Nothing in the report shows
    the gap between the two, so an old file silently turns the sector and
    geographic breakdowns into a picture of a portfolio the fund no
    longer holds. Saying so is a warning, not a failure: the figures are
    still the best available and the report remains usable.

    Args:
        isin: ISIN of the position the file expands.
        file_path: Path to its constituents file.

    Returns:
        None.
    """

    age_days = _file_age_days(file_path)

    if age_days is None or age_days <= _MAX_CONSTITUENTS_AGE_DAYS:
        return

    _LOGGER.warning(
        "El fichero de constituyentes de %s tiene %d días (%s); el desglose "
        "sectorial y geográfico describe una foto antigua combinada con los "
        "precios de hoy. Descárgalo de nuevo del proveedor.",
        isin,
        int(age_days),
        file_path.name,
    )


def compute_top_holdings(df: pl.DataFrame) -> pl.DataFrame:
    """
    Aggregate the look-through holdings by issuer.

    Groups by issuer identity rather than by the provider's raw name,
    so a company spelled differently across two funds ("ASML Holding
    NV" and "ASML HOLDING NV") lands in a single row instead of being
    split into two smaller ones. The weight that no holding accounts
    for is appended as an explicit unclassified row. Sector and country
    are translated to Spanish, since the providers publish them in
    English.

    Args:
        df: Holdings DataFrame with columns name, sector, location,
            effective_pct.

    Returns:
        DataFrame with columns name, sector, location, etf_count and
        effective_pct, sorted by weight descending.
    """

    df_agg = (
        with_identity(df)
        .group_by(IDENTITY_COL)
        .agg(
            pl.col(NAME_COL).first(),
            pl.col(SECTOR_COL).first(),
            pl.col(LOCATION_COL).first(),
            # How many of your ETFs hold this issuer, which is what the
            # overlap view answers, folded in so it needs no own table.
            pl.col(SOURCE_ISIN_COL).n_unique().alias(ETF_COUNT_COL),
            pl.col(EFFECTIVE_PCT_COL).sum(),
        )
        .drop(IDENTITY_COL)
        .sort(EFFECTIVE_PCT_COL, descending=True)
        .with_columns(
            pl.col(SECTOR_COL).replace(SECTOR_ES),
            pl.col(LOCATION_COL).replace(COUNTRY_ES),
        )
    )

    return with_unclassified(df_agg, NAME_COL)


def build_holdings_dataframe(
    all_holdings: list[Holding],
    weights: dict[str, float],
) -> pl.DataFrame:
    """
    Assemble the final holdings DataFrame with effective weights.

    Holdings are assumed already canonical. Effective weights are
    computed only for holdings whose source position was priced; any
    holding whose source ISIN is absent from ``weights`` is dropped
    from the result (inner join) and a prominent warning lists the
    missing ISINs. Effective weights are therefore relative to the
    priced holdings, never silently diluted to zero.

    Args:
        all_holdings: Canonical holdings across all positions.
        weights: Mapping of source ISIN to portfolio weight fraction
            over the priced positions.

    Returns:
        DataFrame with columns name, ticker, sector, location,
        source_isin, weight_pct, portfolio_weight and effective_pct.

    Raises:
        ValueError: If no holdings could be loaded for any position.
    """

    if not all_holdings:
        error_message = (
            "No holdings could be loaded for any position. Check the "
            "portfolio file and the holdings JSON configuration."
        )
        _LOGGER.error(error_message)
        raise ValueError(error_message)

    priced_isins = set(weights)
    holding_isins = {holding.source_isin for holding in all_holdings}
    missing_isins = sorted(holding_isins - priced_isins)

    if missing_isins:
        _LOGGER.warning(
            "Los pesos efectivos son solo sobre las posiciones con precio. "
            "Sin precio %d posición(es): %s. Sus holdings quedan fuera de "
            "la base de peso de la cartera.",
            len(missing_isins),
            ", ".join(missing_isins),
        )

    df_holdings = pl.DataFrame(
        [
            {
                NAME_COL: h.name,
                TICKER_COL: h.ticker,
                SECTOR_COL: h.sector,
                LOCATION_COL: h.location,
                SOURCE_ISIN_COL: h.source_isin,
                WEIGHT_PCT_COL: h.weight_pct,
            }
            for h in all_holdings
        ],
        schema=_HOLDINGS_SCHEMA,
    )

    df_weights = pl.DataFrame(
        {
            SOURCE_ISIN_COL: list(weights.keys()),
            PORTFOLIO_WEIGHT_COL: list(weights.values()),
        },
        schema={SOURCE_ISIN_COL: pl.Utf8(), PORTFOLIO_WEIGHT_COL: pl.Float64()},
    )

    df_priced = df_holdings.join(df_weights, on=SOURCE_ISIN_COL, how="inner")
    effective_pct = (
        pl.col(WEIGHT_PCT_COL) / FULL_PCT * pl.col(PORTFOLIO_WEIGHT_COL) * FULL_PCT
    ).alias(EFFECTIVE_PCT_COL)

    df_result = df_priced.with_columns(effective_pct)

    _warn_on_coverage(coverage_pct(df_result))

    return df_result


def _warn_on_coverage(coverage: float) -> None:
    """
    Report a look-through that does not add up to the whole portfolio.

    Both sides deserve a warning and neither is a failure. Short
    coverage means weight absent from every breakdown, which the
    unclassified row already shows. Excess coverage means the provider's
    published weights add up to more than 100%, and it is the more
    treacherous of the two: the excess of one fund can offset weight
    genuinely missing from another, so the total looks healthy while
    both are wrong. The weights are reported exactly as published and
    never renormalized, because renormalizing would move every figure in
    the report.

    Args:
        coverage: Portfolio weight the look-through covers, as a
            percentage.

    Returns:
        None.
    """

    if coverage < _MIN_COVERAGE_PCT:
        _LOGGER.warning(
            "El look-through cubre solo el %.2f%% de la cartera con precio; "
            "el %.2f%% restante se reporta como no clasificado.",
            coverage,
            FULL_PCT - coverage,
        )
    elif coverage > FULL_PCT + _MAX_COVERAGE_EXCESS_PCT:
        _LOGGER.warning(
            "Los pesos de origen suman el %.2f%% de la cartera, un %.2f%% por "
            "encima del 100%%: la desviación viene de los ficheros del "
            "proveedor, no del cálculo, y no se renormaliza.",
            coverage,
            coverage - FULL_PCT,
        )


def load_portfolio_holdings(
    xlsx_path: Path,
    config_path: Path,
    position_values: dict[str, float],
) -> pl.DataFrame:
    """
    Build a unified DataFrame of all underlying holdings with their
    effective portfolio weights.

    Reads the holdings config JSON to locate the file of each equity
    fund and to learn which positions are declared as another class. A
    position of any class other than equity enters as one passthrough
    holding instead of being expanded. Individual stocks pass through
    with weight_pct=100%. Equity funds without a config entry emit a
    warning and are skipped. Holdings arrive already canonical from the
    providers boundary.

    Effective weights are computed over the successfully-priced
    positions only: holdings whose source position has no price are
    excluded and reported via a warning, so the remaining effective
    weights stay relative to the priced holdings.

    The returned DataFrame contains columns:
        - name, ticker, sector, location, source_isin, weight_pct
        - portfolio_weight (fraction of priced portfolio value)
        - effective_pct (weight_pct/100 * portfolio_weight * 100)

    Args:
        xlsx_path: Path to Portfolio.xlsx exported from Degiro.
        config_path: Path to the holdings JSON config declaring the
            constituents file of each equity fund and the ISINs of
            every other asset class.
        position_values: Market value in EUR per ISIN, as valued by the
            caller, used as the weight base.

    Returns:
        DataFrame with all priced holdings and their effective
        portfolio weight.

    Raises:
        ValueError: If no holdings could be loaded for any position.
    """

    df_portfolio = parse_portfolio_xlsx(xlsx_path)
    config = load_holdings_config(config_path)

    weights = compute_portfolio_weights(position_values)
    all_holdings = collect_holdings(df_portfolio, config)

    return build_holdings_dataframe(all_holdings, weights)
