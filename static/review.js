import { BREACHES, KEYS, MOODS, PENALTIES } from './keys.js';
import { fetchStats } from './api.js';
import { contrastFacts, contrastTimeline, timingClause } from './contrast.js';
import { fitCanvas, paintKline, palette } from './chart.js';
import {
  canCompareEndings, canCompareKeys, percentileCopy, trustPercentile,
} from './stats.js';
import { makeCard, paintHistory, tierRankNote } from './history.js';
import { openClientSheet, transferSeen } from './opening.js';
import { $, showScreen, thread } from './dom.js';
import { syncSend } from './chat.js';
// control.js 也 import 了本文件的 openReview。这条环在 ESM 下是安全的：
// 两边都只在运行时调对方的函数，模块顶层谁都不碰对方的导出。
import { syncEarlyReview } from './control.js';
import {
  RESULT_BASIS, SCENE, TONES, breachTurns, endingMeta, game, peerPronoun,
  pressureNote, resultAmount, reviewKind, saveGame, scoredTurns, startNewClient,
  withTa,
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
 * · 其余 → 确实没往钥匙上走
 *
 * 三句都指向下一步该怎么改，而「没使上劲」一句都不指。
 */
export function missNote(t) {
  if (t.grounded) return `接住了${peerPronoun()}的话，但没往下问`;
  if ((t.utterance || '').replace(/\s/g, '').length <= 8) return '太短了，撑不起一轮';
  return '钥匙一把都没沾上';
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
  else {
    parts.push(`${meta.savedCopy}。`);
    // **同样落进「转账」，戒备与烦躁是两件不同的事。**
    // 阶梯自己声称量的是"他最后有多信你"（CONTEXT.md「结局」），
    // 却把这两档压成了同一句话——这里只把那条信息说回来。
    //
    // **不改分档、不改金额、不改任何门槛**：钱照样是全转走的，
    // 这一档仍然是最低一档。2026-08-25 评估过把烦躁档划进「拖住」
    // （average 27.5%→54.9%），被否掉了：他还烦躁着就说"你争到了时间"，
    // 那是拿一句不成立的话去换一个好看的分布。
    parts.push(game.mood === 'guarded'
      ? `${TA}从头到尾防着你，${game.turns.length} 轮没让你真正靠近。`
      : `${TA}一直在跟你说话，只是始终没有松口。`);
  }

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
    // **不说"下一局"**（2026-08-30）：他只打这一局（POSITIONING「干预只有
    // 一次机会」）。许诺一个不会发生的下一次，是训练器定位的语气残留。
    //
    // **2026-09-03 补第二句，只补这一句，另外四个分支一个字不动。**
    // ROLESafe（docs/EVIDENCE.md §五）的质性发现里，Helper 角色最扎的一条
    // 负面是劝不动时的失落——被试原话是"没达成的那种落差挺大的"。论文给的
    // 建议是难度分层，**那条不采纳**：在那篇里劝不动是学习体验的损耗，
    // 在这里"他劝不动，会自己想到自己"就是干预机制本身，降难度是拆发动机。
    //
    // 真正该借的是别的：另外四个分支都已经把"你差在哪"说得很具体，
    // **具体本身就是解药**——只有这一个分支给不出具体，因为他确实一句都没命中。
    // 于是补一句，把"我很失败"改写成"这件事本来就难"，不安慰、不给下一次、
    // 不印任何会腐烂的数字（判分参数不进前端，见踩过的坑 7）。
    parts.push(`全场没有一句真正推动过${TA}。缺的是最前面那一步——先听懂${TA}在怕什么、在图什么，再往下问。`);
    parts.push(`说不动一个已经做完决定的人，本来就是这件事最常见的结果。`);
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
  // 这一整张模板里凡是指代客户的地方都用它。**跨行模板同样受那条规矩管**——
  // 底下那份投顾处置清单当初就是靠"藏在几十行 HTML 里"躲过了第一版的
  // 静态检查（它只逐行匹配单行的反引号），四条里三条写死着「他」。
  const TA = peerPronoun();

  const view = document.createElement('section');
  view.className = 'review';
  view.setAttribute('role', 'dialog');
  view.setAttribute('aria-label', '复盘');
  view.innerHTML = `
    <div class="osbar"><span>14:56</span><span class="os-signals" aria-hidden="true"><i></i><i></i><i></i><b></b></span></div>
    <!-- 吉祥物 2026-09-05 从「复」旁边那一整块讲评（图+气泡）收进这颗返回键——
         气泡文案删掉，只留形象，**不再单独占一行**。它仍然只出现在复盘这一处
         （不进聊天屏，进去老陈就变成机器人了），只是从"讲评"降级成"署名"。

         **PNG 不退回 SVG，不用 inline onerror**：素材不在时前者是一次 404，
         E2E 有一条"全程零控制台报错"会因此变红；后者撞 CSP 的
         script-src 'self'，内联事件处理器被拦，两条 2026-09-04 都栽过。

         注意：这段注释在模板字符串里，一个反引号都不能写（同日栽过一次）。 -->
    <header class="appbar">
      <button class="brandmark review-mark" id="reviewBack" type="button" aria-label="返回对话">
        <img src="static/assets/miaoxiang-mascot.png" alt="" width="20" height="26">
      </button>
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
             4 回到你自己那一笔 —— **冷开场那个环在这里合上**，C 端干预的落点
             5 分享卡

           **砍掉的一块都没删，全部收进下面那个折叠。** 逐轮三段账、
           七把钥匙条、本机记录对认真的人仍然有价值，只是不该挡在第一屏。 -->
      <section class="summary ${kind}">
        <!-- .tierrank 说的是这一档在阶梯上的位置（history.js 的 tierRankNote）。
             「拖住」这个词自己说不清是好是坏，而 CONTEXT.md 说结局是一道阶梯——
             那就得把阶梯画出来，不能只亮一格。不是胜负判定，是位置。

             **这一段注释里不许出现反引号。** 整张模板是一个模板字符串，
             注释里一个反引号就把它提前闭合，从那里往下全是语法错误
             （2026-08-31 当场栽过一次，前端 36 条测试整组红）。 -->
        <span class="result-kicker">本局结果 · <b class="tierpill"></b><i class="tierrank"></i></span>
        <h2 class="result-title"></h2>
        <div class="savedamt num"></div>
        <div class="savedcap"></div>
        <div class="saved"></div>
        <!-- **这一行是这一屏上最重要的一句话。**
             上面那个大数来自模拟信任度跨没跨过一条线，而不是任何一个
             交易系统的回传。不写清楚，玩家和评委都会读成"这个产品
             挽回了三十万"——那是这个作品最容易被误读、也最不该被误读的一处。
             判分闭集里七把钥匙全是"怎么开口"，没有任何一把是"拦住这笔转账"。 -->
        <p class="result-basis" id="resultBasis"></p>
      </section>

      <!-- ── 2026-09-06：时机对照上移，两个数字下沉 ──────────────────────
           **这一段注释里不许出现反引号**，整张模板是一个模板字符串
           （上面 .summary 那段已经写过一次，这次改动当场又栽了一次）。

           POSITIONING「成功标准 · 对用户」的落地要求是一句原话：
           **第一屏是他最后按没按下确认，紧接着是「同一句话，换个时候说」。**
           而在此之前，这两样中间隔着 ¥0、免责那一段、和这条 scoreline。
           实测（390×844，转账那一档）：折叠线在 844，时机对照那块顶在
           **520**——**62% 的第一屏花在论点开始之前**，而它的最后一行正好被
           折叠线切掉，可探索版整块在线下。
           素材脚本自己写着「它在第一屏底下，不滚过去截不到」。

           **scoreline 不是被贬低，是归位。** 最终信任与使用轮次是事后量出来
           的参照，和上面那个金额是同一类东西；它们该跟在论点后面，
           不该挡在论点前面。它上下本来就是细线，做一条分隔性数据带，
           摆在「同一句话」与「你没看见的」之间同样成立。

           **块数没变**：scoreline 不带 group / summary 类，两处数块的测试
           （review.test.mjs 与 browser.test.mjs）都不数它。 -->
      <div class="group" id="contrastWrap" hidden>
        <div class="group-title">同一句话，换个时候说</div>
        <div class="panel" id="contrastBox"></div>
        <!-- 可探索版（A1，2026-08-31）：上面那块只讲一条教学价值最高的证据，
             这里把这一局里其余能对照的轮次也摆出来，点哪一轮看哪一轮。
             contrastTimeline() 拿不到矩阵时返回空数组，整块不出现——
             与上面那块空壳不留是同一条规矩。 -->
        <div class="panel contrast-explorer" id="contrastExplorer" hidden>
          <div class="contrast-pills" id="contrastPills" hidden></div>
          <p class="contrast-cap" id="contrastCap" aria-live="polite"></p>
          <div class="contrast-bars" id="contrastBars"></div>
        </div>
      </div>

      <div class="scoreline">
        <div class="metric"><b class="num" id="sTrust"></b><span id="sTrustCap">最终信任</span></div>
        <div class="metric"><b class="num" id="sRounds"></b><span>使用轮次</span></div>
      </div>

      <div class="group">
        <div class="group-title" id="phoneTitle">这一局你没看见的</div>
        <div class="panel" id="phoneList"></div>
      </div>

      <!-- ── 回到你自己那一笔（2026-08-30 新增，第一屏第 4 块）─────────────
           **这一块合的是冷开场开的那个环。**
           冷开场让玩家自己签一笔、被拦下、然后「坐到对面」——而在此之前，
           复盘从头到尾没有一处回到那一笔（全仓 grep 零命中）。
           环开了没合上，"角色对调"就只完成了一半：他替别人想了三分钟，
           没有一秒被请回自己身上。POSITIONING「成功标准·对用户」要的那几秒
           犹豫，正是在这里发生，不在结算卡上。

           **它顶掉的是原来那块「话说通了，现实里还差这几步」**，不是删掉——
           那五条整块搬进了下面的折叠。搬家的理由不是它不重要，是它**人称错了**：
           「让客户当场取消」「陪着他打」「按本机构流程报备」「约下一次回访」，
           五条没有一条是一个 C 端用户能对自己做的。它是投顾侧的教学，
           在这个作品里仍然成立（对局中玩家确实在扮投顾），
           所以留在折叠里给认真的人看，不占那个只有五块的第一屏。

           **两条不许改回去**：
           · 引用的是**玩家自己说过的话**，不是产品替他写的金句。
             他劝别人时说得出口的话，回到自己身上才有分量。
           · 下面四条动作**全部是他一个人就能做的**。凡是需要"客户""系统"
             "本机构"的，都属于上面那块，不属于这里。 -->
      <div class="group">
        <div class="group-title">回到你自己那一笔</div>
        <div class="panel">
          <p class="mirror-lead" id="mirrorLead"></p>
          <ol class="mirror-lines" id="mirrorLines"></ol>
          <p class="mirror-turn">这几件事，<b>不用等任何人来劝，你自己就能做</b>：</p>
          <ol class="disposal">
            <li><b>先不按那个确认</b><span><b>催你现在就按</b>的，本身就是最该起疑的那句话。</span></li>
            <li><b>找一个人，把这件事从头讲一遍</b><span>你刚才做的就是这件事——只不过坐在另一边。</span></li>
            <li><b>拨 96110</b><span>国家反诈专线。不确定算不算被骗，也可以打过去问。</span></li>
            <li><b>打你券商 App 里的人工客服</b><span>账户异常、资金去向，他们查得到你查不到的那一半。</span></li>
          </ol>
          <p class="empty">这几步不计分，也不该由一次对话代替。</p>
        </div>
      </div>

      <div class="group">
        <div class="group-title">带走这一局</div>
        <div class="actions"><button id="makeCard">生成分享卡</button></div>
        <div id="cardWrap"></div>
      </div>

      <div class="result-actions">
        <!-- 「未完成」专属，排在这一组的最前面：对刚退出这一局的人来说，
             这是唯一真正紧迫的动作，理由和取色都写在下面 .continue-action
             那条注释里。默认隐藏，只在 kind === 'unfinished' 时解开——
             其余结局是真收场，不该看见这个按钮。 -->
        <button class="continue-action" id="continueChat" type="button" hidden>回去接着打</button>
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

            <!-- 投顾侧的处置清单。2026-08-30 从第一屏搬到这里，理由写在上面
                 那块「回到你自己那一笔」的注释里：**人称是投顾的，不是用户的**。
                 内容一个字没改——对局中玩家确实在扮投顾，这五条对他仍然成立，
                 只是不该占住那个只有五块的第一屏。
                 **不计分、不参与任何统计**：它是"接下来还要做什么"，不是成绩。 -->
            <div class="group">
              <div class="group-title">如果你是${TA}的投顾，话说通之后还差这几步</div>
              <div class="panel">
                <ol class="disposal">
                  <li><b>先把这一笔停下</b><span>让客户当场取消转账或撤回；已提交的联系银行尝试拦截。</span></li>
                  <li><b>核验收款方</b><span>对公户还是个人卡、户名对不对得上${TA}说的那家机构。</span></li>
                  <li><b>拨 96110 / 110</b><span>陪着${TA}打，别让${TA}挂了电话自己再想。</span></li>
                  <li><b>在系统里留痕并上报</b><span>疑似诈骗按本机构流程报备，别只留在聊天记录里。</span></li>
                  <li><b>约下一次回访</b><span>骗子还会再找${TA}。这一通电话不是终点。</span></li>
                </ol>
                <p class="empty">这几步本局不计分，也不该由一次对话代替。真实处置流程以你所在机构的规定为准。</p>
              </div>
            </div>

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
        <!-- 收尾签名。**不放在 howscored 后面**：那一段在 details 里，
             收起来的时候整块看不见，而署名恰恰是收起来也该在的东西。
             用带字的竖版组合标，不是小三叶标——这是全局唯一一处
             "从头到尾是谁在讲评"的落款，它值一个完整的标。
             那句「设计为接入」的分寸不能松：写的是位置，不是妙想的现状。 -->
        <p class="mx-sign mx-sign--foot">
          <img src="static/assets/miaoxiang-lockup.png" alt="妙想" width="43" height="65">
          <span>这一局的讲评由妙想生成<br>设计为接入妙想，非其现有功能</span>
        </p>
      </div>
    </div>`;

  document.querySelector('.app-shell').appendChild(view);
  // **焦点送进这一层。** 它是 `role="dialog"` 盖在对话页上的一屏，而 `#review`
  // 里一个 live region 都没有——不移焦点的话，对屏幕阅读器来说对话页只是
  // "突然不动了"，整个复盘不存在。送标题不送按钮，理由与转账屏那次一样：
  // 焦点即宣告，用户从标题往下读得到全部内容。
  const reviewTitle = view.querySelector('.summary .result-title');
  if (reviewTitle) {
    reviewTitle.setAttribute('tabindex', '-1');
    reviewTitle.focus({ preventScroll: true });
  }
  // 四色语义（设计稿的 data-tone）：绿只给明确劝住，金＝争取到时间或减少了
  // 损失，锈红＝资金损失或联系中断，拖住给中性灰——钱没动但风险一点没解除，
  // 用绿会把"还没输"说成"赢了"。**绿不做装饰色**：它在这一屏只有一个意思，
  // 滥用一次，下次玩家就不信它了。一屏一个 --accent，百分位块直接吃它。
  view.dataset.tone = TONES[kind] || 'plain';
  const result = resultAmount(kind);
  view.querySelector('#reviewScene').textContent = SCENE ? SCENE.name : '风险劝阻';
  view.querySelector('.summary .tierpill').textContent = meta.tier;
  // 主动结束（unfinished）那一档返回空串，整个 `<i>` 就是空的，不占位
  view.querySelector('.summary .tierrank').textContent = tierRankNote(kind);
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
      '顺序反了：先确认客户在怕什么、相信什么，再给判断。';
  }

  if (kind === 'blacklisted') {
    const rank = view.querySelector('#percentileLine');
    rank.hidden = false;
    rank.classList.add('unranked');
    rank.textContent = '不参与排行 · 联系中断属于提前出局';
  }

  view.querySelector('#sTrust').textContent = String(game.trust);
  // **给这个数一把尺子。** 就绪度审计 P1-8：「最终信任 42」没有量纲、没有参照
  // ——满分多少、劝住线在哪，屏幕上一个字都没说，而刻度其实就在同一屏的
  // 分享卡里（K 线上那条「劝住 80」虚线）。信息在旁边，指标格里却不给。
  // 阈值下发不到时就退回原来那句，不编一个数。
  if (game.threshold) {
    view.querySelector('#sTrustCap').textContent = `最终信任 · 劝住线 ${game.threshold}`;
  }
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
  paintMirror(view);
  paintKeyBars(view, kind);

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
  const continueBtn = view.querySelector('#continueChat');
  if (kind === 'unfinished') {
    continueBtn.hidden = false;
    continueBtn.onclick = () => resumeUnfinished(view);
  }
  view.querySelector('#reviewDetails').addEventListener('toggle', (event) => {
    if (event.currentTarget.open) paintChart(view);
  }, { once: true });
}

/** 撤销「就到这儿，看复盘」。
 *
 *  服务端从没消费过这张令牌（`/api/game/exit` 不改变任何对局状态），
 *  `endEarly()`（control.js）只是前端自己把 `game.exited` 标成了 true——
 *  这里原样把它撤回去，聊天就能从掉线的地方接着打。
 *
 *  `game.exited` 必须复位，不能只是把输入框解锁：`resumeGame()`（chat.js）
 *  一刷新页面就看这个标记，不复位的话，玩家接着打的这几轮一遇刷新
 *  又会被强制弹回这张复盘页——刚续上的这几轮当场再废一次。
 */
function resumeUnfinished(view) {
  game.exited = false;
  // 撤销「你结束了这次对话」那句系统提示：继续打下去之后它就不再是实话。
  thread.querySelectorAll('.exit-tip').forEach((el) => el.remove());
  $('composer').hidden = false;
  saveGame();
  view.remove();
  // **必须显式回聊天屏，不能只靠 `view.remove()` 露出底下那一屏。**
  // 在 2026-09-06 之前这条路只有一个入口——`endEarly()` 从聊天屏调起，
  // 底下那一屏本来就是聊天，摘掉遮罩正好落回去。现在工作台上那行
  // 「看这几轮的复盘」是第二个入口，从那儿进来底下是 `#opening`：
  // 摘掉遮罩之后，玩家按了一颗写着「回去接着打」的按钮，
  // 却被送回了工作台——按钮说的话没兑现。
  showScreen('chat');
  syncSend();
  // 抬头那颗「看复盘」跟着回来：他撤回了"就到这儿"，这一局又在打了
  syncEarlyReview();
  $('say').focus();
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
      : `七把钥匙都用到了，差的是更早读准${peerPronoun()}的档位`;

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
    withTa(`<br>这一节和你劝没劝住{ta}无关 —— <b>同样几句话出自持牌投顾之口，合规是要问话的</b>。`) +
    `真实展业中，投顾与客户的沟通全程留痕。`;
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
      why.textContent = withTa(BREACHES[h].tip);
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
  //
  // **「问出一半以上」这句话被实测推翻了**（2026-09-03，tools/cue_coverage.py）。
  // 这套判据认的是"他说没说过"，而它默认了一个从没验证过的前提——**他只有
  // 被问才吐**。拿固定路线量了一遍，前提不成立：`parrot` 那条路线 12 轮只反复
  // 问同一件事（这笔钱的用途），从没提过女儿、没提过平台，而 liu 那一局的
  // 劝阻对象**在 100% 的局里主动把女儿抖了出来**，环球汇盈也是主动说的。
  // 也就是说，一个什么都没问对的玩家照样能拿到 2/4，然后被这句话夸一次。
  //
  // 数字不动（它记在他头上，措辞本来就是"${TA}跟你说到了 N 条"，那是对的），
  // **只把这一句里的"问出"换掉**——判据支持"露出来了几条"，不支持"你问出了几条"。
  // 要真的分清是问出来的还是白送的，得再引一条判据（比如只认玩家扎根那一轮
  // 之后才首次出现的线索），那是另一件事，没做之前不能先把话说出去。
  const verdict =
    got === diggable.length ? `${TA}几乎什么都跟你说了 —— 这一局${TA}是真对你打开了。`
      // 不能写死"十二轮下来"：被拉黑与提前结束的局根本没打满
      : got === 0 ? `一条都没有 —— 打完这一局，你对${TA}的了解和开局时一样多。`
        : got * 2 >= diggable.length ? `一半以上露了出来，${TA}对你是有话说的。`
          : '大部分到最后你也不知道 —— 而不知道这些，你就只能泛泛地劝。';
  /* **「另外」这两个字不是语气词**（2026-08-31）。
   *
   * 原文是「${TA}手机上还有这些，4 条里${TA}跟你说到了 1 条」，而紧挨着的
   * 列表**渲染 5 张卡**——其中一张标着「你已有」（那条银行短信就是开局那条
   * 预警的另一面，不该算进"你问出了几条"）。数字是对的，可读者会先数卡
   * 再读数字，数出来 5 对 4，第一反应是这一块算错了。
   *
   * 把被排除的那一条在句子里点名，数字和列表就对得上了。 */
  const 已有 = rows.length - diggable.length;
  lead.innerHTML =
    `开局你手上只有账户那一侧的一条预警`
    + (已有 ? `，也就是下面标着「你已有」的那条` : '')
    + `。${TA}手机上另外这 <b>${diggable.length}</b> 条里，`
    + `${TA}跟你说到了 <b class="got">${got}</b> 条。<br>${verdict}`;
}

/** 钥匙的维度条。
 *
 * **这一节是补一条真实的产品差距。** 同类的 AI 陪练产品人人都有"维度评分"，
 * 而我们原先只有一张「没用上的钥匙」清单——判分引擎明明按钥匙 × 四个
 * 情绪档位算了一整局，玩家却看不到自己在每一把上站在哪儿。
 *
 * 每一行给三样东西，都从这一局的真实数据里算，不编：
 *   · 用了几次（钝化就是从这儿来的：同一把钥匙用第三次只剩一半效力）
 *   · 这把钥匙一共挣了多少分
 *   · 时机对不对（效力倍率的均值——**这是全作品唯一一处竞品没有的判据**）
 *
 * 没用过的那几把不留白，给出它的一句话说明——那是这一局没动用过的余地。
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
 *
 *  ── 2026-08-25：补第 3、4 两条兜底，这一块从此**永远出现** ────────────────
 *
 *  原先的写法是"两样都没有就整块不出现——不留一个空壳"。这条原则在训练器
 *  定位下成立（打得差的人会再打一局），转向 C 端之后它反过来咬人：
 *
 *  `balance_sim` 自己给的分布是**最低一档仍占 53.4%、novice 胜率 0.0%**，
 *  而"一把钥匙都没命中"正是打得最差的那批人的典型结局。也就是说——
 *  **最需要被解释"你差在时机"的那一半人，恰好是这块解释不给他们看的那一半人。**
 *  评委只玩一局，玩砸的概率过半；这一块不出现，作品唯一的差异点就只剩文档在讲。
 *
 *  补的两条**不编任何评价**，只把矩阵里本来就有的数字念出来：
 *    3. 一把钥匙都没命中 → 念这个场景里"四档之间落差最大"的那一把，
 *       说清同一句话在两个时候差几倍
 *    4. 命中了但时机挑得准（gap ≤ 0.15）→ 说他挑对了，再用他自己用得最多的
 *       那一把把落差摊开
 *  空壳仍然不留：`eff` 拿不到（老令牌、结局事件没下发）时照旧整块不出现。 */
/** 「回到你自己那一笔」：把他自己说过的话，掉头问他自己。
 *
 *  **这是这个作品唯一一处把心理反思落回本人的地方。** 冷开场让他签了一笔、
 *  被拦下、坐到对面；在此之前复盘从没回到过那一笔——他替别人想了三分钟，
 *  没有一秒被请回自己身上。
 *
 *  引用的必须是**他自己挣到过分的那几句**，不是产品替他写的金句：
 *  劝别人时他说得出口，回到自己身上才有分量；而换成一句漂亮话，
 *  这一块立刻退化成又一段鸡汤。
 *
 *  **一句都没挣到分是最常见的一局**（`balance_sim`：最低一档仍占 53.4%，
 *  novice 胜率 0.0%）——那一档不能留白，也不能说"你什么都没做对"。
 *  退回三把钥匙自己的原话：**问题本身照样成立**，只是这一局他没问出口。
 *  这和 P0-4 那条是同一个道理：最需要被说到的那一半人，不能恰好被跳过。
 */
export function paintMirror(view) {
  const lead = view.querySelector('#mirrorLead');
  const list = view.querySelector('#mirrorLines');
  if (!lead || !list) return;

  const own = scoredTurns()
    .filter((t) => t.delta > 0 && t.utterance)
    .sort((a, b) => b.delta - a.delta)
    .slice(0, 3);
  const fallback = ['anchor_real_purpose', 'expose_contradiction', 'check_understanding']
    .map((k) => (KEYS[k] ? withTa(KEYS[k].brief) : '')).filter(Boolean);

  list.innerHTML = '';
  (own.length ? own.map((t) => t.utterance) : fallback).forEach((text) => {
    const li = document.createElement('li');
    li.textContent = text;
    list.appendChild(li);
  });

  // **没演过冷开场就不提它**，见 opening.js `transferSeen` 那段注
  //
  // **「三分钟前」改成「刚才」**（2026-09-03）。这个作品不许对玩家断言一件
  // 没发生的事，而"三分钟"从来没被量过——同机制的外部计时（ROLESafe Table 7，
  // docs/EVIDENCE.md §五）说 5.5 个来回花 5.5 分钟，12 个来回就是十来分钟。
  // 打满十二轮的玩家读到"三分钟前"，那句话对他是假的。"刚才"对谁都真。
  //
  // **同批补上出口那一句虚构声明。** ROLESafe 的设计启示里最贴本项目的一条是
  // "仿真与现实的混淆"：它的被试在有研究员陪同的社区中心尚且会问"我是不是
  // 真的在被骗"，而本项目的形态是在真实券商 App 里、在他真要转一笔钱的那一刻
  // 弹出来的。最坏的一种误读不是不信，是**把局内客户的处境当成自己账户的实情**。
  //
  // 入口两端此前已经各有标识（转账屏 `.transfer-sim`、拦截面 2026-09-03 补、
  // 聊天页 `.strangertip`），**唯独出口没有**——而复盘正是他带着情绪离开的那一屏。
  //
  // 这一句**只声明虚构，不对他自己那笔钱下任何判断**：
  // "你那一笔还没提交"在演示态是假的（他压根没有那一笔），
  // "你可能也在被骗"是判断不是事实——两个都越了线。
  const opener = transferSeen()
    ? '刚才你也按过一次「确认转出」——那一笔和这一局里的人、机构、金额，全是模拟的。'
    : '这一局里的人、机构与金额都是虚构的。';
  const TA = peerPronoun();
  const q = own.length
    ? `你刚才对${TA}说的这几句，换到你自己身上还成立吗？`
    : `这三个问题你这一局没问出口——但它们同样该有人问你。`;
  lead.innerHTML = `${opener}<b>${q}</b>`;
}

export function paintContrast(view) {
  const box = view.querySelector('#contrastBox');
  // **取数搬到 contrast.js 了**（2026-08-29）：分享卡的卡面主角现在也是
  // 这一块，两边必须算出同一个倍数。同一局在两处给出不同的数字，
  // 直接打在"判分是可复现的"这条主张上。这里只负责把它讲成人话。
  const f = contrastFacts();
  if (!f || !box) return;

  const named = (m) => MOODS[m] || m;
  const times = f.times ? `，<b>差 ${f.times} 倍</b>` : '';
  let head = '';
  let body = '';
  const quote = f.quote;

  const TA = peerPronoun();
  // 「这四个字{谁}已经说过了」那半句。**由服务端按场景下发**
  // （`Scenario.warned_by`），拿不到就整句不印。
  //
  // 原先这里写死着「跟他女儿昨天说的那四个字」——照老陈写的，而这一支在
  // 四个场景上都可达：刘卫东的女儿是**三天前**说的、林月娥**没有女儿**
  // （是老公）、顾之然的手机里**根本没有家人**。一半的可达场景里，
  // 复盘对玩家断言了一件那一局剧本里没发生过的事。
  //
  // 顾之然那一局照样成立：没人劝过她本来就是她最难的地方，
  // 少这半句只是少一个类比，不影响"这句话算空口断言"这个判断本身。
  const warned = SCENE && SCENE.warned_by
    ? `——跟${SCENE.warned_by}说的那四个字落在同一个地方。` : '。';

  if (f.kind === 'mistimed') {
    head = `第 ${f.round} 轮 · 你给了依据，也下了判断`;
    body =
      `${TA}当时${named(f.mood)}，这句话算的是<b>空口断言</b>${warned}`
      + `<br>同一句话，${timingClause(f)}，它是这一局分值最高的一把（${f.bestVal}×）。`
      + `<br><b>不是这句话错了，是时候错了。</b>`;
  } else if (f.kind === 'gap') {
    head = `第 ${f.round} 轮 · ${f.name}`;
    body =
      `${TA}当时${named(f.mood)}，这一招值 ${f.val}×。`
      // 「等…再说」还是「早几轮…的时候说」由 timingClause 决定：那一档要是
      // 早就过去了（而且多半是玩家自己把他推过去的），"等"字是句假话
      + `<br>同一句话，${timingClause(f)}，值 ${f.bestVal}×。`
      + `<br><b>动作是对的，差的是时候。</b>`;
  } else if (f.kind === 'flat') {
    head = `你用得最多的那一把 · ${f.name}`;
    body =
      `时机你挑得不错——这一局没出现"动作对、时候错"那种明显的错位。`
      + `<br>但同一把${f.name}，在${TA}${named(f.bestMood)}时值 ${f.bestVal}×，`
      + `在${TA}${named(f.worstMood)}时只值 ${f.worstVal}×${times}。`
      + `<br><b>这一局判的就是这个差值。</b>`;
  } else {
    head = `这一局你一把钥匙都没打中 · 拿 ${f.name} 举个例`;
    body =
      `同一句${f.name}，在${TA}${named(f.bestMood)}时值 ${f.bestVal}×，`
      + `在${TA}${named(f.worstMood)}时只值 ${f.worstVal}×${times}。`
      + (f.mood ? `<br>这一局结束时，${TA}停在${named(f.mood)}。` : '')
      + `<br><b>不是话说得不够好，是时候没等到。</b>`;
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
  foot.textContent = withTa(
    '这一局判的从来不是你说得标不标准，是你用得是不是时候。'
    + '同一把钥匙，在{ta}四种情绪下值的分不一样——换个客户，这张表还会翻过来。');
  box.appendChild(foot);

  view.querySelector('#contrastWrap').hidden = false;
  paintContrastExplorer(view, contrastTimeline());
}

/** 四档的横条，都是同一把钥匙、同一份效力矩阵的一行。
 *
 *  峰值那一档用 `--gold`——全作品唯一的游戏层强调色，`.round.pivot`
 *  早就在用它标"关键转折"那一行，这里接着用，不新造一套视觉语言。
 *  这一轮实际所在的那一档额外挂一个「这轮」标签，两者可能是同一档
 *  （挑对了时候），也可能不是（差的就是这一格）。
 */
function paintContrastBars(box, entry) {
  box.innerHTML = '';
  const named = (m) => MOODS[m] || m;
  const top = Math.max(...Object.keys(MOODS).map((m) => entry.row[m] ?? 0), 0.1);

  Object.keys(MOODS).forEach((m) => {
    const v = entry.row[m];
    if (v == null) return;
    const row = document.createElement('div');
    row.className = 'contrast-bar-row'
      + (m === entry.bestMood ? ' peak' : '')
      + (m === entry.mood ? ' at' : '');

    const label = document.createElement('span');
    label.className = 'contrast-bar-label';
    label.textContent = named(m);
    if (m === entry.mood) {
      label.appendChild(document.createTextNode(' '));
      const tag = document.createElement('b');
      tag.className = 'contrast-bar-tag';
      tag.textContent = '这轮';
      label.appendChild(tag);
    }

    const track = document.createElement('span');
    track.className = 'contrast-bar-track';
    const fill = document.createElement('i');
    fill.style.width = Math.max(6, (v / top) * 100) + '%';
    track.appendChild(fill);

    const num = document.createElement('span');
    num.className = 'contrast-bar-num num';
    num.textContent = `${v}×`;

    row.append(label, track, num);
    box.appendChild(row);
  });
}

/** 「同一句话，换个时候说」可探索版的交互层：一排「第 N 轮」的小按钮，
 *  点哪个就把那一轮命中的钥匙、在四个情绪档位各值多少倍摆出来。
 *
 *  默认选中的就是上面那块headline 讲的同一轮——先接上已经在讲的那个例子，
 *  再让人自己点开别的轮次，不是另起一套无关的东西。
 *  只有一轮能讲时不出现按钮，直接把那一轮的四条摆出来。
 */
function paintContrastExplorer(view, timeline) {
  const wrap = view.querySelector('#contrastExplorer');
  if (!wrap || !timeline.length) return;
  wrap.hidden = false;

  const pillsBox = wrap.querySelector('#contrastPills');
  const cap = wrap.querySelector('#contrastCap');
  const bars = wrap.querySelector('#contrastBars');

  const show = (entry, btn) => {
    if (btn) {
      pillsBox.querySelectorAll('button').forEach(
        (b) => b.setAttribute('aria-pressed', String(b === btn)));
    }
    cap.textContent = entry.mistimed
      ? `第 ${entry.round} 轮 · ${entry.name} · 说早了算空口断言`
      : `第 ${entry.round} 轮 · ${entry.name}`;
    paintContrastBars(bars, entry);
  };

  const buttons = [];
  if (timeline.length > 1) {
    pillsBox.hidden = false;
    timeline.forEach((entry) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'contrast-pill';
      btn.setAttribute('aria-pressed', 'false');
      btn.textContent = `第${entry.round}轮`;
      btn.onclick = () => show(entry, btn);
      pillsBox.appendChild(btn);
      buttons.push(btn);
    });
  }

  // 默认选中的对齐上面那块 headline 已经在讲的那一轮，对不上（flat/nokey
  // 两支没有具体轮次）就退回列表第一条
  const f = contrastFacts();
  const initialIndex = f && f.round ? timeline.findIndex((e) => e.round === f.round) : -1;
  const idx = initialIndex >= 0 ? initialIndex : 0;
  show(timeline[idx], buttons[idx]);
}

/** 七把钥匙这一局各挣了多少。
 *
 *  **`kind === 'unfinished'` 时不给没用过那几把的 `tip`**（2026-09-04）。
 *  `tip` 写的是时机——"这一招得等{ta}晃起来"、"他还硬着的时候让他复述他会
 *  敷衍你"——那是答案，`control.js` 顶上那条分界（词汇是课程，时机是答案）
 *  一直把它留在打完之后。
 *
 *  在此之前这一条只靠"入口藏得深"守着：`endEarly()` 埋在退出抽屉第二项，
 *  点它的人本来就打算走了。把它提到抬头上之后，藏不住了，于是出现一条
 *  合法的泄题路径——第 3 轮点开复盘 → 读完七把钥匙各自该在哪一档用 →
 *  「回去接着打」。这正是 `tools/balance_sim.py` 顶部警告的那件事
 *  （屏幕上有答案，会读字的玩家两轮滑向 expert，§9.1 人群分布当场作废），
 *  只是绕了一圈进来。
 *
 *  **修的是复盘，不是那个「回去接着打」按钮。** 撤回主动结束是无损的
 *  （服务端从没消费过那张令牌），而一个显眼的不可逆按钮比一个藏起来的更糟：
 *  误触一次就丢掉半局。
 *
 *  已经用过的那几把照常给完整反馈——那是他自己打出来的，不是答案。
 *
 *  **`paintContrast` 那张四档对照表刻意没跟着关。** 它确实也露时机（一把
 *  钥匙在四个档位上各值多少），但它只对**你真用过的那把**展开，而这一条
 *  就是这个作品最想教的东西。想靠它把七把钥匙的答案凑齐，得先花七轮各用
 *  一把——十轮的局打到那时候已经结束了，不构成一条走得通的捷径。
 */
export function paintKeyBars(view, kind) {
  const 泄题 = kind === 'unfinished';
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
  // 挣得多的排前面。没用过的沉底——它们是"这一局没动过的那几把"，不是成绩
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
      // 提前结束的那一档只说"这一局没动过它"，不说它该什么时候用
      note.textContent = 泄题 ? '这一局没动过它' : withTa(meta.tip);
    } else {
      const parts = [`第 ${r.rounds.join('、')} 轮用了 ${r.used} 次`];
      if (r.eff != null) {
        const e = r.eff.toFixed(1);
        parts.push(
          r.eff >= 1.2 ? `平均 ${e}× · 时机抓得准`
            : r.eff < 0.7 ? withTa(`平均 ${e}× · 用早了，这一招得等{ta}晃起来`)
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
 * 只知道自己这一局，接上之后才知道自己站在哪：你这一档占多少、钥匙里
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

  /* **样本不够就整行不出**（2026-08-31）。理由与两个常量的口径写在
     `stats.js` 上面那一段：实测这里拿 2 局印出过「100% 的人也停在这一档」，
     而同一块的脚注写着「统计自 13 局」。这是百分位那条门槛
     （`TRUST_SAMPLE_MIN`）当初只落地了一处留下的洞。 */
  const share = data.endings && data.endings[kind];
  if (share && share.share && canCompareEndings(data)) {
    rows.push([
      `你落在「${endingMeta(kind).title}」`,
      `${Math.round(share.share * 100)}% 的人也停在这一档`,
    ]);
  }

  // 分母两边都是"轮"：全局用总轮数，你这局用你打过的轮数。
  // 换成局数会得出大于 1 的"命中率"——一局里同一把钥匙可以用很多次。
  // 所以这一批的门槛也按轮数收（`KEY_SAMPLE_MIN`），不跟上面共用一个数。
  const 够比钥匙 = canCompareKeys(data);
  if (够比钥匙) {
    Object.keys(KEYS).forEach((k) => {
      const g = data.keys && data.keys[k];
      if (!g) return;
      const 我的 = scored.filter((t) => t.hits.includes(k)).length;
      rows.push([
        KEYS[k].name,
        `大家 ${Math.round(g.rate * 100)}% 的发言用到 · 你 ${mine ? Math.round((我的 / mine) * 100) : 0}%`,
      ]);
    });
  }

  // 小雨那条铺垫不在这儿重复说：上面「踩过的坑」里的空口断言卡片已经写了
  // 「这四个字他这三个月听了无数遍」。这一行只负责给一个数。
  const 空口 = 够比钥匙 && data.keys && data.keys.bare_assertion;
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
    el.textContent = withTa(
      `{ta}当时${MOODS[t.judgedMood] || t.judgedMood} · `
      + '这句话本身没问题，你给了依据 —— 但{ta}还没到听得进去的时候，'
      + '这时候说，跟「这是诈骗」四个字在{ta}耳朵里是一样的');
    return el;
  }
  if (t.efficacy == null) return null;
  const mood = MOODS[t.judgedMood] || t.judgedMood;
  const el = document.createElement('div');
  el.className = 'timing' + (t.efficacy >= 1.2 ? ' good' : t.efficacy < 0.7 ? ' bad' : '');
  const verdict = t.efficacy >= 1.2 ? ' 正是时候'
    : t.efficacy < 0.7 ? ' 用早了，这一招得等{ta}晃起来' : '';
  el.textContent = withTa(`{ta}当时${mood} · 这一招值 ${t.efficacy}×${verdict}`);
  return el;
}

// 追问窗口那一行。同类产品公认的一个洞是"评分捕捉不到回避行为"——
// 受训者在安全话题上过度展开，一碰到真正的痛点就退回安全区，而评分只看
// 话术完整度，反而给了正面激励。追问窗口捕捉的正是它：他晃起来的那两轮
// 你有没有跟上。以前这条只在引擎里算，玩家一眼都看不到。
export const WINDOW_NOTE = {
  hit: { text: '{ta}正晃着，你接住了 · 这一招额外加成', cls: 'good' },
  missed: { text: '两轮的口子空掉了，{ta}重新硬了回去 · 扣 6 分', cls: 'bad' },
  open: { text: '口子还开着，还剩一轮', cls: '' },
};

export function windowNote(t) {
  const parts = [];
  const w = WINDOW_NOTE[t.windowResult];
  if (w) parts.push([w.text, w.cls]);
  if (t.windowOpened) parts.push(['{ta}第一次晃到这一档 · 接下来两轮是机会', 'good']);
  if (!parts.length) return null;
  const box = document.createElement('div');
  parts.forEach(([text, cls]) => {
    const el = document.createElement('div');
    el.className = 'window' + (cls ? ' ' + cls : '');
    el.textContent = withTa(text);
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
  p.textContent = withTa(meta.tip);
  card.append(title, p);
  return card;
}
