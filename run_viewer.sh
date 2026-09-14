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
#   TUNNEL_MODE=ssh ./run_viewer.sh 9000 /path/to/unpacked --tunnel
#       # 强制使用 SSH 隧道（localhost.run），不下载 cloudflared，
#       # 适合 GitHub Releases 下载被限速/卡住的网络环境（常见于国内）
#
# 隧道有两种实现方式，脚本会自动选择：
#   1. cloudflared（默认优先）：需要先下载一个 ~40MB 的二进制文件，
#      国内网络访问 GitHub Releases 经常被限速甚至卡死不动。
#   2. SSH 隧道（localhost.run）：用系统自带的 ssh 命令，不需要下载
#      任何额外程序，直接用 SSH 协议连出去，通常比下载 GitHub 二进制
#      文件更容易穿过网络限制。如果 cloudflared 下载 30 秒内没完成，
#      脚本会自动切换到这个方式；也可以用 TUNNEL_MODE=ssh 强制只用这个。
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
#     5. viewer/server.py 还提供了一个 POST /api/self_update 接口：
#        协作的 AI 可以直接触发"git pull + 在原端口原样重启这个 server
#        进程"，公网隧道 URL 不会因此改变（不用你手动 git pull / 重开
#        终端）。这意味着**拿到这个隧道 URL 的任何人也能远程触发这个仓库
#        的更新和重启**——风险等级和上面第1-4条一样（同一个 URL 泄露就
#        什么都能做），不是额外新增的攻击面，只是把"能做的事"从"读文件"
#        扩展到了"更新代码"。不想要这个能力可以不理会这个接口，它不会
#        自动触发，只有明确收到 POST 请求才会执行。
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
TUNNEL_MODE="${TUNNEL_MODE:-auto}"   # auto | cloudflared | ssh

# 下载 cloudflared，带超时（不会无限卡住）。返回非0表示下载失败/超时。
try_download_cloudflared() {
  mkdir -p "${SCRIPT_DIR}/.cloudflared"
  local arch cf_arch
  arch="$(uname -m)"
  case "$arch" in
    x86_64) cf_arch=amd64 ;;
    aarch64|arm64) cf_arch=arm64 ;;
    *) echo "[隧道] 不支持的架构: $arch，跳过 cloudflared"; return 1 ;;
  esac
  echo "[隧道] 未找到 cloudflared，尝试下载（约 30-50MB，最多等 25 秒，超时会自动改用 SSH 隧道）..."
  # --max-time 限制整个请求耗时；-f 让 HTTP 错误也返回非0；
  # 下载到临时文件成功后再 mv，避免留下半个损坏的二进制文件
  local tmp_file="${CLOUDFLARED_BIN}.part"
  if curl -fSL --connect-timeout 8 --max-time 25 -o "${tmp_file}" \
      "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${cf_arch}" 2>/dev/null; then
    mv "${tmp_file}" "${CLOUDFLARED_BIN}"
    chmod +x "${CLOUDFLARED_BIN}"
    echo "[隧道] cloudflared 下载成功。"
    return 0
  else
    rm -f "${tmp_file}"
    echo "[隧道] cloudflared 下载失败或超时（常见于 GitHub Releases 被限速的网络环境），改用 SSH 隧道..."
    return 1
  fi
}

ensure_cloudflared() {
  if [ -x "${CLOUDFLARED_BIN}" ]; then
    return 0
  fi
  try_download_cloudflared
}

TUNNEL_PID=""
cleanup() {
  if [ -n "${TUNNEL_PID}" ] && kill -0 "${TUNNEL_PID}" 2>/dev/null; then
    kill "${TUNNEL_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

start_cloudflared_tunnel() {
  TUNNEL_LOG="$(mktemp)"
  echo "[隧道] 正在启动 Cloudflare Quick Tunnel..."
  "${CLOUDFLARED_BIN}" tunnel --url "http://127.0.0.1:${PORT}" --no-autoupdate \
    > "${TUNNEL_LOG}" 2>&1 &
  TUNNEL_PID=$!

  TUNNEL_URL=""
  for _ in $(seq 1 30); do
    TUNNEL_URL="$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "${TUNNEL_LOG}" | head -n1 || true)"
    if [ -n "${TUNNEL_URL}" ]; then break; fi
    if ! kill -0 "${TUNNEL_PID}" 2>/dev/null; then break; fi
    sleep 1
  done

  if [ -z "${TUNNEL_URL}" ]; then
    kill "${TUNNEL_PID}" 2>/dev/null || true
    TUNNEL_PID=""
    echo "[隧道] 30 秒内未能获取到 Cloudflare 公网 URL（日志: ${TUNNEL_LOG}）"
    return 1
  fi
  return 0
}

# SSH 隧道方案：用系统自带 ssh 连 localhost.run（无需注册、无需下载任何
# 额外程序），把本地端口反向映射到一个 *.lhr.life 的公网 URL 上。
# 原理等价于: ssh -R 80:localhost:PORT nokey@localhost.run
start_ssh_tunnel() {
  if ! command -v ssh >/dev/null 2>&1; then
    echo "[隧道] 系统没有安装 ssh 客户端，无法使用 SSH 隧道方式"
    return 1
  fi
  echo "[隧道] 正在通过 SSH 启动 localhost.run 隧道（首次连接可能需要接受主机指纹，已自动确认）..."
  TUNNEL_LOG="$(mktemp)"
  ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 \
      -R "80:localhost:${PORT}" nokey@localhost.run \
      > "${TUNNEL_LOG}" 2>&1 &
  TUNNEL_PID=$!

  TUNNEL_URL=""
  for _ in $(seq 1 25); do
    TUNNEL_URL="$(grep -oE 'https://[a-zA-Z0-9.-]+\.lhr\.life' "${TUNNEL_LOG}" | head -n1 || true)"
    if [ -n "${TUNNEL_URL}" ]; then break; fi
    if ! kill -0 "${TUNNEL_PID}" 2>/dev/null; then break; fi
    sleep 1
  done

  if [ -z "${TUNNEL_URL}" ]; then
    kill "${TUNNEL_PID}" 2>/dev/null || true
    TUNNEL_PID=""
    echo "[隧道] 未能从 SSH 隧道获取到公网 URL，日志内容："
    cat "${TUNNEL_LOG}" 2>/dev/null || true
    return 1
  fi
  return 0
}

if [ "$USE_TUNNEL" = "1" ]; then
  TUNNEL_URL=""

  if [ "$TUNNEL_MODE" = "ssh" ]; then
    start_ssh_tunnel || true
  elif [ "$TUNNEL_MODE" = "cloudflared" ]; then
    if ensure_cloudflared; then start_cloudflared_tunnel || true; fi
  else
    # auto: 先试 cloudflared（若已缓存或能快速下载），下载失败/超时立刻退到 SSH
    if ensure_cloudflared; then
      start_cloudflared_tunnel || start_ssh_tunnel || true
    else
      start_ssh_tunnel || true
    fi
  fi

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
    echo "[隧道] 未能建立公网隧道，继续以仅本地模式运行..."
  fi
fi

echo "本地访问: http://127.0.0.1:${PORT}/"
echo "按 Ctrl+C 停止服务（会同时关闭隧道）"
echo ""

# Loop around the server process instead of a single exec: viewer/server.py
# exits with a special code (SELF_UPDATE_EXIT_CODE, currently 78) after a
# remote POST /api/self_update successfully `git pull`s, to ask us to
# relaunch it. Handling that restart HERE (still inside this same shell/
# session) instead of letting server.py spawn a fully detached replacement
# process is what keeps the tunnel alive across a self-update: since the
# relaunched python3 stays a child of this same run_viewer.sh, our `trap
# cleanup EXIT` never fires just because one server instance exited to make
# room for the next one, so cloudflared/ssh keeps running the whole time on
# the same URL. Any other exit code (crash, Ctrl+C's SIGINT, etc) falls
# through and lets this script exit normally (tearing down the tunnel too).
SELF_UPDATE_EXIT_CODE=78
while true; do
  # `set -e` is in effect for the whole script (see top), which would
  # otherwise abort the script the instant python3 exits non-zero -- before
  # we ever get to inspect `code` below. Disable it just around this one
  # command so a self-update's special exit code can actually be handled.
  set +e
  python3 "${SCRIPT_DIR}/viewer/server.py" --host 127.0.0.1 --port "${PORT}" --root "${ROOT}"
  code=$?
  set -e
  if [ "${code}" -eq "${SELF_UPDATE_EXIT_CODE}" ]; then
    echo ""
    echo "[自更新] 收到重启请求（POST /api/self_update 已完成 git pull），正在原地重启 server（隧道 URL 不变）..."
    continue
  fi
  exit "${code}"
done
