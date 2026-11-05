# Monthly IPCA: publication-calendar research

**Stage: data audit, not a trained forecasting model.** This research asks whether
a correction based on already observed errors can improve the Focus median for
the next calendar month's realized Brazilian IPCA. No predictive performance has
been measured for this track. The existing [weekly benchmark](../../README.md)
remains a separate experiment with a different target and evaluation calendar.

This update publishes factual tables from the local `diagnostico-focus`
reconstruction, revision **0.3.4-audit-addendum**, reviewed on 5 November 2026.
The source archive is preserved locally. News bodies, PDF reports, review images
and private filesystem links are excluded from this repository.

## What changed

The latest local round audited the 2012 warm-up publication boundaries and
the twelve 2011 IPCA release dates needed to check historical lag availability.
It preserved the 72 proposed training origins, their selected references and
medians. Six warm-up rows were updated in the recorded source round; this is a
historical audit-receipt statement, not a new model result.

| Current audited scope | Count |
| --- | ---: |
| Monthly origins, January 2012–December 2018 | 84 |
| Warm-up origins, 2012 | 12 |
| Proposed training origins, 2013–2018 | 72 |
| Documented-snapshot candidates | 84 |
| Candidates with stronger calendar support | 83 |
| Stronger calendar support within proposed training | 71 of 72 |
| Candidate PDF/API median comparisons | 168 |
| Origins marked ready for training | **0** |

All 168 candidate median pairs agree at the printed precision, with maximum
absolute difference **0.00 percentage points**. This is an **extraction-consistency
check**, not a forecasting error, accuracy score or proof of immutable vintages.

**July 2013 remains unresolved.** The retained edition has reference date
21 June 2013. The newer candidate, 28 June, has conflicting publication evidence
around the 1 July midnight origin. Both print a July median of **0.25%**, but that
agreement cannot resolve the calendar: other features could differ between
editions. The row remains in the nominal training universe with its stronger
calendar flag false. A future experiment must resolve this boundary or record
an explicit handling policy before examining model results.

## Proposed experiment and information clock

- **Economic target:** realized national monthly IPCA for month `m`, in percent.
- **Origin:** the first day of `m` at midnight in `America/Sao_Paulo`; select the
  last Focus edition supported as public strictly before that origin, using the
  base-0 median for the fixed month `m`.
- **Reference versus release:** the Focus calculation date is not its publication
  date. Evidence that an edition was available is also not proof that no newer
  edition was available. These are separate fields and checks.
- **Additional information:** observed IPCA history and future training labels
  must have release dates strictly before the selected Focus reference date.
  A calendar-month shift alone does not make an observation available. Same-day
  releases are excluded by the conservative daily rule.
- **Candidate baselines, not yet run:** the Focus median, last known IPCA,
  prior-year same-month IPCA, historical seasonal mean, and a simple Focus bias
  correction estimated only from already released errors.
- **Proposed split:** 2012 warm-up, 2013–2018 training, 2019–2021 validation,
  2022–2025 test. The exported reconstruction covers **2012–2018 only**; it does
  not establish complete validation/test publication evidence.

No algorithm or hyperparameter was selected for this new monthly experiment.
The published Ridge/random-forest results elsewhere in this repository belong
to the weekly expectations-change benchmark and must not be attributed here.

## Inspect and validate the public export

After installing the project using the root README:

```bash
python -m focus_signal.monthly_audit research/monthly-ipca
pytest -q
```

The validator checks hashes, unique monthly coverage, strict date inequalities,
candidate matching, arithmetic, availability flags and subset counts. Tests
include deliberately corrupted records and boundary dates. CI runs these checks
alongside the unchanged weekly benchmark.

| File | What it contains |
| --- | --- |
| [origins.csv](origins.csv) | 84 chosen editions, dates, medians, evidence URLs, PDF hashes and gate flags. |
| [candidate_pdf_checks.csv](candidate_pdf_checks.csv) | Both candidate editions for every origin; current printed/API medians and extraction method. |
| [ipca_release_dates.csv](ipca_release_dates.csv) | Twelve 2011 observed-IPCA releases, values and official IBGE release URLs/IDs. |
| [lag_availability.csv](lag_availability.csv) | Availability of last known, prior-year and previous-month observations at the 2012 Focus cutoffs. |
| [export_manifest.json](export_manifest.json) | Public-file hashes, original input hashes, excluded fields and source revision. |
| [audit_summary.json](audit_summary.json) | Recomputed structural and numerical findings from this export. |

The public check **does not redo the full source investigation**. In particular,
it cannot re-read the excluded news captures or independently certify every
publication claim. The status columns carry documented curation conclusions;
checks ensure their internal consistency, not source infallibility. The
`source_full_capture_available` field refers to the **local audit archive**, not
to news files shipped in this repository.

The exporter is [scripts/export_monthly_audit.py](../../scripts/export_monthly_audit.py).
Given a copy of the original local reconstruction, it can regenerate the four
public tables into an empty directory without changing the source:

```bash
python scripts/export_monthly_audit.py /path/to/reconstruction /tmp/monthly-export
python -m focus_signal.monthly_audit /tmp/monthly-export
```

## Evidence limits

Identity checks searched two candidate editions per origin. Latest-edition
conclusions depend on the recovered inventory and current historical archives.
None of the 84 rows certifies the first public dissemination time. Original
immutable Focus vintages and first-release IPCA vintages remain unverified.
Later news may corroborate exclusion of a newer edition; its values must never
become historical model inputs. Different sites can syndicate the same article
and do not necessarily provide independent evidence.

The extra 2011 calendar supports the stated warm-up lag checks; it does not
automatically support arbitrarily long rolling histories. The zero-training
gate remains explicit while these limitations and the unresolved boundary are
carried forward.

## Sources and attribution

Banco Central do Brasil: [Focus catalog](https://dadosabertos.bcb.gov.br/dataset/expectativas-mercado),
[API definitions](https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/documentacao),
[Focus reports](https://www.bcb.gov.br/publicacoes/focus), and
[SGS 433 monthly IPCA](https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados?formato=json).
IBGE: [IPCA](https://www.ibge.gov.br/estatisticas/economicas/precos-e-custos/9256-ipca.html),
[SIDRA table 1737](https://sidra.ibge.gov.br/tabela/1737), and the individual official
release URLs in the tables. Contemporary news URLs document publication
corroboration; their full text is not republished. See [source terms](LICENSE.md).
