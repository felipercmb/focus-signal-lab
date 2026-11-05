import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from focus_signal import evaluation
from focus_signal.features import FEATURE_COLUMNS, build_features


@pytest.fixture
def features():
    dates = pd.date_range("2010-01-01", "2020-12-31", freq="W-FRI")
    step = np.arange(len(dates))
    focus = pd.DataFrame(
        {
            "date": dates,
            "median": 4 + np.sin(step / 11) + step / 500,
            "dispersion": 0.4 + 0.1 * np.cos(step / 9),
        }
    )
    selic = pd.DataFrame({"date": dates, "value": 8 + np.sin(step / 20)})
    fx = pd.DataFrame({"date": dates, "value": 3 + step / 1000 + 0.1 * np.sin(step / 5)})
    return build_features(focus, selic, fx)


@pytest.fixture(autouse=True)
def fast_candidates(monkeypatch):
    monkeypatch.setattr(
        evaluation,
        "model_specs",
        lambda: {
            "persistence": (None, FEATURE_COLUMNS),
            "ridge_macro": (make_pipeline(StandardScaler(), Ridge(alpha=10)), FEATURE_COLUMNS),
        },
    )


def test_training_only_uses_strictly_matured_labels(features):
    predictions = evaluation.walk_forward(features, "2017-01-01", "2017-12-31", period="test")
    assert (predictions["train_target_end"] < predictions["refit_date"]).all()
    assert (
        predictions["train_origin_end"] < predictions["refit_date"] - pd.Timedelta(7 * 4, unit="D")
    ).all()
    assert (predictions["origin"] >= predictions["refit_date"]).all()
    assert (
        predictions["origin"] < predictions["refit_date"] + pd.Timedelta(7 * 13, unit="D")
    ).all()
    assert (predictions["target_date"] <= pd.Timestamp("2017-12-31")).all()
    assert predictions.groupby("origin")["model"].nunique().eq(2).all()
    baseline = predictions[predictions["model"] == "persistence"]
    assert baseline["y_pred"].eq(0).all()
    np.testing.assert_allclose(baseline["predicted_level"], baseline["current_level"])


def test_future_target_changes_cannot_change_prior_predictions(features):
    before = evaluation.walk_forward(features, "2017-01-01", "2017-12-31", period="test")
    future = pd.Timestamp("2017-07-01")
    mutated = features.copy()
    mutated.loc[mutated["target_date"] >= future, "target_revision"] += 1000
    after = evaluation.walk_forward(mutated, "2017-01-01", "2017-12-31", period="test")
    compare = ["origin", "model", "y_pred", "train_target_end", "n_train"]
    assert_frame_equal(
        before.loc[before["origin"] < future, compare], after.loc[after["origin"] < future, compare]
    )


def test_scaler_is_fit_only_on_training_rows(features, monkeypatch):
    fits = []

    class RecordingScaler(StandardScaler):
        def fit(self, x, y=None, sample_weight=None):
            fits.append(x.index.copy())
            return super().fit(x, y, sample_weight=sample_weight)

    monkeypatch.setattr(
        evaluation,
        "model_specs",
        lambda: {
            "persistence": (None, FEATURE_COLUMNS),
            "ridge_macro": (make_pipeline(RecordingScaler(), Ridge(alpha=10)), FEATURE_COLUMNS),
        },
    )
    predictions = evaluation.walk_forward(features, "2017-01-01", "2017-03-31", period="test")
    assert len(fits) == 1
    refit = predictions["refit_date"].iloc[0]
    assert (features.loc[fits[0], "target_date"] < refit).all()
    assert len(fits[0]) == predictions["n_train"].iloc[0]


def test_selection_is_frozen_before_test_and_uses_same_samples(features):
    periods = {"validation": ("2015-01-01", "2016-12-31"), "test": ("2017-01-01", "2018-12-31")}
    before = evaluation.run_experiment(features, periods=periods)
    mutated = features.copy()
    mutated.loc[mutated.index >= "2017-01-01", "target_revision"] *= -100
    after = evaluation.run_experiment(mutated, periods=periods)
    assert before.selection == after.selection
    assert before.metrics.groupby("period")["n"].nunique().eq(1).all()
    scores = before.metrics.query("period == 'validation'")
    selected = before.selection["selected_model"]
    assert scores.set_index("model").loc[selected, "mae_pp"] == scores["mae_pp"].min()


def test_metrics_match_manual_computation():
    predictions = pd.DataFrame(
        {
            "period": ["test"] * 4,
            "model": ["persistence", "persistence", "ridge_macro", "ridge_macro"],
            "origin": pd.to_datetime(["2021-01-01", "2021-01-08"] * 2),
            "y_true": [1.0, -3.0, 1.0, -3.0],
            "y_pred": [0.0, 0.0, 1.0, -1.0],
        }
    )
    scores = evaluation.score_predictions(predictions).set_index("model")
    assert scores.loc["persistence", "mae_pp"] == 2
    assert scores.loc["persistence", "rmse_pp"] == pytest.approx(np.sqrt(5))
    assert scores.loc["ridge_macro", "mae_pp"] == 1
    assert scores.loc["ridge_macro", "skill_vs_persistence"] == 0.5
    assert scores.loc["persistence", "skill_vs_persistence"] == 0


def test_zero_baseline_error_has_undefined_skill():
    scores = evaluation.score_predictions(
        pd.DataFrame({"period": ["test"], "model": ["persistence"], "y_true": [0], "y_pred": [0]})
    )
    assert pd.isna(scores.loc[0, "skill_vs_persistence"])


def test_empty_or_insufficient_period_is_rejected(features):
    with pytest.raises(ValueError, match="no complete"):
        evaluation.walk_forward(features, "2030-01-01", "2030-12-31", period="test")
    with pytest.raises(ValueError, match="training rows"):
        evaluation.walk_forward(features, "2010-06-01", "2010-12-31", period="test")


def test_missing_baseline_cannot_silently_discard_results():
    predictions = pd.DataFrame(
        {"period": ["test"], "model": ["ridge_macro"], "y_true": [1], "y_pred": [0]}
    )
    with pytest.raises(ValueError, match="requires persistence"):
        evaluation.score_predictions(predictions)


def test_overlapping_validation_and_test_is_rejected(features):
    with pytest.raises(ValueError, match="before test"):
        evaluation.run_experiment(
            features,
            periods={
                "validation": ("2015-01-01", "2017-12-31"),
                "test": ("2017-01-01", "2018-12-31"),
            },
        )


def test_refit_calendar_does_not_shift_when_rows_are_missing(features):
    # Dropping eligible rows must not change the predeclared calendar schedule.
    full = evaluation.walk_forward(features, "2017-01-01", "2017-12-31", period="test")
    missing = features.drop(features.loc["2017-01-01":"2017-02-28"].index)
    partial = evaluation.walk_forward(missing, "2017-01-01", "2017-12-31", period="test")
    expected = full[["origin", "model", "refit_date"]].merge(
        partial[["origin", "model"]], on=["origin", "model"]
    )
    actual = partial[["origin", "model", "refit_date"]]
    assert_frame_equal(expected.reset_index(drop=True), actual.reset_index(drop=True))
