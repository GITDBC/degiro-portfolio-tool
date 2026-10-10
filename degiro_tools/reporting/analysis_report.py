# 3pps
import polars as pl
from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.table import Table

# Own modules
from ..analysis.columns import (
    CONTINENT_COL,
    EFFECTIVE_PCT_COL,
    ETF_COUNT_COL,
    LOCATION_COL,
    NAME_COL,
    SECTOR_COL,
)
from ..calculation.portfolio import describe_weight_base
from ..domain.models import ExchangeRate, WeightBase
from ..domain.portfolio import (
    NOT_AVAILABLE,
    OUTPUT_ASSET_CLASS_COL,
    OUTPUT_CURRENCY_COL,
    OUTPUT_NATIVE_PRICE_COL,
    OUTPUT_PERCENTAGE_COL,
    OUTPUT_PRICE_COL,
    OUTPUT_PRODUCT_COL,
    OUTPUT_QUANTITY_COL,
    OUTPUT_SOURCE_COL,
    OUTPUT_TOTAL_COL,
    OUTPUT_VALUE_COL,
)
from ..providers.fx import describe
from .helpers import weight_color


def render_lookthrough_base_note(console: Console, base: WeightBase | None) -> None:
    """
    Warn that a look-through breakdown inherits a partial weight base.

    The look-through expands the funds of the positions that could be
    priced. When some could not, every percentage below is relative to a
    subset of the account, and the unattributed remainder shows up as
    unclassified weight. Saying so beside the table is what keeps a
    reader from taking a gap caused by a missing exchange rate for a real
    property of the portfolio.

    Args:
        console: Rich Console instance for output.
        base: What the underlying weights are relative to, or None when
            the caller did not compute it.

    Returns:
        None.
    """

    if base is None or base.is_whole_portfolio:
        return

    console.print(
        f"[yellow]Base parcial: el desglose cubre solo las "
        f"{base.priced_count} de {base.position_count} posiciones con valor "
        f"en euros. Las {base.unpriced_count} restantes "
        f"({', '.join(base.unpriced_isins)}) no entran, y el peso que no se "
        f"les puede atribuir aparece como «no clasificado» por esa razón, no "
        f"por una propiedad de la cartera.[/yellow]"
    )


def render_holdings(
    df: pl.DataFrame,
    overlap_count: int | None = None,
    overlap_pct: float | None = None,
    weight_base: WeightBase | None = None,
) -> None:
    """
    Show the top portfolio holdings.

    Displays the top 50 rows of an already-aggregated holdings
    breakdown (see ``compute_top_holdings``), which includes the
    unclassified residual row when part of the portfolio could not be
    looked through.

    The ``ETFs`` column says how many of your funds hold each issuer,
    which is the overlap question answered in place: a company reached
    through three ETFs is one position, not three. How much weight sits
    in more than one fund is summarized below the table.

    Args:
        df: Aggregated breakdown with columns name, sector, location,
            etf_count, effective_pct, sorted by weight descending.
        overlap_count: Total issuers held through two or more funds.
        overlap_pct: Portfolio weight those issuers add up to.
        weight_base: What the underlying weights are relative to, used
            to disclose positions left out of the look-through.

    Returns:
        None.
    """

    console = Console()

    table = Table(
        title="Top 50 por peso efectivo",
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    # No rank column: the rows are already ordered by weight, and the
    # width it costs is what makes the ETF count fit.
    table.add_column("Nombre", max_width=24, no_wrap=True, overflow="ellipsis")
    table.add_column("Sector", max_width=16, no_wrap=True, overflow="ellipsis")
    table.add_column("País", max_width=14, no_wrap=True, overflow="ellipsis")
    table.add_column("ETFs", justify="right")
    table.add_column("Peso %", justify="right")

    for row in df.head(50).iter_rows(named=True):
        w = row[EFFECTIVE_PCT_COL]
        c = weight_color(w)
        count = row.get(ETF_COUNT_COL)

        table.add_row(
            row[NAME_COL],
            row[SECTOR_COL],
            row[LOCATION_COL],
            "" if count is None else str(count),
            f"[{c}]{w:.2f}%[/{c}]",
        )

    console.print(table)

    if overlap_count:
        console.print(
            f"[dim]{overlap_count} valores en dos o más ETFs, "
            f"{overlap_pct:.2f}% del peso.[/dim]"
        )

    render_lookthrough_base_note(console, weight_base)


def build_weight_table(
    df: pl.DataFrame, label_col: str, header: str, title: str
) -> Table:
    """
    Build a two-column label/weight table.

    Args:
        df: Breakdown with the label column and effective_pct.
        label_col: Name of the label column.
        header: Header to show above the labels.
        title: Table title.

    Returns:
        Rich table with each label and its weight, coloured by size.
    """

    table = Table(title=title, box=box.ROUNDED, header_style="bold cyan")
    table.add_column(header, min_width=14)
    table.add_column("Peso %", justify="right")

    for row in df.iter_rows(named=True):
        w = row[EFFECTIVE_PCT_COL]
        c = weight_color(w)

        table.add_row(row[label_col], f"[{c}]{w:.2f}%[/{c}]")

    return table


def render_distribution(
    df_sectors: pl.DataFrame,
    df_country: pl.DataFrame,
    df_continent: pl.DataFrame,
    weight_base: WeightBase | None = None,
) -> None:
    """
    Show the whole allocation of the portfolio in one block.

    Sectors and continents sit side by side because both are short
    breakdowns of the same total, and reading them together is how you
    notice that a sector bet is also a country bet. The country ranking
    goes below, being too long to pair with anything.

    Args:
        df_sectors: Breakdown with columns sector, effective_pct.
        df_country: Breakdown with columns location, effective_pct.
        df_continent: Breakdown with columns continent, effective_pct.
        weight_base: What the underlying weights are relative to, used
            to disclose positions left out of the look-through.

    Returns:
        None.
    """

    console = Console()

    console.print(
        Columns(
            [
                build_weight_table(df_sectors, SECTOR_COL, "Sector", "Por sector"),
                build_weight_table(
                    df_continent, CONTINENT_COL, "Continente", "Por continente"
                ),
            ],
            padding=(0, 2),
        )
    )
    console.print(build_country_table(df_country))
    render_lookthrough_base_note(console, weight_base)


def build_country_table(df_country: pl.DataFrame) -> Table:
    """
    Build the ranked country table, capped at the top 20.

    Args:
        df_country: Breakdown with columns location, effective_pct.

    Returns:
        Rich table with the heaviest countries.
    """

    table = Table(
        title="Por país (Top 20)",
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    table.add_column("#", justify="right", style="dim")
    table.add_column("País", min_width=18)
    table.add_column("Peso %", justify="right")

    for i, row in enumerate(df_country.head(20).iter_rows(named=True), 1):
        w = row[EFFECTIVE_PCT_COL]
        c = weight_color(w)

        table.add_row(str(i), row[LOCATION_COL], f"[{c}]{w:.2f}%[/{c}]")

    return table


def render_portfolio(
    df: pl.DataFrame,
    cash_eur: float = 0.0,
    weight_base: WeightBase | None = None,
    exchange_rate: ExchangeRate | None = None,
) -> None:
    """
    Show the portfolio valuation as a Rich table.

    Displays each position with quantity, current price, current market
    value and portfolio weight. The value shown is quantity times price
    today, not what the position cost: the acquisition cost lives in the
    account statement and is reported by the latent result section.

    Weights are relative to the market value of the positions; the
    uninvested cash balance is reported separately together with the
    resulting account total, so the part of the balance those weights
    ignore stays visible.

    A position whose euro value could not be resolved keeps its row and
    reads ``n/d`` in the euro and weight columns, with the price its
    market quotes shown in an extra column that only appears when there
    is such a position. Its absence from the weight base is stated right
    under the table, where the weights it distorts are.

    How the priced weight splits across asset classes is printed under
    the table as one line whenever more than one class is held.

    Args:
        df: DataFrame with columns Producto, Symbol/ISIN, Tipo, Clase,
            Cantidad, Precio €, Valor €, Porcentaje Cartera.
        cash_eur: Uninvested cash balance in EUR.
        weight_base: What the weights are relative to, used to disclose
            the positions left out of them.
        exchange_rate: USD to EUR rate the euro figures were built with.

    Returns:
        None.
    """

    console = Console()
    base = weight_base if weight_base is not None else describe_weight_base(df)
    show_native = not base.is_whole_portfolio
    sources = _price_sources(df)

    # A per-row column only earns its width when the rows disagree; with
    # one source for the whole table the line below states it once.
    show_source = len(sources) > 1

    table = Table(box=box.ROUNDED, header_style="bold cyan")
    # The product name yields width to the figures when the native price
    # column appears, so the euro amounts stay legible instead of being
    # elided on an ordinary 80-column terminal.
    table.add_column(
        "Producto",
        max_width=24 if show_native else 34,
        no_wrap=True,
        overflow="ellipsis",
    )
    table.add_column("Cant.", justify="right")

    if show_native:
        table.add_column("Precio orig.", justify="right", no_wrap=True)

    table.add_column("Precio €", justify="right")
    table.add_column("Valor €", justify="right")
    table.add_column("Peso %", justify="right")

    if show_source:
        table.add_column("Fuente", justify="right", no_wrap=True)

    df_sorted = df.sort(OUTPUT_PERCENTAGE_COL, descending=True, nulls_last=True)
    total = df[OUTPUT_TOTAL_COL].item(0) if df.height > 0 else 0.0

    for row in df_sorted.iter_rows(named=True):
        cells = [row[OUTPUT_PRODUCT_COL], str(row[OUTPUT_QUANTITY_COL])]

        if show_native:
            cells.append(_native_price(row))

        cells.extend(_euro_cells(row))

        if show_source:
            cells.append(_source_cell(row))

        table.add_row(*cells)

    console.print(table)

    provenance = _describe_sources(sources)

    if provenance is not None:
        console.print(f"[dim]{provenance}[/dim]")

    if exchange_rate is not None or show_native:
        console.print(f"[dim]{describe(exchange_rate)}[/dim]")

    split = _asset_class_split(df)

    if split is not None:
        console.print(f"[dim]{split}[/dim]")

    console.print(f"[bold]Valor de mercado: [cyan]{total:.2f}[/cyan] €[/bold]")

    if cash_eur:
        console.print(
            f"[dim]Liquidez (fuera de los pesos de arriba): {cash_eur:.2f} €[/dim]"
        )
        console.print(
            f"[bold]Total de la cuenta: [cyan]{total + cash_eur:.2f}[/cyan] €[/bold]"
        )

    render_weight_base_note(console, base)


def _native_price(row: dict[str, object]) -> str:
    """
    Render the price in the currency the market quotes it in.

    Args:
        row: One row of the valuation frame, as a mapping.

    Returns:
        The price with its currency code, or ``n/d`` when no quote was
        retrieved at all.
    """

    price = row[OUTPUT_NATIVE_PRICE_COL]

    if not isinstance(price, int | float):
        return NOT_AVAILABLE

    currency = row[OUTPUT_CURRENCY_COL]
    suffix = f" {currency}" if isinstance(currency, str) and currency else ""

    return f"{price:.2f}{suffix}"


def _euro_cells(row: dict[str, object]) -> list[str]:
    """
    Render the euro price, the euro value and the weight of a position.

    Args:
        row: One row of the valuation frame, as a mapping.

    Returns:
        The three cells, each ``n/d`` when the position could not be
        valued in euro, so a missing figure never reads as a zero.
    """

    price = row[OUTPUT_PRICE_COL]
    value = row[OUTPUT_VALUE_COL]
    weight = row[OUTPUT_PERCENTAGE_COL]

    if not isinstance(value, int | float) or not isinstance(weight, int | float):
        return [NOT_AVAILABLE, NOT_AVAILABLE, NOT_AVAILABLE]

    colour = weight_color(weight)
    euro_price = f"{price:.2f}" if isinstance(price, int | float) else NOT_AVAILABLE

    return [euro_price, f"{value:.2f}", f"[{colour}]{weight:.2f}%[/{colour}]"]


def _price_sources(df: pl.DataFrame) -> list[tuple[str, int]]:
    """
    Count how many positions each provider priced.

    Args:
        df: Valuation frame with the provider column.

    Returns:
        Provider and number of rows it answered for, heaviest first.
        Empty when nothing could be priced at all.
    """

    if OUTPUT_SOURCE_COL not in df.columns or df.height == 0:
        return []

    df_counted = (
        df.filter(pl.col(OUTPUT_SOURCE_COL).is_not_null())
        .group_by(OUTPUT_SOURCE_COL)
        .len()
        .sort("len", descending=True)
    )

    return [
        (str(row[OUTPUT_SOURCE_COL]), int(row["len"]))
        for row in df_counted.iter_rows(named=True)
    ]


def _describe_sources(sources: list[tuple[str, int]]) -> str | None:
    """
    State which providers produced the prices just shown.

    Printed as one line under the table rather than as a column, because
    the answer is usually a single name and a column would spend width
    repeating it on every row. When the sources differ, the count beside
    each name says how the table splits between them and the column
    appears as well.

    Args:
        sources: Provider and row count, heaviest first.

    Returns:
        The provenance line, or None when no position was priced and
        there is therefore nothing to attribute.
    """

    if not sources:
        return None

    if len(sources) == 1:
        return f"Precios: {sources[0][0]}"

    detail = " · ".join(f"{name} {count}" for name, count in sources)

    return f"Precios: {detail}"


def _source_cell(row: dict[str, object]) -> str:
    """
    Render the provider a single position was priced by.

    Args:
        row: One row of the valuation frame, as a mapping.

    Returns:
        The provider name, or ``n/d`` when the position carries no
        price and therefore no provenance.
    """

    source = row.get(OUTPUT_SOURCE_COL)

    if not isinstance(source, str) or not source:
        return NOT_AVAILABLE

    return source


def _asset_class_split(df: pl.DataFrame) -> str | None:
    """
    Summarize how the priced weight splits across asset classes.

    Rendered as one line rather than a column: the table already
    narrows the product name to fit an 80-column terminal, so there is
    no width left for another column. Silent when every position shares
    one class, since a lone "Renta variable 100.00%" states nothing the
    table does not.

    Args:
        df: Valuation frame with the asset class and weight columns.

    Returns:
        One ``Clase X%`` item per declared class, heaviest first and
        joined by ``·`` (e.g. ``"Renta variable 68.28% · Renta fija
        28.69% · Criptomonedas 3.03%"``), or None when there is nothing
        to disclose.
    """

    if OUTPUT_ASSET_CLASS_COL not in df.columns or df.height == 0:
        return None

    df_split = (
        df.filter(pl.col(OUTPUT_PERCENTAGE_COL).is_not_null())
        .group_by(OUTPUT_ASSET_CLASS_COL)
        .agg(pl.col(OUTPUT_PERCENTAGE_COL).sum())
        .sort(OUTPUT_PERCENTAGE_COL, descending=True)
    )

    if df_split.height < 2:
        return None

    return " · ".join(
        f"{row[OUTPUT_ASSET_CLASS_COL]} {row[OUTPUT_PERCENTAGE_COL]:.2f}%"
        for row in df_split.iter_rows(named=True)
    )


def render_weight_base_note(console: Console, base: WeightBase) -> None:
    """
    State what the weights just shown are a percentage of.

    Printed immediately under the table it qualifies rather than left to
    a warning on the error stream, because a reader who scrolled past
    the diagnostics would otherwise take a rebased table for the whole
    portfolio. Silent when every position is priced: there is nothing to
    disclose then.

    Args:
        console: Rich Console instance for output.
        base: What the weights are relative to.

    Returns:
        None.
    """

    if base.is_whole_portfolio:
        return

    share = base.priced_share_pct
    covered = NOT_AVAILABLE if share is None else f"{share:.2f}%"

    console.print(
        f"[yellow]Los pesos NO son sobre la cartera completa: la base son "
        f"{base.priced_count} de {base.position_count} posiciones "
        f"({covered} de las posiciones, {base.market_value_eur:.2f} € de valor "
        f"de mercado).[/yellow]"
    )
    console.print(
        f"[yellow]Fuera de la base: {base.unpriced_count} posición(es) sin "
        f"valor en euros ({', '.join(base.unpriced_isins)}). Su importe es "
        f"{NOT_AVAILABLE}, así que la parte de la cuenta que representan no se "
        f"puede cuantificar.[/yellow]"
    )
