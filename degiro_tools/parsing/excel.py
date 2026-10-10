# Standard libraries
import warnings
from pathlib import Path
from typing import Any, Final

# 3pps
import polars as pl

# Exact text of the only warning this module filters. polars 1.43.2 reads
# spreadsheets through its own ``from_arrow`` (see
# ``polars/io/spreadsheet/functions.py``, which calls it on the capsule
# fastexcel returns), and ``from_arrow`` warns that it will return a
# Series instead of a DataFrame in polars 2.0. The migration the message
# asks for is inside polars itself; nothing here calls ``from_arrow``.
# The warning is nevertheless attributed to this file, because polars
# resolves the stacklevel by walking out of its own frames.
#
# Neither call-site form avoids it while returning the same frame:
# ``engine="openpyxl"`` drops a column and reads quantities as floats,
# and no released polars fixes the internal call (1.43.2 is the latest).
# So the message is filtered by its exact text and only for the frames
# raised inside polars, which leaves every other FutureWarning -
# including a genuine one from this project - fully visible.
#
# Delete this module's filter, not the module, once polars stops calling
# ``from_arrow`` in ``read_excel``.
_POLARS_FROM_ARROW_WARNING: Final[str] = (
    r"from_arrow\(<ArrowStreamExportable>\) will return a Series"
)


def read_excel_frame(path: Path, **options: Any) -> pl.DataFrame:
    """
    Read a spreadsheet into a DataFrame without the polars 2.0 notice.

    Behaves exactly like ``polars.read_excel``, whose result is the
    contract every parser in this package is written against, and only
    keeps polars' own forward-compatibility notice about ``from_arrow``
    out of the output. Drowning the warnings that matter - a missing
    price, an unreachable provider - under three identical notices about
    a polars internal is what this exists to prevent.

    Args:
        path: Path to the spreadsheet to read.
        **options: Options forwarded verbatim to ``polars.read_excel``.

    Returns:
        The sheet as a DataFrame, identical to what ``read_excel``
        returns for the same arguments.

    Raises:
        ValueError: If the options asked for several sheets at once,
            which every parser in this package reads one at a time.
    """

    with warnings.catch_warnings():
        # No 'module' scope: polars sets the stacklevel so the warning is
        # attributed to this file rather than to its own, which is the
        # very reason it looked like a defect in the parsers. The exact
        # message is what keeps the filter narrow.
        warnings.filterwarnings(
            "ignore",
            message=_POLARS_FROM_ARROW_WARNING,
            category=FutureWarning,
        )

        frame = pl.read_excel(path, **options)

    if not isinstance(frame, pl.DataFrame):
        error_message = (
            f"La lectura de {path} devolvió varias hojas; los parsers de este "
            f"paquete leen una sola."
        )
        raise ValueError(error_message)

    return frame
