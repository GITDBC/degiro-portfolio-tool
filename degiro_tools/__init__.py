# Standard libraries
import importlib.metadata

# Own modules
from .calculation.fifo import calculate_fifo
from .calculation.performance import compute_performance, money_weighted_return
from .calculation.report_data import build_report_data
from .calculation.savings_base import compute_savings_base
from .calculation.tax import SAVINGS_BRACKETS, calculate_irpf_quota
from .calculation.yearly import compute_yearly_results
from .domain.etf import ETFProfile
from .domain.models import (
    AccountStatement,
    AcquisitionMatch,
    DividendIncome,
    Lot,
    MarketSnapshot,
    Operation,
    OperationType,
    PerformanceSummary,
    PositionPerformance,
    ReportData,
    Sale,
    SavingsBase,
    TaxBracket,
    YearlyResult,
)
from .parsing import parse_account_xlsx
from .providers import fetch_etf_catalog, fetch_etf_profiles
from .reporting import print_report, render_etf_profiles, render_performance

__version__: str = importlib.metadata.version("degiro_tools")

__all__: list[str] = [
    "AccountStatement",
    "AcquisitionMatch",
    "DividendIncome",
    "ETFProfile",
    "Lot",
    "MarketSnapshot",
    "Operation",
    "OperationType",
    "PerformanceSummary",
    "PositionPerformance",
    "ReportData",
    "SAVINGS_BRACKETS",
    "Sale",
    "SavingsBase",
    "TaxBracket",
    "YearlyResult",
    "__version__",
    "build_report_data",
    "calculate_fifo",
    "calculate_irpf_quota",
    "compute_performance",
    "compute_savings_base",
    "compute_yearly_results",
    "fetch_etf_catalog",
    "fetch_etf_profiles",
    "money_weighted_return",
    "parse_account_xlsx",
    "print_report",
    "render_etf_profiles",
    "render_performance",
]
