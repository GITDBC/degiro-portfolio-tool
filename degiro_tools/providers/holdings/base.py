# Standard libraries
from pathlib import Path
from typing import Protocol, runtime_checkable

# Own modules
from ...domain.holdings import Holding


@runtime_checkable
class HoldingsParser(Protocol):
    """
    Strategy interface for provider-specific holdings parsers.

    Each parser detects whether it can handle a given file based on
    its content (header signatures, file markers) and, if so, parses
    it into a list of Holding objects.

    Attributes:
        name: Short provider identifier used in logs.
    """

    @property
    def name(self) -> str:
        """
        Short provider identifier used in logs.

        Returns:
            Short provider identifier used in logs.
        """

    def can_parse(self, path: Path) -> bool:
        """
        Report whether this parser recognizes the file content.

        Args:
            path: Path to the candidate holdings file.

        Returns:
            True if this parser can handle the file.
        """

    def parse(self, path: Path, isin: str) -> list[Holding]:
        """
        Parse the file into holdings.

        Args:
            path: Path to the holdings file.
            isin: Source ETF ISIN for attribution.

        Returns:
            List of Holding objects.
        """
