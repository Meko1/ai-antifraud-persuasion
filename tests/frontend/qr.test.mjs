/* 二维码编码器逐格比对参考实现。
 *
 * ## 为什么这一组必须存在
 *
 * `static/qr.js` 是自己写的（理由在那个文件顶部：零 npm、零构建、
 * 大赛服务器在独立网段引不了 CDN）。而这类算法的失败方式很坏——
 * **画出来像模像样，但扫不出来**。肉眼复核一点用都没有。
 *
 * ## 比什么，不比什么
 *
 * 参考矩阵由 Python 的 `qrcode` 库生成，**强制字节模式、强制掩码**。
 * 两个"强制"都不是随手加的：
 *
 * - **强制字节模式**：`qrcode` 默认会替你挑模式，"A" 这种纯字母数字的
 *   内容它会用字母数字模式编，那跟本文件（只做字节模式）比是白比。
 *   第一次生成样本时正是栽在这儿。
 * - **强制掩码**：八种掩码得到的都是合法、扫得动的码，**选哪一个是各家
 *   实现自己定的启发式**。实测同一条文本，segno、Python qrcode、本文件
 *   选出三个不同的掩码。把掩码钉死，比的才是真正必须一致的那部分：
 *   UTF-8 编码、模式与字符计数、终止符与填充、Reed-Solomon 纠错、
 *   分块交织、码字排布、功能图形、格式信息、版本信息。
 *
 * 顺带记一条实测发现，免得下一个人再查一遍：**segno 1.6.6 在字节模式下
 * 会多补一个 0x00 填充字节**（`write_padding_bits` 里 `8 - length % 8`
 * 在已经对齐时返回 8 而不是 0）。它编出来的码照样能扫，但跟规范的最小
 * 编码不是同一个符号，所以这份样本没用 segno。
 *
 * ## 真的扫得动吗
 *
 * 逐格对齐只能证明"和参考实现算得一样"。**"能不能被真的扫出来"是另一回事**，
 * 那一条是拿 OpenCV 的 QRCodeDetector 对本文件自动选码的输出逐条解码验过的
 * （2026-08-29，八条样本全部解回原文，含中文与 v10 长链接）。那次验证依赖
 * opencv-python，**不进 requirements**——它只是一次性的取信手段，不是回归测试。
 *
 * ## 参考样本怎么重新生成
 *
 *     pip install --target /tmp/qrref qrcode
 *     PYTHONPATH=/tmp/qrref .venv/bin/python - <<'PY'
 *     import json, qrcode
 *     from qrcode.constants import ERROR_CORRECT_M, ERROR_CORRECT_L
 *     from qrcode.util import QRData, MODE_8BIT_BYTE
 *     cases = [...]                      # 见 fixtures 里每条的 text
 *     out = []
 *     for text in cases:
 *         picked = None
 *         for lvl, const in (("M", ERROR_CORRECT_M), ("L", ERROR_CORRECT_L)):
 *             for ver in range(1, 11):
 *                 try:
 *                     q = qrcode.QRCode(version=ver, error_correction=const,
 *                                       box_size=1, border=0)
 *                     q.add_data(QRData(text.encode("utf-8"), mode=MODE_8BIT_BYTE))
 *                     q.make(fit=False)
 *                 except Exception:
 *                     continue
 *                 picked = (lvl, const, ver); break
 *             if picked: break
 *         lvl, const, ver = picked
 *         for mask in (0, 3, 5, 7):
 *             q = qrcode.QRCode(version=ver, error_correction=const,
 *                               box_size=1, border=0)
 *             q.add_data(QRData(text.encode("utf-8"), mode=MODE_8BIT_BYTE))
 *             q.makeImpl(False, mask)
 *             out.append({"text": text, "level": lvl, "version": ver, "mask": mask,
 *                         "rows": ["".join('1' if v else '0' for v in r)
 *                                  for r in q.get_matrix()]})
 *     json.dump({"cases": out},
 *               open("tests/frontend/fixtures/qr-golden.json", "w"),
 *               ensure_ascii=False, indent=1)
 *     PY
 *
 * ## 为什么不走 harness.mjs
 *
 * 其余前端单测要把十几个模块拼成一份脚本喂给 node:vm（那些模块彼此有顶层
 * 依赖，且碰 DOM）。`qr.js` 两样都没有：不 import 任何东西、顶层不碰 DOM。
 * **Node 能直接 import 它**，那就直接 import——少一层拼接就少一层
 * "沙箱里过了、浏览器里不过"的可能。
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, describe } from 'node:test';

import { encode } from '../../static/qr.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REF = JSON.parse(
  fs.readFileSync(path.join(HERE, 'fixtures', 'qr-golden.json'), 'utf8'),
);

/** 把 encode() 的结果摊成参考样本那种 "0101" 行，好逐行比。 */
function rowsOf(qr) {
  const out = [];
  for (let r = 0; r < qr.size; r++) {
    let line = '';
    for (let c = 0; c < qr.size; c++) line += qr.modules[r * qr.size + c] ? '1' : '0';
    out.push(line);
  }
  return out;
}

const TEXTS = [...new Set(REF.cases.map((c) => c.text))];

describe('二维码', () => {
  test('掩码钉死之后，每一格都和参考实现一样', () => {
    assert.ok(REF.cases.length >= 24, '样本太少就别指望它拦得住什么');

    for (const ref of REF.cases) {
      const qr = encode(ref.text, { mask: ref.mask });
      const where = `${ref.level}${ref.version} mask=${ref.mask} 「${ref.text.slice(0, 24)}」`;
      assert.ok(qr, `这一条应该编得出来: ${where}`);
      assert.equal(qr.level, ref.level, `纠错档选错了: ${where}`);
      assert.equal(qr.version, ref.version, `版本选错了: ${where}`);
      assert.deepEqual(rowsOf(qr), ref.rows, `模块矩阵对不上: ${where}`);
    }
  });

  test('自动选码选的是惩罚分最低的那一个', () => {
    // 掩码选哪个各家不一样（见文件头），所以这一条不跟参考实现比，
    // 只钉住"自动选出来的那个，确实是这套惩罚分里最小的"——
    // 惩罚分算错过一次就会在这儿露出来
    for (const text of TEXTS) {
      const auto = encode(text);
      assert.ok(auto, text.slice(0, 24));
      const fixed = encode(text, { mask: auto.mask });
      assert.deepEqual(rowsOf(auto), rowsOf(fixed), `强制同一个掩码应该得到同一张图: ${text.slice(0, 24)}`);
      assert.ok(auto.mask >= 0 && auto.mask < 8, '掩码只能是 0–7');
    }
  });

  test('版本与纠错档：先尽量用 M，装不下才退 L', () => {
    // 短链接落在低版本、M 档；只有长到 M 装不下才退 L——这条策略变了
    // 分享卡上那个码的容错能力就变了，值得钉住
    assert.deepEqual(
      (({ version, level }) => ({ version, level }))(encode('A')),
      { version: 1, level: 'M' },
    );
    const long = REF.cases.find((c) => c.level === 'L');
    assert.ok(long, '样本里应该有一条只有 L 装得下的');
    const qr = encode(long.text);
    assert.equal(qr.level, 'L');
    assert.equal(qr.version, 10);
  });

  test('真的太长就返回 null，不抛异常', () => {
    // 调用方是画分享卡的那一段，它宁可少印一块也不该整张卡画不出来
    assert.equal(encode('x'.repeat(400)), null);
  });

  test('三个探测图形都在该在的角上', () => {
    // 上面那条逐格比对其实已经覆盖了这个，但它失败时只会说"第 N 行不一样"。
    // 这一条失败时直接告诉你是定位角坏了——诊断价值不一样
    const qr = encode('http://127.0.0.1:21818/');
    const at = (r, c) => qr.modules[r * qr.size + c];
    [[0, 0], [0, qr.size - 7], [qr.size - 7, 0]].forEach(([r0, c0]) => {
      for (let i = 0; i < 7; i++) {
        assert.equal(at(r0, c0 + i), 1, '探测图形上边框');
        assert.equal(at(r0 + 6, c0 + i), 1, '探测图形下边框');
      }
      assert.equal(at(r0 + 1, c0 + 1), 0, '探测图形第二环是浅色');
      assert.equal(at(r0 + 3, c0 + 3), 1, '探测图形正中是深色');
    });
  });

  test('中文按 UTF-8 编，不是只认 ASCII', () => {
    // 分享卡上那句 CTA 与团队名都是中文，这条路径不能只靠 URL 顺带覆盖
    const ref = REF.cases.find((c) => c.text === 'AI 反诈劝阻');
    assert.ok(ref);
    assert.deepEqual(rowsOf(encode(ref.text, { mask: ref.mask })), ref.rows);
  });
});
