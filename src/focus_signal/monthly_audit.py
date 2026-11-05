"""Validate the share-safe monthly IPCA research tables using only the stdlib.

This checks exported records and their consistency. It does not reopen the
underlying news captures or PDFs, certify publication timestamps, establish
immutable historical vintages, or make this research training-ready.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

TABLES = (
    "origins.csv",
    "candidate_pdf_checks.csv",
    "ipca_release_dates.csv",
    "lag_availability.csv",
)
STRONGER_STATUSES = {
    "latest_archived_reference_before_origin",
    "newer_reference_release_on_or_after_cutoff_corroborated",
}
IDENTITY_STATUSES = {
    "content_corroborated_within_two_candidates",
    "reference_explicitly_identified",
}
LIMITATION = "current_archives_not_immutable_vintages;identity_search_limited_to_two_candidates"
FORBIDDEN_COLUMNS = {
    "evidence_files_json",
    "visual_evidence_image",
    "news_html_capture",
    "date_evidence_file",
}
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class AuditValidationError(ValueError):
    """An exported table, hash, or cross-table invariant is invalid."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditValidationError(message)


def _month(value: str) -> str:
    _require(bool(re.fullmatch(r"\d{4}-\d{2}", value)), f"invalid month: {value!r}")
    try:
        date.fromisoformat(value + "-01")
    except ValueError as exc:
        raise AuditValidationError(f"invalid month: {value!r}") from exc
    return value


def _shift_month(value: str, offset: int) -> str:
    year, month = map(int, _month(value).split("-"))
    shifted = year * 12 + month - 1 + offset
    return f"{shifted // 12:04d}-{shifted % 12 + 1:02d}"


def _date(value: str) -> date:
    _require(bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)), f"invalid date: {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AuditValidationError(f"invalid date: {value!r}") from exc


def _bool(value: str) -> bool:
    _require(value.lower() in {"true", "false"}, f"invalid boolean: {value!r}")
    return value.lower() == "true"


def _decimal(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise AuditValidationError(f"invalid decimal: {value!r}") from exc
    _require(result.is_finite(), f"nonfinite decimal: {value!r}")
    return result


def _difference(printed: str, current: str, reported: str, label: str) -> Decimal:
    _require(bool(re.fullmatch(r"-?\d+\.\d{2}", printed)), f"{label}: expected 2 printed decimals")
    difference = abs(_decimal(printed) - _decimal(current))
    _require(
        difference == _decimal(reported), f"{label}: reported absolute difference is incorrect"
    )
    # Reproduce the source audit's half-unit tolerance at two printed decimals.
    _require(difference <= Decimal("0.005"), f"{label}: PDF/API difference exceeds print tolerance")
    return difference


def _url(value: str, label: str) -> None:
    parsed = urlparse(value)
    _require(parsed.scheme in {"http", "https"} and bool(parsed.netloc), f"{label}: invalid URL")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames or []
        _require(columns and len(columns) == len(set(columns)), f"{path.name}: invalid CSV columns")
        _require(
            not (FORBIDDEN_COLUMNS & set(columns)), f"{path.name}: private evidence paths present"
        )
        rows = list(reader)
    _require(bool(rows), f"{path.name}: empty table")
    _require(
        all(None not in r and all(v is not None for v in r.values()) for r in rows),
        f"{path.name}: malformed CSV row",
    )
    return rows


def _check_manifest(directory: Path, verify_hashes: bool) -> None:
    try:
        manifest = json.loads((directory / "export_manifest.json").read_text(encoding="utf-8"))
        entries = manifest["files"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AuditValidationError("invalid or missing export_manifest.json") from exc
    _require(isinstance(entries, list) and bool(entries), "manifest files must be a nonempty list")
    found = set()
    for entry in entries:
        _require(isinstance(entry, dict), "manifest entry must be an object")
        name, digest = entry.get("path"), entry.get("sha256")
        _require(isinstance(name, str) and bool(name), "manifest entry has no path")
        relative = PurePosixPath(name)
        _require(
            not relative.is_absolute() and ".." not in relative.parts and "\\" not in name,
            f"manifest path escapes export directory: {name}",
        )
        _require(name not in found, f"duplicate manifest path: {name}")
        _require(
            isinstance(digest, str) and SHA256.fullmatch(digest) is not None,
            f"invalid SHA256 for {name}",
        )
        path = (directory / relative).resolve()
        _require(path.is_relative_to(directory), f"manifest path escapes export directory: {name}")
        _require(path.is_file(), f"manifest file missing: {name}")
        if verify_hashes:
            _require(
                hashlib.sha256(path.read_bytes()).hexdigest() == digest, f"SHA256 mismatch: {name}"
            )
        found.add(name)
    _require(set(TABLES).issubset(found), "manifest must cover all four audit tables")


def _index_months(rows: list[dict[str, str]], expected: list[str], label: str) -> dict:
    months = [_month(row["target_month"]) for row in rows]
    _require(len(months) == len(set(months)), f"{label}: duplicate target month")
    _require(sorted(months) == expected, f"{label}: missing, extra, or noncontiguous months")
    return {row["target_month"]: row for row in rows}


def _candidate_checks(rows: list[dict[str, str]], months: list[str]) -> tuple[dict, Decimal]:
    _require(len(rows) == 2 * len(months), "candidate checks: expected exactly 168 records")
    candidates = {}
    maximum = Decimal("0.00")
    for row in rows:
        month = _month(row["target_month"])
        rank = row["candidate_rank"]
        label = f"candidate {month}/{rank}"
        _require(month in months and rank in {"1", "2"}, f"{label}: invalid month or rank")
        key = (month, rank)
        _require(key not in candidates, f"{label}: duplicate candidate")
        reference = _date(row["reference_date"])
        _require(
            reference.strftime("%Y-%m") == _shift_month(month, -1),
            f"{label}: reference must be in the previous month",
        )
        _require(row["origin_date"] == month + "-01", f"{label}: incorrect origin date")
        _require(
            row["pdf_url"] == f"https://www.bcb.gov.br/content/focus/focus/R{reference:%Y%m%d}.pdf",
            f"{label}: PDF URL/reference mismatch",
        )
        _require(SHA256.fullmatch(row["pdf_sha256"]) is not None, f"{label}: invalid PDF hash")
        _require(row["pdf_page"].isdigit() and int(row["pdf_page"]) >= 1, f"{label}: invalid page")
        _require(
            _bool(row["printed_reference_matches_filename"]), f"{label}: reference not verified"
        )
        _require(
            row["publication_status"] == "not_inferred_from_candidate_rank",
            f"{label}: candidate rank must not certify publication",
        )
        difference = _difference(
            row["printed_median_pct"], row["api_median_pct"], row["absolute_difference_pp"], label
        )
        maximum = max(maximum, difference)
        _require(
            row["pdf_status"] == "matches_api_at_print_precision", f"{label}: PDF status mismatch"
        )
        visual = _bool(row["visual_reviewed"])
        if row["value_verification_method"] == "independent_visual_transcription_of_rendered_pdf":
            _require(visual, f"{label}: visual method requires recorded visual review")
        else:
            _require(
                row["value_verification_method"] == "text_extraction_with_semantic_column_anchors"
                and row["text_extraction_status"] == "matches_api_at_print_precision",
                f"{label}: unsupported extraction method/status",
            )
        candidates[key] = row
    for month in months:
        _require(
            (month, "1") in candidates and (month, "2") in candidates,
            f"{month}: exactly two distinct candidates required",
        )
        _require(
            candidates[month, "1"]["reference_date"] > candidates[month, "2"]["reference_date"],
            f"{month}: rank 1 must have the newer distinct reference",
        )
    return candidates, maximum


def _release_calendar(releases: list[dict], origins: dict) -> dict[str, date]:
    indexed = _index_months(
        releases, [_shift_month("2011-01", n) for n in range(12)], "2011 releases"
    )
    calendar = {}
    for month, row in indexed.items():
        _require(
            row["release_date_status"] == "verified_against_official_news_metadata",
            f"{month}: unsupported release evidence status",
        )
        _require(
            not _bool(row["historical_vintage_verified"]), f"{month}: vintage cannot be certified"
        )
        _url(row["news_url"], f"{month}: release news")
        _decimal(row["monthly_pct"])
        calendar[month] = _date(row["release_date"])
    calendar.update(
        {month: _date(row["target_ipca_release_date"]) for month, row in origins.items()}
    )
    for month, release in calendar.items():
        _require(
            release.strftime("%Y-%m") == _shift_month(month, 1),
            f"{month}: IPCA release must be in the following calendar month",
        )
    return calendar


def _latest_known(calendar: dict[str, date], reference: date) -> tuple[str, date]:
    known = [(month, released) for month, released in calendar.items() if released < reference]
    _require(bool(known), f"no IPCA release strictly before {reference}")
    return max(known, key=lambda pair: pair[0])


def _origin_checks(origins: dict, candidates: dict, calendar: dict) -> tuple[Counter, list[str]]:
    counts = Counter()
    unresolved = []
    for month, row in sorted(origins.items()):
        label = f"origin {month}"
        expected = datetime.fromisoformat(month + "-01").replace(
            tzinfo=ZoneInfo("America/Sao_Paulo")
        )
        _require(
            row["origin_at"] == expected.isoformat(),
            f"{label}: origin must be midnight in Sao Paulo",
        )
        origin = expected.date()
        reference = _date(row["focus_reference_date"])
        source = _date(row["source_publication_date"])
        available = _date(row["edition_available_by_corroborated"])
        _require(
            reference.strftime("%Y-%m") == _shift_month(month, -1),
            f"{label}: reference must be in previous calendar month",
        )
        _require(
            reference <= source <= available < origin,
            f"{label}: require reference <= source <= availability < origin",
        )
        if row["release_date_claimed"]:
            _require(
                reference <= _date(row["release_date_claimed"]) <= source,
                f"{label}: claimed release conflicts with reference/source dates",
            )
        _require(
            not _bool(row["first_publication_date_certified"]),
            f"{label}: first publication uncertified",
        )
        _require(not _bool(row["training_ready"]), f"{label}: research is not training-ready")
        _require(
            row["base_calculo"] == "0" and row["printed_decimal_places"] == "2",
            f"{label}: expected base 0 and two printed decimal places",
        )
        _require(
            row["identity_status"] in IDENTITY_STATUSES, f"{label}: unsupported identity status"
        )
        _require(
            row["value_vintage_status"] == "archived_pdf_value_verified_current_archive",
            f"{label}: unsupported value-vintage status",
        )
        _require(
            row["universal_limit"] == LIMITATION, f"{label}: archive limitations must be retained"
        )
        candidate = candidates.get((month, row["candidate_rank"]))
        _require(candidate is not None, f"{label}: selected candidate missing")
        for origin_key, candidate_key in (
            ("focus_reference_date", "reference_date"),
            ("pdf_url", "pdf_url"),
            ("pdf_sha256", "pdf_sha256"),
            ("pdf_page", "pdf_page"),
            ("median_monthly_pct_printed", "printed_median_pct"),
            ("api_median_monthly_pct_current", "api_median_pct"),
            ("pdf_api_absolute_difference_pp", "absolute_difference_pp"),
        ):
            _require(
                row[origin_key] == candidate[candidate_key],
                f"{label}: selected candidate mismatch for {origin_key}",
            )
        _difference(
            row["median_monthly_pct_printed"],
            row["api_median_monthly_pct_current"],
            row["pdf_api_absolute_difference_pp"],
            label,
        )
        after = calendar[month] > origin
        _require(
            after and _bool(row["target_release_after_origin"]) == after,
            f"{label}: target release must be strictly after origin",
        )
        known_month, known_date = _latest_known(calendar, reference)
        _require(
            row["latest_known_ipca_month_at_focus_reference"] == known_month
            and _date(row["latest_known_ipca_release_date"]) == known_date,
            f"{label}: latest-known IPCA mismatch; same-day releases must be excluded",
        )
        same_day = ";".join(sorted(m for m, d in calendar.items() if d == reference))
        _require(
            row["same_day_ipca_excluded"] == same_day, f"{label}: incorrect same-day exclusions"
        )
        quoted = _bool(row["monthly_value_directly_quoted_before_origin"])
        quote_date = row["monthly_value_available_by_direct_quote"]
        _require(quoted == bool(quote_date), f"{label}: direct-quote flag/date mismatch")
        if quoted:
            _require(
                reference <= _date(quote_date) < origin,
                f"{label}: quote not strictly before origin",
            )
        urls = json.loads(row["evidence_urls_json"])
        _require(isinstance(urls, list) and bool(urls), f"{label}: evidence URLs missing")
        for url in urls:
            _url(url, label)
        # Recompute the source methodology, rather than trusting the subset flags.
        documented = bool(
            candidate
            and _bool(row["source_full_capture_available"])
            and not _bool(row["known_source_update_after_origin_flag"])
        )
        latestness = row["selection_latest_status"]
        _require(
            latestness in STRONGER_STATUSES | {"newer_reference_publication_unresolved"},
            f"{label}: unknown latestness status",
        )
        if latestness == "latest_archived_reference_before_origin":
            _require(row["candidate_rank"] == "1", f"{label}: latest reference must be rank 1")
        else:
            _require(row["candidate_rank"] == "2", f"{label}: newer candidate must exist")
        stronger = documented and latestness in STRONGER_STATUSES
        _require(
            _bool(row["candidate_for_documented_snapshot_subset"]) == documented,
            f"{label}: documented flag disagrees with prerequisites",
        )
        _require(
            _bool(row["candidate_for_stronger_calendar_subset"]) == stronger,
            f"{label}: stronger flag disagrees with prerequisites",
        )
        split = "warmup" if month < "2013-01" else "train"
        _require(row["split"] == split, f"{label}: incorrect split")
        counts[split] += 1
        counts["documented"] += int(documented)
        counts["stronger"] += int(stronger)
        counts[f"{split}_documented"] += int(documented)
        counts[f"{split}_stronger"] += int(stronger)
        if latestness == "newer_reference_publication_unresolved":
            unresolved.append(month)
            _require(
                "latest_public_edition_unresolved" in row["remaining_gaps"].split(";"),
                f"{label}: unresolved latestness gap must remain visible",
            )
    return counts, unresolved


def _lag_checks(rows: list[dict], origins: dict, calendar: dict) -> dict:
    indexed = _index_months(rows, [_shift_month("2012-01", n) for n in range(12)], "2012 lags")
    counts = Counter()
    for month, row in indexed.items():
        origin = origins[month]
        reference = _date(row["focus_reference_date"])
        _require(
            row["focus_reference_date"] == origin["focus_reference_date"],
            f"lag {month}: reference disagrees with selected origin",
        )
        known_month, known_date = _latest_known(calendar, reference)
        _require(
            row["latest_known_ipca_month"] == known_month
            and _date(row["latest_known_release_date"]) == known_date
            and row["latest_known_ipca_month"]
            == origin["latest_known_ipca_month_at_focus_reference"]
            and row["latest_known_release_date"] == origin["latest_known_ipca_release_date"],
            f"lag {month}: latest-known IPCA mismatch",
        )
        for offset, month_key, date_key, flag_key in (
            (
                -12,
                "previous_year_same_month",
                "previous_year_release_date",
                "previous_year_available_before_cutoff",
            ),
            (
                -1,
                "previous_calendar_month",
                "previous_calendar_month_release_date",
                "previous_calendar_month_available_before_cutoff",
            ),
        ):
            prior_month = _shift_month(month, offset)
            _require(
                row[month_key] == prior_month and _date(row[date_key]) == calendar[prior_month],
                f"lag {month}: inconsistent {month_key} or release date",
            )
            available = calendar[prior_month] < reference
            _require(
                _bool(row[flag_key]) == available,
                f"lag {month}: {flag_key} disagrees with strict release cutoff",
            )
            counts[flag_key] += int(available)
    return dict(counts)


def validate_audit(directory: str | Path, *, verify_hashes: bool = True) -> dict:
    """Return deterministic facts after validating the exported monthly audit.

    ``verify_hashes=False`` is useful for inspecting proposed table revisions;
    the returned summary explicitly records that integrity was not checked.
    Publication/reproduction workflows should retain the default ``True``.
    """
    directory = Path(directory).resolve()
    _check_manifest(directory, verify_hashes)
    try:
        tables = {name: _read_csv(directory / name) for name in TABLES}
        months = [_shift_month("2012-01", n) for n in range(84)]
        origins = _index_months(tables["origins.csv"], months, "origins")
        candidates, maximum = _candidate_checks(tables["candidate_pdf_checks.csv"], months)
        calendar = _release_calendar(tables["ipca_release_dates.csv"], origins)
        counts, unresolved = _origin_checks(origins, candidates, calendar)
        lag_counts = _lag_checks(tables["lag_availability.csv"], origins, calendar)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AuditValidationError(f"missing or malformed audit field: {exc}") from exc
    return {
        "rows": len(origins),
        "warmup": counts["warmup"],
        "train": counts["train"],
        "documented": counts["documented"],
        "stronger": counts["stronger"],
        "train_documented": counts["train_documented"],
        "train_stronger": counts["train_stronger"],
        "unresolved": unresolved,
        "candidate_checks": len(candidates),
        "max_difference_pp": str(maximum),
        "lag_availability": lag_counts,
        "training_ready": False,
        "first_publication_certified": False,
        "immutable_vintages_certified": False,
        "hashes_verified": verify_hashes,
        "scope": "share-safe table consistency; underlying news/PDF provenance not reverified",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="directory containing export_manifest.json")
    args = parser.parse_args()
    try:
        summary = validate_audit(args.directory)
    except (AuditValidationError, OSError) as exc:
        parser.exit(1, f"Audit validation failed: {exc}\n")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
