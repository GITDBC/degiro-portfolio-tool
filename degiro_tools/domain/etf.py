# Standard libraries
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

# Difference in percentage points below which two published TERs are
# considered the same figure. Both sources publish the TER with two
# decimals, so anything under a hundredth of a point is formatting.
TER_TOLERANCE_PCT: Final[Decimal] = Decimal("0.01")


class Agreement(StrEnum):
    """
    Verdict of comparing one figure across two data sources.

    Attributes:
        MATCH: Both sources publish the figure and they agree.
        MISMATCH: Both publish it and they disagree beyond tolerance.
        ONLY_JUSTETF: Only justETF publishes it.
        ONLY_YAHOO: Only Yahoo Finance publishes it.
        MISSING: Neither source publishes it.
    """

    MATCH = "coincide"
    MISMATCH = "discrepa"
    ONLY_JUSTETF = "solo justETF"
    ONLY_YAHOO = "solo Yahoo"
    MISSING = "sin dato"


@dataclass(frozen=True)
class ETFProfile:
    """
    Cost, return and risk figures of a listed fund, as published.

    Mirrors one row of the justETF screener. Every numeric field is
    optional because young funds have no three or five year history and
    the source reports those cells as a dash; keeping them as ``None``
    avoids passing a zero off as a measurement.

    Percentages are stored as the number the source shows, so ``0.20``
    means 0.20% and not 20%.

    Attributes:
        isin: ISIN identifier of the fund.
        name: Commercial name of the share class.
        ticker: Primary exchange ticker, empty when not published.
        ter_pct: Total expense ratio in percent per year.
        fund_size_meur: Assets under management in millions of EUR.
        return_ytd_pct: Return so far this year, in percent.
        return_1y_pct: Return over the last twelve months, in percent.
        return_3y_pct: Cumulative return over three years, in percent.
        return_5y_pct: Cumulative return over five years, in percent.
        volatility_1y_pct: Annualized volatility over one year.
        volatility_3y_pct: Annualized volatility over three years.
        max_drawdown_3y_pct: Worst peak-to-trough fall over three
            years, as a negative percentage.
        return_per_risk_3y: Return divided by risk over three years.
        replication: Replication method (physical, synthetic, ...).
        distribution_policy: Accumulating or distributing.
        domicile: Country of domicile of the fund.
        securities_lending: Whether the fund lends its securities;
            ``None`` when the source does not state it.
    """

    isin: str
    name: str
    ticker: str
    ter_pct: Decimal | None
    fund_size_meur: Decimal | None
    return_ytd_pct: Decimal | None
    return_1y_pct: Decimal | None
    return_3y_pct: Decimal | None
    return_5y_pct: Decimal | None
    volatility_1y_pct: Decimal | None
    volatility_3y_pct: Decimal | None
    max_drawdown_3y_pct: Decimal | None
    return_per_risk_3y: Decimal | None
    replication: str
    distribution_policy: str
    domicile: str
    securities_lending: bool | None


@dataclass(frozen=True)
class YahooETFData:
    """
    Fund figures as published by Yahoo Finance.

    Deliberately narrow: it carries only what can be compared with
    justETF on equal terms. Yahoo also publishes returns, but in the
    fund's own currency and annualized for the three and five year
    windows, whereas justETF publishes them in EUR and cumulative.
    Those numbers answer different questions, so putting them side by
    side would invent disagreements instead of finding them.

    Attributes:
        isin: ISIN identifier of the fund.
        ticker: Yahoo ticker the data was read from.
        name: Long name of the share class.
        ter_pct: Net expense ratio in percent per year.
        currency: Currency the fund reports in.
    """

    isin: str
    ticker: str
    name: str
    ter_pct: Decimal | None
    currency: str


@dataclass(frozen=True)
class ETFCrossCheck:
    """
    One fund seen through both data sources at once.

    Exists to answer a single question: do the two providers tell the
    same story about this fund? A figure only one of them publishes is
    reported as such rather than as agreement, because a number nobody
    contradicted has not been verified.

    Only the TER is contrasted, because it is the one figure both
    sources define identically. Fund size is not: Yahoo reports the
    assets of the whole fund while justETF reports those of the share
    class, which for a multi-class fund differ by more than a factor of
    two without either being wrong. Returns are not either, being in
    different currencies and cumulative on one side, annualized on the
    other.

    Attributes:
        isin: ISIN identifier of the fund.
        justetf: The justETF profile, or None when its screener does
            not cover the ISIN.
        yahoo: The Yahoo figures, or None when the ticker or its data
            could not be resolved.
    """

    isin: str
    justetf: ETFProfile | None
    yahoo: YahooETFData | None

    @property
    def name(self) -> str:
        """
        Best available commercial name of the fund.

        Returns:
            Name from justETF, falling back to Yahoo, then to the ISIN.
        """

        if self.justetf is not None and self.justetf.name:
            return self.justetf.name

        if self.yahoo is not None and self.yahoo.name:
            return self.yahoo.name

        return self.isin

    @property
    def ter_pct(self) -> Decimal | None:
        """
        The TER to report, taking justETF as the primary source.

        justETF specialises in European listed funds and states the fee
        of the concrete share class, so it wins when both publish one.
        Yahoo is the fallback, which is what covers an ISIN the justETF
        screener does not list.

        Returns:
            The authoritative TER in percent per year, or None when
            neither source publishes it.
        """

        if self.justetf is not None and self.justetf.ter_pct is not None:
            return self.justetf.ter_pct

        if self.yahoo is not None:
            return self.yahoo.ter_pct

        return None

    @property
    def ter_source(self) -> str | None:
        """
        Name of the source the reported TER came from.

        Returns:
            "justETF", "Yahoo Finance", or None when there is no TER.
        """

        if self.justetf is not None and self.justetf.ter_pct is not None:
            return "justETF"

        if self.yahoo is not None and self.yahoo.ter_pct is not None:
            return "Yahoo Finance"

        return None

    @property
    def ter_delta_pct(self) -> Decimal | None:
        """
        Signed gap between the two published TERs.

        Returns:
            justETF TER minus Yahoo TER in percentage points, or None
            when either source lacks the figure.
        """

        if self.justetf is None or self.yahoo is None:
            return None

        if self.justetf.ter_pct is None or self.yahoo.ter_pct is None:
            return None

        return self.justetf.ter_pct - self.yahoo.ter_pct

    @property
    def ter_agreement(self) -> Agreement:
        """
        Verdict of comparing the TER across both sources.

        Returns:
            The Agreement describing whether the two TERs match, differ
            beyond tolerance, or come from a single source only.
        """

        delta = self.ter_delta_pct

        if delta is not None:
            if abs(delta) <= TER_TOLERANCE_PCT:
                return Agreement.MATCH

            return Agreement.MISMATCH

        justetf_ter = None if self.justetf is None else self.justetf.ter_pct
        yahoo_ter = None if self.yahoo is None else self.yahoo.ter_pct

        if justetf_ter is not None:
            return Agreement.ONLY_JUSTETF

        if yahoo_ter is not None:
            return Agreement.ONLY_YAHOO

        return Agreement.MISSING
