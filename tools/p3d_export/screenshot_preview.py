#!/usr/bin/env python3
"""
用 headless Chromium (playwright) 打开一个 pack_preview_html.py 生成的
自包含 HTML 预览页，等 three.js 把 glb 加载渲染完成后截图存成 PNG。

这是"自己长眼睛看一眼到底渲染成什么样"的验证工具 —— 而不是只凭代码逻辑
自认为贴图解码对了。用法:

    python3 screenshot_preview.py --html /path/to/preview.html --out /tmp/shot.png

可选 --width/--height 控制截图分辨率，--wait-ms 控制加载完成后额外等待时间
(留给 OrbitControls 阻尼/贴图上传 GPU 完成)。
"""
import argparse
import sys

from playwright.sync_api import sync_playwright


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=960)
    ap.add_argument("--wait-ms", type=int, default=1500)
    ap.add_argument("--timeout-ms", type=int, default=30000)
    args = ap.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-webgl",
                                           "--ignore-gpu-blocklist"])
        page = browser.new_page(viewport={"width": args.width, "height": args.height})
        errors = []
        page.on("console", lambda msg: errors.append(f"[console:{msg.type}] {msg.text}"))
        page.on("pageerror", lambda exc: errors.append(f"[pageerror] {exc}"))

        page.goto(f"file://{args.html}")
        try:
            page.wait_for_function(
                "document.getElementById('status').textContent.includes('完成') || "
                "document.getElementById('status').textContent.includes('失败')",
                timeout=args.timeout_ms,
            )
        except Exception as e:
            print(f"!! 等待加载状态超时: {e}", file=sys.stderr)

        status_text = page.eval_on_selector("#status", "el => el.textContent")
        print(f"页面状态文字: {status_text!r}")

        page.wait_for_timeout(args.wait_ms)
        page.screenshot(path=args.out)
        print(f"截图已保存: {args.out}")

        if errors:
            print("---- 浏览器控制台/页面错误 ----")
            for e in errors:
                print(e)

        browser.close()


if __name__ == "__main__":
    main()
