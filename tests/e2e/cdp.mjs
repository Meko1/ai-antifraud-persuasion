/* 一个够用就好的 Chrome DevTools Protocol 客户端。
 *
 * ## 为什么是手写的
 *
 * 这个仓库**没有 node_modules，也不该有**（harness.mjs 顶部第 1 条）。
 * Playwright / puppeteer 各自会拖进上百个包和一份自己下载的浏览器，
 * 与打包规范（单包体积、条目数、无外网依赖）正面冲突。
 *
 * 而 CDP 本身只要两样东西：一个 HTTP GET 拿调试地址，一个 WebSocket 收发
 * JSON。**Node 22 起 WebSocket 是内建全局**，于是这两样都不用装东西——
 * 整个客户端一百来行，比一份 lockfile 便宜得多。
 *
 * CI 的 node-version 因此从 20 提到了 22（.github/workflows/ci.yml）。
 * 提之前 `node --test` 那条命令还有个 glob 的坑，注释留在 workflow 里。
 *
 * ## 它做得了什么
 *
 * 导航、跑 JS、收控制台消息与网络响应。**没有实现的**：帧分片、二进制帧、
 * 权限弹窗、多标签页。够 tests/e2e/browser.test.mjs 用，不够就别在这儿加——
 * 那时候要谈的是"值不值得引一个依赖"，不是"再补两百行"。
 */

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

/** 找一个能用的 Chrome。找不到返回 null，由调用方决定是跳过还是报错。 */
export function findChrome() {
  const fromEnv = process.env.CHROME_PATH;
  if (fromEnv && fs.existsSync(fromEnv)) return fromEnv;
  const 候选 = [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/snap/bin/chromium',
  ];
  return 候选.find((p) => fs.existsSync(p)) || null;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 轮询到能拿到东西为止。CI 上冷启动比本机慢得多，超时给宽一点。 */
async function 等到(fn, { timeout = 30000, every = 100, what = '' } = {}) {
  const 截止 = Date.now() + timeout;
  let last;
  while (Date.now() < 截止) {
    try {
      const v = await fn();
      if (v) return v;
    } catch (e) { last = e; }
    await sleep(every);
  }
  // `what` 允许是个函数：超时那一刻才去算"还差哪几个"，比一句干巴巴的
  // "超时了"有用得多
  const 说明 = typeof what === 'function' ? what() : what;
  throw new Error(`等 ${说明} 超时（${timeout}ms）${last ? `：${last.message}` : ''}`);
}

/** 起一个 headless Chrome，返回 { kill, wsUrl }。 */
export async function launchChrome(chromePath) {
  const 用户目录 = fs.mkdtempSync(path.join(os.tmpdir(), 'aap-e2e-'));
  const proc = spawn(chromePath, [
    '--headless=new',
    '--remote-debugging-port=0',       // 让系统挑端口，免得并发跑时撞上
    `--user-data-dir=${用户目录}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-gpu',
    '--disable-dev-shm-usage',
    // CI 的容器里没有 user namespace，不给这个起不来。
    // 本机也给：这个浏览器只加载我们自己 127.0.0.1 上的一页
    '--no-sandbox',
    '--window-size=390,844',           // 这是一个手机上的作品，就按手机量
    'about:blank',
  ], { stdio: ['ignore', 'pipe', 'pipe'] });

  // 调试端口从 stderr 的 "DevTools listening on ws://..." 那一行读
  let stderr = '';
  proc.stderr.on('data', (b) => { stderr += b.toString(); });

  const wsUrl = await 等到(() => {
    const m = stderr.match(/ws:\/\/[^\s]+/);
    return m ? m[0] : null;
  }, { what: 'Chrome 的调试端口', timeout: 20000 });

  return {
    wsUrl,
    kill() {
      proc.kill('SIGKILL');
      try { fs.rmSync(用户目录, { recursive: true, force: true }); } catch { /* 清不掉就算了 */ }
    },
  };
}

/** 连上去。返回一个 { send, on, close }。 */
export async function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true });
    ws.addEventListener('error', () => reject(new Error('连不上 CDP')), { once: true });
  });

  let seq = 0;
  const 等回复 = new Map();
  const 监听 = new Map();

  ws.addEventListener('message', (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id != null) {
      const 它 = 等回复.get(msg.id);
      if (!它) return;
      等回复.delete(msg.id);
      if (msg.error) 它.reject(new Error(`${msg.error.message}（${JSON.stringify(msg.error)}）`));
      else 它.resolve(msg.result);
      return;
    }
    for (const fn of 监听.get(msg.method) || []) fn(msg.params);
  });

  return {
    /** 发一条命令，等它的回复。`sessionId` 走的是同一条连接。 */
    send(method, params = {}, sessionId) {
      const id = (seq += 1);
      return new Promise((resolve, reject) => {
        等回复.set(id, { resolve, reject });
        ws.send(JSON.stringify(sessionId ? { id, method, params, sessionId } : { id, method, params }));
        setTimeout(() => {
          if (等回复.delete(id)) reject(new Error(`${method} 没有回复（30s）`));
        }, 30000);
      });
    },
    on(event, fn) {
      if (!监听.has(event)) 监听.set(event, []);
      监听.get(event).push(fn);
    },
    close() { try { ws.close(); } catch { /* 已经断了 */ } },
  };
}

/** 开一个标签页并 attach，返回带 sessionId 的调用器。 */
export async function newPage(cdp) {
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  const call = (method, params) => cdp.send(method, params, sessionId);
  await call('Page.enable');
  await call('Runtime.enable');
  await call('Log.enable');
  await call('Network.enable');
  return { call, sessionId, targetId };
}

/** 在页面里跑一段表达式，把结果取回来。抛出的异常照原样抛。 */
export async function evaluate(call, expression) {
  const r = await call('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (r.exceptionDetails) {
    const d = r.exceptionDetails;
    throw new Error(d.exception?.description || d.text || '页面里抛异常了');
  }
  return r.result.value;
}

/** 反复跑同一个表达式，等它为真。用来等界面到某个状态。 */
export function waitFor(call, expression, what, timeout = 15000) {
  return 等到(() => evaluate(call, expression), { what, timeout });
}

export { 等到 as waitUntil };
