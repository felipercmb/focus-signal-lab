# Data card

## Purpose and scope

This dataset supports a reproducible benchmark for changes in Brazilian inflation
expectations. It combines public aggregate statistics from Banco Central do Brasil
(BCB): the Focus survey's smoothed 12-month IPCA expectations, the Selic policy-rate
target, and the USD selling exchange rate. It contains no institution-level survey
responses, personal data, or private financial information.

The default snapshot requests **2010-01-01 through 2025-12-31**, inclusive. These
are the requested boundaries, not a promise that every series contains a record on
each boundary or every calendar day. Consult the snapshot's `manifest.json` for
the actual retrieval time, observed coverage, row counts, request URLs, and SHA-256
checksums. No live API call is needed to evaluate an existing snapshot.

## Sources and definitions

| Series | Official source | Selection and meaning | Units |
| --- | --- | --- | --- |
| Focus IPCA | [BCB expectations catalog](https://dadosabertos.bcb.gov.br/dataset/expectativas-mercado) and [API dictionary](https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/documentacao) | `ExpectativasMercadoInflacao12Meses`; `Indicador='IPCA'`, `Suavizada='S'`, `baseCalculo=0`. Retains `Mediana` and `DesvioPadrao`. | Expected cumulative 12-month inflation, percent; cross-sectional standard deviation, percentage points. |
| Selic target | [SGS 432 catalog](https://dadosabertos.bcb.gov.br/dataset/432-taxa-de-juros---meta-selic-definida-pelo-copom) | Daily target for the Selic rate set by Copom. This is the policy target, not the realized overnight rate. | Percent per year. |
| USD selling rate | [SGS 1 catalog](https://dadosabertos.bcb.gov.br/dataset/1-taxa-de-cambio---livre---dolar-americano-venda---diario) | Daily USD selling/reference exchange-rate series, identified as PTAX in the catalog. | BRL per USD during the selected period. |

Focus is a survey of participating institutions' expectations, not a forecast made
by BCB. `baseCalculo=0` uses each institution's latest valid expectation submitted
from the 30th calendar day before the statistic's calculation. The alternative
`baseCalculo=1` uses a shorter business-day window and is not mixed into this
dataset. `Data` is the **calculation date**, not a publication timestamp.

The word *smoothed* refers to BCB's treatment of the rolling inflation horizon.
The [BCB methodology FAQ, pages 16–18](https://www.bcb.gov.br/content/controleinflacao/Documents/Expectativas_mercado/FAQ13-Expectativas-de-Mercado.pdf)
describes prorating the effect of replacing the first forecast month with a new
month at the end of the horizon. It does not mean that this project applies a
centered moving average or another future-looking filter. A change in the series
can reflect horizon roll, changes in the respondent pool, and revised individual
expectations. The FAQ is a historical methodological reference, dated June 2016.

## Acquisition and stored representation

The downloader uses anonymous HTTPS requests to these official endpoints:

```text
https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/ExpectativasMercadoInflacao12Meses
https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados
https://api.bcb.gov.br/dados/serie/bcdata.sgs.1/dados
```

Focus requests include the fixed indicator filters, explicit date bounds, date
ordering, and pagination. SGS requests are split by year. BCB's SGS catalog states
that daily JSON/CSV requests require date filters and permit at most ten years per
request, a restriction introduced in March 2025.

Raw JSON responses are retained alongside three normalized CSV tables:

| Table | Columns | Grain |
| --- | --- | --- |
| `focus.csv` | `date`, `median`, `dispersion` | One selected Focus statistic per calculation date. |
| `selic.csv` | `date`, `value` | One policy-rate target per observation date. |
| `fx.csv` | `date`, `value` | One USD selling rate per observation date. |

The normalizer checks the Focus indicator, calculation base, and smoothing flag;
rejects duplicate or invalid dates, missing or non-finite retained values, negative
dispersion, and non-positive exchange rates; and sorts each series by date.
Unused Focus fields are not model inputs. Some historical API fields, such as
respondent counts and extrema, may be null and should not be presumed complete.

The manifest is written only after a complete download. Loading verifies listed
file hashes before analysis. A fresh download uses a new, empty destination so
that refreshing the archive cannot silently overwrite the evidence behind an
existing result. Checksums establish file consistency, not the historical truth
of the source observations.

## Availability, missingness, and historical limitations

BCB describes the expectations statistics as calculated daily and published on
the first business day of the week. The [Focus report page](https://www.bcb.gov.br/publicacoes/focus)
describes the usual release as Monday, covering expectations collected through the
previous Friday. Holidays can shift publication. Same-day access cannot be inferred
from a historical row's `Data` field.

This benchmark therefore uses Friday forecast origins and a **seven-calendar-day
availability lag** for every source. For each cutoff it takes the latest prior
observation, accepting at most seven additional days of staleness. An input can
thus be between seven and fourteen days old at the forecast origin. This common
clock is conservative for the market series too; it does not use all information
that might actually have been available that Friday.

Absent dates are not synthesized into observations. Weekly alignment uses a
bounded backward match, without interpolation or backward filling from future
records. Missing weekly inputs remain missing; rows without complete features and
targets are excluded consistently across models. The Friday grid is retained
before lags and targets are calculated so that missing weeks do not shorten a
four-week horizon. See [methodology](METHODOLOGY.md).

This is a snapshot of the **current historical API archive**, not a collection of
immutable, as-published vintages. The seven-day lag is an explicit availability
proxy, not a verified release calendar or a guarantee about every historical
publication. Neither this lag nor the manifest removes possible archival
revisions. Survey participation, publication practices, statistical definitions,
and macroeconomic regimes can change over a long sample. The fixed API filters
make the selection explicit; they do not establish complete historical
comparability.

## Attribution and reuse

All three BCB catalog pages identify their datasets as licensed under the
[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
The raw snapshot and derived data tables retain that license and BCB attribution;
see [data/LICENSE.md](../data/LICENSE.md). The project's MIT software license does
not relicense the source data. Reports and figures should retain their BCB source
and ODbL notices when reused. BCB does not endorse this project.
