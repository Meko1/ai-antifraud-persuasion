#!/usr/bin/env bash
# 部署平台生命周期脚本 · 安装（含升级）
#
# 规范要求：
#   - 支持重复执行，已安装的依赖不应导致失败
#   - 依赖装在项目目录 / 虚拟环境 / 当前用户目录，不依赖管理员权限
#   - 成功返回 0；失败返回非 0，且失败原因输出到标准错误流
set -euo pipefail

APP_ID="ai-antifraud-persuasion"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${APP_DIR}/.venv"
RUNTIME_DIR="${HOME}/.${APP_ID}"
MIN_PY_MINOR=10

log()  { echo "[install] $*"; }
fail() { echo "[install][ERROR] $*" >&2; exit 1; }

log "应用目录: ${APP_DIR}"

# ── 1. 找一个 >= 3.10 的 python3 ─────────────────────────────────────────────
find_python() {
  local c bin
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    bin="$(command -v "$c" 2>/dev/null || true)"
    [ -n "$bin" ] || continue
    if "$bin" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, ${MIN_PY_MINOR}) else 1)" 2>/dev/null; then
      echo "$bin"
      return 0
    fi
  done
  return 1
}

PYTHON_BIN="$(find_python)" || fail \
  "未找到 Python 3.${MIN_PY_MINOR}+。CentOS 7.9 自带 3.6，版本过低，请联系运维预装 Python 3.10 及以上。"
log "使用 Python: ${PYTHON_BIN} ($("${PYTHON_BIN}" --version 2>&1))"

# ── 2. 创建 / 复用虚拟环境（幂等）────────────────────────────────────────────
if [ -x "${VENV_DIR}/bin/python" ]; then
  log "复用已有虚拟环境: ${VENV_DIR}"
else
  log "创建虚拟环境: ${VENV_DIR}"
  "${PYTHON_BIN}" -m venv "${VENV_DIR}" \
    || fail "创建虚拟环境失败，请确认目标机器已安装 venv 模块（python3-venv）"
fi

# ── 3. 安装依赖（幂等，重复执行不会失败）────────────────────────────────────
log "升级 pip / setuptools / wheel"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel \
  || fail "pip 自升级失败，请检查 pip 源是否可用（CentOS 7 官方源已停止维护）"

log "安装项目依赖"
"${VENV_DIR}/bin/python" -m pip install -r "${APP_DIR}/requirements.txt" \
  || fail "依赖安装失败，请检查 requirements.txt 与 pip 源可用性"

# ── 4. 准备运行时目录 ───────────────────────────────────────────────────────
# 放在 HOME 而不是解压目录：重新部署时解压目录会变，PID 文件必须跨版本稳定，
# 否则新版本的 stop.sh 找不到旧版本的进程。
mkdir -p "${RUNTIME_DIR}/logs" || fail "无法创建运行时目录 ${RUNTIME_DIR}"
log "运行时目录: ${RUNTIME_DIR}"

log "安装完成"
exit 0
