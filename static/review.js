import { BREACHES, KEYS, MOODS, PENALTIES } from './keys.js';
import { fetchStats } from './api.js';
import { fitCanvas, paintKline, palette } from './chart.js';
import { percentileCopy, trustPercentile } from './stats.js';
import { makeCard, paintHistory } from './history.js';
import { openClientSheet } from './opening.js';
import {
  RESULT_BASIS, SCENE, TONES, breachTurns, endingMeta, game, peerPronoun,
  pressureNote, resultAmount, reviewKind, scoredTurns, startNewClient,
} from './state.js';

export function tag(id) {
  const meta = KEYS[id] || PENALTIES[id] || BREACHES[id];
  const el = document.createElement('span');
  // 红线单独一个类：它在对局中就要比失误更扎眼一点。踩线那一刻的反馈
  // 才教得会人，等到复盘才说，玩家已经忘了自己当时为什么那么讲。
  el.className = 'tag ' + (KEYS[id] ? 'key' : BREACHES[id] ? 'breach' : 'penalty');
  el.textContent = BREACHES[id] ? `合规红线 · ${BREACHES[id].name}` : (meta ? meta.name : id);
  return el;
}

/** 一轮没命中任何东西时，说点有用的。
 *
 * **原来这里只有一句「没使上劲」，八轮里七轮都是它。** 那等于告诉玩家
 * "你错了，但我不打算说错在哪"——而这恰恰是同类产品最被诟病的地方。
 * 我们手上其实有三条信号可以分辨，一条都没用过：
 *
 * · 判成扎根却没命中钥匙 → 他接住了对方的话，但停在那儿没往下问
 * · 一句话短到撑不起一轮 → 「嗯呢」「你先忙」，那不是劝，是应付
 * · 其余 → 确实没往三把钥匙上走
 *
 * 三句都指向下一步该怎么改，而「没使上劲」一句都不指。
 */
export function missNote(t) {
  if (t.grounded) return `接住了${peerPronoun()}的话，但没往下问`;
  if ((t.utterance || '').replace(/\s/g, '').length <= 8) return '太短了，撑不起一轮';
  return '三把钥匙一把都没沾上';
}

export function hitTags(t) {
  const out = t.hits.map(tag);
  // 说早了的「有据告知」不挂"扎根/未扎根"那一条：它根本没被当钥匙算过
  // （判分那边已经换成 bare_assertion），再说一句"效力打折"是在解释一件
  // 没发生的事，只会把玩家的注意力从真正的问题——时机——上引开。
  if (t.mistimedWarning) return out;
  if (t.hits.some((h) => KEYS[h])) {
    const g = document.createElement('span');
    g.className = 'tag';
    g.textContent = t.grounded ? '扎根' : '未扎根，效力打折';
    out.push(g);
  } else if (!t.hits.length) {
    const n = document.createElement('span');
    n.className = 'tag';
    n.textContent = missNote(t);
    out.push(n);
  }
  return out;
}

// ── 复盘 ────────────────────────────────────────────────────

/** 用逐轮数据说一句具体的话。失败的结局也要能说出玩家做对了什么。 */
export function verdictCopy() {
  const kind = reviewKind();
  const best = game.turns.reduce(
    (a, b) => (b.delta > (a ? a.delta : -Infinity) ? b : a), null);
  const parts = [];

  // 代词与那句「他做了什么」都随场景走。原先这五句写死着"他"和"三十万"，
  // 周淑琴那一局读起来就是在讲另一个人的事
  const TA = peerPronoun();
  const meta = endingMeta(kind);
  if (kind === 'persuaded') parts.push(`你用了 ${game.turns.length} 轮把${TA}劝了回来。`);
  else if (kind === 'intercepted') parts.push(`${meta.savedCopy}。`);
  else if (kind === 'stalled') parts.push(`${TA}把这事推后了——你争到的是时间，不是${TA}的决定。`);
  else if (kind === 'blacklisted') parts.push(`第 ${game.turns.length} 轮，${TA}把你拉黑了——这条线断了，你再也看不到${TA}的动静。`);
  else parts.push(`${meta.savedCopy}。`);

  if (best && best.delta > 0) {
    // **"最有力的一句"这句不在这儿说了。** 上面「本局关键转折」整块讲的
    // 就是它：同一个轮次、同一句原话、同一个涨幅，紧挨着说两遍。
    // 这一段只留它独有的东西——追问窗口接没接住、最后一公里。
    //
    // **这句话以前是无条件说的**——只要没劝住就说"你没乘胜追击"，
    // 哪怕玩家每一次窗口都追上了。追问窗口这个机制本来就是为了让这句话
    // 变成真的（TECH-DESIGN §3.4），现在才真正接上线：错过了才说。
    const missed = game.turns.filter((x) => x.windowResult === 'missed');
    const caught = game.turns.filter((x) => x.windowResult === 'hit');
    if (missed.length) {
      // 报的是**开窗**那一轮，不是关窗那一轮：他晃起来是在开窗时，
      // 错过的那一轮只是口子合上的时刻。两个轮次差着两轮，说错了玩家会对不上号
      const opened = game.turns
        .filter((x) => x.windowOpened && x.round < missed[0].round)
        .pop();
      parts.push(
        `你差的不是方向——第 <em>${(opened || missed[0]).round}</em> 轮${TA}晃到了新的一档，` +
        `接下来两轮本来是机会，你没接住，第 ${missed[0].round} 轮${TA}重新硬了回去。`);
    } else if (caught.length && kind !== 'persuaded') {
      parts.push(
        `${TA}松动的那几次你都接住了（第 ${caught.map((x) => x.round).join('、')} 轮），` +
        `差的是最后一公里——越接近松口，同一句话推动${TA}的幅度越小。`);
    }
  } else {
    parts.push(`全场没有一句真正推动过${TA}。下一局试着先听懂${TA}在怕什么、在图什么，再往下问。`);
  }
  return parts.join('');
}

export function openReview() {
  const usedPenalties = Object.keys(PENALTIES).filter(
    (p) => game.turns.some((t) => t.hits.includes(p)));
  const best = game.turns.reduce(
    (a, b) => (b.delta > (a ? a.delta : -Infinity) ? b : a), null);
  const kind = reviewKind();
  const meta = endingMeta(kind);

  const view = document.createElement('section');
  view.className = 'review';
  view.setAttribute('role', 'dialog');
  view.setAttribute('aria-label', '复盘');
  view.innerHTML = `
    <div class="osbar"><span>14:56</span><span class="os-signals" aria-hidden="true"><i></i><i></i><i></i><b></b></span></div>
    <header class="appbar">
      <button class="brandmark review-mark" id="reviewBack" type="button" aria-label="返回对话">复</button>
      <strong>本局复盘</strong>
      <span class="quiet" id="reviewScene"></span>
    </header>
    <div class="review-body">
      <!-- **第一屏只留五块，这是硬上限**（PIVOT-C-END §3.2）。
           它自己引用的那条研究就是这么说的：PUBG 后置屏 N=12 用户研究里，
           玩家不会为了看懂一个指标跑去别处找解释，看不懂就直接忽略。
           复盘一度长到十三块，等于把十二块也一起废掉。
           转向 C 端之后这条从"设计瑕疵"变成"能不能用"——异动干预的对象
           是一个正要转账的普通用户，不是一个来受训的投顾。

           留下的五块，每一块都在回答一个他此刻真的会问的问题：
             1 结算卡（summary + scoreline 是同一块）—— 他最后按没按下确认
             2 同一句话，换个时候说 —— 全作品唯一竞品没有的判据，不能砍
             3 他没说出口的那些 —— "原来我也一样"的转折点
             4 现实里还差这几步 —— C 端干预的落点
             5 分享卡

           **砍掉的一块都没删，全部收进下面那个折叠。** 逐轮三段账、
           七把钥匙条、本机记录对认真的人仍然有价值，只是不该挡在第一屏。 -->
      <section class="summary ${kind}">
        <span class="result-kicker">本局结果 · <b class="tierpill"></b></span>
        <h2 class="result-title"></h2>
        <div class="savedamt num"></div>
        <div class="savedcap"></div>
        <div class="saved"></div>
        <!-- **这一行是这一屏上最重要的一句话。**
             上面那个大数来自模拟信任度跨没跨过一条线，而不是任何一个
             交易系统的回传。不写清楚，玩家和评委都会读成"这个产品
             挽回了三十万"——那是这个作品最容易被误读、也最不该被误读的一处。
             判分闭集里七把钥匙全是问法，没有任何一把是"拦住这笔转账"。 -->
        <p class="result-basis" id="resultBasis"></p>
      </section>

      <div class="scoreline">
        <div class="metric"><b class="num" id="sTrust"></b><span>最终信任</span></div>
        <div class="metric"><b class="num" id="sRounds"></b><span>使用轮次</span></div>
      </div>

      <div class="group" id="contrastWrap" hidden>
        <div class="group-title">同一句话，换个时候说</div>
        <div class="panel" id="contrastBox"></div>
      </div>

      <div class="group">
        <div class="group-title" id="phoneTitle">这一局你没看见的</div>
        <div class="panel" id="phoneList"></div>
      </div>

      <!-- 这一局练的只是"怎么开口"。现实里把话说通之后还有一串动作，
           而这套判分闭集里一个都没有（七把钥匙全是问法）。
           不列出来，玩家会以为劝住了就完事了——那是这个作品最容易
           教错的一件事，而它只要一张清单就能说清。
           **不计分、不参与任何统计**：它是"接下来还要做什么"，不是成绩。 -->
      <div class="group">
        <div class="group-title">话说通了，现实里还差这几步</div>
        <div class="panel">
          <ol class="disposal">
            <li><b>先把这一笔停下</b><span>让客户当场取消转账或撤回；已提交的联系银行尝试拦截。</span></li>
            <li><b>核验收款方</b><span>对公户还是个人卡、户名对不对得上他说的那家机构。</span></li>
            <li><b>拨 96110 / 110</b><span>陪着他打，别让他挂了电话自己再想。</span></li>
            <li><b>在系统里留痕并上报</b><span>疑似诈骗按本机构流程报备，别只留在聊天记录里。</span></li>
            <li><b>约下一次回访</b><span>骗子还会再找他。这一通电话不是终点。</span></li>
          </ol>
          <p class="empty">这几步本局不计分，也不该由一次对话代替。真实处置流程以你所在机构的规定为准。</p>
        </div>
      </div>

      <div class="group">
        <div class="group-title">带走这一局</div>
        <div class="actions"><button id="makeCard">生成分享卡</button></div>
        <div id="cardWrap"></div>
      </div>

      <div class="result-actions">
        <details class="review-details" id="reviewDetails">
          <!-- 折叠标题由 JS 改写：踩了合规红线的话要在标题上说出来。
               收进折叠不等于藏起来——那一块是"你自己有没有事"，
               把它闷在第二屏里，是这次砍块唯一可能砍出的实质损失。 -->
          <summary id="reviewMore">详细复盘</summary>
          <div class="evidence-content">
            <p class="percentile" id="percentileLine">排行样本积累中 · 暂不显示百分位</p>

            <section class="turning-point" id="turningPoint">
              <b id="turningTitle"></b>
              <blockquote id="turningQuote"></blockquote>
              <p id="turningNote"></p>
            </section>

            <!-- 本局复盘：一句结论 + 三行可扫的要点（做对了 / 可改进 / 合规）。
                 三行**从真实对局里算**，不按结局写死——写死的话，两个玩家用完全
                 不同的打法拿到同一档结局，复盘会说一模一样的话，那是占位符不是复盘。
                 判据见 reviewRows()。 -->
            <section class="result-review">
              <h3>本局复盘</h3>
              <p class="copy"></p>
              <div class="review-rows" id="reviewRows"></div>
            </section>

            <div class="group">
              <div class="group-title">逐轮信任曲线</div>
              <div class="panel">
                <canvas id="chart"></canvas>
                <p class="legend">一根蜡烛一轮，红涨绿跌。细横线是判分，实体端点是计入流失后的信任度。</p>
              </div>
            </div>

            <div class="group" id="breachWrap" hidden>
              <div class="group-title">合规红线</div>
              <div class="panel" id="breachList"></div>
            </div>

            <div class="group">
              <div class="group-title">关键方法 · 你这一局用得怎么样</div>
              <div class="panel" id="keyBars"></div>
            </div>

            <div class="group">
              <div class="group-title">逐轮 · 挣了多少 / 掉了多少 / 剩多少</div>
              <div class="panel" id="roundsList"></div>
            </div>

            <div class="group" id="penaltyWrap" hidden>
              <div class="group-title">踩过的坑</div>
              <div class="panel" id="penaltyList"></div>
            </div>

            <div class="group" id="statsWrap" hidden>
              <div class="group-title">别人打成什么样</div>
              <div class="panel" id="statsList"></div>
            </div>

            <div class="group" id="historyWrap" hidden>
              <div class="group-title">这台设备上打过的局</div>
              <div class="panel statstrip" id="historyStrip"></div>
              <div class="panel" id="historyList"></div>
              <div class="panel keyrow" id="historyWeak" hidden>
                <div class="keyhead"><span class="keyname"></span><span class="keynum"></span></div>
                <div class="keynote"></div>
              </div>
            </div>

            <p class="howscored">上面每一分都是<b>程序按规则表算的，不是模型打的</b>：同一把钥匙在客户不同的情绪档位上值不同的分，这张规则表是纯函数、可以离线重跑。<b>但"命中了哪一把"仍由模型判定</b>，那一步不是确定性的——所以别把这里的分当成一个精确刻度，它是画像，不是成绩单。</p>
          </div>
        </details>
        <button class="restart-action" id="restart" type="button">开始一位新客户</button>
        <!-- 「换一位」只在演示态出现（catalog 为空时隐藏）。
             它和上面那个按钮的区别是**挑不挑**：随机来一位是默认，
             想再打一遍某个场景不该靠反复重开去抽。 -->
        <button class="ghost-action wide" id="pickAnother" type="button"
                aria-haspopup="dialog" hidden>换一位客户</button>
      </div>
    </div>`;

  document.querySelector('.app-shell').appendChild(view);
  // 四色语义（设计稿的 data-tone）：绿只给明确劝住，金＝争取到时间或减少了
  // 损失，锈红＝资金损失或联系中断，拖住给中性灰——钱没动但风险一点没解除，
  // 用绿会把"还没输"说成"赢了"。**绿不做装饰色**：它在这一屏只有一个意思，
  // 滥用一次，下次玩家就不信它了。一屏一个 --accent，百分位块直接吃它。
  view.dataset.tone = TONES[kind] || 'plain';
  const result = resultAmount(kind);
  view.querySelector('#reviewScene').textContent = SCENE ? SCENE.name : '风险劝阻';
  view.querySelector('.summary .tierpill').textContent = meta.tier;
  view.querySelector('.summary .result-title').textContent = meta.title;
  view.querySelector('.summary .savedamt').textContent = result.value;
  view.querySelector('.summary .savedcap').textContent = result.label;
  view.querySelector('.summary .saved').textContent = meta.savedCopy;
  view.querySelector('#resultBasis').textContent = RESULT_BASIS[kind] || RESULT_BASIS.default;
  view.querySelector('.result-review .copy').innerHTML = verdictCopy();
  paintReviewRows(view);

  const turning = view.querySelector('#turningPoint');
  if (best && best.delta > 0) {
    turning.querySelector('#turningTitle').textContent =
      `真正改变${peerPronoun()}的是第 ${best.round} 轮`;
    turning.querySelector('#turningQuote').textContent = `“${best.utterance}”`;
    turning.querySelector('#turningNote').textContent =
      `第 ${best.round} 轮让信任上升 ${best.delta} 分，这是本局最有力的一句。`;
  } else {
    turning.querySelector('#turningTitle').textContent = '本局尚未出现关键转折';
    turning.querySelector('#turningQuote').textContent = '这一局没有一句真正推动客户。';
    turning.querySelector('#turningNote').textContent =
      '下一局先确认客户在怕什么、相信什么，再尝试给出判断。';
  }

  if (kind === 'blacklisted') {
    const rank = view.querySelector('#percentileLine');
    rank.hidden = false;
    rank.classList.add('unranked');
    rank.textContent = '不参与排行 · 联系中断属于提前出局';
  }

  view.querySelector('#sTrust').textContent = String(game.trust);
  view.querySelector('#sRounds').textContent = String(game.turns.length);

  paintBreaches(view);
  paintPhone(view);

  // 折叠里装了什么，标题上要说出来，否则它就是一个没人点的按钮。
  // 合规红线单独点名：那一块回答的是"你自己有没有事"，与输赢无关，
  // 是这一屏上唯一一个**不点开就可能真的错过**的东西。
  const breaches = breachTurns().length;
  view.querySelector('#reviewMore').textContent = breaches
    ? `详细复盘 · 含 ${breaches} 次合规红线`
    : '详细复盘 · 逐轮证据与能力画像';

  const list = view.querySelector('#roundsList');
  game.turns.forEach((t) => {
    const row = document.createElement('div');
    row.className = 'round' + (best && t.round === best.round && t.delta > 0 ? ' pivot' : '');

    const no = document.createElement('div');
    no.className = 'no num';
    no.textContent = String(t.round);

    const body = document.createElement('div');
    const said = document.createElement('div');
    said.className = 'said';
    said.textContent = t.utterance;
    const tags = document.createElement('div');
    tags.className = 'tags';
    hitTags(t).forEach((x) => tags.appendChild(x));
    body.append(said, tags);
    const when = timingNote(t);
    if (when) body.append(when);
    const win = windowNote(t);
    if (win) body.append(win);
    // **这一轮没判成。** 必须说出来，而且必须说清是我们的问题：
    // 不说的话，这一行看起来就是"你说了句什么也没命中的话"——
    // 一次网关抖动被永久记成了玩家的失误，而且他连申辩的依据都没有。
    // 上面 scoredTurns() 已经把它踢出了所有比率的分母，这里补上告知。
    if (t.degraded) {
      const bad = document.createElement('div');
      bad.className = 'window';
      bad.textContent = '这一轮没能判分（系统原因），已不计入上面的能力画像';
      body.append(bad);
    }
    // 催单那一轮多掉 3 分。不写出来，玩家只会看到流失那一列忽然从 -2 变成 -5，
    // 而 title 提示在手机上根本摸不到
    if (t.pressure) {
      const push = document.createElement('div');
      push.className = 'window';
      push.textContent = `${pressureNote()} · 多掉 3 分`;
      body.append(push);
    }

    row.append(no, body, scoreLedger(t));
    list.appendChild(row);
  });

  paintContrast(view);
  paintKeyBars(view);

  if (usedPenalties.length) {
    view.querySelector('#penaltyWrap').hidden = false;
    const box = view.querySelector('#penaltyList');
    usedPenalties.forEach((p) => box.appendChild(tipCard(PENALTIES[p])));
  }

  paintStats(view, kind);
  paintHistory(view, kind);

  view.querySelector('#restart').onclick = () => startNewClient();
  const another = view.querySelector('#pickAnother');
  another.hidden = !(game.catalog && game.catalog.length > 1);
  another.onclick = openClientSheet;
  view.querySelector('#makeCard').onclick = () => makeCard(view);
  view.querySelector('#reviewBack').onclick = () => view.remove();
  view.querySelector('#reviewDetails').addEventListener('toggle', (event) => {
    if (event.currentTarget.open) paintChart(view);
  }, { once: true });
}

/** 合规红线那一节。
 *
 * **这是全作品唯一一处与输赢无关的判定。** 其余每一个数字都在回答
 * "你有没有劝住他"；这一节回答的是另一个问题——"你这么劝，自己有没有事"。
 * 两个问题的答案可以同时是"很好"和"很糟"，而那正是它要教的东西。
 *
 * 逐条列出踩线的那一轮和原话：合规这件事上，"你说过这句话"本身就是证据，
 * 泛泛说一句"注意合规"没有任何用。
 */
/** 本局复盘三行：做对了 / 可改进 / 合规。
 *
 * **设计稿里这三行是按结局写死的**（每个客户 × 每种结局一套文案）。
 * 照抄会出一个问题：两个玩家用完全不同的打法拿到同一档结局，复盘会对他们
 * 说一模一样的话——那是占位符，不是复盘。所以这里全部从这一局的真实数据算：
 *
 *   做对了 —— 真实挣分最多的那把钥匙（一把都没挣到就说实话）
 *   可改进 —— 优先说踩过的坑；没踩坑才说这一局压根没用过的那几把
 *   合规   —— 直接读 `breachTurns()`，与那张合规红线卡同源，不另算一套口径
 */
export function reviewRows() {
  const gains = {};
  game.turns.forEach((t) => t.hits.forEach((h) => {
    if (KEYS[h]) gains[h] = (gains[h] || 0) + Math.max(0, t.delta);
  }));
  const bestKey = Object.keys(gains)
    .filter((k) => gains[k] > 0)
    .sort((a, b) => gains[b] - gains[a])[0];

  const didRight = bestKey
    ? `${KEYS[bestKey].name}用得最见效，这一局靠它挣了 ${gains[bestKey]} 分`
    : '识别到了资产异动，并且开口问了';

  const penalties = Object.keys(PENALTIES).filter(
    (p) => game.turns.some((t) => t.hits.includes(p)));
  const unused = Object.keys(KEYS).filter(
    (k) => !game.turns.some((t) => t.hits.includes(k)));
  const canImprove = penalties.length
    ? `${penalties.map((p) => PENALTIES[p].name).join('、')}顶高了${peerPronoun()}的防备`
    : unused.length
      ? `这一局没用过${unused.slice(0, 2).map((k) => KEYS[k].name).join('、')}`
      : `七把钥匙都用到了，下一局试着更早读准${peerPronoun()}的档位`;

  const breaches = breachTurns();
  const compliance = breaches.length
    ? `踩了 ${breaches.length} 次执业红线，这在现实里是要被问话的`
    : '未触碰敏感信息红线';

  return [
    ['做对了', didRight, false],
    ['可改进', canImprove, false],
    ['合规', compliance, breaches.length > 0],
  ];
}

export function paintReviewRows(view) {
  const box = view.querySelector('#reviewRows');
  if (!box) return;
  reviewRows().forEach(([label, text, bad]) => {
    const row = document.createElement('div');
    row.className = 'review-row';
    const dt = document.createElement('span');
    dt.textContent = label;
    const dd = document.createElement('b');
    if (bad) dd.className = 'redline';
    dd.textContent = text;
    row.append(dt, dd);
    box.appendChild(row);
  });
}

export function paintBreaches(view) {
  const turns = breachTurns();
  if (!turns.length) return;

  view.querySelector('#breachWrap').hidden = false;
  const box = view.querySelector('#breachList');

  const lead = document.createElement('p');
  lead.className = 'breach-lead';
  const kinds = new Set();
  turns.forEach((t) => t.hits.forEach((h) => h in BREACHES && kinds.add(h)));
  // 最后一句原来是「本会话按监管要求存档，开局那条提示不是布景」。
  // 两处都不成立：这个练习没有任何监管存档义务，而开局那条提示 8-22 已改。
  // 换成一句关于**真实展业**的陈述——教学分量一分没少，断言没了。
  lead.innerHTML =
    `这一局你有 <b>${turns.length}</b> 轮踩到了执业红线。` +
    `<br>这一节和你劝没劝住他无关 —— <b>这样的对话记录，合规那边看到是要问话的</b>。` +
    `真实展业中，投顾与客户的沟通是要留痕的。`;
  box.appendChild(lead);

  turns.forEach((t) => {
    const row = document.createElement('div');
    row.className = 'breachrow';

    const head = document.createElement('div');
    head.className = 'breachhead';
    t.hits.filter((h) => h in BREACHES).forEach((h) => {
      const b = document.createElement('span');
      b.className = 'breachname';
      b.textContent = BREACHES[h].name;
      head.appendChild(b);
    });
    const no = document.createElement('span');
    no.className = 'breachno';
    no.textContent = `第 ${t.round} 轮`;
    head.appendChild(no);

    const said = document.createElement('div');
    said.className = 'breachsaid';
    said.textContent = `你说：${t.utterance}`;

    row.append(head, said);
    t.hits.filter((h) => h in BREACHES).forEach((h) => {
      const why = document.createElement('div');
      why.className = 'breachwhy';
      why.textContent = BREACHES[h].tip;
      row.appendChild(why);
    });
    box.appendChild(row);
  });
}

// ── 揭晓：老陈的手机 ────────────────────────────────────────
//
// 这五条会话原本是首页那一屏。移过来的理由写在 index.html 的注释里，
// 一句话说完：**首页把这一局要挖的东西全给了，而挖它就是玩法。**
//
// 判据是"**他**说没说过"，不是"你猜没猜到"——所以匹配跑在老陈的台词上。
// 这与扎根的判据同源（引用的是他说过的话），也更诚实：玩家从别处知道
// 老陈有个女儿不算本事，把他问到主动提起小雨才算。
//
// 银行短信那条单独标"这一条你有"：系统推给李经理的资产异动预警就是它的另一面。
// 它在这张清单上的作用是让"你开局只有这一条"看得见。
// 揭晓清单**随场景下发**（app/scenario.py 的 PhoneRow）。它原先是写死在这里的
// 五条老陈的会话——第二个场景一来，那五条就成了另一个人的手机。
//
// `test` 是正则源码字符串，在这儿编译。判据仍然是"**他**说没说过"，
// 匹配跑在劝阻对象的台词上，与扎根同源。
export function phoneRows() {
  // **数据源是 ending，不是 SCENE**（P1-14）。这几条是这一局要挖的答案，
  // 开局响应里不再带着它们——否则打开开发者工具就能提前看完。
  //
  // 主动结束那一档没有 ending，因此这里是空的，而那是对的：他没打完，
  // 揭晓清单里"哪几条他跟你说了"本来就无从谈起（`paintPhone` 会把整节收掉）。
  const rows = (game.ending && game.ending.phone) || [];
  return rows.map((r) => ({
    cls: r.avatar,
    text: r.initial,
    name: r.name,
    time: r.time,
    line: r.line,
    clue: r.clue,
    own: r.own,
    test: r.test ? new RegExp(r.test) : null,
  }));
}

/** 他这一局说过的话，按时间序，不做任何过滤——掐掉一个字都可能让
 *  某条线索误判成"他没提"。
 */
export function hisSpeech() {
  const out = [{ round: 0, text: game.opening || '' }];
  game.turns.forEach((t) =>
    out.push({ round: t.round, text: t.reply || (t.lines || []).join('') }));
  ((game.ending && game.ending.lines) || []).forEach((line) =>
    out.push({ round: null, text: line }));
  return out.filter((x) => x.text);
}

/** 揭晓老陈的手机：哪几条他跟你说了，哪几条到最后你也不知道。 */
export function paintPhone(view) {
  const box = view.querySelector('#phoneList');
  const speech = hisSpeech();
  const rows = phoneRows();
  // 没走到结局就没有揭晓清单（主动结束那一档）。**整节收掉，不留一个空面板**——
  // 一个写着标题却什么都没有的区块，读起来像是坏了
  if (!rows.length) {
    const wrap = box.closest('.group');
    if (wrap) wrap.hidden = true;
    return;
  }
  const diggable = rows.filter((x) => !x.own);
  const TA = peerPronoun();
  // 标题也随场景走。原先写死「老陈的手机」，周淑琴那一局照样这么印
  view.querySelector('#phoneTitle').textContent =
    `这一局你没看见的 · ${SCENE ? SCENE.client.name : '客户'}的手机`;
  let got = 0;

  const lead = document.createElement('p');
  lead.className = 'phone-lead';
  box.appendChild(lead);

  rows.forEach((item) => {
    const found = item.own
      ? null
      : speech.find((s) => item.test.test(s.text));
    if (found) got += 1;

    const row = document.createElement('li');
    row.className = 'chatrow' + (!item.own && !found ? ' missed' : '');

    const av = document.createElement('span');
    av.className = 'avatar ' + item.cls;
    av.setAttribute('aria-hidden', 'true');
    av.textContent = item.text;
    // 群头像是四格拼的，没有文字
    if (item.cls === 'av-group') av.innerHTML = '<i></i><i></i><i></i><i></i>';

    const state = document.createElement('span');
    if (item.own) {
      state.className = 'phone-state own';
      state.textContent = '你已有';
    } else if (found) {
      state.className = 'phone-state yes';
      // 开场白算第 0 轮，结局台词没有轮次——都得说人话，不能印出「第 0 轮」
      state.textContent =
        found.round === 0 ? `${TA}开口就说了`
          : found.round === null ? '最后才说'
            : `${TA}第 ${found.round} 轮说了`;
    } else {
      state.className = 'phone-state no';
      state.textContent = `${TA}没提`;
    }

    const main = document.createElement('span');
    main.className = 'row-main';
    main.innerHTML = `
      <span class="row-top">
        <span class="row-name"></span>
        <span class="row-time"></span>
      </span>
      <span class="row-bottom">
        <span class="row-preview"></span>
      </span>
      <span class="phone-clue"></span>`;
    main.querySelector('.row-name').textContent = item.name;
    main.querySelector('.row-time').textContent = item.time;
    main.querySelector('.row-preview').textContent = item.line;
    main.querySelector('.phone-clue').textContent = item.clue;
    main.querySelector('.row-bottom').appendChild(state);

    row.append(av, main);
    box.appendChild(row);
  });

  // 数字之外还要有一句判断，否则「4 条里问到 1 条」玩家不知道算好算差
  const verdict =
    got === diggable.length ? `${TA}几乎什么都跟你说了 —— 这一局你是真把${TA}问开了。`
      // 不能写死"十二轮下来"：被拉黑与提前结束的局根本没打满
      : got === 0 ? `一条都没有 —— 打完这一局，你对${TA}的了解和开局时一样多。`
        : got * 2 >= diggable.length ? `问出一半以上，${TA}对你是有话说的。`
          : '大部分到最后你也不知道 —— 而不知道这些，你就只能泛泛地劝。';
  lead.innerHTML =
    `开局你手上只有账户那一侧的一条预警。${TA}手机上还有这些，` +
    `<b>${diggable.length}</b> 条里${TA}跟你说到了 <b class="got">${got}</b> 条。<br>${verdict}`;
}

/** 三把钥匙的维度条。
 *
 * **这一节是补一条真实的产品差距。** 同类的 AI 陪练产品人人都有"维度评分"，
 * 而我们原先只有一张「没用上的钥匙」清单——判分引擎明明按三把钥匙 × 四个
 * 情绪档位算了一整局，玩家却看不到自己在每一把上站在哪儿。
 *
 * 每一行给三样东西，都从这一局的真实数据里算，不编：
 *   · 用了几次（钝化就是从这儿来的：同一把钥匙用第三次只剩一半效力）
 *   · 这把钥匙一共挣了多少分
 *   · 时机对不对（效力倍率的均值——**这是全作品唯一一处竞品没有的判据**）
 *
 * 没用过的那几把不留白，给出它的一句话说明——那正是下一局该试的东西。
 */
/** 「同一句话，换个时候说」。
 *
 *  **这是全作品唯一一条竞品没有的判据第一次被摆到玩家眼前。**
 *  金融 AI 陪练判"回答准确度、语速、音量"，销售 roleplay 判"方法论有没有
 *  照做"，没有一家判"你这一招用得是不是时候"——而效力矩阵判的正是这个。
 *  在这个块之前，它只出现在两个地方：逐轮里一闪而过的 `1.3×`，
 *  以及技术文档。**玩家自己打一局是看不见它的**，评审也一样。
 *
 *  矩阵随结局事件下发（app/engine.py），不在前端抄一份（踩过的坑 7）：
 *  调参数时蒙特卡洛会重跑，抄一份的话页面上这个数不会跟着动。
 *
 *  挑哪一轮来讲，按"教学价值"排，不按分数排：
 *    1. 说早了的「有据告知」——同一句话早说是失误、晚说是钥匙，
 *       这是本条判据的**极端形态**，有就一定讲它
 *    2. 否则挑"实际效力与这一把最高效力差得最多"的那一轮——
 *       他做对了动作、只是挑错了时候，那正是这一节要教的
 *  两样都没有（比如一把钥匙都没用上），整块不出现——不留一个空壳。 */
export function paintContrast(view) {
  const box = view.querySelector('#contrastBox');
  const eff = game.ending && game.ending.efficacy;
  if (!eff || !box) return;

  const named = (m) => MOODS[m] || m;
  const rowOf = (k) => eff[k] || null;
  // 一把钥匙在四档里的最高与最低。用来说"同一句话差多少倍"
  const peak = (row) => Object.entries(row).sort((a, b) => b[1] - a[1])[0];
  const floor = (row) => Object.entries(row).sort((a, b) => a[1] - b[1])[0];

  let head = '';
  let body = '';
  let quote = '';

  const mistimed = scoredTurns().find((t) => t.mistimedWarning);
  if (mistimed && rowOf('informed_warning')) {
    const row = rowOf('informed_warning');
    const [bestMood, bestVal] = peak(row);
    quote = mistimed.utterance;
    head = `第 ${mistimed.round} 轮 · 你给了依据，也下了判断`;
    body =
      `他当时${named(mistimed.judgedMood)}，这句话算的是<b>空口断言</b>——`
      + `跟他女儿昨天说的那四个字落在同一个地方。`
      + `<br>同一句话，等他${named(bestMood)}再说，它是这一局分值最高的一把（${bestVal}×）。`
      + `<br><b>不是这句话错了，是时候错了。</b>`;
  } else {
    // 找"动作对、时候不对"差得最远的那一轮
    let worst = null;
    scoredTurns().forEach((t) => {
      if (t.efficacy == null) return;
      const k = (t.hits || []).find((h) => rowOf(h));
      if (!k) return;
      const [bestMood, bestVal] = peak(rowOf(k));
      const gap = bestVal - t.efficacy;
      if (!worst || gap > worst.gap) worst = { t, k, bestMood, bestVal, gap };
    });
    if (!worst || worst.gap <= 0.15) return;   // 差得不明显就不硬讲
    const { t, k, bestMood, bestVal } = worst;
    quote = t.utterance;
    head = `第 ${t.round} 轮 · ${KEYS[k] ? KEYS[k].name : k}`;
    body =
      `他当时${named(t.judgedMood)}，这一招值 ${t.efficacy}×。`
      + `<br>同一句话，等他${named(bestMood)}再说，值 ${bestVal}×。`
      + `<br><b>动作是对的，差的是时候。</b>`;
  }

  if (!head) return;

  const h = document.createElement('div');
  h.className = 'contrast-head';
  h.textContent = head;
  box.appendChild(h);

  if (quote) {
    const q = document.createElement('div');
    q.className = 'contrast-quote';
    q.textContent = quote;
    box.appendChild(q);
  }

  const p = document.createElement('p');
  p.className = 'contrast-body';
  p.innerHTML = body;
  box.appendChild(p);

  // 一句话把这个块为什么存在说清楚。竞品判"说得标不标准"，我们判"用得是不是
  // 时候"——这句是全作品的论点，值得在它唯一被看见的地方写出来
  const foot = document.createElement('p');
  foot.className = 'empty';
  foot.textContent =
    '这一局判的从来不是你说得标不标准，是你用得是不是时候。'
    + '同一把钥匙，在他四种情绪下值的分不一样——换个客户，这张表还会翻过来。';
  box.appendChild(foot);

  view.querySelector('#contrastWrap').hidden = false;
}

export function paintKeyBars(view) {
  const box = view.querySelector('#keyBars');
  const scored = scoredTurns();
  const rows = Object.keys(KEYS).map((k) => {
    const turns = scored.filter((t) => t.hits.includes(k));
    const gained = turns.reduce((s, t) => s + Math.max(0, t.delta), 0);
    const effs = turns.map((t) => t.efficacy).filter((e) => e != null);
    return {
      k,
      used: turns.length,
      gained,
      eff: effs.length ? effs.reduce((a, b) => a + b, 0) / effs.length : null,
      rounds: turns.map((t) => t.round),
    };
  });
  // 挣得多的排前面。没用过的沉底——它们是"下一局试试这个"，不是成绩
  rows.sort((a, b) => b.gained - a.gained || b.used - a.used);

  const top = Math.max(1, ...rows.map((r) => r.gained));

  rows.forEach((r) => {
    const meta = KEYS[r.k];
    const row = document.createElement('div');
    row.className = 'keyrow' + (r.used ? '' : ' unused');

    const head = document.createElement('div');
    head.className = 'keyhead';
    const name = document.createElement('span');
    name.className = 'keyname';
    name.textContent = meta.name;
    const num = document.createElement('span');
    num.className = 'keynum num';
    num.textContent = r.used ? `+${r.gained}` : '没用过';
    head.append(name, num);

    const bar = document.createElement('div');
    bar.className = 'keybar';
    const fill = document.createElement('i');
    // 条长按"这一局挣得最多的那把"归一。绝对分值没有天花板可言，
    // 拿一个想象出来的满分去除，条会长期趴在左边，什么也说明不了
    fill.style.width = r.gained ? Math.max(6, (r.gained / top) * 100) + '%' : '0%';
    bar.appendChild(fill);

    const note = document.createElement('div');
    note.className = 'keynote';
    if (!r.used) {
      note.textContent = meta.tip;
    } else {
      const parts = [`第 ${r.rounds.join('、')} 轮用了 ${r.used} 次`];
      if (r.eff != null) {
        const e = r.eff.toFixed(1);
        parts.push(
          r.eff >= 1.2 ? `平均 ${e}× · 时机抓得准`
            : r.eff < 0.7 ? `平均 ${e}× · 用早了，这一招得等他晃起来`
            : `平均 ${e}×`);
      }
      if (r.used >= 3) parts.push('用到第三次效力只剩一半');
      note.textContent = parts.join(' · ');
    }

    row.append(head, bar, note);
    box.appendChild(row);
  });
}

/** 一轮的账，摊开写。
 *
 * **原来这里只有两个数：判分和结果。** 于是复盘上是这样的：
 * 第 3 轮 23 分 → 第 4 轮 +18 → 39。玩家会去算 23+18=41，对不上，
 * 然后合理地怀疑判分是不是错了。差的那 2 分是每轮无条件的信任流失，
 * 而它在界面上一个字都没有——机制建了三个月，玩家一次都没见过它。
 *
 * 现在三段都写出来：挣了多少 / 掉了多少 / 剩多少。掉的那一列还要说明白
 * 为什么是 5 不是 2（王老师催单）——那正是「他背后有人在往回拉」这件事
 * 唯一一次在数字上现身。
 */
export function scoreLedger(t) {
  const box = document.createElement('div');
  box.className = 'ledger num';

  const gain = document.createElement('span');
  gain.className = 'gain' + (t.delta > 0 ? ' up' : t.delta < 0 ? ' down' : '');
  gain.textContent = t.delta > 0 ? `+${t.delta}` : String(t.delta);
  box.appendChild(gain);

  // drift 是后加的字段。老令牌里没有它，别让一局旧对局在复盘上崩掉
  if (typeof t.drift === 'number' && t.drift) {
    const loss = document.createElement('span');
    loss.className = 'loss';
    loss.textContent = String(t.drift);
    loss.title = t.pressure ? `每轮的信任流失，加上这一轮${pressureNote()}` : '每轮的信任流失';
    box.appendChild(loss);
  }
  if (t.released) {
    const pool = document.createElement('span');
    pool.className = 'pool';
    pool.textContent = `+${t.released}`;
    pool.title = '开局封顶时存进蓄势池的分，这一轮释放出来了';
    box.appendChild(pool);
  }

  const after = document.createElement('span');
  after.className = 'after';
  after.textContent = String(t.trust);
  box.appendChild(after);
  return box;
}

/** 「别人打成什么样」。
 *
 * `app/stats.py` 与 `/api/stats` 早就写好了，前端一次都没调用过——同类产品
 * 公认的四个失败模式里，"量的是对话，不是能不能上岗"就漏在这儿。一个人打完
 * 只知道自己这一局，接上之后才知道自己站在哪：你这一档占多少、三把钥匙里
 * 哪一把是大家都想不起来用的。
 *
 * **全程是旁路**：Redis 没配、接口挂了、还没人玩过，这一节整块不出现，
 * 复盘的其余部分一个字都不受影响。绝不让一个纯展示功能拖累最后一屏。
 */

export async function paintStats(view, kind) {
  // 被拉黑是提前出局，不属于四档完整对局，不拿 0 分和完成对局比较。
  // 主动结束同理，而且更明显：他自己按下的结束，拿它跟打满的人比排名，
  // 量出来的是"谁打得久"，不是"谁劝得好"。
  if (kind === 'blacklisted' || kind === 'unfinished') return;
  let data;
  try {
    // sid 与口径这两个参数一个都不能省，为什么见 api.js 的 `fetchStats`
    data = await fetchStats(
      SCENE ? SCENE.id : '',
      game.origin ? game.origin.source || '' : '');
  } catch (e) {
    return;
  }
  if (!data || !data.available) return;

  // 百分位单独判定，不跟下面 `!data.turns` 的早退共用一个门槛——
  // 它现在挂在结算卡那块里，不属于「别人打成什么样」这一节，
  // 后者没数据不该连累前者也不出现。
  const pct = trustPercentile(data.trust_buckets, game.trust);
  if (pct != null) {
    game._percentile = pct;
    const line = view.querySelector('#percentileLine');
    line.hidden = false;
    line.textContent = percentileCopy(pct);
    // 底色不在这儿判：它跟着整屏的 data-tone 走（style.css 的 .review[data-tone]），
    // 与上面那个大数同色。一屏一个语义色，玩家不用学第二套规则。
  }

  if (!data.turns) return;

  const rows = [];
  // 分母只数真正判成了的轮次（见 scoredTurns）——降级轮的 hits 是空的，
  // 留在分母里等于让网关抖动去稀释玩家的命中率
  const scored = scoredTurns();
  const mine = scored.length;

  const share = data.endings && data.endings[kind];
  if (share && share.share) {
    rows.push([
      `你落在「${endingMeta(kind).title}」`,
      `${Math.round(share.share * 100)}% 的人也停在这一档`,
    ]);
  }

  // 分母两边都是"轮"：全局用总轮数，你这局用你打过的轮数。
  // 换成局数会得出大于 1 的"命中率"——一局里同一把钥匙可以用很多次。
  Object.keys(KEYS).forEach((k) => {
    const g = data.keys && data.keys[k];
    if (!g) return;
    const 我的 = scored.filter((t) => t.hits.includes(k)).length;
    rows.push([
      KEYS[k].name,
      `大家 ${Math.round(g.rate * 100)}% 的发言用到 · 你 ${mine ? Math.round((我的 / mine) * 100) : 0}%`,
    ]);
  });

  // 小雨那条铺垫不在这儿重复说：上面「踩过的坑」里的空口断言卡片已经写了
  // 「这四个字他这三个月听了无数遍」。这一行只负责给一个数。
  const 空口 = data.keys && data.keys.bare_assertion;
  if (空口 && 空口.rate) {
    rows.push([
      '空口说“这是诈骗”',
      `${Math.round(空口.rate * 100)}% 的发言还在这么劝`,
    ]);
  }

  if (!rows.length) return;

  const box = view.querySelector('#statsList');
  rows.forEach(([name, note]) => {
    const row = document.createElement('div');
    row.className = 'statrow';
    const n = document.createElement('div');
    n.className = 'statname';
    n.textContent = name;
    const v = document.createElement('div');
    v.className = 'statnote';
    v.textContent = note;
    row.append(n, v);
    box.appendChild(row);
  });

  const foot = document.createElement('p');
  foot.className = 'empty';
  foot.textContent = `统计自 ${data.games} 局、${data.turns} 轮对话`;
  box.appendChild(foot);

  view.querySelector('#statsWrap').hidden = false;
}

/** 时机那一行。**这是全作品唯一一处竞品没有的判据**——金融 AI 陪练判
 *  "回答准确度、语速、音量"，销售 roleplay 判"方法论有没有照做"，
 *  没有一家判"你这一招用得是不是时候"。而效力矩阵判的正是这个：
 *  同一把钥匙，他戒备时用和他动摇时用，差四倍多。
 *
 *  倍率由服务端下发（判分参数只此一份，前端不抄那张表）。
 */
export function timingNote(t) {
  // 说早了的「有据告知」优先说这一句。判分那边已经把它换成了 bare_assertion，
  // 玩家只看到一个"空口断言"标签会完全对不上号——**他明明给了依据**。
  // 错的不是那句话，是时候，而这正是全作品唯一在判的东西的极端形态：
  // 同一句话，早说是失误，晚说是钥匙。
  if (t.mistimedWarning) {
    const el = document.createElement('div');
    el.className = 'timing bad';
    el.textContent =
      `他当时${MOODS[t.judgedMood] || t.judgedMood} · `
      + '这句话本身没问题，你给了依据 —— 但他还没到听得进去的时候，'
      + '这时候说，跟「这是诈骗」四个字在他耳朵里是一样的';
    return el;
  }
  if (t.efficacy == null) return null;
  const mood = MOODS[t.judgedMood] || t.judgedMood;
  const el = document.createElement('div');
  el.className = 'timing' + (t.efficacy >= 1.2 ? ' good' : t.efficacy < 0.7 ? ' bad' : '');
  const verdict = t.efficacy >= 1.2 ? ' 正是时候'
    : t.efficacy < 0.7 ? ' 用早了，这一招得等他晃起来' : '';
  el.textContent = `他当时${mood} · 这一招值 ${t.efficacy}×${verdict}`;
  return el;
}

// 追问窗口那一行。同类产品公认的一个洞是"评分捕捉不到回避行为"——
// 受训者在安全话题上过度展开，一碰到真正的痛点就退回安全区，而评分只看
// 话术完整度，反而给了正面激励。追问窗口捕捉的正是它：他晃起来的那两轮
// 你有没有跟上。以前这条只在引擎里算，玩家一眼都看不到。
export const WINDOW_NOTE = {
  hit: { text: '他正晃着，你接住了 · 这一招额外加成', cls: 'good' },
  missed: { text: '两轮的口子空掉了，他重新硬了回去 · 扣 6 分', cls: 'bad' },
  open: { text: '口子还开着，还剩一轮', cls: '' },
};

export function windowNote(t) {
  const parts = [];
  const w = WINDOW_NOTE[t.windowResult];
  if (w) parts.push([w.text, w.cls]);
  if (t.windowOpened) parts.push(['他第一次晃到这一档 · 接下来两轮是机会', 'good']);
  if (!parts.length) return null;
  const box = document.createElement('div');
  parts.forEach(([text, cls]) => {
    const el = document.createElement('div');
    el.className = 'window' + (cls ? ' ' + cls : '');
    el.textContent = text;
    box.appendChild(el);
  });
  return box;
}

/** 画那根 K 线。
 *
 * **不赌布局时序。** 原来这里直接读 `clientWidth` 就画，注释写着"读它本身
 * 就会强制布局，不必等下一帧"——那句话在这条路径上不成立：`.review` 刚被
 * 塞进 body、还带着一个 slidein 动画，实测量到的是 **16px**（`width:100%`
 * 落在一个还没有宽度的容器上，再加左右各 8px 内边距）。画布按 16px 画完，
 * CSS 再把它拉到全宽，K 线就糊成一片。
 *
 * 而且它是**间歇性**的——同一段代码有时对有时不对，这种视觉 bug 最坏：
 * 平时看不出来，路演当天糊给评审看。
 *
 * 所以量到不合理的宽度就等下一帧重画，而不是相信第一次的结果。
 */
export function paintChart(view) {
  const chart = view.querySelector('#chart');
  if (!chart) return;
  const w = chart.clientWidth;
  const h = chart.clientHeight;
  // 一根蜡烛最窄 3px、最多 12 轮，再加边距——比这还窄只可能是没量准
  if (w < 100 || h < 40) {
    requestAnimationFrame(() => paintChart(view));
    return;
  }
  paintKline(fitCanvas(chart, w, h), { x: 10, y: 12, w: w - 20, h: h - 26 }, {
    turns: game.turns,
    threshold: game.threshold,
    slots: Math.max(game.turns.length, 6),
    palette: palette(),
    axis: true,
    // 第 1 轮判分之前的信任度，就是开局那个数。不另外记一份：
    // 记两份迟早走散，而这一份本来就在逐轮数据里
    start: game.turns.length ? game.turns[0].before : null,
  });
}

export function tipCard(meta) {
  const card = document.createElement('div');
  card.className = 'tip';
  const title = document.createElement('div');
  title.className = 'title';
  title.textContent = meta.name;
  const p = document.createElement('p');
  p.textContent = meta.tip;
  card.append(title, p);
  return card;
}
