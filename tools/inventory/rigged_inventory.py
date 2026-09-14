#!/usr/bin/env python3
"""Build a resumable, metadata-only inventory of every rigged P3D in an RCF.

The viewer server performs the byte-level parsing on the user's own machine;
this client receives only RCF entry metadata plus CompositeDrawable, Skeleton,
and shader-binding header fields.  No mesh vertices, indices, textures or
animation keys are requested or written.

Typical use:

    python3 tools/inventory/rigged_inventory.py \
      --base-url https://<current-tunnel>.trycloudflare.com \
      --rcf-path '/path/to/Prototype/art.rcf' \
      --out /private/research/art_rigged_inventory.json

The server scans a name-sorted page of every known ``.p3d.rz`` package at a
time.  The output is atomically saved after every page, so a temporary tunnel
failure can be resumed by running the same command again.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
import urllib.parse
import urllib.request
from typing import Any

SCHEMA_VERSION = 1
ENDPOINT = "/api/rcf_rigged_manifest"


def _atomic_json(path: str, data: dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".rigged_inventory_", suffix=".json", dir=directory)
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


def _get_json(url: str, timeout: int) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = response.read()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"could not fetch {url}: {exc}") from exc
    try:
        result = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"endpoint returned invalid JSON: {exc}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("endpoint returned a non-object JSON value")
    if "error" in result:
        raise RuntimeError(f"endpoint returned an error: {result['error']}")
    return result


def _blank_inventory(base_url: str, rcf_path: str, batch_size: int, min_size: int) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "source": {
            "base_url": base_url.rstrip("/"),
            "rcf_path": rcf_path,
            "endpoint": ENDPOINT,
        },
        "scope": "all known-name .p3d.rz entries, without filename/category filtering",
        "qualification": (
            "CompositeDrawable with a named skeleton and at least one direct "
            "type=2 polyskin primitive; skeleton locality is reported separately"),
        "scan_config": {
            "batch_size": batch_size,
            "min_compressed_size": min_size,
        },
        "progress": {
            "next_entry_offset": 0,
            "complete": False,
            "eligible_entry_count": None,
            "scanned_entry_count": 0,
            "nonrigged_entry_count": 0,
            "scan_error_count": 0,
        },
        "records": [],
    }


def _load_or_create(path: str, base_url: str, rcf_path: str, batch_size: int, min_size: int) -> dict[str, Any]:
    if not os.path.exists(path):
        return _blank_inventory(base_url, rcf_path, batch_size, min_size)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not resume existing inventory {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("existing inventory has an incompatible schema_version")
    source = data.get("source", {})
    config = data.get("scan_config", {})
    if source.get("base_url") != base_url.rstrip("/") or source.get("rcf_path") != rcf_path:
        raise RuntimeError("existing inventory source differs; refuse to mix two archives/tunnels")
    if config.get("min_compressed_size") != min_size:
        raise RuntimeError("existing inventory min_compressed_size differs; refuse to mix scan scopes")
    # A changed page size is harmless because progress is an index in the
    # server's stable name-sorted eligible list, but keep the current value
    # visible in saved metadata.
    data["scan_config"]["batch_size"] = batch_size
    return data


def _merge_records(inventory: dict[str, Any], records: list[dict[str, Any]]) -> None:
    merged = {record.get("entry_name_hash"): record for record in inventory["records"]}
    for record in records:
        key = record.get("entry_name_hash")
        if not isinstance(key, str) or not key:
            raise RuntimeError(f"server returned a record without entry_name_hash: {record!r}")
        merged[key] = record
    inventory["records"] = sorted(
        merged.values(),
        key=lambda record: (-int(record.get("decompressed_size", 0)), record.get("entry_name", "").casefold()),
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    inventory = _load_or_create(args.out, args.base_url, args.rcf_path, args.batch_size, args.min_compressed_size)
    progress = inventory["progress"]
    pages_done = 0

    while not progress["complete"]:
        params = {
            "path": args.rcf_path,
            "offset": progress["next_entry_offset"],
            "limit": args.batch_size,
            "min_compressed_size": args.min_compressed_size,
        }
        url = args.base_url.rstrip("/") + ENDPOINT + "?" + urllib.parse.urlencode(params)
        page = _get_json(url, args.timeout)
        if int(page.get("entry_offset", -1)) != progress["next_entry_offset"]:
            raise RuntimeError("server returned a page at an unexpected entry offset")
        scanned = int(page.get("scanned_entry_count", -1))
        next_offset = int(page.get("next_entry_offset", -1))
        if scanned <= 0 and not page.get("complete"):
            raise RuntimeError("server made no progress before completing the eligible entry list")
        if next_offset < progress["next_entry_offset"] + scanned:
            raise RuntimeError("server returned a non-monotonic next_entry_offset")

        _merge_records(inventory, page.get("records", []))
        progress["next_entry_offset"] = next_offset
        progress["complete"] = bool(page.get("complete"))
        progress["eligible_entry_count"] = int(page.get("eligible_entry_count", 0))
        progress["scanned_entry_count"] += scanned
        progress["nonrigged_entry_count"] += int(page.get("nonrigged_count_in_page", 0))
        progress["scan_error_count"] += int(page.get("scan_error_count_in_page", 0))
        inventory["generated_at_utc"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
        _atomic_json(args.out, inventory)
        pages_done += 1
        print(
            f"scanned {progress['next_entry_offset']}/{progress['eligible_entry_count']} packages; "
            f"rigged candidates retained: {len(inventory['records'])}",
            file=sys.stderr,
        )
        if args.max_pages and pages_done >= args.max_pages:
            break

    return inventory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True, help="current viewer tunnel/base URL")
    parser.add_argument("--rcf-path", required=True, help="absolute RCF path on the viewer host")
    parser.add_argument("--out", required=True, help="private metadata JSON output path; overwritten/resumed atomically")
    parser.add_argument("--batch-size", type=int, default=25, help="P3D packages per server request (default: 25, max server-side 100)")
    parser.add_argument("--min-compressed-size", type=int, default=0, help="optional byte threshold; default 0 scans all P3D packages")
    parser.add_argument("--timeout", type=int, default=180, help="HTTP timeout per page in seconds")
    parser.add_argument("--max-pages", type=int, default=0, help="testing/controlled-run cap; 0 scans to completion")
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if args.min_compressed_size < 0:
        parser.error("--min-compressed-size must be non-negative")
    if args.max_pages < 0:
        parser.error("--max-pages must be non-negative")
    result = run(args)
    p = result["progress"]
    print(
        f"{'Complete' if p['complete'] else 'Paused'}: {p['scanned_entry_count']}/{p['eligible_entry_count']} "
        f"packages; {len(result['records'])} rigged candidates; "
        f"{p['nonrigged_entry_count']} nonrigged; {p['scan_error_count']} errors.\n{args.out}")


if __name__ == "__main__":
    main()
