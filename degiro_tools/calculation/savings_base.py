# Standard libraries
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

# Own modules
from ..domain.constants import CENT_QUANTIZE
from ..domain.models import DividendIncome, SavingsBase

# Share of the positive dividend balance that a negative capital-gains
# balance may offset (art. 49.b LIRPF).
OFFSET_LIMIT_RATE: Final[Decimal] = Decimal("0.25")


def total_dividend_income(dividends: list[DividendIncome]) -> Decimal:
    """
    Gross dividend income of the period.

    Args:
        dividends: Dividends collected during the period.

    Returns:
        Sum of the gross amounts in EUR.
    """

    return sum((dividend.gross_eur for dividend in dividends), Decimal(0))


def total_withholdings(dividends: list[DividendIncome]) -> Decimal:
    """
    Withholdings practised over the dividends, wherever they were taken.

    Args:
        dividends: Dividends collected during the period.

    Returns:
        Sum of the withholdings in EUR, as a positive amount.
    """

    return sum((dividend.withholding_eur for dividend in dividends), Decimal(0))


def domestic_withholdings(dividends: list[DividendIncome]) -> Decimal:
    """
    Spanish withholdings, which are a payment on account.

    Only these subtract from the resulting quota: art. 99 LIRPF makes
    them an advance of the same tax being settled.

    Args:
        dividends: Dividends collected during the period.

    Returns:
        Sum of the Spanish withholdings in EUR, as a positive amount.
    """

    return sum(
        (dividend.withholding_eur for dividend in dividends if dividend.domestic),
        Decimal(0),
    )


def foreign_withholdings(dividends: list[DividendIncome]) -> Decimal:
    """
    Withholdings practised abroad, which are not a payment on account.

    A foreign tax is not an advance of the Spanish one: it gives the
    right to the double-taxation deduction of art. 80 LIRPF, capped by
    the treaty rate and by the average effective rate of the return,
    neither of which can be computed from a broker statement.

    Args:
        dividends: Dividends collected during the period.

    Returns:
        Sum of the foreign withholdings in EUR, as a positive amount.
    """

    return sum(
        (dividend.withholding_eur for dividend in dividends if not dividend.domestic),
        Decimal(0),
    )


def compute_savings_base(
    capital_gains: Decimal,
    dividend_income: Decimal,
    interest_income: Decimal = Decimal(0),
) -> SavingsBase:
    """
    Integrate capital income and capital gains into the savings base.

    Both balances feed the same savings base but are integrated
    separately (art. 49 LIRPF): a positive capital-gains balance simply
    adds up, whereas a negative one may only offset the positive balance
    of ``rendimientos del capital mobiliario`` up to 25% of it. Dividends
    and credit interest are both such returns, so the 25% is measured
    over their sum. Whatever loss is left over is not lost, it carries
    forward to the next four years, so it is reported instead of silently
    dropped.

    Args:
        capital_gains: Net capital gains/losses balance in EUR.
        dividend_income: Gross dividend income in EUR.
        interest_income: Credit interest collected in EUR.

    Returns:
        ``SavingsBase`` with the integration detail and the resulting
        taxable total, never negative.
    """

    capital_income = dividend_income + interest_income

    if capital_gains >= 0:
        return SavingsBase(
            capital_gains=capital_gains,
            dividend_income=dividend_income,
            interest_income=interest_income,
            offset_applied=Decimal(0),
            carryforward_losses=Decimal(0),
            total=capital_gains + capital_income,
        )

    losses = -capital_gains
    limit = (capital_income * OFFSET_LIMIT_RATE).quantize(
        CENT_QUANTIZE, rounding=ROUND_HALF_UP
    )
    offset = min(losses, max(limit, Decimal(0)))

    return SavingsBase(
        capital_gains=capital_gains,
        dividend_income=dividend_income,
        interest_income=interest_income,
        offset_applied=offset,
        carryforward_losses=losses - offset,
        total=capital_income - offset,
    )
