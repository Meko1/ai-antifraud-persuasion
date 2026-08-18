# AI 反诈劝阻

> AI 扮演一位已被荐股骗局洗脑、正准备转账 30 万的客户。你是他的投资顾问，有 12 轮对话劝住他。

一个以游戏化方式呈现的 AI 投顾话术训练器。

完整的产品定位、人物关系、剧情、业务流程、判分机制与技术架构见
[项目完整介绍](docs/PROJECT-INTRODUCTION.md)。

同类 AI 训练产品对比、业务逻辑问题与分阶段改进建议见
[同类产品研究与业务逻辑审计](docs/COMPETITIVE-RESEARCH-AND-BUSINESS-AUDIT.md)。

---

## 当前状态

当前版本已经端到端可玩，包含微信式冷开场、8 种老陈人格、12 轮自由文本对局、
结构化判分、输出安全层、五种终态、逐轮复盘、分享卡和可选的 Redis 全局统计。
规划中的“接话”短练习与异议题库尚未实现，详见项目完整介绍的“当前完成度”。

## 目录结构

```
.
├── install.sh          生命周期脚本 · 安装（幂等）
├── start.sh            生命周期脚本 · 启动（健康检查通过才返回 0）
├── stop.sh             生命周期脚本 · 停止（未运行时也返回 0）
├── package.sh          打包为符合部署规范的 ZIP，含自检
├── requirements.txt
├── CONTEXT.md          领域术语表（只定义语言，不含实现）
├── docs/
│   ├── TECH-DESIGN.md  技术方案 · 施工图
│   ├── PROJECT-INTRODUCTION.md  产品、角色与核心业务完整介绍
│   └── adr/            五条不可轻易反转的决策及其理由
├── app/
│   ├── config.py       环境变量配置，密钥不入源码
│   ├── llm.py          大模型 provider 抽象（内网网关 / 公网接口可切换）
│   ├── engine.py       一轮对局编排、流式事件与多级降级
│   ├── scoring.py      确定性判分、情绪档位与结局状态机
│   ├── persona.py      8 种老陈人格变体与开场白
│   └── main.py         FastAPI 入口
└── static/             微信式对局前端、复盘与分享卡
```

对局引擎怎么做，先读 [docs/TECH-DESIGN.md](docs/TECH-DESIGN.md)；动手改判分或流式粒度之前，先读 [docs/adr/](docs/adr/)——那里有几条看起来像 bug 的设计。

## 本地运行

需要 **Python 3.10+**。

Windows PC（PowerShell）：

```powershell
.\install-local.ps1 -Dev
.\start-local.ps1
Start-Process http://127.0.0.1:21818/
```

停止本地服务：

```powershell
.\stop-local.ps1
```

`install-local.ps1` 会创建 / 复用 `.venv`，安装依赖，并在缺少 `.env` 时从
`.env.example` 生成本地配置；若 `STATE_SIGNING_SECRET` 为空，也会自动生成一个仅供
本机测试使用的签名密钥。没有配置大模型 Key 时，开局、健康检查和基础页面仍可用；
打一轮对话时模型调用会走现有降级链路，用兜底台词完成本地链路验证。需要真实模型
效果时，再填写 `.env` 中对应的 `*_LLM_*` 变量。

Linux / 部署平台：

```bash
./stop.sh && ./install.sh && ./start.sh
open http://127.0.0.1:21818/
```

## 关键约束

| 项 | 值 | 说明 |
|---|---|---|
| 对外端口 | **21818** | 平台强制固定，改了作品打不开 |
| 生命周期顺序 | `stop → install → start` | 平台固定执行顺序 |
| 脚本位置 | ZIP 根目录 | 不能多套一层项目目录 |
| 目标系统 | CentOS 7.9 | 自带 Python 3.6，**需预装 3.10+** |
| Redis | 5.0.14 | 以实际环境为准 |

### 为什么用 plain uvicorn 而不是 `uvicorn[standard]`

`uvicorn[standard]` 会拉入 uvloop 与 httptools，这两个包在 CentOS 7.9 的老 gcc 上需要
现场编译，是部署失败的高频原因。纯 h11 实现的性能足够本作品使用。

### 为什么 PID 文件放在 `~/.ai-antifraud-persuasion/`

重新部署时平台执行的是**新服务包**里的 `stop.sh`，而解压目录每次都会变。
PID 文件若放在解压目录，新版本的 `stop.sh` 就找不到旧版本的进程。
放在 HOME 下的固定路径才能跨版本稳定定位。

`stop.sh` 还有一层兜底：PID 文件丢失时从端口 21818 反查监听进程，
并用进程特征 `app.main:app` 二次确认，确保不会误杀同机器上的其他作品。

## 部署到目标服务器

```bash
./package.sh          # 生成 dist/ai-antifraud-persuasion-<时间戳>.zip
```

上传前请确认：

- [ ] `.env` 未进包（`package.sh` 会自动校验并拦截）
- [ ] 服务器已装 Python 3.10+
- [ ] 访问 `/healthz?probe=1` 确认大模型网关连通性
- [ ] 已跑过 `security-skill` 源码安全扫描

## 配置

复制 `.env.example` 为 `.env` 后填写。**`.env` 绝不可提交入库**。

拿到服务器后第一件事是访问 `/healthz?probe=1`：它会探测当前 provider 的网关连通性。
若内网网关不可达，把 `LLM_PROVIDER` 改成 `public` 即可切到公网模型，无需改代码。
