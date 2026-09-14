#!/usr/bin/env python3
"""
资源清点扫描工具 (asset inventory scanner)

扫描一个已经用 rcf_extract.py 解包出来的目录树（或者任何包含 .p3d 文件的
目录），按文件名/路径关键词把资源粗分类为：角色(characters)、武器(weapons)、
形态/变身(forms/mutations)、动画(animations)、其它(misc)，并生成一份
JSON + Markdown 清单，方便快速定位、以及后续贴给我一起排查代码结构、
剔除无关文件。

这一步*不解析* .p3d 内部结构，只做文件名/路径层面的启发式分类——这是最快
能把"到底有哪些人物/武器/形态"这个问题回答个大概的办法，具体是否准确
需要之后结合 chunk 内容（比如 CompositeDrawable 的 Name/SkeletonName）
进一步验证。

Usage:
    python3 scan_assets.py <unpacked_dir> [--json out.json] [--md out.md]
"""
import argparse
import json
import os
import re


# 关键词表：基于《虐杀原形》已知的资源命名习惯（社区 wiki / mod 页面里
# 出现过的文件名模式，如 alex_fig.p3d, alex_tod.p3d 等）加上常见英文命名
# 习惯（fig=figure角色模型, tod=?贴图/描述, anim=动画, weap/wpn=武器等）
# 做的猜测性关键词集合。这是一个可以持续迭代补充的表，发现新的命名规律
# 应该随时加进来。
CATEGORY_KEYWORDS = {
    "characters": [
        r"\bfig\b", r"_fig\.", r"\bchar", r"\balex\b", r"\bmercer\b",
        r"\bhunter\b", r"\bsoldier\b", r"\bcivilian\b", r"\bzombie\b",
        r"\bboss\b", r"\bnpc\b", r"\bped\b", r"\bcreature\b",
    ],
    "weapons": [
        r"\bweap", r"\bwpn\b", r"\bgun\b", r"\brifle\b", r"\bpistol\b",
        r"\bblade\b", r"\bclaw", r"\bhammer", r"\bwhip\b", r"\btentacle",
        r"\barmor\b", r"\barmour\b", r"\bshield\b",
    ],
    "forms_mutations": [
        r"\bmutation", r"\btransform", r"\bmorph", r"\bconsume",
        r"\bdisguise\b", r"\bmuscle", r"\bhammerfist", r"\bwhipfist",
        r"\bshield\b", r"\bglide\b", r"\bform\b",
    ],
    "animations": [
        r"\banim", r"_tod\.", r"\bmotion\b", r"\bmocap\b",
    ],
    "levels_missions": [
        r"^e\d+m\d+", r"\bmission\b", r"\blevel\b", r"\bscenario\b",
    ],
    "audio": [
        r"\.wav$", r"\.ogg$", r"\.mp3$", r"\bvoice\b", r"\bsfx\b",
        r"\bmusic\b",
    ],
    "textures": [
        r"\.dds$", r"\.png$", r"\.tga$", r"\btexture\b", r"\btex\b",
    ],
    "ui_frontend": [
        r"\bfrontend\b", r"\bhud\b", r"\bmenu\b", r"\bfont\b",
    ],
}

CATEGORY_ORDER = list(CATEGORY_KEYWORDS.keys()) + ["misc"]

_COMPILED = {
    cat: [re.compile(pat, re.IGNORECASE) for pat in pats]
    for cat, pats in CATEGORY_KEYWORDS.items()
}


def classify(path: str):
    lower = path.lower()
    matched = []
    for cat in CATEGORY_ORDER[:-1]:
        for rx in _COMPILED[cat]:
            if rx.search(lower):
                matched.append(cat)
                break
    return matched or ["misc"]


def scan(root_dir):
    results = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, root_dir)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            cats = classify(rel)
            results.append({
                "path": rel.replace(os.sep, "/"),
                "size": size,
                "categories": cats,
            })
    results.sort(key=lambda r: r["path"])
    return results


def write_markdown(results, out_path, root_dir):
    by_cat = {cat: [] for cat in CATEGORY_ORDER}
    for r in results:
        for cat in r["categories"]:
            by_cat[cat].append(r)

    lines = []
    lines.append(f"# 资源清点报告\n")
    lines.append(f"扫描目录：`{root_dir}`\n")
    lines.append(f"文件总数：{len(results)}\n")
    lines.append("")
    lines.append("| 分类 | 文件数 |")
    lines.append("|---|---|")
    for cat in CATEGORY_ORDER:
        lines.append(f"| {cat} | {len(by_cat[cat])} |")
    lines.append("")

    for cat in CATEGORY_ORDER:
        items = by_cat[cat]
        if not items:
            continue
        lines.append(f"## {cat} ({len(items)})\n")
        for r in items[:500]:  # 避免单个分类过大导致文档爆炸
            size_kb = r["size"] / 1024 if r["size"] >= 0 else -1
            lines.append(f"- `{r['path']}` ({size_kb:.1f} KB)")
        if len(items) > 500:
            lines.append(f"- ...及其余 {len(items) - 500} 个文件（见 JSON 清单）")
        lines.append("")

    with open(out_path, "w") as f:
        f.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("unpacked_dir")
    parser.add_argument("--json", default="asset_inventory.json")
    parser.add_argument("--md", default="asset_inventory.md")
    args = parser.parse_args()

    if not os.path.isdir(args.unpacked_dir):
        raise SystemExit(f"not a directory: {args.unpacked_dir}")

    results = scan(args.unpacked_dir)

    with open(args.json, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    write_markdown(results, args.md, args.unpacked_dir)

    print(f"Scanned {len(results)} files.")
    print(f"JSON manifest: {args.json}")
    print(f"Markdown report: {args.md}")


if __name__ == "__main__":
    main()
