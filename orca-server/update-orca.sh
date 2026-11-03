#!/usr/bin/env bash
# ==============================================================================
# Orca Headless Server 更新维护脚本 (Linux ARM64 / Free-Arm)
# 适用仓库: stablyai/orca
# ==============================================================================

set -euo pipefail

# 默认变量（可通过环境变量覆盖）
ORCA_DIR="${ORCA_DIR:-/home/ubuntu/apps/orca}"
CURRENT_SQUASHFS="${ORCA_DIR}/squashfs-root"
APPIMAGE_NAME="orca-linux-arm64.AppImage"
PAIRING_IP="${PAIRING_IP:-100.126.255.75}"
PORT="${PORT:-6768}"
LOG_FILE="${ORCA_DIR}/logs/daemon.log"
CONFIG_LOG_FILE="/home/ubuntu/.config/orca/logs/daemon.log"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info() { echo -e "${BLUE}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# 参数解析
FORCE=false
RESTART=false
TARGET_VERSION=""

while [[ $# -gt 0 ]]; do
  if [[ "$1" =~ ^(-v|--version|-d|--dir|--ip|-p|--port)$ && $# -lt 2 ]]; then
    error "参数 $1 缺少取值"
  fi
  case "$1" in
    --force|-f)
      FORCE=true
      shift
      ;;
    --restart|-r)
      RESTART=true
      shift
      ;;
    --version|-v)
      TARGET_VERSION="$2"
      shift 2
      ;;
    --dir|-d)
      ORCA_DIR="$2"
      CURRENT_SQUASHFS="${ORCA_DIR}/squashfs-root"
      shift 2
      ;;
    --ip)
      PAIRING_IP="$2"
      shift 2
      ;;
    --port|-p)
      PORT="$2"
      shift 2
      ;;
    --help|-h)
      echo "用法: $0 [选项]"
      echo "选项:"
      echo "  -r, --restart        更新文件后自动平滑重启 orcad 服务"
      echo "  -f, --force          即使版本相同也强制重新下载并解压"
      echo "  -v, --version <v>    指定更新版本 (例如 v1.4.247)，默认自动拉取 GitHub latest"
      echo "  -d, --dir <path>     指定 Orca 安装目录 (默认: /home/ubuntu/apps/orca)"
      echo "      --ip <ip>        指定 pairing-address 监听广告 IP (默认: 100.126.255.75)"
      echo "  -p, --port <port>    指定服务监听端口 (默认: 6768)"
      echo "  -h, --help           显示本帮助信息"
      exit 0
      ;;
    *)
      error "未知参数: $1"
      ;;
  esac
done

if [[ ! -d "${ORCA_DIR}" ]]; then
  error "Orca 安装目录不存在: ${ORCA_DIR}"
fi

# 脚本是否跑在 Orca 里：内置终端会注入 ORCA_PANE_KEY；automation/agent 派生的进程靠祖先链识别。
running_inside_orca() {
  [[ -n "${ORCA_PANE_KEY-}" ]] && return 0
  local p=${PPID}
  while [[ ${p} -gt 1 ]]; do
    if ps -o args= -p "${p}" 2>/dev/null | grep -qE 'orcad-server\.js|orca-ide|out/cli/index\.js serve'; then
      return 0
    fi
    p=$(ps -o ppid= -p "${p}" 2>/dev/null | tr -d ' ' || true)
    [[ -z ${p} ]] && return 1
  done
  return 1
}

restart_orca() {
  if running_inside_orca; then
    error "检测到脚本跑在 Orca 里（内置终端或 Orca 派生的进程）：pkill 会先杀掉脚本自己，启动新进程那步永远执行不到。请从 SSH 会话运行。"
  fi

  warn "正在重启 Orca 守护进程..."

  pkill -f "out/cli/index.js serve" 2>/dev/null || true
  pkill -f "orcad-server.js" 2>/dev/null || true
  sleep 2

  mkdir -p "$(dirname "${CONFIG_LOG_FILE}")"

  info "启动新版 Orca 守护服务..."
  nohup "${CURRENT_SQUASHFS}/orca-ide" \
    "${CURRENT_SQUASHFS}/resources/app.asar.unpacked/out/cli/index.js" \
    serve --port "${PORT}" --pairing-address "${PAIRING_IP}" \
    > "${CONFIG_LOG_FILE}" 2>&1 &

  info "等待服务就绪校验..."
  SUCCESS=false
  for i in {1..15}; do
    if curl -s -I "http://127.0.0.1:${PORT}/web-index.html" | grep -q "200 OK"; then
      SUCCESS=true
      break
    fi
    sleep 1
  done

  if [[ "${SUCCESS}" == "true" ]]; then
    success "Orca 服务已成功重启并就绪！(端口: ${PORT})"
    NEW_PID=$(pgrep -f "out/cli/index.js serve" | head -n 1 || echo "未知")
    info "当前服务 PID: ${NEW_PID}"
  else
    error "服务未在 15s 内响应 200 OK，请检查日志: ${CONFIG_LOG_FILE}"
  fi
}

cd "${ORCA_DIR}"

# 1. 检查当前安装版本
LOCAL_VERSION="unknown"
PKG_JSON="${CURRENT_SQUASHFS}/resources/app.asar.unpacked/out/package.json"
if [[ -f "${PKG_JSON}" ]]; then
  LOCAL_VERSION=$(python3 -c "import json; print(json.load(open('${PKG_JSON}')).get('version', 'unknown'))" 2>/dev/null || echo "unknown")
fi
info "当前本地版本: ${LOCAL_VERSION}"

# 2. 查询 GitHub 最新 Release
if [[ -z "${TARGET_VERSION}" ]]; then
  info "正在查询 GitHub 最新发布版本..."
  LATEST_JSON=$(curl -sSL "https://api.github.com/repos/stablyai/orca/releases/latest")
  TARGET_TAG=$(echo "${LATEST_JSON}" | grep -Po '"tag_name":\s*"\K[^"]+' || true)
  if [[ -z "${TARGET_TAG}" ]]; then
    error "无法从 GitHub 获取最新版本信息，请检查网络或通过 -v 指定版本。"
  fi
  TARGET_VERSION="${TARGET_TAG}"
else
  info "已指定目标版本: ${TARGET_VERSION}"
  LATEST_JSON=$(curl -sSL "https://api.github.com/repos/stablyai/orca/releases/tags/${TARGET_VERSION}")
fi

CLEAN_TARGET_VER="${TARGET_VERSION#v}"
info "目标版本: ${TARGET_VERSION} (版本号: ${CLEAN_TARGET_VER})"

if [[ "${LOCAL_VERSION}" == "${CLEAN_TARGET_VER}" && "${FORCE}" != "true" ]]; then
  success "当前版本已是最新 (${LOCAL_VERSION})，无需更新。"
  if [[ "${RESTART}" == "true" ]]; then
    restart_orca
  else
    info "如需强制重装请添加 --force 参数；如需重启服务请添加 --restart 参数。"
  fi
  exit 0
fi

# 3. 提取 arm64 AppImage 下载链接
DOWNLOAD_URL=$(echo "${LATEST_JSON}" | grep -Po '"browser_download_url":\s*"\K[^"]*orca-linux-arm64\.AppImage' | head -n 1 || true)
if [[ -z "${DOWNLOAD_URL}" ]]; then
  DOWNLOAD_URL="https://github.com/stablyai/orca/releases/download/${TARGET_VERSION}/orca-linux-arm64.AppImage"
fi

info "下载地址: ${DOWNLOAD_URL}"

# 4. 下载新版 AppImage
TMP_APPIMAGE="${ORCA_DIR}/${APPIMAGE_NAME}.tmp"
info "正在下载 ${APPIMAGE_NAME}..."
rm -f "${TMP_APPIMAGE}"
curl -fL --progress-bar -o "${TMP_APPIMAGE}" "${DOWNLOAD_URL}" || error "下载失败，请检查网络或 Release 资源是否存在。"
chmod +x "${TMP_APPIMAGE}"
success "下载完成。"

# 5. 解压到隔离的新目录 (蓝绿解包)
TMP_EXTRACT_DIR="${ORCA_DIR}/squashfs-root.new"
info "正在解包 AppImage 到隔离临时目录..."
rm -rf "${TMP_EXTRACT_DIR}"

mkdir -p "${TMP_EXTRACT_DIR}"
(
  cd "${TMP_EXTRACT_DIR}"
  "${TMP_APPIMAGE}" --appimage-extract >/dev/null 2>&1 || error "AppImage 解包失败！"
)

EXTRACTED_CONTENT="${TMP_EXTRACT_DIR}/squashfs-root"
if [[ ! -f "${EXTRACTED_CONTENT}/orca-ide" ]]; then
  error "解包文件校验失败: 未找到 ${EXTRACTED_CONTENT}/orca-ide"
fi

# 6. 原子切换目录与可执行文件
# Electron 的 SUID sandbox 必须 root:root 4755；普通用户解包会丢 setuid，启动即 FATAL。
# 必须在切换前对解包目录做，sudo 失败时旧版本原封不动，不会留下跑不起来的新目录。
info "重设 chrome-sandbox 权限..."
sudo chown root:root "${EXTRACTED_CONTENT}/chrome-sandbox"
sudo chmod 4755 "${EXTRACTED_CONTENT}/chrome-sandbox"

info "替换旧版本核心文件..."
BACKUP_DIR="${ORCA_DIR}/squashfs-root.bak"
rm -rf "${BACKUP_DIR}"

if [[ -d "${CURRENT_SQUASHFS}" ]]; then
  mv "${CURRENT_SQUASHFS}" "${BACKUP_DIR}"
fi

mv "${EXTRACTED_CONTENT}" "${CURRENT_SQUASHFS}"
rm -rf "${TMP_EXTRACT_DIR}"
mv "${TMP_APPIMAGE}" "${ORCA_DIR}/${APPIMAGE_NAME}"

success "文件更新完成！新版本已就绪。"

# 7. 重启服务处理
if [[ "${RESTART}" == "true" ]]; then
  restart_orca
else
  echo ""
  echo -e "${YELLOW}================================================================${NC}"
  echo -e "核心文件已更新，但未重启后台服务（避免中断你当前的会话）。"
  echo -e "当你空闲时，请执行以下命令完成重启生效："
  echo -e ""
  echo -e "  ${GREEN}pkill -f 'out/cli/index.js serve' || true; pkill -f 'orcad-server.js' || true${NC}"
  echo -e "  ${GREEN}nohup ${CURRENT_SQUASHFS}/orca-ide ${CURRENT_SQUASHFS}/resources/app.asar.unpacked/out/cli/index.js serve --port ${PORT} --pairing-address ${PAIRING_IP} > ${CONFIG_LOG_FILE} 2>&1 &${NC}"
  echo -e ""
  echo -e "或者下次直接带 --restart 参数运行更新脚本："
  echo -e "  ${BLUE}${ORCA_DIR}/update-orca.sh --restart${NC}"
  echo -e "${YELLOW}================================================================${NC}"
fi
