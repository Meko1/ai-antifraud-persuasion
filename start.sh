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
RUNTIME_DIR="${HOME}/.${APP_ID}"
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

# ── 2. 后台拉起服务 ─────────────────────────────────────────────────────────
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
