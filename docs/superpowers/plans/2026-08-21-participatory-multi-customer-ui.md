# 多客户参与式反诈界面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已确认的高保真稿落入真实应用，提供随机客户接入、数据驱动的异常事件页、客户档案、沉浸式对话，以及五种可信终局复盘。

**Architecture:** 保留现有 `/api/game/start` 随机选场景、签名 token 固定同局身份、SSE 对话和 `/api/stats` 百分位实现。前端新增 `assignment → opening → home → chat → review` 状态流，所有客户、金额、性别、诈骗类型和结果文案继续来自 `scenario.payload()`，不在界面中复制第二份业务真相。

**Tech Stack:** FastAPI、原生 HTML/CSS/JavaScript、pytest、Chrome headless。

---

### Task 1: 建立新增页面契约

**Files:**
- Modify: `static/index.html`
- Verify: 临时 Node 契约命令，不新增测试源码

- [ ] **Step 1: 运行缺失页面的失败契约**

```powershell
node -e "const fs=require('fs');const s=fs.readFileSync('static/index.html','utf8');for(const id of ['assignment','opening'])if(!s.includes('id=\"'+id+'\"'))throw Error('missing '+id)"
```

Expected: FAIL with `missing assignment`。

- [ ] **Step 2: 增加随机接入与异常事件页面**

在 `static/index.html` 中增加：

```html
<section class="screen on" id="assignment" aria-live="polite">...</section>
<section class="screen" id="opening">...</section>
```

并将现有 `home` 调整为不默认展示的客户档案页。异常事件页只呈现服务端下发的客户名称、金额与异常摘要，不提前泄露骗局答案。

- [ ] **Step 3: 重新运行契约**

Expected: PASS，无输出。

### Task 2: 串联随机客户与同局固定状态

**Files:**
- Modify: `static/app.js`
- Modify: `app/scenario.py`
- Modify: `tests/test_api.py`
- Verify: 临时 Node 契约命令

- [ ] **Step 1: 运行新生命周期的失败契约**

```powershell
node -e "const fs=require('fs');const s=fs.readFileSync('static/app.js','utf8');for(const x of ['paintOpening','showScreen(\'assignment\')','startNewClient'])if(!s.includes(x))throw Error('missing '+x)"
```

Expected: FAIL with `missing paintOpening`。

- [ ] **Step 2: 实现状态流**

在 `Scenario.payload()` 中增加 `incident` 事件文案，避免前端按场景 ID 硬编码。增加 `paintOpening()`，在 `/api/game/start` 返回后铺设随机客户事件，并保证同一 `game.token` 和 `SCENE.id` 从事件页持续到复盘结束。`startNewClient()` 仅在用户主动开始新客户时刷新整局；返回档案和对话不会重新请求客户。

- [ ] **Step 3: 补齐加载失败状态**

随机接入失败时在 `assignment` 内显示“暂时无法接入客户”和“重新连接”，不进入空白聊天页。

- [ ] **Step 4: 重新运行生命周期契约**

Expected: PASS，无输出。

### Task 3: 重构五档结果摘要

**Files:**
- Modify: `static/app.js`
- Modify: `static/style.css`
- Verify: 临时 Node 契约命令

- [ ] **Step 1: 运行终局语义的失败契约**

```powershell
node -e "const fs=require('fs');const s=fs.readFileSync('static/app.js','utf8');for(const x of ['resultAmount','暂未转出','不参与排行'])if(!s.includes(x))throw Error('missing '+x)"
```

Expected: FAIL with `missing resultAmount`。

- [ ] **Step 2: 实现五档金额与标签**

实现统一结果模型：劝住显示全部保住；拦下显示扣除试转金额后的保住金额；拖住显示总额但标签为“暂未转出，风险尚未解除”；转账显示 `¥0`；被拉黑显示“状态未知”。

- [ ] **Step 3: 调整百分位规则**

完整对局继续使用 `/api/stats` 的同场景分桶；被拉黑提前出局时显示“不参与排行”，且不写入 `_percentile`。

- [ ] **Step 4: 重构复盘首屏**

复盘首屏依次显示结局标题、资金状态、最终信任、使用轮次、同场景百分位和关键转折，详细 K 线、合规、逐轮证据与本机历史保留在下方。

- [ ] **Step 5: 重新运行终局契约**

Expected: PASS，无输出。

### Task 4: 落地高保真视觉系统

**Files:**
- Modify: `static/style.css`
- Verify: Chrome headless 截图与 Impeccable detector

- [ ] **Step 1: 替换全局视觉令牌**

采用暖灰纸面、墨蓝主操作、锈红风险、墨绿成功、暖金未决；统一 12–16px 圆角、柔和中性阴影和清晰焦点环。

- [ ] **Step 2: 完成五个页面的响应式排版**

移动端保持全屏原生体验；桌面端限制为 480px 应用画布。对话页不展示数字信任度，只显示外显情绪和剩余轮次。

- [ ] **Step 3: 覆盖交互状态**

实现按钮 hover/focus/disabled、加载、错误、空百分位、减少动态效果以及安全区适配。

- [ ] **Step 4: 机械反模板检查**

```powershell
node C:\Users\Administrator\.agents\skills\impeccable\scripts\detect.mjs --json static/index.html static/style.css static/app.js
```

Expected: 无 error；所有 warning 均已人工核对或修复。

### Task 5: 回归、浏览器验收与发布

**Files:**
- Modify: `docs/superpowers/plans/2026-08-21-participatory-multi-customer-ui.md`
- Verify: 全量测试、浏览器截图、Git

- [ ] **Step 1: 运行 Python 全量测试**

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Expected: 全部通过。

- [ ] **Step 2: 启动应用并验证页面**

```powershell
$env:STATE_SIGNING_SECRET='local-ui-verification'; .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 21818
```

验证随机接入、周淑琴档案、对话、五档复盘、陈国栋金额，以及 390px 和 1280px 两种视口无横向溢出。

- [ ] **Step 3: 复核差异**

```powershell
git diff --check
git diff --stat
git status --short
```

- [ ] **Step 4: 提交并推送**

```powershell
git add static/index.html static/style.css static/app.js docs/superpowers/plans/2026-08-21-participatory-multi-customer-ui.md
git commit -m "feat: implement participatory multi-customer experience"
git push origin feature_support_windows
```
