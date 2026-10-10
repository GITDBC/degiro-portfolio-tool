# Standard libraries
import logging
from typing import Final

# Name of the application-wide parent logger configured at the CLI entry
# point. Every module logs through its own ``logging.getLogger(__name__)``
# under this package, so the module loggers descend from this one and
# inherit its level and handler without being handed anything.
_APP_LOGGER_NAME: Final[str] = "degiro_tools"

# Default format, used when the run is not verbose.
_LOG_FORMAT: Final[str] = "%(levelname)s: %(message)s"

# Verbose format, which adds the logger name: it is the dotted module
# path of whatever emitted the line, which is what makes a debug run
# traceable back to its origin.
_VERBOSE_LOG_FORMAT: Final[str] = "%(levelname)s %(name)s: %(message)s"


def build_logger(*, verbose: bool = False) -> None:
    """
    Build and configure the application logger.

    Creates (or retrieves) the parent ``degiro_tools`` logger, attaches
    a stream handler exactly once, and sets its level from the
    verbosity flag. Modules do not receive this logger: they fetch their
    own with ``logging.getLogger(__name__)``, which descends from this
    one and therefore inherits its level and handler.

    Args:
        verbose: When True, lower the threshold to DEBUG and name the
            emitting logger on every line; otherwise INFO.
    """

    logger = logging.getLogger(_APP_LOGGER_NAME)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(_VERBOSE_LOG_FORMAT if verbose else _LOG_FORMAT)
        )
        logger.addHandler(handler)
