# Standard libraries
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

# 3pps
import pytest

# Own modules
from degiro_tools import parse_account_xlsx
from degiro_tools.calculation.savings_base import (
    compute_savings_base,
    domestic_withholdings,
    foreign_withholdings,
    total_dividend_income,
    total_withholdings,
)
from degiro_tools.domain.models import DividendIncome
from tests.conftest import account_row, write_account_xlsx

GROSS = Decimal("9")
WITHHELD = Decimal("1.71")
EXPECTED_DIVIDENDS = 2


def dividend_rows(
    *,
    currency: str = "EUR",
    date_str: str = "10-07-2026",
    isin: str = "ES0118594417",
) -> list[list[object]]:
    """
    Build a dividend payment with its withholding.

    Degiro assigns no Order ID to dividends, so both rows share the
    ``(date, time, ISIN)`` fallback grouping key.

    Args:
        currency: Currency of the gross dividend row.
        date_str: Payment date in ``dd-mm-yyyy`` format.
        isin: ISIN of the paying instrument.

    Returns:
        Account-statement rows for the payment.
    """

    return [
        account_row(
            "Retención del dividendo",
            currency="EUR",
            amount=float(-WITHHELD),
            isin=isin,
            date_str=date_str,
            product="INDRA SISTEMAS SA",
        ),
        account_row(
            "Dividendo",
            currency=currency,
            amount=float(GROSS),
            isin=isin,
            date_str=date_str,
            product="INDRA SISTEMAS SA",
        ),
    ]


def test_dividend_and_withholding_are_parsed(tmp_path: Path) -> None:
    """
    A dividend row and its withholding become one DividendIncome.

    Both used to be discarded because the group carried no buy/sell
    header, leaving the savings base understated.
    """

    path = write_account_xlsx(tmp_path / "dividend.xlsx", dividend_rows())

    statement = parse_account_xlsx(path)

    assert statement.operations == []
    assert len(statement.dividends) == 1

    dividend = statement.dividends[0]

    assert dividend.date == date(2026, 7, 10)
    assert dividend.isin == "ES0118594417"
    assert dividend.product == "INDRA SISTEMAS SA"
    assert dividend.gross_eur == GROSS
    assert dividend.withholding_eur == WITHHELD
    assert dividend.net_eur == GROSS - WITHHELD


def test_dividends_are_sorted_by_date(tmp_path: Path) -> None:
    """
    Dividends are returned in chronological order.
    """

    rows = dividend_rows(date_str="18-06-2026", isin="ES0105777017") + dividend_rows()
    path = write_account_xlsx(tmp_path / "dividends.xlsx", rows)

    statement = parse_account_xlsx(path)

    assert len(statement.dividends) == EXPECTED_DIVIDENDS
    assert statement.dividends[0].date == date(2026, 6, 18)
    assert statement.dividends[1].date == date(2026, 7, 10)


def test_foreign_currency_dividend_without_a_rate_is_reported_and_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    A dividend in a foreign currency with no exchange row is excluded.

    Nothing in the statement says what it was worth in euros, so
    treating the amount as EUR would misstate the savings base.
    """

    path = write_account_xlsx(
        tmp_path / "usd_dividend.xlsx", dividend_rows(currency="USD")
    )

    with caplog.at_level(logging.WARNING):
        statement = parse_account_xlsx(path)

    assert statement.dividends == []
    assert "USD" in caplog.text
    assert "cambio de divisa" in caplog.text
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_foreign_currency_dividend_uses_the_exchange_it_settled_with(
    tmp_path: Path,
) -> None:
    """
    A dividend in a foreign currency is converted by its own exchange.

    Degiro settles the payment through a ``Cambio de Divisa`` pair, and
    its two legs state the rate applied. Excluding a payment whose rate
    the statement does carry left real income out of the base.
    """

    rows = [
        account_row(
            "Dividendo",
            currency="USD",
            amount=20.0,
            isin="US0378331005",
            date_str="10-07-2026",
            product="APPLE INC",
        ),
        account_row(
            "Retención del dividendo",
            currency="USD",
            amount=-3.0,
            isin="US0378331005",
            date_str="10-07-2026",
            product="APPLE INC",
        ),
        # 17 USD credited net, settled as 8.50 EUR: half a euro per USD.
        account_row(
            "Retirada Cambio de Divisa",
            currency="USD",
            amount=-17.0,
            isin="US0378331005",
            date_str="10-07-2026",
            product="APPLE INC",
        ),
        account_row(
            "Ingreso Cambio de Divisa",
            currency="EUR",
            amount=8.5,
            isin="US0378331005",
            date_str="10-07-2026",
            product="APPLE INC",
        ),
    ]
    path = write_account_xlsx(tmp_path / "usd_with_fx.xlsx", rows)

    statement = parse_account_xlsx(path)

    assert len(statement.dividends) == 1

    dividend = statement.dividends[0]

    assert dividend.gross_eur == Decimal("10.00")
    assert dividend.withholding_eur == Decimal("1.50")
    # A US issuer withholds abroad: not a payment on account.
    assert dividend.domestic is False


def test_withholding_origin_comes_from_the_issuer_country(tmp_path: Path) -> None:
    """
    A Spanish ISIN withholds at home, any other one withholds abroad.

    The distinction decides what can be done with the amount: subtract
    it from the quota, or claim the double-taxation deduction.
    """

    rows = dividend_rows() + dividend_rows(isin="NL0011794037")
    path = write_account_xlsx(tmp_path / "origins.xlsx", rows)

    statement = parse_account_xlsx(path)
    origins = {dividend.isin: dividend.domestic for dividend in statement.dividends}

    assert origins == {"ES0118594417": True, "NL0011794037": False}


def test_two_gross_rows_in_one_group_are_added_up(tmp_path: Path) -> None:
    """
    A payment split across two gross rows counts in full.

    Reading only the first row declared half the dividend.
    """

    rows = [
        account_row(
            "Dividendo",
            currency="EUR",
            amount=6.0,
            isin="ES0118594417",
            date_str="10-07-2026",
        ),
        account_row(
            "Dividendo",
            currency="EUR",
            amount=3.0,
            isin="ES0118594417",
            date_str="10-07-2026",
        ),
    ]
    path = write_account_xlsx(tmp_path / "split.xlsx", rows)

    statement = parse_account_xlsx(path)

    assert statement.dividends[0].gross_eur == Decimal("9")


@pytest.mark.parametrize(
    "description",
    ["Dividendo (ajuste)", "Reparto de dividendo", "Pago de dividendo"],
)
def test_dividend_description_variants_are_recognized(
    tmp_path: Path, description: str
) -> None:
    """
    ``Dividendo (ajuste)`` and ``Reparto de dividendo`` are dividends.

    Requiring the exact word ``Dividendo`` dropped both without a
    word, and the report declared no dividend at all.
    """

    rows = [
        account_row(
            description,
            currency="EUR",
            amount=5.0,
            isin="ES0118594417",
            date_str="10-07-2026",
        )
    ]
    path = write_account_xlsx(tmp_path / "variant.xlsx", rows)

    statement = parse_account_xlsx(path)

    assert len(statement.dividends) == 1
    assert statement.dividends[0].gross_eur == Decimal("5")


def test_dividend_processing_fee_is_not_read_as_a_dividend(tmp_path: Path) -> None:
    """
    A fee that names dividends is a fee, not income.

    Matching the word anywhere in the description would have turned the
    charge into a dividend collected.
    """

    rows = [
        account_row(
            "Comisión de procesamiento de dividendos",
            currency="EUR",
            amount=-1.0,
            isin="ES0118594417",
            date_str="10-07-2026",
        )
    ]
    path = write_account_xlsx(tmp_path / "divfee.xlsx", rows)

    statement = parse_account_xlsx(path)

    assert statement.dividends == []


def test_withholding_alone_is_not_a_dividend(tmp_path: Path) -> None:
    """
    A withholding row without its gross dividend yields nothing.

    ``Retención del dividendo`` mentions a dividend but is not one.
    """

    rows = [
        account_row(
            "Retención del dividendo",
            currency="EUR",
            amount=float(-WITHHELD),
            isin="ES0118594417",
        )
    ]
    path = write_account_xlsx(tmp_path / "withholding.xlsx", rows)

    assert parse_account_xlsx(path).dividends == []


def test_savings_base_adds_gains_and_dividends() -> None:
    """
    A positive gains balance simply adds to the dividend income.

    Art. 66 LIRPF applies the progressive scale to the total savings
    base, not to each item separately.
    """

    result = compute_savings_base(Decimal("500"), Decimal("100"))

    assert result.total == Decimal("600")
    assert result.offset_applied == Decimal(0)
    assert result.carryforward_losses == Decimal(0)


def test_savings_base_caps_loss_offset_at_25_percent() -> None:
    """
    A negative gains balance offsets at most 25% of the dividends.

    The remainder carries forward to the next four years instead of
    reducing this year's base (art. 49.b LIRPF).
    """

    result = compute_savings_base(Decimal("-500"), Decimal("100"))

    assert result.offset_applied == Decimal("25.00")
    assert result.total == Decimal("75.00")
    assert result.carryforward_losses == Decimal("475.00")


def test_savings_base_offsets_only_what_the_loss_covers() -> None:
    """
    A loss smaller than the 25% ceiling is offset in full.
    """

    result = compute_savings_base(Decimal("-10"), Decimal("100"))

    assert result.offset_applied == Decimal("10")
    assert result.total == Decimal("90")
    assert result.carryforward_losses == Decimal(0)


def test_savings_base_without_dividends_carries_the_whole_loss() -> None:
    """
    With no dividends to offset, the entire loss carries forward.
    """

    result = compute_savings_base(Decimal("-500"), Decimal(0))

    assert result.total == Decimal(0)
    assert result.carryforward_losses == Decimal("500")


def test_savings_base_integrates_the_account_interest() -> None:
    """
    Credit interest is capital income and adds to the same base.

    Art. 25.2 LIRPF makes it a ``rendimiento del capital mobiliario``
    just like a dividend, so leaving it out understated the base.
    """

    result = compute_savings_base(Decimal("100"), Decimal("50"), Decimal("10"))

    assert result.interest_income == Decimal("10")
    assert result.capital_income == Decimal("60")
    assert result.total == Decimal("160")


def test_the_offset_ceiling_counts_interest_as_capital_income() -> None:
    """
    The 25% ceiling is measured over dividends plus interest.

    Art. 49 LIRPF caps the offset at a quarter of the positive balance of
    capital income, not of the dividends alone.
    """

    result = compute_savings_base(Decimal("-500"), Decimal("100"), Decimal("100"))

    assert result.offset_applied == Decimal("50.00")
    assert result.total == Decimal("150.00")
    assert result.carryforward_losses == Decimal("450.00")


def test_only_spanish_withholdings_are_a_payment_on_account() -> None:
    """
    A foreign withholding is not an advance of the Spanish tax.

    Subtracting it from the quota would claim a deduction that art. 80
    LIRPF caps by the treaty rate and by the average effective rate.
    """

    dividends = [
        DividendIncome(
            date=date(2026, 6, 18),
            isin="ES0105777017",
            product="ES",
            gross_eur=Decimal("100"),
            withholding_eur=Decimal("19"),
            domestic=True,
        ),
        DividendIncome(
            date=date(2026, 7, 10),
            isin="US0378331005",
            product="US",
            gross_eur=Decimal("100"),
            withholding_eur=Decimal("15"),
            domestic=False,
        ),
    ]

    assert total_withholdings(dividends) == Decimal("34")
    assert domestic_withholdings(dividends) == Decimal("19")
    assert foreign_withholdings(dividends) == Decimal("15")


def test_dividend_totals() -> None:
    """
    Gross income and withholdings are summed independently.
    """

    dividends = [
        DividendIncome(
            date=date(2026, 6, 18),
            isin="A",
            product="A",
            gross_eur=Decimal("16.02"),
            withholding_eur=Decimal("3.04"),
        ),
        DividendIncome(
            date=date(2026, 7, 10),
            isin="B",
            product="B",
            gross_eur=GROSS,
            withholding_eur=WITHHELD,
        ),
    ]

    assert total_dividend_income(dividends) == Decimal("25.02")
    assert total_withholdings(dividends) == Decimal("4.75")
