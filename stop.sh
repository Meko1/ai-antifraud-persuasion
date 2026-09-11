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
# 解压目录。只用来找 .venv 里那个 python（`port_is_free` 要用），
# 找不到就退回系统 python3——首次部署时 stop 跑在 install 之前，那时还没有 .venv。
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
  # netstat 是第三条路。**部署机上它可能是唯一一条**：2026-09-11 那台
  # CentOS 上运维就是用 `netstat -tlnp` 找到那个遗留进程的，而前面两个
  # 要么没装、要么在非 root 下看不见别人的进程（ss 只对自己的 socket 报 pid）。
  if [ -z "${pid}" ] && command -v netstat >/dev/null 2>&1; then
    pid="$(netstat -tlnp 2>/dev/null \
           | awk -v p=":${PORT}\$" '$4 ~ p {split($7, a, "/"); print a[1]; exit}')"
    case "${pid}" in ''|*[!0-9]*) pid="" ;; esac
  fi
  echo "${pid}"
}

# 端口到底还有没有人在 LISTEN。**这一问不需要看得见对方的 PID**——
# 上面三条路在非 root 下都可能把进程号藏起来，而"还占着"这件事本身
# 是藏不住的：bind 一下就知道。
port_is_free() {
  local py=""
  if [ -x "${APP_DIR:-}/.venv/bin/python" ]; then py="${APP_DIR}/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then py="python3"
  else return 0; fi   # 没 python 可用就不猜，当它是空的
  "${py}" - "${PORT}" <<'PY' >/dev/null 2>&1
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("0.0.0.0", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
}

# 收尾时统一走这一条：**端口还占着就不许报"已停止"**。
#
# 2026-09-11 之前这里是直接 `exit 0`。那天的实际经过：PID 文件对不上、
# 三条反查都没看见那个进程，于是本脚本打印「未发现运行中的服务，无需停止」
# 并返回 0——而 21818 上明明还有一个上一次部署的 python 在 LISTEN。
# start.sh 接着起新进程、bind 失败，健康检查却被那个旧进程答成功，
# 部署判定成功，跑的是旧版本。**这一行 exit 0 就是那次事故的源头。**
finish_ok() {
  if port_is_free; then
    exit 0
  fi
  echo "[stop][ERROR] 端口 ${PORT} 仍然有人在 LISTEN，但本脚本认不出它是不是本作品的进程。" >&2
  echo "[stop][ERROR] **不会去杀一个认不出来的进程**（打包契约：不得影响其他作品）。" >&2
  echo "[stop][ERROR] 就这么返回 0 的话，接下来的 start.sh 会把健康检查打在这个旧进程上，" >&2
  echo "[stop][ERROR] 部署会被判成功，而跑着的是上一个版本。定位并处理：" >&2
  echo "[stop][ERROR]   sudo netstat -tlnp | grep ${PORT}    # 或 sudo ss -lptn 'sport = :${PORT}'" >&2
  echo "[stop][ERROR]   sudo kill <那个 PID>" >&2
  exit 1
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
  # 首次部署的正常路径：服务本来就没起来。
  # **但要先确认端口真的是空的**，见 finish_ok 的注释。
  log "未发现运行中的服务，无需停止"
  finish_ok
fi

log "发送 TERM 信号到 ${TARGET_PID}"
kill -TERM "${TARGET_PID}" 2>/dev/null || true

for i in $(seq 1 "${GRACE_SECONDS}"); do
  if ! kill -0 "${TARGET_PID}" 2>/dev/null; then
    rm -f "${PID_FILE}"
    log "进程已退出 (${i}s)"
    finish_ok
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
finish_ok
