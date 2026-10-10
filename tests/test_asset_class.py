# Standard libraries
import json
from pathlib import Path
from unittest.mock import MagicMock

# 3pps
import polars as pl
import pytest

# Own modules
import degiro_tools.analysis.holdings as analysis_holdings
import degiro_tools.calculation.portfolio as calculation_portfolio
from degiro_tools.analysis.holdings import collect_holdings, declared_class_holding
from degiro_tools.analysis.sectors import compute_sectors
from degiro_tools.calculation.portfolio import obtain_yahoo_info
from degiro_tools.domain.holdings import HoldingsConfig
from degiro_tools.domain.models import PriceQuote, PriceSource
from degiro_tools.domain.portfolio import (
    OUTPUT_ASSET_CLASS_COL,
    OUTPUT_CURRENCY_COL,
    OUTPUT_ISIN_COL,
    OUTPUT_NATIVE_PRICE_COL,
    OUTPUT_PERCENTAGE_COL,
    OUTPUT_PRICE_COL,
    OUTPUT_PRODUCT_COL,
    OUTPUT_QUANTITY_COL,
    OUTPUT_TOTAL_COL,
    OUTPUT_TYPE_COL,
    OUTPUT_VALUE_COL,
    AssetClass,
)
from degiro_tools.domain.vocabulary import UNCLASSIFIED_LABEL
from degiro_tools.providers.holdings.registry import load_holdings_config
from degiro_tools.reporting.analysis_report import render_portfolio

# A money-market fund whose product name carries no bond token, which
# is exactly why the asset class has to be declared instead of inferred.
MONETARY_ISIN: str = "LU0290358497"
MONETARY_NAME: str = "XTRACKERS II EUR OVERNIGHT RATE SWAP UCITS ETF 1C"

# An equity fund from a brand ``ETF_LIST_NAME`` does not list, so
# ``is_etf`` cannot recognize it from its name.
UNLISTED_NAME: str = "SOME OBSCURE ISSUER BOND UCITS ETF"

EQUITY_ISIN: str = "IE00B4L5Y983"
FULL_PCT: float = 100.0

# A crypto ETP holding one asset: ``is_etf`` does recognize its brand,
# so before it had a class of its own it was reported as an ETF with no
# holdings file and its weight fell into the unclassified residual.
CRYPTO_ISIN: str = "XS2940466316"
CRYPTO_NAME: str = "ISHARES BITCOIN ETP"

# Width the reports are laid out for, which the asset class line shares
# with nothing else and must still fit once a third class appears.
MAX_TERMINAL_WIDTH: int = 80


def write_config(tmp_path: Path, payload: object) -> Path:
    """
    Write a holdings config file with the given JSON payload.

    Args:
        tmp_path: Temporary directory provided by pytest.
        payload: Object serialized as the config contents.

    Returns:
        Path to the written config file.
    """

    path = tmp_path / "holdings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    return path


def positions(*rows: tuple[str, str]) -> pl.DataFrame:
    """
    Build a Portfolio frame with the columns ``collect_holdings`` reads.

    Args:
        rows: Pairs of product name and ISIN.

    Returns:
        DataFrame with the product, ISIN and quantity columns.
    """

    return pl.DataFrame(
        {
            "Producto": [product for product, _isin in rows],
            "Symbol/ISIN": [isin for _product, isin in rows],
            "Cantidad": [1] * len(rows),
        }
    )


def test_load_holdings_config_reads_the_two_original_sections(tmp_path: Path) -> None:
    """
    The original two-section config yields files for equity and classes
    for fixed income, unchanged by the arrival of further sections.

    The equity section keeps mapping ISIN to a path resolved against
    the config's own directory, while the fixed income section declares
    only ISINs and contributes no file at all. A config written before
    any other class existed must keep loading exactly like this, which
    is what the absence of a crypto entry below asserts.
    """

    config_path = write_config(
        tmp_path,
        {
            "renta_variable": {EQUITY_ISIN: "holdings/msci_world.xls"},
            "renta_fija": [MONETARY_ISIN],
        },
    )

    config = load_holdings_config(config_path)

    assert config.files == {EQUITY_ISIN: tmp_path / "holdings/msci_world.xls"}
    assert config.classes == {MONETARY_ISIN: AssetClass.FIXED_INCOME}
    assert config.asset_class(MONETARY_ISIN) is AssetClass.FIXED_INCOME
    assert config.asset_class(EQUITY_ISIN) is AssetClass.EQUITY
    assert MONETARY_ISIN not in config.files


def test_load_holdings_config_merges_every_class_section(tmp_path: Path) -> None:
    """
    Each ISIN-list section contributes its own class to one mapping.

    The sections are read through the same path, so the crypto section
    needs no expansion file either and the two declarations land side by
    side instead of one overwriting the other.
    """

    config_path = write_config(
        tmp_path,
        {
            "renta_fija": [MONETARY_ISIN],
            "criptomonedas": [CRYPTO_ISIN],
        },
    )

    config = load_holdings_config(config_path)

    assert config.classes == {
        MONETARY_ISIN: AssetClass.FIXED_INCOME,
        CRYPTO_ISIN: AssetClass.CRYPTO,
    }
    assert config.files == {}
    assert config.asset_class(CRYPTO_ISIN) is AssetClass.CRYPTO


def test_load_holdings_config_accepts_missing_sections(tmp_path: Path) -> None:
    """
    Either section may be absent, and an empty object is an empty
    config rather than an error.
    """

    only_equity = load_holdings_config(
        write_config(tmp_path, {"renta_variable": {EQUITY_ISIN: "a.xls"}})
    )

    assert only_equity.classes == {}
    assert set(only_equity.files) == {EQUITY_ISIN}

    empty = load_holdings_config(write_config(tmp_path, {}))

    assert empty.files == {}
    assert empty.classes == {}


def test_load_holdings_config_rejects_old_flat_shape(tmp_path: Path) -> None:
    """
    A config still written flat is named instead of silently ignored.

    Reading the flat shape as a section-less object would drop every
    declaration the user wrote, so the error points at the migration.
    """

    config_path = write_config(tmp_path, {EQUITY_ISIN: "holdings/msci_world.xls"})

    with pytest.raises(ValueError, match="old flat format") as err:
        load_holdings_config(config_path)

    assert "renta_variable" in str(err.value)
    assert EQUITY_ISIN in str(err.value)


@pytest.mark.parametrize("section", ["renta_fija", "criptomonedas"])
def test_load_holdings_config_rejects_a_file_under_a_class_section(
    tmp_path: Path,
    section: str,
) -> None:
    """
    An ISIN-list section takes ISINs only, never files.

    A position declared there is never expanded, so a constituents file
    for it would never be read and accepting one would promise a
    breakdown that never arrives. Every such section is validated by
    the same helper, which is what makes this hold for a class added
    later.
    """

    config_path = write_config(
        tmp_path, {section: {MONETARY_ISIN: "holdings/monetary.xlsx"}}
    )

    with pytest.raises(ValueError, match="must be a list of ISIN") as err:
        load_holdings_config(config_path)

    assert section in str(err.value)


def test_load_holdings_config_rejects_an_isin_declared_in_two_sections(
    tmp_path: Path,
) -> None:
    """
    An ISIN belongs to one section only, and a repeat is an error.

    Keeping one of the two declarations would make the reported class
    depend on the order the sections happen to be read in, which the
    config never states, so the contradiction is handed back to the user
    naming both sections. It holds for two class sections and for a
    class section against the equity one.
    """

    with pytest.raises(ValueError, match="one asset class") as err:
        load_holdings_config(
            write_config(
                tmp_path,
                {"renta_fija": [CRYPTO_ISIN], "criptomonedas": [CRYPTO_ISIN]},
            )
        )

    assert CRYPTO_ISIN in str(err.value)
    assert "renta_fija" in str(err.value)
    assert "criptomonedas" in str(err.value)

    with pytest.raises(ValueError, match="one asset class"):
        load_holdings_config(
            write_config(
                tmp_path,
                {
                    "renta_variable": {CRYPTO_ISIN: "holdings/fund.xls"},
                    "criptomonedas": [CRYPTO_ISIN],
                },
            )
        )


def test_load_holdings_config_missing_file_shows_the_new_format(
    tmp_path: Path,
) -> None:
    """
    The not-found message documents every section of the format.
    """

    with pytest.raises(FileNotFoundError, match="renta_variable") as err:
        load_holdings_config(tmp_path / "absent.json")

    assert "renta_fija" in str(err.value)
    assert "criptomonedas" in str(err.value)


def test_fixed_income_position_emits_one_passthrough_holding() -> None:
    """
    A declared fixed income position enters as a single holding.

    It is not expanded, carries its asset class as its sector so the
    sector table accounts for it, and keeps an empty location so the
    geographic breakdown still leaves it out.
    """

    config = HoldingsConfig(classes={MONETARY_ISIN: AssetClass.FIXED_INCOME})

    holdings = collect_holdings(positions((MONETARY_NAME, MONETARY_ISIN)), config)

    assert len(holdings) == 1
    assert holdings[0].name == MONETARY_NAME
    assert holdings[0].sector == AssetClass.FIXED_INCOME.value
    assert holdings[0].location == ""
    assert holdings[0].weight_pct == FULL_PCT
    assert holdings[0].source_isin == MONETARY_ISIN


def test_crypto_position_passes_through_without_the_missing_file_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    A declared crypto ETP enters whole and stops being an ETF with no
    holdings file.

    ``is_etf`` recognizes the brand, so undeclared the position was
    reported as a fund whose constituents file is missing and its weight
    was left in the unclassified residual. Declaring the class must be
    evaluated before that check: the ETP holds one asset and will never
    have a constituents file worth reading.
    """

    df = positions((CRYPTO_NAME, CRYPTO_ISIN))

    with caplog.at_level("WARNING"):
        undeclared = collect_holdings(df, HoldingsConfig())

    assert undeclared == []
    assert "Sin fichero de holdings" in caplog.text

    caplog.clear()

    with caplog.at_level("WARNING"):
        holdings = collect_holdings(
            df, HoldingsConfig(classes={CRYPTO_ISIN: AssetClass.CRYPTO})
        )

    assert "Sin fichero de holdings" not in caplog.text
    assert len(holdings) == 1
    assert holdings[0].name == CRYPTO_NAME
    assert holdings[0].sector == AssetClass.CRYPTO.value
    assert holdings[0].location == ""
    assert holdings[0].weight_pct == FULL_PCT
    assert holdings[0].source_isin == CRYPTO_ISIN


def test_fixed_income_is_not_expanded_as_a_single_equity_holding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Bug H1: a fund whose brand ``is_etf`` does not know is no longer
    taken for a stock once it is declared fixed income.

    Undeclared, such a position fell through to ``fetch_stock_holding``
    and wrote 100% of its weight into whatever sector and country Yahoo
    publishes for the fund's own ticker. The declaration must short
    that path out before any provider is consulted.
    """

    stock = MagicMock()
    monkeypatch.setattr(analysis_holdings, "fetch_stock_holding", stock)

    df = positions((UNLISTED_NAME, MONETARY_ISIN))

    undeclared = collect_holdings(df, HoldingsConfig())

    # Without the declaration the position is still taken as a stock,
    # which is what the aggregate log now discloses.
    assert stock.call_count == 1
    assert len(undeclared) == 1

    stock.reset_mock()

    declared = collect_holdings(
        df, HoldingsConfig(classes={MONETARY_ISIN: AssetClass.FIXED_INCOME})
    )

    stock.assert_not_called()
    assert [h.sector for h in declared] == [AssetClass.FIXED_INCOME.value]


def test_undeclared_single_stocks_are_logged_once_in_aggregate(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """
    The positions taken as individual stocks are counted, not narrated.

    One line per position would repeat the same caveat as many times as
    the account has stocks; one aggregate line states the count and
    names them.
    """

    monkeypatch.setattr(
        analysis_holdings,
        "fetch_stock_holding",
        lambda product, isin: declared_class_holding(product, isin, AssetClass.EQUITY),
    )

    df = positions(("ACME INC", "US0000000001"), ("GLOBEX SA", "US0000000002"))

    with caplog.at_level("INFO"):
        collect_holdings(df, HoldingsConfig())

    aggregate = [
        record for record in caplog.records if "acciones individuales" in record.message
    ]

    assert len(aggregate) == 1
    assert "US0000000001" in caplog.text
    assert "US0000000002" in caplog.text


def test_sector_breakdown_carries_fixed_income_and_shrinks_the_residual() -> None:
    """
    Finding F3: the fixed income row takes its weight out of the
    residual.

    The synthetic holding arrives as an ordinary holding, so
    ``compute_sectors`` needs no change: it gains a "Renta fija" row of
    exactly the position's portfolio weight and ``No clasificado`` drops
    by the same amount, going back to meaning "data missing".
    """

    monetary_pct = 9.19
    equity_pct = 80.0

    df_without = pl.DataFrame(
        {
            "sector": ["Information Technology"],
            "effective_pct": [equity_pct],
        }
    )
    df_with = pl.DataFrame(
        {
            "sector": ["Information Technology", AssetClass.FIXED_INCOME.value],
            "effective_pct": [equity_pct, monetary_pct],
        }
    )

    def residual(df: pl.DataFrame) -> float:
        return float(
            compute_sectors(df)
            .filter(pl.col("sector") == UNCLASSIFIED_LABEL)["effective_pct"]
            .sum()
        )

    result = compute_sectors(df_with)
    fixed_income = result.filter(pl.col("sector") == AssetClass.FIXED_INCOME.value)[
        "effective_pct"
    ]

    assert fixed_income.to_list() == [pytest.approx(monetary_pct)]
    assert residual(df_with) == pytest.approx(residual(df_without) - monetary_pct)


def test_valuation_frame_carries_the_declared_asset_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The valuation frame states the asset class beside the wrapper type.

    Both axes travel: a money-market ETF is an "ETF" in the type column
    and "Renta fija" in the class column, and conflating the two is what
    let a fixed income position be reported as equity.
    """

    monkeypatch.setattr(
        calculation_portfolio,
        "resolve_quote",
        lambda isin, rate, max_age: PriceQuote(
            isin=isin,
            price_eur=10.0,
            native_price=10.0,
            currency="EUR",
            unavailable=None,
            source=PriceSource.JUSTETF,
        ),
    )

    df = obtain_yahoo_info(
        positions((MONETARY_NAME, MONETARY_ISIN), ("ACME INC", "US0000000001")),
        asset_classes={MONETARY_ISIN: AssetClass.FIXED_INCOME},
    )

    classes = dict(
        zip(
            df[OUTPUT_ISIN_COL].to_list(),
            df[OUTPUT_ASSET_CLASS_COL].to_list(),
            strict=True,
        )
    )

    assert classes == {
        MONETARY_ISIN: AssetClass.FIXED_INCOME.value,
        "US0000000001": AssetClass.EQUITY.value,
    }
    assert df[OUTPUT_TYPE_COL].to_list() == ["ETF", "Accion"]


def valuation_frame(classes: list[str]) -> pl.DataFrame:
    """
    Build the minimal valuation frame ``render_portfolio`` reads.

    Args:
        classes: Asset class of each position, one per row.

    Returns:
        Frame with the columns the valuation table and its subtotal
        line consume.
    """

    share = FULL_PCT / len(classes)
    value = 1000.0

    return pl.DataFrame(
        {
            OUTPUT_PRODUCT_COL: [f"POS {i}" for i in range(len(classes))],
            OUTPUT_ISIN_COL: [f"ISIN{i}" for i in range(len(classes))],
            OUTPUT_ASSET_CLASS_COL: classes,
            OUTPUT_QUANTITY_COL: [1] * len(classes),
            OUTPUT_PRICE_COL: [value] * len(classes),
            OUTPUT_VALUE_COL: [value] * len(classes),
            OUTPUT_NATIVE_PRICE_COL: [value] * len(classes),
            OUTPUT_CURRENCY_COL: ["EUR"] * len(classes),
            OUTPUT_TOTAL_COL: [value * len(classes)] * len(classes),
            OUTPUT_PERCENTAGE_COL: [share] * len(classes),
        }
    )


def rendered(df: pl.DataFrame, capsys: pytest.CaptureFixture[str]) -> str:
    """
    Render the valuation table and return its output on one line.

    Args:
        df: Valuation frame to render.
        capsys: pytest capture fixture.

    Returns:
        The captured output with runs of whitespace collapsed, so the
        assertions do not depend on the table's column widths.
    """

    render_portfolio(df)

    return " ".join(capsys.readouterr().out.split())


def test_valuation_subtotal_states_both_asset_classes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    The split is one line under the table, heaviest class first.

    A line rather than a column: the table already narrows the product
    name to fit an 80-column terminal, so there is no width left.
    """

    df = valuation_frame(
        [
            AssetClass.FIXED_INCOME.value,
            AssetClass.EQUITY.value,
            AssetClass.EQUITY.value,
        ]
    )

    assert "Renta variable 66.67% · Renta fija 33.33%" in rendered(df, capsys)


def test_valuation_subtotal_lists_every_class_within_the_terminal_width(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    The split grows a term per declared class and still fits 80 columns.

    The line is not limited to two classes, and the raw output is read
    here rather than the whitespace-collapsed form so that a line too
    long to fit, which Rich would wrap, fails instead of passing.
    """

    df = valuation_frame(
        [AssetClass.EQUITY.value] * 3
        + [AssetClass.FIXED_INCOME.value] * 2
        + [AssetClass.CRYPTO.value]
    )

    render_portfolio(df)

    split_lines = [
        line
        for line in capsys.readouterr().out.splitlines()
        if AssetClass.CRYPTO.value in line
    ]

    assert len(split_lines) == 1
    assert split_lines[0].strip() == (
        "Renta variable 50.00% · Renta fija 33.33% · Criptomonedas 16.67%"
    )
    assert len(split_lines[0]) <= MAX_TERMINAL_WIDTH


def test_valuation_subtotal_is_silent_on_a_single_asset_class(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    A lone "Renta variable 100.00%" states nothing the table does not,
    so no line is printed for it.
    """

    output = rendered(valuation_frame([AssetClass.EQUITY.value]), capsys)

    assert AssetClass.EQUITY.value not in output
    assert AssetClass.FIXED_INCOME.value not in output
