@echo off
chcp 65001 >nul
title 更新 Prototype 本地预览器 ^
echo ==============================
echo   正在从 GitHub 拉取最新预览器代码
echo   仓库: 204343414/prototype-Game-p3d (公开仓库, 无需 token)
echo ==============================
echo.

REM ---- 把这个 .bat 放在你的预览器目录里再运行 ----
REM (就是含 viewer\ 和 tools\ 的那个文件夹)

setlocal
set TMPD=%TEMP%\proto-update-%RANDOM%
set ROOT=%~dp0

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;" ^
  "Write-Host '[1/3] 下载最新代码...';" ^
  "Invoke-WebRequest 'https://github.com/204343414/prototype-Game-p3d/archive/refs/heads/main.zip' -OutFile '%TMPD%.zip';" ^
  "Write-Host '[2/3] 解压...';" ^
  "Expand-Archive -Force '%TMPD%.zip' '%TMPD%';" ^
  "Write-Host '[3/3] 覆盖到预览器目录(保留你的 art.rcf 等本地配置)...';" ^
  "robocopy '%TMPD%\prototype-Game-p3d-main' '%ROOT%' /E /XD .git __pycache__ /NFL /NDL /NJH ^| Out-Null;" ^
  "Remove-Item -Recurse -Force '%TMPD%','%TMPD%.zip';" ^
  "Write-Host '完成'" 

if %ERRORLEVEL% NEQ 0 (
  echo.
  echo [失败] 请确认网络可达 github.com,然后重试。
  pause
  exit /b 1
)

echo.
echo ==============================
echo   更新完成! 请重启预览器:
echo   1. 关掉旧预览器窗口(ctrl+c 或直接关 cmd 窗口)
echo   2. 重新启动 server.py
echo   3. 在预览器里重新导出你要的实体
echo ==============================
pause
