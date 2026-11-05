"""Compare actual measurements, allowing tiny cross-platform numeric differences."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

reference, reproduced = map(Path, sys.argv[1:])
for filename in ("metrics.csv", "annual_metrics.csv"):
    left, right = (pd.read_csv(folder / filename) for folder in (reference, reproduced))
    pd.testing.assert_frame_equal(left, right, check_exact=False, rtol=1e-6, atol=1e-8)
left_run, right_run = (
    json.loads((folder / "run.json").read_text()) for folder in (reference, reproduced)
)
assert left_run["selection"]["selected_model"] == right_run["selection"]["selected_model"]
assert left_run["snapshot_manifest_sha256"] == right_run["snapshot_manifest_sha256"]
left, right = (pd.read_csv(folder / "predictions.csv") for folder in (reference, reproduced))
assert left[["period", "model", "origin", "target_date"]].equals(
    right[["period", "model", "origin", "target_date"]]
)
np.testing.assert_allclose(left["y_pred"], right["y_pred"], rtol=1e-6, atol=1e-8)
print("Reproduced selection, snapshot fingerprint, metrics and every prediction")
