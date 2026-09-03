/* 跟服务端说话的全部出口。**别处不许再出现 fetch。**
 *
 * 收在一处的理由不是整洁：路径、请求体形状、以及"哪一条绝不能 await"
 * 这类规矩，散在四个视图里就会各自漂移一份。 */

// ── SSE over POST ───────────────────────────────────────────
// EventSource 只能发 GET，而每轮要把令牌 POST 上去，因此自己解一遍协议。

export async function* readEvents(resp) {
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let cut;
    while ((cut = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let name = null;
      let data = '';
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) name = line.slice(6).trim();
        else if (line.startsWith('data:')) data += line.slice(5).trim();
      }
      if (name) yield { name, data: data ? JSON.parse(data) : {} };
    }
  }
}

/** 开一局。`picked` 是「换一位客户」挑中的那一位，空字符串表示随机。 */
export async function startGame(picked) {
  const resp = await fetch('api/game/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    // **不挑的时候也发一个空对象**：走的是和真实接入完全相同的那条
    // 服务端逻辑（app/trigger.py），生产那条路径才不会长年没人走过
    body: JSON.stringify(picked ? { sid: picked } : {}),
  });
  if (!resp.ok) throw new Error(`start failed: ${resp.status}`);
  return resp.json();
}

/** 打一轮。返回原始 Response——事件要交给 readEvents 逐个读出来，
 *  在这儿 await 完就没有"逐句下发"这回事了。 */
export function postTurn(token, utterance) {
  return fetch('api/game/turn', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token, utterance }),
  });
}

/** 「别人打成什么样」那一节的数据。
 *
 * **必须带 sid**：信任度分布按场景分开存（app/stats.py `key_trust`）。
 * 场景之间难度并不一样（balance_sim 实测 expert 胜率 45.8%~54.0%），
 * 混在一起比，量出来的是"你抽到的场景是难是易"，不是你打得好不好。
 * 不带这个参数会读到空的默认桶，百分位于是永远不显示——**静默失效**，
 * 页面上看不出任何异常，所以这个参数不能省。
 * **口径也要带**：演示态的局跟演示态的比，真实接入的局跟真实接入的比
 * （app/stats.py `data_mode`）。混着比，那句"高于同场景 X%"就不成立了。 */
export async function fetchStats(sid, source) {
  const resp = await fetch(
    `api/stats?sid=${encodeURIComponent(sid)}&source=${encodeURIComponent(source)}`);
  return resp.json();
}

/** 把退出这件事告诉服务端。**绝不能挡住退出本身**——所以不 await、不看结果。
 *
 *  令牌由调用方传进来，这个模块因此不认识 `game`：它只知道怎么跟服务端说话。 */
export function reportExit(token, reason) {
  if (!token) return;
  try {
    const body = JSON.stringify({ token, reason });
    // sendBeacon 在页面正在卸载时也送得出去，fetch 不一定。
    // 拿不到（老浏览器）就退回 fetch + keepalive
    if (navigator.sendBeacon) {
      navigator.sendBeacon('api/game/exit', new Blob([body], { type: 'application/json' }));
    } else {
      fetch('api/game/exit', {
        method: 'POST', body, keepalive: true,
        headers: { 'Content-Type': 'application/json' },
      }).catch(() => {});
    }
  } catch { /* 埋点失败就失败，用户该走还是走 */ }
}
