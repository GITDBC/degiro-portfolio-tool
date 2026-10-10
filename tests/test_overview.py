# Standard libraries
from unittest.mock import MagicMock

# 3pps
import polars as pl
import pytest

# Own modules
from degiro_tools import cli
from degiro_tools.calculation.portfolio import ValuationSnapshot
from degiro_tools.cli import ANALYSIS_SECTIONS, build_arg_parser, main
from degiro_tools.domain.models import WeightBase

# Sections that need no account statement, so they render on a bare run.
DEFAULT_SECTIONS: tuple[str, ...] = (
    "valuation",
    "holdings",
    "sectors",
    "etf",
)

# Snapshot ``build_valuation`` is stubbed to return. The frame itself is
# a sentinel: these tests check the wiring between sections, not the
# numbers, which the per-section test modules cover against real frames.
VALUATION_SNAPSHOT: ValuationSnapshot = ValuationSnapshot(
    frame="valuation_df",  # type: ignore[arg-type]
    exchange_rate=None,
)

# A base covering every position, so no caveat is printed and the
# renderers are exercised on their ordinary path.
WHOLE_BASE: WeightBase = WeightBase(
    priced_count=2,
    unpriced_count=0,
    unpriced_isins=(),
    market_value_eur=2250.0,
)

# Minimal real frame: the holdings block measures the overlap to print
# its summary line, so a bare sentinel would not survive the call.
OVERLAP_FRAME: pl.DataFrame = pl.DataFrame(
    {
        "name": ["NEWMONT", "IBERDROLA SA"],
        "etf_count": [2, 3],
        "effective_pct": [0.9, 0.5],
    }
)


def stub_pipeline(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """
    Replace every section's data source with mocks that touch no network.

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        Mapping of mock name to the MagicMock installed.
    """

    mocks = {
        "build_valuation": MagicMock(return_value=VALUATION_SNAPSHOT),
        "extract_position_values": MagicMock(return_value={"ISIN": 1.0}),
        "describe_weight_base": MagicMock(return_value=WHOLE_BASE),
        "parse_portfolio_cash": MagicMock(return_value=0.0),
        "load_portfolio_holdings": MagicMock(return_value="holdings_df"),
        "compute_top_holdings": MagicMock(return_value="top_df"),
        # The holdings block folds in the overlap, so this result is
        # measured (row count and total weight), not just passed along.
        "compute_overlap": MagicMock(return_value=OVERLAP_FRAME),
        "compute_sectors": MagicMock(return_value="sectors_df"),
        "compute_geography": MagicMock(return_value=("country_df", "continent_df")),
        "render_portfolio": MagicMock(),
        "render_holdings": MagicMock(),
        "render_distribution": MagicMock(),
        "run_etf_section": MagicMock(),
        "run_performance": MagicMock(),
    }

    for name, mock in mocks.items():
        monkeypatch.setattr(cli, name, mock)

    return mocks


def test_only_two_commands_remain() -> None:
    """
    The CLI exposes exactly tax and overview.

    portfolio and etf were folded into overview as sections, so keeping
    them as commands would be two ways of doing the same thing.
    """

    parser = build_arg_parser()

    assert parser.parse_args(["tax", "Account.xlsx"]).command == "tax"
    assert parser.parse_args(["overview", "Portfolio.xlsx"]).command == "overview"

    for gone in ("portfolio", "etf", "holdings", "overlap", "sectors", "geography"):
        with pytest.raises(SystemExit):
            parser.parse_args([gone, "Portfolio.xlsx"])


def test_the_inflation_region_defaults_to_spain() -> None:
    """
    The real result is deflated with the Spanish index unless told otherwise.

    The tool reports a Spanish tax return, so the index of the country
    whose prices the reader actually pays is the sensible default.
    """

    parser = build_arg_parser()

    assert parser.parse_args(["overview", "Portfolio.xlsx"]).inflation == "es"


def test_the_euro_area_index_can_be_chosen() -> None:
    """
    The euro area is the other published series the tool offers.
    """

    parser = build_arg_parser()
    args = parser.parse_args(["overview", "Portfolio.xlsx", "--inflation", "ea"])

    assert args.inflation == "ea"


def test_an_unknown_inflation_region_is_a_usage_error() -> None:
    """
    A region with no published series is refused at the boundary.

    Falling back to a default would deflate with an index the reader did
    not ask for and would have no way of noticing.
    """

    parser = build_arg_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["overview", "Portfolio.xlsx", "--inflation", "us"])


def test_full_pass_renders_every_section_from_one_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A bare run prices the portfolio once and renders all sections.
    """

    mocks = stub_pipeline(monkeypatch)

    assert main(["overview", "Portfolio.xlsx"]) == 0

    mocks["build_valuation"].assert_called_once()
    mocks["load_portfolio_holdings"].assert_called_once()

    # Prices come from the valuation, so no second fetch.
    mocks["extract_position_values"].assert_called_once_with("valuation_df")
    assert mocks["load_portfolio_holdings"].call_args.kwargs["position_values"] == {
        "ISIN": 1.0
    }

    for renderer in (
        "render_portfolio",
        "render_holdings",
        "run_etf_section",
    ):
        mocks[renderer].assert_called_once()

    # Sectors, continents and countries arrive as one allocation block.
    mocks["render_distribution"].assert_called_once_with(
        "sectors_df", "country_df", "continent_df", weight_base=WHOLE_BASE
    )

    # Latent result needs --account, so it stays out of a bare run.
    mocks["run_performance"].assert_not_called()


def test_account_adds_the_latent_section_to_the_full_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    With --account the full pass gains the latent result.
    """

    mocks = stub_pipeline(monkeypatch)

    assert main(["overview", "Portfolio.xlsx", "--account", "Account.xlsx"]) == 0

    mocks["run_performance"].assert_called_once()
    mocks["render_portfolio"].assert_called_once()


def test_missing_portfolio_file_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Every section is a view of your own positions, so the export is
    required.
    """

    stub_pipeline(monkeypatch)

    assert main(["overview"]) == 1
    assert "XLSX de Portfolio" in capsys.readouterr().err


def test_section_renderer_works_on_a_real_frame() -> None:
    """
    The section dispatcher runs end to end on a small real frame.

    Guards the wiring the mocked tests above cannot see.
    """

    frame = pl.DataFrame(
        {
            "name": ["APPLE INC", "NVIDIA CORP"],
            "sector": ["Information Technology", "Information Technology"],
            "location": ["United States", "United States"],
            "effective_pct": [2.86, 2.62],
            "source_isin": ["IE00B4L5Y983", "IE00B4L5Y983"],
        }
    )

    cli.render_analysis_section("sectors", frame)
    cli.render_analysis_section("holdings", frame)


def test_every_section_has_a_heading() -> None:
    """
    No section can be added without a Spanish heading to print.
    """

    assert set(ANALYSIS_SECTIONS) >= set(DEFAULT_SECTIONS)

    for title in ANALYSIS_SECTIONS.values():
        assert title
        assert title[0].isupper()
