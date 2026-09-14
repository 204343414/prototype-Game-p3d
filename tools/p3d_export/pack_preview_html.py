#!/usr/bin/env python3
"""
把一个 .glb 文件打包成一个"自包含单文件 HTML"预览页面：
three.js (vendor目录里的 three.min.js + GLTFLoader.js + OrbitControls.js)
和 .glb 模型数据(base64编码) 全部内嵌进同一个 HTML 文件的 <script> 标签里，
不依赖任何外部 CDN / 网络请求 —— 专门用于 workspace 的沙盒
`allow-scripts` 无网络 iframe 预览环境（也可以在任何普通浏览器双击打开）。

背景: workspace 的文件预览器不支持直接渲染 .glb 三维文件（只认
文本/Markdown/HTML/SVG/图片/音频/视频/PDF/CSV/Office 几类)，所以用
"生成一个能自己跑 three.js 的 HTML"来曲线预览。

用法:
    python3 pack_preview_html.py \
        --glb samples/exported/alex_reg_body.glb \
        --out samples/exported/alex_reg_body_preview.html \
        --title "Alex Mercer 身体部位组预览" \
        --subtitle "alex_reg_body (身体+头部+外套, T/A-pose静止姿势)"

依赖: viewer/vendor/three/{three.min.js,GLTFLoader.js,OrbitControls.js}
（这三个文件已经下载好提交在仓库里，来自 three.js r128 的非 ES-module
UMD 版本 examples/js，直接全局挂 THREE 命名空间，没有 import/export
语句，适合直接内嵌进普通 <script> 标签。如果这三个文件缺失，去
https://unpkg.com/three@0.128.0/build/three.min.js 、
https://unpkg.com/three@0.128.0/examples/js/loaders/GLTFLoader.js 、
https://unpkg.com/three@0.128.0/examples/js/controls/OrbitControls.js
重新下载放回 viewer/vendor/three/ 即可）。
"""
import argparse
import base64
import os


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
  html, body {{ margin: 0; padding: 0; width: 100%; height: 100%; background: #1a1a1e; overflow: hidden; font-family: -apple-system, "Microsoft YaHei", sans-serif; }}
  #viewer {{ width: 100%; height: 100%; display: block; }}
  #overlay {{
    position: fixed; top: 12px; left: 12px; color: #eee; background: rgba(0,0,0,0.55);
    padding: 10px 14px; border-radius: 8px; font-size: 13px; line-height: 1.6; max-width: 360px;
    pointer-events: none;
  }}
  #overlay b {{ color: #7ad1ff; }}
  #status {{
    position: fixed; top: 12px; right: 12px; color: #eee; background: rgba(0,0,0,0.55);
    padding: 8px 12px; border-radius: 8px; font-size: 13px;
  }}
  .err {{ color: #ff7a7a !important; }}
  #shotBtn {{
    position: fixed; bottom: 16px; left: 16px; z-index: 10;
    padding: 10px 16px; font-size: 14px; border-radius: 8px; border: none;
    background: #2f7de1; color: white; cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,0.4);
  }}
  #shotBtn:hover {{ background: #4a91ee; }}
  #shotPanel {{
    position: fixed; inset: 0; background: rgba(0,0,0,0.85); z-index: 20;
    display: none; align-items: center; justify-content: center; flex-direction: column;
  }}
  #shotPanel.show {{ display: flex; }}
  #shotPanel img {{ max-width: 90%; max-height: 80%; border: 2px solid #555; background: #000; }}
  #shotPanel .hint {{ color: #ddd; margin-top: 12px; font-size: 13px; }}
  #shotPanel button {{
    margin-top: 12px; padding: 8px 14px; border-radius: 6px; border: none;
    background: #444; color: #fff; cursor: pointer;
  }}
</style>
</head>
<body>
<div id="viewer"></div>
<div id="overlay">
  <div><b>Prototype 游戏资源考古</b> —— {title}</div>
  <div>{subtitle}</div>
  <div>拖拽=旋转 / 滚轮=缩放 / 右键拖拽=平移</div>
</div>
<div id="status">加载中…</div>
<button id="shotBtn">📷 拍照 (截取当前渲染画面)</button>
<div id="shotPanel">
  <img id="shotImg" alt="截图预览" />
  <div class="hint">这就是 WebGL canvas 当前实际渲染出的像素内容 (非缓存/非外部截图工具)</div>
  <div>
    <a id="shotDownload" download="preview_screenshot.png"><button>下载这张截图</button></a>
    <button id="shotClose">关闭</button>
  </div>
</div>

<script>
{three_js}
</script>
<script>
{gltf_loader_js}
</script>
<script>
{orbit_controls_js}
</script>
<script>
// ---- 内嵌的 .glb 模型数据 (base64) ----
const GLB_BASE64 = "{glb_b64}";

function base64ToArrayBuffer(base64) {{
  const binaryString = atob(base64);
  const len = binaryString.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) {{
    bytes[i] = binaryString.charCodeAt(i);
  }}
  return bytes.buffer;
}}

const statusEl = document.getElementById('status');

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1a1a1e);

const viewerEl = document.getElementById('viewer');
const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.01, 100);
camera.position.set({cam_x}, {cam_y}, {cam_z});

const renderer = new THREE.WebGLRenderer({{ antialias: true, preserveDrawingBuffer: true }});
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(window.devicePixelRatio || 1);
renderer.outputEncoding = THREE.sRGBEncoding;
viewerEl.appendChild(renderer.domElement);

const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.target.set({target_x}, {target_y}, {target_z});
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.update();

// 灯光: 简单三点布光，突出模型细节
const hemi = new THREE.HemisphereLight(0xffffff, 0x444455, 1.1);
scene.add(hemi);
const dir1 = new THREE.DirectionalLight(0xffffff, 1.0);
dir1.position.set(2, 3, 2);
scene.add(dir1);
const dir2 = new THREE.DirectionalLight(0xaaccff, 0.5);
dir2.position.set(-2, 1, -2);
scene.add(dir2);

// 地面网格，给出比例尺参照
const grid = new THREE.GridHelper(4, 20, 0x555566, 0x333340);
scene.add(grid);

const loader = new THREE.GLTFLoader();
try {{
  const arrayBuffer = base64ToArrayBuffer(GLB_BASE64);
  loader.parse(arrayBuffer, '', function(gltf) {{
    scene.add(gltf.scene);
    statusEl.textContent = '加载完成 ✓';
    setTimeout(() => {{ statusEl.style.opacity = '0'; statusEl.style.transition = 'opacity 1s'; }}, 3000);
  }}, function(error) {{
    console.error(error);
    statusEl.textContent = '加载失败: ' + error;
    statusEl.classList.add('err');
  }});
}} catch (e) {{
  console.error(e);
  statusEl.textContent = '解析失败: ' + e;
  statusEl.classList.add('err');
}}

function animate() {{
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}}
animate();

window.addEventListener('resize', () => {{
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}});

// ---- "拍照"按钮: 直接从 WebGL canvas 读取当前实际渲染的像素, 生成PNG
// data URL 展示+提供下载。用来自证"页面上到底渲染出了什么", 排除任何
// 外部截图工具/缓存导致的疑虑。preserveDrawingBuffer 没开的话 toDataURL
// 可能拿到空白, 所以这里在拍照前先强制多渲染一帧再立刻读取。----
const shotBtn = document.getElementById('shotBtn');
const shotPanel = document.getElementById('shotPanel');
const shotImg = document.getElementById('shotImg');
const shotDownload = document.getElementById('shotDownload');
const shotClose = document.getElementById('shotClose');

shotBtn.addEventListener('click', () => {{
  renderer.render(scene, camera);
  const dataUrl = renderer.domElement.toDataURL('image/png');
  shotImg.src = dataUrl;
  shotDownload.href = dataUrl;
  shotPanel.classList.add('show');
}});
shotClose.addEventListener('click', () => {{
  shotPanel.classList.remove('show');
}});
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glb", required=True, help="输入 .glb 文件路径")
    ap.add_argument("--out", required=True, help="输出 .html 文件路径")
    ap.add_argument("--title", default="Prototype 角色预览")
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--vendor-dir", default=None,
                     help="three.js vendor 目录，默认是相对本脚本的 ../../viewer/vendor/three")
    ap.add_argument("--cam", default="1.6,1.4,1.9", help="相机初始位置 x,y,z")
    ap.add_argument("--target", default="0,0.9,0", help="OrbitControls 环绕目标点 x,y,z")
    args = ap.parse_args()

    vendor_dir = args.vendor_dir
    if vendor_dir is None:
        here = os.path.dirname(os.path.abspath(__file__))
        vendor_dir = os.path.join(here, "..", "..", "viewer", "vendor", "three")

    def read_text(name):
        path = os.path.join(vendor_dir, name)
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    three_js = read_text("three.min.js")
    gltf_loader_js = read_text("GLTFLoader.js")
    orbit_controls_js = read_text("OrbitControls.js")

    with open(args.glb, "rb") as f:
        glb_bytes = f.read()
    glb_b64 = base64.b64encode(glb_bytes).decode("ascii")

    cam_x, cam_y, cam_z = [s.strip() for s in args.cam.split(",")]
    target_x, target_y, target_z = [s.strip() for s in args.target.split(",")]

    html = HTML_TEMPLATE.format(
        title=args.title,
        subtitle=args.subtitle,
        three_js=three_js,
        gltf_loader_js=gltf_loader_js,
        orbit_controls_js=orbit_controls_js,
        glb_b64=glb_b64,
        cam_x=cam_x, cam_y=cam_y, cam_z=cam_z,
        target_x=target_x, target_y=target_y, target_z=target_z,
    )

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"完成: {args.out} ({len(html)} 字符, glb={len(glb_bytes)} bytes -> "
          f"base64={len(glb_b64)} chars)")


if __name__ == "__main__":
    main()
