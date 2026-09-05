import { $, showScreen, thread } from './dom.js';
import { KEYS } from './keys.js';
import { reportExit } from './api.js';
import { openSheet } from './sheet.js';
import { openReview } from './review.js';
import { game, saveGame, withTa } from './state.js';

// ── 用户控制：退出、暂停、提前结束 ────────────────────────────────────────
//
// **在此之前这一屏没有任何出口。** 十二轮打完之前，页面上只有输入框和
// 发送键——而定位文档写着"不做成必须通过才能交易的障碍"。
// 一个关不掉的弹窗，在真实的转账前场景里换来的是投诉，不是反思。
//
// 三条出路，对应三种真实意图，一个都不能省：
//
// · **稍后继续** —— 他想去看一眼真实的转账页面再回来。这是最该被支持的
//   那一种，而且已经有现成的机制（sessionStorage 续局），此前只是没有入口。
// · **结束并看复盘** —— 他觉得说完了。**这一条是"不必打完整局"的落点**：
//   已经打的那几轮判分照常、复盘照常，只是不编造一个资金结局。
// · **直接离开** —— 他不想要这个东西。**不设挽留、不加二次确认。**
//   在退出路径上放障碍，正是这条定位明确要避免的事。
//
// 三条都会向服务端报一笔（`/api/game/exit`），那是护栏指标：
// 直接关闭率、中断率是判断这个干预有没有伤到用户的第一组数。


/** 抬头上那颗「看复盘」从第几轮起出现（2026-09-04）。
 *
 *  **为什么要把它从抽屉里提出来。** 「就到这儿，看复盘」这条出路机制上早就
 *  齐了——已打的轮次照常判分、复盘照常、只是不编造一个资金结局——但它是
 *  退出抽屉的第二项，玩家得先想到"我要退出"才碰得到。而这条出路要接的人
 *  恰恰不想退出：他是**说不下去了**。这两件事不是一回事，入口也就不该是
 *  同一个。中途走掉的局在这之前一个字的复盘都没有，而这个作品全部的教学
 *  价值都在复盘里。
 *
 *  **为什么是 4 而不是 1。** 更早出现等于在人还没打进去的时候先递一个出口；
 *  而且一两轮的复盘没有内容可讲，点开只会让人觉得这东西没什么可看的。
 *  取 `MAX_ROUNDS` 的四成上下：十轮的局第 4 轮起，后面六轮一直在。
 *
 *  **写成常数不写成 `maxRounds * 0.4`**：轮次上限刚从 12 改到 10（一次），
 *  比例式会让"第几轮出现"跟着悄悄浮动，而这是个体验判断，该由人定。
 */
export const EARLY_REVIEW_FROM = 4;

/** 抬头那颗「看复盘」的显隐。**只有这一处决定它**（同 `syncSend` 的理由）。
 *
 *  打完的局不显示：那时候聊天窗口末尾已经有一颗「看复盘」了（`finish()`），
 *  同一屏上两颗同名按钮，玩家会以为它们不是一件事。
 *
 *  **判据是"抬头显示的那个轮次"，不是 `game.turns.length`。** 两者差一——
 *  `game.turns.length` 是**打完**的轮数，而 `turnCurrent` 显示的是
 *  `chat.js`/`opening.js` 里那套"下一轮"算法算出来的、玩家正在看的那个数
 *  （抬头写着「第 4 轮 / 10」时，`game.turns.length` 其实是 3）。
 *  `EARLY_REVIEW_FROM` 这个名字对应的是玩家读到的那个数，写成
 *  `game.turns.length < EARLY_REVIEW_FROM` 的话，实测按钮要等到玩家已经
 *  在打第 5 轮才出现——晚了一整轮，注释与实际行为对不上。
 */
export function syncEarlyReview() {
  const btn = $('earlyReview');
  if (!btn) return;
  const 抬头显示的轮次 = game.turns.length + 1;
  btn.hidden = !!game.ending || !!game.exited
    || 抬头显示的轮次 < EARLY_REVIEW_FROM;
}

export function openExitSheet() {
  const played = game.turns.length;
  openSheet({
    title: '离开这次对话',
    note: played
      ? `已经进行了 ${played} 轮。这一局会给你留着，随时可以回来。`
      : '还没开始。随时可以离开，不需要理由。',
    items: [
      {
        label: '稍后继续',
        note: '这一局给你留着。回到这个页面就接着打。',
        onPick: () => { reportExit(game.token, 'abandoned'); showScreen('opening'); },
      },
      {
        label: played ? '就到这儿，看复盘' : '就到这儿',
        note: played
          ? '不再继续，直接看这几轮的复盘。已经打的轮次照常判分。'
          : '一轮都没打，没有可复盘的内容。',
        onPick: () => (played ? endEarly() : leaveIntervention()),
      },
      {
        label: '直接离开',
        note: '关掉这次干预，不看复盘。',
        tone: 'danger',
        onPick: leaveIntervention,
      },
    ],
  });
}

/** 主动结束并看复盘。**不发请求，不推进任何状态。**
 *
 *  这一局在服务端仍然停在原地——令牌没被消费，理论上他回来还能接着打。
 *  前端这一侧把它标成"已结束"，复盘据此走 `unfinished` 那一档：
 *  判分照常，**但不编造一个资金结局**（见 resultAmount 里那个分支）。
 */
export function endEarly() {
  game.exited = true;
  game.busy = false;
  reportExit(game.token, 'finished_early');
  $('composer').hidden = true;
  syncEarlyReview();   // 抬头那颗要收起来：复盘页自己有出口，这里不能再有一个
  const tip = document.createElement('p');
  // `exit-tip` 单独标一下：复盘页「回去接着打」要把它从聊天记录里摘掉——
  // 撤销这个决定之后，这句「你结束了」就成了一句不实的记录。
  tip.className = 'strangertip exit-tip';
  tip.textContent = '你结束了这次对话。这笔钱最后怎么样，本次模拟没有给出结果。';
  thread.appendChild(tip);
  saveGame();
  openReview();
}

/** 退出这次干预，回到开场那一屏。
 *
 *  **不清存档**：他可能只是想喘口气。真要重开有"开始一位新客户"那个按钮，
 *  而一个不小心点到退出就丢掉十轮对话的产品，只会让人再也不敢点任何东西。
 */
export function leaveIntervention() {
  reportExit(game.token, game.turns.length ? 'abandoned' : 'dismissed');
  showScreen('opening');
}

/** 七把钥匙：对局中随时翻回来看。**识别优于回忆。**
 *
 *  只给名字和 `brief`，**不给 `tip`**——那两句话的分界写在 KEYS 顶部：
 *  词汇是课程，时机是答案。开打前讲时机就是泄题，对局中讲更是。
 */
export function openMethodsSheet() {
  openSheet({
    // 标题与这一句 2026-08-30 跟 index.html 那一屏一起改，理由写在那边：
    // 七把里有四把不是"问法"，而"要练的"是训练器定位的残留。**两处必须同步**，
    // 它们是同一份内容的两个入口，玩家会先后看到。
    title: '你手里有这七把钥匙',
    note: '难的从来不是说什么，是什么时候说。',
    items: Object.keys(KEYS).map((k) => ({
      label: KEYS[k].name,
      // 抽屉打开时客户已经在标题栏上了，代词必须跟着本局走：这一层就贴在
      // 「{ta}开始犯嘀咕了」和「输入你想对{ta}说的话」中间，写死了当场穿帮
      note: withTa(KEYS[k].brief),
    })),
  });
}
