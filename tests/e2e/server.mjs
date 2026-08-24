/* 给 E2E 起一个真的服务：真的 FastAPI、真的静态目录、真的响应头。
 *
 * **走离线演示态**（OFFLINE_DEMO=true）：分类换成关键词规则、台词取自
 * 本地语料，**判分一格都不打折**（app/offline.py）。于是这套测试
 * 不需要任何 API key，也不会因为网关抖一下就红——而它要验的东西
 * （模块图加载得起来、SSE 逐句到、复盘画得出来）一样都不少。
 */

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const REPO = path.join(HERE, '..', '..');

/** 找一个装了依赖的 python。
 *
 *  CI 上依赖装在 PATH 里那个 python 上，本机多半在 `.venv`。
 *  **往上找而不是只看当前目录**：在 git worktree 里干活时，`.venv` 在主仓，
 *  worktree 自己没有一份。找不到就交给 PATH，让它自己去报缺哪个包——
 *  那比这里猜一个更有用。 */
export function findPython() {
  const fromEnv = process.env.E2E_PYTHON;
  if (fromEnv && fs.existsSync(fromEnv)) return fromEnv;
  let dir = REPO;
  for (let i = 0; i < 6; i += 1) {
    const p = path.join(dir, '.venv', 'bin', 'python');
    if (fs.existsSync(p)) return p;
    const 上 = path.dirname(dir);
    if (上 === dir) break;
    dir = 上;
  }
  return 'python3';
}

/** 要一个没人用的端口。**不写死 21818**：本机上多半正开着一个。 */
function 空闲端口() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.on('error', reject);
    s.listen(0, '127.0.0.1', () => {
      const { port } = s.address();
      s.close(() => resolve(port));
    });
  });
}

/** 起服务，等它 /healthz 通了再返回。 */
export async function startServer() {
  const python = findPython();
  const port = await 空闲端口();
  const proc = spawn(python, [
    '-m', 'uvicorn', 'app.main:app',
    '--host', '127.0.0.1', '--port', String(port),
    '--log-level', 'warning',
  ], {
    cwd: REPO,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env,
      OFFLINE_DEMO: 'true',
      STATE_SIGNING_SECRET: 'e2e-only-not-a-real-secret',
      PYTHONUNBUFFERED: '1',
    },
  });

  let 日志 = '';
  proc.stdout.on('data', (b) => { 日志 += b.toString(); });
  proc.stderr.on('data', (b) => { 日志 += b.toString(); });

  const base = `http://127.0.0.1:${port}`;
  const 截止 = Date.now() + 45000;
  for (;;) {
    if (proc.exitCode != null) {
      throw new Error(`服务起不来（退出码 ${proc.exitCode}）：\n${日志}`);
    }
    try {
      const r = await fetch(`${base}/healthz`);
      if (r.ok) break;
    } catch { /* 还没起来 */ }
    if (Date.now() > 截止) throw new Error(`等 /healthz 超时：\n${日志}`);
    await new Promise((r) => setTimeout(r, 200));
  }

  return {
    base,
    port,
    日志: () => 日志,
    stop() { proc.kill('SIGTERM'); },
  };
}
