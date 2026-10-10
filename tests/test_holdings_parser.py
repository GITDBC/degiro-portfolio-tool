# Standard libraries
import logging
from pathlib import Path

# 3pps
import openpyxl
import pytest

# Own modules
from degiro_tools.domain.constants import CONTINENT_MAP, COUNTRY_MAP, SECTOR_MAP
from degiro_tools.domain.holdings import Holding, normalize_holding
from degiro_tools.providers.holdings import (
    PARSERS,
    fetch_holdings,
    parse_holdings_file,
)
from degiro_tools.providers.holdings.helpers import parse_weight
from degiro_tools.providers.holdings.ishares import ISharesParser
from degiro_tools.providers.holdings.vanguard import VanguardParser
from degiro_tools.providers.holdings.xtrackers import XtrackersParser

ISIN = "TEST_ISIN"

EXPECTED_ISHARES_HOLDINGS = 2
EXPECTED_VANGUARD_HOLDINGS = 2
EXPECTED_XTRACKERS_EQUITIES = 3

# The ISO 3166-1 alpha-2 half of COUNTRY_MAP, derived from the table
# instead of restated, so the structural tests below fail when the table
# is incomplete rather than when a second copy of it drifts.
ISO2_COUNTRY_MAP: dict[str, str] = {
    code: country
    for code, country in COUNTRY_MAP.items()
    if len(code) == 2 and code.isupper()
}

ISHARES_XML = """<?xml version="1.0"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
 <Worksheet ss:Name="Holdings">
  <Table>
   <Row><Cell><Data>iShares fund preamble</Data></Cell></Row>
   <Row>
    <Cell><Data>Name</Data></Cell>
    <Cell><Data>Ticker</Data></Cell>
    <Cell><Data>Sector</Data></Cell>
    <Cell><Data>Asset Class</Data></Cell>
    <Cell><Data>Weight (%)</Data></Cell>
    <Cell><Data>Location</Data></Cell>
   </Row>
   <Row>
    <Cell><Data>APPLE INC</Data></Cell>
    <Cell><Data>AAPL</Data></Cell>
    <Cell><Data>Information Technology</Data></Cell>
    <Cell><Data>Equity</Data></Cell>
    <Cell><Data>5.34</Data></Cell>
    <Cell><Data>United States</Data></Cell>
   </Row>
   <Row>
    <Cell><Data>NESTLE SA</Data></Cell>
    <Cell><Data>NESN</Data></Cell>
    <Cell><Data>Consumer Staples</Data></Cell>
    <Cell><Data>Equity</Data></Cell>
    <Cell><Data>1.20</Data></Cell>
    <Cell><Data>Switzerland</Data></Cell>
   </Row>
   <Row>
    <Cell><Data>USD CASH</Data></Cell>
    <Cell><Data>CASH</Data></Cell>
    <Cell><Data></Data></Cell>
    <Cell><Data>Cash</Data></Cell>
    <Cell><Data>0.50</Data></Cell>
    <Cell><Data>United States</Data></Cell>
   </Row>
  </Table>
 </Worksheet>
</Workbook>
"""


# SpreadsheetML payload carrying a forbidden internal entity declaration.
# defusedxml must refuse to expand it; stdlib ElementTree would not.
ISHARES_XXE_XML = """<?xml version="1.0"?>
<!DOCTYPE Workbook [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;">
]>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet">
 <Worksheet ss:Name="Holdings">
  <Table>
   <Row><Cell><Data>boom</Data></Cell></Row>
  </Table>
 </Worksheet>
</Workbook>
"""


def write_xlsx(path: Path, sheet_name: str, rows: list[list[object]]) -> Path:
    """
    Write rows to a single-sheet XLSX fixture.

    Args:
        path: Destination .xlsx path.
        sheet_name: Name of the worksheet.
        rows: Rows to append, each a list of cell values.

    Returns:
        Path to the written file.
    """

    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = sheet_name

    for row in rows:
        worksheet.append(row)

    workbook.save(path)
    return path


@pytest.fixture
def ishares_file(tmp_path: Path) -> Path:
    """
    Write a minimal iShares SpreadsheetML fixture.
    """

    path = tmp_path / "ishares.xls"
    path.write_text(ISHARES_XML, encoding="utf-8")
    return path


@pytest.fixture
def vanguard_file(tmp_path: Path) -> Path:
    """
    Write a minimal Vanguard XLSX fixture.
    """

    rows: list[list[object]] = [
        ["This file was downloaded on 27 Jun 2026"],
        [],
        ["Holdings details"],
        ["Vanguard FTSE Developed Europe UCITS ETF"],
        ["As at 31 May 2026"],
        [],
        [
            "Ticker",
            "Holding name",
            "% of market value",
            "Sector",
            "Region",
            "Market value",
            "Shares",
        ],
        [
            "ASML",
            "ASML Holding NV",
            "4.2547%",
            "Technology",
            "NL",
            "€315,368,812.80",
            "227,736",
        ],
        [
            "NESN",
            "Nestle SA",
            "3.1000%",
            "Consumer Defensive",
            "CH",
            "€200,000,000.00",
            "100,000",
        ],
    ]
    return write_xlsx(tmp_path / "vanguard.xlsx", "Holdings details", rows)


@pytest.fixture
def xtrackers_file(tmp_path: Path) -> Path:
    """
    Write a minimal Xtrackers/DWS XLSX fixture with weights.
    """

    rows: list[list[object]] = [
        [],
        ["Aviso legal en espanol ..."],
        [],
        [
            None,
            "Name",
            "ISIN",
            "Country",
            "Currency",
            "Exchange",
            "Type of Security",
            "Rating",
            "Primary Listing",
            "Industry Classification",
            "Weighting",
        ],
        [
            1,
            "NEXTERA ENERGY INC",
            "US65339F1012",
            "Estados Unidos",
            "USD",
            "NYSE",
            "Renta Variable",
            "Baa1",
            "S&P 500",
            "Suministro Eléctrico",
            0.50,
        ],
        [
            2,
            "IBERDROLA SA",
            "ES0144580Y14",
            "España",
            "EUR",
            "Continuo",
            "Renta Variable",
            "Baa1",
            "IBEX 35",
            "Multiservicios ",
            0.30,
        ],
        [
            3,
            "ENEL SPA",
            "IT0003128367",
            "Italia",
            "EUR",
            "Borsa",
            "Renta Variable",
            "-",
            "FTSE MIB",
            "Energías Alternativas",
            0.20,
        ],
        [
            4,
            "USD CASH",
            "_CURRENCYUSD",
            "Estados Unidos",
            "USD",
            "-",
            "Cash",
            "-",
            "-",
            "desconocido",
            0.0001,
        ],
    ]
    return write_xlsx(tmp_path / "Constituent_TEST.xlsx", "2026-06-29", rows)


@pytest.fixture
def xtrackers_no_weight_file(tmp_path: Path) -> Path:
    """
    Write an Xtrackers fixture lacking the Weighting column.
    """

    rows: list[list[object]] = [
        ["disclaimer"],
        [None, "Name", "ISIN", "Country", "Type of Security"],
        [1, "ALPHA SA", "ES0000000001", "España", "Renta Variable"],
        [2, "BETA SPA", "IT0000000002", "Italia", "Renta Variable"],
        [3, "CASH EUR", "_CURRENCYEUR", "España", "Cash"],
    ]
    return write_xlsx(tmp_path / "Constituent_NOWEIGHT.xlsx", "2026-06-29", rows)


@pytest.fixture
def xtrackers_partial_weight_file(tmp_path: Path) -> Path:
    """
    Write an Xtrackers fixture with one missing Weighting value.
    """

    rows: list[list[object]] = [
        ["disclaimer"],
        [
            None,
            "Name",
            "ISIN",
            "Country",
            "Type of Security",
            "Industry Classification",
            "Weighting",
        ],
        [1, "ALPHA SA", "ES1", "España", "Renta Variable", "Multiservicios", 0.50],
        # Missing weight: must force equal weight for the WHOLE file.
        [2, "BETA SPA", "IT1", "Italia", "Renta Variable", "Energías", ""],
        [3, "GAMMA SA", "FR1", "Francia", "Renta Variable", "Suministro", 0.20],
    ]
    return write_xlsx(tmp_path / "Constituent_PARTIAL.xlsx", "2026-06-29", rows)


def test_ishares_parses_equities_and_filters_cash(ishares_file: Path) -> None:
    """
    iShares parser keeps equities and drops the cash row.
    """

    holdings = parse_holdings_file(ishares_file, ISIN)

    assert len(holdings) == EXPECTED_ISHARES_HOLDINGS
    apple = holdings[0]
    assert apple.name == "APPLE INC"
    assert apple.ticker == "AAPL"
    assert apple.weight_pct == pytest.approx(5.34)
    assert apple.location == "United States"
    assert all(h.source_isin == ISIN for h in holdings)


def test_vanguard_parses_market_value_weight_and_region(
    vanguard_file: Path,
) -> None:
    """
    Vanguard parser reads '% of market value' and 'Region'.
    """

    holdings = parse_holdings_file(vanguard_file, ISIN)

    assert len(holdings) == EXPECTED_VANGUARD_HOLDINGS
    asml = holdings[0]
    assert asml.name == "ASML Holding NV"
    assert asml.weight_pct == pytest.approx(4.2547)
    assert asml.location == "NL"
    assert asml.sector == "Technology"


def test_xtrackers_uses_real_weighting_and_filters_non_equity(
    xtrackers_file: Path,
) -> None:
    """
    Xtrackers parser converts fractional weights to percentages.
    """

    holdings = parse_holdings_file(xtrackers_file, ISIN)

    assert len(holdings) == EXPECTED_XTRACKERS_EQUITIES
    nextera = holdings[0]
    assert nextera.name == "NEXTERA ENERGY INC"
    # 0.50 fraction -> 50% percentage.
    assert nextera.weight_pct == pytest.approx(50.0)
    # Spanish country is preserved for downstream normalization.
    assert nextera.location == "Estados Unidos"


def test_xtrackers_emits_raw_stripped_sector(xtrackers_file: Path) -> None:
    """
    Xtrackers parser reads 'Industry Classification', stripped.
    """

    holdings = parse_holdings_file(xtrackers_file, ISIN)

    sectors = [h.sector for h in holdings]
    # Raw Spanish sub-industries are emitted verbatim.
    assert sectors == [
        "Suministro Eléctrico",
        # Trailing whitespace on "Multiservicios " must be stripped.
        "Multiservicios",
        "Energías Alternativas",
    ]


def test_sector_map_normalizes_xtrackers_sub_industries() -> None:
    """
    SECTOR_MAP folds utilities sub-industries into 'Utilities'.
    """

    utilities_labels = [
        "Energías Alternativas",
        "Multiservicios",
        "Productores de Energía Independientes y Operadores de Energía",
        "Suministro Eléctrico",
        "Suministro de Agua",
        "Suministro de Gas",
    ]

    assert all(SECTOR_MAP[label] == "Utilities" for label in utilities_labels)
    # Genuinely-unknown rows normalize to empty, like other providers.
    assert SECTOR_MAP["desconocido"] == ""


def test_xtrackers_equal_weight_fallback(
    xtrackers_no_weight_file: Path,
) -> None:
    """
    Without a weight column, equities get an equal weight.
    """

    holdings = parse_holdings_file(xtrackers_no_weight_file, ISIN)

    assert len(holdings) == EXPECTED_XTRACKERS_EQUITIES - 1
    assert all(h.weight_pct == pytest.approx(50.0) for h in holdings)


def test_xtrackers_partial_weights_falls_back_to_equal(
    xtrackers_partial_weight_file: Path,
) -> None:
    """
    A single missing weight forces equal weights for the whole file.
    """

    holdings = parse_holdings_file(xtrackers_partial_weight_file, ISIN)

    # All-or-nothing: no per-row mixing of real and equal weights.
    assert len(holdings) == EXPECTED_XTRACKERS_EQUITIES
    assert all(h.weight_pct == pytest.approx(100.0 / 3) for h in holdings)
    # Weights must never sum to far more than 100% as the old bug did.
    assert sum(h.weight_pct for h in holdings) == pytest.approx(100.0)


def test_registry_detects_correct_provider(
    ishares_file: Path, vanguard_file: Path, xtrackers_file: Path
) -> None:
    """
    Content-based detection routes each file to one parser.
    """

    assert [p.name for p in PARSERS if p.can_parse(ishares_file)] == ["ishares"]
    assert [p.name for p in PARSERS if p.can_parse(vanguard_file)] == ["vanguard"]
    assert [p.name for p in PARSERS if p.can_parse(xtrackers_file)] == ["xtrackers"]


def test_ishares_detects_markers_within_sniff_window_despite_large_padding(
    tmp_path: Path,
) -> None:
    """
    Detection succeeds when the SpreadsheetML markers sit in the head.

    Trailing padding well beyond ``_SNIFF_BYTES`` must not matter: only
    the leading bytes are read from disk, mirroring the multi-megabyte
    real-world iShares export.
    """

    payload = ISHARES_XML.encode("utf-8") + b" " * (2 * 1024 * 1024)
    path = tmp_path / "ishares_padded.xls"
    path.write_bytes(payload)

    assert ISharesParser().can_parse(path)


def test_ishares_rejects_markers_beyond_sniff_window(tmp_path: Path) -> None:
    """
    Detection fails when the SpreadsheetML markers sit past the head.

    Padding placed before the markers pushes both ``<?xml`` and
    ``spreadsheet`` past the ``_SNIFF_BYTES`` cutoff, so the sniffed
    head never contains them.
    """

    padding = b" " * 4200
    payload = padding + ISHARES_XML.encode("utf-8")
    path = tmp_path / "ishares_late_markers.xls"
    path.write_bytes(payload)

    assert not ISharesParser().can_parse(path)


def test_ishares_rejects_csv_extension(tmp_path: Path) -> None:
    """
    A .csv file is not treated as iShares SpreadsheetML.
    """

    # SpreadsheetML content but with the wrong extension: the iShares
    # parser must not claim it (.csv is not a supported holdings type).
    path = tmp_path / "holdings.csv"
    path.write_text(ISHARES_XML, encoding="utf-8")

    assert not ISharesParser().can_parse(path)


def test_ishares_rejects_internal_entity_expansion(tmp_path: Path) -> None:
    """
    defusedxml blocks entity-expansion payloads (XXE hardening).
    """

    path = tmp_path / "ishares_xxe.xls"
    path.write_text(ISHARES_XXE_XML, encoding="utf-8")

    # The file is detected as iShares but parsing must refuse the
    # forbidden entity declaration and return no holdings.
    assert ISharesParser().can_parse(path)
    assert parse_holdings_file(path, ISIN) == []


def test_unknown_file_yields_no_holdings(tmp_path: Path) -> None:
    """
    An unrecognized file produces an empty holdings list.
    """

    path = tmp_path / "unknown.txt"
    path.write_text("not a holdings file", encoding="utf-8")

    assert parse_holdings_file(path, ISIN) == []
    assert not ISharesParser().can_parse(path)
    assert not VanguardParser().can_parse(path)
    assert not XtrackersParser().can_parse(path)


def truncate(path: Path, destination: Path, keep: int) -> Path:
    """
    Copy the leading bytes of a file, the way a cut download leaves it.

    Args:
        path: Valid file to copy from.
        destination: Path of the truncated copy.
        keep: Number of leading bytes to keep.

    Returns:
        Path to the truncated copy.
    """

    destination.write_bytes(path.read_bytes()[:keep])

    return destination


@pytest.fixture
def truncated_xlsx(tmp_path: Path, vanguard_file: Path) -> Path:
    """
    Cut a valid Vanguard XLSX in half, leaving an unreadable workbook.

    Truncating real workbook bytes is what makes the fixture fail where
    a half-downloaded file fails - inside the spreadsheet reader - rather
    than at a suffix or header check, which is what a text file with an
    .xlsx name would exercise instead.
    """

    return truncate(
        vanguard_file,
        tmp_path / "Constituent_TRUNCATED.xlsx",
        keep=vanguard_file.stat().st_size // 2,
    )


@pytest.fixture
def truncated_xls(tmp_path: Path) -> Path:
    """
    Cut an iShares SpreadsheetML export inside a multi-byte character.

    The .xls half of the same failure, and it is a different one: these
    exports are read as text, not through the spreadsheet reader, so a
    download cut mid character fails while decoding. The accent is what
    puts a multi-byte sequence at the cut.
    """

    payload = ISHARES_XML.replace("NESTLE SA", "NESTLÉ SA").encode("utf-8")
    cut = payload.index("É".encode()) + 1

    path = tmp_path / "Constituent_TRUNCATED.xls"
    path.write_bytes(payload[:cut])

    return path


@pytest.mark.parametrize("fixture_name", ["truncated_xlsx", "truncated_xls"])
def test_unreadable_file_is_reported_and_yields_no_holdings(
    fixture_name: str,
    request: pytest.FixtureRequest,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    A corrupt constituents file reports itself instead of propagating.

    The exception used to escape the detection loop and abort the whole
    overview with a traceback, even though the program already knows how
    to report a position it cannot break down. Both published formats
    are covered because they fail in different readers.
    """

    path = request.getfixturevalue(fixture_name)

    with caplog.at_level(logging.WARNING):
        holdings = fetch_holdings(ISIN, path)

    assert holdings == []
    assert "no se puede leer" in caplog.text
    assert path.name in caplog.text
    assert ISIN in caplog.text


def test_missing_file_is_reported_as_missing_not_as_unreadable(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    An absent file keeps its own message, which asks for something else.

    A file that is not there and a file that cannot be read are fixed
    differently - declare it, or download it again - so the two
    diagnostics must stay apart.
    """

    with caplog.at_level(logging.WARNING):
        holdings = fetch_holdings(ISIN, tmp_path / "absent.xlsx")

    assert holdings == []
    assert "No se encuentra el fichero" in caplog.text
    assert "no se puede leer" not in caplog.text


def test_readable_file_of_unknown_format_keeps_its_own_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    A readable workbook no parser recognizes is not called unreadable.

    The guard must not swallow the detection outcome: the file was read
    fine, and what it needs is a parser, not another download.
    """

    path = write_xlsx(tmp_path / "unrecognized.xlsx", "Sheet1", [["Sin cabecera"]])

    with caplog.at_level(logging.WARNING):
        holdings = fetch_holdings(ISIN, path)

    assert holdings == []
    assert "Ningún parser de holdings reconoce" in caplog.text
    assert "no se puede leer" not in caplog.text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("4.2547%", 4.2547),
        ("5,34", 5.34),
        ("1.234,56", 1234.56),
        ("0.0785247806", 0.0785247806),
        ("", None),
        ("n/a", None),
    ],
)
def test_parse_weight_formats(raw: str, expected: float | None) -> None:
    """
    parse_weight handles US, EU and fractional notations.
    """

    result = parse_weight(raw)

    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_normalize_holding_maps_sector_and_country() -> None:
    """
    normalize_holding canonicalizes Spanish sector and country.
    """

    raw = Holding(
        name="IBERDROLA",
        ticker="",
        sector="Tecnología de la Información",
        weight_pct=1.0,
        location="Estados Unidos",
        source_isin=ISIN,
    )

    canonical = normalize_holding(raw)

    assert canonical.sector == "Information Technology"
    assert canonical.location == "United States"
    # Untouched fields are preserved verbatim.
    assert canonical.name == "IBERDROLA"
    assert canonical.source_isin == ISIN


def test_fetch_holdings_normalizes_at_provider_boundary(
    xtrackers_file: Path,
) -> None:
    """
    fetch_holdings returns canonical sectors/countries while the raw
    parser still emits provider values.
    """

    raw = parse_holdings_file(xtrackers_file, ISIN)
    canonical = fetch_holdings(ISIN, xtrackers_file)

    # The parser keeps raw Spanish values...
    assert raw[0].location == "Estados Unidos"
    assert raw[0].sector == "Suministro Eléctrico"

    # ...while the providers boundary hands consumers canonical values.
    assert canonical[0].location == "United States"
    assert canonical[0].sector == "Utilities"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("CA", "Canada"),
        ("JP", "Japan"),
        ("TW", "Taiwan"),
        ("KR", "South Korea"),
        ("HK", "Hong Kong"),
        ("IN", "India"),
        ("AU", "Australia"),
        ("CN", "China"),
    ],
)
def test_normalize_holding_resolves_non_european_iso_codes(
    code: str, expected: str
) -> None:
    """
    Vanguard's two-letter regions resolve to canonical country names.

    The table used to cover European codes only, so every non-European
    domicile of the largest position in the portfolio stayed a raw code
    and became a country bucket of its own.
    """

    raw = Holding(
        name="ACME",
        ticker="",
        sector="Information Technology",
        weight_pct=1.0,
        location=code,
        source_isin=ISIN,
    )

    assert normalize_holding(raw).location == expected


def test_every_continent_country_has_an_iso2_code() -> None:
    """
    No country known to CONTINENT_MAP may lack an ISO-2 entry.

    Vanguard reports domiciles as two-letter codes, so a country
    without one is reported twice — once under its code and once under
    the name other providers use — and its weight lands in the "Other"
    continent. This test is what keeps the table from being left half
    finished again.
    """

    missing = sorted(set(CONTINENT_MAP) - set(ISO2_COUNTRY_MAP.values()))

    assert missing == []


def test_iso2_codes_resolve_to_canonical_countries() -> None:
    """
    Every ISO-2 entry names a country the rest of the domain knows.

    Catches a code mapped to a name that is not canonical, which would
    keep the weight out of the continent breakdown while looking
    correct in the country table.
    """

    unknown = sorted(set(ISO2_COUNTRY_MAP.values()) - set(CONTINENT_MAP))

    assert unknown == []


def test_iso2_codes_are_unique_per_country() -> None:
    """
    No country is reachable through two different ISO-2 codes.

    Two codes for one country is the signature of a mis-assigned code,
    which would attribute weight to the wrong country.
    """

    countries = list(ISO2_COUNTRY_MAP.values())

    assert len(countries) == len(set(countries))
