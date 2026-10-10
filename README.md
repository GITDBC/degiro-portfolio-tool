# Degiro Tools

CLI que extrae y calcula datos a partir de extractos de Degiro: fiscalidad IRPF,
valoración de cartera y análisis _look-through_ de ETFs. Parsea los ficheros, computa las
cifras y muestra las tablas por terminal.

## Setup

`holdings.json` declara por ISIN lo que el export de Degiro no trae. Tiene tres
secciones:

```json
{
  "renta_variable": {
    "IE00B4L5Y983": "holdings/msci_world.xls",
    "IE00BK5BQX27": "holdings/vanguard_europe.xlsx"
  },
  "renta_fija": ["LU0290358497"],
  "criptomonedas": ["XS2940466316"]
}
```

En `renta_variable` va cada fondo de renta variable con su fichero de holdings, que es lo
que el _look-through_ desglosa. Descárgalo de la web de su proveedor (iShares `.xls`;
Vanguard y Xtrackers `.xlsx`) y déjalo en `holdings/`.

`renta_fija` es una lista de ISIN **sin ficheros**: una posición de renta fija (bonos o
monetario) entra en el informe como una sola línea con su peso exacto, sin desglosarse.

`criptomonedas` es igual, otra lista de ISIN **sin ficheros**: un ETP de activo único
(bitcoin, por ejemplo) no tiene constituyentes que desglosar, así que entra también como
una sola línea con su peso. Sin declararlo, su peso acabaría en `No clasificado`.

Lo que no declares se toma como renta variable. Un ISIN va en una sección y solo en una:
declararlo en dos se rechaza con un error, porque una posición tiene una sola clase de
activo.

## Comandos

Hay dos:

```bash
uv run degiro-tools overview holdings/Portfolio.xlsx    # Todo sobre la cartera actual
uv run degiro-tools tax holdings/Account.xlsx            # IRPF (FIFO)
```

`overview` hace la pasada completa y pide los precios una sola vez para todas las
secciones. Son cinco bloques: valoración, resultado latente (con `--account`), posiciones
reales, distribución y coste y riesgo de los ETFs. El solapamiento entre fondos se ve en
la columna `ETFs` de la tabla de posiciones reales.

```bash
uv run degiro-tools overview holdings/Portfolio.xlsx --account holdings/Account.xlsx
```

| Flag                   | Descripción                                                   |
| ---------------------- | ------------------------------------------------------------- |
| `--config <path>`      | JSON de holdings (default: `holdings.json`)                   |
| `--account <path>`     | Estado de cuenta: añade coste, resultado latente y anualizado |
| `--inflation <es\|ea>` | IPC con el que se descuenta la inflación (default: `es`)      |
| `--refresh`            | Ignora la caché y vuelve a pedir precios y catálogo           |
| `--no-tax`             | Omite la estimación IRPF (solo `tax`)                         |
| `-v`                   | Traceback completo                                            |

El XLSX de Portfolio es obligatorio: todas las secciones son vistas de tus propias
posiciones. En cualquier tabla, **`n/d` no es un cero**: es un dato que no existe, porque
el fondo no tiene historia suficiente o la posición es demasiado joven para anualizarla.

## De dónde salen los precios

Cada posición se pide primero a [justETF](https://www.justetf.com), que devuelve el precio
ya en euros, y solo si no contesta se recurre a Yahoo Finance. El orden no es porque los
precios de Yahoo estén mal: comparados uno a uno, se quedaban entre el 0,02% y el 0,27%
de los de justETF, una diferencia sin efecto en el informe. Lo que se gana es quitar un
paso: pedir el precio en euros ahorra convertir de dólares, y con ello desaparece la
posibilidad de que un fallo al obtener el tipo de cambio deje posiciones sin valorar.

Yahoo sigue haciendo falta, no desaparece: cubre lo que justETF no tiene y es la fuente
de respaldo del bloque de coste y riesgo de los ETFs. Si una fuente falla, la otra
responde y ninguna posición se cae de la tabla por eso. Debajo de la valoración se indica
qué fuente ha dado los precios, y si han hecho falta las dos aparece una columna que lo
dice fila a fila.

Los precios se guardan en `~/.cache/degiro-tools` durante 15 minutos, lo justo para que
repetir el informe no vuelva a pedirlos sin que el dato envejezca más de lo que ya viene;
`--refresh` los pide de nuevo.

## Resultado latente (`--account`)

La valoración muestra `Valor €`, cantidad por precio de hoy: lo que vale, no lo que costó.
Al pasar también el estado de cuenta sale el resultado latente: lo ganado o perdido sin
haber vendido, con el coste FIFO de los lotes abiertos, los días medios de tenencia y la
rentabilidad anualizada.

El bloque consolidado separa el resultado latente (no tributa, la posición sigue abierta)
de las ganancias realizadas y los dividendos brutos (sí tributan). La suma de los tres es
lo que ha dado la cuenta, **no** lo que hay que declarar; para eso está `tax`.

### Lo que gana descontando la inflación

Junto al resultado de siempre sale el mismo resultado **real**: el que queda después de
descontar la subida de los precios. La diferencia importa porque los euros que metiste
hace seis años valían más que los de hoy, así que compararlos sin más mide dos cosas
distintas. Una posición que sube un 10% mientras los precios suben un 20% tiene más euros
y compra menos: eso es una pérdida, y solo la cifra real lo dice.

El coste de cada compra se pasa a euros de hoy con el IPC armonizado que publica el Banco
Central Europeo, y desde ahí se recalcula igual que la rentabilidad normal. Con
`--inflation` eliges el índice: `es` para España (el de partida) o `ea` para la zona euro.

El IPC se publica con unas seis semanas de retraso, así que el informe dice siempre con
qué mes está midiendo. Las compras posteriores a ese mes se quedan con el último índice
publicado, es decir con un factor de 1,00: no se inventa la inflación que todavía no se
ha publicado. Si alguna posición se compró antes de donde empieza la serie, su coste
queda fuera del total real y el informe lo avisa en lugar de dar una cifra a medias.

Necesita internet. La serie se guarda en `~/.cache/degiro-tools` y se reutiliza un día,
que es de sobra para un dato mensual; `--refresh` fuerza la descarga.

## Coste y riesgo de los ETFs

Pone tus posiciones en la misma vara de medir con los datos de
[justETF](https://www.justetf.com), en una tabla ordenada por **R/R** (rentabilidad entre
volatilidad a 3 años). El TER se cruza con Yahoo Finance como fuente de respaldo y
verificación; el resto de cifras (rentabilidad, volatilidad, caída) se toman solo de
justETF, porque es la única fuente que las da en euros y en el criterio de un inversor
europeo.

Requiere internet. La respuesta del catálogo de justETF se cachea en
`~/.cache/degiro-tools` y se reutiliza 12 horas, al ritmo al que justETF recalcula sus
cifras; `--refresh` fuerza la descarga.

## Informe fiscal (`tax`)

Integra en una sola base del ahorro todo lo que tributa por la escala del art. 66 LIRPF:
las **ganancias patrimoniales** de las ventas, casadas por FIFO por ISIN, y los
**rendimientos del capital mobiliario** (dividendos brutos e intereses). Después resta
las retenciones españolas, que son pago a cuenta y no impuesto definitivo.

Bloques que conviene conocer:

- **Resultado realizado por año**: solo aparece si el extracto abarca varios ejercicios.
- **Detalle para la declaración**: una línea por fecha de adquisición, lista para
  teclear en Renta Web.
- **Posibles pérdidas no imputables**: candidatas a la regla de los dos meses (art.
  33.5.f LIRPF); el importe sigue dentro de la base porque decidir si se difiere exige
  valorar la homogeneidad en todos tus brokers, algo que un extracto no resuelve.
- **Operaciones excluidas**: órdenes en divisa sin cambio asociado (cuenta Manual FX);
  se excluye todo el historial de ese ISIN porque no se puede expresar en euros.

Ninguna fila del extracto se descarta en silencio: lo que no encaja en ningún concepto se
avisa por log con su descripción.
