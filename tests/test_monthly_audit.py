"""Integrity and temporal boundary checks against the public research snapshot."""

import csv
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from focus_signal.monthly_audit import AuditValidationError, main, validate_audit

SNAPSHOT = Path(__file__).resolve().parents[1] / "research" / "monthly-ipca"


@pytest.fixture
def audit(tmp_path):
    destination = tmp_path / "audit"
    shutil.copytree(SNAPSHOT, destination)
    return destination


def _manifest(audit, transform):
    path = audit / "export_manifest.json"
    payload = json.loads(path.read_text())
    transform(payload)
    path.write_text(json.dumps(payload))


def _mutate(audit, filename, transform):
    path = audit / filename
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        rows = list(reader)
    transform(rows)
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    # The semantics tests use an internally consistent new manifest so a hash
    # error cannot conceal the temporal or cross-table failure being tested.
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    _manifest(
        audit,
        lambda payload: [
            entry.update(sha256=digest) for entry in payload["files"] if entry["path"] == filename
        ],
    )


def test_shipped_snapshot_recomputes_expected_counts():
    summary = validate_audit(SNAPSHOT)
    assert {
        key: summary[key]
        for key in (
            "rows",
            "warmup",
            "train",
            "documented",
            "stronger",
            "train_documented",
            "train_stronger",
            "candidate_checks",
            "max_difference_pp",
            "unresolved",
        )
    } == {
        "rows": 84,
        "warmup": 12,
        "train": 72,
        "documented": 84,
        "stronger": 83,
        "train_documented": 72,
        "train_stronger": 71,
        "candidate_checks": 168,
        "max_difference_pp": "0.00",
        "unresolved": ["2013-07"],
    }
    assert summary["lag_availability"] == {
        "previous_year_available_before_cutoff": 12,
        "previous_calendar_month_available_before_cutoff": 0,
    }
    assert summary["hashes_verified"] is True
    assert summary["training_ready"] is False
    assert summary["first_publication_certified"] is False
    assert summary["immutable_vintages_certified"] is False
    assert "provenance not reverified" in summary["scope"]


def test_hash_mismatch_is_detected_before_semantic_validation(audit):
    with (audit / "origins.csv").open("a") as stream:
        stream.write("\n")
    with pytest.raises(AuditValidationError, match="SHA256 mismatch: origins.csv"):
        validate_audit(audit)


def test_inspection_without_hashes_is_explicit_in_summary(audit):
    _manifest(audit, lambda payload: payload["files"][0].update(sha256="0" * 64))
    assert validate_audit(audit, verify_hashes=False)["hashes_verified"] is False


@pytest.mark.parametrize("path", ["../outside.csv", "/tmp/outside.csv", "..\\outside.csv"])
def test_manifest_paths_cannot_escape_export(audit, path):
    _manifest(audit, lambda payload: payload["files"][0].update(path=path))
    with pytest.raises(AuditValidationError, match="escapes export"):
        validate_audit(audit)


def test_manifest_cannot_follow_symlink_outside_export(audit):
    outside = audit.parent / "outside.csv"
    outside.write_bytes((audit / "origins.csv").read_bytes())
    (audit / "origins.csv").unlink()
    (audit / "origins.csv").symlink_to(outside)
    with pytest.raises(AuditValidationError, match="escapes export"):
        validate_audit(audit)


def test_manifest_must_cover_all_tables(audit):
    _manifest(audit, lambda payload: payload["files"].pop())
    with pytest.raises(AuditValidationError, match="cover all four"):
        validate_audit(audit)


def test_duplicate_origin_is_rejected(audit):
    _mutate(audit, "origins.csv", lambda rows: rows[1].update(target_month=rows[0]["target_month"]))
    with pytest.raises(AuditValidationError, match="duplicate target month"):
        validate_audit(audit)


def test_missing_month_is_rejected(audit):
    _mutate(audit, "origins.csv", lambda rows: rows.pop(20))
    with pytest.raises(AuditValidationError, match="noncontiguous months"):
        validate_audit(audit)


def test_origin_uses_historical_sao_paulo_offset(audit):
    # January 2012 uses -02:00; a fixed -03:00 offset changes the forecast origin.
    _mutate(
        audit, "origins.csv", lambda rows: rows[0].update(origin_at="2012-01-01T00:00:00-03:00")
    )
    with pytest.raises(AuditValidationError, match="midnight in Sao Paulo"):
        validate_audit(audit)


def test_edition_available_on_origin_is_too_late(audit):
    _mutate(
        audit,
        "origins.csv",
        lambda rows: rows[0].update(edition_available_by_corroborated="2012-01-01"),
    )
    with pytest.raises(AuditValidationError, match="availability < origin"):
        validate_audit(audit)


def test_target_release_on_origin_is_not_allowed(audit):
    _mutate(
        audit, "origins.csv", lambda rows: rows[0].update(target_ipca_release_date="2012-01-01")
    )
    with pytest.raises(
        AuditValidationError, match="following calendar month|strictly after origin"
    ):
        validate_audit(audit)


def test_same_day_ipca_release_is_excluded_from_latest_known(audit):
    # The Jan2012 reference is Dec23. Moving the Nov2011 release to Dec23
    # makes that observation unavailable under the strict date rule.
    _mutate(
        audit, "ipca_release_dates.csv", lambda rows: rows[10].update(release_date="2011-12-23")
    )
    with pytest.raises(AuditValidationError, match="same-day releases must be excluded"):
        validate_audit(audit)


def test_latest_known_is_recomputed_not_just_checked_before_reference(audit):
    _mutate(
        audit,
        "origins.csv",
        lambda rows: rows[0].update(
            latest_known_ipca_month_at_focus_reference="2011-10",
            latest_known_ipca_release_date="2011-11-11",
        ),
    )
    with pytest.raises(AuditValidationError, match="latest-known IPCA mismatch"):
        validate_audit(audit)


@pytest.mark.parametrize("field", ["training_ready", "first_publication_date_certified"])
def test_unjustified_certification_is_rejected(audit, field):
    _mutate(audit, "origins.csv", lambda rows: rows[0].update({field: "True"}))
    with pytest.raises(
        AuditValidationError, match="not training-ready|first publication uncertified"
    ):
        validate_audit(audit)


def test_selected_origin_must_match_the_candidate_hash(audit):
    _mutate(audit, "origins.csv", lambda rows: rows[0].update(pdf_sha256="0" * 64))
    with pytest.raises(AuditValidationError, match="selected candidate mismatch for pdf_sha256"):
        validate_audit(audit)


def test_both_candidates_are_checked_even_when_not_selected(audit):
    # The first candidate is rank1 for Jan2012; the selected origin uses rank2.
    _mutate(
        audit, "candidate_pdf_checks.csv", lambda rows: rows[0].update(printed_median_pct="0.61")
    )
    with pytest.raises(AuditValidationError, match="reported absolute difference is incorrect"):
        validate_audit(audit)


def test_candidate_disagreement_above_print_precision_is_rejected(audit):
    _mutate(
        audit,
        "candidate_pdf_checks.csv",
        lambda rows: rows[0].update(printed_median_pct="0.61", absolute_difference_pp="0.02"),
    )
    with pytest.raises(AuditValidationError, match="exceeds print tolerance"):
        validate_audit(audit)


def test_duplicate_candidate_rank_is_rejected(audit):
    _mutate(audit, "candidate_pdf_checks.csv", lambda rows: rows[1].update(candidate_rank="1"))
    with pytest.raises(AuditValidationError, match="duplicate candidate"):
        validate_audit(audit)


def test_documented_flag_is_recomputed_from_prerequisites(audit):
    _mutate(
        audit, "origins.csv", lambda rows: rows[0].update(source_full_capture_available="False")
    )
    with pytest.raises(AuditValidationError, match="documented flag disagrees"):
        validate_audit(audit)


def test_unresolved_july_cannot_be_counted_as_stronger(audit):
    def mutate(rows):
        row = next(row for row in rows if row["target_month"] == "2013-07")
        row["candidate_for_stronger_calendar_subset"] = "True"

    _mutate(audit, "origins.csv", mutate)
    with pytest.raises(AuditValidationError, match="stronger flag disagrees"):
        validate_audit(audit)


def test_lag_availability_flag_is_recomputed(audit):
    _mutate(
        audit,
        "lag_availability.csv",
        lambda rows: rows[0].update(previous_calendar_month_available_before_cutoff="True"),
    )
    with pytest.raises(AuditValidationError, match="disagrees with strict release cutoff"):
        validate_audit(audit)


def test_lag_latest_known_must_match_origin(audit):
    _mutate(
        audit,
        "lag_availability.csv",
        lambda rows: rows[0].update(
            latest_known_ipca_month="2011-10", latest_known_release_date="2011-11-11"
        ),
    )
    with pytest.raises(AuditValidationError, match="lag 2012-01: latest-known IPCA mismatch"):
        validate_audit(audit)


def test_cli_prints_the_same_deterministic_summary(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["monthly_audit", str(SNAPSHOT)])
    main()
    assert json.loads(capsys.readouterr().out) == validate_audit(SNAPSHOT)
