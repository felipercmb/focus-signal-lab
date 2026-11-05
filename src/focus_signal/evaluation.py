"""Purged expanding-window evaluation with a held-out test period."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from focus_signal.features import FEATURE_COLUMNS, FOCUS_FEATURE_COLUMNS

DEFAULT_PERIODS = {
    "validation": ("2018-01-01", "2020-12-31"),
    "test": ("2021-01-01", "2025-12-31"),
}


@dataclass
class ExperimentResult:
    predictions: pd.DataFrame
    metrics: pd.DataFrame
    annual_metrics: pd.DataFrame
    selection: dict


def model_specs() -> dict:
    """Fixed candidates. Test results must not be used to modify these settings."""
    return {
        "persistence": (None, FOCUS_FEATURE_COLUMNS),
        "ridge_focus": (make_pipeline(StandardScaler(), Ridge(alpha=10.0)), FOCUS_FEATURE_COLUMNS),
        "ridge_macro": (make_pipeline(StandardScaler(), Ridge(alpha=10.0)), FEATURE_COLUMNS),
        "random_forest": (
            RandomForestRegressor(
                n_estimators=200,
                max_depth=4,
                min_samples_leaf=12,
                max_features=1.0,
                random_state=42,
                n_jobs=1,
            ),
            FEATURE_COLUMNS,
        ),
    }


def _eligible(frame: pd.DataFrame) -> pd.DataFrame:
    required = [*FEATURE_COLUMNS, "target_date", "target_revision", "target_level"]
    missing = set(required) - set(frame.columns)
    if missing:
        raise ValueError(f"missing evaluation columns: {sorted(missing)}")
    if (
        not isinstance(frame.index, pd.DatetimeIndex)
        or frame.index.hasnans
        or not frame.index.is_unique
    ):
        raise ValueError("evaluation requires unique nonmissing DatetimeIndex origins")
    if frame.index.tz is not None:
        raise ValueError("evaluation requires timezone-naive origins")
    result = frame.sort_index().copy()
    result["target_date"] = pd.to_datetime(result["target_date"], errors="raise")
    if (result["target_date"] <= result.index).any():
        raise ValueError("targets must become available strictly after their origins")
    numeric = [*FEATURE_COLUMNS, "target_revision", "target_level"]
    values = result[numeric].to_numpy(dtype=float)
    if np.isinf(values).any():
        raise ValueError("evaluation inputs cannot contain infinity")
    # All models use identical rows, including the Focus-only ablation.
    return result.dropna(subset=required)


def walk_forward(
    frame: pd.DataFrame,
    start: str,
    end: str,
    *,
    period: str,
    refit_weeks: int = 13,
    min_train: int = 100,
) -> pd.DataFrame:
    """Refit on fixed calendar blocks using only strictly matured targets.

    Outcomes whose availability date equals the refit date are excluded.
    Forecasts in a block share fitted parameters, but use their own then-known
    covariates. Earlier test outcomes may enter later scheduled refits; test
    errors never drive candidate choice or hyperparameter tuning.
    """
    if refit_weeks < 1 or min_train < 1:
        raise ValueError("refit_weeks and min_train must be positive")
    eligible = _eligible(frame)
    period_start, period_end = pd.Timestamp(start), pd.Timestamp(end)
    if period_start > period_end:
        raise ValueError("start must not be after end")
    evaluation = eligible.loc[
        (eligible.index >= period_start)
        & (eligible.index <= period_end)
        & (eligible["target_date"] <= period_end)
    ]
    if evaluation.empty:
        raise ValueError(f"{period}: no complete evaluation observations")
    blocks = pd.date_range(period_start, period_end, freq=f"{refit_weeks}W-FRI")
    rows = []
    for block_start in blocks:
        block_end = block_start + pd.Timedelta(7 * refit_weeks, unit="D")
        block = evaluation.loc[(evaluation.index >= block_start) & (evaluation.index < block_end)]
        if block.empty:
            continue
        train = eligible.loc[
            (eligible.index < block_start) & (eligible["target_date"] < block_start)
        ]
        if len(train) < min_train:
            raise ValueError(
                f"{period} at {block_start.date()}: {len(train)} training rows, need {min_train}"
            )
        for name, (estimator, columns) in model_specs().items():
            if estimator is None:
                predicted = np.zeros(len(block), dtype=float)
            else:
                fitted = clone(estimator)
                fitted.fit(train.loc[:, list(columns)], train["target_revision"])
                predicted = fitted.predict(block.loc[:, list(columns)])
            records = pd.DataFrame(
                {
                    "period": period,
                    "model": name,
                    "origin": block.index,
                    "target_date": block["target_date"].to_numpy(),
                    "y_true": block["target_revision"].to_numpy(),
                    "y_pred": predicted,
                    "current_level": block["current_level"].to_numpy(),
                    "actual_level": block["target_level"].to_numpy(),
                    "predicted_level": block["current_level"].to_numpy() + predicted,
                    "refit_date": block_start,
                    "train_origin_end": train.index.max(),
                    "train_target_end": train["target_date"].max(),
                    "n_train": len(train),
                }
            )
            rows.append(records)
    if not rows:
        raise ValueError(f"{period}: no predictions generated")
    return (
        pd.concat(rows, ignore_index=True).sort_values(["origin", "model"]).reset_index(drop=True)
    )


def score_predictions(predictions: pd.DataFrame, *, by_year: bool = False) -> pd.DataFrame:
    """MAE/RMSE in percentage points; skill = 1 - MAE / persistence MAE."""
    working = predictions.copy()
    groups = ["period"]
    if by_year:
        working["year"] = pd.to_datetime(working["origin"]).dt.year
        groups.append("year")
    groups.append("model")
    rows = []
    for key, group in working.groupby(groups, sort=True):
        error = group["y_pred"].to_numpy() - group["y_true"].to_numpy()
        rows.append(
            dict(zip(groups, key, strict=True))
            | {
                "n": len(group),
                "mae_pp": float(np.mean(np.abs(error))),
                "rmse_pp": float(np.sqrt(np.mean(error**2))),
            }
        )
    scores = pd.DataFrame(rows)
    if scores.empty:
        raise ValueError("no predictions to score")
    join_keys = groups[:-1]
    baseline = scores.loc[scores["model"] == "persistence", [*join_keys, "mae_pp"]].rename(
        columns={"mae_pp": "baseline_mae_pp"}
    )
    if len(baseline) != len(scores[join_keys].drop_duplicates()):
        raise ValueError("every scoring group requires persistence predictions")
    scores = scores.merge(baseline, on=join_keys, validate="many_to_one")
    scores["skill_vs_persistence"] = np.where(
        scores["baseline_mae_pp"] > 0,
        1 - scores["mae_pp"] / scores["baseline_mae_pp"],
        np.nan,
    )
    return scores


def run_experiment(
    frame: pd.DataFrame,
    *,
    periods: dict[str, tuple[str, str]] | None = None,
    refit_weeks: int = 13,
    min_train: int = 100,
) -> ExperimentResult:
    """Freeze selection on validation MAE before evaluating the test period."""
    periods = DEFAULT_PERIODS if periods is None else periods
    if set(periods) != {"validation", "test"}:
        raise ValueError("periods must contain validation and test")
    if pd.Timestamp(periods["validation"][1]) >= pd.Timestamp(periods["test"][0]):
        raise ValueError("validation must end before test begins")
    validation = walk_forward(
        frame,
        *periods["validation"],
        period="validation",
        refit_weeks=refit_weeks,
        min_train=min_train,
    )
    validation_scores = score_predictions(validation)
    # Stable ties favor persistence, then the predeclared candidate order.
    order = {name: i for i, name in enumerate(model_specs())}
    ranked = validation_scores.assign(_order=validation_scores["model"].map(order)).sort_values(
        ["mae_pp", "_order"]
    )
    winner = ranked.iloc[0]
    selection = {
        "selected_model": str(winner["model"]),
        "selection_metric": "validation MAE (percentage points)",
        "validation_mae_pp": float(winner["mae_pp"]),
        "validation_start": periods["validation"][0],
        "validation_end": periods["validation"][1],
        "test_start": periods["test"][0],
        "test_end": periods["test"][1],
        "refit_weeks": refit_weeks,
        "min_train": min_train,
        "training_rule": "target_date < refit_date",
        "candidate_order": list(model_specs()),
    }
    test = walk_forward(
        frame, *periods["test"], period="test", refit_weeks=refit_weeks, min_train=min_train
    )
    predictions = pd.concat([validation, test], ignore_index=True)
    return ExperimentResult(
        predictions=predictions,
        metrics=score_predictions(predictions),
        annual_metrics=score_predictions(predictions, by_year=True),
        selection=selection,
    )
