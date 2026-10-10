# Standard libraries
import json
from decimal import Decimal

# 3pps
import pytest

# Own modules
from degiro_tools.providers import justetf
from degiro_tools.providers.justetf import (
    JustETFError,
    build_profile,
    fetch_etf_catalog,
    fetch_etf_profiles,
)

# One screener row with every field justETF publishes for a fund that
# does have history, shaped exactly as the real payload: percentages
# with a sign, thousands with a comma and HTML breaks inside the
# replication method.
WORLD_ROW: dict[str, object] = {
    "isin": "IE00B4L5Y983",
    "name": "iShares Core MSCI World UCITS ETF USD (Acc)",
    "ticker": "IWDA",
    "ter": "0.20%",
    "fundSize": "128,942",
    "ytdReturnCUR": "16.60%",
    "yearReturnCUR": "23.78%",
    "threeYearReturnCUR": "68.38%",
    "fiveYearReturnCUR": "77.09%",
    "yearVolatilityCUR": "13.10%",
    "threeYearVolatilityCUR": "13.01%",
    "threeYearMaxDrawdownCUR": "-20.45%",
    "threeYearReturnPerRiskCUR": "1.46",
    "replicationMethod": "Optimized<br />sampling",
    "distributionPolicy": "Accumulating",
    "domicileCountry": "Ireland",
    "hasSecuritiesLending": "Yes",
}

# A young fund: justETF reports the missing history as a dash.
YOUNG_ROW: dict[str, object] = {
    "isin": "LU2641054551",
    "name": "Xtrackers II Germany Government Bond 0-1 UCITS ETF 1C",
    "ticker": "XG01",
    "ter": "0.07%",
    "fundSize": "510",
    "ytdReturnCUR": "1.27%",
    "yearReturnCUR": "1.95%",
    "threeYearReturnCUR": "-",
    "fiveYearReturnCUR": "-",
    "yearVolatilityCUR": "0.30%",
    "threeYearVolatilityCUR": "-",
    "threeYearMaxDrawdownCUR": "-",
    "threeYearReturnPerRiskCUR": "-",
    "replicationMethod": "Optimized<br />sampling",
    "distributionPolicy": "Accumulating",
    "domicileCountry": "Luxembourg",
    "hasSecuritiesLending": "No",
}


def payload(*rows: dict[str, object]) -> str:
    """
    Build a screener payload carrying the given rows.

    Args:
        rows: Raw screener rows.

    Returns:
        JSON document as text.
    """

    return json.dumps({"recordsFiltered": len(rows), "data": list(rows)})


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Make any unexpected download fail instead of hitting the network.

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        None.
    """

    def _forbidden() -> str:
        raise AssertionError("the test must not reach the network")

    monkeypatch.setattr(justetf, "_download_catalog", _forbidden)


def stub_download(monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    """
    Replace the catalog download with a fixed response body.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        body: Response body to return.

    Returns:
        None.
    """

    monkeypatch.setattr(justetf, "_download_catalog", lambda: body)


def test_build_profile_parses_human_formatted_numbers() -> None:
    """
    Percentages, thousands separators and HTML breaks are normalized.
    """

    profile = build_profile(WORLD_ROW)

    assert profile is not None
    assert profile.isin == "IE00B4L5Y983"
    assert profile.ticker == "IWDA"
    assert profile.ter_pct == Decimal("0.20")
    assert profile.fund_size_meur == Decimal("128942")
    assert profile.return_1y_pct == Decimal("23.78")
    assert profile.max_drawdown_3y_pct == Decimal("-20.45")
    assert profile.return_per_risk_3y == Decimal("1.46")
    assert profile.replication == "Muestreo optimizado"
    assert profile.securities_lending is True


def test_missing_history_becomes_none_not_zero() -> None:
    """
    A dash means no measurement, which must not be read as a zero.
    """

    profile = build_profile(YOUNG_ROW)

    assert profile is not None
    assert profile.return_3y_pct is None
    assert profile.volatility_3y_pct is None
    assert profile.max_drawdown_3y_pct is None
    assert profile.return_per_risk_3y is None
    assert profile.return_1y_pct == Decimal("1.95")
    assert profile.securities_lending is False


def test_build_profile_without_isin_is_discarded() -> None:
    """
    A row with no ISIN cannot be matched to a position.
    """

    assert build_profile({"name": "Sin ISIN"}) is None


def test_unparsable_number_becomes_none() -> None:
    """
    A non-numeric cell degrades to None instead of raising.
    """

    profile = build_profile({**WORLD_ROW, "ter": "n.a."})

    assert profile is not None
    assert profile.ter_pct is None


def test_catalog_is_indexed_by_isin(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The catalog maps every parsed ISIN to its profile.
    """

    stub_download(monkeypatch, payload(WORLD_ROW, YOUNG_ROW))

    catalog = fetch_etf_catalog()

    assert set(catalog) == {"IE00B4L5Y983", "LU2641054551"}
    assert catalog["IE00B4L5Y983"].ter_pct == Decimal("0.20")


def test_catalog_skips_malformed_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Rows that are not objects do not abort the whole download.
    """

    body = json.dumps({"data": ["not a row", WORLD_ROW, 42]})
    stub_download(monkeypatch, body)

    catalog = fetch_etf_catalog()

    assert list(catalog) == ["IE00B4L5Y983"]


def test_profiles_keep_the_requested_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Results follow the order of the requested ISINs, not the catalog.
    """

    stub_download(monkeypatch, payload(WORLD_ROW, YOUNG_ROW))

    found, missing = fetch_etf_profiles(["LU2641054551", "IE00B4L5Y983"])

    assert [profile.isin for profile in found] == ["LU2641054551", "IE00B4L5Y983"]
    assert missing == []


def test_uncovered_isins_are_reported_not_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    An ISIN outside the screener is listed apart, without failing.
    """

    stub_download(monkeypatch, payload(WORLD_ROW))

    found, missing = fetch_etf_profiles(["IE00B4L5Y983", "ES0148396007"])

    assert [profile.isin for profile in found] == ["IE00B4L5Y983"]
    assert missing == ["ES0148396007"]


def test_html_response_raises_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    An HTML page means the endpoint changed, and it is said so.
    """

    stub_download(monkeypatch, "<html><body>nope</body></html>")

    with pytest.raises(JustETFError, match="no devolvió JSON"):
        fetch_etf_catalog()


def test_payload_without_table_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Valid JSON with no data key is reported as a missing table.
    """

    stub_download(monkeypatch, json.dumps({"recordsFiltered": 0}))

    with pytest.raises(JustETFError, match="no contiene la tabla"):
        fetch_etf_catalog()


def test_non_object_payload_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    A JSON array instead of an object is an unexpected format.
    """

    stub_download(monkeypatch, json.dumps([1, 2, 3]))

    with pytest.raises(JustETFError, match="formato inesperado"):
        fetch_etf_catalog()


def test_empty_catalog_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    An empty table is an error, not a portfolio without ETFs.
    """

    stub_download(monkeypatch, payload())

    with pytest.raises(JustETFError, match="catálogo vacío"):
        fetch_etf_catalog()


def test_transport_failure_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    A network error surfaces as JustETFError, not as OSError.
    """

    class _FailingOpener:
        addheaders: list[tuple[str, str]] = []

        def open(self, *args: object, **kwargs: object) -> object:
            raise TimeoutError("connection timed out")

    monkeypatch.setattr(justetf, "_open_session", _FailingOpener)

    with pytest.raises(JustETFError, match="no se pudo consultar justETF"):
        fetch_etf_catalog()


def test_offline_fixture_blocks_the_network(offline: None) -> None:
    """
    The offline guard turns an unstubbed download into a failure.
    """

    with pytest.raises(AssertionError, match="must not reach the network"):
        fetch_etf_catalog()


def test_vocabularies_are_translated() -> None:
    """
    justETF publishes closed vocabularies in English; we show Spanish.

    Translating at the provider boundary keeps the tables and the CSV
    in one language instead of mixing them.
    """

    profile = justetf.build_profile(
        {
            "isin": "IE00B4L5Y983",
            "replicationMethod": "Full replication",
            "distributionPolicy": "Distributing",
            "domicileCountry": "Luxembourg",
        }
    )

    assert profile.replication == "Réplica física total"
    assert profile.distribution_policy == "Reparto"
    assert profile.domicile == "Luxemburgo"


def test_unknown_vocabulary_passes_through() -> None:
    """
    A category the source adds later is shown as-is, not dropped.
    """

    profile = justetf.build_profile(
        {"isin": "X", "replicationMethod": "Quantum replication"}
    )

    assert profile.replication == "Quantum replication"
