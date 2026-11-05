# Methodology

## Research question

Do recent changes and disagreement in the Focus survey, together with lagged
interest-rate and exchange-rate movements, help predict the **four-week change in
smoothed rolling 12-month IPCA expectations** better than assuming no change?

This is a supervised regression benchmark for an expectations series. It does
not forecast realized IPCA, estimate the causal effects of monetary policy, or
evaluate a trading strategy. The economic motivation is to examine signals that
could help monitor changes in the inflation outlook relevant to planning.
Useful predictive accuracy and economic value are separate claims.

## Forecast clock and target

Let `t` be a Friday forecast origin. The default information cutoff is `t - 7`
calendar days. For each source, select the latest observation dated on or before
that cutoff, with a maximum additional staleness of seven days. Denote the selected
Focus median by `F(t)`, the Selic target by `S(t)`, and the USD selling rate by
`X(t)`. All are availability proxies, not independently verified historical
publication timestamps.

The prediction target is:

```text
y(t) = F(t + 28 days) - F(t)
target_date(t) = t + 28 days
predicted_level(t) = F(t) + predicted_change(t)
```

`y(t)` is measured in percentage points. The four-week interval is between
availability-proxy dates. Its underlying survey observation window starts about
one week before `t`; holiday gaps can add staleness. The target is not a change
from the unreleased, same-Friday survey statistic. For a normal pair of Fridays,
a forecast at `t` uses the survey value from `t - 7 days` and predicts the
difference to the value from `t + 21 days`, treated as available at `t + 28 days`.

Rows live on a complete Friday grid. Lags and shifts refer to calendar weeks,
not a compressed list of nonmissing observations. Missing inputs are not
interpolated; insufficient history, stale matches, and incomplete future targets
produce ineligible rows. In particular, the first 13 weeks provide feature
history, and the final four origins cannot have complete targets without later
observations.

BCB's smoothed series already includes a gradual roll of its forecast horizon.
Changes also depend on which institutions have valid responses. Consequently,
the target should not be interpreted as a pure measure of individual belief
revisions. Definitions and source references are in the [data card](DATA_CARD.md).

## Features

Each feature uses only the observations selected by its origin's cutoff and
earlier weekly values. No realized future inflation, future exchange rate, or
future survey statistic enters the feature matrix.

| Group | Feature | Definition |
| --- | --- | --- |
| Focus | `current_level` | `F(t)`. |
| Focus | `revision_1w` | `F(t) - F(t - 7 days)`. |
| Focus | `revision_4w` | `F(t) - F(t - 28 days)`. |
| Focus | `revision_13w` | `F(t) - F(t - 91 days)`. |
| Focus | `dispersion` | Cross-sectional standard deviation of the selected survey expectations. |
| Macro | `selic_level` | `S(t)`, percent per year. |
| Macro | `selic_change_4w` | `S(t) - S(t - 28 days)`, percentage points. |
| Macro | `fx_log_change_1w` | `log(X(t)) - log(X(t - 7 days))`, a dimensionless log change. |
| Macro | `fx_log_change_4w` | `log(X(t)) - log(X(t - 28 days))`, a dimensionless log change. |

The `revision_*` column names are convenient labels for series changes; they do
not isolate individual respondents' revisions. All candidates, including the
Focus-only model and persistence baseline, are evaluated on the same complete
rows requiring all nine features and a valid target. This makes comparisons
consistent but restricts them to periods with complete macro data as well.

## Fixed candidate models

| Candidate | Inputs and settings |
| --- | --- |
| `persistence` | Predicts a zero change, equivalently retaining the latest available Focus median as the future level. |
| `ridge_focus` | Five Focus features; training-only standardization and Ridge regression with `alpha=10.0`. |
| `ridge_macro` | All nine features; training-only standardization and Ridge regression with `alpha=10.0`. |
| `random_forest` | All nine features; 200 trees, maximum depth 4, minimum leaf size 12, all features available at each split, random seed 42, one worker. |

These settings are fixed candidates, not a hyperparameter search. Comparing the
two Ridge models is an ablation for the added macro features. The forest tests
a constrained nonlinear alternative. A more complex model is not presumed to
outperform persistence.

## Temporal evaluation and model selection

The default requested data range is 2010–2025. The evaluation periods are fixed:

| Period | Role |
| --- | --- |
| History before 2018 | Initial training history after feature warm-up and target-maturity filtering. |
| 2018-01-01 to 2020-12-31 | Walk-forward validation used to select the candidate with the lowest MAE. |
| 2021-01-01 to 2025-12-31 | Walk-forward test used to report performance after candidate selection is frozen. |

Both an evaluated origin and its `target_date` must fall within its period.
Therefore validation labels do not cross into 2021 and test labels do not cross
into 2026. There is no random train/test split.

Within each period, models are refitted on an expanding historical window every
13 calendar weeks, on a Friday. A training row is eligible only if:

```text
training_origin < refit_date
training_target_date < refit_date
```

The strict inequality excludes labels whose assumed availability date equals the
refit date. At least 100 complete training rows are required. Standardization is
fitted only on those training rows, inside the Ridge pipeline. Parameters remain
fixed until the next scheduled refit; forecasts between refits use their own
then-available features.

Earlier validation outcomes can enter later validation fits once mature. Earlier
test outcomes likewise can enter later scheduled test fits. This simulates a
predeclared updating procedure rather than a model frozen throughout five years.
Test errors do not determine refit timing, candidate selection, or model settings.

Selection uses aggregate validation MAE. Exact ties favor persistence, then
`ridge_focus`, `ridge_macro`, and `random_forest`, in that order. The test report
may show all fixed candidates for transparency; the selected candidate remains
the validation winner even if a different candidate has a better test score.
Changing features or settings after inspecting test results would make that test
period part of development and require a fresh future evaluation.

## Metrics and inspection

The generated artifacts, rather than this document, contain measured results:

- **MAE:** average absolute error in the four-week change, in percentage points.
- **RMSE:** square root of average squared error, in percentage points; it gives
  larger misses more weight.
- **Skill relative to persistence:** `1 - model_MAE / persistence_MAE`. Positive
  values indicate lower MAE; negative values indicate worse MAE. Skill is
  undefined when persistence MAE is zero.
- **Annual breakdown:** metrics grouped by forecast-origin year, to make
  instability across regimes visible.

Change and level errors are identical because both predicted and actual levels
share the same current median. Each prediction records its origin, target date,
refit date, training sample size, and latest training origin and target dates,
allowing inspection of the temporal boundary. The snapshot manifest links the
experiment to retained source responses and normalized input hashes.

## Limits of the evidence

The seven-day lag is a conservative publication proxy, not a historical release
calendar. Current API archives may differ from records originally published at
the time. This is therefore a retrospective backtest with explicit availability
assumptions, not a verified real-time vintage experiment.

Weekly four-week targets overlap, so successive errors are dependent. Counts of
weekly predictions are not counts of independent experiments. To describe
uncertainty in the test comparison, the benchmark computes a paired moving-block
bootstrap of **model MAE minus persistence MAE**, in percentage points. It
resamples blocks of 13 consecutive evaluated forecast origins, keeping model and
baseline errors paired and preserving their local temporal dependence. The
defaults are 2,000 resamples and random seed 42; the 2.5th and 97.5th percentiles
form an approximate 95% interval. Negative differences favor the model. An
interval that includes zero does not establish a clear advantage on this
diagnostic.

These descriptive intervals are conditional on the fitted prediction path:
bootstrap resamples do not refit the models. They provide no correction for
candidate selection or multiple comparisons, and do not capture all uncertainty
from structural changes, source revisions, or availability assumptions. The block
length is fixed rather than selected from the test results. These are intervals
for the average error difference, not calibrated prediction intervals for future
inflation expectations or a guarantee of future performance.

The sample is small for machine learning and spans changes in inflation,
interest-rate, and exchange-rate regimes. Validation performance may not survive
those changes. Survey composition, mechanical horizon roll, archival revisions,
and the deliberate information lag further limit interpretation. Improvements
over persistence, if observed, do not establish causality, financial returns, or
production readiness. This project is a research and engineering demonstration,
not financial advice.
