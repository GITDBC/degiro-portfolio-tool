# Standard libraries
from collections import deque
from decimal import Decimal

# 3pps
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

# Own modules
from ..calculation.declaration import (
    DeclarationTotals,
    declaration_lines,
    declaration_totals,
)
from ..calculation.savings_base import (
    foreign_withholdings,
    total_dividend_income,
    total_withholdings,
)
from ..calculation.tax import total_quota
from ..calculation.wash_sale import total_blocked_loss
from ..domain.models import (
    CurrencyConversion,
    DividendIncome,
    Lot,
    OtherIncome,
    ReportData,
    Sale,
    SavingsBase,
    TaxBracket,
    UnvaluedOperation,
    WashSaleAlert,
    YearlyResult,
)
from .helpers import fmt_date, fmt_decimal, section, signed_text

# Tuples of the informative blocks, aliased so the renderers read as
# what they show instead of as their container.
CurrencyConversions = tuple[CurrencyConversion, ...]
OtherIncomes = tuple[OtherIncome, ...]
UnvaluedOperations = tuple[UnvaluedOperation, ...]
WashSaleAlerts = tuple[WashSaleAlert, ...]


def render_dividends(console: Console, dividends: list[DividendIncome]) -> None:
    """
    Render the dividends collected and the tax withheld at source.

    The origin column is what tells a payment on account from a foreign
    tax: the first subtracts from the quota, the second only opens the
    right to the double-taxation deduction.

    Args:
        console: Rich Console instance for output.
        dividends: Dividends collected during the period.

    Returns:
        None.
    """

    section(
        console,
        "Dividendos cobrados",
        "[dim]rendimientos del capital mobiliario por su importe bruto[/dim]",
    )

    if not dividends:
        console.print("[dim]Sin dividendos en el ejercicio.[/dim]")
        return

    table = Table(box=box.ROUNDED, header_style="bold", expand=False)
    table.add_column("Fecha")
    table.add_column("ISIN")
    table.add_column("Producto", overflow="fold")
    table.add_column("Bruto", justify="right")
    table.add_column("Retención", justify="right")
    table.add_column("Origen")
    table.add_column("Neto", justify="right")

    for dividend in dividends:
        table.add_row(
            fmt_date(dividend.date),
            dividend.isin,
            dividend.product,
            fmt_decimal(dividend.gross_eur),
            fmt_decimal(-dividend.withholding_eur),
            "España" if dividend.domestic else "Extranjera",
            fmt_decimal(dividend.net_eur),
        )

    gross = total_dividend_income(dividends)
    withheld = total_withholdings(dividends)

    table.add_section()
    table.add_row(
        "",
        "",
        Text("TOTAL", style="bold"),
        Text(fmt_decimal(gross), style="bold"),
        Text(fmt_decimal(-withheld), style="bold"),
        "",
        Text(fmt_decimal(gross - withheld), style="bold"),
    )

    console.print(table)

    foreign = foreign_withholdings(dividends)

    if foreign > 0:
        console.print(
            f"[yellow]{fmt_decimal(foreign)} EUR de retención extranjera: no es pago a "
            f"cuenta, no se resta de la cuota. Va a la deducción por doble "
            f"imposición internacional (art. 80 LIRPF), limitada al menor de "
            f"la retención soportada, el tope del convenio y el tipo medio "
            f"efectivo de tu declaración.[/yellow]"
        )


def render_interest_income(console: Console, interest_income: Decimal) -> None:
    """
    Render the credit interest integrated into the savings base.

    Args:
        console: Rich Console instance for output.
        interest_income: Credit interest collected in EUR.

    Returns:
        None.
    """

    section(
        console,
        "Intereses de la cuenta",
        "[dim]rendimientos del capital mobiliario, integrados en la base del "
        "ahorro junto con los dividendos[/dim]",
    )
    console.print(f"Intereses abonados: {fmt_decimal(interest_income)} EUR")


def render_other_income(console: Console, other_income: OtherIncomes) -> None:
    """
    Render the amounts credited that no fiscal block claims.

    Promotions and rebates depend on what the broker paid them for, so
    they are shown verbatim and left out of the base instead of being
    integrated into one they may not belong to.

    Args:
        console: Rich Console instance for output.
        other_income: Promotions, rebates and refunds credited.

    Returns:
        None.
    """

    section(
        console,
        "Otros importes abonados",
        "[dim]no integrados en la base: su calificación depende de por qué los "
        "pagó el broker[/dim]",
    )

    table = Table(box=box.ROUNDED, header_style="bold", expand=False)
    table.add_column("Fecha")
    table.add_column("Concepto", max_width=48, overflow="ellipsis")
    table.add_column("EUR", justify="right")

    for item in other_income:
        table.add_row(
            fmt_date(item.date), item.description, fmt_decimal(item.amount_eur)
        )

    console.print(table)
    console.print(
        "[yellow]Decide su tratamiento: una promoción suele ser ganancia "
        "patrimonial de la base general; un reembolso, un menor gasto.[/yellow]"
    )


def render_currency_conversions(
    console: Console, conversions: CurrencyConversions
) -> None:
    """
    Render the currency exchanges that pair with no trade.

    Args:
        console: Rich Console instance for output.
        conversions: Exchanges tied to no purchase or sale.

    Returns:
        None.
    """

    section(
        console,
        "Cambios de divisa sin operación asociada",
        "[dim]permutas de divisa: ganancia o pérdida patrimonial propia "
        "(art. 37.1.h LIRPF), casilla 1634[/dim]",
    )

    table = Table(box=box.ROUNDED, header_style="bold", expand=False)
    table.add_column("Fecha")
    table.add_column("Divisa")
    table.add_column("Importe divisa", justify="right")
    table.add_column("Importe EUR", justify="right")

    for conversion in conversions:
        table.add_row(
            fmt_date(conversion.date),
            conversion.foreign_currency,
            fmt_decimal(conversion.foreign_amount),
            fmt_decimal(conversion.eur_amount),
        )

    console.print(table)
    console.print(
        "[yellow]Resultado no calculado: exige FIFO de divisa con el tipo del "
        "BCE de cada fecha, que el extracto no trae.[/yellow]"
    )
    console.print(
        "[dim]Las ligadas a una compra o venta ya están dentro de sus valores "
        "de adquisición y transmisión.[/dim]"
    )


def render_unvalued_operations(console: Console, unvalued: UnvaluedOperations) -> None:
    """
    Render the trades left out for having no euro consideration.

    Args:
        console: Rich Console instance for output.
        unvalued: Trades the statement does not express in euros.

    Returns:
        None.
    """

    section(
        console,
        "Operaciones excluidas por no tener contravalor en EUR",
        "[dim]el extracto no trae el cambio de divisa de estas órdenes[/dim]",
    )

    table = Table(box=box.ROUNDED, header_style="bold", expand=False)
    table.add_column("Fecha")
    table.add_column("ISIN")
    table.add_column("Concepto", max_width=44, overflow="ellipsis")
    table.add_column("Importe", justify="right")
    table.add_column("Divisa")

    for operation in unvalued:
        table.add_row(
            fmt_date(operation.date),
            operation.isin,
            operation.description,
            fmt_decimal(operation.amount),
            operation.currency,
        )

    console.print(table)
    console.print(
        "[red]Todo el historial de estos ISIN queda fuera del informe: casar "
        "por FIFO una parte de las compras daría una ganancia calculada sobre "
        "un coste incompleto. Calcúlalos aparte con el tipo del BCE de cada "
        "fecha.[/red]"
    )


def render_wash_sales(console: Console, alerts: WashSaleAlerts) -> None:
    """
    Render the losses that the two-month rule may defer.

    Args:
        console: Rich Console instance for output.
        alerts: Detected candidates for art. 33.5.f LIRPF.

    Returns:
        None.
    """

    section(
        console,
        "Posibles pérdidas no imputables (regla de los dos meses)",
        "[dim]art. 33.5.f LIRPF: la pérdida se difiere si se adquirieron "
        "valores homogéneos en los dos meses anteriores o posteriores[/dim]",
    )

    table = Table(box=box.ROUNDED, header_style="bold", expand=False)
    table.add_column("Transm.")
    table.add_column("Adquis.")
    table.add_column("ISIN")
    table.add_column("Cant.", justify="right")
    table.add_column("Pérdida", justify="right")
    table.add_column("Recompras en la ventana")
    table.add_column("Posición")

    for alert in alerts:
        table.add_row(
            fmt_date(alert.transfer_date),
            fmt_date(alert.acquisition_date),
            alert.isin,
            str(alert.quantity),
            signed_text(alert.loss_eur),
            ", ".join(fmt_date(day) for day in alert.repurchase_dates),
            "cerrada" if alert.position_closed else "abierta",
        )

    console.print(table)
    blocked = fmt_decimal(total_blocked_loss(alerts))
    console.print(
        f"[yellow]Importe afectado: {blocked} EUR. Sigue "
        f"dentro de la base: aplicarla exige valorar la homogeneidad en todos "
        f"tus brokers.[/yellow]"
    )

    if all(alert.position_closed for alert in alerts):
        console.print(
            "[dim]Las posiciones quedaron cerradas: la pérdida diferida "
            "afloraría en el mismo ejercicio.[/dim]"
        )


def render_yearly_results(console: Console, results: list[YearlyResult]) -> None:
    """
    Render the realized result per year and its running total.

    The last column is the cumulative result since the first operation
    in the statement, so a single glance shows both the year and the
    whole history. When the statement spans more than one year a notice
    is printed, because the IRPF blocks below aggregate every year into
    one base and each year has to be declared on its own.

    Args:
        console: Rich Console instance for output.
        results: Yearly results with running totals, oldest year first.

    Returns:
        None.
    """

    section(
        console,
        "Resultado realizado por año",
        "[dim]ganancias patrimoniales y dividendos brutos imputados al año de "
        "la operación; la última columna es el acumulado desde el primer "
        "movimiento del extracto[/dim]",
    )

    if not results:
        console.print("[dim]Sin operaciones realizadas.[/dim]")
        return

    table = Table(box=box.ROUNDED, header_style="bold", expand=False)
    table.add_column("Año")
    table.add_column("Ventas", justify="right")
    table.add_column("G/P patrimonial", justify="right")
    table.add_column("Dividendos", justify="right")
    table.add_column("Retenciones", justify="right")
    table.add_column("Resultado año", justify="right")
    table.add_column("Acumulado", justify="right")

    for result in results:
        table.add_row(
            str(result.year),
            str(result.sale_count),
            signed_text(result.gain_loss),
            fmt_decimal(result.dividend_income),
            fmt_decimal(-result.withholdings),
            signed_text(result.total),
            signed_text(result.cumulative_total, bold=True),
        )

    console.print(table)

    if len(results) > 1:
        console.print(
            "[yellow]El extracto abarca varios ejercicios: los bloques de base "
            "del ahorro y cuota que siguen agregan todos los años en uno. Cada "
            "ejercicio se declara por separado.[/yellow]"
        )


def render_savings_base(console: Console, savings: SavingsBase) -> None:
    """
    Render how dividends and capital gains integrate into the base.

    Args:
        console: Rich Console instance for output.
        savings: Savings-base integration detail.

    Returns:
        None.
    """

    section(console, "Base imponible del ahorro")

    table = Table(box=box.ROUNDED, header_style="bold", expand=False)
    table.add_column("Concepto")
    table.add_column("EUR", justify="right")

    table.add_row("Dividendos (bruto)", fmt_decimal(savings.dividend_income))

    if savings.interest_income:
        table.add_row("Intereses de la cuenta", fmt_decimal(savings.interest_income))

    table.add_row(
        "Ganancias/pérdidas patrimoniales", fmt_decimal(savings.capital_gains)
    )

    if savings.offset_applied > 0:
        table.add_row(
            "Compensación de pérdidas (límite 25%)",
            fmt_decimal(-savings.offset_applied),
        )

    table.add_section()
    table.add_row(
        Text("BASE DEL AHORRO", style="bold"),
        Text(fmt_decimal(savings.total), style="bold"),
    )

    console.print(table)

    if savings.carryforward_losses > 0:
        console.print(
            f"[yellow]Pérdidas pendientes de compensar: "
            f"{fmt_decimal(savings.carryforward_losses)} EUR. Compensables en los "
            f"4 ejercicios siguientes (art. 49 LIRPF).[/yellow]"
        )


def render_matched_sales(console: Console, sales: list[Sale]) -> None:
    """
    Render the table of FIFO-matched sales.

    Args:
        console: Rich Console instance for output.
        sales: List of matched sales.

    Returns:
        None.
    """

    section(console, "Ventas casadas (FIFO por ISIN)")
    console.print("[dim]valores en EUR reales del broker[/dim]")

    if not sales:
        console.print("[dim]Sin ventas en el ejercicio.[/dim]")
        return

    table = Table(box=box.ROUNDED, header_style="bold", expand=False)
    table.add_column("Fecha")
    table.add_column("ISIN")
    # The issuer name is what the return asks for, so it is never cut:
    # a truncated name is a name you have to go and look up elsewhere.
    table.add_column("Producto", overflow="fold")
    table.add_column("Cant.", justify="right")
    table.add_column("Coste adq.", justify="right")
    table.add_column("Valor trans.", justify="right")
    table.add_column("G/P", justify="right")

    for sale in sales:
        table.add_row(
            fmt_date(sale.date),
            sale.isin,
            sale.product,
            str(sale.quantity),
            fmt_decimal(sale.acquisition_cost),
            fmt_decimal(sale.transfer_value),
            signed_text(sale.gain_loss, bold=True),
        )

    console.print(table)


def render_declaration_lines(console: Console, sales: list[Sale]) -> None:
    """
    Render the lines to type into the tax return, one per acquisition.

    The return declares every transferred element with its acquisition
    date and value, so a sale that consumed lots bought on different
    days needs several lines even though it was a single order. Fees
    are already inside both values (art. 35 LIRPF): they must not be
    deducted again.

    Args:
        console: Rich Console instance for output.
        sales: List of matched sales.

    Returns:
        None.
    """

    section(
        console,
        "Detalle para la declaración",
        "[dim]una línea por fecha de adquisición, comisiones ya incluidas en "
        "ambos valores[/dim]",
    )

    lines = declaration_lines(sales)

    if not lines:
        console.print("[dim]Sin transmisiones que declarar.[/dim]")
        return

    table = Table(box=box.SIMPLE_HEAD, header_style="bold", expand=False)
    table.add_column("Transm.", no_wrap=True)
    table.add_column("Adquis.", no_wrap=True)
    table.add_column("ISIN", no_wrap=True)
    table.add_column("Cant.", justify="right")
    table.add_column("Valor t.", justify="right")
    table.add_column("Valor a.", justify="right")
    table.add_column("G/P", justify="right")

    for sale, match in lines:
        table.add_row(
            fmt_date(sale.date),
            fmt_date(match.acquisition_date),
            sale.isin,
            str(match.quantity),
            fmt_decimal(match.transfer_value),
            fmt_decimal(match.acquisition_cost),
            signed_text(match.gain_loss),
        )

    totals = declaration_totals(sales)

    table.add_section()
    table.add_row(
        Text("TOTAL", style="bold"),
        "",
        "",
        "",
        Text(fmt_decimal(totals.transfer_value), style="bold"),
        Text(fmt_decimal(totals.acquisition_cost), style="bold"),
        signed_text(totals.gain_loss, bold=True),
    )

    console.print(table)
    warn_on_rounding_drift(console, totals)


def warn_on_rounding_drift(console: Console, totals: DeclarationTotals) -> None:
    """
    Warn when the declared lines drift from the real sale total.

    Rounding each line to the cent can differ by a few cents from the
    gain computed once per sale. The sale total is the accurate figure,
    so the difference is disclosed instead of hidden.

    Args:
        console: Rich Console instance for output.
        totals: Aggregates of the declared lines.

    Returns:
        None.
    """

    if totals.rounding_drift == 0:
        return

    drift = fmt_decimal(totals.rounding_drift)
    console.print(
        f"[dim]El total de las líneas difiere en {drift} EUR "
        f"del total real ({fmt_decimal(totals.real_gain_loss)} EUR) por redondear cada "
        f"línea al céntimo.[/dim]"
    )


def render_total_gain_loss(console: Console, total_gain_loss: Decimal) -> None:
    """
    Render the highlighted total gain or loss of the period.

    No per-ISIN breakdown accompanies it: regrouping the same result by
    instrument answers no question of the return, which is filled in per
    transmitted element, and the sales table already shows each ISIN.

    Args:
        console: Rich Console instance for output.
        total_gain_loss: Total gain or loss across all sales.

    Returns:
        None.
    """

    if total_gain_loss > 0:
        color = "green"
    elif total_gain_loss < 0:
        color = "red"
    else:
        color = "yellow"

    console.print(
        Panel(
            Text(
                f"{fmt_decimal(total_gain_loss)} EUR",
                style=f"bold {color}",
                justify="center",
            ),
            title="[bold]TOTAL GANANCIA/PÉRDIDA PATRIMONIAL[/bold]",
            border_style=color,
            box=box.HEAVY,
        )
    )


def build_brackets_table(irpf_quota: list[TaxBracket], total_quota: Decimal) -> Table:
    """
    Build a Rich table with tax brackets and total tax row.

    Args:
        irpf_quota: List of tax bracket breakdowns.
        total_quota: Total estimated tax amount.

    Returns:
        Rich Table with brackets and total.
    """

    table = Table(box=box.ROUNDED, header_style="bold", expand=False)
    table.add_column("Desde", justify="right")
    table.add_column("Hasta", justify="right")
    table.add_column("Tipo", justify="right")
    table.add_column("Base", justify="right")
    table.add_column("Cuota", justify="right")

    for bracket in irpf_quota:
        table.add_row(
            fmt_decimal(bracket.lower),
            fmt_decimal(bracket.upper) if bracket.upper is not None else "∞",
            f"{int(bracket.rate * 100)}%",
            fmt_decimal(bracket.base),
            fmt_decimal(bracket.quota),
        )

    table.add_section()
    table.add_row(
        "",
        "",
        "",
        Text("CUOTA ESTIMADA TOTAL", style="bold"),
        Text(fmt_decimal(total_quota), style="bold yellow"),
    )
    return table


def render_irpf_quota(
    console: Console,
    irpf_quota: list[TaxBracket] | None,
    withholdings: Decimal,
    quota_to_pay: Decimal | None,
) -> None:
    """
    Render the IRPF tax estimation over the savings base.

    Shows the progressive bracket breakdown, subtracts the Spanish
    withholdings already practised and reports the resulting amount left
    to pay (or to be refunded). The return after tax is left to the final
    panel, which deducts the costs this one ignores instead of stating
    the same figure twice.

    Args:
        console: Rich Console instance for output.
        irpf_quota: Tax bracket breakdown, or None if not applicable.
        withholdings: Spanish withholdings already paid on account.
        quota_to_pay: Quota left to pay after the withholdings.

    Returns:
        None.
    """

    section(
        console,
        "Estimación cuota IRPF",
        "[dim]tramos art. 66 LIRPF. Estimación aislada: la cuota real depende "
        "del resto de tu base y de años anteriores[/dim]",
    )

    if not irpf_quota:
        console.print(
            "[dim]Sin cuota: no hay base del ahorro positiva en el ejercicio.[/dim]"
        )
        return

    console.print(build_brackets_table(irpf_quota, total_quota(irpf_quota)))

    if withholdings > 0 and quota_to_pay is not None:
        console.print(
            f"[dim]Retenciones ya practicadas: -{fmt_decimal(withholdings)} EUR[/dim]"
        )
        label = "A ingresar" if quota_to_pay >= 0 else "A devolver"
        color = "yellow" if quota_to_pay >= 0 else "green"
        console.print(
            Panel(
                Text(
                    f"{fmt_decimal(abs(quota_to_pay))} EUR",
                    style=f"bold {color}",
                    justify="center",
                ),
                title=f"[bold]{label} tras retenciones[/bold]",
                border_style=color,
                box=box.ROUNDED,
            )
        )


def render_unattributed_costs(
    console: Console, connectivity_fees: Decimal, interest_expense: Decimal
) -> None:
    """
    Render the costs that no single operation carries.

    Args:
        console: Rich Console instance for output.
        connectivity_fees: Total connectivity fees in EUR.
        interest_expense: Debit interest charged on the balance, in EUR.

    Returns:
        None.
    """

    parts = []

    if connectivity_fees > 0:
        parts.append(f"conectividad {fmt_decimal(connectivity_fees)} EUR")

    if interest_expense > 0:
        parts.append(f"intereses deudores {fmt_decimal(interest_expense)} EUR")

    section(
        console,
        "Gastos no imputados a operaciones",
        f"[dim]{', '.join(parts)}. No suman al coste FIFO ni son gasto "
        f"deducible de los rendimientos; solo reducen tu rentabilidad "
        f"real[/dim]",
    )


def render_net_return(console: Console, net_return: Decimal, *, costs: bool) -> None:
    """
    Render the final net return panel.

    Args:
        console: Rich Console instance for output.
        net_return: Net return amount in EUR.
        costs: Whether costs outside the operations were deducted too,
            which is what the title has to say.

    Returns:
        None.
    """

    if net_return > 0:
        color = "green"
    elif net_return < 0:
        color = "red"
    else:
        color = "yellow"

    title = (
        "RENTABILIDAD NETA REAL (tras impuestos y gastos)"
        if costs
        else "RENTABILIDAD NETA REAL (tras impuestos)"
    )

    console.print(
        Panel(
            Text(
                f"{fmt_decimal(net_return)} EUR",
                style=f"bold {color}",
                justify="center",
            ),
            title=f"[bold]{title}[/bold]",
            border_style=color,
            box=box.HEAVY,
        )
    )


def render_pending_lots(console: Console, lots: dict[str, deque[Lot]]) -> None:
    """
    Render the open portfolio (pending lots) grouped by ISIN.

    Args:
        console: Rich Console instance for output.
        lots: Dict mapping ISIN to deque of remaining lots.

    Returns:
        None.
    """

    section(console, "Cartera abierta (lotes pendientes)")

    items: list[tuple[str, Lot]] = [
        (isin, lot) for isin in sorted(lots) for lot in lots[isin]
    ]

    if not items:
        console.print("[dim]Sin cartera abierta.[/dim]")
        return

    table = Table(box=box.ROUNDED, header_style="bold", expand=False)
    table.add_column("Fecha")
    table.add_column("ISIN")
    table.add_column("Cantidad", justify="right")
    table.add_column("Coste medio (EUR/ud)", justify="right")

    prev_isin: str | None = None
    for isin, lot in items:
        if prev_isin is not None and isin != prev_isin:
            table.add_section()
        table.add_row(
            fmt_date(lot.date),
            isin,
            str(lot.quantity),
            str(lot.unit_cost.quantize(Decimal("0.0001"))),
        )
        prev_isin = isin

    console.print(table)


def render_report_sections(console: Console, data: ReportData) -> None:
    """
    Orchestrate the rendering from precomputed ReportData.

    Only blocks that answer something appear. The per-year breakdown
    waits until there is more than one year to break down, and every
    concept the statement does not carry stays off screen, so what is
    printed is what the reader has to act on.

    Args:
        console: Rich Console instance for output.
        data: Precomputed report data with sales, dividends, taxes and
            lots.

    Returns:
        None.
    """

    console.print(
        Rule(
            "[bold]INFORME FISCAL — GANANCIAS/PÉRDIDAS PATRIMONIALES "
            "(IRPF España)[/bold]"
        )
    )
    render_matched_sales(console, data.sales)
    render_declaration_lines(console, data.sales)

    if data.wash_sale_alerts:
        render_wash_sales(console, data.wash_sale_alerts)

    render_total_gain_loss(console, data.total_gain_loss)
    render_dividends(console, data.dividends)

    if data.interest_income:
        render_interest_income(console, data.interest_income)

    # A single exercise needs no per-year table: every figure in it is
    # already above and the running total equals the year itself.
    if len(data.yearly_results) > 1:
        render_yearly_results(console, data.yearly_results)

    if data.unvalued_operations:
        render_unvalued_operations(console, data.unvalued_operations)

    if data.currency_conversions:
        render_currency_conversions(console, data.currency_conversions)

    if data.other_income:
        render_other_income(console, data.other_income)

    if data.include_tax:
        render_savings_base(console, data.savings_base)
        render_irpf_quota(
            console,
            data.irpf_quota,
            data.domestic_withholdings,
            data.quota_to_pay,
        )

    costs = data.connectivity_fees > 0 or data.interest_expense > 0

    if costs:
        render_unattributed_costs(
            console, data.connectivity_fees, data.interest_expense
        )

    if data.net_return is not None:
        render_net_return(console, data.net_return, costs=costs)

    render_pending_lots(console, data.pending_lots)


def print_report(data: ReportData, console: Console | None = None) -> None:
    """
    Print the full fiscal report using rich.

    Receives the aggregates already computed by ``build_report_data``,
    so this module only renders and never recalculates.

    Args:
        data: Precomputed report aggregates.
        console: Rich console to use. If ``None`` a default one is
            created.
    """

    render_report_sections(console or Console(), data)
