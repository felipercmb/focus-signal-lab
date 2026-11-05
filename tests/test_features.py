import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from focus_signal.features import FEATURE_COLUMNS, build_features


@pytest.fixture
def observations():
    dates = pd.date_range("2010-01-01", periods=180, freq="W-FRI")
    step = np.arange(len(dates))
    focus = pd.DataFrame({"date": dates, "median": 4 + step / 100, "dispersion": 0.4 + step / 1000})
    selic = pd.DataFrame({"date": dates, "value": 8 + step / 100})
    fx = pd.DataFrame({"date": dates, "value": np.exp(step / 100)})
    return focus, selic, fx


def test_calendar_horizon_and_values(observations):
    focus, selic, fx = observations
    features = build_features(focus, selic, fx)
    row = features.iloc[20]
    assert features.index.name == "origin"
    assert row.name.dayofweek == 4
    assert row["feature_cutoff"] == row.name - pd.Timedelta(7, unit="D")
    assert row["focus_source_date"] == row["feature_cutoff"]
    assert row["target_date"] == row.name + pd.Timedelta(7 * 4, unit="D")
    assert row["target_source_date"] == row["focus_source_date"] + pd.Timedelta(7 * 4, unit="D")
    assert row["current_level"] == pytest.approx(4.2)
    assert row["target_level"] == pytest.approx(4.24)
    assert row["target_revision"] == pytest.approx(0.04)
    assert row["revision_13w"] == pytest.approx(0.13)
    assert row["fx_log_change_4w"] == pytest.approx(0.04)
    assert features["target_level"].tail(4).isna().all()


def test_future_values_cannot_change_earlier_features(observations):
    before = build_features(*observations)
    cutoff = observations[0].iloc[70]["date"]
    mutated = [frame.copy() for frame in observations]
    for frame in mutated:
        numeric = frame.columns.drop("date")
        frame.loc[frame["date"] > cutoff, numeric] *= 100
    after = build_features(*mutated)
    columns = [
        *FEATURE_COLUMNS,
        "feature_cutoff",
        "focus_source_date",
        "selic_source_date",
        "fx_source_date",
    ]
    origin = cutoff + pd.Timedelta(7, unit="D")
    assert_frame_equal(before.loc[:origin, columns], after.loc[:origin, columns])


def test_missing_weeks_are_not_compressed_or_interpolated(observations):
    focus, selic, fx = observations
    dates = focus["date"]
    focus = focus.drop(index=[40, 41, 42])
    features = build_features(focus, selic, fx)
    missing_origin = dates.iloc[41] + pd.Timedelta(7, unit="D")
    assert pd.isna(features.loc[missing_origin, "current_level"])
    earlier_origin = missing_origin - pd.Timedelta(7 * 4, unit="D")
    assert pd.isna(features.loc[earlier_origin, "target_revision"])
    assert features.loc[earlier_origin, "target_date"] == missing_origin
    assert (features.index.to_series().diff().dropna() == pd.Timedelta(7 * 1, unit="D")).all()


def test_backward_asof_handles_holidays_without_reading_ahead(observations):
    focus, selic, fx = observations
    friday = focus.loc[20, "date"]
    focus.loc[20, "date"] = friday - pd.Timedelta(1, unit="D")
    # A later observation must not displace the prior Thursday at Friday's cutoff.
    focus = pd.concat(
        [
            focus,
            pd.DataFrame(
                {"date": [friday + pd.Timedelta(1, unit="D")], "median": [999], "dispersion": [0.4]}
            ),
        ]
    )
    row = build_features(focus, selic, fx).loc[friday + pd.Timedelta(7, unit="D")]
    assert row["focus_source_date"] == friday - pd.Timedelta(1, unit="D")
    assert row["current_level"] == pytest.approx(4.2)


def test_duplicate_dates_are_rejected(observations):
    focus, selic, fx = observations
    with pytest.raises(ValueError, match="unique"):
        build_features(pd.concat([focus, focus.iloc[[0]]]), selic, fx)


def test_nonpositive_fx_is_rejected(observations):
    focus, selic, fx = observations
    fx.loc[20, "value"] = 0
    with pytest.raises(ValueError, match="positive"):
        build_features(focus, selic, fx)


@pytest.mark.parametrize("lag_days", [0, 6, True, 7.5])
def test_release_lag_cannot_be_disabled(observations, lag_days):
    with pytest.raises(ValueError, match="at least 7"):
        build_features(*observations, lag_days=lag_days)


def test_longer_release_lag_is_supported(observations):
    result = build_features(*observations, lag_days=14)
    assert (result.index - result["focus_source_date"] == pd.Timedelta(14, unit="D")).all()
