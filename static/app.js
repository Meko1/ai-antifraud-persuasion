/* AI 反诈劝阻 · 前端
 *
 * 你＝老陈的投资顾问（局内他叫你李经理），他＝你的客户。首页那一屏是**你的
 * 工作台**（企业微信 + 券商 CRM）：一条资产异动预警加一张客户档案，就是你
 * 开局手上的全部。点"给陈叔发消息"才进聊天。
 *
 * 他手机上那五条（启航财经群、银行短信、老伴、小雨、反诈中心）**不在开局**——
 * 它们在复盘里逐条揭晓（`paintPhone`）。原先它们就是首页，等于开局把这一局
 * 要挖的东西全给了玩家，而挖它就是玩法。
 *
 * 服务端不存会话：每轮请求都要带上一轮返回的 token（ADR-0003）。
 * SSE 事件顺序恒为 meta → sentence* → score → ending? → state → done，
 * score 刻意排在台词播完之后——信任度那一跳要等他把话说完才发生。
 *
 * 界面语法照搬微信：判分卡走「系统提示」的位置，对局状态走「群公告」的位置。
 */

/* ── 模块图 ──────────────────────────────────────────────────
 *
 * 这个文件从 2026-08-24 起只做两件事：把模块串起来、把监听器挂上去。
 * 在此之前它是 3087 行的单文件（复核清单 P2-1）。
 *
 *   dom.js       拿节点、切屏、等一会儿          （谁都可以依赖）
 *   keys.js      七把钥匙 / 三种失误 / 两条红线  （与 app/scoring.py 对齐）
 *   state.js     会话状态机、结局判定、续局存档  （不碰 DOM，单测就打在这一层）
 *   api.js       /api/game/* 与 SSE 客户端       （全站唯一的 fetch 出口）
 *   stats.js     百分位（纯函数）
 *   chart.js     K 线与 canvas 那几笔            （正文与分享卡共用同一张图）
 *   sheet.js     底部动作面板                    （焦点捕获只此一套）
 *   chat.js      聊天视图、一轮的流程、续局重画
 *   review.js    复盘视图
 *   history.js   本机对局记录与分享卡
 *   opening.js   开场、客户档案、课程表、挑客户
 *   control.js   退出、七种问法
 *   contrast.js  复盘「同一句话换个时候说」那一支
 *   qr.js        分享卡上那个二维码（自绘，不拉第三方库）
 *
 * chat / review / history / opening / control / contrast 是视图，
 * dom / keys / state / api / stats / chart / qr 是它们共用的底座。
 * 方向只有一条：**视图依赖状态，状态不依赖视图**。dom / keys / state /
 * api / stats / chart / qr 一个视图模块都不 import，这一条是硬的。
 *
 * 视图之间有环，而且去不掉，因为产品本来就是环的：
 *
 *   chat → review     打完这一局要开复盘
 *   review → opening  复盘页上那个「换一位客户」
 *   opening → chat    有存档时要把聊天重画一遍
 *
 * ES module 认得这种环（跨模块引用的全是函数声明，实例化时就绑好了），
 * **但模块体里的副作用不认**：环里谁先求值取决于 import 的书写顺序，
 * 先跑的那个可能去读后跑的那个还没初始化的 `const`。
 * 所以点火那一下从 opening.js 的模块体挪到了这里——**入口最后求值，
 * 到这一行时所有模块体都跑完了**。见 opening.js 的 `boot()`。
 *
 * 加载方式是 `<script type="module">`，**没有打包步骤**：
 * 浏览器自己按 import 解析，全部同源，因此 app/main.py 里那条
 * `script-src 'self'` 一个字都不用改。 */

import { $, showScreen } from './dom.js';
import { playTurn, syncSend } from './chat.js';
import {
  ackTransfer, boot, confirmTransfer, enterGame, markPrimerSeen, openClientSheet,
} from './opening.js';
import { openExitSheet, openMethodsSheet } from './control.js';
import { game, startNewClient } from './state.js';

// 点火。放在挂监听之前，与拆分前的顺序一致：开局请求要尽早发出去，
// 玩家读工作台那一屏的时间同样在给它买单（「首屏 ≤3 秒」）。
boot();

// 转账确认那一屏的两步：签字 → 被拦下来 → 坐到对面。
// 两步都在同一屏上完成，理由见 index.html 那一段注释。
$('transferGo')?.addEventListener('click', confirmTransfer);
$('handoffGo')?.addEventListener('click', ackTransfer);

// 它现在是个真 <button>，回车与空格由浏览器自己管，不用再补 keydown
$('openChen').addEventListener('click', enterGame);

// 「跳过」与「知道了」走同一条路：都记下已看过，然后进聊天。
// 跳过不该被惩罚——愿意空手上阵的人本来就是这个作品最想要的那批玩家。
['primerGo', 'primerSkip'].forEach((id) => {
  const el = $(id);
  if (el) el.addEventListener('click', () => { markPrimerSeen(); enterGame(); });
});
$('backHome')?.addEventListener('click', () => showScreen('home'));
$('openProfile').addEventListener('click', () => showScreen('home'));
$('backOpening').addEventListener('click', () => showScreen('opening'));
// `startNewClient` 现在收一个可选的 sid，而事件回调的第一个参数是 Event——
// 直接挂上去会把一个 MouseEvent 当成场景 id。包一层
$('retryStart').addEventListener('click', () => startNewClient());
$('pickClient')?.addEventListener('click', openClientSheet);

// 用户控制那三个入口
$('chatExit').addEventListener('click', openExitSheet);
$('openMethods').addEventListener('click', openMethodsSheet);

$('composer').addEventListener('submit', (e) => {
  e.preventDefault();
  if (game.busy) return;
  const input = $('say');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  playTurn(text);
});

// 输入一变就重算发送键。**用 input 不用 keyup**：粘贴、输入法上屏、
// 清空按钮都只触发 input，用 keyup 会漏掉它们
$('say').addEventListener('input', syncSend);
syncSend();

// 回车发送。isComposing 那一层不能少：中文输入法用回车确认候选词，
// 少了它，玩家选词时会把半句话发出去。
$('say').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.isComposing) {
    e.preventDefault();
    $('composer').requestSubmit();
  }
});
