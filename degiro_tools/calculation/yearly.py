# Standard libraries
from collections import defaultdict
from decimal import Decimal

# Own modules
from ..domain.models import DividendIncome, Sale, YearlyResult


def _gains_by_year(sales: list[Sale]) -> dict[int, tuple[Decimal, int]]:
    """
    Aggregate capital gains and sale counts per calendar year.

    Args:
        sales: Sales matched by FIFO.

    Returns:
        Mapping year to a tuple of net gain/loss in EUR and number of
        sales matched that year.
    """

    totals: dict[int, Decimal] = defaultdict(lambda: Decimal(0))
    counts: dict[int, int] = defaultdict(int)

    for sale in sales:
        totals[sale.date.year] += sale.gain_loss
        counts[sale.date.year] += 1

    return {year: (totals[year], counts[year]) for year in totals}


def _dividends_by_year(
    dividends: list[DividendIncome],
) -> dict[int, tuple[Decimal, Decimal]]:
    """
    Aggregate gross dividends and withholdings per calendar year.

    Args:
        dividends: Dividends collected during the period.

    Returns:
        Mapping year to a tuple of gross income and withholdings, both
        in EUR and the withholding as a positive amount.
    """

    gross: dict[int, Decimal] = defaultdict(lambda: Decimal(0))
    withheld: dict[int, Decimal] = defaultdict(lambda: Decimal(0))

    for dividend in dividends:
        gross[dividend.date.year] += dividend.gross_eur
        withheld[dividend.date.year] += dividend.withholding_eur

    return {year: (gross[year], withheld[year]) for year in gross}


def compute_yearly_results(
    sales: list[Sale], dividends: list[DividendIncome]
) -> list[YearlyResult]:
    """
    Break down the realized result per year and accumulate it.

    Groups sales and dividends by calendar year and walks the years in
    chronological order carrying the running totals, so the caller can
    show both what each year produced and what the account has made
    since the first operation.

    Years present only in the sales or only in the dividends are still
    reported, with zero in the missing concept.

    Args:
        sales: Sales matched by FIFO.
        dividends: Dividends collected during the period.

    Returns:
        One ``YearlyResult`` per year with data, oldest year first.
        Empty when there are neither sales nor dividends.
    """

    gains = _gains_by_year(sales)
    payouts = _dividends_by_year(dividends)

    running_gain = Decimal(0)
    running_dividend = Decimal(0)
    results: list[YearlyResult] = []

    for year in sorted(set(gains) | set(payouts)):
        gain, count = gains.get(year, (Decimal(0), 0))
        gross, withheld = payouts.get(year, (Decimal(0), Decimal(0)))

        running_gain += gain
        running_dividend += gross

        results.append(
            YearlyResult(
                year=year,
                gain_loss=gain,
                dividend_income=gross,
                withholdings=withheld,
                sale_count=count,
                cumulative_gain_loss=running_gain,
                cumulative_dividend_income=running_dividend,
            )
        )

    return results
