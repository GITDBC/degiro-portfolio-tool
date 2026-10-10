# Standard libraries
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

# 3pps
from rich.console import Console
from rich.rule import Rule
from rich.text import Text

# Own modules
from ..domain.constants import CENT_QUANTIZE


def fmt_decimal(value: Decimal) -> str:
    """
    Format a Decimal to two decimals, no thousands separator.

    Every euro figure the reports print goes through here, so the
    rounding of the cent is decided once instead of once per report.

    Args:
        value: Value to format.

    Returns:
        The value as text with two decimals, rounded half up.
    """

    return str(value.quantize(CENT_QUANTIZE, rounding=ROUND_HALF_UP))


def fmt_date(value: date) -> str:
    """
    Format a date the way the Spanish return expects it.

    Args:
        value: Date to format.

    Returns:
        The date as ``dd-mm-yyyy``.
    """

    return value.strftime("%d-%m-%Y")


def fmt_optional(value: Decimal | None, decimals: int = 2) -> str:
    """
    Format an optional Decimal, marking the absence of data.

    Args:
        value: Value to format, possibly missing.
        decimals: Number of decimal places to show.

    Returns:
        The number as text, or ``n/d`` when there is no value, so an
        unavailable figure is never confused with a zero.
    """

    if value is None:
        return "n/d"

    return f"{value:.{decimals}f}"


def signed_text(value: Decimal, *, bold: bool = False, suffix: str = "") -> Text:
    """
    Build a Rich Text colored by the sign of the value.

    Zero counts as positive: a null result is not a loss, so it stays
    green in every report.

    Args:
        value: Signed value to render.
        bold: Whether to add bold to the color.
        suffix: Text appended after the number, such as a percent sign.

    Returns:
        Rich Text in green when the value is zero or positive and red
        when it is negative.
    """

    color = "green" if value >= 0 else "red"
    return Text(
        f"{fmt_decimal(value)}{suffix}", style=f"bold {color}" if bold else color
    )


def weight_color(value: float) -> str:
    """
    Select a Rich color tag based on a weight percentage magnitude.

    Used to visually distinguish high-impact holdings (green) from
    medium (yellow) and low (white) in terminal output.

    Args:
        value: Weight percentage value.

    Returns:
        Rich color name string.
    """

    if value >= 5.0:
        return "green"

    if value >= 1.0:
        return "yellow"

    return "white"


def section(console: Console, title: str, subtitle: str | None = None) -> None:
    """
    Open a report section with its heading and optional caption.

    Args:
        console: Rich Console instance for output.
        title: Section heading.
        subtitle: Explanatory caption printed under the heading, dimmed.

    Returns:
        None.
    """

    console.print(Rule(f"[bold]{title}[/bold]"))

    if subtitle is not None:
        console.print(f"[dim]{subtitle}[/dim]")
