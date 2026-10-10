# Standard libraries
import argparse
import json
from decimal import Decimal
from pathlib import Path

# 3pps
import pytest
from openpyxl import Workbook

# Own modules
from degiro_tools import cli
from degiro_tools.cli import collect_etf_isins, main
from degiro_tools.domain.etf import ETFCrossCheck, ETFProfile, YahooETFData
from degiro_tools.providers import justetf
from degiro_tools.reporting.etf_report import render_etf_profiles
from degiro_tools.reporting.helpers import fmt_optional

CATALOG_ROW: dict[str, object] = {
    "isin": "IE00B4L5Y983",
    "name": "iShares Core MSCI World UCITS ETF USD (Acc)",
    "ticker": "IWDA",
    "ter": "0.20%",
    "fundSize": "128,942",
    "yearReturnCUR": "23.78%",
    "threeYearReturnPerRiskCUR": "1.46",
}


def profile(isin: str, **overrides: object) -> ETFProfile:
    """
    Build an ETFProfile with sensible defaults for rendering tests.

    Args:
        isin: ISIN identifier.
        overrides: Fields to override.

    Returns:
        ETFProfile ready to render.
    """

    base: dict[str, object] = {
        "isin": isin,
        "name": f"Fondo {isin}",
        "ticker": "TCK",
        "ter_pct": Decimal("0.20"),
        "fund_size_meur": Decimal("128942"),
        "return_ytd_pct": Decimal("16.60"),
        "return_1y_pct": Decimal("23.78"),
        "return_3y_pct": Decimal("68.38"),
        "return_5y_pct": Decimal("77.09"),
        "volatility_1y_pct": Decimal("13.10"),
        "volatility_3y_pct": Decimal("13.01"),
        "max_drawdown_3y_pct": Decimal("-20.45"),
        "return_per_risk_3y": Decimal("1.46"),
        "replication": "Optimized sampling",
        "distribution_policy": "Accumulating",
        "domicile": "Ireland",
        "securities_lending": True,
    }
    base.update(overrides)

    return ETFProfile(**base)  # type: ignore[arg-type]


def crosscheck(
    isin: str, yahoo_ter: str | None = "0.20", with_justetf: bool = True
) -> ETFCrossCheck:
    """
    Build a cross-check pairing both sources for rendering tests.

    Args:
        isin: ISIN identifier.
        yahoo_ter: TER Yahoo publishes, or None when it has none.
        with_justetf: Whether justETF covers the ISIN.

    Returns:
        ETFCrossCheck ready to render.
    """

    return ETFCrossCheck(
        isin=isin,
        justetf=profile(isin) if with_justetf else None,
        yahoo=YahooETFData(
            isin=isin,
            ticker="TCK",
            name=f"Fondo {isin}",
            ter_pct=None if yahoo_ter is None else Decimal(yahoo_ter),
            currency="USD",
        ),
    )


def write_portfolio(path: Path, isins: list[str | None]) -> None:
    """
    Write a minimal Degiro Portfolio XLSX with the given ISINs.

    Args:
        path: Destination file path.
        isins: ISIN of each row; ``None`` writes a cash row.

    Returns:
        None.
    """

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["Producto", "Symbol/ISIN", "Cantidad", "Valor en EUR"])

    for index, isin in enumerate(isins):
        quantity = None if isin is None else index + 1
        sheet.append([f"PROD {index}", isin, quantity, 100.0])

    workbook.save(path)


def test_fmt_optional_marks_absent_data() -> None:
    """
    A missing measurement renders as n/d, never as 0.00.
    """

    assert fmt_optional(None) == "n/d"
    assert fmt_optional(Decimal("1.5")) == "1.50"
    assert fmt_optional(Decimal("510"), 0) == "510"


def test_render_without_profiles_does_not_crash(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    An empty result set prints a notice instead of an empty table.
    """

    render_etf_profiles([], missing=["ES0148396007"])

    output = capsys.readouterr().out
    assert "Sin datos de ETFs" in output
    assert "ES0148396007" in output


def test_render_reports_missing_isins(capsys: pytest.CaptureFixture[str]) -> None:
    """
    Uncovered ISINs are named below the table, not swallowed.
    """

    render_etf_profiles([crosscheck("IE00B4L5Y983")], missing=["ES0148396007"])

    output = capsys.readouterr().out
    assert "IE00B4L5Y983" in output
    assert "ES0148396007" in output


def test_collect_isins_from_portfolio(tmp_path: Path) -> None:
    """
    The portfolio file supplies the ISINs of the open positions.
    """

    path = tmp_path / "Portfolio.xlsx"
    write_portfolio(path, ["IE00B4L5Y983", "IE00BKM4GZ66", None])

    assert collect_etf_isins(path) == ["IE00B4L5Y983", "IE00BKM4GZ66"]


def test_collect_isins_deduplicates(tmp_path: Path) -> None:
    """
    A fund held twice is requested once.
    """

    path = tmp_path / "Portfolio.xlsx"
    write_portfolio(path, ["IE00B4L5Y983", "LU2641054551", "IE00B4L5Y983"])

    assert collect_etf_isins(path) == ["IE00B4L5Y983", "LU2641054551"]


def test_collect_isins_without_positions_fails(tmp_path: Path) -> None:
    """
    A portfolio with no ISIN is a usage error with a hint.
    """

    path = tmp_path / "Portfolio.xlsx"
    write_portfolio(path, [None])

    with pytest.raises(ValueError, match="XLSX de Portfolio"):
        collect_etf_isins(path)


def test_cli_etf_section_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    The ETF block of the pass renders the catalog data for a portfolio.
    """

    monkeypatch.setattr(
        justetf, "_download_catalog", lambda: json.dumps({"data": [CATALOG_ROW]})
    )
    monkeypatch.setattr(cli, "fetch_yahoo_rows", lambda isins: [])

    path = tmp_path / "Portfolio.xlsx"
    write_portfolio(path, ["IE00B4L5Y983", "ES0148396007"])

    cli.run_etf_section(argparse.Namespace(xlsx_path=path, refresh=False))

    output = capsys.readouterr().out
    assert "iShares Core MSCI World" in output
    assert "0.20" in output


def test_cli_without_a_portfolio_file_returns_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    Missing input exits with code 1 and an actionable message.
    """

    assert main(["overview"]) == 1
    assert "XLSX de Portfolio" in capsys.readouterr().err


def test_justetf_failure_degrades_to_yahoo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    A justETF outage still reports what Yahoo knows, with a warning.

    The ETF section needs the network; the rest of the analysis does
    not, so one provider being down must not abort the run.
    """

    def explode(isins: list[str], **kwargs: object) -> None:
        raise justetf.JustETFError("503 Service Unavailable")

    monkeypatch.setattr(cli, "fetch_etf_profiles", explode)
    monkeypatch.setattr(
        cli,
        "fetch_yahoo_rows",
        lambda isins: [
            YahooETFData(
                isin="IE00B4L5Y983",
                ticker="IWDA.L",
                name="iShares Core MSCI World",
                ter_pct=Decimal("0.20"),
                currency="USD",
            )
        ],
    )

    path = tmp_path / "Portfolio.xlsx"
    write_portfolio(path, ["IE00B4L5Y983"])

    cli.run_etf_section(argparse.Namespace(xlsx_path=path, refresh=False))

    assert "0.20" in capsys.readouterr().out
