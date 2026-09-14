#!/usr/bin/env bash
# 启动本地资源查看器 (local asset viewer)
#
# 用法：
#   ./run_viewer.sh                  # 默认端口 8420，可浏览整个文件系统
#   ./run_viewer.sh 9000             # 指定端口
#   ./run_viewer.sh 9000 /path/to/unpacked   # 指定端口 + 限制可浏览的根目录（推荐）
#
# 运行后终端会常驻显示 "http://127.0.0.1:xxxx"，
# 在浏览器打开该地址即可使用；关闭这个终端 / Ctrl+C 会停止服务。
#
# 仅限个人学习与非商业同人创作用途，详见 NOTICE.md。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${1:-8420}"
ROOT="${2:-/}"

echo "启动 Prototype P3D 本地查看器..."
echo "端口: ${PORT}"
echo "可浏览根目录: ${ROOT}"
echo ""

exec python3 "${SCRIPT_DIR}/viewer/server.py" --host 127.0.0.1 --port "${PORT}" --root "${ROOT}"
