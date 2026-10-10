# Standard libraries
from typing import Final

# Label for weight that could not be attributed to a real holding.
UNCLASSIFIED_LABEL: Final[str] = "No clasificado"

# Label for a category the source does not resolve.
OTHER_LABEL: Final[str] = "Otros"

# GICS sectors as the ETF providers publish them. A closed vocabulary,
# so translating it is safe; anything unlisted passes through unchanged.
SECTOR_ES: Final[dict[str, str]] = {
    "Communication": "Comunicaciones",
    "Communication Services": "Comunicaciones",
    "Consumer Discretionary": "Consumo discrecional",
    "Consumer Staples": "Consumo básico",
    "Energy": "Energía",
    "Financials": "Financiero",
    "Health Care": "Salud",
    "Industrials": "Industrial",
    "Information Technology": "Tecnología",
    "Materials": "Materiales",
    "Real Estate": "Inmobiliario",
    "Utilities": "Servicios públicos",
    "Other": OTHER_LABEL,
}

# Countries appearing in provider holdings files. Mirrors the keys of
# CONTINENT_MAP so both stay in step.
COUNTRY_ES: Final[dict[str, str]] = {
    "Argentina": "Argentina",
    "Australia": "Australia",
    "Austria": "Austria",
    "Bangladesh": "Bangladés",
    "Belgium": "Bélgica",
    "Brazil": "Brasil",
    "Canada": "Canadá",
    "Chile": "Chile",
    "China": "China",
    "Colombia": "Colombia",
    "Czech Republic": "República Checa",
    "Denmark": "Dinamarca",
    "Egypt": "Egipto",
    "Finland": "Finlandia",
    "France": "Francia",
    "Germany": "Alemania",
    "Greece": "Grecia",
    "Hong Kong": "Hong Kong",
    "Hungary": "Hungría",
    "Iceland": "Islandia",
    "India": "India",
    "Indonesia": "Indonesia",
    "Ireland": "Irlanda",
    "Israel": "Israel",
    "Italy": "Italia",
    "Japan": "Japón",
    "Kenya": "Kenia",
    "Kuwait": "Kuwait",
    "Malaysia": "Malasia",
    "Mexico": "México",
    "Morocco": "Marruecos",
    "Netherlands": "Países Bajos",
    "New Zealand": "Nueva Zelanda",
    "Nigeria": "Nigeria",
    "North Korea": "Corea del Norte",
    "Norway": "Noruega",
    "Pakistan": "Pakistán",
    "Peru": "Perú",
    "Philippines": "Filipinas",
    "Poland": "Polonia",
    "Portugal": "Portugal",
    "Qatar": "Catar",
    "Romania": "Rumanía",
    "Russia": "Rusia",
    "Saudi Arabia": "Arabia Saudí",
    "Singapore": "Singapur",
    "South Africa": "Sudáfrica",
    "South Korea": "Corea del Sur",
    "Spain": "España",
    "Sweden": "Suecia",
    "Switzerland": "Suiza",
    "Taiwan": "Taiwán",
    "Thailand": "Tailandia",
    "Turkey": "Turquía",
    "United Arab Emirates": "Emiratos Árabes Unidos",
    "United Kingdom": "Reino Unido",
    "United States": "Estados Unidos",
    "Vietnam": "Vietnam",
}

# Continents produced by CONTINENT_MAP.
CONTINENT_ES: Final[dict[str, str]] = {
    "Africa": "África",
    "Asia": "Asia",
    "Europe": "Europa",
    "Middle East": "Oriente Medio",
    "North America": "Norteamérica",
    "Oceania": "Oceanía",
    "South America": "Sudamérica",
    "Other": OTHER_LABEL,
}
