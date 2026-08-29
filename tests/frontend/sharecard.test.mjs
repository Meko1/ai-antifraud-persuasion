/* 分享卡卡尾那一块：回到作品的那条路。
 *
 * 就绪度审计 P1-9：**一张传出去的图片如果不能把人带回作品，它就只是一张
 * 图片**。2026-08-29 补上二维码 + 地址之后，这一组守着它别再掉。
 *
 * 画出来长什么样测不了（canvas 在 node:vm 里是替身，见 harness.mjs 顶部
 * "测不了什么"那一段），所以这里分两路：
 *
 * · `shareLabel` 是纯函数，直接调；
 * · "卡尾还在不在"走源码断言——跟 review.test.mjs 里"复盘第一屏五块"
 *   那一组是同一个做法，理由也一样：删掉一整块不会让任何断言变红。
 *
 * 二维码本身编得对不对由 qr.test.mjs 逐格对着参考实现比，不在这儿重复。
 */

import assert from 'node:assert/strict';
import { test, describe } from 'node:test';

import { bodyOf, loadApp, sourceOf } from './harness.mjs';

const app = loadApp();
const HISTORY = sourceOf('history.js');

describe('分享卡 · 地址那一行', () => {
  test('协议头去掉，末尾那个斜杠也去掉', () => {
    // 协议头对读的人没有信息量，占的却是最贵的那一行宽度。
    // 真正编进二维码的仍然是完整地址——那一头不能省，省了扫出来不是链接
    assert.equal(app.shareLabel('http://10.20.30.40:21818/'), '10.20.30.40:21818');
    assert.equal(app.shareLabel('https://demo.example.com/w/12'), 'demo.example.com/w/12');
  });

  test('太长就截断，不让它压到二维码上', () => {
    const long = `https://demo.example.com/${'x'.repeat(80)}`;
    const label = app.shareLabel(long);
    assert.ok(label.length <= 42, `截断后应该不超过 42 个字符，实际 ${label.length}`);
    assert.ok(label.endsWith('…'), '截断了就要看得出来被截断了');
  });

  test('短地址一个字都不动', () => {
    assert.equal(app.shareLabel('demo.example.com'), 'demo.example.com');
  });
});

describe('分享卡 · 卡尾这一块不许再掉', () => {
  const body = bodyOf(HISTORY, 'makeCard');

  test('二维码、作品名、地址三样都画了', () => {
    assert.match(body, /drawQR\(/, '卡尾要画二维码');
    assert.match(body, /shareUrl\(\)/, '二维码的地址取自 shareUrl()');
    assert.match(body, /shareLabel\(/, '地址还要以人能读的形式印一行');
    assert.match(body, /AI 反诈劝阻/, '卡上要有作品名');
  });

  test('二维码画不出来时，地址那一行仍然印', () => {
    // 唯一会画不出来的情况是地址长到十版都装不下（实际碰不到）。
    // 真碰到了，卡尾要退化成"只有链接"，不能退化成"什么都没有"
    assert.match(body, /hasQR \?/, '要区分画得出来和画不出来两种写法');
    assert.match(body, /if \(url\) \{/, '地址那一行的条件是有没有地址，不是有没有码');
  });
});

describe('分享卡 · 地址从哪来', () => {
  test('默认取当前访问地址，不写死任何域名', () => {
    const body = bodyOf(HISTORY, 'shareUrl');
    assert.match(body, /location\.origin/, '默认值应该是当前访问地址');
    assert.match(body, /af-share-url/, '要留一个 meta 覆盖口');
    // 写死一个域名会在换机器部署时静默指错地方，而分享卡是最不容易被复核的
    // 那一块——它只在别人手机上出现
    assert.doesNotMatch(body, /https?:\/\/[\w.]+\.\w+/, '不许写死任何具体域名');
  });
});
