# Standard libraries
import logging
from dataclasses import dataclass
from typing import Any, Final

# 3pps
import polars as pl

# Own modules
from ..domain.models import ExchangeRate, PriceQuote, WeightBase
from ..domain.portfolio import (
    OUTPUT_ASSET_CLASS_COL,
    OUTPUT_CURRENCY_COL,
    OUTPUT_ISIN_COL,
    OUTPUT_NATIVE_PRICE_COL,
    OUTPUT_PERCENTAGE_COL,
    OUTPUT_PRICE_COL,
    OUTPUT_PRODUCT_COL,
    OUTPUT_QUANTITY_COL,
    OUTPUT_REASON_COL,
    OUTPUT_SOURCE_COL,
    OUTPUT_TOTAL_COL,
    OUTPUT_TYPE_COL,
    OUTPUT_VALUE_COL,
    AssetClass,
)
from ..providers.justetf import QUOTE_MAX_AGE_SECONDS
from ..providers.quotes import resolve_quote
from ..providers.yahoo import is_etf

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

# Declared schema of the valuation frame. Stating it keeps the column
# names and types stable when every position is unpriced, so a run that
# could reach no provider still yields a frame the renderers can read
# instead of a schema-less empty one.
_VALUATION_SCHEMA: Final[dict[str, pl.DataType]] = {
    OUTPUT_PRODUCT_COL: pl.Utf8(),
    OUTPUT_ISIN_COL: pl.Utf8(),
    OUTPUT_TYPE_COL: pl.Utf8(),
    OUTPUT_ASSET_CLASS_COL: pl.Utf8(),
    OUTPUT_QUANTITY_COL: pl.Int64(),
    OUTPUT_PRICE_COL: pl.Float64(),
    OUTPUT_VALUE_COL: pl.Float64(),
    OUTPUT_NATIVE_PRICE_COL: pl.Float64(),
    OUTPUT_CURRENCY_COL: pl.Utf8(),
    OUTPUT_REASON_COL: pl.Utf8(),
    OUTPUT_SOURCE_COL: pl.Utf8(),
}


@dataclass(frozen=True)
class ValuationSnapshot:
    """
    Priced positions together with the rate that valued them.

    The frame alone cannot say which exchange rate produced its euro
    figures, and on a day when the primary source is rate-limited that
    is exactly what a reader needs to know. Carrying both keeps the
    provenance attached to the numbers it explains.

    Attributes:
        frame: Valuation frame with one row per open position, priced or
            not, and its portfolio weight.
        exchange_rate: USD to EUR rate the conversion used, or None when
            none could be resolved.
    """

    frame: pl.DataFrame
    exchange_rate: ExchangeRate | None


def _valuation_row(
    product: str,
    isin: str,
    quantity: int,
    quote: PriceQuote,
    asset_class: AssetClass,
) -> dict[str, Any]:
    """
    Build one row of the valuation frame from a resolved quote.

    Args:
        product: Descriptive product name.
        isin: ISIN identifier of the position.
        quantity: Units held.
        quote: Price as the provider chain resolved it.
        asset_class: Declared asset class of the position.

    Returns:
        The row, with nulls in the euro columns when the quote carries
        no euro price.
    """

    return {
        OUTPUT_PRODUCT_COL: product,
        OUTPUT_ISIN_COL: isin,
        OUTPUT_TYPE_COL: "ETF" if is_etf(product) else "Accion",
        OUTPUT_ASSET_CLASS_COL: asset_class.value,
        OUTPUT_QUANTITY_COL: quantity,
        OUTPUT_PRICE_COL: quote.price_eur,
        OUTPUT_VALUE_COL: (
            None if quote.price_eur is None else quantity * quote.price_eur
        ),
        OUTPUT_NATIVE_PRICE_COL: quote.native_price,
        OUTPUT_CURRENCY_COL: quote.currency,
        OUTPUT_REASON_COL: (
            None if quote.unavailable is None else str(quote.unavailable)
        ),
        OUTPUT_SOURCE_COL: None if quote.source is None else str(quote.source),
    }


def obtain_yahoo_info(
    df_input: pl.DataFrame,
    eur_rate: float | None = None,
    asset_classes: dict[str, AssetClass] | None = None,
    max_age_seconds: float = QUOTE_MAX_AGE_SECONDS,
) -> pl.DataFrame:
    """
    Enrich the positions DataFrame with current prices.

    For each position, resolves the price from the first provider that
    answers: justETF in euros, then Yahoo with a conversion when the
    quote is in dollars. The provider that answered travels in the frame
    so the report can state where each figure came from.

    A position whose EUR value could not be computed keeps its row, with
    nulls in the euro and weight columns, the price the market quotes it
    at in its own currency, and the reason the euro figure is missing.
    Dropping the row instead would rebase every weight in the report
    onto a subset of the account while still presenting it as the whole.

    Args:
        df_input: DataFrame with columns Producto, Symbol/ISIN and
            Cantidad (output of parse_portfolio_xlsx).
        eur_rate: USD to EUR conversion rate, or None when it could not
            be retrieved.
        asset_classes: Asset class declared per ISIN in the holdings
            config. Anything absent is equity, which is what the rest
            of the analysis already assumes.
        max_age_seconds: Age above which a cached quote is refetched.
            Zero forces a fresh request per position.

    Returns:
        DataFrame with additional columns: product type, declared asset
        class, unit price in EUR, current market value in EUR, native
        price, currency, the reason a euro value is absent and the
        provider the price came from.
    """

    classes = asset_classes if asset_classes is not None else {}
    data: list[dict[str, Any]] = []

    for product, isin, quantity in df_input.iter_rows():
        quote = resolve_quote(isin, eur_rate, max_age_seconds)

        if quote.price_eur is None:
            _LOGGER.warning(
                "No hay valor en euros para %s; la posición se muestra sin "
                "valorar y queda fuera de la base de los pesos.",
                isin,
            )

        data.append(
            _valuation_row(
                product,
                isin,
                quantity,
                quote,
                classes.get(isin, AssetClass.EQUITY),
            )
        )

    return pl.DataFrame(data, schema=_VALUATION_SCHEMA)


def compute_portfolio_percentage(df_input: pl.DataFrame) -> pl.DataFrame:
    """
    Add total invested and portfolio percentage columns.

    The total sums the positions that could be valued, and an unpriced
    position keeps a null weight rather than a zero: it is a position of
    unknown weight, which is not a position of no weight.

    Args:
        df_input: DataFrame with the market value column (output of
            obtain_yahoo_info).

    Returns:
        DataFrame with additional columns Total EUR and Porcentaje
        Cartera.
    """

    return df_input.with_columns(
        pl.col(OUTPUT_VALUE_COL).sum().alias(OUTPUT_TOTAL_COL)
    ).with_columns(
        # An empty or all-zero portfolio would otherwise yield NaN/inf
        # percentages when dividing by a zero total.
        pl.when(pl.col(OUTPUT_TOTAL_COL) == 0)
        .then(pl.lit(0.0))
        .otherwise(pl.col(OUTPUT_VALUE_COL) / pl.col(OUTPUT_TOTAL_COL) * 100)
        .alias(OUTPUT_PERCENTAGE_COL)
    )


def priced_positions(df_valuation: pl.DataFrame) -> pl.DataFrame:
    """
    Keep only the positions that carry a EUR value.

    Args:
        df_valuation: DataFrame produced by ``obtain_yahoo_info``.

    Returns:
        The rows whose market value in EUR is present.
    """

    return df_valuation.filter(pl.col(OUTPUT_VALUE_COL).is_not_null())


def describe_weight_base(df_valuation: pl.DataFrame) -> WeightBase:
    """
    Report what the weights in the valuation frame are relative to.

    Args:
        df_valuation: DataFrame produced by ``compute_portfolio_percentage``.

    Returns:
        The weight base: how many positions form it, which ones were
        left out, and the market value it adds up to.
    """

    df_priced = priced_positions(df_valuation)
    unpriced = df_valuation.filter(pl.col(OUTPUT_VALUE_COL).is_null())

    return WeightBase(
        priced_count=df_priced.height,
        unpriced_count=unpriced.height,
        unpriced_isins=tuple(unpriced[OUTPUT_ISIN_COL].to_list()),
        market_value_eur=float(df_priced[OUTPUT_VALUE_COL].sum())
        if df_priced.height > 0
        else 0.0,
    )


def extract_position_values(df_valuation: pl.DataFrame) -> dict[str, float]:
    """
    Extract the market value per ISIN from a valuation DataFrame.

    Lets the holdings analysis reuse the prices already fetched for the
    valuation table instead of querying Yahoo Finance a second time,
    which also guarantees both views are built from the same price
    snapshot. Unpriced positions are absent, since a look-through weight
    cannot be computed from a value that does not exist.

    Args:
        df_valuation: DataFrame produced by ``obtain_yahoo_info``.

    Returns:
        Dict mapping ISIN to market value in EUR. Empty when the
        valuation carries no priced position.
    """

    df_priced = priced_positions(df_valuation)

    if df_priced.height == 0:
        return {}

    return dict(
        zip(
            df_priced[OUTPUT_ISIN_COL].to_list(),
            df_priced[OUTPUT_VALUE_COL].to_list(),
            strict=True,
        )
    )
