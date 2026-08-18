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
# 运行时目录（PID 与日志）。**优先级照大赛打包契约写**：
# AI_CREATOR_STATE_ROOT → XDG_STATE_HOME → HOME → /tmp
#
# 原先只写 "${HOME}/.${APP_ID}"，而契约明确说了
# 「Linux 脚本不得假设 Salt 提供 HOME」。平台执行部署时若 HOME 未设置，
# ${HOME} 展开成空串，路径就变成 /.ai-antifraud-persuasion——
# mkdir 在文件系统根目录上必然失败，整个部署挂在 install 这一步，
# 而且报错信息看不出是 HOME 的问题。
#
# HOME 仍然留在第三顺位（契约只要求"不得假设"，没禁止用）：
# 它比 /tmp 稳，/tmp 可能被系统清理，而 PID 文件必须跨 release 存活——
# 平台每次部署都会删掉并重建解压目录，PID 放那儿新版 stop 就找不到旧进程。
state_root() {
  if [ -n "${AI_CREATOR_STATE_ROOT:-}" ]; then echo "${AI_CREATOR_STATE_ROOT}"
  elif [ -n "${XDG_STATE_HOME:-}" ]; then echo "${XDG_STATE_HOME}"
  elif [ -n "${HOME:-}" ]; then echo "${HOME}"
  else echo "/tmp"; fi
}
RUNTIME_DIR="$(state_root)/.${APP_ID}"
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

# ── 5. 对局状态签名密钥 ─────────────────────────────────────────────────────
#
# **这一段是 2026-08-18 补的，补之前服务包在目标机上根本起不来。**
#
# 对局状态由客户端持有并签名（ADR-0003），app/config.py 在缺 STATE_SIGNING_SECRET
# 时**拒绝启动**——这条守则是对的，不能动。可 .env 按规范不进 ZIP（里面有真实
# API key），于是目标机上没有任何东西提供这个密钥：ZIP 能过全部结构校验、
# 能上传成功，然后死在 start 这一步，部署被判失败。
# 本地测不出来，因为本地项目目录里就躺着一个 .env。
#
# 做法：首次安装时生成一次性随机密钥，写进**跨 release 稳定**的运行时目录。
#   · 不写进仓库，也不写进 ZIP —— 每台机器各自一份，不是硬编码密钥
#   · 权限 600，只有当前部署用户读得到
#   · 重新部署时已存在就复用 —— 换密钥会让所有在玩的局当场作废
#   · 真实环境变量优先（start.sh 里判），运维想自己管密钥随时可以覆盖
SECRET_FILE="${RUNTIME_DIR}/state_signing_secret"
if [ -s "${SECRET_FILE}" ]; then
  log "复用已有的对局签名密钥"
else
  umask 077
  if ! "${VENV_DIR}/bin/python" -c "import secrets,sys; sys.stdout.write(secrets.token_urlsafe(48))" > "${SECRET_FILE}"; then
    fail "生成对局签名密钥失败"
  fi
  chmod 600 "${SECRET_FILE}" 2>/dev/null || true
  log "已生成对局签名密钥（仅本机，权限 600）"
fi

log "安装完成"
exit 0
