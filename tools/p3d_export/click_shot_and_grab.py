#!/usr/bin/env python3
"""
用 headless Chromium 打开预览页，等模型加载完，真的去点击页面上的"拍照"
按钮 (#shotBtn)，然后从弹出的 <img id="shotImg"> 里把 data URL 取出来存成
PNG 文件 —— 这条路径和用户在自己浏览器里手动点按钮看到的图片完全一致
(都是 canvas.toDataURL() 读出来的真实渲染像素，不是外部截图工具糊上去的)。
"""
import argparse
import base64
import sys

from playwright.sync_api import sync_playwright


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=960)
    ap.add_argument("--wait-ms", type=int, default=2000)
    args = ap.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-webgl",
                                           "--ignore-gpu-blocklist"])
        page = browser.new_page(viewport={"width": args.width, "height": args.height})
        page.on("pageerror", lambda exc: print(f"[pageerror] {exc}", file=sys.stderr))

        page.goto(f"file://{args.html}")
        page.wait_for_function(
            "document.getElementById('status').textContent.includes('完成') || "
            "document.getElementById('status').textContent.includes('失败')",
            timeout=30000,
        )
        page.wait_for_timeout(args.wait_ms)

        # 真的去点页面上那颗"拍照"按钮
        page.click("#shotBtn")
        page.wait_for_selector("#shotPanel.show", timeout=5000)

        data_url = page.eval_on_selector("#shotImg", "el => el.src")
        assert data_url.startswith("data:image/png;base64,"), data_url[:60]
        png_bytes = base64.b64decode(data_url.split(",", 1)[1])
        with open(args.out, "wb") as f:
            f.write(png_bytes)
        print(f"通过点击页面'拍照'按钮取得的截图已保存: {args.out} ({len(png_bytes)} bytes)")

        browser.close()


if __name__ == "__main__":
    main()
