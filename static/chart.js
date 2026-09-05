/* canvas 那几笔：K 线、取色、按 dpr 铺画布、圆角矩形。
 *
 * 复盘正文与分享卡画的是**同一张图**（同一个 paintKline），
 * 屏幕上看到什么，卡上就是什么。 */

// ── K 线（复盘）─────────────────────────────────────────────
// 一根蜡烛就是一轮：开＝上轮信任度，收＝本轮信任度，细横线＝这一轮判分给出的
// 分数。横线与实体端点的落差，正是每轮的信任流失与蓄势池的存取。

export function paintKline(ctx, box, opts) {
  const { x, y, w, h } = box;
  const { turns, threshold, slots, palette: c } = opts;
  const py = (v) => y + h - (Math.max(0, Math.min(100, v)) / 100) * h;

  ctx.save();

  // 开局那条线。**加它是为了把图上那片空白变成信息。**
  // 纵轴固定 0–100，而多数局子收在 40 以下，于是上面大半张图是空的，
  // 看着像没画完。有了这条线，同一片空白立刻在说一件事：
  // 你是把他往上推了，还是一路把他推下去了——一眼就看得出来。
  if (opts.start != null) {
    ctx.strokeStyle = c.line;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, Math.round(py(opts.start)) + 0.5);
    ctx.lineTo(x + w, Math.round(py(opts.start)) + 0.5);
    ctx.stroke();
    if (opts.axis) {
      ctx.fillStyle = c.note || c.gray;
      ctx.font = `400 ${opts.labelSize || 10}px ${c.sans}`;
      ctx.textAlign = 'right';
      ctx.textBaseline = 'bottom';
      ctx.fillText(`开局 ${opts.start}`, x + w - 2, py(opts.start) - 3);
    }
  }

  ctx.strokeStyle = c.goal;
  ctx.setLineDash([3, 4]);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x, py(threshold) + 0.5);
  ctx.lineTo(x + w, py(threshold) + 0.5);
  ctx.stroke();
  ctx.setLineDash([]);

  if (opts.axis) {
    // 标线本身用鲜亮的 goal，字用专门配出的 goalText——同一个颜色兼职当
    // 线又当字，字那份对比度不够看（2.42:1，门槛 4.5），这是它看着发虚的
    // 原因之一
    ctx.fillStyle = c.goalText || c.goal;
    ctx.font = `600 ${opts.labelSize || 10}px ${c.sans}`;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'bottom';
    ctx.fillText(`劝住 ${threshold}`, x + 2, py(threshold) - 3);
  }

  const count = Math.max(slots, turns.length, 1);
  const step = w / count;
  const width = Math.max(4, Math.min(opts.maxWidth || 16, step * 0.5));
  const radius = Math.min(2.5, width / 2);

  turns.forEach((t, i) => {
    const cx = x + step * (i + 0.5);
    const open = t.before;
    const close = t.trust;
    const raw = Math.max(0, Math.min(100, open + t.delta));
    const color = close > open ? c.rise : close < open ? c.fall : c.sub;

    // 影线：2px、圆头——原来 1px 加透明度叠在实体上会糊成一团浅色，
    // 换成不透明的细线，粗细不够就直接调宽度，不靠透明度撑视觉重量
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(cx, py(Math.max(open, close, raw)));
    ctx.lineTo(cx, py(Math.min(open, close, raw)));
    ctx.stroke();

    // 实体：4px 圆角——原来是直角矩形，跟界面其余地方（气泡、徽章）
    // 全是圆角的语言对不上，这块地方最扎眼地显得"没做完"
    const top = py(Math.max(open, close));
    const bottom = py(Math.min(open, close));
    ctx.fillStyle = color;
    roundRect(ctx, cx - width / 2, top, width, Math.max(3, bottom - top), radius);
    ctx.fill();

    // 判分线：原来是半透明叠加，颜色会随底下是实体还是空白而变深浅不一，
    // 换成固定的中性色、不透明，同一条线在任何底色上都是同一个视觉重量。
    // 只画影线的话，蓄势池「释放」那一侧会看不见，所以这条线不能省
    ctx.strokeStyle = c.note;
    ctx.lineWidth = 1.5;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(cx - width * 0.6, py(raw));
    ctx.lineTo(cx + width * 0.6, py(raw));
    ctx.stroke();
  });

  ctx.strokeStyle = c.line;
  ctx.lineWidth = 1.5;
  ctx.lineCap = 'round';
  for (let i = turns.length; i < count; i++) {
    const cx = x + step * (i + 0.5);
    ctx.beginPath();
    ctx.moveTo(cx - width / 2, y + h);
    ctx.lineTo(cx + width / 2, y + h);
    ctx.stroke();
  }

  ctx.restore();
}

export function palette() {
  const s = getComputedStyle(document.documentElement);
  const v = (n) => s.getPropertyValue(n).trim();
  return {
    rise: v('--wx-rise'), fall: v('--wx-fall'), brand: v('--wx-brand'),
    sub: v('--wx-sub'), line: v('--wx-line'), text: v('--wx-text'),
    white: v('--wx-white'), gray: v('--wx-gray'), goal: '#c9a227',
    // 画在图上的小字要能读：--wx-sub 对白底只有 2.12:1、--gold 只有 2.42:1，
    // 门槛是 4.5。图形色（线、蜡烛）照旧鲜亮，文字色单独取深的那一份——
    // goalText 就是给"劝住 80"那行字用的，标线本身仍然用 goal
    note: v('--wx-note'), goalText: v('--gold-text'),
    bg: v('--wx-bg'), red: v('--wx-red'),
    sans: '-apple-system, "PingFang SC", "Microsoft YaHei", sans-serif',
    mono: 'ui-monospace, Menlo, Consolas, monospace',
  };
}

// 同一张图只解码一次：分享卡每次重画都会调这里，不用每次都重新拉一遍网络。
const _imageCache = new Map();

/** 加载一张图片供 `ctx.drawImage()` 用。**失败也 resolve（给 null），不 reject**——
 *  调用方据此跳过这一笔绘制，而不是让一张分享卡因为一张图标加载失败就整个
 *  生成不出来。同一个理由见 `review.js` 里吉祥物那处注释：这类资源缺失
 *  该退化成"少画一笔"，不该是一次没接住的 rejection。 */
export function loadImage(src) {
  if (!_imageCache.has(src)) {
    _imageCache.set(src, new Promise((resolve) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => resolve(null);
      img.src = src;
    }));
  }
  return _imageCache.get(src);
}

export function fitCanvas(canvas, cssW, cssH) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  return ctx;
}

/** 圆角矩形。ctx.roundRect 在旧一点的 iOS Safari 上还没有，自己描一遍。 */
export function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
