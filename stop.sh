#!/usr/bin/env bash
# 部署平台生命周期脚本 · 停止
#
# 规范要求：
#   - 首次部署时服务尚未运行，此时也必须返回 0，不得把"进程不存在"当失败
#   - 只能停止本作品自身的进程，不得使用范围过大的匹配条件影响其他作品
#   - 不能仅依赖上一版本解压目录中的临时文件（重新部署时执行的是新包的 stop.sh）
#   - 应等待进程真正退出；停止失败返回非 0 并输出明确原因
set -uo pipefail

APP_ID="ai-antifraud-persuasion"
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
PORT="${PORT:-21818}"
# 进程特征：只有同时匹配它，才会被认定为"本作品的进程"
APP_MARKER="app.main:app"
GRACE_SECONDS=15

log()  { echo "[stop] $*"; }
fail() { echo "[stop][ERROR] $*" >&2; exit 1; }

# 校验候选 PID 确实是本作品的进程，避免误杀同机器上的其他作品
is_our_process() {
  local pid="$1" cmd
  [ -n "${pid}" ] || return 1
  kill -0 "${pid}" 2>/dev/null || return 1
  cmd="$(ps -p "${pid}" -o command= 2>/dev/null || true)"
  case "${cmd}" in
    *"${APP_MARKER}"*) return 0 ;;
    *) return 1 ;;
  esac
}

# 兜底定位：PID 文件丢失时，从端口反查监听进程，再用进程特征二次确认。
# 两道校验都过才动手，符合"不得使用范围过大的匹配条件"的要求。
find_pid_by_port() {
  local pid=""
  if command -v lsof >/dev/null 2>&1; then
    pid="$(lsof -ti "tcp:${PORT}" -sTCP:LISTEN 2>/dev/null | head -n 1)"
  fi
  if [ -z "${pid}" ] && command -v ss >/dev/null 2>&1; then
    pid="$(ss -lptnH "sport = :${PORT}" 2>/dev/null \
           | grep -o 'pid=[0-9]*' | head -n 1 | cut -d= -f2)"
  fi
  echo "${pid}"
}

TARGET_PID=""

if [ -f "${PID_FILE}" ]; then
  CANDIDATE="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if is_our_process "${CANDIDATE}"; then
    TARGET_PID="${CANDIDATE}"
    log "由 PID 文件定位到进程 ${TARGET_PID}"
  else
    log "PID 文件存在但进程已不在（或不属于本作品），清理陈旧 PID 文件"
    rm -f "${PID_FILE}"
  fi
fi

if [ -z "${TARGET_PID}" ]; then
  CANDIDATE="$(find_pid_by_port)"
  if is_our_process "${CANDIDATE}"; then
    TARGET_PID="${CANDIDATE}"
    log "由端口 ${PORT} 反查到本作品进程 ${TARGET_PID}"
  fi
fi

if [ -z "${TARGET_PID}" ]; then
  # 首次部署的正常路径：服务本来就没起来
  log "未发现运行中的服务，无需停止"
  exit 0
fi

log "发送 TERM 信号到 ${TARGET_PID}"
kill -TERM "${TARGET_PID}" 2>/dev/null || true

for i in $(seq 1 "${GRACE_SECONDS}"); do
  if ! kill -0 "${TARGET_PID}" 2>/dev/null; then
    rm -f "${PID_FILE}"
    log "进程已退出 (${i}s)"
    exit 0
  fi
  sleep 1
done

log "优雅停止超时，发送 KILL 信号"
kill -KILL "${TARGET_PID}" 2>/dev/null || true
sleep 2

if kill -0 "${TARGET_PID}" 2>/dev/null; then
  fail "进程 ${TARGET_PID} 在 KILL 后仍存活，请人工介入排查"
fi

rm -f "${PID_FILE}"
log "进程已强制终止"
exit 0
