"""Download, validate and fingerprint public Banco Central datasets.

No credentials are required. Downloads are explicit: analysis and tests work offline.
The date on an API record is an observation date, not its publication timestamp.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

FOCUS_URL = (
    "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/"
    "ExpectativasMercadoInflacao12Meses"
)
SOURCES = {
    "focus": "https://dadosabertos.bcb.gov.br/dataset/expectativas-mercado",
    "selic": "https://dadosabertos.bcb.gov.br/dataset/432-taxa-de-juros---meta-selic-definida-pelo-copom",
    "fx": "https://dadosabertos.bcb.gov.br/dataset/1-taxa-de-cambio---livre---dolar-americano-venda---diario",
}
LICENSE_URL = "https://opendatacommons.org/licenses/odbl/1-0/"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_json(url: str, *, attempts: int = 3) -> tuple[object, bytes]:
    """Bounded retries for transient failures; never swallow malformed responses."""
    if attempts < 1:
        raise ValueError("attempts must be positive")
    for attempt in range(attempts):
        try:
            req = Request(url, headers={"User-Agent": "focus-signal-lab/0.1 (public research)"})
            with urlopen(req, timeout=60) as response:
                raw = response.read()
            return json.loads(raw), raw
        except (HTTPError, URLError, TimeoutError) as error:
            if isinstance(error, HTTPError) and error.code not in {429, 500, 502, 503, 504}:
                raise
            if attempt == attempts - 1:
                raise
            time.sleep(2**attempt)
    raise RuntimeError("unreachable")


def validate_series(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    """Fail closed on ambiguous keys, missing observations or non-finite values."""
    columns = ["median", "dispersion"] if name == "focus" else ["value"]
    required = ["date", *columns]
    if not set(required).issubset(frame):
        raise ValueError(f"{name}: missing columns {required}")
    frame = frame[required].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    if frame.empty or frame["date"].isna().any() or frame["date"].duplicated().any():
        raise ValueError(f"{name}: empty data, missing dates or duplicate dates")
    if frame["date"].dt.tz is not None:
        raise ValueError(f"{name}: expected timezone-naive calendar dates")
    if (frame["date"] != frame["date"].dt.normalize()).any():
        raise ValueError(f"{name}: expected daily observations")
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if not np.isfinite(frame[column].to_numpy(dtype=float)).all():
            raise ValueError(f"{name}: {column} contains missing or non-finite values")
    if name == "fx" and (frame["value"] <= 0).any():
        raise ValueError("fx: exchange rate must be positive")
    if name == "focus" and (frame["dispersion"] < 0).any():
        raise ValueError("focus: dispersion cannot be negative")
    return frame.sort_values("date").reset_index(drop=True)


def normalize_focus(records: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(records)
    required = {"Indicador", "baseCalculo", "Suavizada", "Data", "Mediana", "DesvioPadrao"}
    if frame.empty or not required.issubset(frame):
        raise ValueError("focus: missing records or unexpected API schema")
    valid = frame["Indicador"].eq("IPCA") & frame["baseCalculo"].eq(0) & frame["Suavizada"].eq("S")
    if not valid.all():
        raise ValueError("focus: response contains an unexpected indicator, base or smoothing")
    return validate_series(
        frame.rename(columns={"Data": "date", "Mediana": "median", "DesvioPadrao": "dispersion"}),
        "focus",
    )


def normalize_sgs(records: list[dict], name: str) -> pd.DataFrame:
    frame = pd.DataFrame(records)
    if frame.empty or not {"data", "valor"}.issubset(frame):
        raise ValueError(f"{name}: missing records or unexpected SGS schema")
    frame["date"] = pd.to_datetime(frame["data"], format="%d/%m/%Y", errors="raise")
    frame["value"] = pd.to_numeric(frame["valor"], errors="raise")
    return validate_series(frame, name)


def download_snapshot(
    destination: Path, start: str = "2010-01-01", end: str = "2025-12-31"
) -> dict:
    """Fetch bounded yearly SGS slices and paginated Focus observations.

    A destination must be new/empty to protect existing evidence. The manifest is
    written last, so interrupted downloads cannot be mistaken for a valid snapshot.
    """
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    if start_date > end_date or end_date >= datetime.now(UTC).date():
        raise ValueError("Use an ordered historical date range ending before today")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"Destination must be empty: {destination}")
    raw_dir = destination / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "requested_start": start,
        "requested_end": end,
        "publisher": "Banco Central do Brasil",
        "data_license": "ODbL-1.0",
        "license_url": LICENSE_URL,
        "sources": SOURCES,
        "requests": [],
        "files": {},
        "series": {},
        "vintage_warning": "Current API archive; not immutable as-published vintages.",
    }

    def retrieve(url: str, filename: str) -> object:
        payload, raw = fetch_json(url)
        path = raw_dir / filename
        path.write_bytes(raw)
        manifest["requests"].append({"url": url, "file": str(path.relative_to(destination))})
        manifest["files"][str(path.relative_to(destination))] = sha256(path)
        return payload

    records = []
    for page in range(100):
        params = {
            "$format": "json",
            "$top": 10000,
            "$skip": page * 10000,
            "$filter": (
                "Indicador eq 'IPCA' and baseCalculo eq 0 and Suavizada eq 'S' "
                f"and Data ge '{start}' and Data le '{end}'"
            ),
            "$orderby": "Data asc",
        }
        # Olinda expects percent-encoded spaces; '+' causes type errors in filters.
        url = FOCUS_URL + "?" + urlencode(params, quote_via=quote, safe="$, ").replace(" ", "%20")
        payload = retrieve(url, f"focus_{page:03d}.json")
        if not isinstance(payload, dict) or not isinstance(payload.get("value"), list):
            raise ValueError("focus: unexpected response envelope")
        records.extend(payload["value"])
        if len(payload["value"]) < 10000:
            break
    else:
        raise ValueError("focus: pagination limit exceeded")
    frames = {"focus": normalize_focus(records)}

    for name, code in {"selic": 432, "fx": 1}.items():
        records = []
        for year in range(start_date.year, end_date.year + 1):
            lower, upper = max(start_date, date(year, 1, 1)), min(end_date, date(year, 12, 31))
            params = {
                "formato": "json",
                "dataInicial": lower.strftime("%d/%m/%Y"),
                "dataFinal": upper.strftime("%d/%m/%Y"),
            }
            url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados?{urlencode(params)}"
            payload = retrieve(url, f"{name}_{year}.json")
            if not isinstance(payload, list):
                raise ValueError(f"{name}: unexpected response envelope")
            records.extend(payload)
        frames[name] = normalize_sgs(records, name)

    for name, frame in frames.items():
        if not frame["date"].between(pd.Timestamp(start), pd.Timestamp(end)).all():
            raise ValueError(f"{name}: dates outside requested range")
        path = destination / f"{name}.csv"
        frame.to_csv(path, index=False, date_format="%Y-%m-%d", float_format="%.10g")
        manifest["files"][path.name] = sha256(path)
        manifest["series"][name] = {
            "rows": len(frame),
            "first_date": str(frame["date"].min().date()),
            "last_date": str(frame["date"].max().date()),
            "missing_values": int(frame.isna().sum().sum()),
        }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def load_snapshot(directory: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Verify all evidence checksums before loading the three normalized series."""
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported snapshot manifest version")
    for filename, digest in manifest["files"].items():
        path = (directory / filename).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError("Snapshot path escapes data directory")
        if sha256(path) != digest:
            raise ValueError(f"Snapshot checksum mismatch: {filename}")
    for name in ("focus", "selic", "fx"):
        if f"{name}.csv" not in manifest["files"]:
            raise ValueError(f"Untracked snapshot input: {name}")
    return tuple(
        validate_series(pd.read_csv(directory / f"{name}.csv"), name)
        for name in ("focus", "selic", "fx")
    )
