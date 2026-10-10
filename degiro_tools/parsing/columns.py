# Standard libraries
from typing import Final

# Column indices for the Degiro "Estado de cuenta" XLSX export.
# 0 Fecha, 1 Hora, 2 Fecha valor, 3 Producto, 4 ISIN, 5 Descripción,
# 6 Tipo, 7 Variación (currency), 8 Variación (amount), 9 Saldo
# (currency), 10 Saldo (amount), 11 ID Orden.
# The "Variación" header label sits over the currency column (7); the
# signed amount is the following unlabeled column (8).

DATE_COL: Final[int] = 0
TIME_COL: Final[int] = 1
PRODUCT_COL: Final[int] = 3
ISIN_COL: Final[int] = 4
DESCRIPTION_COL: Final[int] = 5
CURRENCY_COL: Final[int] = 7
VARIATION_COL: Final[int] = 8
ORDER_ID_COL: Final[int] = 11
