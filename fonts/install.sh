#!/usr/bin/env bash
# 中文字体方案（Ubuntu 24.04 / free-arm 实测）：
#   sans-serif → Noto Sans CJK SC（界面）、serif → 霞鹜文楷（正文）、monospace → 霞鹜文楷 Bright Code NF（终端/编程，含 Nerd 图标）
# 用法：bash fonts/install.sh（可重复执行，已装的步骤会跳过）
set -euo pipefail

NF_TAG="26.10.22"            # lxgw/LxgwBright-Code 的 release 标签
NF_ASSET="LxgwBrightCodeNF.7z"
NF_DIR="/usr/share/fonts/truetype/lxgw-bright-code-nf"
CONF="/etc/fonts/conf.d/99-local-zh-prefer.conf"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/font-setup"

here=$(cd "$(dirname "$0")" && pwd)
SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo"

echo "== 1/4 安装 apt 字体"
$SUDO apt-get update -qq
$SUDO apt-get install -y fonts-lxgw-wenkai fonts-noto-cjk p7zip-full >/dev/null

echo "== 2/4 安装 LXGW Bright Code NF（apt 没有这个包，从 GitHub release 取）"
if ls "$NF_DIR"/*.ttf >/dev/null 2>&1; then
  echo "已存在，跳过：$NF_DIR"
else
  mkdir -p "$CACHE"
  archive="$CACHE/$NF_ASSET"
  [ -f "$archive" ] || curl -fL --retry 3 -o "$archive" \
    "https://github.com/lxgw/LxgwBright-Code/releases/download/$NF_TAG/$NF_ASSET"
  7z x -y -o"$CACHE/extract" "$archive" >/dev/null
  $SUDO mkdir -p "$NF_DIR"
  $SUDO cp "$CACHE/extract/LxgwBrightCodeNF"/*.ttf "$NF_DIR"/
fi

echo "== 3/4 安装 fontconfig 规则"
$SUDO cp "$here/99-local-zh-prefer.conf" "$CONF"
$SUDO fc-cache -f >/dev/null

echo "== 4/4 自检"
fail=0
check() {
  local query=$1 want=$2 got
  got=$(fc-match "$query")
  case $got in
    *"$want"*) printf '  ok   %-26s %s\n' "$query" "$got" ;;
    *) printf '  FAIL %-26s %s（期望含 %s）\n' "$query" "$got" "$want"; fail=1 ;;
  esac
}
check "sans-serif:lang=zh"     "Noto Sans CJK SC"
check "serif:lang=zh"          "LXGW WenKai"
check "monospace"              "LXGW Bright Code NF"
check "monospace:charset=f015" "LXGW Bright Code NF"   # Nerd Font 图标码位只有 NF 版有

if [ "$fail" -ne 0 ]; then
  echo "字体映射未达到预期，见上方 FAIL 行" >&2
  exit 1
fi
echo "字体方案就绪（新开的终端/应用才会生效）。"
