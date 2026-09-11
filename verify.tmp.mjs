/* 一次性核对：本机排名那一级真的画得出来，配色也没吃整屏的 tone。
   跑完就删。 */
import { connect, evaluate, findChrome, launchChrome, newPage, waitFor } from './tests/e2e/cdp.mjs';
import { startServer } from './tests/e2e/server.mjs';

const server = await startServer();
const chrome = await launchChrome(findChrome());
const cdp = await connect(chrome.wsUrl);
const { call } = await newPage(cdp);

await call('Page.navigate', { url: `${server.base}/` });
await waitFor(call, `document.querySelector('.screen.on')?.id !== 'assignment'`, '开局完成', 25000);

const out = await evaluate(call, `(async () => {
  const review = await import('/static/review.js');
  const state = await import('/static/state.js');
  const sid = 'chen';
  const name = '陈国栋';
  if (!state.SCENE) state.setScene({ id: sid, client: { name } });

  const seed = (list) => localStorage.setItem('af_history_v1', JSON.stringify(list));
  const 局 = (s, trust) => ({ ts: Date.now(), sid: s, clientName: '', kind: 'stalled',
                              trust, rounds: 8, uses: {}, gains: {} });
  state.game.trust = 55;

  const 画 = (kind) => {
    document.querySelectorAll('.probe').forEach((n) => n.remove());
    const host = document.createElement('div');
    host.className = 'review probe';
    host.dataset.tone = 'green';   // 最容易出事的那一档：绿是"明确劝住"专用
    host.innerHTML = '<p class="percentile" id="percentileLine" hidden></p>';
    document.body.appendChild(host);
    review.paintLocalRank(host, kind);
    const el = host.querySelector('#percentileLine');
    const cs = getComputedStyle(el);
    return { hidden: el.hidden, text: el.textContent, cls: el.className,
             color: cs.color, bg: cs.backgroundColor };
  };

  const r = {};
  seed([局(sid, 40)]);                                   r.一局 = 画('stalled');
  seed([局(sid, 40), 局(sid, 71), 局(sid, 55)]);          r.同场景 = 画('stalled');
  seed([局(sid, 40), 局('__other__', 90)]);               r.跨客户 = 画('stalled');
  seed([局(sid, 40), 局(sid, 71)]);                       r.未完成 = 画('unfinished');
  r.客户 = name;

  // 服务端那一级：把 fetch 换掉，喂一份 12 局的分布进去。
  // api.js 的 fetchStats 是调用时才读全局 fetch，所以拦得住。
  const 桶 = new Array(20).fill(0);
  桶[6] = 7;   // 30-34 分：七局在我下面
  桶[11] = 5;  // 55-59 分：我在这一桶里
  window.fetch = async () => ({
    ok: true,
    json: async () => ({ available: true, games: 12, turns: 0, trust_buckets: 桶 }),
  });

  document.querySelectorAll('.probe').forEach((n) => n.remove());
  const host = document.createElement('div');
  host.className = 'review probe';
  host.dataset.tone = 'green';
  host.innerHTML = '<p class="percentile" id="percentileLine" hidden></p>';
  document.body.appendChild(host);
  seed([局(sid, 40), 局(sid, 71)]);
  review.paintLocalRank(host, 'stalled');          // 先垫上本机那一句
  const 垫 = host.querySelector('#percentileLine').textContent;
  await review.paintStats(host, 'stalled');        // 服务端那一份到了，覆盖掉
  const el = host.querySelector('#percentileLine');
  r.服务端 = { 垫, hidden: el.hidden, text: el.textContent, cls: el.className,
               色: getComputedStyle(el).color, 底: getComputedStyle(el).backgroundColor,
               卡上的分母: state.game._percentileSample };
  return r;
})()`);

console.log(JSON.stringify(out, null, 2));

cdp.close();
chrome.kill();
server.stop();
