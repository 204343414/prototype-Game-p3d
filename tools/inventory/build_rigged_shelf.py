#!/usr/bin/env python3
"""Flatten a completed rigged-package census into stable review-card metadata.

This never reads game assets.  Its input is the metadata-only JSON produced by
``rigged_inventory.py`` and its output is another metadata-only JSON intended
for a future thumbnail/review shelf.  Each CompositeDrawable receives a stable
artifact ID derived from its RCF entry hash and its ordinal in that package, so
a reviewer can send e.g. ``ARC-A1B2C3D4-03`` without relying on a guessed name.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import tempfile
from typing import Any

SCHEMA_VERSION = 1


def _atomic_json(path: str, data: dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".rigged_shelf_", suffix=".json", dir=directory)
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


def build_shelf(inventory: dict[str, Any], generated_at: str | None = None) -> dict[str, Any]:
    if not isinstance(inventory, dict) or inventory.get("schema_version") != 1:
        raise ValueError("input must be a schema_version 1 rigged inventory")
    progress = inventory.get("progress", {})
    if not progress.get("complete"):
        raise ValueError("refuse to build a review shelf from an incomplete census")

    items = []
    for record in inventory.get("records", []):
        entry_hash = record.get("entry_name_hash")
        entry_name = record.get("entry_name")
        if not isinstance(entry_hash, str) or not entry_hash.startswith("0x") or not isinstance(entry_name, str):
            raise ValueError(f"invalid package record identity: {record!r}")
        token = entry_hash[2:].upper()
        for ordinal, composite in enumerate(record.get("rigged_composites", []), start=1):
            primitive_refs = composite.get("primitive_references", [])
            polyskins = [
                ref.get("name") for ref in primitive_refs
                if ref.get("primitive_type") == 2 and isinstance(ref.get("name"), str)
            ]
            attachments = [
                {"name": ref.get("name"), "joint_index": ref.get("joint_index")}
                for ref in primitive_refs if ref.get("primitive_type") != 2
            ]
            items.append({
                "artifact_id": f"ARC-{token}-{ordinal:02d}",
                "source": {
                    "entry_name": entry_name,
                    "entry_name_hash": entry_hash,
                    "entry_size_compressed": record.get("entry_size_compressed"),
                    "decompressed_size": record.get("decompressed_size"),
                },
                "drawable_name": composite.get("name"),
                "skeleton_name": composite.get("skeleton_name"),
                "skeleton_is_local": composite.get("skeleton_is_local"),
                "declared_primitive_count": composite.get("declared_primitive_count"),
                "polyskin_reference_count": composite.get("polyskin_reference_count"),
                "polyskin_names": polyskins,
                "rigid_attachment_references": attachments,
                "package_shader_names": record.get("shader_names", []),
                "review": {
                    "classification": "unreviewed",
                    "priority": "unmarked",
                    "appearance_verified": False,
                    "static_preview_status": "not_generated",
                    "animation_preview_status": "not_checked",
                },
            })

    items.sort(key=lambda item: (
        -int(item["source"].get("decompressed_size") or 0),
        item["source"]["entry_name"].casefold(),
        item["artifact_id"],
    ))
    ids = [item["artifact_id"] for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("artifact ID collision")
    if generated_at is None:
        generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "source_inventory": {
            "base_url": inventory.get("source", {}).get("base_url"),
            "rcf_path": inventory.get("source", {}).get("rcf_path"),
            "completed_scan": True,
            "eligible_p3d_package_count": progress.get("eligible_entry_count"),
            "rigged_package_count": len(inventory.get("records", [])),
        },
        "scope": inventory.get("scope"),
        "qualification": inventory.get("qualification"),
        "review_protocol": (
            "Items are deliberately unclassified. Send one or more artifact_id values "
            "with a desired label/priority; do not infer identity from source filename alone."),
        "item_count": len(items),
        "items": items,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="completed metadata-only rigged inventory JSON")
    parser.add_argument("--out", required=True, help="metadata-only review shelf JSON")
    args = parser.parse_args()
    with open(args.input, encoding="utf-8") as handle:
        inventory = json.load(handle)
    shelf = build_shelf(inventory)
    _atomic_json(args.out, shelf)
    print(f"Wrote {shelf['item_count']} review items to {args.out}")


if __name__ == "__main__":
    main()
