/* 二维码编码器（字节模式，版本 1–10，纠错 M/L）。
 *
 * ## 为什么自己写一个
 *
 * 分享卡要能把人带回作品，那就得印一个码。而这个仓库的两条硬约束把所有
 * 现成方案都挡在门外：**零 npm 依赖、零构建步骤**（见 tests/frontend/harness.mjs
 * 顶部那三条），CDN 引一个 qrcode.js 更不行——大赛服务器在独立网段，
 * 一个连不上的 CDN 会让分享卡当场少一块。
 *
 * 所以这里是一份自带的实现。**范围被刻意压到最小**：只做字节模式、
 * 只支持版本 1–10、只用 M 与 L 两档纠错。够印一条 URL，不够的场合
 * `encode()` 返回 null，调用方自己决定怎么办（history.js 的做法是
 * 只印链接文字，不印码——少一块，不错一块）。
 *
 * 版本卡在 10 有个具体好处：再往上（版本 14+）要处理 3/4 位的剩余位与
 * 更长的字符计数字段，而 10-L 已经能装 78 字节，是任何一个部署 URL 的
 * 三倍还多。**不为一个不会发生的场合背一张四十行的表。**
 *
 * ## 正确性怎么保证
 *
 * 这类算法的失败方式很坏：**画出来是个像模像样的方块，但扫不出来**——
 * 肉眼复核一点用都没有。所以 tests/frontend/qr.test.mjs 拿 segno（Python
 * 那边的成熟实现）生成的模块矩阵当黄金样本逐格比对，样本落在
 * tests/frontend/fixtures/qr-golden.json 里，生成脚本写在那个文件的注释里。
 *
 * 实现遵循 ISO/IEC 18004。掩码惩罚与 Reed-Solomon 这两段的写法参考了
 * Nayuki 的 QR Code generator（公有领域）所采用的通用算法结构。
 */

// 纠错等级：值是格式信息里那两位的编码，不是"第几档"。L=01, M=00。
const ECL = {
  L: { bits: 1, ecc: [-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18], blocks: [-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4] },
  M: { bits: 0, ecc: [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26], blocks: [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5] },
};

const MAX_VERSION = 10;

/** 这个版本一共有多少个能放数据的模块（扣掉全部功能图形）。 */
function rawDataModules(ver) {
  let result = (16 * ver + 128) * ver + 64;
  if (ver >= 2) {
    const numAlign = Math.floor(ver / 7) + 2;
    result -= (25 * numAlign - 10) * numAlign - 55;
    if (ver >= 7) result -= 36; // 版本信息两块 6×3
  }
  return result;
}

/** 扣掉纠错码之后，真正能装数据的字节数。 */
function dataCodewords(ver, ecl) {
  return Math.floor(rawDataModules(ver) / 8) - ecl.ecc[ver] * ecl.blocks[ver];
}

/** 校正图形的中心坐标。算出来的，不是查表——公式在 ISO 18004 附录 E。 */
function alignPositions(ver) {
  if (ver === 1) return [];
  const numAlign = Math.floor(ver / 7) + 2;
  const size = ver * 4 + 17;
  const step = Math.ceil((ver * 4 + 4) / (numAlign * 2 - 2)) * 2;
  const result = [6];
  for (let pos = size - 7; result.length < numAlign; pos -= step) result.splice(1, 0, pos);
  return result;
}

// ── GF(256) 与 Reed-Solomon ────────────────────────────────────────────────

function gfMul(x, y) {
  let z = 0;
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11d); // 本原多项式 x^8+x^4+x^3+x^2+1
    z ^= ((y >>> i) & 1) * x;
  }
  return z & 0xff;
}

function rsDivisor(degree) {
  const result = new Array(degree).fill(0);
  result[degree - 1] = 1;
  let root = 1;
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < result.length; j++) {
      result[j] = gfMul(result[j], root);
      if (j + 1 < result.length) result[j] ^= result[j + 1];
    }
    root = gfMul(root, 0x02);
  }
  return result;
}

function rsRemainder(data, divisor) {
  const result = new Array(divisor.length).fill(0);
  for (const b of data) {
    const factor = b ^ result.shift();
    result.push(0);
    divisor.forEach((coef, i) => { result[i] ^= gfMul(coef, factor); });
  }
  return result;
}

// ── 码字：分块、加纠错、交织 ────────────────────────────────────────────────

function addEccAndInterleave(data, ver, ecl) {
  const numBlocks = ecl.blocks[ver];
  const blockEccLen = ecl.ecc[ver];
  const rawCodewords = Math.floor(rawDataModules(ver) / 8);
  const numShortBlocks = numBlocks - (rawCodewords % numBlocks);
  const shortBlockLen = Math.floor(rawCodewords / numBlocks);

  const blocks = [];
  const divisor = rsDivisor(blockEccLen);
  for (let i = 0, k = 0; i < numBlocks; i++) {
    const len = shortBlockLen - blockEccLen + (i < numShortBlocks ? 0 : 1);
    const dat = data.slice(k, k + len);
    k += len;
    const ecc = rsRemainder(dat, divisor);
    // 短块补一个占位 0，好让下面的交织能按同一个列宽走；这一位在交织时跳过
    if (i < numShortBlocks) dat.push(0);
    blocks.push(dat.concat(ecc));
  }

  const result = [];
  for (let i = 0; i < blocks[0].length; i++) {
    blocks.forEach((block, j) => {
      if (i !== shortBlockLen - blockEccLen || j >= numShortBlocks) result.push(block[i]);
    });
  }
  return result;
}

/** 文本 → 数据码字（含模式指示符、字符计数、终止符与填充）。装不下返回 null。 */
function toCodewords(bytes, ver, ecl) {
  const capacity = dataCodewords(ver, ecl);
  // 字节模式：4 位模式 + 8 位计数（版本 1–9）/ 16 位（版本 10+）
  const countBits = ver < 10 ? 8 : 16;
  const needed = Math.ceil((4 + countBits + bytes.length * 8) / 8);
  if (needed > capacity) return null;

  const bits = [];
  const push = (value, len) => {
    for (let i = len - 1; i >= 0; i--) bits.push((value >>> i) & 1);
  };
  push(0b0100, 4);
  push(bytes.length, countBits);
  for (const b of bytes) push(b, 8);

  push(0, Math.min(4, capacity * 8 - bits.length)); // 终止符
  while (bits.length % 8 !== 0) bits.push(0);

  const words = [];
  for (let i = 0; i < bits.length; i += 8) {
    words.push(bits.slice(i, i + 8).reduce((acc, bit) => (acc << 1) | bit, 0));
  }
  // 填充字节交替 0xEC / 0x11，标准规定的两个值，不是随手挑的
  for (let pad = 0xec; words.length < capacity; pad ^= 0xec ^ 0x11) words.push(pad);
  return words;
}

// ── 矩阵 ────────────────────────────────────────────────────────────────────

class Matrix {
  constructor(ver) {
    this.ver = ver;
    this.size = ver * 4 + 17;
    this.modules = new Uint8Array(this.size * this.size);
    this.reserved = new Uint8Array(this.size * this.size); // 功能图形，不许放数据
  }

  get(r, c) { return this.modules[r * this.size + c]; }

  set(r, c, dark) { this.modules[r * this.size + c] = dark ? 1 : 0; }

  fn(r, c, dark) {
    if (r < 0 || c < 0 || r >= this.size || c >= this.size) return;
    this.set(r, c, dark);
    this.reserved[r * this.size + c] = 1;
  }
}

function drawFunctionPatterns(m, ecl) {
  const { size } = m;

  for (let i = 0; i < size; i++) {
    m.fn(6, i, i % 2 === 0); // 定位图形
    m.fn(i, 6, i % 2 === 0);
  }

  // 三个探测图形，连同一圈分隔符（dist 4 那一环恒为浅色）
  [[3, 3], [3, size - 4], [size - 4, 3]].forEach(([cr, cc]) => {
    for (let dr = -4; dr <= 4; dr++) {
      for (let dc = -4; dc <= 4; dc++) {
        const dist = Math.max(Math.abs(dr), Math.abs(dc));
        m.fn(cr + dr, cc + dc, dist !== 2 && dist !== 4);
      }
    }
  });

  const align = alignPositions(m.ver);
  const last = align.length - 1;
  align.forEach((r, i) => align.forEach((c, j) => {
    // 三个角上的校正图形会压到探测图形，跳过
    if ((i === 0 && j === 0) || (i === 0 && j === last) || (i === last && j === 0)) return;
    for (let dr = -2; dr <= 2; dr++) {
      for (let dc = -2; dc <= 2; dc++) {
        m.fn(r + dr, c + dc, Math.max(Math.abs(dr), Math.abs(dc)) !== 1);
      }
    }
  }));

  // 格式信息的位置先占住（内容等选完掩码再写），外加那个恒为深色的模块
  for (let i = 0; i <= 5; i++) m.fn(i, 8, false);
  m.fn(7, 8, false);
  m.fn(8, 8, false);
  m.fn(8, 7, false);
  for (let i = 9; i < 15; i++) m.fn(8, 14 - i, false);
  for (let i = 0; i < 8; i++) m.fn(8, size - 1 - i, false);
  for (let i = 8; i < 15; i++) m.fn(size - 15 + i, 8, false);
  m.fn(size - 8, 8, true);

  if (m.ver >= 7) {
    let rem = m.ver;
    for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25);
    const bits = (m.ver << 12) | rem;
    for (let i = 0; i < 18; i++) {
      const dark = ((bits >>> i) & 1) !== 0;
      const a = size - 11 + (i % 3);
      const b = Math.floor(i / 3);
      m.fn(b, a, dark);
      m.fn(a, b, dark);
    }
  }

  void ecl; // 格式信息在 drawFormatBits 里写，这里只负责占位
}

function drawFormatBits(m, ecl, mask) {
  const data = (ecl.bits << 3) | mask;
  let rem = data;
  for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
  const bits = ((data << 10) | rem) ^ 0x5412; // 标准规定的掩码，防止全零格式串
  const bit = (i) => ((bits >>> i) & 1) !== 0;
  const { size } = m;

  for (let i = 0; i <= 5; i++) m.fn(i, 8, bit(i));
  m.fn(7, 8, bit(6));
  m.fn(8, 8, bit(7));
  m.fn(8, 7, bit(8));
  for (let i = 9; i < 15; i++) m.fn(8, 14 - i, bit(i));

  for (let i = 0; i < 8; i++) m.fn(8, size - 1 - i, bit(i));
  for (let i = 8; i < 15; i++) m.fn(size - 15 + i, 8, bit(i));
}

function drawCodewords(m, words) {
  const { size } = m;
  let i = 0;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5; // 第 6 列是定位图形，整列跳过
    for (let vert = 0; vert < size; vert++) {
      for (let j = 0; j < 2; j++) {
        const c = right - j;
        const upward = ((right + 1) & 2) === 0;
        const r = upward ? size - 1 - vert : vert;
        if (!m.reserved[r * size + c] && i < words.length * 8) {
          m.set(r, c, ((words[i >>> 3] >>> (7 - (i & 7))) & 1) !== 0);
          i++;
        }
      }
    }
  }
}

function maskFn(mask, r, c) {
  switch (mask) {
    case 0: return (c + r) % 2 === 0;
    case 1: return r % 2 === 0;
    case 2: return c % 3 === 0;
    case 3: return (c + r) % 3 === 0;
    case 4: return (Math.floor(c / 3) + Math.floor(r / 2)) % 2 === 0;
    case 5: return ((c * r) % 2) + ((c * r) % 3) === 0;
    case 6: return (((c * r) % 2) + ((c * r) % 3)) % 2 === 0;
    default: return (((c + r) % 2) + ((c * r) % 3)) % 2 === 0;
  }
}

function applyMask(m, mask) {
  for (let r = 0; r < m.size; r++) {
    for (let c = 0; c < m.size; c++) {
      if (!m.reserved[r * m.size + c] && maskFn(mask, r, c)) {
        m.modules[r * m.size + c] ^= 1;
      }
    }
  }
}

// 惩罚分：四条规则，标准里的权重
const N1 = 3;
const N2 = 3;
const N3 = 40;
const N4 = 10;

function finderPatternCount(history) {
  const n = history[1];
  const core = n > 0 && history[2] === n && history[3] === n * 3 && history[4] === n && history[5] === n;
  return (core && history[0] >= n * 4 && history[6] >= n ? 1 : 0)
    + (core && history[6] >= n * 4 && history[0] >= n ? 1 : 0);
}

function addHistory(size, run, history) {
  if (history[0] === 0) run += size; // 边界外当作浅色，补一段
  history.pop();
  history.unshift(run);
}

function terminateAndCount(size, runColor, runLen, history) {
  let len = runLen;
  if (runColor) {
    addHistory(size, len, history);
    len = 0;
  }
  len += size;
  addHistory(size, len, history);
  return finderPatternCount(history);
}

function penalty(m) {
  const { size } = m;
  let result = 0;

  for (let pass = 0; pass < 2; pass++) {
    for (let a = 0; a < size; a++) {
      let runColor = 0;
      let runLen = 0;
      const history = [0, 0, 0, 0, 0, 0, 0];
      for (let b = 0; b < size; b++) {
        const v = pass === 0 ? m.get(a, b) : m.get(b, a);
        if (v === runColor) {
          runLen++;
          if (runLen === 5) result += N1;
          else if (runLen > 5) result++;
        } else {
          addHistory(size, runLen, history);
          if (!runColor) result += finderPatternCount(history) * N3;
          runColor = v;
          runLen = 1;
        }
      }
      result += terminateAndCount(size, runColor, runLen, history) * N3;
    }
  }

  for (let r = 0; r < size - 1; r++) {
    for (let c = 0; c < size - 1; c++) {
      const v = m.get(r, c);
      if (v === m.get(r, c + 1) && v === m.get(r + 1, c) && v === m.get(r + 1, c + 1)) {
        result += N2;
      }
    }
  }

  let dark = 0;
  for (let i = 0; i < m.modules.length; i++) dark += m.modules[i];
  const total = size * size;
  const k = Math.ceil(Math.abs(dark * 20 - total * 10) / total) - 1;
  return result + k * N4;
}

// ── 对外 ────────────────────────────────────────────────────────────────────

/** UTF-8 编码。TextEncoder 在目标浏览器与 node:vm 沙箱里都有，不自己写一份。 */
function utf8(text) {
  if (typeof TextEncoder !== 'undefined') return Array.from(new TextEncoder().encode(text));
  return Array.from(unescape(encodeURIComponent(text)), (ch) => ch.charCodeAt(0));
}

/** 编码一段文本。
 *
 *  先试 M（普通相机在卡片被转发、压缩、缩放之后仍然扫得动），装不下再退 L。
 *  两档都装不下返回 **null**——不抛异常：调用方是画分享卡的那一段，
 *  它宁可少印一块也不该整张卡画不出来。
 *
 *  `mask` 传 0–7 就跳过自动选码，用指定的那个。**这不是给业务用的**，
 *  是给测试用的：掩码选择是各家实现自己定的启发式（见下面那段注释），
 *  而"数据、纠错、码字排布、格式信息"这些必须一致的部分，只有把掩码
 *  钉死才比得了。tests/frontend/qr.test.mjs 靠它逐格对齐参考实现。
 *
 *  @returns {{size: number, modules: Uint8Array, version: number, level: string, mask: number}|null}
 */
export function encode(text, { mask: forcedMask = null } = {}) {
  const bytes = utf8(String(text));
  for (const level of ['M', 'L']) {
    const ecl = ECL[level];
    for (let ver = 1; ver <= MAX_VERSION; ver++) {
      const words = toCodewords(bytes, ver, ecl);
      if (!words) continue;

      const m = new Matrix(ver);
      drawFunctionPatterns(m, ecl);
      drawCodewords(m, addEccAndInterleave(words, ver, ecl));

      // 八个掩码全试一遍，取惩罚分最低的那个。**不能省成固定掩码**：
      // 惩罚分低意味着大片同色与假探测图形少，直接决定扫得动扫不动。
      //
      // 一句话说清这里为什么不跟别家逐位一致：**惩罚分是启发式，各家实现
      // 选出来的掩码本来就会不一样**（实测 segno、Python qrcode、本文件
      // 三家在同一条文本上选出三个不同的掩码），而八种掩码得到的都是合法、
      // 扫得动的码。真正必须一致的是数据、纠错、码字排布与格式信息——
      // 那几样已经和参考实现逐格对齐，见测试。
      const clean = m.modules.slice();
      let best = -1;
      if (forcedMask != null) {
        best = forcedMask;
      } else {
        let bestScore = Infinity;
        for (let mask = 0; mask < 8; mask++) {
          m.modules.set(clean);
          applyMask(m, mask);
          drawFormatBits(m, ecl, mask);
          const score = penalty(m);
          if (score < bestScore) {
            bestScore = score;
            best = mask;
          }
        }
      }
      m.modules.set(clean);
      applyMask(m, best);
      drawFormatBits(m, ecl, best);

      return { size: m.size, modules: m.modules, version: ver, level, mask: best };
    }
  }
  return null;
}

/** 把码画到 canvas 上。
 *
 *  `size` 是**含静区**的边长：静区是标准要求的四个模块宽的白边，少了它
 *  很多相机就不认。调用方给多大就画多大，模块边长取整避免出现半像素的
 *  灰边——那种灰边在缩略图里会糊成一片，是扫不出来的常见原因。
 *
 *  @returns {boolean} 画没画成（文本太长时返回 false，调用方自己兜底）
 */
export function drawQR(ctx, text, { x, y, size, dark = '#000', light = '#fff' }) {
  const qr = encode(text);
  if (!qr) return false;

  const quiet = 4;
  const cells = qr.size + quiet * 2;
  const scale = Math.max(1, Math.floor(size / cells));
  const drawn = scale * cells;
  const ox = x + Math.floor((size - drawn) / 2);
  const oy = y + Math.floor((size - drawn) / 2);

  ctx.fillStyle = light;
  ctx.fillRect(ox, oy, drawn, drawn);
  ctx.fillStyle = dark;
  for (let r = 0; r < qr.size; r++) {
    for (let c = 0; c < qr.size; c++) {
      if (qr.modules[r * qr.size + c]) {
        ctx.fillRect(ox + (c + quiet) * scale, oy + (r + quiet) * scale, scale, scale);
      }
    }
  }
  return true;
}
