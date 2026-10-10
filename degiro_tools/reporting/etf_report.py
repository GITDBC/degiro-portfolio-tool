# Standard libraries
from decimal import Decimal

# 3pps
from rich import box
from rich.console import Console
from rich.table import Table

# Own modules
from ..calculation.etf_compare import count_agreements
from ..domain.etf import Agreement, ETFCrossCheck
from .helpers import fmt_optional

# Signed columns of the performance table: header, attribute and number
# of decimals. Kept apart from the cost table so both fit an 80 column
# terminal instead of collapsing into unreadable ellipses.
_PERFORMANCE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("YTD %", "return_ytd_pct"),
    ("1A %", "return_1y_pct"),
    ("3A %", "return_3y_pct"),
    ("5A %", "return_5y_pct"),
)

# Risk columns, rendered unsigned except the drawdown.
_RISK_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Vol 3A %", "volatility_3y_pct"),
    ("Caída 3A %", "max_drawdown_3y_pct"),
)


def ter_cell(check: ETFCrossCheck) -> str:
    """
    Render the reported TER, marked when the second source disputes it.

    The figure shown is the authoritative one (justETF for a listed
    fund, Yahoo otherwise). A trailing ``!`` in red means the other
    source publishes a different number, which is a reason to check
    the fund page before trusting either.

    Args:
        check: Cross-check holding both published TERs.

    Returns:
        Rich markup for the TER cell.
    """

    text = fmt_optional(check.ter_pct)

    if check.ter_agreement is Agreement.MISMATCH:
        return f"[bold red]{text}![/bold red]"

    return text


def by_return_per_risk(check: ETFCrossCheck) -> tuple[int, Decimal]:
    """
    Sort key ranking funds by return per unit of risk, best first.

    Funds whose ratio is unknown sort last instead of being treated as
    a zero, which would rank them as the worst performers on the basis
    of missing data.

    Args:
        check: Fund to place in the ranking.

    Returns:
        Tuple of a present-data flag and the negated ratio, so sorting
        ascending puts the best ratio first and the unknowns at the end.
    """

    ratio = None if check.justetf is None else check.justetf.return_per_risk_3y

    if ratio is None:
        return (1, Decimal(0))

    return (0, -ratio)


def build_etf_table(crosschecks: list[ETFCrossCheck]) -> Table:
    """
    Build the single cost, return and risk table.

    One row per fund: what it costs, what it returned and what it made
    you suffer to return it, ranked by return per unit of risk. Seven
    columns is what a terminal fits without truncating a number, so the
    year-to-date and five year returns, the fund size and the
    replication method are left out.

    Args:
        crosschecks: Funds to render.

    Returns:
        Rich table ready to print, ranked by return per risk.
    """

    table = Table(box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Producto", max_width=24, no_wrap=True, overflow="ellipsis")
    table.add_column("TER", justify="right")
    table.add_column("1A %", justify="right")
    table.add_column("3A %", justify="right")
    table.add_column("Vol %", justify="right")
    table.add_column("Caída %", justify="right")
    table.add_column("R/R", justify="right")

    for check in sorted(crosschecks, key=by_return_per_risk):
        profile = check.justetf

        table.add_row(
            check.name,
            ter_cell(check),
            fmt_optional(None if profile is None else profile.return_1y_pct),
            fmt_optional(None if profile is None else profile.return_3y_pct),
            fmt_optional(None if profile is None else profile.volatility_3y_pct),
            fmt_optional(None if profile is None else profile.max_drawdown_3y_pct),
            fmt_optional(None if profile is None else profile.return_per_risk_3y),
        )

    return table


def render_ter_verdict(console: Console, crosschecks: list[ETFCrossCheck]) -> None:
    """
    Report only what the reader has to act on about the fees.

    Silence means both sources agree wherever both publish a TER.
    A disagreement is named fund by fund with the two figures, because
    that is the case where the single number in the table above cannot
    be taken at face value.

    Args:
        console: Console to print to.
        crosschecks: Funds that were compared.

    Returns:
        None.
    """

    disputed = [
        check
        for check in crosschecks
        if check.ter_agreement is Agreement.MISMATCH
        and check.justetf is not None
        and check.yahoo is not None
    ]

    for check in disputed:
        console.print(
            f"[bold red]![/bold red] TER en discrepancia para {check.name}: "
            f"{fmt_optional(check.justetf.ter_pct)} en justETF frente a "  # type: ignore[union-attr]
            f"{fmt_optional(check.yahoo.ter_pct)} en Yahoo Finance."  # type: ignore[union-attr]
        )

    counts = count_agreements(crosschecks)
    confirmed = counts[Agreement.MATCH]
    single = counts[Agreement.ONLY_JUSTETF] + counts[Agreement.ONLY_YAHOO]
    parts = [f"{confirmed} confirmado(s) por Yahoo Finance"]

    if single:
        parts.append(f"{single} de fuente única")

    console.print(f"[dim]TER de justETF: {', '.join(parts)}.[/dim]")


def render_missing(console: Console, missing: list[str] | None) -> None:
    """
    Report the ISINs justETF does not cover.

    Args:
        console: Rich Console instance for output.
        missing: ISINs absent from the catalog.

    Returns:
        None.
    """

    if not missing:
        return

    console.print(
        f"[yellow]Sin datos en justETF ({len(missing)}): "
        f"{', '.join(missing)}. Suele ser una acción individual, un ETC fuera "
        "del buscador o una clase retirada.[/yellow]"
    )


def render_etf_profiles(
    crosschecks: list[ETFCrossCheck],
    missing: list[str] | None = None,
) -> None:
    """
    Show cost, return and risk of the given funds in one table.

    Puts every position on the same yardstick: what it costs, what it
    returned and what it made you suffer to return it, ranked by return
    per risk, since a higher return taken with more volatility is not
    necessarily a better position.

    The fee is a single figure from the primary source rather than one
    column per provider. The second source is used to verify it, and
    only speaks up when it disagrees.

    Args:
        crosschecks: Funds to render, in the order to display them.
        missing: ISINs neither source covers, reported below the table
            so a silent omission is never mistaken for an absent
            position.

    Returns:
        None.
    """

    console = Console()

    if not crosschecks:
        console.print("[yellow]Sin datos de ETFs para los ISIN indicados.[/yellow]")
        render_missing(console, missing)
        return

    console.print(build_etf_table(crosschecks))
    render_ter_verdict(console, crosschecks)
    console.print(
        "[dim]R/R = rentabilidad entre volatilidad a 3 años; compara posiciones "
        "descontando el riesgo asumido. n/d = el fondo no tiene historia "
        "suficiente, no es un cero.[/dim]"
    )
    render_missing(console, missing)
