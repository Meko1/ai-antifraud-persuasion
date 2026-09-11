import { startServer } from './tests/e2e/server.mjs';
import { findChrome, launchChrome, connect, newPage, evaluate, waitFor } from './tests/e2e/cdp.mjs';
const server = await startServer();
const chrome = await launchChrome(findChrome());
const cdp = await connect(chrome.wsUrl);
const { call } = await newPage(cdp);
const S = (ms) => new Promise(r => setTimeout(r, ms));
try {
  await call('Page.navigate', { url: `${server.base}/?from=miaoxiang` });
  await waitFor(call, `document.getElementById('entry')?.classList.contains('on')`, '入口卡', 25000);
  // 照搬 开局前钉住老邵()
  await call('Page.navigate', { url: `${server.base}/` });
  await waitFor(call, `document.readyState === 'complete'`, '首屏');
  await evaluate(call, `window.__钉 = 1`);
  await evaluate(call, `sessionStorage.removeItem('aap.game.v1');
    sessionStorage.removeItem('aap.transfer.seen');
    sessionStorage.setItem('aap.pick.sid', 'shao')`);
  await call('Page.reload');
  await waitFor(call, `!window.__钉 && document.readyState === 'complete'`, '重载后的新文档', 25000);
  await waitFor(call, `document.getElementById('transfer')?.classList.contains('on')`, '转账屏', 25000);
  await evaluate(call, `document.getElementById('transferGo').click()`);
  await waitFor(call, `!document.getElementById('transferHandoff').hidden`, '拦截面');
  await waitFor(call, `!document.getElementById('handoffGo').hasAttribute('aria-disabled')`, '解锁');
  await evaluate(call, `document.getElementById('handoffGo').click()`);
  await waitFor(call, `document.getElementById('chat')?.classList.contains('on')`, '对话', 25000);
  await waitFor(call, `document.querySelectorAll('#thread .msg').length >= 2`, '开场两条', 30000);

  // 复刻真脚本那段生切屏
  for (const id of ['opening', 'home', 'chat']) {
    await evaluate(call, `document.querySelectorAll('.screen').forEach(s => s.classList.toggle('on', s.id === '${id}'))`);
    await S(300);
  }
  console.log('生切三屏后：', await evaluate(call, `JSON.stringify({屏:document.querySelector('.screen.on')?.id})`));
  console.log('开抽屉前：', await evaluate(call, `JSON.stringify({们:document.querySelectorAll('#thread .msg.them').length})`));
  await evaluate(call, `document.getElementById('openMethods').click()`);
  await waitFor(call, `!!document.querySelector('.sheet')`, '抽屉');
  await S(400);
  await evaluate(call, `document.querySelector('.sheet-cancel')?.click()`);
  await waitFor(call, `!document.querySelector('.sheet')`, '抽屉收起');
  await S(400);
  console.log('关抽屉后：', await evaluate(call, `JSON.stringify({
    sheetHostHidden: document.getElementById('sheetHost')?.hidden,
    sayDisabled: document.getElementById('say')?.disabled,
    sendDisabled: document.getElementById('send')?.disabled,
    inert: document.getElementById('chat')?.inert,
    屏: document.querySelector('.screen.on')?.id,
  })`));
  // 复刻 存图() 的设备覆盖
  await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await S(300);
  const before = await evaluate(call, `document.querySelectorAll('#thread .msg.them').length`);
  await evaluate(call, `(() => {
    const el = document.getElementById('say');
    el.value = '邵叔，这就是个骗局，您千万别再交钱了。';
    el.dispatchEvent(new Event('input', { bubbles: true }));
    document.getElementById('composer').requestSubmit();
  })()`);
  await S(8000);
  console.log('打一轮后：', await evaluate(call, `JSON.stringify({们:document.querySelectorAll('#thread .msg.them').length,我:document.querySelectorAll('#thread .msg.me').length})`), 'before=', before);
} finally { cdp.close(); chrome.kill(); server.stop(); }
