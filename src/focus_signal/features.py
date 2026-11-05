"""Calendar-aligned features using a conservative public-availability proxy.

The BCB API is a historical archive, not an immutable real-time vintage store.
Seven days between a survey observation and its use is a conservative release
assumption, not a claim that every historical release timestamp is verified.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FOCUS_FEATURE_COLUMNS = (
    "current_level",
    "revision_1w",
    "revision_4w",
    "revision_13w",
    "dispersion",
)
FEATURE_COLUMNS = FOCUS_FEATURE_COLUMNS + (
    "selic_level",
    "selic_change_4w",
    "fx_log_change_1w",
    "fx_log_change_4w",
)


def _validate_daily(frame: pd.DataFrame, columns: tuple[str, ...], name: str) -> pd.DataFrame:
    """Reject ambiguous dates and invalid numbers; preserve genuine missing values."""
    missing = {"date", *columns} - set(frame.columns)
    if missing:
        raise ValueError(f"{name}: missing columns {sorted(missing)}")
    if frame.empty:
        raise ValueError(f"{name}: no observations")
    result = frame.loc[:, ["date", *columns]].copy()
    result["date"] = pd.to_datetime(result["date"], errors="raise")
    if result["date"].dt.tz is not None:
        raise ValueError(f"{name}: use timezone-naive calendar dates")
    if result["date"].isna().any() or result["date"].duplicated().any():
        raise ValueError(f"{name}: dates must be present and unique")
    if (result["date"] != result["date"].dt.normalize()).any():
        raise ValueError(f"{name}: dates must not contain intraday timestamps")
    for column in columns:
        result[column] = pd.to_numeric(result[column], errors="raise")
        if np.isinf(result[column].to_numpy(dtype=float)).any():
            raise ValueError(f"{name}: infinite {column}")
    return result.sort_values("date").reset_index(drop=True)


def _asof_weekly(
    daily: pd.DataFrame, origins: pd.DatetimeIndex, lag_days: int, prefix: str
) -> pd.DataFrame:
    cutoff = origins - pd.Timedelta(lag_days, unit="D")
    aligned = pd.merge_asof(
        pd.DataFrame({"cutoff": cutoff}),
        daily,
        left_on="cutoff",
        right_on="date",
        direction="backward",
        tolerance=pd.Timedelta(7, unit="D"),
    )
    aligned.index = origins
    return aligned.drop(columns="cutoff").rename(columns={"date": f"{prefix}_source_date"})


def build_features(
    focus: pd.DataFrame,
    selic: pd.DataFrame,
    fx: pd.DataFrame,
    *,
    lag_days: int = 7,
    horizon_weeks: int = 4,
) -> pd.DataFrame:
    """Build a complete Friday grid, retaining rows with missing inputs/targets.

    Inputs are daily observations: ``focus`` has date, median, dispersion;
    ``selic`` and ``fx`` have date, value. Each origin uses observations no
    newer than origin minus ``lag_days`` and no more than seven days older
    than that cutoff. No interpolation or forward-looking fill is performed.

    ``target_revision`` is the difference between the availability-proxy
    median at origin + horizon and the current median. Thus its economic
    observation window starts about seven days before the forecast origin.
    ``target_date`` is the assumed availability date, not the survey date.
    """
    if isinstance(lag_days, bool) or not isinstance(lag_days, int) or lag_days < 7:
        raise ValueError("lag_days must be an integer of at least 7")
    if isinstance(horizon_weeks, bool) or not isinstance(horizon_weeks, int) or horizon_weeks < 1:
        raise ValueError("horizon_weeks must be a positive integer")
    focus = _validate_daily(focus, ("median", "dispersion"), "focus")
    selic = _validate_daily(selic, ("value",), "selic")
    fx = _validate_daily(fx, ("value",), "fx")
    if (fx["value"].dropna() <= 0).any():
        raise ValueError("fx: values must be positive for log changes")
    if (focus["dispersion"].dropna() < 0).any():
        raise ValueError("focus: dispersion must be nonnegative")

    origins = pd.date_range(
        focus["date"].min() + pd.Timedelta(lag_days, unit="D"),
        focus["date"].max() + pd.Timedelta(lag_days, unit="D"),
        freq="W-FRI",
        name="origin",
    )
    if origins.empty:
        raise ValueError("focus: not enough history to construct a Friday origin")
    result = _asof_weekly(focus, origins, lag_days, "focus").rename(
        columns={"median": "current_level"}
    )
    result["feature_cutoff"] = origins - pd.Timedelta(lag_days, unit="D")
    for weeks in (1, 4, 13):
        result[f"revision_{weeks}w"] = result["current_level"].diff(weeks)
    rates = _asof_weekly(selic, origins, lag_days, "selic")
    result["selic_source_date"] = rates["selic_source_date"]
    result["selic_level"] = rates["value"]
    result["selic_change_4w"] = result["selic_level"].diff(4)
    exchange = _asof_weekly(fx, origins, lag_days, "fx")
    result["fx_source_date"] = exchange["fx_source_date"]
    log_fx = np.log(exchange["value"])
    result["fx_log_change_1w"] = log_fx.diff(1)
    result["fx_log_change_4w"] = log_fx.diff(4)

    result["target_date"] = origins + pd.Timedelta(7 * horizon_weeks, unit="D")
    result["target_level"] = result["current_level"].shift(-horizon_weeks)
    result["target_source_date"] = result["focus_source_date"].shift(-horizon_weeks)
    result["target_revision"] = result["target_level"] - result["current_level"]
    result.attrs.update(lag_days=lag_days, horizon_weeks=horizon_weeks)
    return result
