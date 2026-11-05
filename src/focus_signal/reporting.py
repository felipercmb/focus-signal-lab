"""Inspectable experiment artifacts and a static figure for the GitHub README."""

from __future__ import annotations

import json
import platform
from importlib.metadata import version
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from focus_signal.data import sha256
from focus_signal.evaluation import ExperimentResult


def paired_block_intervals(
    predictions: pd.DataFrame, *, block_size: int = 13, repetitions: int = 2000, seed: int = 42
) -> list[dict]:
    """Approximate paired moving-block bootstrap of test MAE differences.

    Each block keeps adjacent errors together; this is descriptive uncertainty
    conditional on the fitted path, not model-selection-adjusted inference.
    """
    test = predictions.loc[predictions["period"].eq("test")].copy()
    test["absolute_error"] = (test["y_pred"] - test["y_true"]).abs()
    errors = test.pivot(index="origin", columns="model", values="absolute_error").sort_index()
    if errors.isna().any().any() or len(errors) < block_size or block_size < 1 or repetitions < 1:
        raise ValueError("Bootstrap requires matched observations and valid block/sample sizes")
    rng = np.random.default_rng(seed)
    n = len(errors)
    starts = rng.integers(0, n - block_size + 1, size=(repetitions, int(np.ceil(n / block_size))))
    indices = (starts[:, :, None] + np.arange(block_size)).reshape(repetitions, -1)[:, :n]
    rows = []
    for model in errors:
        difference = (errors[model] - errors["persistence"]).to_numpy()
        low, high = np.quantile(difference[indices].mean(axis=1), [0.025, 0.975])
        rows.append(
            {
                "model": model,
                "mae_difference_pp": float(difference.mean()),
                "approximate_95pct_lower_pp": float(low),
                "approximate_95pct_upper_pp": float(high),
                "block_weeks": block_size,
                "repetitions": repetitions,
                "seed": seed,
            }
        )
    return rows


def _table(frame: pd.DataFrame, columns: list[str]) -> str:
    # Avoid an optional tabulate dependency just for Markdown output.
    rows = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for _, row in frame[columns].iterrows():
        values = [f"{v:.4f}" if isinstance(v, float) else str(v) for v in row]
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join(rows)


def plot_results(result: ExperimentResult, output: Path, settings: dict, manifest: dict) -> None:
    labels = {
        "persistence": "No change",
        "ridge_focus": "Ridge · Focus",
        "ridge_macro": "Ridge · Focus + macro",
        "random_forest": "Random forest",
    }
    colors = {
        "persistence": "#777F8C",
        "ridge_focus": "#965DCE",
        "ridge_macro": "#007F82",
        "random_forest": "#D27B35",
    }
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.labelcolor": "#27364A",
            "text.color": "#172C43",
            "axes.titleweight": "bold",
            "figure.facecolor": "#FAFBFD",
            "axes.facecolor": "#FAFBFD",
        }
    )
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.5), gridspec_kw={"width_ratios": [1, 1.25]})
    test = result.metrics.loc[result.metrics["period"].eq("test")].sort_values("mae_pp")
    axes[0].barh(
        [labels[x] for x in test["model"]],
        test["mae_pp"],
        color=[colors[x] for x in test["model"]],
        height=0.6,
    )
    for i, value in enumerate(test["mae_pp"]):
        axes[0].text(value + 0.004, i, f"{value:.3f}", va="center", fontsize=10)
    axes[0].invert_yaxis()
    axes[0].set_xlim(0, test["mae_pp"].max() * 1.28)
    axes[0].set_xlabel("Mean absolute error (percentage points)")
    test_period = f"{result.selection['test_start'][:4]}–{result.selection['test_end'][:4]}"
    validation_period = (
        f"{result.selection['validation_start'][:4]}–{result.selection['validation_end'][:4]}"
    )
    snapshot_period = f"{manifest['requested_start']} to {manifest['requested_end']}"
    axes[0].set_title(f"Held-out test · {test_period}", loc="left", pad=16)
    selected = result.selection["selected_model"]
    annual = result.annual_metrics.query("period == 'test'")
    for model in dict.fromkeys(["persistence", selected]):
        values = annual.loc[annual["model"].eq(model)]
        axes[1].plot(
            values["year"],
            values["mae_pp"],
            marker="o",
            linewidth=2,
            color=colors[model],
            label=labels[model],
            linestyle="--" if model == "persistence" else "-",
        )
    axes[1].set_title("Year-to-year stability", loc="left", pad=16)
    axes[1].set_ylabel("Mean absolute error (percentage points)")
    axes[1].set_xticks(sorted(annual["year"].unique()))
    axes[1].set_ylim(bottom=0)
    axes[1].grid(axis="y", alpha=0.2)
    axes[1].legend(frameon=False)
    fig.suptitle(
        "Can ML anticipate changes in Brazil’s inflation expectations?",
        x=0.02,
        ha="left",
        fontsize=16,
        fontweight="bold",
    )
    fig.text(
        0.02,
        0.88,
        f"{settings['horizon_weeks']}-week expectation changes · "
        f"selected on {validation_period} validation: {labels[selected]}",
        fontsize=10,
    )
    fig.text(
        0.02,
        0.06,
        f"Source: Banco Central do Brasil / Focus + SGS · ODbL-1.0 · {snapshot_period}",
        fontsize=8.5,
    )
    fig.text(
        0.02,
        0.025,
        f"{settings['lag_days']}-day availability proxy; archive, not immutable vintages. "
        "Overlapping outcomes; no investment claim.",
        fontsize=8.5,
    )
    fig.subplots_adjust(left=0.16, right=0.98, top=0.76, bottom=0.22, wspace=0.6)
    fig.savefig(output, dpi=170)
    plt.close(fig)


def write_report(
    result: ExperimentResult, frame: pd.DataFrame, snapshot: Path, output: Path
) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((snapshot / "manifest.json").read_text())
    result.predictions.to_csv(output / "predictions.csv", index=False, float_format="%.12g")
    result.metrics.to_csv(output / "metrics.csv", index=False, float_format="%.12g")
    result.annual_metrics.to_csv(output / "annual_metrics.csv", index=False, float_format="%.12g")
    frame.to_csv(output / "features.csv", float_format="%.12g")
    intervals = paired_block_intervals(result.predictions)
    provenance = {
        "selection": result.selection,
        "feature_settings": frame.attrs,
        "snapshot_manifest_sha256": sha256(snapshot / "manifest.json"),
        "python": platform.python_version(),
        "packages": {
            name: version(name) for name in ["numpy", "pandas", "scikit-learn", "matplotlib"]
        },
        "data_quality": {
            "weekly_rows": len(frame),
            "rows_with_complete_features_and_target": int(frame.dropna().shape[0]),
            "first_origin": str(frame.index.min().date()),
            "last_origin": str(frame.index.max().date()),
        },
        "paired_mae_difference_intervals": intervals,
        "interval_note": (
            "Approximate paired moving-block bootstrap; model MAE minus persistence MAE. "
            "Negative favors model. Conditional on fitted path; no multiplicity adjustment "
            "or guarantee."
        ),
    }
    (output / "run.json").write_text(json.dumps(provenance, indent=2) + "\n")
    selected = result.selection["selected_model"]
    selected_test = result.metrics.query("period == 'test' and model == @selected").iloc[0]
    baseline = result.metrics.query("period == 'test' and model == 'persistence'").iloc[0]
    interval_table = _table(
        pd.DataFrame(intervals),
        [
            "model",
            "mae_difference_pp",
            "approximate_95pct_lower_pp",
            "approximate_95pct_upper_pp",
        ],
    )
    summary = f"""# Measured benchmark

Selected on validation MAE: **{selected}**. Test MAE: **{selected_test.mae_pp:.4f} pp**;
persistence: **{baseline.mae_pp:.4f} pp**. Relative MAE improvement:
**{100 * selected_test.skill_vs_persistence:.1f}%**, across {selected_test.n} matched test origins.
These are historical measurements, not evidence of deployable trading value.

{_table(result.metrics, ["period", "model", "n", "mae_pp", "rmse_pp", "skill_vs_persistence"])}

![Measured model errors and annual stability](benchmark.png)

## Uncertainty

Approximate 95% paired moving-block intervals (13 consecutive weeks, 2,000 resamples).
The quantity is model MAE minus persistence MAE in percentage points; negative favors ML.
An interval crossing zero does not support a clear advantage on this diagnostic.
Overlapping four-week outcomes are not independent. These intervals condition on the
fitted prediction path and do not correct for candidate selection or all structural changes.

{interval_table}

## How to audit

`predictions.csv` contains each origin, actual/predicted outcome, refit date and latest
training-label availability date. `features.csv` records input dates and exact target
alignment. `annual_metrics.csv` exposes year-to-year instability. `run.json` records
the snapshot fingerprint, software versions, selection rule and uncertainty settings.

Source: [Banco Central do Brasil](https://dadosabertos.bcb.gov.br/dataset/expectativas-mercado),
[SGS 432](https://dadosabertos.bcb.gov.br/dataset/432-taxa-de-juros---meta-selic-definida-pelo-copom),
[SGS 1](https://dadosabertos.bcb.gov.br/dataset/1-taxa-de-cambio---livre---dolar-americano-venda---diario).
Data and derived tables: [ODbL-1.0](https://opendatacommons.org/licenses/odbl/1-0/).
Current API archive; {frame.attrs["lag_days"]}-day publication proxy,
not historically verified vintages. Source window: {manifest["requested_start"]} to
{manifest["requested_end"]}.
The target includes rolling-horizon and respondent-composition effects, not just beliefs.
"""
    (output / "RESULTS.md").write_text(summary)
    plot_results(result, output / "benchmark.png", frame.attrs, manifest)
    return provenance
