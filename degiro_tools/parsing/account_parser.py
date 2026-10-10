# Standard libraries
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, TypedDict

# Own modules
from ..domain.models import (
    AccountStatement,
    CurrencyConversion,
    DividendIncome,
    Operation,
    OperationType,
    OtherIncome,
    StatementRowKind,
    UnclassifiedRow,
    UnvaluedOperation,
)
from .columns import (
    CURRENCY_COL,
    DATE_COL,
    DESCRIPTION_COL,
    ISIN_COL,
    ORDER_ID_COL,
    PRODUCT_COL,
    TIME_COL,
    VARIATION_COL,
)
from .excel import read_excel_frame

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

# Worksheet holding the Degiro account statement inside Account.xlsx.
ACCOUNT_SHEET_NAME: Final[str] = "Estado de cuenta"

# Largest column index accessed unconditionally while reading a data row.
# Rows shorter than this are truncated/malformed and are skipped.
MIN_ROW_LEN: Final[int] = VARIATION_COL + 1

# Currency every fiscal figure of the report is expressed in.
EUR: Final[str] = "EUR"

# Country prefix of the ISINs whose withholding is Spanish, and
# therefore a payment on account instead of a foreign tax.
SPANISH_ISIN_PREFIX: Final[str] = "ES"

# Thousands separator Degiro writes quantities with, in the Spanish
# locale of the export: "1.081" is 1081 units, not a fractional amount.
THOUSANDS_SEPARATOR: Final[str] = "."

# Quantity of units of a buy/sell row. The separated form is matched
# explicitly, group by group, instead of accepting any run of digits and
# dots: a match then converts exactly by removing the separators, and a
# quantity that is not a whole number of units does not match at all.
# The plain alternative comes second so "1.081" is read as one grouped
# number rather than as the "1" in front of it.
QUANTITY_PATTERN: Final[str] = r"\d{1,3}(?:\.\d{3})+|\d+"

# Buy/sell row: type, quantity in Spanish notation, product name and the
# unit price after the "@" marker, this one with a decimal comma.
OPERATION_REGEX: Final[re.Pattern[str]] = re.compile(
    rf"^(Compra|Venta)\s+({QUANTITY_PATTERN})\s+.*@([\d.,]+)\s+\w+"
)

# Prefix shared by every buy/sell row. Used to detect operation rows that
# OPERATION_REGEX failed to parse so they are reported instead of silently
# dropped from the fiscal report.
OPERATION_PREFIX_REGEX: Final[re.Pattern[str]] = re.compile(r"^(Compra|Venta)\s")

FEE_DESCRIPTIONS: Final[tuple[str, ...]] = (
    "Costes de transacción",
    "Comisión",
    "Tasa",
    "Impuesto",
    "Spanish Transaction Tax",
    "Transaction Tax",
)

# Description of the market data subscription, charged to the account and
# not to any single trade.
CONNECTIVITY_DESCRIPTION: Final[str] = "Comisión de conectividad con el mercado"

# Fragment identifying both legs of a currency exchange.
EXCHANGE_DESCRIPTION: Final[str] = "Cambio de Divisa"

# Descriptions of the tax withheld at source on a dividend. Checked
# before the dividend itself, since their text also mentions one.
WITHHOLDING_DESCRIPTIONS: Final[tuple[str, ...]] = (
    "Retención del dividendo",
    "Impuesto sobre dividendo",
)

# Openings of a gross dividend row. Matched as a prefix rather than by
# equality: Degiro writes variants such as "Dividendo (ajuste)" or
# "Reparto de dividendo", and requiring the exact word "Dividendo" left
# them out of the report without a word.
DIVIDEND_PREFIXES: Final[tuple[str, ...]] = (
    "Dividendo",
    "Reparto de dividendo",
    "Pago de dividendo",
)

# Fragments of the credit/debit interest rows on the cash balance.
INTEREST_DESCRIPTIONS: Final[tuple[str, ...]] = (
    "Interest",
    "Interés",
    "Intereses",
)

# Fragments of amounts credited that are neither a return on an
# instrument nor the transmission of one.
OTHER_INCOME_DESCRIPTIONS: Final[tuple[str, ...]] = (
    "Promoción",
    "Bonificación",
    "Reembolso",
)

# Fragments of the rows that only move cash in or out of the account,
# which alter no patrimony and belong in no fiscal block.
CASH_DESCRIPTIONS: Final[tuple[str, ...]] = (
    "Cash Sweep",
    "Deposit",
    "Depósito",
    "Withdrawal",
    "Transferir",
    "Ingreso",
    "Retirada",
    "flatex",
)

# Description fragments mapped to what they mean, in the order they must
# be tried. A withholding names a dividend, a dividend processing fee
# names dividends, an exchange leg is worded like a deposit and the
# connectivity fee is worded like a fee: the first match wins, so the
# narrower concept has to come first.
DESCRIPTION_KINDS: Final[tuple[tuple[tuple[str, ...], StatementRowKind], ...]] = (
    ((CONNECTIVITY_DESCRIPTION,), StatementRowKind.CONNECTIVITY_FEE),
    ((EXCHANGE_DESCRIPTION,), StatementRowKind.CURRENCY_EXCHANGE),
    (WITHHOLDING_DESCRIPTIONS, StatementRowKind.DIVIDEND_WITHHOLDING),
    (INTEREST_DESCRIPTIONS, StatementRowKind.INTEREST),
    (OTHER_INCOME_DESCRIPTIONS, StatementRowKind.OTHER_INCOME),
    (FEE_DESCRIPTIONS, StatementRowKind.FEE),
    (CASH_DESCRIPTIONS, StatementRowKind.CASH_MOVEMENT),
)


class AccountRow(TypedDict):
    """
    Normalized account-statement row with an exact Decimal amount.

    Attributes:
        date: Operation date in ``dd-mm-yyyy`` format.
        time: Time in ``HH:MM`` format.
        isin: ISIN identifier of the instrument, empty when absent.
        product: Product name, empty when absent.
        description: Operation description.
        variation: Exact variation amount in its own currency (sign
            included).
        currency: Variation currency code, empty when absent.
    """

    date: str
    time: str
    isin: str
    product: str
    description: str
    variation: Decimal
    currency: str


@dataclass
class StatementRows:
    """
    Every row of the statement, sorted into what it means fiscally.

    Attributes:
        groups: Rows that belong to an operation or a payment, keyed by
            order ID or by ``(date, time, ISIN)``.
        connectivity_fees: Absolute total of market connectivity fees.
        interest_income: Credit interest collected, in EUR.
        interest_expense: Absolute total of debit interest charged.
        other_income: Promotions, rebates and refunds credited.
        loose_exchanges: Currency exchange rows tied to no instrument.
        unclassified: Rows the parser could neither recognize nor
            attribute.
    """

    groups: dict[object, list[AccountRow]] = field(
        default_factory=lambda: defaultdict(list)
    )
    connectivity_fees: Decimal = Decimal(0)
    interest_income: Decimal = Decimal(0)
    interest_expense: Decimal = Decimal(0)
    other_income: list[OtherIncome] = field(default_factory=list)
    loose_exchanges: list[AccountRow] = field(default_factory=list)
    unclassified: list[UnclassifiedRow] = field(default_factory=list)


def is_dividend_row(description: str) -> bool:
    """
    Report whether a description opens a gross dividend row.

    Args:
        description: Statement description to test.

    Returns:
        True when the row is a gross dividend and not its withholding.
    """

    if any(key in description for key in WITHHOLDING_DESCRIPTIONS):
        return False

    return description.startswith(DIVIDEND_PREFIXES)


def classify_description(description: str) -> StatementRowKind:
    """
    Decide what a statement row means from its description.

    Every row gets a kind, so a description this parser does not know
    ends up as ``UNKNOWN`` and can be reported. The order of the checks
    matters: a withholding mentions a dividend, a dividend processing
    fee mentions dividends, and both legs of a currency exchange are
    worded like a deposit and a withdrawal.

    Args:
        description: Statement description to classify.

    Returns:
        The kind the row belongs to.
    """

    if OPERATION_PREFIX_REGEX.match(description):
        return StatementRowKind.OPERATION

    # Checked before the keyword table because a gross dividend row is
    # recognized by how it opens, not by a fragment: that is what keeps
    # a dividend processing fee from being read as a dividend.
    if is_dividend_row(description):
        return StatementRowKind.DIVIDEND

    for keywords, kind in DESCRIPTION_KINDS:
        if any(keyword in description for keyword in keywords):
            return kind

    return StatementRowKind.UNKNOWN


def amount_to_decimal(text: str) -> Decimal:
    """
    Convert a Degiro XLSX amount cell to an exact Decimal.

    The XLSX export stores amounts as native numbers that Polars
    renders as dot-decimal text (for example ``"-1734.6"``) when the
    sheet is read as strings. Converting straight from that text keeps
    full precision and avoids both the European thousand/decimal
    ambiguity and IEEE-754 float artifacts.

    Args:
        text: Raw amount cell, empty when the cell is blank.

    Returns:
        Equivalent Decimal value, or ``Decimal(0)`` if the string is
        empty.

    Raises:
        ValueError: If the string is not a valid amount.
    """

    if not text:
        return Decimal(0)

    try:
        return Decimal(text)

    except InvalidOperation as err:
        error_message = (
            f"Importe no numérico en el extracto: {text!r}. Exporta de nuevo "
            f"el Estado de cuenta desde Degiro sin abrirlo ni editarlo."
        )
        raise ValueError(error_message) from err


def quantity_to_int(text: str) -> int:
    """
    Convert the quantity of a buy/sell description to units.

    Degiro writes the quantity in the Spanish locale, so the dot is a
    thousands separator: "1.081" is 1081 units. Removing the separators
    by hand, rather than leaning on ``locale``, keeps the conversion
    independent of the environment the report is produced in.

    Only text already matched by ``QUANTITY_PATTERN`` reaches here, so
    every dot present is a separator and the result is exact.

    Args:
        text: Quantity group of an ``OPERATION_REGEX`` match.

    Returns:
        Number of units the operation moved.
    """

    return int(text.replace(THOUSANDS_SEPARATOR, ""))


def parse_date(text: str) -> date:
    """
    Parse dates in ``dd-mm-yyyy`` or ``dd-mm-yy`` format.

    Args:
        text: String with the date to parse.

    Returns:
        Corresponding ``date`` object.

    Raises:
        ValueError: If the string matches none of the accepted
            formats.
    """

    for fmt in ("%d-%m-%Y", "%d-%m-%y"):
        try:
            return datetime.strptime(text, fmt).date()

        except ValueError:
            continue

    error_message = (
        f"Fecha no reconocida en el extracto: {text!r}. Se espera "
        f"dd-mm-aaaa, el formato del export de Degiro."
    )
    raise ValueError(error_message)


def group_key(row: list[str]) -> object | None:
    """
    Return the grouping key of the row or ``None`` if it has none.

    Uses the Order ID when present; otherwise groups by
    ``(date, time, ISIN)``.

    Args:
        row: Raw account-statement row as a list of strings.

    Returns:
        Grouping key (Order ID or tuple) or ``None`` when the row is
        tied to no instrument.
    """

    order_id = row[ORDER_ID_COL] if len(row) > ORDER_ID_COL else ""
    if order_id:
        return order_id

    isin = row[ISIN_COL]
    if isin:
        return (row[DATE_COL], row[TIME_COL], isin)

    return None


def is_continuation_row(row: list[str]) -> bool:
    """
    Report whether a row is the tail of a wrapped description.

    Degiro splits long descriptions across two spreadsheet rows: the
    second one carries only the remaining text and leaves every other
    column empty. Such a row is not an operation by itself and its text
    must be appended to the preceding row.

    Args:
        row: Raw account-statement row as a list of strings.

    Returns:
        True if the row only carries description text.
    """

    if len(row) <= DESCRIPTION_COL or not row[DESCRIPTION_COL]:
        return False

    return not any(
        row[col] for col in (DATE_COL, TIME_COL, ISIN_COL) if col < len(row)
    ) and not (len(row) > VARIATION_COL and row[VARIATION_COL])


def merge_continuation_rows(rows: list[list[str]]) -> list[list[str]]:
    """
    Join descriptions that Degiro wrapped onto a following row.

    Without this step a wrapped description loses the ``@price``
    fragment, ``OPERATION_REGEX`` stops matching and the whole
    operation disappears from the fiscal report.

    Args:
        rows: Raw account-statement rows as lists of strings.

    Returns:
        New list of rows where every continuation row has been folded
        into the operation row above it.
    """

    merged: list[list[str]] = []

    for row in rows:
        if merged and is_continuation_row(row):
            previous = merged[-1]
            tail = row[DESCRIPTION_COL].strip()
            previous[DESCRIPTION_COL] = f"{previous[DESCRIPTION_COL].rstrip()} {tail}"
            continue

        merged.append(list(row))

    return merged


def read_account_rows(path: Path) -> list[list[str]]:
    """
    Read the account-statement sheet as rows of plain strings.

    Reads the sheet with ``infer_schema_length=0`` so every cell is
    returned as text, preserving the full decimal precision of the
    amount columns. Empty cells are normalized to empty strings, the
    header row is excluded and descriptions wrapped onto a second row
    are folded back into their operation row.

    Args:
        path: Path to the Account.xlsx file exported from Degiro.

    Returns:
        List of rows, each a list of cell strings.
    """

    frame = read_excel_frame(
        path,
        sheet_name=ACCOUNT_SHEET_NAME,
        infer_schema_length=0,
    )

    rows = [
        ["" if cell is None else str(cell) for cell in row] for row in frame.iter_rows()
    ]

    return merge_continuation_rows(rows)


def normalize_row(row: list[str], variation: Decimal) -> AccountRow:
    """
    Turn a raw cell list into a normalized row.

    Args:
        row: Raw account-statement row as a list of strings.
        variation: Amount already converted to Decimal.

    Returns:
        The normalized row.
    """

    return AccountRow(
        date=row[DATE_COL],
        time=row[TIME_COL],
        isin=row[ISIN_COL],
        product=row[PRODUCT_COL] if len(row) > PRODUCT_COL else "",
        description=row[DESCRIPTION_COL],
        variation=variation,
        currency=row[CURRENCY_COL],
    )


def collect_loose_row(
    entry: AccountRow, kind: StatementRowKind, into: StatementRows
) -> None:
    """
    File a row that belongs to no instrument by what it means.

    Args:
        entry: Normalized row tied to neither an order nor an ISIN.
        kind: Kind the description was classified as.
        into: Accumulator to add the row to.

    Returns:
        None.
    """

    if kind is StatementRowKind.CURRENCY_EXCHANGE:
        into.loose_exchanges.append(entry)
    elif kind is StatementRowKind.INTEREST:
        if entry["variation"] >= 0:
            into.interest_income += entry["variation"]
        else:
            into.interest_expense += abs(entry["variation"])
    elif kind is StatementRowKind.OTHER_INCOME:
        into.other_income.append(
            OtherIncome(
                date=parse_date(entry["date"]),
                description=entry["description"],
                amount_eur=entry["variation"],
            )
        )
    elif kind is not StatementRowKind.CASH_MOVEMENT:
        # A fee or an unknown description with nothing to attach it to.
        # Reported rather than dropped: it is the only signal that the
        # statement carries something this parser does not model.
        into.unclassified.append(
            UnclassifiedRow(
                date=entry["date"],
                description=entry["description"],
                amount=entry["variation"],
                currency=entry["currency"],
            )
        )


def read_statement_rows(
    path: Path,
) -> StatementRows:
    """
    Read every row of the XLSX and sort it by what it means.

    Args:
        path: Path to the Account.xlsx file exported from Degiro.

    Returns:
        Rows grouped per operation plus every standalone concept found.
    """

    collected = StatementRows()

    for row in read_account_rows(path):
        # Defensive guard: rows shorter than the columns we read are
        # malformed and would raise IndexError mid-parse.
        if len(row) < MIN_ROW_LEN:
            _LOGGER.warning(
                "Se omite una fila mal formada con %d columnas "
                "(se esperaban >= %d): %r.",
                len(row),
                MIN_ROW_LEN,
                row,
            )
            continue

        try:
            variation = amount_to_decimal(row[VARIATION_COL])

        except ValueError:
            _LOGGER.warning(
                "Se omite una fila con importe inválido %r: %r.",
                row[VARIATION_COL],
                row,
            )
            continue

        entry = normalize_row(row, variation)
        kind = classify_description(entry["description"])

        if kind is StatementRowKind.CONNECTIVITY_FEE:
            collected.connectivity_fees += abs(variation)
            continue

        key = group_key(row)
        if key is None:
            collect_loose_row(entry, kind, collected)
            continue

        if kind is StatementRowKind.UNKNOWN:
            # Being grouped is not being read: a group only yields
            # operations, fees, exchanges, dividends and withholdings.
            # An unknown description inside one is used by nothing, so
            # it is reported like any other.
            collected.unclassified.append(
                UnclassifiedRow(
                    date=entry["date"],
                    description=entry["description"],
                    amount=entry["variation"],
                    currency=entry["currency"],
                )
            )

        collected.groups[key].append(entry)

    return collected


def sum_fx_eur(rows: list[AccountRow]) -> Decimal | None:
    """
    Total EUR leg of the currency exchanges of an operation group.

    Every ``Cambio de Divisa`` row denominated in EUR is added up.
    Degiro emits one exchange pair per execution, so an order filled in
    several tranches carries several EUR legs and taking only the first
    would understate the consideration.

    Args:
        rows: Rows of the operation group.

    Returns:
        Sum of the EUR exchange legs, or ``None`` when the group has
        none.
    """

    legs = [
        row["variation"]
        for row in rows
        if EXCHANGE_DESCRIPTION in row["description"] and row["currency"] == EUR
    ]

    if not legs:
        return None

    return sum(legs, Decimal(0))


def exchange_rate_of_group(rows: list[AccountRow]) -> Decimal | None:
    """
    EUR per unit of foreign currency implied by an exchange pair.

    Taking the rate from the two legs the broker actually moved needs no
    external source and stays consistent with the euro figures of the
    rest of the report.

    Args:
        rows: Rows of the group.

    Returns:
        Euros per unit of the foreign currency, or ``None`` when the
        group holds no usable exchange pair.
    """

    eur_leg = Decimal(0)
    foreign_leg = Decimal(0)

    for row in rows:
        if EXCHANGE_DESCRIPTION not in row["description"]:
            continue

        if row["currency"] == EUR:
            eur_leg += abs(row["variation"])
        else:
            foreign_leg += abs(row["variation"])

    if not eur_leg or not foreign_leg:
        return None

    return eur_leg / foreign_leg


def build_conversions(rows: list[AccountRow]) -> list[CurrencyConversion]:
    """
    Pair the legs of the currency exchanges of a set of rows.

    Args:
        rows: Rows holding exchange legs, from one group or from the
            statement at large.

    Returns:
        One conversion per ``(date, time)`` that has both legs, in the
        order the dates appear.
    """

    legs: dict[tuple[str, str], list[AccountRow]] = defaultdict(list)

    for row in rows:
        if EXCHANGE_DESCRIPTION in row["description"]:
            legs[(row["date"], row["time"])].append(row)

    conversions: list[CurrencyConversion] = []

    for (day, time), pair in legs.items():
        eur = next((row for row in pair if row["currency"] == EUR), None)
        foreign = next((row for row in pair if row["currency"] != EUR), None)

        if eur is None or foreign is None:
            continue

        conversions.append(
            CurrencyConversion(
                date=parse_date(day),
                time=time,
                eur_amount=eur["variation"],
                foreign_amount=foreign["variation"],
                foreign_currency=foreign["currency"],
            )
        )

    return conversions


def unvalued_from_header(header: AccountRow) -> UnvaluedOperation:
    """
    Describe a trade the statement gives no euro consideration for.

    Args:
        header: Buy/sell row that could not be valued.

    Returns:
        The trade, ready to be reported as excluded.
    """

    return UnvaluedOperation(
        date=parse_date(header["date"]),
        isin=header["isin"],
        product=header["product"],
        description=header["description"],
        amount=header["variation"],
        currency=header["currency"],
    )


def build_operation(
    header: AccountRow, match: re.Match[str], amount_eur: Decimal, fee_eur: Decimal
) -> Operation:
    """
    Build a normalized operation from its buy/sell row.

    Args:
        header: Buy/sell row of the group.
        match: Match of ``OPERATION_REGEX`` over the row description,
            resolved by ``select_operation_headers``.
        amount_eur: Consideration in EUR, sign included.
        fee_eur: Fees attributable to this execution, sign included.

    Returns:
        The normalized operation.
    """

    return Operation(
        date=parse_date(header["date"]),
        time=header["time"],
        isin=header["isin"],
        product=header["description"].split("@")[0].split(" ", 2)[2].strip(),
        operation_type=OperationType(match.group(1)),
        quantity=quantity_to_int(match.group(2)),
        amount_eur=amount_eur,
        fee_eur=fee_eur,
    )


def select_operation_headers(
    rows: list[AccountRow],
) -> list[tuple[AccountRow, re.Match[str]]]:
    """
    Pick the buy/sell rows of a group and report unparseable ones.

    A row that starts like an operation but does not match
    ``OPERATION_REGEX`` would otherwise be dropped without trace,
    silently removing a real trade from the fiscal report. Those rows
    are logged as errors so the discrepancy is visible.

    Each selected row travels with the match that parsed it, so the
    callers read the operation type and the quantity off a match the
    type system guarantees instead of matching again behind an
    ``assert`` that ``python -O`` strips from the bytecode.

    A fractional quantity ("Compra 0,5 ...") is reported here too:
    ``Operation.quantity`` holds whole units, and rounding one in
    silence would put an invented figure in a tax return. Being named in
    an error the user can act on beats being declared wrong.

    Args:
        rows: Rows belonging to a single operation group.

    Returns:
        Pairs of row and its match, for the rows whose description is a
        fully parseable buy/sell.
    """

    headers: list[tuple[AccountRow, re.Match[str]]] = []

    for row in rows:
        match = OPERATION_REGEX.match(row["description"])

        if match is not None:
            headers.append((row, match))
        elif OPERATION_PREFIX_REGEX.match(row["description"]):
            _LOGGER.error(
                "Fila de operación ilegible omitida (%s %s %s): %r. NO se "
                "incluye en el informe fiscal.",
                row["date"],
                row["time"],
                row["isin"],
                row["description"],
            )

    return headers


def build_operations_from_group(
    rows: list[AccountRow],
) -> tuple[list[Operation], list[UnvaluedOperation]]:
    """
    Build the operations of a group, or report them as unvalued.

    When an order has partial executions (several buy/sell rows with the
    same Order ID) it yields one operation per execution, with the fees
    and the euro consideration distributed by share quantity.

    A trade in a foreign currency with no ``Cambio de Divisa`` row of
    its own cannot be expressed in euros from the statement alone, which
    is what a Manual FX account looks like. Those are returned apart
    instead of raised: aborting threw away a whole report over one
    unvaluable order, and inventing a rate would put a made-up number in
    a tax return.

    Args:
        rows: XLSX rows belonging to a single operation.

    Returns:
        Tuple with the operations built and the trades left unvalued.

    Raises:
        ValueError: If a date in the group is not valid.
    """

    headers = select_operation_headers(rows)
    if not headers:
        return [], []

    total_fee = sum(
        (
            row["variation"]
            for row in rows
            if any(key in row["description"] for key in FEE_DESCRIPTIONS)
        ),
        Decimal(0),
    )

    # Degiro emits one Cambio de Divisa pair per execution, so every
    # EUR exchange leg is added up rather than only the first, which
    # would understate multi-tranche foreign orders.
    total_fx_eur = sum_fx_eur(rows)
    total_qty = sum(quantity_to_int(match.group(2)) for _, match in headers)

    operations: list[Operation] = []
    unvalued: list[UnvaluedOperation] = []

    for header, match in headers:
        ratio = Decimal(quantity_to_int(match.group(2))) / Decimal(total_qty)

        if total_fx_eur is not None:
            amount = total_fx_eur * ratio
        elif header["currency"] == EUR:
            amount = header["variation"]
        else:
            unvalued.append(unvalued_from_header(header))
            continue

        operations.append(build_operation(header, match, amount, total_fee * ratio))

    return operations, unvalued


def dividend_withholding(rows: list[AccountRow], currency: str) -> Decimal:
    """
    Total tax withheld at source inside a dividend group.

    Args:
        rows: Rows of the group.
        currency: Currency the gross dividend is denominated in.

    Returns:
        Withholding as a positive amount, in that same currency.
    """

    return sum(
        (
            abs(row["variation"])
            for row in rows
            if any(key in row["description"] for key in WITHHOLDING_DESCRIPTIONS)
            and row["currency"] == currency
        ),
        Decimal(0),
    )


def build_dividend_from_group(rows: list[AccountRow]) -> DividendIncome | None:
    """
    Build a ``DividendIncome`` from grouped rows, if any.

    A dividend payment appears as a gross row optionally followed by a
    withholding row for the tax taken at source. Both are matched by the
    ``(date, time, ISIN)`` fallback key because Degiro assigns them no
    Order ID. Every gross row of the group is added up, so a payment
    split across two lines is not read as one.

    A dividend paid in a foreign currency is converted with the rate
    implied by the ``Cambio de Divisa`` pair Degiro settled it with. Only
    when the statement carries no such pair is the payment excluded, with
    a warning, since there is then no rate to convert it by.

    Args:
        rows: XLSX rows belonging to a single group.

    Returns:
        The dividend collected, or ``None`` when the group holds none.

    Raises:
        ValueError: If the payment date is not valid.
    """

    gross_rows = [row for row in rows if is_dividend_row(row["description"])]

    if not gross_rows:
        return None

    header = gross_rows[0]
    currency = header["currency"]
    gross = sum((row["variation"] for row in gross_rows), Decimal(0))
    withholding = dividend_withholding(rows, currency)
    rate = Decimal(1)

    if currency != EUR:
        implied = exchange_rate_of_group(rows)

        if implied is None:
            _LOGGER.warning(
                "El dividendo de %s (%s) está en %s y el extracto no trae su "
                "cambio de divisa; se excluye del informe fiscal. Añádelo a "
                "mano con el tipo del BCE del día de cobro.",
                header["isin"],
                header["date"],
                currency,
            )
            return None

        rate = implied

    return DividendIncome(
        date=parse_date(header["date"]),
        isin=header["isin"],
        product=header["product"],
        gross_eur=gross * rate,
        withholding_eur=withholding * rate,
        domestic=header["isin"].startswith(SPANISH_ISIN_PREFIX),
    )


def drop_unvalued_isins(
    operations: list[Operation],
    unvalued: list[UnvaluedOperation],
) -> list[Operation]:
    """
    Remove every ISIN whose history has an unvaluable trade.

    A FIFO chain is only as good as the operations feeding it: keeping
    the valuable half of an ISIN would match sales against lots that are
    not all there and publish a gain computed on a partial cost. The
    whole instrument steps aside instead, and is reported.

    Args:
        operations: Operations built from the statement.
        unvalued: Trades with no euro consideration.

    Returns:
        Operations of the ISINs that could be valued in full.
    """

    if not unvalued:
        return operations

    blocked = {item.isin for item in unvalued}

    _LOGGER.warning(
        "%d operación(es) sin contravalor en EUR en el extracto (cuenta "
        "Manual FX o divisa sin cambio asociado). Se excluye del informe "
        "fiscal todo el historial de %s: calcula esos valores aparte con el "
        "tipo del BCE de cada fecha.",
        len(unvalued),
        ", ".join(sorted(blocked)),
    )

    return [operation for operation in operations if operation.isin not in blocked]


def report_unclassified(rows: list[UnclassifiedRow]) -> None:
    """
    Warn about the rows that reached no fiscal concept.

    An export in another language, or a movement type Degiro adds later,
    shows up here. Saying so beats printing a report that quietly leaves
    money out.

    Args:
        rows: Rows the parser could neither recognize nor attribute.

    Returns:
        None.
    """

    if not rows:
        return

    sample = ", ".join(sorted({row.description for row in rows})[:5])

    _LOGGER.warning(
        "%d fila(s) del extracto no se han podido clasificar y quedan fuera "
        "del informe: %s. Revisa si el extracto está en otro idioma o si "
        "contiene movimientos no contemplados.",
        len(rows),
        sample,
    )


def warn_on_empty_statement(statement: AccountStatement) -> None:
    """
    Warn when nothing fiscally relevant came out of the statement.

    An empty report is indistinguishable from a year with no activity,
    which is exactly how an unparseable export would look.

    Args:
        statement: Parsed statement about to be returned.

    Returns:
        None.
    """

    if statement.operations or statement.dividends:
        return

    _LOGGER.warning(
        "No se ha reconocido ninguna compra, venta ni dividendo en el "
        "extracto. Comprueba que es el XLSX de Estado de cuenta de Degiro "
        "exportado en castellano y que cubre el periodo esperado."
    )


def parse_account_xlsx(path: Path) -> AccountStatement:
    """
    Parse a Degiro Account.xlsx statement.

    Reads the ``Estado de cuenta`` sheet and groups rows by Order ID
    (or by ``(Date, Time, ISIN)`` when there is no ID) to obtain the
    broker's real EUR consideration (the ``Cambio de Divisa`` rows in
    EUR), the associated fees and the dividends collected with their
    withholdings. Amounts are read as exact Decimals so no monetary
    precision is lost.

    Every row is accounted for: interest, promotions and currency
    exchanges that pair with no trade travel with the statement instead
    of being dropped, and anything left unrecognized is reported.

    Args:
        path: Path to the Account.xlsx file exported from Degiro
            (account statement).

    Returns:
        ``AccountStatement`` with the operations sorted by date and
        time, the dividends sorted by date, and every other concept the
        statement carries.

    Raises:
        ValueError: If a date or an amount in the sheet is not valid.
    """

    collected = read_statement_rows(path)

    operations: list[Operation] = []
    dividends: list[DividendIncome] = []
    unvalued: list[UnvaluedOperation] = []
    conversions = build_conversions(collected.loose_exchanges)

    for rows in collected.groups.values():
        group_ops, group_unvalued = build_operations_from_group(rows)
        operations.extend(group_ops)
        unvalued.extend(group_unvalued)

        dividend = build_dividend_from_group(rows)
        if dividend is not None:
            dividends.append(dividend)

        # An exchange inside a group of trades or of dividends is the
        # conversion of that very payment, already inside its euro
        # figures. One that pairs with neither is a swap of currency on
        # its own, which alters the patrimony by itself.
        if not group_ops and not group_unvalued and dividend is None:
            conversions.extend(build_conversions(rows))

    operations = drop_unvalued_isins(operations, unvalued)
    operations.sort(key=lambda operation: (operation.date, operation.time))
    dividends.sort(key=lambda dividend: dividend.date)
    report_unclassified(collected.unclassified)

    statement = AccountStatement(
        operations=operations,
        dividends=dividends,
        connectivity_fees=collected.connectivity_fees,
        interest_income=collected.interest_income,
        interest_expense=collected.interest_expense,
        other_income=tuple(collected.other_income),
        currency_conversions=tuple(
            sorted(conversions, key=lambda item: (item.date, item.time))
        ),
        unvalued_operations=tuple(unvalued),
        unclassified_rows=tuple(collected.unclassified),
    )

    warn_on_empty_statement(statement)

    return statement
