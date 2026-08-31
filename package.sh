#!/usr/bin/env bash
# 打包为符合部署规范的服务包 ZIP。
#
# 规范要点（逐条对应）：
#   - 生命周期脚本必须直接位于 ZIP 根目录，不得多套一层项目目录
#   - ZIP 内路径必须用 / 分隔，不得含绝对路径、盘符、冒号或 ..
#   - 不得包含密钥、缓存、日志、无关构建目录
#   - 单包 <= 500 MiB，条目数 <= 10000，解压后 <= 2 GiB
#
# ── PACKAGE_INCLUDE_SECRETS：上面那条"不得包含密钥"的显式例外 ──────────────
#
# ⚠️ **这个开关打出来的包不能上传到大赛平台。** 2026-08-25 从平台截图里
# 抄回来的《作品规范》多了一条本仓库此前没有的硬约束：
#
#   「涉及公司内部数据须脱敏处理，不得上传敏感凭证、密钥或未授权材料。」
#
# 而方式 B 的部署路径**就是把 ZIP 上传到平台**，平台随后还会联合安全部门
# 对部署结果做漏洞扫描。所以这个开关只服务一种场景：**你自己 ssh 上去手动
# 部署**（官方 FAQ Q3 明确允许）。
#
# 2026-08-31 之前，开了这个开关的产物文件名会带 `-WITH-SECRETS-DO-NOT-UPLOAD`
# 后缀，专门防"两种包被拿混"。按明确要求改成了统一文件名（见下面 ZIP_PATH
# 那段注释）——**这条护栏现在只剩终端里的 log 警告**，选文件上传前得自己
# 认得清哪次跑带了这个开关，文件名不再替你把关了。
#
# 默认（不设这个变量）行为跟这条规范写的一样：`.env` 排除在外，
# 部署机靠 start.sh 的 ${RUNTIME_DIR}/env 拿密钥（见 start.sh 那段注释）。
#
# 2026-08-24 加了这个开关：这台机器是公司内部机器，且 `.env` 只应该进
# **本地这个 ZIP**，绝不进 git——ZIP 不提交、不 push，`dist/` 在
# .gitignore 里；CI 从没有 `.env` 的 checkout 打包，这个开关在那边永远
# 不生效，行为跟以前一模一样，不会有真密钥被 CI 的 artifact upload 带出去。
#
# **不做成默认值。** 这是这个仓库一贯的做法（TRANSCRIPT_RETENTION、
# OFFLINE_DEMO 都默认关）：任何一次让密钥离开这台机器的动作，
# 都该是一次显式的、有人按下去的选择，不该是"忘了配置就自动发生"。
#
#   PACKAGE_INCLUDE_SECRETS=1 ./package.sh
#
set -euo pipefail

INCLUDE_SECRETS="${PACKAGE_INCLUDE_SECRETS:-0}"

APP_ID="ai-antifraud-persuasion"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="${APP_DIR}/dist"
STAMP="$(date +%Y%m%d-%H%M%S)"
# 2026-08-31 按要求改回统一命名：不管含不含密钥，文件名都是
# `${APP_ID}-${STAMP}.zip`，不再带 WITH-SECRETS-DO-NOT-UPLOAD 后缀。
# **这样一来文件名不再能替你分辨这个包能不能上传**——上传前要靠下面
# 那两条 `log` 警告（终端里看得见）或自己确认这次跑没跑
# PACKAGE_INCLUDE_SECRETS=1，别单看文件名。
ZIP_PATH="${DIST_DIR}/${APP_ID}-${STAMP}.zip"

log()  { echo "[package] $*"; }
fail() { echo "[package][ERROR] $*" >&2; exit 1; }

command -v zip >/dev/null 2>&1 || fail "未找到 zip 命令"
mkdir -p "${DIST_DIR}"

ZIP_INCLUDES=(install.sh start.sh stop.sh requirements.txt app static)

if [ "${INCLUDE_SECRETS}" = "1" ]; then
  [ -f "${APP_DIR}/.env" ] \
    || fail "PACKAGE_INCLUDE_SECRETS=1 但 ${APP_DIR}/.env 不存在，没有东西可以打进去"
  ZIP_INCLUDES+=(.env)
  log "PACKAGE_INCLUDE_SECRETS=1：.env 会被打进这个 ZIP"
  log "这个包只能留在这台机器上部署——绝不能提交进 git、绝不能上传到任何非本机的地方"
elif [ -f "${APP_DIR}/.env" ]; then
  log "检测到 .env（含密钥），已在排除列表中，不会进包（要打进去见本文件顶部 PACKAGE_INCLUDE_SECRETS 那段注释）"
fi

cd "${APP_DIR}"

log "打包 -> ${ZIP_PATH}"
# 在项目根目录内执行 zip，条目路径天然是相对路径，不会带绝对路径或盘符
zip -r -q "${ZIP_PATH}" \
  "${ZIP_INCLUDES[@]}" \
  -x '*.pyc' \
  -x '*__pycache__*' \
  -x '*/.DS_Store' \
  || fail "zip 打包失败"

# ── 打包后自检 ──────────────────────────────────────────────────────────────
log "自检中…"

ENTRIES="$(unzip -Z1 "${ZIP_PATH}" | wc -l | tr -d ' ')"
SIZE_MB="$(du -m "${ZIP_PATH}" | cut -f1)"

# 条目清单只取一次，后面全用 herestring 喂给 grep。
# 不能写成 `unzip -Z1 ... | grep -q`：grep -q 命中即退出，unzip 随后吃到
# SIGPIPE 返回非零，pipefail 把它当成整条流水线失败——匹配越靠前、清单越长
# 越容易触发。install.sh 恰好是第一个条目，于是"检查通过"反而变成打包失败。
ENTRY_LIST="$(unzip -Z1 "${ZIP_PATH}")"

# 生命周期脚本必须在根目录（条目名不含 /）
for f in install.sh start.sh stop.sh; do
  grep -qx "$f" <<< "${ENTRY_LIST}" \
    || fail "生命周期脚本 $f 不在 ZIP 根目录"
done

# 禁止绝对路径 / 上级目录 / 反斜杠
if grep -qE '^/|\.\./|\\' <<< "${ENTRY_LIST}"; then
  fail "ZIP 内存在绝对路径、上级目录或反斜杠"
fi

# 禁止密钥文件混入。**.key / .pem 不管开没开 PACKAGE_INCLUDE_SECRETS 都拦**：
# 这个仓库没有任何理由往包里塞私钥文件，出现了大概率是别的东西泄漏进来，
# 不是这次要的功能，不能被这个开关一起放过。
if grep -qE '\.key$|\.pem$' <<< "${ENTRY_LIST}"; then
  fail "ZIP 内混入了密钥文件"
fi
# .env 只在**没有显式打开开关**时才算意外混入；开了的话它就是故意打进去的，
# 上面已经打过一条日志说清楚了
if [ "${INCLUDE_SECRETS}" != "1" ] && grep -qE '(^|/)\.env$' <<< "${ENTRY_LIST}"; then
  fail "ZIP 内混入了密钥文件"
fi

[ "${ENTRIES}" -le 10000 ] || fail "条目数 ${ENTRIES} 超过 10000 上限"
[ "${SIZE_MB}" -le 500 ]   || fail "包体 ${SIZE_MB}MB 超过 500MiB 上限"

log "自检通过：条目 ${ENTRIES} 个，${SIZE_MB}MB"
log "产物: ${ZIP_PATH}"
if [ "${INCLUDE_SECRETS}" = "1" ]; then
  echo "" >&2
  echo "======================================================================" >&2
  echo "[package][警告] 这个包里有真实密钥（.env）。" >&2
  echo "  能做的：ssh 上目标服务器，自己解压部署（官方 FAQ Q3 允许）。" >&2
  echo "  不能做的：上传到大赛平台的作品部署入口。《作品规范》写着" >&2
  echo "            「不得上传敏感凭证、密钥或未授权材料」，平台还会联合" >&2
  echo "            安全部门做漏洞扫描。" >&2
  echo "  要上传的包：不设 PACKAGE_INCLUDE_SECRETS 再跑一次 ./package.sh，" >&2
  echo "            密钥改走 start.sh 里那条 \${RUNTIME_DIR}/env 通路。" >&2
  echo "======================================================================" >&2
  echo "" >&2
else
  log "这个包不含密钥，可以上传到平台；部署机的密钥请放在 <state>/.${APP_ID}/env"
fi
exit 0
