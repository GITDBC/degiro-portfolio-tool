# Standard libraries
from collections import deque
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum


class OperationType(StrEnum):
    """
    Stock operation type recognized by the parser.

    Inherits from ``str`` to keep compatibility with direct
    comparisons against the ``"Compra"``/``"Venta"`` literals used by
    existing renderers and by the Degiro XLSX statement data.
    """

    BUY = "Compra"
    SELL = "Venta"


class StatementRowKind(StrEnum):
    """
    What a row of the Degiro account statement represents.

    Every row is classified into one of these, so a description the
    parser does not recognize lands in ``UNKNOWN`` and is reported
    instead of dropped in silence. A statement exported in another
    language, or a movement type Degiro adds later, then shows up as a
    warning rather than as a report that quietly declares less.
    """

    OPERATION = "operation"
    FEE = "fee"
    CONNECTIVITY_FEE = "connectivity_fee"
    CURRENCY_EXCHANGE = "currency_exchange"
    DIVIDEND = "dividend"
    DIVIDEND_WITHHOLDING = "dividend_withholding"
    INTEREST = "interest"
    OTHER_INCOME = "other_income"
    CASH_MOVEMENT = "cash_movement"
    UNKNOWN = "unknown"


class PriceUnavailable(StrEnum):
    """
    Why a position could not be valued in EUR.

    A position with no EUR value is still a position the account holds,
    so the reason it could not be valued travels with it instead of the
    row being dropped.
    """

    NO_TICKER = "no_ticker"
    NO_QUOTE = "no_quote"
    NO_EXCHANGE_RATE = "no_exchange_rate"


class PriceSource(StrEnum):
    """
    Provider a price was actually read from.

    Two providers answer for the same position and they do not fail
    together, so which one produced a given row is a fact the report
    can state. The values are the names a reader recognizes, since they
    are printed verbatim under the valuation table.
    """

    JUSTETF = "justETF"
    YAHOO = "Yahoo"


@dataclass(frozen=True)
class ExchangeRate:
    """
    Conversion rate together with where and when it comes from.

    A rate without provenance cannot be audited: the same number may be
    a live quote or a reference fixing published two days ago, and every
    valuation in the report depends on which. Keeping the source and the
    date beside the figure makes that answerable.

    Attributes:
        rate: Units of the target currency per unit of the source
            currency, so a USD price is multiplied by it to obtain EUR.
        source: Identifier of the provider the rate was read from.
        as_of: Date the rate belongs to, or None when the source
            publishes no date for it.
    """

    rate: float
    source: str
    as_of: date | None = None


@dataclass(frozen=True)
class InflationIndex:
    """
    Consumer price index series used to express money in one month's euros.

    A euro paid in 2019 and a euro paid today are different amounts of
    purchasing power, so a nominal return over several years compares
    quantities that are not the same unit. This carries the published
    index needed to restate every one of them in the euros of a single
    reference month, together with which month that is: the figure is
    only auditable if the report can say what it deflated to.

    Attributes:
        region: Region code the series was requested for.
        region_label: Name of that region as the report prints it.
        reference_period: Last published month, as ``YYYY-MM``. Every
            factor carries money to the euros of this month.
        values: Index value per monthly period, keyed ``YYYY-MM``.
    """

    region: str
    region_label: str
    reference_period: str
    values: dict[str, float]

    @staticmethod
    def period_of(day: date) -> str:
        """
        Monthly period a date belongs to.

        Built from the date rather than by parsing a period back into
        one, because the two are not interchangeable: the SDMX periods
        are ``YYYY-MM`` and ``date.fromisoformat`` rejects that shape.
        Going date to period, never the reverse, keeps the conversion
        one-way and total, and the zero padding is what makes the result
        match the keys the endpoint publishes.

        Args:
            day: Date to locate.

        Returns:
            The period as ``YYYY-MM``.
        """

        return f"{day.year:04d}-{day.month:02d}"

    @property
    def reference_value(self) -> float:
        """
        Index value of the reference month.

        Returns:
            The published index for ``reference_period``.
        """

        return self.values[self.reference_period]

    def is_after_reference(self, day: date) -> bool:
        """
        Whether a date falls past the last published month.

        The index is published about six weeks late, so recent
        purchases genuinely have no month of their own yet.

        Args:
            day: Date to test.

        Returns:
            True when the date's month is newer than the reference one.
        """

        return self.period_of(day) > self.reference_period

    def factor_for(self, day: date) -> Decimal | None:
        """
        Deflator carrying money paid on a date into reference euros.

        A date past the last published month carries the last index
        forward, which gives a factor of exactly one. That is not an
        extrapolation of future inflation, it is declining to invent
        one, and the report says so beside the figure.

        Args:
            day: Date the money was paid on.

        Returns:
            Multiplier taking that amount to the euros of the reference
            month, or None when the series covers no such month, which
            is the case for a purchase older than the series itself.
        """

        if self.is_after_reference(day):
            return Decimal(1)

        published = self.values.get(self.period_of(day))

        if published is None or published <= 0:
            return None

        return Decimal(str(self.reference_value)) / Decimal(str(published))


@dataclass(frozen=True)
class PriceQuote:
    """
    Last price of an instrument, in EUR when that could be resolved.

    Carries the native price and its currency even when the EUR
    conversion failed, because the quantity and the price the market
    actually quotes are real data the report can still show. Only the
    euro figure is missing, and ``unavailable`` says why.

    Attributes:
        isin: ISIN code of the instrument.
        price_eur: Last price converted to EUR, or None when it could
            not be resolved.
        native_price: Last price in the currency the market quotes it
            in, or None when no quote was retrieved at all.
        currency: Currency code of ``native_price``, empty when no
            quote was retrieved.
        unavailable: Reason the EUR price is missing, or None when it
            is present.
        source: Provider the price was read from, or None when no quote
            was obtained at all.
    """

    isin: str
    price_eur: float | None
    native_price: float | None
    currency: str
    unavailable: PriceUnavailable | None
    source: PriceSource | None = None


@dataclass(frozen=True)
class WeightBase:
    """
    What the portfolio weights are actually a percentage of.

    Weights are computed over the positions that could be priced. When
    any position could not, that base is no longer the portfolio, and
    every table built on it is describing something narrower than it
    appears to. This value carries the facts needed to say so next to
    the numbers themselves.

    Attributes:
        priced_count: Positions with a EUR value, which form the base.
        unpriced_count: Positions left outside the base for want of a
            EUR value.
        unpriced_isins: ISINs of those positions, in table order.
        market_value_eur: Market value of the base, in EUR.
    """

    priced_count: int
    unpriced_count: int
    unpriced_isins: tuple[str, ...]
    market_value_eur: float

    @property
    def position_count(self) -> int:
        """
        Positions the account holds, priced or not.

        Returns:
            Total number of open positions.
        """

        return self.priced_count + self.unpriced_count

    @property
    def is_whole_portfolio(self) -> bool:
        """
        Whether the base covers every position the account holds.

        Returns:
            True when no position was left unpriced, so the weights are
            over the whole portfolio and need no caveat.
        """

        return self.unpriced_count == 0

    @property
    def priced_share_pct(self) -> float | None:
        """
        Share of the open positions that made it into the base.

        Counted in positions, not in euro: the value of an unpriced
        position is the very datum that is missing, so the euro share it
        represents cannot be computed without inventing it.

        Returns:
            Percentage of positions inside the base, or None when the
            account holds no position at all.
        """

        if self.position_count == 0:
            return None

        return self.priced_count / self.position_count * 100.0


@dataclass(frozen=True)
class Operation:
    """
    Normalized buy or sell operation in the broker's real EUR.

    Attributes:
        date: Operation date.
        time: Time in ``HH:MM`` format.
        isin: ISIN identifier of the instrument.
        product: Descriptive product name.
        operation_type: ``OperationType.BUY`` or ``OperationType.SELL``.
        quantity: Number of units.
        amount_eur: Consideration in EUR (sign included).
        fee_eur: Fees attributable to the operation (sign included).
    """

    date: date
    time: str
    isin: str
    product: str
    operation_type: OperationType
    quantity: int
    amount_eur: Decimal
    fee_eur: Decimal


@dataclass(frozen=True)
class AcquisitionMatch:
    """
    Portion of a sale imputed to a single acquisition date.

    The Spanish return asks for the acquisition date and value of every
    transferred element, so a sale that consumes lots bought on
    different days needs one declared line per acquisition date even
    though the broker executed a single order.

    Attributes:
        acquisition_date: Date of the purchase that supplied the units.
        quantity: Units taken from that acquisition date.
        acquisition_cost: Acquisition value of those units in EUR,
            fees included (art. 35.1.b LIRPF).
        transfer_value: Share of the transfer value attributed to those
            units, net of the sale fee (art. 35.2 LIRPF).
    """

    acquisition_date: date
    quantity: int
    acquisition_cost: Decimal
    transfer_value: Decimal

    @property
    def gain_loss(self) -> Decimal:
        """
        Gain or loss attributable to this acquisition date.

        Returns:
            Transfer value minus acquisition cost, in EUR.
        """

        return self.transfer_value - self.acquisition_cost


@dataclass(frozen=True)
class Sale:
    """
    Sale matched against FIFO lots with its acquisition cost.

    Attributes:
        date: Sale date.
        isin: ISIN identifier of the instrument.
        product: Descriptive product name.
        quantity: Units sold.
        acquisition_cost: FIFO acquisition cost imputed in EUR.
        transfer_value: Transfer value net of fees in EUR.
        gain_loss: Capital gain or loss
            (``transfer_value - acquisition_cost``).
        matches: Breakdown per acquisition date, in FIFO order. Empty
            when the caller builds the sale without lot detail.
    """

    date: date
    isin: str
    product: str
    quantity: int
    acquisition_cost: Decimal
    transfer_value: Decimal
    gain_loss: Decimal
    matches: tuple[AcquisitionMatch, ...] = ()


@dataclass(frozen=True)
class DividendIncome:
    """
    Dividend collected on a position, with its withholding at source.

    Dividends are ``rendimientos del capital mobiliario`` and feed the
    same savings base as capital gains (art. 49 LIRPF). Where the
    withholding was practised decides what can be done with it: a
    Spanish one is a 19% payment on account that subtracts from the
    quota, while a foreign one is not, and only gives the right to the
    double-taxation deduction of art. 80 LIRPF, capped by the treaty
    and by the average effective rate. Both are tracked apart from the
    gross amount and from each other.

    Attributes:
        date: Payment date.
        isin: ISIN identifier of the paying instrument.
        product: Descriptive product name.
        gross_eur: Gross dividend in EUR before withholding.
        withholding_eur: Withholding practised, as a positive amount.
        domestic: Whether the withholding is Spanish, and therefore a
            payment on account. Defaults to ``True``.
    """

    date: date
    isin: str
    product: str
    gross_eur: Decimal
    withholding_eur: Decimal
    domestic: bool = True

    @property
    def net_eur(self) -> Decimal:
        """
        Amount actually credited to the account.

        Returns:
            Gross dividend minus the withholding, in EUR.
        """

        return self.gross_eur - self.withholding_eur


@dataclass(frozen=True)
class CurrencyConversion:
    """
    Currency exchange with no trade of its own to be folded into.

    A conversion between euros and another currency alters the
    composition of the patrimony, so it is a capital gain or loss in its
    own right (art. 37.1.h LIRPF, ``casilla 1634``). When Degiro
    converts as part of a purchase or a sale, the result is already
    inside the acquisition and transfer values, which are taken from the
    euro leg the broker actually moved. A conversion that pairs with no
    trade has nowhere to hide, so it travels on its own to be reported.

    Attributes:
        date: Date of the exchange.
        time: Time in ``HH:MM`` format.
        eur_amount: Signed euro leg of the exchange.
        foreign_amount: Signed leg in the other currency.
        foreign_currency: Currency code of the non-euro leg.
    """

    date: date
    time: str
    eur_amount: Decimal
    foreign_amount: Decimal
    foreign_currency: str


@dataclass(frozen=True)
class OtherIncome:
    """
    Amount credited that is neither a dividend nor a transmission.

    Promotions, rebates and refunds reach the cash balance without
    being a return on an instrument or the sale of one. Their
    qualification depends on what the broker paid them for, so they are
    reported verbatim instead of being integrated into a base they may
    not belong to.

    Attributes:
        date: Date the amount was credited.
        description: Statement description, verbatim.
        amount_eur: Signed amount in EUR.
    """

    date: date
    description: str
    amount_eur: Decimal


@dataclass(frozen=True)
class UnvaluedOperation:
    """
    Trade the statement gives no way to express in euros.

    Happens on a Manual FX account: the order is settled against a
    balance already held in the foreign currency, so no
    ``Cambio de Divisa`` row accompanies it and the statement never
    states what the trade cost in euros. Guessing a rate would put an
    invented number in a tax report, so the whole ISIN is left out and
    listed here.

    Attributes:
        date: Operation date.
        isin: ISIN identifier of the instrument.
        product: Descriptive product name.
        description: Statement description, verbatim.
        amount: Signed amount in the currency of the trade.
        currency: Currency code of that amount.
    """

    date: date
    isin: str
    product: str
    description: str
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class UnclassifiedRow:
    """
    Statement row whose description the parser does not recognize.

    Attributes:
        date: Row date as written in the statement.
        description: Statement description, verbatim.
        amount: Signed amount as read.
        currency: Currency code of that amount, empty when absent.
    """

    date: str
    description: str
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class WashSaleAlert:
    """
    Loss that art. 33.5.f LIRPF may keep out of this year's base.

    A loss on listed securities is not integrated when homogeneous
    securities were acquired within the two months before or after the
    transmission: it is deferred until they are transmitted for good.
    Whether it applies depends on facts a statement cannot settle
    (homogeneity across brokers, what is still held), so the affected
    amount is reported and left inside the base rather than removed on
    the tool's own authority.

    Attributes:
        transfer_date: Date of the transmission that closed in loss.
        acquisition_date: Acquisition date of the transferred units.
        isin: ISIN identifier of the instrument.
        product: Descriptive product name.
        quantity: Units the loss belongs to.
        loss_eur: The loss itself, as a negative amount in EUR.
        repurchase_dates: Dates of the purchases found inside the
            window, excluding the acquisition the loss comes from.
        position_closed: Whether the ISIN was left with no open lots at
            the end of the statement, in which case the deferred loss
            surfaces in the same year.
    """

    transfer_date: date
    acquisition_date: date
    isin: str
    product: str
    quantity: int
    loss_eur: Decimal
    repurchase_dates: tuple[date, ...]
    position_closed: bool


@dataclass
class Lot:
    """
    Purchase lot pending consumption by FIFO.

    Mutable: the quantity is decremented as sales consume it.

    Attributes:
        quantity: Units remaining in the lot.
        unit_cost: Unit cost in EUR (includes the purchase fee).
        date: Date of the original purchase.
    """

    quantity: int
    unit_cost: Decimal
    date: date


@dataclass(frozen=True)
class TaxBracket:
    """
    Savings-base bracket (art. 66 LIRPF) with its base and quota.

    Attributes:
        lower: Lower bound of the bracket in EUR.
        upper: Upper bound of the bracket in EUR, or ``None`` if last.
        rate: Applied tax rate (e.g. ``0.19``).
        base: Taxable base covered by this bracket in EUR.
        quota: Resulting quota (``base * rate``) in EUR.
    """

    lower: Decimal
    upper: Decimal | None
    rate: Decimal
    base: Decimal
    quota: Decimal


@dataclass(frozen=True)
class AccountStatement:
    """
    Everything the fiscal report needs from a Degiro account export.

    Attributes:
        operations: Normalized buy/sell operations, chronologically
            sorted. Excludes every ISIN that could not be valued in
            euros, so a FIFO chain is never built on a partial history.
        dividends: Dividends collected, with their withholdings.
        connectivity_fees: Absolute total of market connectivity fees
            in EUR.
        interest_income: Credit interest collected, in EUR.
        interest_expense: Absolute total of debit interest charged, in
            EUR.
        other_income: Promotions, rebates and refunds credited.
        currency_conversions: Currency exchanges that pair with no
            trade.
        unvalued_operations: Trades with no euro consideration in the
            statement, whose ISIN was left out of ``operations``.
        unclassified_rows: Rows whose description the parser does not
            recognize.
    """

    operations: list[Operation]
    dividends: list[DividendIncome]
    connectivity_fees: Decimal
    interest_income: Decimal = Decimal(0)
    interest_expense: Decimal = Decimal(0)
    other_income: tuple[OtherIncome, ...] = ()
    currency_conversions: tuple[CurrencyConversion, ...] = ()
    unvalued_operations: tuple[UnvaluedOperation, ...] = ()
    unclassified_rows: tuple[UnclassifiedRow, ...] = ()


@dataclass(frozen=True)
class SavingsBase:
    """
    Savings-base integration of capital income and capital gains.

    Art. 49 LIRPF integrates both balances separately: when the capital
    gains balance is negative it may offset the positive balance of
    ``rendimientos del capital mobiliario`` only up to 25% of it, and the
    remainder carries forward to the next four years. Dividends and
    credit interest are both such returns, so the 25% is measured over
    their sum rather than over the dividends alone.

    Attributes:
        capital_gains: Net balance of capital gains/losses in EUR
            (negative when the year closes at a loss).
        dividend_income: Gross dividend income in EUR.
        interest_income: Credit interest collected in EUR.
        offset_applied: Losses actually offset against capital income.
        carryforward_losses: Losses left to carry to future years.
        total: Resulting taxable savings base in EUR.
    """

    capital_gains: Decimal
    dividend_income: Decimal
    offset_applied: Decimal
    carryforward_losses: Decimal
    total: Decimal
    interest_income: Decimal = Decimal(0)

    @property
    def capital_income(self) -> Decimal:
        """
        Positive balance of ``rendimientos del capital mobiliario``.

        Returns:
            Gross dividends plus credit interest, in EUR.
        """

        return self.dividend_income + self.interest_income


@dataclass(frozen=True)
class YearlyResult:
    """
    Realized result of a single fiscal year, with its running total.

    Capital gains and dividends are reported per year because IRPF is
    settled per year, and cumulatively because the running total is
    what tells the investor how the account has done since it was
    opened. Losses pending offset are not tracked here: they depend on
    the four-year carryforward window (art. 49 LIRPF) and on balances
    outside this statement.

    Attributes:
        year: Calendar year of the operations.
        gain_loss: Net capital gains/losses realized that year in EUR.
        dividend_income: Gross dividends collected that year in EUR.
        withholdings: Withholdings practised that year, as a positive
            amount in EUR.
        sale_count: Number of sales matched that year.
        cumulative_gain_loss: Capital gains/losses accumulated from the
            first year up to and including this one.
        cumulative_dividend_income: Gross dividends accumulated from
            the first year up to and including this one.
    """

    year: int
    gain_loss: Decimal
    dividend_income: Decimal
    withholdings: Decimal
    sale_count: int
    cumulative_gain_loss: Decimal
    cumulative_dividend_income: Decimal

    @property
    def total(self) -> Decimal:
        """
        Realized result of the year before taxes.

        Returns:
            Capital gains plus gross dividends of the year, in EUR.
        """

        return self.gain_loss + self.dividend_income

    @property
    def cumulative_total(self) -> Decimal:
        """
        Realized result accumulated up to and including this year.

        Returns:
            Accumulated capital gains plus accumulated gross dividends,
            in EUR.
        """

        return self.cumulative_gain_loss + self.cumulative_dividend_income


@dataclass(frozen=True)
class MarketSnapshot:
    """
    Prices of the open portfolio together with their valuation date.

    A price is only meaningful as of a date: the same quote read a month
    later would report a different holding period and a different
    annual return. Keeping both in one value makes the valuation date an
    explicit input instead of a reading of the machine's clock.

    Attributes:
        prices: Current unit price in EUR per ISIN.
        as_of: Valuation date the prices belong to. Defaults to the day
            the snapshot is built, which is when live prices are read;
            on a closed market that is the day of the query and not the
            session the quote closed on.
    """

    prices: dict[str, Decimal]
    as_of: date = field(default_factory=date.today)


@dataclass(frozen=True)
class PositionPerformance:
    """
    Open position valued against its FIFO acquisition cost.

    Holds the unrealized result: what the remaining lots cost and what
    they are worth now. Nothing here is taxable, precisely because the
    position has not been transmitted (art. 33.1 LIRPF); it is reported
    apart from the realized result for that reason.

    Attributes:
        isin: ISIN identifier of the instrument.
        product: Descriptive product name.
        quantity: Units held.
        cost_total: Acquisition cost of the remaining lots in EUR,
            purchase fees included.
        market_value: Current market value of those units in EUR.
        holding_days: Cost-weighted average holding period in days,
            which is what makes the annualized figure interpretable.
        annualized_pct: Money-weighted annual return (IRR) over the
            purchase dates, or ``None`` when the position is too young
            for annualizing to mean anything.
    """

    isin: str
    product: str
    quantity: int
    cost_total: Decimal
    market_value: Decimal
    holding_days: int
    annualized_pct: Decimal | None

    @property
    def unit_cost(self) -> Decimal:
        """
        Average acquisition cost per unit.

        Returns:
            Cost divided by quantity in EUR, or zero when there are no
            units.
        """

        if self.quantity == 0:
            return Decimal(0)

        return self.cost_total / self.quantity

    @property
    def gain_loss(self) -> Decimal:
        """
        Unrealized gain or loss of the position.

        Returns:
            Market value minus acquisition cost, in EUR.
        """

        return self.market_value - self.cost_total

    @property
    def gain_loss_pct(self) -> Decimal:
        """
        Unrealized gain or loss as a percentage of the cost.

        Returns:
            Percentage over the acquisition cost, or zero when the cost
            is zero.
        """

        if self.cost_total == 0:
            return Decimal(0)

        return self.gain_loss / self.cost_total * 100


@dataclass(frozen=True)
class PerformanceSummary:
    """
    Realized and unrealized result of the account side by side.

    Separates what has already been cashed in, and therefore taxed,
    from what is still on paper, so neither figure is mistaken for the
    other.

    Attributes:
        positions: Open positions with their unrealized result.
        realized_gain_loss: Net capital gains already realized in EUR.
        dividend_income: Gross dividends collected in EUR.
        priced_isins: ISINs whose current price could be obtained.
        unpriced_isins: Open ISINs left out for want of a price, so a
            missing position is never read as a position at zero.
    """

    positions: list[PositionPerformance]
    realized_gain_loss: Decimal
    dividend_income: Decimal
    priced_isins: tuple[str, ...] = ()
    unpriced_isins: tuple[str, ...] = ()

    @property
    def cost_total(self) -> Decimal:
        """
        Acquisition cost of every open position.

        Returns:
            Sum of the position costs in EUR.
        """

        return sum((position.cost_total for position in self.positions), Decimal(0))

    @property
    def market_value(self) -> Decimal:
        """
        Current market value of every open position.

        Returns:
            Sum of the position market values in EUR.
        """

        return sum((position.market_value for position in self.positions), Decimal(0))

    @property
    def unrealized_gain_loss(self) -> Decimal:
        """
        Unrealized result of the whole open portfolio.

        Returns:
            Market value minus acquisition cost, in EUR.
        """

        return self.market_value - self.cost_total

    @property
    def unrealized_pct(self) -> Decimal:
        """
        Unrealized result as a percentage of the cost.

        Returns:
            Percentage over the acquisition cost, or zero when the cost
            is zero.
        """

        if self.cost_total == 0:
            return Decimal(0)

        return self.unrealized_gain_loss / self.cost_total * 100

    @property
    def total_result(self) -> Decimal:
        """
        Everything the account has produced so far.

        Returns:
            Unrealized result plus realized gains plus gross dividends,
            in EUR.
        """

        return (
            self.unrealized_gain_loss + self.realized_gain_loss + self.dividend_income
        )


@dataclass(frozen=True)
class RealPositionReturn:
    """
    Open position valued against its cost restated in today's euros.

    Attributes:
        isin: ISIN identifier of the instrument.
        cost_real: Acquisition cost of the remaining lots expressed in
            the euros of the index reference month.
        market_value: Current market value of those units in EUR.
        annualized_pct: Money-weighted annual return over the deflated
            purchases, or ``None`` when the position is too young for
            annualizing to mean anything.
        carried_forward: Whether any of its purchases fell past the last
            published index month and was therefore deflated by one.
    """

    isin: str
    cost_real: Decimal
    market_value: Decimal
    annualized_pct: Decimal | None
    carried_forward: bool = False

    @property
    def gain_loss(self) -> Decimal:
        """
        Unrealized gain or loss net of inflation.

        Returns:
            Market value minus the cost restated in today's euros.
        """

        return self.market_value - self.cost_real


@dataclass(frozen=True)
class RealReturnSummary:
    """
    Result of the open portfolio once inflation is taken out.

    The nominal figure answers how many euros the account has gained;
    this one answers whether those euros buy more than the ones that
    went in, which is the only version of the question an investor
    actually cares about. Both are reported, never one instead of the
    other.

    Attributes:
        positions: Per-position real result, in the order the nominal
            table shows them.
        region: Region code of the index used.
        region_label: Name of that region as the report prints it.
        reference_period: Month the costs were restated to, ``YYYY-MM``.
        uncovered_isins: Open, priced ISINs the index could not cover,
            whose cost is therefore absent from the real total.
    """

    positions: tuple[RealPositionReturn, ...]
    region: str
    region_label: str
    reference_period: str
    uncovered_isins: tuple[str, ...] = ()

    @property
    def cost_total(self) -> Decimal:
        """
        Cost of every covered position, in reference-month euros.

        Returns:
            Sum of the restated costs in EUR.
        """

        return sum((position.cost_real for position in self.positions), Decimal(0))

    @property
    def market_value(self) -> Decimal:
        """
        Market value of the positions inside the real total.

        Reported apart from the nominal market value on purpose: when a
        position has no index the two bases differ, and dividing one by
        the other would state a return over a base it does not belong
        to.

        Returns:
            Sum of the covered market values in EUR.
        """

        return sum((position.market_value for position in self.positions), Decimal(0))

    @property
    def gain_loss(self) -> Decimal:
        """
        Real unrealized result of the covered positions.

        Returns:
            Market value minus restated cost, in EUR.
        """

        return self.market_value - self.cost_total

    @property
    def gain_loss_pct(self) -> Decimal:
        """
        Real unrealized result as a percentage of the restated cost.

        Returns:
            Percentage over the restated cost, or zero when there is no
            cost to divide by.
        """

        if self.cost_total == 0:
            return Decimal(0)

        return self.gain_loss / self.cost_total * 100

    @property
    def is_complete(self) -> bool:
        """
        Whether every priced position made it into the real total.

        Returns:
            True when no position was left out for want of an index.
        """

        return not self.uncovered_isins

    @property
    def carried_forward(self) -> bool:
        """
        Whether any purchase was deflated by a carried-forward index.

        Returns:
            True when at least one purchase is newer than the last
            published month, which the report discloses.
        """

        return any(position.carried_forward for position in self.positions)

    def annualized_for(self, isin: str) -> Decimal | None:
        """
        Real annual return of one position, for the nominal table.

        Args:
            isin: ISIN identifier to look up.

        Returns:
            The real annualized percentage, or None when the position
            has none or is not covered.
        """

        for position in self.positions:
            if position.isin == isin:
                return position.annualized_pct

        return None


@dataclass(frozen=True)
class ReportData:
    """
    Precomputed report aggregates, decoupled from the output format.

    Holds all the information required to render the report in any
    format (stdout, CSV, Excel) without recomputing.

    Attributes:
        sales: Sales matched by FIFO.
        total_gain_loss: Sum of capital gain/loss for the period.
        dividends: Dividends collected during the period.
        savings_base: Integration of capital income and capital gains.
        domestic_withholdings: Spanish withholdings, which are a
            payment on account and subtract from the quota. Deliberately
            not named after the total: ``savings_base`` exposes a
            ``total_withholdings`` function that sums every withholding
            regardless of origin, and only the Spanish part is an
            advance of this same tax.
        irpf_quota: Bracket breakdown over the savings base, or
            ``None`` if ``include_tax`` is ``False`` or the base is not
            positive.
        quota_to_pay: Quota left to pay after subtracting the
            withholdings already practised; negative means a refund.
            ``None`` when ``irpf_quota`` is ``None``.
        connectivity_fees: Absolute total of connectivity fees.
        net_return: Return after taxes and custody fees, or ``None``
            when not applicable.
        pending_lots: Open portfolio after applying FIFO.
        yearly_results: Realized result per year with running totals,
            oldest year first.
        include_tax: Whether the report must render the IRPF block.
        foreign_withholdings: Withholdings practised abroad, which are
            not a payment on account and only give the right to the
            deduction of art. 80 LIRPF.
        interest_income: Credit interest integrated into the base.
        interest_expense: Absolute total of debit interest charged.
        other_income: Promotions and rebates, reported but not
            integrated.
        currency_conversions: Currency exchanges with no trade attached.
        unvalued_operations: Trades excluded for having no euro
            consideration.
        wash_sale_alerts: Losses that art. 33.5.f LIRPF may defer.
    """

    sales: list[Sale]
    total_gain_loss: Decimal
    dividends: list[DividendIncome]
    savings_base: SavingsBase
    domestic_withholdings: Decimal
    irpf_quota: list[TaxBracket] | None
    quota_to_pay: Decimal | None
    connectivity_fees: Decimal
    net_return: Decimal | None
    pending_lots: dict[str, deque[Lot]]
    yearly_results: list[YearlyResult]
    include_tax: bool
    foreign_withholdings: Decimal = Decimal(0)
    interest_income: Decimal = Decimal(0)
    interest_expense: Decimal = Decimal(0)
    other_income: tuple[OtherIncome, ...] = ()
    currency_conversions: tuple[CurrencyConversion, ...] = ()
    unvalued_operations: tuple[UnvaluedOperation, ...] = ()
    wash_sale_alerts: tuple[WashSaleAlert, ...] = ()
