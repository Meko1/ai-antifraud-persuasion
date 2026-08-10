#!/usr/bin/env bash
# 打包为符合部署规范的服务包 ZIP。
#
# 规范要点（逐条对应）：
#   - 生命周期脚本必须直接位于 ZIP 根目录，不得多套一层项目目录
#   - ZIP 内路径必须用 / 分隔，不得含绝对路径、盘符、冒号或 ..
#   - 不得包含密钥、缓存、日志、无关构建目录
#   - 单包 <= 500 MiB，条目数 <= 10000，解压后 <= 2 GiB
set -euo pipefail

APP_ID="ai-antifraud-persuasion"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="${APP_DIR}/dist"
STAMP="$(date +%Y%m%d-%H%M%S)"
ZIP_PATH="${DIST_DIR}/${APP_ID}-${STAMP}.zip"

log()  { echo "[package] $*"; }
fail() { echo "[package][ERROR] $*" >&2; exit 1; }

command -v zip >/dev/null 2>&1 || fail "未找到 zip 命令"
mkdir -p "${DIST_DIR}"

if [ -f "${APP_DIR}/.env" ]; then
  log "检测到 .env（含密钥），已在排除列表中，不会进包"
fi

cd "${APP_DIR}"

log "打包 -> ${ZIP_PATH}"
# 在项目根目录内执行 zip，条目路径天然是相对路径，不会带绝对路径或盘符
zip -r -q "${ZIP_PATH}" \
  install.sh start.sh stop.sh \
  requirements.txt \
  app static \
  -x '*.pyc' \
  -x '*__pycache__*' \
  -x '*/.DS_Store' \
  || fail "zip 打包失败"

# ── 打包后自检 ──────────────────────────────────────────────────────────────
log "自检中…"

ENTRIES="$(unzip -Z1 "${ZIP_PATH}" | wc -l | tr -d ' ')"
SIZE_MB="$(du -m "${ZIP_PATH}" | cut -f1)"

# 生命周期脚本必须在根目录（条目名不含 /）
for f in install.sh start.sh stop.sh; do
  unzip -Z1 "${ZIP_PATH}" | grep -qx "$f" \
    || fail "生命周期脚本 $f 不在 ZIP 根目录"
done

# 禁止绝对路径 / 上级目录 / 反斜杠
if unzip -Z1 "${ZIP_PATH}" | grep -qE '^/|\.\./|\\'; then
  fail "ZIP 内存在绝对路径、上级目录或反斜杠"
fi

# 禁止密钥文件混入
if unzip -Z1 "${ZIP_PATH}" | grep -qE '(^|/)\.env$|\.key$|\.pem$'; then
  fail "ZIP 内混入了密钥文件"
fi

[ "${ENTRIES}" -le 10000 ] || fail "条目数 ${ENTRIES} 超过 10000 上限"
[ "${SIZE_MB}" -le 500 ]   || fail "包体 ${SIZE_MB}MB 超过 500MiB 上限"

log "自检通过：条目 ${ENTRIES} 个，${SIZE_MB}MB"
log "产物: ${ZIP_PATH}"
exit 0
