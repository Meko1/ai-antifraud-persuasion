/* 百分位：这一局在别人里排第几。
 *
 * 纯函数，不碰 DOM 也不发请求——复盘正文（review.js）与分享卡
 * （history.js）都要用，放在两边任何一边都会让另一边反向依赖。 */

// 后端 `app/stats.py` 的 `_trust_bucket`：5 分一档、20 个桶，下标 = trust // 5。
// 复盘定的门槛：样本（桶内计数之和）不满 20 局不显示——数据太少时报一个
// "超过 100% 的人"没有意义，不如不说，跟这一节整体"没数据就不出现"是同一条原则。
export const TRUST_SAMPLE_MIN = 20;

/* 「别人打成什么样」那一块的同款门槛（2026-08-31 补）。
 *
 * **上面那条原则只落地了百分位一处。** 隔壁那块——结局占比与钥匙命中率——
 * 一道闸都没有，服务端 `available: true` 就照单全印。实测接口返回：
 *
 *     games: 13, turns: 30
 *     endings.stalled: { count: 2, share: 1.0 }
 *
 * 屏幕上于是写着「**100% 的人也停在这一档**」，而紧挨着的脚注写着
 * 「统计自 13 局、30 轮对话」——**样本是 2 局**。
 *
 * 这个作品最值钱的资产是主张边界：README 里逐条撤回过站不住的论据，
 * POSITIONING 里明说"没有基线之前任何一个预期数字都是编的"。
 * 而运行中的界面在拿 n=2 印一个 100% 的人群结论。**两件事同框，
 * 伤的是前者。**
 *
 * 门槛取跟百分位同一个数、同一条理由：宁可不显示，也不显示一个不成立的
 * 群体结论。攒不够就整块不出现（`paintStats` 里 `rows.length` 那道闸），
 * 与这一节"没数据就不出现"的处理一致。
 *
 * 两个分母不一样，所以是两个常量：
 *  · 结局占比的分母是**入档的局数**（`sum(endings[*].count)`）
 *  · 钥匙命中率的分母是**轮数**，一局贡献十来轮，按 20 局的量级折成 200
 */
export const ENDING_SAMPLE_MIN = 20;
export const KEY_SAMPLE_MIN = 200;

/** 结局占比那一行能不能说。分母是**入档的局数**，不是 `games`——
 *  后者含被拉黑与半途而废的局，它们没进任何一档，
 *  拿它当分母会把"100% 的人"的那个 100% 稀释成一个看不出问题的数。 */
export function canCompareEndings(data) {
  const finished = Object.values((data && data.endings) || {})
    .reduce((sum, e) => sum + ((e && e.count) || 0), 0);
  return finished >= ENDING_SAMPLE_MIN;
}

/** 钥匙命中率那一批能不能说。分母是轮数（"大家 X% 的**发言**用到"）。 */
export function canCompareKeys(data) {
  return ((data && data.turns) || 0) >= KEY_SAMPLE_MIN;
}

/** 分布是分桶存的，不是每一局的原始值，百分位因此是个近似值：
 *  桶外的直接算"被我超过"，桶内按信任度在这 5 分区间里的相对位置插值——
 *  不然数字会卡在 5 分一档的台阶上，一眼就看得出是硬凑的。 */
export function trustPercentile(buckets, trust) {
  if (!Array.isArray(buckets) || !buckets.length) return null;
  const total = buckets.reduce((a, b) => a + b, 0);
  if (total < TRUST_SAMPLE_MIN) return null;
  const mine = Math.max(0, Math.min(buckets.length - 1, Math.floor(trust / 5)));
  const below = buckets.slice(0, mine).reduce((a, b) => a + b, 0);
  const within = buckets[mine] || 0;
  const pos = within ? Math.max(0, Math.min(1, (trust - mine * 5) / 5)) : 0;
  return Math.round(((below + within * pos) / total) * 100);
}

/** 百分位这句话，跟 verdictCopy 是同一套嘴——具体、说人话、不打鸡血。
 *
 * **原来的写法是"这一局的信任度超过了已有记录里 X%"**：不管 X 是 95 还是 5，
 * 都是同一句模板换个数字，是典型的"仪表盘播报腔"。分数低的时候尤其显得假——
 * 一个 15% 配一句语气跟 95% 一模一样的话，像是没看懂自己在说什么。
 *
 * 分数不同，值得说的话也不同：高分是真值得夸的一手；低分不回避那个数，
 * 但接一句具体能改的东西，跟 verdictCopy 低分那句"缺的是最前面那一步——
 * 先听懂{ta}在怕什么、在图什么"是同一个路数——情绪价值不是把烂分数说成
 * 好分数，是把冷冰冰的排名换成一句听得出是在跟你说话的话。
 *
 * （引的那句 2026-08-30 改过一次：原文以"下一局试着…"开头，而这个人
 * 只打这一局，许诺一个不会发生的下一次是训练器定位的残留。）
 */
export function percentileCopy(pct) {
  return `高于同场景 ${pct}% 的已完成对局`;
}

/** 百分位配色跟着分数走，不是每次都用那罐"值得庆祝"的绿——
 *  15% 配一个和 95% 一样鲜亮的绿底，正是看着"怪"的地方。
 *  三色沿用复盘正文其余地方的用法：够亮眼才给品牌绿，其余一律中性灰。 */
export function percentileTier(pct) {
  return pct >= 60 ? 'good' : 'plain';
}

/** percentileCopy 的简短版，给分享卡用——卡片宽度固定，长版那句带建议的话
 *  放不下一行，canvas 又不像 CSS 那样会自动折行。语气分级跟长版是同一套。 */
export function percentileHeadline(pct) {
  if (pct >= 85) return `比 ${pct}% 打过的人都高`;
  if (pct >= 60) return `超过了 ${pct}% 的人`;
  if (pct >= 35) return `超过了 ${pct}% 的人，还有空间`;
  if (pct >= 10) return `超过了 ${pct}% 的人，才刚起步`;
  return `超过了 ${pct}% 的人，这局是真难`;
}
