import pandas as pd
import pytest

from focus_signal.reporting import paired_block_intervals


def test_paired_intervals_preserve_known_difference_and_are_repeatable():
    rows = []
    for origin in pd.date_range("2021-01-01", periods=30, freq="W-FRI"):
        for model, prediction in {"persistence": 0, "perfect": 1}.items():
            rows.append(
                {
                    "period": "test",
                    "origin": origin,
                    "model": model,
                    "y_true": 1,
                    "y_pred": prediction,
                }
            )
    frame = pd.DataFrame(rows)
    first = paired_block_intervals(frame, repetitions=50)
    assert first == paired_block_intervals(frame, repetitions=50)
    perfect = next(row for row in first if row["model"] == "perfect")
    assert perfect["mae_difference_pp"] == -1
    assert perfect["approximate_95pct_lower_pp"] == -1
    assert perfect["approximate_95pct_upper_pp"] == -1


def test_unmatched_predictions_are_rejected():
    rows = []
    for i, origin in enumerate(pd.date_range("2021-01-01", periods=30, freq="W-FRI")):
        for model in ["persistence"] if i == 0 else ["persistence", "model"]:
            rows.append(
                {"period": "test", "origin": origin, "model": model, "y_true": 1, "y_pred": 0}
            )
    with pytest.raises(ValueError, match="matched observations"):
        paired_block_intervals(pd.DataFrame(rows))
