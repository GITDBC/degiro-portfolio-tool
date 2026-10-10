# Standard libraries
from enum import StrEnum
from typing import Final


class AssetClass(StrEnum):
    """
    Asset class a position belongs to for allocation purposes.

    Declared per ISIN in the holdings config, because nothing in the
    Degiro export identifies it: the ISIN prefix is the issuing country
    and the product name of a money-market fund need not carry any
    money-market token. A money-market fund counts as fixed income
    here, which is what the allocation asks of it. Crypto is a class of
    its own for the mirror reason: a single-asset ETP has no equity
    constituents to break down, so without a class to land in its whole
    weight would be reported as unclassified residual.

    Every class other than ``EQUITY`` describes a position that enters
    the look-through whole, so adding one is a declaration here plus a
    section in the holdings config, never a new code path.

    Attributes:
        EQUITY: Shares and equity funds, the default for anything not
            declared.
        FIXED_INCOME: Bonds, bond funds and money-market funds.
        CRYPTO: Crypto ETPs and other single-asset crypto wrappers.
    """

    EQUITY = "Renta variable"
    FIXED_INCOME = "Renta fija"
    CRYPTO = "Criptomonedas"


# Column names for the Degiro Portfolio XLSX input
INPUT_PRODUCT_COL: Final[str] = "Producto"
INPUT_ISIN_COL: Final[str] = "Symbol/ISIN"
INPUT_QUANTITY_COL: Final[str] = "Cantidad"
INPUT_CASH_VALUE_COL: Final[str] = "Valor en EUR"

# Column names for the enriched portfolio output
OUTPUT_PRODUCT_COL: Final[str] = "Producto"
OUTPUT_ISIN_COL: Final[str] = "Symbol/ISIN"
OUTPUT_TYPE_COL: Final[str] = "Tipo"
OUTPUT_QUANTITY_COL: Final[str] = "Cantidad"
OUTPUT_PRICE_COL: Final[str] = "Precio €"
OUTPUT_VALUE_COL: Final[str] = "Valor €"
OUTPUT_TOTAL_COL: Final[str] = "Total €"
OUTPUT_PERCENTAGE_COL: Final[str] = "Porcentaje Cartera"

# Price as the market quotes it, kept beside the euro figure so a
# position that could not be converted still shows a real price instead
# of an empty row.
OUTPUT_NATIVE_PRICE_COL: Final[str] = "Precio orig."
OUTPUT_CURRENCY_COL: Final[str] = "Moneda"

# Why a position carries no euro value. Null when it carries one.
OUTPUT_REASON_COL: Final[str] = "Motivo"

# Provider the price came from. Two sources answer for the same
# position and they do not fail together, so which one produced a row is
# reportable provenance rather than an implementation detail.
OUTPUT_SOURCE_COL: Final[str] = "Fuente"

# Declared asset class of the position. A different axis from the
# instrument wrapper in OUTPUT_TYPE_COL: a bond ETF is an "ETF" there
# and "Renta fija" here, and conflating the two is what let a fixed
# income position be reported as equity.
OUTPUT_ASSET_CLASS_COL: Final[str] = "Clase"

# What the tables print where a datum does not exist. The reports use it
# as a real value, never as a blank: an unpriced position has no euro
# value, which is not a value of zero.
NOT_AVAILABLE: Final[str] = "n/d"
