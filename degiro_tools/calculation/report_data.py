# Standard libraries
from collections import deque
from decimal import ROUND_HALF_UP, Decimal

# Own modules
from ..domain.constants import CENT_QUANTIZE
from ..domain.models import AccountStatement, Lot, ReportData, Sale
from .savings_base import (
    compute_savings_base,
    domestic_withholdings,
    foreign_withholdings,
    total_dividend_income,
)
from .tax import calculate_irpf_quota, total_quota
from .wash_sale import detect_wash_sales
from .yearly import compute_yearly_results


def build_report_data(
    sales: list[Sale],
    lots: dict[str, deque[Lot]],
    statement: AccountStatement,
    *,
    include_tax: bool,
) -> ReportData:
    """
    Precompute the report aggregates from a statement and its FIFO.

    Integrates the capital gains balance with the gross dividend income
    and the credit interest into a single savings base, breaks down the
    IRPF quota over it, subtracts the Spanish withholdings already
    practised to obtain what is left to pay, and derives the final net
    return. Foreign withholdings are kept apart: they are not a payment
    on account of this tax, only a right to the deduction of art. 80
    LIRPF.

    Taking the whole statement rather than a handful of its fields is
    deliberate: every concept it carries reaches the report, so a caller
    cannot drop one by forgetting to pass it.

    Args:
        sales: Sales matched by FIFO over the statement operations.
        lots: Open portfolio per ISIN left by that matching.
        statement: Parsed Degiro account statement.
        include_tax: If ``False`` omit quota and net return.

    Returns:
        ``ReportData`` with every field precomputed and ready to
        render.
    """

    dividends = statement.dividends
    total_gain_loss = sum((sale.gain_loss for sale in sales), Decimal(0))
    savings_base = compute_savings_base(
        total_gain_loss,
        total_dividend_income(dividends),
        statement.interest_income,
    )
    domestic = domestic_withholdings(dividends)
    foreign = foreign_withholdings(dividends)

    irpf_quota = (
        calculate_irpf_quota(savings_base.total)
        if include_tax and savings_base.total > 0
        else None
    )

    quota_to_pay: Decimal | None = None
    net_return: Decimal | None = None

    if irpf_quota is not None:
        estimated_quota = total_quota(irpf_quota)
        quota_to_pay = (estimated_quota - domestic).quantize(
            CENT_QUANTIZE, rounding=ROUND_HALF_UP
        )
        net_return = (
            savings_base.total
            - estimated_quota
            - statement.connectivity_fees
            - statement.interest_expense
            - foreign
        ).quantize(CENT_QUANTIZE, rounding=ROUND_HALF_UP)

    return ReportData(
        sales=sales,
        total_gain_loss=total_gain_loss,
        dividends=dividends,
        savings_base=savings_base,
        domestic_withholdings=domestic,
        irpf_quota=irpf_quota,
        quota_to_pay=quota_to_pay,
        connectivity_fees=statement.connectivity_fees,
        net_return=net_return,
        pending_lots=lots,
        yearly_results=compute_yearly_results(sales, dividends),
        include_tax=include_tax,
        foreign_withholdings=foreign,
        interest_income=statement.interest_income,
        interest_expense=statement.interest_expense,
        other_income=statement.other_income,
        currency_conversions=statement.currency_conversions,
        unvalued_operations=statement.unvalued_operations,
        wash_sale_alerts=detect_wash_sales(sales, statement.operations, lots),
    )
