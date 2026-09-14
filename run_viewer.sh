#!/usr/bin/env bash
# 启动本地资源查看器 (local asset viewer)，并可选地自动开一条公网隧道
# (Cloudflare Quick Tunnel)，方便协作的 AI / 队友通过一个临时 URL
# 直接访问你本地的目录结构和文件——不用你手动复制粘贴代码。
#
# 用法：
#   ./run_viewer.sh                          # 仅本地：默认端口 8420，
#                                             #   可浏览整个 toolkit 项目目录
#   ./run_viewer.sh 9000                     # 指定端口
#   ./run_viewer.sh 9000 /path/to/unpacked   # 指定端口 + 限制可浏览的根目录（推荐）
#   ./run_viewer.sh 9000 /path/to/unpacked --tunnel   # 同上，并额外开一条公网隧道
#   NO_TUNNEL=1 ./run_viewer.sh --tunnel     # 强制不开隧道（调试用）
#
# 运行后终端会常驻显示：
#   - "本地访问: http://127.0.0.1:xxxx"     -> 你自己在本机浏览器打开
#   - "公网隧道: https://xxxx.trycloudflare.com"（仅当传了 --tunnel 时）
#       -> 把这个 URL 发给协作的 AI，它可以直接读取你暴露的目录内容
#
# ⚠️⚠️ 安全须知 ⚠️⚠️
#   --tunnel 会把这台机器上被 --root 限定的目录，通过一个公网可达的
#   随机 URL 暴露出去。这个 URL 极难被人猜到/扫到，但只要拿到这个 URL
#   的任何人都能读取该目录下的所有文件。因此：
#     1. 只在需要协作查看的时间段开着，用完就 Ctrl+C 关掉；
#     2. 一定要配合 --root 把范围限制在你想公开的目录（比如这个项目本身），
#        不要不给 root 就开隧道（默认给的是本项目目录，不是整个 /home）；
#     3. 不要把 --root 指向真的包含游戏本体资源的目录再开隧道对外，
#        版权风险自己承担（另见 NOTICE.md）。
#     4. 每次重开隧道，Cloudflare 都会分配一个新的随机 URL，旧链接失效。
#
# 仅限个人学习与非商业同人创作用途，详见 NOTICE.md。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PORT="8420"
ROOT="${SCRIPT_DIR}"
USE_TUNNEL=0

# 简单解析参数：位置参数 1=端口 2=root，任意位置的 --tunnel 开启隧道
POSITIONAL=()
for arg in "$@"; do
  if [ "$arg" = "--tunnel" ]; then
    USE_TUNNEL=1
  else
    POSITIONAL+=("$arg")
  fi
done
if [ "${#POSITIONAL[@]}" -ge 1 ]; then PORT="${POSITIONAL[0]}"; fi
if [ "${#POSITIONAL[@]}" -ge 2 ]; then ROOT="${POSITIONAL[1]}"; fi

if [ "${NO_TUNNEL:-0}" = "1" ]; then USE_TUNNEL=0; fi

echo "========================================================"
echo " Prototype P3D 本地查看器"
echo "========================================================"
echo "端口:         ${PORT}"
echo "可浏览根目录: ${ROOT}"
echo "公网隧道:     $([ "$USE_TUNNEL" = "1" ] && echo "启用" || echo "未启用（加 --tunnel 参数开启）")"
echo ""

CLOUDFLARED_BIN="${SCRIPT_DIR}/.cloudflared/cloudflared"

ensure_cloudflared() {
  if [ -x "${CLOUDFLARED_BIN}" ]; then
    return 0
  fi
  echo "[隧道] 未找到 cloudflared，正在下载（约 30-50MB，仅需一次）..."
  mkdir -p "${SCRIPT_DIR}/.cloudflared"
  local arch cf_arch
  arch="$(uname -m)"
  case "$arch" in
    x86_64) cf_arch=amd64 ;;
    aarch64|arm64) cf_arch=arm64 ;;
    *) echo "[隧道] 不支持的架构: $arch，跳过隧道功能"; return 1 ;;
  esac
  if ! curl -sSL -o "${CLOUDFLARED_BIN}" \
      "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${cf_arch}"; then
    echo "[隧道] 下载失败（可能没有公网访问权限），跳过隧道功能"
    return 1
  fi
  chmod +x "${CLOUDFLARED_BIN}"
  return 0
}

TUNNEL_PID=""
cleanup() {
  if [ -n "${TUNNEL_PID}" ] && kill -0 "${TUNNEL_PID}" 2>/dev/null; then
    kill "${TUNNEL_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if [ "$USE_TUNNEL" = "1" ]; then
  if ensure_cloudflared; then
    TUNNEL_LOG="$(mktemp)"
    echo "[隧道] 正在启动 Cloudflare Quick Tunnel..."
    "${CLOUDFLARED_BIN}" tunnel --url "http://127.0.0.1:${PORT}" --no-autoupdate \
      > "${TUNNEL_LOG}" 2>&1 &
    TUNNEL_PID=$!

    # 等待日志里出现分配好的 https://xxx.trycloudflare.com URL
    TUNNEL_URL=""
    for _ in $(seq 1 30); do
      TUNNEL_URL="$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "${TUNNEL_LOG}" | head -n1 || true)"
      if [ -n "${TUNNEL_URL}" ]; then break; fi
      sleep 1
    done

    if [ -n "${TUNNEL_URL}" ]; then
      echo ""
      echo "========================================================"
      echo "  公网隧道已就绪，把下面这个 URL 发给协作的 AI/队友："
      echo ""
      echo "    ${TUNNEL_URL}"
      echo ""
      echo "  （每次重启这个脚本都会生成一个新的随机 URL）"
      echo "========================================================"
      echo ""
    else
      echo "[隧道] 30 秒内未能获取到公网 URL，查看日志: ${TUNNEL_LOG}"
      echo "[隧道] 继续以仅本地模式运行..."
    fi
  fi
fi

echo "本地访问: http://127.0.0.1:${PORT}/"
echo "按 Ctrl+C 停止服务（会同时关闭隧道）"
echo ""

python3 "${SCRIPT_DIR}/viewer/server.py" --host 127.0.0.1 --port "${PORT}" --root "${ROOT}"
