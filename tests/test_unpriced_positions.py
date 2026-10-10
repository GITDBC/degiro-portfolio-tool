# Standard libraries

# 3pps
import polars as pl
import pytest
from rich.console import Console

# Own modules
from degiro_tools.calculation.portfolio import (
    compute_portfolio_percentage,
    describe_weight_base,
    extract_position_values,
    obtain_yahoo_info,
)
from degiro_tools.domain.models import (
    ExchangeRate,
    PriceQuote,
    PriceSource,
    PriceUnavailable,
    WeightBase,
)
from degiro_tools.providers.fx import SOURCE_ECB
from degiro_tools.reporting import analysis_report
from degiro_tools.reporting.analysis_report import (
    render_lookthrough_base_note,
    render_portfolio,
)

# Positions as the Degiro export leaves them: one euro-denominated and
# one quoted in dollars.
POSITIONS: pl.DataFrame = pl.DataFrame(
    {
        "Producto": ["IBERDROLA SA", "ISHARES CORE MSCI WORLD"],
        "Symbol/ISIN": ["ES0144580Y14", "IE00B4L5Y983"],
        "Cantidad": [100, 10],
    }
)

RATE: ExchangeRate = ExchangeRate(rate=0.861698, source=SOURCE_ECB)


def wide_console() -> Console:
    """
    Build a console wide enough not to wrap a cell under test.

    Returns:
        A Rich console with a fixed generous width.
    """

    return Console(width=200)


def stub_quotes(monkeypatch: pytest.MonkeyPatch, quotes: dict[str, PriceQuote]) -> None:
    """
    Answer every price request from a canned mapping, touching no network.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        quotes: Quote to return per ISIN.

    Returns:
        None.
    """

    def fake_quote(isin: str, _rate: float | None, _max_age: float) -> PriceQuote:
        return quotes[isin]

    monkeypatch.setattr("degiro_tools.calculation.portfolio.resolve_quote", fake_quote)


def priced(isin: str, price: float, currency: str = "EUR") -> PriceQuote:
    """
    Build a quote whose euro price resolved.

    Args:
        isin: ISIN identifier.
        price: Price in euro.
        currency: Currency the market quotes it in.

    Returns:
        A quote with no unavailability reason.
    """

    return PriceQuote(
        isin=isin,
        price_eur=price,
        native_price=price,
        currency=currency,
        unavailable=None,
        source=PriceSource.JUSTETF,
    )


def unpriced_usd(isin: str, native: float) -> PriceQuote:
    """
    Build a quote whose dollar price could not be converted.

    Args:
        isin: ISIN identifier.
        native: Price in dollars, which is known.

    Returns:
        A quote with no euro price and the exchange-rate reason set.
    """

    return PriceQuote(
        isin=isin,
        price_eur=None,
        native_price=native,
        currency="USD",
        unavailable=PriceUnavailable.NO_EXCHANGE_RATE,
    )


def build_quotes(world: PriceQuote | None = None) -> dict[str, PriceQuote]:
    """
    Build the canned quotes every valuation test starts from.

    Args:
        world: Quote for the dollar-denominated fund. Defaults to one
            whose euro price could not be resolved, which is the case
            the regression is about.

    Returns:
        One quote per ISIN present in ``POSITIONS``.
    """

    if world is None:
        world = unpriced_usd("IE00B4L5Y983", 134.5)

    return {"ES0144580Y14": priced("ES0144580Y14", 12.5), "IE00B4L5Y983": world}


def build_frame(
    monkeypatch: pytest.MonkeyPatch, quotes: dict[str, PriceQuote]
) -> pl.DataFrame:
    """
    Build a valuation frame from canned quotes.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        quotes: Quote to return per ISIN.

    Returns:
        The frame as ``build_valuation`` would leave it.
    """

    stub_quotes(monkeypatch, quotes)

    return compute_portfolio_percentage(obtain_yahoo_info(POSITIONS, RATE.rate))


class TestUnpricedPositionsSurvive:
    """
    An unvaluable position stays in the frame instead of vanishing.
    """

    def test_the_row_is_kept_with_null_euro_figures(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Both positions are present, one of them without a euro value.

        The regression this guards is the whole defect: the unpriced row
        used to be dropped, which rebased every weight in the report onto
        a subset of the account while still presenting it as the whole.
        """

        df = build_frame(monkeypatch, build_quotes())

        assert df.height == 2
        assert set(df["Symbol/ISIN"]) == {"ES0144580Y14", "IE00B4L5Y983"}

        row = df.filter(pl.col("Symbol/ISIN") == "IE00B4L5Y983").row(0, named=True)

        assert row["Precio €"] is None
        assert row["Valor €"] is None
        assert row["Porcentaje Cartera"] is None
        assert row["Cantidad"] == 10
        assert row["Precio orig."] == pytest.approx(134.5)
        assert row["Moneda"] == "USD"
        assert row["Motivo"] == "no_exchange_rate"

    def test_the_priced_arithmetic_is_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Keeping the extra row does not change any priced figure.

        The total is still the sum of what could be valued and the priced
        weights still add up to 100 over that base.
        """

        df = build_frame(monkeypatch, build_quotes())

        assert df["Total €"].item(0) == pytest.approx(1250.0)

        priced_weights = df.filter(pl.col("Porcentaje Cartera").is_not_null())

        assert priced_weights["Porcentaje Cartera"].sum() == pytest.approx(100.0)

    def test_the_lookthrough_never_sees_a_null_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Only priced positions reach the look-through weights.
        """

        df = build_frame(monkeypatch, build_quotes())

        assert extract_position_values(df) == {"ES0144580Y14": 1250.0}


class TestWeightBase:
    """
    Saying what the weights are a percentage of.
    """

    def test_a_partial_base_is_described(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """
        The base names its size, its value and who is outside it.
        """

        df = build_frame(monkeypatch, build_quotes())
        base = describe_weight_base(df)

        assert base.is_whole_portfolio is False
        assert base.priced_count == 1
        assert base.unpriced_count == 1
        assert base.position_count == 2
        assert base.unpriced_isins == ("IE00B4L5Y983",)
        assert base.market_value_eur == pytest.approx(1250.0)
        assert base.priced_share_pct == pytest.approx(50.0)

    def test_a_whole_base_needs_no_caveat(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        With every position priced the base is the portfolio.
        """

        df = build_frame(
            monkeypatch, build_quotes(priced("IE00B4L5Y983", 100.0, "USD"))
        )
        base = describe_weight_base(df)

        assert base.is_whole_portfolio is True
        assert base.unpriced_count == 0
        assert base.unpriced_isins == ()


class TestTerminalTable:
    """
    What the reader actually sees under the valuation table.
    """

    def render(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        quotes: dict[str, PriceQuote],
        rate: ExchangeRate | None = RATE,
    ) -> str:
        """
        Render the valuation table and return what was printed.

        Args:
            monkeypatch: pytest monkeypatch fixture.
            capsys: pytest capture fixture.
            quotes: Quote to return per ISIN.
            rate: Exchange rate to report under the table.

        Returns:
            The captured terminal output, newlines collapsed so the
            assertions do not depend on where Rich wrapped a line.
        """

        df = build_frame(monkeypatch, quotes)

        # A wide console so Rich never wraps a cell mid-assertion.
        monkeypatch.setattr(analysis_report, "Console", wide_console)
        render_portfolio(df, cash_eur=250.0, exchange_rate=rate)

        return " ".join(capsys.readouterr().out.split())

    def test_the_unpriced_row_reads_n_d(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        The euro and weight cells of an unpriced position are n/d.

        n/d is a real datum in this report meaning the value does not
        exist, which is not the same as a value of zero.
        """

        output = self.render(monkeypatch, capsys, build_quotes())

        assert "ISHARES CORE MSCI WORLD" in output
        assert "n/d" in output

        # Its quantity and the price its market quotes are still shown.
        assert "134.50 USD" in output

    def test_the_weight_base_is_stated_under_the_table(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        The caveat sits with the numbers, not only in the log.

        The owner read authoritative-looking weights and concluded the
        holdings no longer added up. The table itself must now say that
        the weights are over a subset and how big that subset is.
        """

        output = self.render(monkeypatch, capsys, build_quotes())

        assert "NO son sobre la cartera completa" in output
        assert "1 de 2 posiciones" in output
        assert "IE00B4L5Y983" in output
        assert "no se puede cuantificar" in output

    def test_a_fully_priced_table_carries_no_caveat(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        Nothing is disclosed when there is nothing to disclose.
        """

        output = self.render(
            monkeypatch, capsys, build_quotes(priced("IE00B4L5Y983", 100.0, "USD"))
        )

        assert "NO son sobre la cartera completa" not in output
        assert "n/d" not in output

    def test_the_rate_and_its_source_are_shown(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        The reader can see which rate produced the euro figures.
        """

        output = self.render(
            monkeypatch, capsys, build_quotes(priced("IE00B4L5Y983", 100.0, "USD"))
        )

        assert "0.861698" in output
        assert "BCE" in output

    def test_a_missing_rate_is_disclosed_as_n_d(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        With no rate at all the report says so rather than staying quiet.
        """

        output = self.render(monkeypatch, capsys, build_quotes(), rate=None)

        assert "ninguna fuente disponible" in output


class TestPriceProvenance:
    """
    Saying which provider produced each price.
    """

    def render(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        quotes: dict[str, PriceQuote],
    ) -> str:
        """
        Render the valuation table and return what was printed.

        Args:
            monkeypatch: pytest monkeypatch fixture.
            capsys: pytest capture fixture.
            quotes: Quote to return per ISIN.

        Returns:
            The captured output with newlines collapsed.
        """

        df = build_frame(monkeypatch, quotes)

        monkeypatch.setattr(analysis_report, "Console", wide_console)
        render_portfolio(df, weight_base=describe_weight_base(df), exchange_rate=RATE)

        return " ".join(capsys.readouterr().out.split())

    def from_yahoo(self, isin: str, price: float) -> PriceQuote:
        """
        Build a quote the Yahoo fallback resolved.

        Args:
            isin: ISIN identifier.
            price: Price in euro.

        Returns:
            A euro quote attributed to Yahoo.
        """

        return PriceQuote(
            isin=isin,
            price_eur=price,
            native_price=price,
            currency="EUR",
            unavailable=None,
            source=PriceSource.YAHOO,
        )

    def test_a_single_source_is_stated_once_under_the_table(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        One provider for every row costs one line, not a column.

        A per-row column would spend terminal width repeating the same
        name on every position, which is width the euro figures need.
        """

        output = self.render(
            monkeypatch, capsys, build_quotes(priced("IE00B4L5Y983", 100.0))
        )

        assert "Precios: justETF" in output
        assert "Fuente" not in output

    def test_two_sources_earn_a_column_and_a_count(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        When the rows disagree, the reader can see which is which.

        This is the case the provenance exists for: one provider covered
        part of the account and the other covered the rest, and a single
        summary line could not say which row came from where.
        """

        output = self.render(
            monkeypatch, capsys, build_quotes(self.from_yahoo("IE00B4L5Y983", 100.0))
        )

        assert "Fuente" in output
        assert "justETF" in output
        assert "Yahoo" in output

    def test_an_unpriced_position_attributes_nothing(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        A row with no price has no provenance to claim.

        It must not inherit the source of the rows that did resolve,
        which is how a missing figure starts looking like a real one.
        """

        output = self.render(monkeypatch, capsys, build_quotes())

        assert "Precios: justETF" in output
        assert "Fuente" not in output


class TestLookthroughDisclosure:
    """
    The sector and geography tables inherit the distortion, and say so.
    """

    def note(self, base: WeightBase | None, capsys: pytest.CaptureFixture[str]) -> str:
        """
        Render the look-through caveat and return what was printed.

        Args:
            base: What the underlying weights are relative to.
            capsys: pytest capture fixture.

        Returns:
            The captured output with newlines collapsed.
        """

        render_lookthrough_base_note(wide_console(), base)

        return " ".join(capsys.readouterr().out.split())

    def test_the_unclassified_gap_is_attributed_to_the_missing_price(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        A gap caused by a missing rate must not read as a real allocation.

        Without this, a large "No clasificado" slice looks like a genuine
        property of the portfolio rather than the symptom of an exchange
        rate the run could not fetch.
        """

        output = self.note(
            WeightBase(
                priced_count=3,
                unpriced_count=4,
                unpriced_isins=("IE00BKM4GZ66", "IE00B4L5Y983"),
                market_value_eur=1000.0,
            ),
            capsys,
        )

        assert "Base parcial" in output
        assert "3 de 7 posiciones" in output
        assert "IE00BKM4GZ66" in output
        assert "no clasificado" in output

    def test_a_whole_base_prints_nothing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        A complete look-through base needs no explanation.
        """

        base = WeightBase(
            priced_count=7,
            unpriced_count=0,
            unpriced_isins=(),
            market_value_eur=1000.0,
        )

        assert self.note(base, capsys) == ""

    def test_an_absent_base_prints_nothing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        A caller with no base to report stays silent rather than guessing.
        """

        assert self.note(None, capsys) == ""
