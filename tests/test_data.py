import hashlib
import json
from urllib.error import HTTPError

import pandas as pd
import pytest

from focus_signal.data import fetch_json, load_snapshot, normalize_focus, validate_series


def test_focus_does_not_silently_mix_population():
    record = {
        "Indicador": "IPCA",
        "baseCalculo": 0,
        "Suavizada": "S",
        "Data": "2020-01-02",
        "Mediana": 4.1,
        "DesvioPadrao": 0.4,
    }
    assert normalize_focus([record]).iloc[0]["median"] == 4.1
    with pytest.raises(ValueError, match="unexpected indicator"):
        normalize_focus([record | {"baseCalculo": 1}])


@pytest.mark.parametrize("values", [[1.0, float("nan")], [1.0, float("inf")], [1.0, 0.0]])
def test_bad_fx_values_fail_closed(values):
    with pytest.raises(ValueError):
        validate_series(pd.DataFrame({"date": ["2020-01-01", "2020-01-02"], "value": values}), "fx")


def test_duplicate_observations_are_not_arbitrarily_resolved():
    with pytest.raises(ValueError, match="duplicate"):
        validate_series(pd.DataFrame({"date": ["2020-01-01"] * 2, "value": [4, 5]}), "fx")


def test_tampering_is_detected_before_analysis(tmp_path):
    csv = tmp_path / "focus.csv"
    csv.write_text("date,median,dispersion\n2020-01-01,4,0.5\n")
    digest = hashlib.sha256(csv.read_bytes()).hexdigest()
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "files": {"focus.csv": digest},
            }
        )
    )
    csv.write_text("date,median,dispersion\n2020-01-01,400,0.5\n")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_snapshot(tmp_path)


def test_manifest_cannot_escape_snapshot_directory(tmp_path):
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "files": {"../private.txt": "unused"},
            }
        )
    )
    with pytest.raises(ValueError, match="escapes"):
        load_snapshot(tmp_path)


def test_permanent_http_errors_are_not_retried(monkeypatch):
    calls = []

    def bad_request(*args, **kwargs):
        calls.append(1)
        raise HTTPError("https://example.org", 400, "Bad Request", {}, None)

    monkeypatch.setattr("focus_signal.data.urlopen", bad_request)
    with pytest.raises(HTTPError):
        fetch_json("https://example.org")
    assert len(calls) == 1
