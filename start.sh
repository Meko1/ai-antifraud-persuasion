#!/usr/bin/env bash
# 部署平台生命周期脚本 · 启动
#
# 规范要求：
#   - 必须在确认服务已成功启动后才返回 0
#   - 服务进程须在后台持续运行，脚本本身不能长期占用部署任务
#   - 应避免重复启动同一服务
#   - 关键启动过程与错误信息输出到控制台
set -euo pipefail

APP_ID="ai-antifraud-persuasion"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="${APP_DIR}/.venv/bin/python"
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
PID_FILE="${RUNTIME_DIR}/app.pid"
LOG_FILE="${RUNTIME_DIR}/logs/app.log"
PORT="${PORT:-21818}"
HEALTH_URL="http://127.0.0.1:${PORT}/healthz"
START_TIMEOUT=60

log()  { echo "[start] $*"; }
fail() { echo "[start][ERROR] $*" >&2; exit 1; }

[ -x "${VENV_PY}" ] || fail "虚拟环境不存在: ${VENV_PY}，请先执行 install.sh"
mkdir -p "${RUNTIME_DIR}/logs" || fail "无法创建运行时目录 ${RUNTIME_DIR}"

# ── 1. 避免重复启动 ─────────────────────────────────────────────────────────
if [ -f "${PID_FILE}" ]; then
  OLD_PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [ -n "${OLD_PID}" ] && kill -0 "${OLD_PID}" 2>/dev/null; then
    log "检测到服务已在运行 (PID ${OLD_PID})，先执行 stop.sh 停止旧进程"
    "${APP_DIR}/stop.sh" || fail "停止旧进程失败，中止启动"
  fi
fi

# ── 2. 注入对局签名密钥 ─────────────────────────────────────────────────────
#
# 优先用真实环境变量（运维自己管密钥时走这条），否则读 install.sh 生成的那份。
# 两者都没有时不在这里假装成功——让 app/config.py 去拒绝启动，并把原因说清楚。
if [ -z "${STATE_SIGNING_SECRET:-}" ]; then
  SECRET_FILE="${RUNTIME_DIR}/state_signing_secret"
  if [ -s "${SECRET_FILE}" ]; then
    STATE_SIGNING_SECRET="$(cat "${SECRET_FILE}")"
    export STATE_SIGNING_SECRET
  else
    fail "缺少 STATE_SIGNING_SECRET，且未找到 ${SECRET_FILE}；请先执行 install.sh"
  fi
fi

# ── 3. 后台拉起服务 ─────────────────────────────────────────────────────────
log "启动服务，端口 ${PORT}，日志 ${LOG_FILE}"
cd "${APP_DIR}"
nohup "${VENV_PY}" -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --log-level info \
  >> "${LOG_FILE}" 2>&1 &

APP_PID=$!
echo "${APP_PID}" > "${PID_FILE}"
log "进程已拉起 (PID ${APP_PID})"

# ── 3. 等待健康检查通过后才返回 0 ───────────────────────────────────────────
# 部署平台按退出码判定成败，所以这里必须真的确认服务可用，
# 不能进程一拉起就返回 0 —— 那样端口没起来也会被判成"部署成功"。
probe_health() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 3 "${HEALTH_URL}" >/dev/null 2>&1
  else
    "${VENV_PY}" - "${HEALTH_URL}" <<'PY' >/dev/null 2>&1
import sys, urllib.request
urllib.request.urlopen(sys.argv[1], timeout=3).read()
PY
  fi
}

log "等待服务就绪 (最长 ${START_TIMEOUT}s)…"
for i in $(seq 1 "${START_TIMEOUT}"); do
  if ! kill -0 "${APP_PID}" 2>/dev/null; then
    echo "[start][ERROR] 进程已退出，最后 40 行日志：" >&2
    tail -n 40 "${LOG_FILE}" >&2 || true
    rm -f "${PID_FILE}"
    exit 1
  fi
  if probe_health; then
    log "服务就绪，健康检查通过 (${i}s)"
    log "访问地址: http://<server-ip>:${PORT}/"
    exit 0
  fi
  sleep 1
done

echo "[start][ERROR] ${START_TIMEOUT}s 内健康检查未通过，最后 40 行日志：" >&2
tail -n 40 "${LOG_FILE}" >&2 || true
kill "${APP_PID}" 2>/dev/null || true
rm -f "${PID_FILE}"
exit 1
