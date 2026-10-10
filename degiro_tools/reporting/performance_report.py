# Standard libraries
from decimal import Decimal

# 3pps
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

# Own modules
from ..domain.models import (
    PerformanceSummary,
    PositionPerformance,
    RealReturnSummary,
)
from .helpers import fmt_decimal, signed_text


def build_positions_table(
    positions: list[PositionPerformance],
    real: RealReturnSummary | None = None,
) -> Table:
    """
    Build the table of open positions and their unrealized result.

    Quantity, unit cost and market value are left out on purpose: the
    valuation table already shows quantity and value, ``tax`` shows the
    average cost of the open lots, and eleven columns do not fit a
    terminal.

    Args:
        positions: Open positions, already sorted for display.
        real: Inflation-adjusted result, which adds the real annual
            column beside the nominal one. Absent when no price index
            could be obtained.

    Returns:
        Rich table with cost, unrealized result and the annualized
        return where the holding period makes it meaningful.
    """

    table = Table(box=box.ROUNDED, header_style="bold", expand=False)
    table.add_column("Producto", max_width=24, no_wrap=True, overflow="ellipsis")
    table.add_column("Coste €", justify="right")
    table.add_column("Latente €", justify="right")
    table.add_column("%", justify="right")
    table.add_column("Días", justify="right")
    table.add_column("Anual. %", justify="right")

    if real is not None:
        table.add_column("Anual. real %", justify="right")

    for position in positions:
        cells: list[str | Text] = [
            position.product,
            fmt_decimal(position.cost_total),
            signed_text(position.gain_loss),
            signed_text(position.gain_loss_pct),
            str(position.holding_days),
            _annual_cell(position.annualized_pct),
        ]

        if real is not None:
            cells.append(_annual_cell(real.annualized_for(position.isin)))

        table.add_row(*cells)

    return table


def _annual_cell(value: Decimal | None) -> Text:
    """
    Render an annualized percentage, or the notice that there is none.

    Args:
        value: The percentage, or None when the figure is withheld.

    Returns:
        The signed percentage, or a dim ``n/d``: a withheld annual
        return is a datum that does not exist, not a zero.
    """

    if value is None:
        return Text("n/d", style="dim")

    return signed_text(value)


def build_summary_table(
    summary: PerformanceSummary, real: RealReturnSummary | None = None
) -> Table:
    """
    Build the consolidated result table.

    Args:
        summary: Realized and unrealized result of the account.
        real: Inflation-adjusted result, which adds the cost restated in
            today's euros and the result net of inflation. Absent when
            no price index could be obtained.

    Returns:
        Rich table adding up the unrealized result, the gains already
        realized and the dividends collected.
    """

    table = Table(box=box.ROUNDED, header_style="bold", expand=False)
    table.add_column("Concepto")
    table.add_column("EUR", justify="right")

    table.add_row("Coste de la cartera abierta", fmt_decimal(summary.cost_total))
    table.add_row("Valor de mercado actual", fmt_decimal(summary.market_value))
    table.add_row(
        "Resultado latente (no tributa)",
        signed_text(summary.unrealized_gain_loss),
    )
    table.add_row("  sobre el coste", signed_text(summary.unrealized_pct, suffix="%"))

    if real is not None:
        _add_real_rows(table, real)

    table.add_section()
    table.add_row(
        "Ganancias realizadas (tributan)", signed_text(summary.realized_gain_loss)
    )
    table.add_row("Dividendos brutos (tributan)", fmt_decimal(summary.dividend_income))
    table.add_section()
    table.add_row(
        Text("RESULTADO TOTAL", style="bold"),
        signed_text(summary.total_result),
    )

    return table


def _add_real_rows(table: Table, real: RealReturnSummary) -> None:
    """
    Add the inflation-adjusted rows to the consolidated table.

    Placed right under the nominal result rather than in a block of
    their own, because the pair is the point: the nominal figure says
    how many more euros there are and the real one says whether those
    euros buy more than the ones that went in.

    Args:
        table: Consolidated table being built.
        real: Inflation-adjusted result of the open portfolio.

    Returns:
        None.
    """

    table.add_section()
    table.add_row(
        f"Coste en euros de {real.reference_period}",
        fmt_decimal(real.cost_total),
    )
    table.add_row(
        "Resultado latente real (sin inflación)",
        signed_text(real.gain_loss),
    )
    table.add_row("  sobre el coste real", signed_text(real.gain_loss_pct, suffix="%"))


def render_performance(
    summary: PerformanceSummary, real: RealReturnSummary | None = None
) -> None:
    """
    Show the unrealized result next to the realized one.

    Separates the two on purpose: the realized part has already been
    cashed in and feeds the savings base, while the unrealized part is
    still on paper and does not tax until the position is transmitted.
    Adding them gives what the account has produced, not what has to be
    declared.

    Args:
        summary: Realized and unrealized result of the account.
        real: Inflation-adjusted result of the same positions, shown
            beside the nominal one. Absent when no price index could be
            obtained, in which case only the nominal figures appear.

    Returns:
        None.
    """

    console = Console()

    if not summary.positions:
        console.print("[yellow]Sin posiciones abiertas que valorar.[/yellow]")
        render_unpriced(console, summary.unpriced_isins)
        return

    console.rule("[bold cyan]Resultado latente por posición[/bold cyan]")
    console.print(
        "[dim]coste FIFO de los lotes abiertos frente al precio actual; "
        "comisiones de compra incluidas en el coste[/dim]"
    )
    console.print(build_positions_table(summary.positions, real))
    console.print(
        "[dim]Anual. % = rentabilidad anual ponderada por dinero (TIR) sobre las "
        "fechas de compra. n/d cuando la posición lleva menos de 90 días: "
        "anualizar unas semanas convierte el ruido en titular.[/dim]"
    )

    console.rule("[bold cyan]Resultado consolidado[/bold cyan]")
    console.print(build_summary_table(summary, real))
    render_real_note(console, real)
    render_unpriced(console, summary.unpriced_isins)


def render_real_note(console: Console, real: RealReturnSummary | None) -> None:
    """
    Explain what the real figures were deflated with.

    A real return is only auditable if the report says which index
    produced it and which month it restated the money to, so the caption
    names both. It also states when the last published month had to be
    carried forward, and when the total covers only part of the
    portfolio: a partial real total is disclosed, the way a partial
    weight base is, rather than withheld.

    Args:
        console: Rich Console instance for output.
        real: Inflation-adjusted result, or None when there is none.

    Returns:
        None.
    """

    if real is None:
        return

    console.print(
        f"[dim]Real = descontada la inflación con el IPC armonizado de "
        f"{real.region_label} (BCE), en euros de {real.reference_period}, "
        f"el último mes publicado.[/dim]"
    )

    if real.carried_forward:
        console.print(
            f"[dim]El IPC se publica con unas seis semanas de retraso: las "
            f"compras posteriores a {real.reference_period} se descuentan con "
            f"ese mismo índice, es decir con un factor de 1,00. No se "
            f"extrapola la inflación que aún no se ha publicado.[/dim]"
        )

    if not real.is_complete:
        console.print(
            f"[yellow]Coste real parcial: {len(real.positions)} de "
            f"{len(real.positions) + len(real.uncovered_isins)} posiciones "
            f"tienen índice. Quedan fuera del total real "
            f"{', '.join(real.uncovered_isins)}, cuyo coste no se puede "
            f"expresar en euros de hoy; su resultado nominal sí está "
            f"arriba.[/yellow]"
        )


def render_unpriced(console: Console, unpriced: tuple[str, ...]) -> None:
    """
    Report the open ISINs left out for want of a price.

    Args:
        console: Rich Console instance for output.
        unpriced: ISINs with open lots and no price available.

    Returns:
        None.
    """

    if not unpriced:
        return

    console.print(
        f"[yellow]Sin precio actual ({len(unpriced)}): {', '.join(unpriced)}. "
        "Su coste y su resultado latente quedan fuera de los totales.[/yellow]"
    )
