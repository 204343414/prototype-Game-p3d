#!/usr/bin/env python3
"""Smoke test for scan_assets.py using a synthetic directory tree that
mimics likely Prototype-style file naming."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "scan_assets.py")


def main():
    tmpdir = tempfile.mkdtemp(prefix="scan_test_")
    layout = [
        "art/characters/alex_fig.p3d",
        "art/characters/alex_tod.p3d",
        "art/characters/hunter_fig.p3d",
        "art/weapons/claw_weap.p3d",
        "art/weapons/hammerfist.p3d",
        "art/anim/e09m01_anim.p3d",
        "art/textures/alex_diffuse.dds",
        "audio/voice/alex_line01.wav",
        "misc/readme_internal.txt",
    ]
    for rel in layout:
        full = os.path.join(tmpdir, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(b"x" * 10)

    json_out = os.path.join(tmpdir, "out.json")
    md_out = os.path.join(tmpdir, "out.md")

    result = subprocess.run(
        [sys.executable, SCRIPT, tmpdir, "--json", json_out, "--md", md_out],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    with open(json_out) as f:
        data = json.load(f)
    assert len(data) == len(layout), (len(data), len(layout))

    by_path = {d["path"]: d["categories"] for d in data}
    assert "characters" in by_path["art/characters/alex_fig.p3d"]
    assert "characters" in by_path["art/characters/hunter_fig.p3d"]
    assert "weapons" in by_path["art/weapons/claw_weap.p3d"]
    assert "forms_mutations" in by_path["art/weapons/hammerfist.p3d"]
    assert "animations" in by_path["art/anim/e09m01_anim.p3d"]
    assert "textures" in by_path["art/textures/alex_diffuse.dds"]
    assert "audio" in by_path["audio/voice/alex_line01.wav"]
    assert by_path["misc/readme_internal.txt"] == ["misc"]

    assert os.path.exists(md_out)
    with open(md_out) as f:
        md_content = f.read()
    assert "characters" in md_content

    print("OK: scan_assets.py smoke test passed (classification + JSON/Markdown output)")


if __name__ == "__main__":
    main()
