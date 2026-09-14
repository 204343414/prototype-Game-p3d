#!/usr/bin/env python3
"""Build a resumable, metadata-only static-geometry census of Manhattan Cells.

The viewer-side endpoint opens and parses each Cell locally. This client receives
only Cell IDs, sizes, Geometry/PrimitiveGroup counts, observed vertex strides,
and aggregate POSITION bounds. It never writes P3D, texture, index, or vertex
payloads to disk.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import tempfile
import urllib.parse
import urllib.request
from typing import Any

SCHEMA_VERSION = 1
ENDPOINT = "/api/rcf_cell_geometry_manifest"


def _atomic_json(path: str, data: dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".manhattan_cell_census_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _fetch_json(url: str, timeout: int) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            data = json.loads(response.read())
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"could not fetch {url}: {exc}") from exc
    if not isinstance(data, dict) or "error" in data:
        raise RuntimeError(f"endpoint error: {data.get('error') if isinstance(data, dict) else data!r}")
    return data


def _new_inventory(base_url: str, rcf_path: str, page_size: int) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "source": {"base_url": base_url.rstrip("/"), "rcf_path": rcf_path, "endpoint": ENDPOINT},
        "scope": "numbered Manhattan base Cells; metadata-only geometry/stride/POSITION bounds; _ft excluded",
        "progress": {
            "next_entry_offset": 0,
            "complete": False,
            "eligible_cell_count": None,
            "scanned_cell_count": 0,
        },
        "scan_config": {"page_size": page_size, "detail": "summary"},
        "records": [],
    }


def _load_or_create(path: str, base_url: str, rcf_path: str, page_size: int) -> dict[str, Any]:
    if not os.path.exists(path):
        return _new_inventory(base_url, rcf_path, page_size)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not resume {path}: {exc}") from exc
    if data.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("existing census has incompatible schema_version")
    source = data.get("source", {})
    if source.get("base_url") != base_url.rstrip("/") or source.get("rcf_path") != rcf_path:
        raise RuntimeError("existing census belongs to another archive/tunnel")
    data["scan_config"]["page_size"] = page_size
    return data


def _merge_records(inventory: dict[str, Any], records: list[dict[str, Any]]) -> None:
    merged = {int(record["cell_index"]): record for record in inventory["records"]}
    for record in records:
        if not isinstance(record.get("cell_index"), int):
            raise RuntimeError(f"server record lacks integer cell_index: {record!r}")
        merged[record["cell_index"]] = record
    inventory["records"] = [merged[index] for index in sorted(merged)]


def run(args: argparse.Namespace) -> dict[str, Any]:
    inventory = _load_or_create(args.out, args.base_url, args.rcf_path, args.page_size)
    progress = inventory["progress"]
    pages = 0
    while not progress["complete"]:
        params = {"path": args.rcf_path, "offset": progress["next_entry_offset"], "limit": args.page_size}
        url = args.base_url.rstrip("/") + ENDPOINT + "?" + urllib.parse.urlencode(params)
        page = _fetch_json(url, args.timeout)
        if int(page.get("entry_offset", -1)) != progress["next_entry_offset"]:
            raise RuntimeError("server returned an unexpected Cell page offset")
        count = int(page.get("scanned_cell_count", -1))
        next_offset = int(page.get("next_entry_offset", -1))
        if count <= 0 and not page.get("complete"):
            raise RuntimeError("Cell endpoint made no progress")
        if next_offset < progress["next_entry_offset"] + count:
            raise RuntimeError("Cell endpoint returned a non-monotonic next offset")
        _merge_records(inventory, page.get("records", []))
        progress["next_entry_offset"] = next_offset
        progress["complete"] = bool(page.get("complete"))
        progress["eligible_cell_count"] = int(page.get("eligible_cell_count", 0))
        progress["scanned_cell_count"] += count
        inventory["generated_at_utc"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
        _atomic_json(args.out, inventory)
        pages += 1
        print(f"scanned {next_offset}/{progress['eligible_cell_count']} Manhattan Cells", flush=True)
        if args.max_pages and pages >= args.max_pages:
            break
    return inventory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--rcf-path", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--page-size", type=int, default=10, help="Cells per request (1..25, default 10)")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-pages", type=int, default=0, help="controlled-run cap; 0 scans all Cells")
    args = parser.parse_args()
    if not 1 <= args.page_size <= 25:
        parser.error("--page-size must be between 1 and 25")
    if args.max_pages < 0:
        parser.error("--max-pages must be non-negative")
    result = run(args)
    statuses = {}
    for record in result["records"]:
        statuses[record.get("status", "unknown")] = statuses.get(record.get("status", "unknown"), 0) + 1
    p = result["progress"]
    print(
        f"{'Complete' if p['complete'] else 'Paused'}: {p['scanned_cell_count']}/{p['eligible_cell_count']} Cells; "
        f"statuses={statuses}; {args.out}")


if __name__ == "__main__":
    main()
