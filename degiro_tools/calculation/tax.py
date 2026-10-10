# Standard libraries
from decimal import ROUND_HALF_UP, Decimal

# Own modules
from ..domain.constants import CENT_QUANTIZE
from ..domain.models import TaxBracket

SAVINGS_BRACKETS: tuple[tuple[Decimal | None, Decimal], ...] = (
    (Decimal("6000"), Decimal("0.19")),
    (Decimal("50000"), Decimal("0.21")),
    (Decimal("200000"), Decimal("0.23")),
    (Decimal("300000"), Decimal("0.27")),
    # Top savings-base rate for income over EUR 300,000.
    (None, Decimal("0.30")),
)


def calculate_irpf_quota(savings_base: Decimal) -> list[TaxBracket]:
    """
    Break down the IRPF quota by savings-base brackets.

    The base must already integrate every savings-base item (dividends
    and other ``rendimientos del capital mobiliario`` plus capital
    gains), since art. 66 LIRPF applies the progressive scale to their
    total rather than to each item separately.

    Args:
        savings_base: Total positive savings base in EUR.

    Returns:
        List of applied brackets with base and quota. Empty if
        ``savings_base <= 0``.
    """

    if savings_base <= 0:
        return []

    breakdown: list[TaxBracket] = []
    lower = Decimal(0)
    remaining = savings_base

    for upper, rate in SAVINGS_BRACKETS:
        width = (upper - lower) if upper is not None else remaining
        bracket_base = min(remaining, width)

        if bracket_base <= 0:
            break

        quota = (bracket_base * rate).quantize(CENT_QUANTIZE, rounding=ROUND_HALF_UP)
        breakdown.append(
            TaxBracket(
                lower=lower.quantize(CENT_QUANTIZE),
                upper=upper.quantize(CENT_QUANTIZE) if upper is not None else None,
                rate=rate,
                base=bracket_base.quantize(CENT_QUANTIZE, rounding=ROUND_HALF_UP),
                quota=quota,
            )
        )
        remaining -= bracket_base
        lower = upper if upper is not None else lower

        if remaining <= 0:
            break

    return breakdown


def total_quota(brackets: list[TaxBracket]) -> Decimal:
    """
    Add up the quota of every applied bracket.

    Args:
        brackets: Bracket breakdown over the savings base.

    Returns:
        Total estimated quota in EUR, zero when no bracket applies.
    """

    return sum((bracket.quota for bracket in brackets), Decimal(0))
