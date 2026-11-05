"""Export factual audit tables without redistributing local news/PDF captures.

Usage: python scripts/export_monthly_audit.py SOURCE_RECONSTRUCTION NEW_DESTINATION
This script reads a completed local reconstruction; it neither collects sources
nor trains models. The public validator checks the resulting tables separately.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

EXPORTS = {
    "origin_reconstruction.csv": ("origins.csv", {"evidence_files_json"}),
    "pdf_values_verified.csv": ("candidate_pdf_checks.csv", {"visual_evidence_image"}),
    "followup_2012/ipca_release_dates.csv": (
        "ipca_release_dates.csv",
        {"news_html_capture", "date_evidence_file"},
    ),
    "followup_2012/lag_availability.csv": ("lag_availability.csv", set()),
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export(source: Path, destination: Path) -> dict:
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Use a new or empty destination; existing research is never overwritten")
    contract = json.loads((source / "reconstruction_contract.json").read_text())
    receipt = json.loads((source / "verification.json").read_text())
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "source_project": "diagnostico-focus/reconstruction",
        "source_audit_revision": contract["version"],
        "source_receipt_time_utc": receipt["verified_at_utc"],
        "source_receipt_note": (
            "Historical local receipt; the public validator checks exported-table consistency. "
            "It does not rerun the full local source reconstruction."
        ),
        "input_files": [],
        "files": [],
        "excluded_material": [
            "Full news captures and search extracts",
            "PDFs and review images",
            "Private filesystem paths and links to omitted local evidence files",
        ],
    }
    for relative, (filename, omitted) in EXPORTS.items():
        path = source / relative
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            fields = [field for field in reader.fieldnames if field not in omitted]
            rows = [{key: row[key] for key in fields} for row in reader]
        target = destination / filename
        with target.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        manifest["input_files"].append(
            {
                "path": relative,
                "sha256": digest(path),
                "omitted_columns": sorted(omitted),
            }
        )
        manifest["files"].append({"path": filename, "sha256": digest(target)})
    for relative in ("reconstruction_contract.json", "verification.json", "followup_rules.py"):
        manifest["input_files"].append({"path": relative, "sha256": digest(source / relative)})
    (destination / "export_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    result = export(args.source, args.destination)
    print(f"Exported {len(result['files'])} factual tables to {args.destination}")
