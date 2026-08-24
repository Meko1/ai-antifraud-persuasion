import { $, showScreen, thread } from './dom.js';
import { KEYS } from './keys.js';
import { reportExit } from './api.js';
import { openSheet } from './sheet.js';
import { openReview } from './review.js';
import { game, saveGame } from './state.js';

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
  const tip = document.createElement('p');
  tip.className = 'strangertip';
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

/** 七种问法：对局中随时翻回来看。**识别优于回忆。**
 *
 *  只给名字和 `brief`，**不给 `tip`**——那两句话的分界写在 KEYS 顶部：
 *  词汇是课程，时机是答案。开打前讲时机就是泄题，对局中讲更是。
 */
export function openMethodsSheet() {
  openSheet({
    title: '你手里有这七种问法',
    note: '什么时候用哪一种，这里不会告诉你——那正是这一局要练的。',
    items: Object.keys(KEYS).map((k) => ({
      label: KEYS[k].name,
      note: KEYS[k].brief,
    })),
  });
}
