# Standard libraries
from decimal import Decimal

# 3pps
import pytest

# Own modules
from degiro_tools import build_report_data, calculate_irpf_quota

# Own modules
from .conftest import make_sale, make_statement

EXPECTED_BRACKETS_10K = 2
EXPECTED_BRACKETS_60K = 3
EXPECTED_BRACKETS_TOP = 5


@pytest.mark.parametrize("total", [Decimal(0), Decimal("-50"), Decimal("-0.01")])
def test_no_gain_returns_empty_list(total: Decimal) -> None:
    """
    Verify a null or negative gain produces an empty breakdown.
    """

    assert calculate_irpf_quota(total) == []


@pytest.mark.parametrize(
    ("total", "n_brackets", "expected_quota", "expected_last_bracket"),
    [
        # exact boundary bracket 1
        pytest.param(
            Decimal("6000"), 1, Decimal("1140.00"), None, id="boundary-bracket-1"
        ),
        # barely crosses to bracket 2 (0.01*0.21 = 0.0021 -> quantize 0.00)
        pytest.param(
            Decimal("6000.01"), 2, Decimal("1140.00"), None, id="crosses-bracket-2"
        ),
        # exact boundary bracket 2 (6000@19 + 44000@21 = 1140 + 9240)
        pytest.param(
            Decimal("50000"), 2, Decimal("10380.00"), None, id="boundary-bracket-2"
        ),
        # exact boundary bracket 3 (+150000@23 = 34500)
        pytest.param(
            Decimal("200000"), 3, Decimal("44880.00"), None, id="boundary-bracket-3"
        ),
        # exact boundary bracket 4 (+100000@27 = 27000)
        pytest.param(
            Decimal("300000"), 4, Decimal("71880.00"), None, id="boundary-bracket-4"
        ),
        # barely crosses to bracket 5 (0.01*0.30 = 0.003 -> quantize 0.00)
        pytest.param(
            Decimal("300000.01"), 5, Decimal("71880.00"), None, id="crosses-bracket-5"
        ),
        # 100 EUR stays in the first bracket: 100 @ 19% = 19.00
        pytest.param(
            Decimal("100"),
            1,
            Decimal("19.00"),
            (Decimal("0.19"), Decimal("100.00")),
            id="single-bracket-100",
        ),
        # 6000 EUR fills the first bracket without opening the second
        pytest.param(
            Decimal("6000"),
            1,
            Decimal("1140.00"),
            (Decimal("0.19"), Decimal("6000.00")),
            id="first-bracket-limit-6000",
        ),
        # 6000 @ 19% = 1140 + 4000 @ 21% = 840 -> 1980
        pytest.param(
            Decimal("10000"),
            EXPECTED_BRACKETS_10K,
            Decimal("1980.00"),
            (Decimal("0.21"), Decimal("4000.00")),
            id="two-brackets-10000",
        ),
        # 6000@19 + 44000@21 + 10000@23 = 1140 + 9240 + 2300 = 12680
        pytest.param(
            Decimal("60000"),
            EXPECTED_BRACKETS_60K,
            Decimal("12680.00"),
            None,
            id="three-brackets-60000",
        ),
    ],
)
def test_irpf_bracket_boundaries(
    total: Decimal,
    n_brackets: int,
    expected_quota: Decimal,
    expected_last_bracket: tuple[Decimal, Decimal] | None,
) -> None:
    """
    Verify bracket count and total quota at each savings-bracket
    boundary.

    Args:
        total: Savings base to break down.
        n_brackets: Number of brackets expected to be active.
        expected_quota: Total quota expected over every bracket.
        expected_last_bracket: Rate and base expected on the last
            active bracket, when the case pins them down.
    """

    breakdown = calculate_irpf_quota(total)

    assert len(breakdown) == n_brackets
    assert sum(bracket.quota for bracket in breakdown) == expected_quota

    if expected_last_bracket is not None:
        expected_rate, expected_base = expected_last_bracket
        assert breakdown[-1].rate == expected_rate
        assert breakdown[-1].base == expected_base


def test_last_bracket_without_upper() -> None:
    """
    Verify the last active bracket (>300,000) leaves ``upper=None``.
    """

    breakdown = calculate_irpf_quota(Decimal("400000"))

    assert breakdown[-1].upper is None
    assert breakdown[-1].rate == Decimal("0.30")


def test_top_bracket_quota_400000() -> None:
    """
    Verify the full quota when the top 30% bracket is active.
    """

    # 71,880 (first four brackets) + 100,000 @ 30% = 101,880.00.
    breakdown = calculate_irpf_quota(Decimal("400000"))

    assert len(breakdown) == EXPECTED_BRACKETS_TOP
    assert sum(bracket.quota for bracket in breakdown) == Decimal("101880.00")


def test_negative_total_yields_no_quota_or_net_return() -> None:
    """
    Verify a net loss produces no IRPF quota and no net return.
    """

    sales = [
        make_sale(
            2026,
            "-50.00",
            month=3,
            day=1,
            isin="IE0000000099",
            product="TEST",
            quantity=10,
            acquisition_cost="200.00",
        )
    ]

    report = build_report_data(sales, {}, make_statement(), include_tax=True)

    assert report.irpf_quota is None
    assert report.net_return is None
