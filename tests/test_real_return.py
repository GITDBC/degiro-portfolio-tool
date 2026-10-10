# Standard libraries
from collections import deque
from datetime import date, timedelta
from decimal import Decimal

# 3pps
import pytest
from rich.console import Console

# Own modules
from degiro_tools.calculation.performance import compute_performance
from degiro_tools.calculation.real_return import (
    build_real_position,
    compute_real_return,
)
from degiro_tools.domain.models import (
    InflationIndex,
    Lot,
    MarketSnapshot,
    PerformanceSummary,
    RealReturnSummary,
)
from degiro_tools.reporting import performance_report
from degiro_tools.reporting.performance_report import render_performance

TODAY: date = date(2026, 9, 15)

# A price index whose ratio is deliberately round: the reference month
# sits at 120 and the month of the old purchase at 100, so restating an
# old euro costs exactly 20% and every expected figure below can be
# written down by hand instead of copied from the implementation.
INDEX: InflationIndex = InflationIndex(
    region="es",
    region_label="España",
    reference_period="2026-08",
    values={
        "2020-09": 100.0,
        "2024-09": 110.0,
        "2026-06": 118.0,
        "2026-08": 120.0,
    },
)


def wide_console() -> Console:
    """
    Build a console wide enough not to wrap a cell under test.

    Returns:
        A Rich console with a fixed generous width.
    """

    return Console(width=240)


def days_ago(days: int) -> date:
    """
    Return the date the given number of days before the fixed today.

    Args:
        days: Number of days to go back.

    Returns:
        The resulting date.
    """

    return TODAY - timedelta(days=days)


def lot(quantity: int, unit_cost: str, day: date) -> Lot:
    """
    Build one open purchase lot.

    Args:
        quantity: Units bought.
        unit_cost: Unit cost in EUR as a decimal string.
        day: Purchase date.

    Returns:
        The lot.
    """

    return Lot(quantity=quantity, unit_cost=Decimal(unit_cost), date=day)


class TestRestatedCost:
    """
    Expressing the acquisition cost in the euros of one month.
    """

    def test_an_old_purchase_costs_more_in_todays_euros(self) -> None:
        """
        1000 EUR paid at index 100 is 1200 EUR of the month at 120.

        This is the entire point of the feature: the euros that went in
        were bigger than the ones coming out, and the nominal cost hides
        that by comparing two different units.
        """

        lots = deque([lot(10, "100.00", date(2020, 9, 15))])

        position = build_real_position("IE0001", lots, Decimal("130.00"), TODAY, INDEX)

        assert position is not None
        assert position.cost_real == Decimal("1200.00")
        assert position.market_value == Decimal("1300.00")

    def test_the_real_gain_is_smaller_than_the_nominal_one(self) -> None:
        """
        Inflation is subtracted from the result, not added to it.

        Guards the direction: dividing where the code should multiply
        would report a real gain larger than the nominal one, which is
        the kind of wrong that still looks like a number.
        """

        lots = deque([lot(10, "100.00", date(2020, 9, 15))])

        position = build_real_position("IE0001", lots, Decimal("130.00"), TODAY, INDEX)

        assert position is not None
        assert position.gain_loss == Decimal("100.00")
        # The nominal gain on the same position is 300.
        assert position.gain_loss < Decimal("300.00")

    def test_a_nominal_gain_can_be_a_real_loss(self) -> None:
        """
        More euros that buy less is reported as the loss it is.

        A position up 10% over six years while prices rose 20% has lost
        purchasing power, and the nominal figure alone would call it a
        gain.
        """

        lots = deque([lot(10, "100.00", date(2020, 9, 15))])

        position = build_real_position("IE0001", lots, Decimal("110.00"), TODAY, INDEX)

        assert position is not None
        assert position.market_value > Decimal("1000.00")
        assert position.gain_loss < 0

    def test_each_lot_is_deflated_by_its_own_month(self) -> None:
        """
        Two purchases years apart are restated at different rates.

        1000 at index 100 becomes 1200, and 1000 at index 110 becomes
        about 1090.91; a single average factor would give neither.
        """

        lots = deque(
            [
                lot(10, "100.00", date(2020, 9, 15)),
                lot(10, "100.00", date(2024, 9, 15)),
            ]
        )

        position = build_real_position("IE0001", lots, Decimal("130.00"), TODAY, INDEX)
        expected = Decimal("1200.00") + Decimal(1000) * Decimal("120.0") / Decimal(
            "110.0"
        )

        assert position is not None
        assert position.cost_real == expected


class TestCarryForward:
    """
    Purchases newer than the last published index.
    """

    def test_a_recent_purchase_is_deflated_by_one_and_flagged(self) -> None:
        """
        A month with no published index is not extrapolated.

        The index lags about six weeks, so a purchase made after the
        reference month keeps its nominal cost and the report discloses
        that it did.
        """

        lots = deque([lot(10, "100.00", date(2026, 9, 1))])

        position = build_real_position("IE0001", lots, Decimal("110.00"), TODAY, INDEX)

        assert position is not None
        assert position.cost_real == Decimal("1000.00")
        assert position.carried_forward is True

    def test_an_older_purchase_is_not_flagged(self) -> None:
        """
        Nothing is disclosed when nothing was carried forward.
        """

        lots = deque([lot(10, "100.00", date(2026, 6, 1))])

        position = build_real_position("IE0001", lots, Decimal("110.00"), TODAY, INDEX)

        assert position is not None
        assert position.carried_forward is False

    def test_one_recent_lot_flags_the_whole_position(self) -> None:
        """
        A position with a mix of months discloses the part that lagged.
        """

        lots = deque(
            [
                lot(10, "100.00", date(2020, 9, 15)),
                lot(10, "100.00", date(2026, 9, 1)),
            ]
        )

        position = build_real_position("IE0001", lots, Decimal("130.00"), TODAY, INDEX)

        assert position is not None
        assert position.carried_forward is True


class TestUncoveredPositions:
    """
    A position the index cannot restate at all.
    """

    def test_a_purchase_older_than_the_series_has_no_real_figure(self) -> None:
        """
        Half a deflated cost is worse than none, so none is reported.
        """

        lots = deque([lot(10, "100.00", date(1990, 5, 1))])

        assert build_real_position("IE0001", lots, Decimal("130.00"), TODAY, INDEX) is (
            None
        )

    def test_one_uncoverable_lot_withholds_the_whole_position(self) -> None:
        """
        A position is restated in full or not at all.

        Deflating only the lots that have an index would mix euros of
        two different months inside one cost figure.
        """

        lots = deque(
            [
                lot(10, "100.00", date(2020, 9, 15)),
                lot(10, "100.00", date(1990, 5, 1)),
            ]
        )

        assert build_real_position("IE0001", lots, Decimal("130.00"), TODAY, INDEX) is (
            None
        )

    def test_a_fully_sold_position_is_not_open(self) -> None:
        """
        No units left is not a position to value.
        """

        assert (
            build_real_position("IE0001", deque(), Decimal("1"), TODAY, INDEX) is None
        )


class TestAnnualizedRealReturn:
    """
    The annual figure, solved on the deflated purchases.
    """

    def test_the_real_rate_is_below_the_nominal_one(self) -> None:
        """
        The same solver on bigger costs yields a smaller rate.
        """

        lots = deque([lot(10, "100.00", days_ago(730))])
        index = InflationIndex(
            region="es",
            region_label="España",
            reference_period=InflationIndex.period_of(TODAY),
            values={
                InflationIndex.period_of(days_ago(730)): 100.0,
                InflationIndex.period_of(TODAY): 110.0,
            },
        )

        position = build_real_position("IE0001", lots, Decimal("121.00"), TODAY, index)

        assert position is not None
        assert position.annualized_pct is not None
        # The nominal rate on this position is exactly 10% a year.
        assert position.annualized_pct < Decimal("10.00")

    def test_a_young_position_is_not_annualized(self) -> None:
        """
        The real column is withheld under the same 90-day gate.

        Both columns are blank together, so a reader never sees a real
        annual figure beside a nominal ``n/d`` and wonders which gate
        applied.
        """

        lots = deque([lot(10, "100.00", days_ago(10))])

        position = build_real_position("IE0001", lots, Decimal("105.00"), TODAY, INDEX)

        assert position is not None
        assert position.annualized_pct is None


def summary_and_real(
    prices: dict[str, Decimal],
    lots: dict[str, deque[Lot]],
    index: InflationIndex = INDEX,
) -> tuple[PerformanceSummary, RealReturnSummary]:
    """
    Build the nominal and real summaries from one set of lots.

    Args:
        prices: Current unit price per ISIN.
        lots: Open purchase lots per ISIN.
        index: Price index to deflate with.

    Returns:
        The nominal summary and its inflation-adjusted counterpart.
    """

    snapshot = MarketSnapshot(prices, TODAY)
    summary = compute_performance(
        lots,
        snapshot,
        {isin: f"FONDO {isin}" for isin in lots},
        realized_gain_loss=Decimal("0"),
        dividend_income=Decimal("0"),
    )

    return summary, compute_real_return(lots, snapshot, index, summary.priced_isins)


class TestSummary:
    """
    Aggregating the real result over the open portfolio.
    """

    def test_the_real_total_adds_up_the_restated_costs(self) -> None:
        """
        Two positions restate to the sum of their own factors.
        """

        _, real = summary_and_real(
            {"IE0001": Decimal("130.00"), "IE0002": Decimal("130.00")},
            {
                "IE0001": deque([lot(10, "100.00", date(2020, 9, 15))]),
                "IE0002": deque([lot(10, "100.00", date(2026, 6, 1))]),
            },
        )
        expected = Decimal("1200.00") + Decimal(1000) * Decimal("120.0") / Decimal(
            "118.0"
        )

        assert real.cost_total == expected
        assert real.is_complete is True

    def test_the_real_percentage_is_over_the_real_cost(self) -> None:
        """
        The rate divides by the restated cost, not the nominal one.

        Dividing a real gain by a nominal cost would overstate the
        percentage while every figure beside it looked right.
        """

        _, real = summary_and_real(
            {"IE0001": Decimal("132.00")},
            {"IE0001": deque([lot(10, "100.00", date(2020, 9, 15))])},
        )

        assert real.cost_total == Decimal("1200.00")
        assert real.gain_loss == Decimal("120.00")
        assert real.gain_loss_pct == Decimal("10")

    def test_an_uncovered_position_leaves_a_partial_total(self) -> None:
        """
        The real total is reported partial, never voided.

        Mirrors how a partial weight base is handled: the figure that
        can be computed is shown, and what it excludes is named.
        """

        _, real = summary_and_real(
            {"IE0001": Decimal("130.00"), "IE0002": Decimal("130.00")},
            {
                "IE0001": deque([lot(10, "100.00", date(2020, 9, 15))]),
                "IE0002": deque([lot(10, "100.00", date(1990, 5, 1))]),
            },
        )

        assert real.is_complete is False
        assert real.uncovered_isins == ("IE0002",)
        assert real.cost_total == Decimal("1200.00")

    def test_the_real_market_value_excludes_what_the_total_excludes(self) -> None:
        """
        Both sides of the real result rest on the same positions.

        Keeping the nominal market value against a partial real cost
        would invent a gain out of the position that was left out.
        """

        _, real = summary_and_real(
            {"IE0001": Decimal("130.00"), "IE0002": Decimal("130.00")},
            {
                "IE0001": deque([lot(10, "100.00", date(2020, 9, 15))]),
                "IE0002": deque([lot(10, "100.00", date(1990, 5, 1))]),
            },
        )

        assert real.market_value == Decimal("1300.00")

    def test_the_positions_follow_the_nominal_display_order(self) -> None:
        """
        Both tables list the positions alike, heaviest first.
        """

        summary, real = summary_and_real(
            {"IE0001": Decimal("10.00"), "IE0002": Decimal("500.00")},
            {
                "IE0001": deque([lot(10, "100.00", date(2020, 9, 15))]),
                "IE0002": deque([lot(10, "100.00", date(2020, 9, 15))]),
            },
        )

        assert summary.priced_isins == ("IE0002", "IE0001")
        assert [position.isin for position in real.positions] == ["IE0002", "IE0001"]

    def test_the_index_provenance_travels_with_the_result(self) -> None:
        """
        The summary can say which index and which month produced it.
        """

        _, real = summary_and_real(
            {"IE0001": Decimal("130.00")},
            {"IE0001": deque([lot(10, "100.00", date(2020, 9, 15))])},
        )

        assert real.region == "es"
        assert real.region_label == "España"
        assert real.reference_period == "2026-08"


class TestRender:
    """
    What the reader actually sees in the latent result block.

    These assert on content rather than on the absence of a crash: a
    render test that only checks the call returns would pass on an empty
    table, which is the failure it is most likely to be protecting
    against.
    """

    def render(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        summary: PerformanceSummary,
        real: RealReturnSummary | None,
    ) -> str:
        """
        Render the latent result block and return what was printed.

        Args:
            monkeypatch: pytest monkeypatch fixture.
            capsys: pytest capture fixture.
            summary: Nominal result to render.
            real: Inflation-adjusted result, or None.

        Returns:
            The captured output, newlines collapsed so the assertions do
            not depend on where Rich wrapped a line.
        """

        # A wide console so Rich never wraps a cell mid-assertion.
        monkeypatch.setattr(performance_report, "Console", wide_console)
        render_performance(summary, real)

        return " ".join(capsys.readouterr().out.split())

    def positions(self) -> tuple[PerformanceSummary, RealReturnSummary]:
        """
        Build one long-held position with a known real result.

        Returns:
            The nominal and real summaries.
        """

        return summary_and_real(
            {"IE0001": Decimal("132.00")},
            {"IE0001": deque([lot(10, "100.00", date(2020, 9, 15))])},
        )

    def test_the_nominal_block_still_renders_its_figures(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        The nominal cost, value and result all reach the terminal.
        """

        summary, _ = self.positions()
        output = self.render(monkeypatch, capsys, summary, None)

        assert "Resultado latente por posición" in output
        assert "1000.00" in output
        assert "1320.00" in output
        assert "320.00" in output

    def test_the_real_rows_state_the_restated_cost_and_result(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        Both real figures appear with the month they belong to.
        """

        summary, real = self.positions()
        output = self.render(monkeypatch, capsys, summary, real)

        assert "Coste en euros de 2026-08" in output
        assert "1200.00" in output
        assert "Resultado latente real" in output
        assert "120.00" in output

    def test_the_real_column_appears_beside_the_nominal_one(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        The per-position table gains the real annual column.
        """

        summary, real = self.positions()
        output = self.render(monkeypatch, capsys, summary, real)

        assert "Anual. real" in output

    def test_the_caption_names_the_index_and_its_month(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        A real return is only auditable if its deflator is stated.
        """

        summary, real = self.positions()
        output = self.render(monkeypatch, capsys, summary, real)

        assert "IPC armonizado de España" in output
        assert "euros de 2026-08" in output

    def test_nothing_real_is_printed_without_an_index(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        With no index the block is nominal only, and says nothing more.

        The index needs the network and the nominal result does not, so
        losing it must degrade the report rather than break it.
        """

        summary, _ = self.positions()
        output = self.render(monkeypatch, capsys, summary, None)

        assert "IPC armonizado" not in output
        assert "Anual. real" not in output
        assert "Coste en euros de" not in output
        assert "Resultado latente real" not in output

    def test_the_carry_forward_is_disclosed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        A factor of one is explained, not left as a coincidence.
        """

        summary, real = summary_and_real(
            {"IE0001": Decimal("110.00")},
            {"IE0001": deque([lot(10, "100.00", date(2026, 9, 1))])},
        )
        output = self.render(monkeypatch, capsys, summary, real)

        assert "seis semanas" in output
        assert "1,00" in output

    def test_a_partial_real_total_is_disclosed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        The reader is told the real total covers only part of the account.
        """

        summary, real = summary_and_real(
            {"IE0001": Decimal("130.00"), "IE0002": Decimal("130.00")},
            {
                "IE0001": deque([lot(10, "100.00", date(2020, 9, 15))]),
                "IE0002": deque([lot(10, "100.00", date(1990, 5, 1))]),
            },
        )
        output = self.render(monkeypatch, capsys, summary, real)

        assert "Coste real parcial" in output
        assert "1 de 2 posiciones" in output
        assert "IE0002" in output

    def test_an_empty_portfolio_says_so_instead_of_rendering_a_table(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        Nothing open is a sentence, not an empty table with real rows.
        """

        summary = compute_performance(
            {},
            MarketSnapshot({}, TODAY),
            {},
            realized_gain_loss=Decimal("0"),
            dividend_income=Decimal("0"),
        )
        output = self.render(monkeypatch, capsys, summary, None)

        assert "Sin posiciones abiertas" in output
