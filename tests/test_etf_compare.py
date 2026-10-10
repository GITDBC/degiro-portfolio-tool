# Standard libraries
from decimal import Decimal

# Own modules
from degiro_tools.calculation.etf_compare import build_crosschecks
from degiro_tools.domain.etf import (
    Agreement,
    ETFCrossCheck,
    ETFProfile,
    YahooETFData,
)


def profile(isin: str, ter: str | None) -> ETFProfile:
    """
    Build a justETF profile carrying only the TER under test.

    Args:
        isin: ISIN identifier.
        ter: TER as a decimal string, or None when unpublished.

    Returns:
        ETFProfile with everything else left empty.
    """

    return ETFProfile(
        isin=isin,
        name=f"Fondo {isin}",
        ticker="TCK",
        ter_pct=None if ter is None else Decimal(ter),
        fund_size_meur=None,
        return_ytd_pct=None,
        return_1y_pct=None,
        return_3y_pct=None,
        return_5y_pct=None,
        volatility_1y_pct=None,
        volatility_3y_pct=None,
        max_drawdown_3y_pct=None,
        return_per_risk_3y=None,
        replication="",
        distribution_policy="",
        domicile="",
        securities_lending=None,
    )


def yahoo(isin: str, ter: str | None, name: str = "") -> YahooETFData:
    """
    Build a Yahoo row carrying only the TER under test.

    Args:
        isin: ISIN identifier.
        ter: TER as a decimal string, or None when unpublished.
        name: Optional long name.

    Returns:
        YahooETFData for the cross-check.
    """

    return YahooETFData(
        isin=isin,
        ticker="TCK.L",
        name=name,
        ter_pct=None if ter is None else Decimal(ter),
        currency="USD",
    )


def test_identical_ter_is_confirmed() -> None:
    """
    Two sources publishing the same fee confirm each other.
    """

    check = ETFCrossCheck("X", profile("X", "0.20"), yahoo("X", "0.20"))

    assert check.ter_agreement is Agreement.MATCH
    assert check.ter_delta_pct == Decimal("0.00")


def test_trailing_zero_is_not_a_discrepancy() -> None:
    """
    justETF prints 0.20 where Yahoo returns 0.2; same number.

    This is the real shape of the data, so treating it as a mismatch
    would flag every single fund.
    """

    check = ETFCrossCheck("X", profile("X", "0.20"), yahoo("X", "0.2"))

    assert check.ter_agreement is Agreement.MATCH


def test_real_difference_is_flagged() -> None:
    """
    A gap beyond a hundredth of a point is reported as a mismatch.
    """

    check = ETFCrossCheck("X", profile("X", "0.25"), yahoo("X", "0.30"))

    assert check.ter_agreement is Agreement.MISMATCH
    assert check.ter_delta_pct == Decimal("-0.05")


def test_single_source_is_not_confirmation() -> None:
    """
    A figure nobody contradicted has not been verified.

    Reporting it as agreement would claim a check that never happened.
    """

    only_justetf = ETFCrossCheck("X", profile("X", "0.10"), yahoo("X", None))
    only_yahoo = ETFCrossCheck("X", profile("X", None), yahoo("X", "0.07"))
    absent_yahoo = ETFCrossCheck("X", profile("X", "0.10"), None)
    nothing = ETFCrossCheck("X", None, None)

    assert only_justetf.ter_agreement is Agreement.ONLY_JUSTETF
    assert only_yahoo.ter_agreement is Agreement.ONLY_YAHOO
    assert absent_yahoo.ter_agreement is Agreement.ONLY_JUSTETF
    assert nothing.ter_agreement is Agreement.MISSING

    assert only_justetf.ter_delta_pct is None
    assert nothing.ter_delta_pct is None


def test_name_falls_back_across_sources() -> None:
    """
    The fund is named from whichever source knows it.
    """

    assert ETFCrossCheck("X", profile("X", "0.1"), None).name == "Fondo X"
    assert ETFCrossCheck("X", None, yahoo("X", "0.1", "Yahoo Name")).name == (
        "Yahoo Name"
    )
    assert ETFCrossCheck("X", None, None).name == "X"


def test_build_crosschecks_keeps_requested_order() -> None:
    """
    Rows follow the requested ISINs, not the provider's ordering.
    """

    isins = ["A", "B", "C"]
    profiles = [profile("C", "0.3"), profile("A", "0.1")]
    rows = [yahoo("B", "0.2")]

    checks = build_crosschecks(isins, profiles, rows)

    assert [check.isin for check in checks] == isins
    assert checks[0].justetf is not None
    assert checks[0].yahoo is None
    assert checks[1].justetf is None
    assert checks[1].yahoo is not None


def test_isin_unknown_to_both_sources_still_appears() -> None:
    """
    A fund neither source covers stays visible as an empty row.

    Dropping it would turn a gap in the data into a gap in the
    portfolio.
    """

    checks = build_crosschecks(["GHOST"], [], [])

    assert len(checks) == 1
    assert checks[0].justetf is None
    assert checks[0].yahoo is None
    assert checks[0].ter_agreement is Agreement.MISSING


def test_justetf_is_the_primary_source_for_the_reported_ter() -> None:
    """
    With both sources present, the figure shown is justETF's.

    justETF states the fee of the concrete share class, which is the
    one you actually pay.
    """

    check = ETFCrossCheck("X", profile("X", "0.20"), yahoo("X", "0.30"))

    assert check.ter_pct == Decimal("0.20")
    assert check.ter_source == "justETF"
    assert check.ter_agreement is Agreement.MISMATCH


def test_yahoo_is_the_fallback_when_justetf_has_no_ter() -> None:
    """
    An ISIN the screener does not cover still reports a fee.

    This is what keeps individual stocks and retired classes from
    showing an empty row.
    """

    absent = ETFCrossCheck("X", None, yahoo("X", "0.07"))
    no_figure = ETFCrossCheck("X", profile("X", None), yahoo("X", "0.07"))

    assert absent.ter_pct == Decimal("0.07")
    assert absent.ter_source == "Yahoo Finance"
    assert no_figure.ter_pct == Decimal("0.07")
    assert no_figure.ter_source == "Yahoo Finance"


def test_no_source_reports_no_ter_and_no_origin() -> None:
    """
    Nothing published means None, never a zero.
    """

    check = ETFCrossCheck("X", None, None)

    assert check.ter_pct is None
    assert check.ter_source is None
