#!/usr/bin/env python3
"""Unit tests for the metadata-only RCF inventory ledger builder."""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location("rcf_inventory", os.path.join(HERE, "rcf_inventory.py"))
rcf_inventory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rcf_inventory)


def main():
    manifest = {
        "path": "/games/Prototype/art.rcf",
        "file_size": 9999,
        "endian": "LE",
        "major_version": 2,
        "minor_version": 1,
        "entry_count": 4,
        "metadata_count": 4,
        "known_count": 3,
        "unknown_count": 1,
        "entries": [
            {"name_hash": "0x01", "name": "\\art\\alex\\alex.p3d.rz", "offset": 64, "size": 400},
            {"name_hash": "0x02", "name": "\\art\\audio\\alex_voice.wav", "offset": 464, "size": 50},
            {"name_hash": "0x03", "name": "\\art\\weapons\\claw_weap.p3d.rz", "offset": 514, "size": 100},
            {"name_hash": "0x04", "name": None, "offset": 614, "size": 1},
        ],
    }
    ledger = rcf_inventory.build_ledger(manifest, generated_at="2026-09-14T00:00:00+00:00")

    assert ledger["schema_version"] == 1
    assert ledger["summary"]["entry_count_ledger"] == 4
    assert ledger["source"]["entry_count_reported"] == 4

    by_hash = {entry["name_hash"]: entry for entry in ledger["entries"]}
    assert by_hash["0x01"]["extension"] == ".p3d.rz"
    assert "characters" in by_hash["0x01"]["categories"]
    assert "audio" in by_hash["0x02"]["categories"]
    assert "weapons" in by_hash["0x03"]["categories"]
    assert by_hash["0x04"]["categories"] == ["unresolved_name"]
    assert ledger["summary"]["category_counts"]["unresolved_name"] == 1

    markdown = rcf_inventory.ledger_markdown(ledger)
    assert "RCF 资产元数据台账" in markdown
    assert "alex.p3d.rz" in markdown
    assert "不含、不提取、也不分发游戏资源内容" in markdown

    try:
        rcf_inventory.build_ledger({"entries": [{"name": "bad", "offset": -1, "size": 0}]})
    except ValueError:
        pass
    else:
        raise AssertionError("negative offset must be rejected")

    print("OK: rcf_inventory.py ledger schema, classification, Markdown, and validation passed")


if __name__ == "__main__":
    main()
