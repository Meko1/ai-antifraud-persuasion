/* 轮次上限：**从 app/scoring.py 现读，素材脚本一律不写死。**
 *
 * ## 为什么要有这个文件
 *
 * 2026-09-04 轮次上限从十二轮改成十轮（novice 中途被拉黑集中在后两轮，
 * 砍掉能把被拉黑率从 20.2% 压到 9.7%）。产品各处都跟着改了——状态机、
 * 聊天页、拦截面、复盘。**唯独两个素材脚本的文案没改。**
 *
 * 于是 09-05 重出的那套素材里，封面标题写着「你有十二轮」，
 * 而同一张图右边那张手机截图上印着「第 6 轮 / 10」；51 秒的片子里
 * 「十二轮」出现三次。**一张自己跟自己打架的封面是这套素材里最贵的一处错**——
 * 按 CONTEST §6.2 的漏斗，图集第一张承担全部点击转化。
 *
 * 写死一次就会再错一次，所以改成现读：轮次上限只有 `scoring.py` 那一处，
 * 素材的措辞跟着它走。
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SCORING = path.join(HERE, '..', 'app', 'scoring.py');

const 命中 = /^MAX_ROUNDS\s*=\s*(\d+)/m.exec(fs.readFileSync(SCORING, 'utf8'));
if (!命中) {
  // 静默落到一个猜的数，等于把这个文件想解决的问题换个地方再犯一次
  throw new Error(`读不到 MAX_ROUNDS：${SCORING} 的写法变了？素材文案会说错轮数`);
}

/** 一局的轮次上限，与 `app/scoring.py` 的 `MAX_ROUNDS` 同一个数。 */
export const MAX_ROUNDS = Number(命中[1]);

const 汉字 = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九'];

/** 素材文案里那个中文数字（「你有十轮」）。 */
export const 轮数汉字 = (function 转(n) {
  if (n < 10) return 汉字[n];
  if (n === 10) return '十';
  if (n < 20) return `十${汉字[n % 10]}`;
  return `${汉字[Math.floor(n / 10)]}十${n % 10 ? 汉字[n % 10] : ''}`;
})(MAX_ROUNDS);
