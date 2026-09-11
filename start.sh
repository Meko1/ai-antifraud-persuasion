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
READY_URL="http://127.0.0.1:${PORT}/readyz"
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

# ── 1b. 端口必须真的是空的 ──────────────────────────────────────────────────
#
# **2026-09-11 实测撞见的一次"部署成功但跑的是旧版本"。**
#
# 上一次部署留下的进程还占着 21818（PID 文件里那个号已经对不上了，stop.sh
# 因此没认出它，见 stop.sh 同批改动）。于是这一轮：
#
#   1. 新进程起来，bind 失败，一两秒后退出；
#   2. 下面那个健康检查的 curl 打在**那个旧进程**上，照样 200；
#   3. 脚本打印「服务就绪，健康检查通过 (2s)」，退出码 0，平台判定部署成功。
#
# 跑着的却是上一个版本。**这种失败最贵**：端口通、页面在、接口全对，
# 只有行为是旧的——当天是靠 /healthz 里少了一个字段才看出来的。
#
# 下面那个 `kill -0 ${APP_PID}` 挡不住它：新进程要花一两秒 import 完才去
# bind，而健康检查在第 1 秒就已经被旧进程答成功了，赛跑赢的是旧的那个。
#
# 所以在起进程**之前**先确认端口是空的。用真的 bind 一下来判，不靠
# lsof/ss/netstat——那三个在部署机上不一定装、不一定有权限看到别人的进程，
# 而 bind 失败与否正是 uvicorn 待会儿要面对的同一件事。
port_is_free() {
  "${VENV_PY}" - "${PORT}" <<'PY' >/dev/null 2>&1
import socket, sys
s = socket.socket()
# uvicorn 也设这一位：TIME_WAIT 不算占用，真的有人在 LISTEN 才算
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("0.0.0.0", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
}

if ! port_is_free; then
  log "端口 ${PORT} 已被占用，先执行 stop.sh"
  "${APP_DIR}/stop.sh" || true
  sleep 1
fi

if ! port_is_free; then
  # 走到这里说明占着端口的那个进程 stop.sh 认不出来（多半是上一次部署的
  # 遗留，或者属于别的用户）。**绝不在这里乱杀**——打包契约写着"不得使用
  # 范围过大的匹配条件影响其他作品"。如实报错、给出定位命令，交给人。
  echo "[start][ERROR] 端口 ${PORT} 被别的进程占着，而 stop.sh 没能认出它。" >&2
  echo "[start][ERROR] 继续启动的话，新进程会 bind 失败退出，" >&2
  echo "[start][ERROR] 而健康检查会打在那个旧进程上，把部署判成功——跑的却是旧版本。" >&2
  echo "[start][ERROR] 定位并处理：" >&2
  echo "[start][ERROR]   sudo netstat -tlnp | grep ${PORT}    # 或 sudo ss -lptn 'sport = :${PORT}'" >&2
  echo "[start][ERROR]   sudo kill <那个 PID>" >&2
  exit 1
fi

# ── 2. 载入部署机上的本地环境文件（可选）────────────────────────────────────
#
# **这是给密钥用的唯一一条通路。** `.env` 里有真实 API key，按打包规范不进
# ZIP；而平台通过 Salt 执行 stop/install/start，中间没有地方能传环境变量。
# 于是任何需要密钥的配置（大模型网关、Redis 口令）在目标机上都没有来源。
#
# 做法：运维在**跨 release 稳定**的运行时目录里放一个 env 文件，一次就够，
# 之后每次重新部署都自动带上——解压目录会被平台删掉重建，这个目录不会。
#
#   ${RUNTIME_DIR}/env      权限建议 600
#
# 里面按 KEY=VALUE 写，例如（口令用真值替换，**不要提交进仓库**）：
#   INTERNAL_LLM_API_KEY=...
#   REDIS_URL=redis://:口令@10.126.192.12:7001/0
#
# `set -a` 让文件里的赋值自动导出；用完立刻关掉，别影响后面的局部变量。
# 已经存在的真实环境变量不会被覆盖（下面几处都是 `-z` 判空才取值）。
ENV_FILE="${RUNTIME_DIR}/env"
if [ -f "${ENV_FILE}" ]; then
  log "载入本地环境文件: ${ENV_FILE}"
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
fi

# ── 3. 注入对局签名密钥 ─────────────────────────────────────────────────────
#
# 优先用真实环境变量（运维自己管密钥时走这条），否则读 install.sh 生成的那份。
#
# **两者都没有时不在这里报错。** 开发机上密钥来自项目目录里的 `.env`，
# 那是 python-dotenv 在**应用进程内**读的，脚本这一层根本看不见——
# 在这儿 fail 会把本地 `./start.sh` 直接打死（第一版就是这么写的，当场翻车）。
# 真的缺，让 app/config.py 去拒绝启动：那条报错写得比这里清楚，
# 而且下面的健康检查循环会把日志尾巴打出来。
if [ -z "${STATE_SIGNING_SECRET:-}" ]; then
  SECRET_FILE="${RUNTIME_DIR}/state_signing_secret"
  if [ -s "${SECRET_FILE}" ]; then
    STATE_SIGNING_SECRET="$(cat "${SECRET_FILE}")"
    export STATE_SIGNING_SECRET
  fi
fi

# ── 4. 后台拉起服务 ─────────────────────────────────────────────────────────
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

# ── 5. 等待健康检查通过后才返回 0 ───────────────────────────────────────────
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

# /readyz 报的是"这一局能不能真的调模型"（llm.configured || offline_demo），
# 与 /healthz 是两件事，**不能拿它当部署门槛**：ADR-0005 明确接受"网关故障时
# 降级到兜底台词，不改变数据流向"，本地测试与离线演示都合法地"活着但没配模型"。
# 拿它卡部署，等于把一个设计上允许的降级状态变成部署失败——不是这个函数该管的事。
#
# 它只负责在这里**喊出来**：2026-08-24 那次事故是配置链断在部署机上
# （.env 按规范不进包，运维没在 RUNTIME_DIR/env 补上），/healthz 全程 ok，
# 于是没人发现——玩家看到的每一句都是兜底台词，直到有人截图问「AI 呢」。
# 这条检查探到就打印，探不到也不影响退出码，纯粹是运维肉眼能看见的那一行。
warn_if_not_ready() {
  local body
  body="$("${VENV_PY}" - "${READY_URL}" <<'PY' 2>/dev/null
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=3) as r:
        print(r.read().decode())
except urllib.error.HTTPError as e:
    print(e.read().decode())
except Exception:
    pass
PY
  )"
  [ -n "${body}" ] || return 0
  # RUNTIME_DIR 当第二个参数传进去，**不能指望 Python 里插值**：
  # 这个 heredoc 用的是带引号的 'PY'（防止 body 里的反引号/$ 被 bash 当命令展开），
  # 带引号就意味着 bash 不会展开里面任何 ${VAR}，写在 Python 字符串里的
  # ${RUNTIME_DIR} 只会原样打印成这几个字符，而不是那个真实路径。
  "${VENV_PY}" - "${body}" "${RUNTIME_DIR}" <<'PY'
import json, sys
try:
    d = json.loads(sys.argv[1])
except Exception:
    sys.exit(0)
if d.get("ready"):
    sys.exit(0)
reason = d.get("reason", "未知原因")
runtime_dir = sys.argv[2]
print("", file=sys.stderr)
print("=" * 70, file=sys.stderr)
print("[start][警告] 服务已启动，但 /readyz 未就绪：" + reason, file=sys.stderr)
print("玩家现在看到的每一句台词都来自兜底台词库，不是大模型生成的回复。", file=sys.stderr)
print(f"请检查 {runtime_dir}/env 里的 *_LLM_* 变量，改完 ./stop.sh && ./start.sh 即可。",
      file=sys.stderr)
print("=" * 70, file=sys.stderr)
print("", file=sys.stderr)
PY
}

# ── 网关连通性：/readyz 答不了的那半个问题 ──────────────────────────────────
#
# `/readyz` 判的是 `llm.configured`，而 configured 只看**变量填没填**，
# 不看**打不打得通**。这两件事在开发机上从来不会分开，在大赛服务器上大概率
# 会分开——官方 FAQ Q2 写着分配的机器在嘉定独立网段，「与公司周浦、浦江以及
# 嘉定 T/P 机房网络隔离」，而内网模型网关在哪一侧没人核实过。
#
# 不核实的代价是一种最难发现的失败：/healthz ok、/readyz ready、
# 部署脚本退出码 0、平台判定成功，**而玩家看到的每一句都是兜底台词**，
# 一直到赛期结束。这与 8-24 那次事故是同一种结局，只是根因换了一个。
#
# ADR-0007 的自动切换**接不住这一种**：它的判据是网关回的
# 「该令牌状态不可用」那句原文，网络不可达连不到那个分支。
#
# 所以这里只做一件事：**探一次，探不通就把话说死**，包括下一步该改哪个变量。
# 与 warn_if_not_ready 一样，不影响退出码——探测失败不等于部署失败，
# 兜底台词库仍然能把一局走完（ADR-0005）。
warn_if_gateway_unreachable() {
  local body
  body="$("${VENV_PY}" - "http://127.0.0.1:${PORT}/healthz?probe=1" <<'PY' 2>/dev/null
import sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=15) as r:
        print(r.read().decode())
except Exception:
    pass
PY
  )"
  [ -n "${body}" ] || return 0
  "${VENV_PY}" - "${body}" "${RUNTIME_DIR}" <<'PY'
import json, sys
try:
    d = json.loads(sys.argv[1])
except Exception:
    sys.exit(0)
if d.get("offline_demo"):
    sys.exit(0)              # 离线演示模式本来就不该有网关，探不通是预期

provider = d.get("llm_provider", "?")
runtime_dir = sys.argv[2]
probe = d.get("llm_probe") or {}


def why(p):
    return p.get("reason") or p.get("error") or p.get("status_code") or "未知"


def banner(lines):
    print("", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    for line in lines:
        print(line, file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print("", file=sys.stderr)


if not probe.get("ok"):
    banner([
        f"[start][警告] 大模型网关探测失败（provider={provider}）：{why(probe)}",
        "服务是活的，但玩家看到的每一句台词都会来自兜底台词库。",
        "",
        "最可能的原因：这台机器在独立网段，访问不到内网网关。",
        f"处理：在 {runtime_dir}/env 里把 LLM_PROVIDER 改成 public",
        "      （PUBLIC_LLM_* 三个变量要先填好），然后 ./stop.sh && ./start.sh。",
        "      实在都不通，就用 OFFLINE_DEMO=true —— 罐头台词，但至少不装活。",
    ])
    sys.exit(0)

# ── 网关通了，但 ADR-0007 那条退路还没人验过 ────────────────────────────
#
# 这一段回答的是另一个问题：**内网 token 明天用尽的话，这台机器接得住吗。**
# 那是本仓库自己撞过三次的事（2026-08-15 / 08-22 / 08-23，每次持续 8 小时
# 以上），而它的失败样子是安静的——/healthz 一路 ok，只是每一句都变成
# 兜底台词。两种接不住，分开报：
failover = d.get("llm_failover") or {}
fallback = failover.get("fallback")

if provider == "internal" and not fallback:
    banner([
        "[start][警告] 内网网关通了，但没有配退路（ADR-0007）。",
        "内网 token 一旦用尽（本仓库撞过三次，每次持续一整个工作日），",
        "往后每一句台词都会来自兜底台词库，而 /healthz 仍然一路 ok。",
        "",
        f"处理：在 {runtime_dir}/env 里填好 PUBLIC_LLM_BASE_URL /",
        "      PUBLIC_LLM_API_KEY / PUBLIC_LLM_MODEL 三个变量，重启即生效。",
    ])
    sys.exit(0)

fb_probe = d.get("llm_probe_fallback")
if fallback and fb_probe is not None and not fb_probe.get("ok"):
    banner([
        "[start][警告] 退路本身探不通（ADR-0007）："
        f"{fallback.get('provider')} / {fallback.get('model')}",
        f"原因：{why(fb_probe)}",
        "",
        "现在是好的：内网网关通着，对局一切正常。",
        "但内网 token 用尽那天，自动切换会'成功'然后立刻再失败，",
        "最终仍然落回兜底台词——那时候查这个问题要贵得多。",
        "",
        f"处理：核对 {runtime_dir}/env 里的 PUBLIC_LLM_* 三个变量"
        "（地址、密钥、模型名、账号余额）。",
    ])
PY
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
    warn_if_not_ready
    warn_if_gateway_unreachable
    exit 0
  fi
  sleep 1
done

echo "[start][ERROR] ${START_TIMEOUT}s 内健康检查未通过，最后 40 行日志：" >&2
tail -n 40 "${LOG_FILE}" >&2 || true
kill "${APP_PID}" 2>/dev/null || true
rm -f "${PID_FILE}"
exit 1
