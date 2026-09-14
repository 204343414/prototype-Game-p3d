#!/usr/bin/env python3
"""Build a metadata-only inventory from a viewer server's RCF manifest API.

This utility intentionally asks ``/api/rcf_manifest`` for names, hashes,
offsets and sizes only. It never extracts or writes game payloads. The JSON
and Markdown reports are a reproducible asset ledger for research; they are
not a mechanism for packaging or redistributing game files.

Typical use (write reports outside the Git worktree):

    python3 tools/inventory/rcf_inventory.py \
      --base-url https://your-tunnel.trycloudflare.com \
      --rcf-path '/path/to/Prototype/art.rcf' \
      --json /tmp/art_rcf_inventory.json \
      --md /tmp/art_rcf_inventory.md

The API call uses ``limit=0`` deliberately to get the complete manifest. It
does not use the P3D chunk API or its ``max_depth`` option.
"""
import argparse
import collections
import datetime as dt
import json
import os
import urllib.parse
import urllib.request

from scan_assets import classify

SCHEMA_VERSION = 1


def _asset_extension(name):
    """Return a useful logical extension, retaining the common .p3d.rz pair."""
    lower = name.lower()
    if lower.endswith(".p3d.rz"):
        return ".p3d.rz"
    return os.path.splitext(lower)[1] or "[none]"


def fetch_manifest(base_url, rcf_path, timeout=120):
    """Fetch the complete metadata manifest, raising RuntimeError on API errors."""
    params = {"path": rcf_path, "limit": 0}
    url = base_url.rstrip("/") + "/api/rcf_manifest?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = response.read()
    except Exception as exc:  # noqa: BLE001 - expose network context to CLI user
        raise RuntimeError(f"could not fetch RCF manifest from {url}: {exc}") from exc

    try:
        manifest = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"manifest endpoint returned invalid JSON: {exc}") from exc
    if "error" in manifest:
        raise RuntimeError(f"manifest endpoint returned an error: {manifest['error']}")
    if not isinstance(manifest.get("entries"), list):
        raise RuntimeError("manifest endpoint response has no entries list")
    if manifest.get("entries_truncated"):
        raise RuntimeError("manifest API returned a truncated list; refusing to write a partial ledger")
    return manifest


def build_ledger(manifest, generated_at=None):
    """Validate an API manifest and return the versioned, metadata-only ledger."""
    if not isinstance(manifest, dict) or not isinstance(manifest.get("entries"), list):
        raise ValueError("manifest must be an object with an entries list")

    entries = []
    for raw in manifest["entries"]:
        if not isinstance(raw, dict):
            raise ValueError("manifest entry must be an object")
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            # Unknown-name entries are still represented, but cannot be
            # classified from a path. The known/unknown count remains visible.
            name = None
            categories = ["unresolved_name"]
            extension = "[unknown]"
        else:
            categories = classify(name)
            extension = _asset_extension(name)

        try:
            offset = int(raw["offset"])
            size = int(raw["size"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"manifest entry has invalid offset/size: {raw!r}") from exc
        if offset < 0 or size < 0:
            raise ValueError(f"manifest entry has negative offset/size: {raw!r}")

        entries.append({
            "name": name,
            "name_hash": raw.get("name_hash"),
            "offset": offset,
            "size": size,
            "extension": extension,
            "categories": categories,
        })

    entries.sort(key=lambda item: ((item["name"] or "").casefold(), item["name_hash"] or ""))
    category_counts = collections.Counter()
    extension_counts = collections.Counter()
    for entry in entries:
        category_counts.update(entry["categories"])
        extension_counts[entry["extension"]] += 1

    if generated_at is None:
        generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "source": {
            "rcf_path": manifest.get("path"),
            "archive_size": manifest.get("file_size"),
            "endian": manifest.get("endian"),
            "major_version": manifest.get("major_version"),
            "minor_version": manifest.get("minor_version"),
            "entry_count_reported": manifest.get("entry_count"),
            "metadata_count_reported": manifest.get("metadata_count"),
            "known_count_reported": manifest.get("known_count"),
            "unknown_count_reported": manifest.get("unknown_count"),
        },
        "summary": {
            "entry_count_ledger": len(entries),
            "category_counts": dict(sorted(category_counts.items())),
            "extension_counts": dict(sorted(extension_counts.items())),
        },
        "entries": entries,
    }


def _md_cell(value):
    return str(value).replace("|", "\\|")


def ledger_markdown(ledger):
    """Create a bounded human-readable companion report for a ledger."""
    source = ledger["source"]
    summary = ledger["summary"]
    lines = [
        "# RCF 资产元数据台账",
        "",
        "> 本报告只含归档条目名称、哈希、偏移、大小和启发式分类；不含、不提取、也不分发游戏资源内容。",
        "",
        f"- 生成时间（UTC）：`{ledger['generated_at_utc']}`",
        f"- RCF 路径（生成机器本地路径）：`{source['rcf_path']}`",
        f"- 归档大小：`{source['archive_size']}` bytes",
        f"- RCF 版本：`{source['major_version']}.{source['minor_version']}`，字节序：`{source['endian']}`",
        f"- API 报告条目数：`{source['entry_count_reported']}`；台账条目数：`{summary['entry_count_ledger']}`",
        f"- 已知名称 / 未知名称：`{source['known_count_reported']}` / `{source['unknown_count_reported']}`",
        "",
        "## 分类计数（名称启发式，非格式事实）",
        "",
        "| 分类 | 条目数 |",
        "|---|---:|",
    ]
    for category, count in summary["category_counts"].items():
        lines.append(f"| {_md_cell(category)} | {count} |")

    lines += ["", "## 扩展名计数", "", "| 扩展名 | 条目数 |", "|---|---:|"]
    for extension, count in summary["extension_counts"].items():
        lines.append(f"| {_md_cell(extension)} | {count} |")

    # A complete JSON file remains the canonical machine-readable ledger.
    # Keep Markdown usable even for archives containing many thousands entries.
    lines += [
        "",
        "## 角色候选（名称启发式）",
        "",
        "下表只列名称被分类为 `characters` 的条目。它是排查入口，不等于已验证的角色模型。",
        "",
        "| 条目名 | 哈希 | 大小（bytes） | 分类 |",
        "|---|---|---:|---|",
    ]
    character_entries = [entry for entry in ledger["entries"] if "characters" in entry["categories"]]
    for entry in character_entries[:500]:
        lines.append(
            f"| `{_md_cell(entry['name'])}` | `{_md_cell(entry['name_hash'])}` | "
            f"{entry['size']} | {_md_cell(', '.join(entry['categories']))} |"
        )
    if len(character_entries) > 500:
        lines.append(f"| … | … | … | 其余 {len(character_entries) - 500} 个见 JSON |")
    lines.append("")
    return "\n".join(lines)


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True, help="viewer tunnel/base URL, e.g. https://xxxx.trycloudflare.com")
    parser.add_argument("--rcf-path", required=True, help="absolute RCF path on the viewer host")
    parser.add_argument("--json", required=True, help="output JSON ledger path (prefer outside the Git worktree)")
    parser.add_argument("--md", required=True, help="output Markdown report path (prefer outside the Git worktree)")
    parser.add_argument("--timeout", type=int, default=120, help="HTTP timeout in seconds (default: 120)")
    args = parser.parse_args()

    manifest = fetch_manifest(args.base_url, args.rcf_path, timeout=args.timeout)
    ledger = build_ledger(manifest)
    _write_json(args.json, ledger)
    with open(args.md, "w", encoding="utf-8") as handle:
        handle.write(ledger_markdown(ledger))

    print(f"Wrote {ledger['summary']['entry_count_ledger']} metadata-only entries.")
    print(f"JSON ledger: {args.json}")
    print(f"Markdown report: {args.md}")


if __name__ == "__main__":
    main()
