/* 百分位：这一局在别人里排第几。
 *
 * 纯函数，不碰 DOM 也不发请求——复盘正文（review.js）与分享卡
 * （history.js）都要用，放在两边任何一边都会让另一边反向依赖。 */

// 后端 `app/stats.py` 的 `_trust_bucket`：5 分一档、20 个桶，下标 = trust // 5。
// 样本 = 桶内计数之和，不满这个数就不显示百分位。
//
// **2026-09-11 从 20 降到 8。** 原来那个 20 判断没错，代价算错了：分布按
// 场景 × 口径分开存（`app/stats.py` 的 `key_trust`），20 局的门槛乘上六个
// 场景就是一百二十局**入档**对局，而被拉黑不入档——`tools/stats_seed.py`
// 实测灌 20 局只落得下 17~19 个样本，真要跨过这条线得灌一百四十多局。
// 结果是这条线在任何一次真实试用里都不会被跨过，屏幕上常驻那句
// 「排行样本积累中」：一个从来没亮过的功能，跟没有这个功能的区别，
// 只在于它还占着一行。
//
// 降下来不是把当初那条原则收回去。当初出事的不是样本小，是**样本看不见**
// ——n=2 印「100% 的人也停在这一档」，而同一块的脚注写着「统计自 13 局」。
// 所以这次是两件事一起改：门槛降到 8，同时把 n 写进那句话本身
// （`percentileCopy`，分享卡同理见 history.js 的 `makeCard`）。
// 读的人看得见自己在跟几局比，8 局的百分位就是一句成立的话——
// 它本来声称的也只是"在这 8 局里排第几"。
//
// 8 这个数：5 分一桶、8 个样本的分辨率约 12 个百分点，高低两头还分得开；
// 再往下（3~5 局）那个数会因为一局的进出大幅跳动，那才是编出来的精度。
export const TRUST_SAMPLE_MIN = 8;

/* 服务端样本还没攒够时的第二条路：**跟自己比**（2026-09-11）。
 *
 * 上面那条门槛无论降到几，都还有两种情况是它救不了的：
 *  · `REDIS_URL` 没配、或者像本机这样连不上那台内网 Redis —— `/api/stats`
 *    返回 `available: false`，一个桶都读不到；
 *  · 换了个冷门场景、或者刚上线的头几十局。
 * 这两种情况下原来的处理是"那一行原样留着占位文案"，等于把"我们这儿有个
 * 功能坏着"写在复盘里。
 *
 * 本机记录（history.js 的 `loadHistory`）不依赖任何服务端设施，第二局起就有
 * 东西可比。它**不是人群排名**，所以文案里必须把这件事说出口——把"你打过的
 * 几局里排第几"说成"超过了多少人"，才是这一节真正不能犯的错。
 *
 * 2 局：第二局打完就能给出对比，而 1 局时"排第 1"是句废话。
 */
export const LOCAL_RANK_MIN = 2;

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

/** 这份分布一共几局。**要能单独拿到**：门槛降下来之后，n 不再是一个只用来
 *  过闸的内部数，它得跟着百分位一起印到屏幕上（见 `percentileCopy`）。 */
export function trustSample(buckets) {
  if (!Array.isArray(buckets)) return 0;
  return buckets.reduce((a, b) => a + (Number(b) || 0), 0);
}

/** 分布是分桶存的，不是每一局的原始值，百分位因此是个近似值：
 *  桶外的直接算"被我超过"，桶内按信任度在这 5 分区间里的相对位置插值——
 *  不然数字会卡在 5 分一档的台阶上，一眼就看得出是硬凑的。 */
export function trustPercentile(buckets, trust) {
  if (!Array.isArray(buckets) || !buckets.length) return null;
  const total = trustSample(buckets);
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
 *
 * **`sample` 是 2026-09-11 加的，不是装饰。** 门槛从 20 降到 8（见
 * `TRUST_SAMPLE_MIN`），换来的条件就是这半句：一句不写分母的「高于 62%」，
 * 读的人无从判断它是跟八局比还是跟八百局比。分母写在同一行里，
 * 这句话才跟着样本一起变得诚实——脚注在别处、正文里不提，正是当初
 * 「n=2 印 100%」那个洞的形状。
 */
export function percentileCopy(pct, sample) {
  const n = Number(sample) || 0;
  return `高于同场景 ${pct}% 的已完成对局` + (n ? ` · 统计自 ${n} 局` : '');
}

/** 本机排名：这一局在**这台设备打过的局**里排第几。
 *
 * 只收入档的局（调用方按 history.js 的 `TIER_RANK` 过滤好再传进来——
 * 那张阶梯表是它的，不在这儿抄第二份）。同场景够两局就只跟同场景比，
 * 理由与服务端百分位按场景分桶完全一样（`app/stats.py` 的 `key_trust`：
 * 各场景难度不同，混着比量出来的是"抽到的场景难不难"）；不够就退回全部，
 * 但那时文案要把"跨客户"说出来（见 `localRankCopy`）。
 *
 * 本局自己也在 `entries` 里（`recordGame` 先于这里跑），所以名次是
 * "比我高的局数 + 1"，并列同名次。 */
export function localRank(entries, { sid = '', trust = 0 } = {}) {
  const list = (Array.isArray(entries) ? entries : []).filter(Boolean);
  const same = sid ? list.filter((e) => e.sid === sid) : [];
  const sameScene = same.length >= LOCAL_RANK_MIN;
  const pool = sameScene ? same : list;
  if (pool.length < LOCAL_RANK_MIN) return null;
  const above = pool.filter((e) => (Number(e.trust) || 0) > trust).length;
  return { rank: above + 1, total: pool.length, sameScene };
}

/** 本机排名那句话。**"本机记录"四个字不许省**：这一行长得跟百分位一模一样，
 *  不说清楚它就会被读成人群排名——那正是这一节唯一不能犯的错。 */
export function localRankCopy(res, name = '') {
  if (!res) return '';
  const 谁 = res.sameScene && name ? `跟${name}` : '';
  const 口径 = res.sameScene ? '本机记录，不是人群排名' : '跨客户比，难度不完全一样';
  return `这台设备${谁}打过 ${res.total} 局，这一局排第 ${res.rank} · ${口径}`;
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
