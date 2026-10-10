# Standard libraries
import argparse
import logging
import sys
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final

# 3pps
import polars as pl
from rich.console import Console

# Own modules
from . import __version__
from .analysis import (
    compute_geography,
    compute_overlap,
    compute_sectors,
    compute_top_holdings,
    load_portfolio_holdings,
)
from .calculation.etf_compare import (
    ETFSectionData,
    build_crosschecks,
    fetch_yahoo_rows,
)
from .calculation.fifo import calculate_fifo
from .calculation.performance import compute_performance
from .calculation.portfolio import (
    ValuationSnapshot,
    compute_portfolio_percentage,
    describe_weight_base,
    extract_position_values,
    obtain_yahoo_info,
    priced_positions,
)
from .calculation.real_return import compute_real_return
from .calculation.report_data import build_report_data
from .calculation.savings_base import total_dividend_income
from .domain.etf import ETFProfile
from .domain.models import (
    ExchangeRate,
    Lot,
    MarketSnapshot,
    PerformanceSummary,
    RealReturnSummary,
    WeightBase,
)
from .domain.portfolio import (
    INPUT_ISIN_COL,
    OUTPUT_ISIN_COL,
    OUTPUT_PRICE_COL,
    OUTPUT_PRODUCT_COL,
)
from .logging_config import build_logger
from .parsing import parse_account_xlsx, parse_portfolio_cash, parse_portfolio_xlsx
from .providers import (
    CATALOG_MAX_AGE_SECONDS,
    DEFAULT_REGION,
    INFLATION_MAX_AGE_SECONDS,
    QUOTE_MAX_AGE_SECONDS,
    REGIONS,
    JustETFError,
    fetch_etf_profiles,
    fetch_inflation_index,
    load_holdings_config,
    resolve_usd_eur_rate,
)
from .reporting import print_report, render_etf_profiles, render_performance
from .reporting.analysis_report import (
    render_distribution,
    render_holdings,
    render_portfolio,
)

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

# Blocks of the report, in display order, mapped to the heading printed
# above each. overlap and geography have no block of their own on
# purpose: the report folds the overlap into a column of the holdings
# table and the geography into the allocation block, rather than
# repeating rows that are already on screen.
ANALYSIS_SECTIONS: Final[dict[str, str]] = {
    "valuation": "Valoración de la cartera",
    "performance": "Resultado latente",
    "holdings": "Principales posiciones reales",
    "sectors": "Distribución de la cartera",
    "etf": "Coste y riesgo de los ETFs",
}


def add_verbose_arg(
    parser: argparse.ArgumentParser, help_text: str | None = None
) -> None:
    """
    Add the verbosity flag to a subcommand parser.

    Declared once so ``-v`` means the same everywhere: DEBUG logging
    and the traceback instead of the one-line error.

    Args:
        parser: The subcommand argument parser to extend.
        help_text: Caption for the flag, or None to leave it
            undocumented, as ``tax`` has it.

    Returns:
        None.
    """

    parser.add_argument("-v", "--verbose", action="store_true", help=help_text)


def add_analysis_args(parser: argparse.ArgumentParser) -> None:
    """
    Add the arguments of the portfolio analysis command.

    Args:
        parser: The subcommand argument parser to extend.

    Returns:
        None.
    """

    parser.add_argument(
        "xlsx_path",
        type=Path,
        nargs="?",
        default=None,
        help="XLSX de Portfolio exportado de Degiro.",
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=Path("holdings.json"),
        help="JSON que mapea ISIN a ficheros de holdings (default: holdings.json).",
    )

    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignora la caché de justETF y vuelve a descargar el catálogo.",
    )

    add_verbose_arg(parser, "Show full traceback on error.")

    # The Portfolio export carries quantities but no acquisition cost,
    # so the unrealized result is only available when the account
    # statement is supplied as well.
    parser.add_argument(
        "--account",
        type=Path,
        default=None,
        metavar="PATH",
        help="XLSX de Estado de cuenta, que añade coste, resultado latente "
        "y rentabilidad anualizada.",
    )

    parser.add_argument(
        "--inflation",
        choices=REGIONS,
        default=DEFAULT_REGION,
        help="Región cuyo IPC descuenta la inflación del resultado latente: "
        f"es = España, ea = zona euro (default: {DEFAULT_REGION}).",
    )


def build_arg_parser() -> argparse.ArgumentParser:
    """
    Build the CLI argument parser with all subcommands.

    Registers the two subcommands: tax for the IRPF report and
    overview for every view of the current portfolio.

    Returns:
        Configured ArgumentParser instance.
    """

    parser = argparse.ArgumentParser(
        prog="degiro-tools",
        description="Herramientas para extractos Degiro.",
    )

    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )

    sub = parser.add_subparsers(dest="command")

    tax_p = sub.add_parser(
        "tax",
        help="Calcula ganancias/pérdidas patrimoniales IRPF.",
    )
    tax_p.add_argument(
        "account_path",
        type=Path,
        help="Ruta al XLSX de Estado de cuenta (Account.xlsx).",
    )
    tax_p.add_argument(
        "--no-tax",
        action="store_true",
        help="Omite el bloque de estimación IRPF.",
    )
    add_verbose_arg(tax_p)

    overview_p = sub.add_parser(
        "overview",
        help="Análisis de cartera: valoración, resultado latente, posiciones "
        "reales con su solapamiento, distribución y coste de los ETFs.",
    )
    add_analysis_args(overview_p)

    return parser


def run_tax(args: argparse.Namespace) -> None:
    """
    Execute the IRPF tax calculation flow.

    Parses the Degiro Account.xlsx, applies FIFO matching to the trades
    and integrates the dividends collected into the same savings base.
    Everything else the statement carries (interest, promotions,
    currency swaps, trades with no euro value) travels with it, so no
    row is dropped without the report saying so. The aggregates
    themselves live in ``calculation.build_report_data``, which is what
    keeps them independent of how the report is rendered.

    Args:
        args: Parsed CLI arguments with account_path and no_tax.

    Returns:
        None.
    """

    statement = parse_account_xlsx(args.account_path)
    sales, lots = calculate_fifo(statement.operations)

    print_report(build_report_data(sales, lots, statement, include_tax=not args.no_tax))


def build_valuation(args: argparse.Namespace) -> ValuationSnapshot:
    """
    Build the current valuation of the open positions.

    Reads the Portfolio XLSX and fetches current prices from the first
    provider that answers for each position. The USD to EUR rate is
    resolved once, from the first source that answers, and travels with
    the frame so every section can state which rate produced its euro
    figures. The declared asset class of
    each position travels with it too, so the table can state how the
    account splits between equity and fixed income. Rendering is left to
    the caller so the same snapshot can feed the valuation table, the
    latent result and the look-through analyses without asking for
    prices again.

    Args:
        args: Parsed CLI arguments with xlsx_path and config.

    Returns:
        Valuation snapshot with positions, prices, weights and the rate
        used.
    """

    df = parse_portfolio_xlsx(args.xlsx_path)
    config = load_holdings_config(args.config)
    exchange_rate = resolve_usd_eur_rate()
    eur_rate = None if exchange_rate is None else exchange_rate.rate

    return ValuationSnapshot(
        frame=compute_portfolio_percentage(
            obtain_yahoo_info(
                df,
                eur_rate,
                asset_classes=config.classes,
                max_age_seconds=0 if args.refresh else QUOTE_MAX_AGE_SECONDS,
            )
        ),
        exchange_rate=exchange_rate,
    )


def render_analysis_section(
    name: str,
    df: pl.DataFrame,
    weight_base: WeightBase | None = None,
) -> None:
    """
    Render one look-through section from an already-loaded frame.

    The holdings table carries the overlap column and a summary line,
    and the whole allocation is one block, so the overlap ranking and
    the geography tables have no section of their own.

    Args:
        name: Section name: holdings or sectors.
        df: Portfolio holdings with effective weights.
        weight_base: What the underlying weights are relative to, so a
            partial base is disclosed beside the tables it distorts.

    Returns:
        None.
    """

    if name == "holdings":
        overlap = compute_overlap(df)
        render_holdings(
            compute_top_holdings(df),
            overlap_count=len(overlap),
            overlap_pct=float(overlap["effective_pct"].sum()),
            weight_base=weight_base,
        )
    elif name == "sectors":
        df_country, df_continent = compute_geography(df)
        render_distribution(
            compute_sectors(df),
            df_country,
            df_continent,
            weight_base=weight_base,
        )


def collect_etf_data(args: argparse.Namespace) -> ETFSectionData:
    """
    Read cost and risk of the funds from two independent sources.

    Reads justETF and Yahoo Finance so their published TER can be put
    side by side, which is what tells a fee both providers agree on
    apart from one that rests on a single source. A justETF failure
    degrades to the Yahoo figures with a warning instead of aborting the
    run, since it needs the network and the rest of the analysis does
    not depend on it.

    Args:
        args: Parsed CLI arguments with xlsx_path and refresh.

    Returns:
        Cross-checks per requested ISIN plus the ISINs each source
        failed to cover.
    """

    isins = collect_etf_isins(args.xlsx_path)
    profiles: list[ETFProfile] = []
    uncovered: list[str] = []

    try:
        profiles, uncovered = fetch_etf_profiles(
            isins,
            max_age_seconds=0 if args.refresh else CATALOG_MAX_AGE_SECONDS,
        )
    except (JustETFError, OSError) as err:
        _LOGGER.warning(
            "No se pudo consultar justETF (%s); se sigue solo con Yahoo Finance.",
            err,
        )

    crosschecks = build_crosschecks(isins, profiles, fetch_yahoo_rows(isins))

    # An ISIN is only missing when neither source knows it; Yahoo often
    # covers what the justETF screener does not.
    missing = [
        check.isin
        for check in crosschecks
        if check.justetf is None and check.yahoo is None
    ]

    return ETFSectionData(crosschecks=crosschecks, missing=missing, uncovered=uncovered)


def run_etf_section(args: argparse.Namespace) -> None:
    """
    Render the ETF cost and risk section from two independent sources.

    Args:
        args: Parsed CLI arguments with xlsx_path and refresh.

    Returns:
        None.
    """

    data = collect_etf_data(args)

    render_etf_profiles(data.crosschecks, missing=data.missing)

    if data.uncovered and not data.missing:
        _LOGGER.info(
            "justETF no cubre %d ISIN, resueltos por Yahoo: %s.",
            len(data.uncovered),
            ", ".join(data.uncovered),
        )


@dataclass(frozen=True)
class OverviewContext:
    """
    Data every overview section is built from, fetched once.

    Attributes:
        sections: Section names to produce, in display order.
        valuation: Open positions with their prices. Unpriced positions
            are present with null euro figures.
        holdings: Look-through holdings with their effective weights.
        weight_base: What the weights are relative to.
        exchange_rate: USD to EUR rate the euro figures were built with,
            or ``None`` when none could be resolved.
    """

    sections: tuple[str, ...]
    valuation: pl.DataFrame
    holdings: pl.DataFrame
    weight_base: WeightBase
    exchange_rate: ExchangeRate | None = None


def build_overview_context(args: argparse.Namespace) -> OverviewContext:
    """
    Resolve the sections of the pass and load what they share.

    Prices are fetched at most once and handed to every section that
    needs them, so a pass shares a single market snapshot instead of
    asking the provider again per table. The same is true of the
    exchange rate, which is resolved once and reported with the numbers
    it produced.

    Args:
        args: Parsed CLI arguments with xlsx_path, config and account.

    Returns:
        The shared context for the sections of the pass.

    Raises:
        ValueError: If no Portfolio export was given, since every
            section is a view of your own positions.
    """

    if args.xlsx_path is None:
        raise ValueError(
            "Indica el XLSX de Portfolio. "
            "Ejemplo: degiro-tools overview holdings/Portfolio.xlsx"
        )

    snapshot = build_valuation(args)

    # The acquisition cost only lives in the account statement, so the
    # latent result leaves the pass when no statement was supplied.
    sections = tuple(
        name
        for name in ANALYSIS_SECTIONS
        if name != "performance" or args.account is not None
    )

    return OverviewContext(
        sections=sections,
        valuation=snapshot.frame,
        holdings=load_portfolio_holdings(
            args.xlsx_path,
            args.config,
            position_values=extract_position_values(snapshot.frame),
        ),
        weight_base=describe_weight_base(snapshot.frame),
        exchange_rate=snapshot.exchange_rate,
    )


def run_overview(args: argparse.Namespace) -> None:
    """
    Execute the portfolio analysis.

    Renders the curated report: valuation, latent result, real holdings
    with their ETF overlap folded in, the whole allocation in one block,
    and cost and risk of the funds. Prices are fetched once and handed
    to the sections that need them, so the pass shares a single market
    snapshot.

    Args:
        args: Parsed CLI arguments with xlsx_path, config and account.

    Returns:
        None.
    """

    render_overview(args, build_overview_context(args))


def render_overview(args: argparse.Namespace, context: OverviewContext) -> None:
    """
    Render every section of the pass to the terminal.

    Args:
        args: Parsed CLI arguments with xlsx_path and account.
        context: Shared context with the frames the sections need.

    Returns:
        None.
    """

    console = Console()

    for name in context.sections:
        console.rule(f"[bold cyan]{ANALYSIS_SECTIONS[name]}[/bold cyan]")

        if name == "valuation":
            render_portfolio(
                context.valuation,
                cash_eur=parse_portfolio_cash(args.xlsx_path),
                weight_base=context.weight_base,
                exchange_rate=context.exchange_rate,
            )
        elif name == "performance":
            run_performance(args, context.valuation)
        elif name == "etf":
            run_etf_section(args)
        else:
            render_analysis_section(name, context.holdings, context.weight_base)


def run_performance(
    args: argparse.Namespace,
    df_valuation: pl.DataFrame,
) -> None:
    """
    Render the unrealized result of the open positions.

    Reuses the prices already fetched for the valuation table and takes
    the acquisition cost from the FIFO lots left open by the account
    statement, so both views are built from the same snapshot and the
    cost never has to be typed by hand.

    The same result is reported twice: in the euros actually paid, and
    with inflation taken out. Over a multi-year holding period the two
    answer different questions, and only the second one says whether the
    money bought more at the end than at the start.

    Args:
        args: Parsed CLI arguments with account and inflation.
        df_valuation: Valuation DataFrame produced by
            ``build_valuation``.

    Returns:
        None.
    """

    statement = parse_account_xlsx(args.account)
    sales, lots = calculate_fifo(statement.operations)

    # Unpriced positions carry a null price in the valuation frame. They
    # are left out of the snapshot so compute_performance reports them
    # as unpriced, which is the flag it already has for exactly this.
    df_priced = priced_positions(df_valuation)

    prices = {
        isin: Decimal(str(price))
        for isin, price in zip(
            df_priced[OUTPUT_ISIN_COL].to_list(),
            df_priced[OUTPUT_PRICE_COL].to_list(),
            strict=True,
        )
    }
    products = dict(
        zip(
            df_valuation[OUTPUT_ISIN_COL].to_list(),
            df_valuation[OUTPUT_PRODUCT_COL].to_list(),
            strict=True,
        )
    )

    snapshot = MarketSnapshot(prices)
    summary = compute_performance(
        lots,
        snapshot,
        products,
        realized_gain_loss=sum((sale.gain_loss for sale in sales), Decimal(0)),
        dividend_income=total_dividend_income(statement.dividends),
    )

    render_performance(summary, resolve_real_return(args, lots, snapshot, summary))


def resolve_real_return(
    args: argparse.Namespace,
    lots: dict[str, deque[Lot]],
    snapshot: MarketSnapshot,
    summary: PerformanceSummary,
) -> RealReturnSummary | None:
    """
    Take inflation out of the unrealized result, when the index answers.

    The index needs the network, and the nominal result does not. A
    failure therefore degrades to the nominal figures alone with a
    warning, rather than aborting a report that is otherwise complete.

    Args:
        args: Parsed CLI arguments with inflation and refresh.
        lots: Open purchase lots per ISIN, as left by FIFO matching.
        snapshot: Prices and valuation date the nominal figures used.
        summary: The nominal result, whose display order is reused.

    Returns:
        The inflation-adjusted result, or None when no index could be
        obtained.
    """

    region = getattr(args, "inflation", DEFAULT_REGION)
    refresh = getattr(args, "refresh", False)
    index = fetch_inflation_index(
        region,
        max_age_seconds=0 if refresh else INFLATION_MAX_AGE_SECONDS,
    )

    if index is None:
        return None

    return compute_real_return(lots, snapshot, index, summary.priced_isins)


def collect_etf_isins(xlsx_path: Path) -> list[str]:
    """
    Resolve which ISINs the etf section must look up.

    Takes the positions of the Portfolio XLSX, preserving order and
    dropping repeats so the same fund is not requested twice.

    Args:
        xlsx_path: Portfolio XLSX exported from Degiro.

    Returns:
        Ordered list of unique ISINs.

    Raises:
        ValueError: If the file carries no position with an ISIN.
    """

    frame = parse_portfolio_xlsx(xlsx_path)
    isins = [
        str(value) for value in frame[INPUT_ISIN_COL].to_list() if value is not None
    ]

    if not isins:
        raise ValueError(
            "Indica un XLSX de Portfolio con posiciones. "
            "Ejemplo: degiro-tools overview holdings/Portfolio.xlsx"
        )

    return list(dict.fromkeys(isins))


def main(argv: list[str] | None = None) -> int:
    """
    CLI entry point.

    Parses arguments, dispatches to the appropriate subcommand
    handler, and returns an integer exit code. Errors, logs and
    warnings go to stderr, so the tables stay readable on stdout.

    Args:
        argv: Arguments list (optional, for testing). Uses sys.argv
            if None.

    Returns:
        Exit code: 0 success, 1 error, 130 keyboard interrupt.
    """

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    verbose = getattr(args, "verbose", False)

    # Configures the parent logger the per-module loggers hang from; the
    # instance itself is never needed here.
    build_logger(verbose=verbose)

    try:
        if args.command == "tax":
            run_tax(args)
        else:
            run_overview(args)

    except KeyboardInterrupt:
        return 130

    except Exception as exc:
        if verbose:
            raise

        sys.stderr.write(f"Error: {exc}\n")
        return 1

    return 0
