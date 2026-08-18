# AI 反诈劝阻

> 客户被荐股骗局套住、正要转账时，投资顾问该怎么开口。

一套可交互的**投顾专业训练系统**。AI 扮演你自己的客户——一位已被洗脑、
正准备大额转账的股民；你有 12 轮，而且**只能问，不能荐**。

产品身份（核心矛盾 / 成功标准 / 不做什么）写在 [docs/POSITIONING.md](docs/POSITIONING.md)。
**提需求之前先拿那一页量一遍**，它是尺子。

---

## 当前状态

端到端可玩：12 轮状态机、结构化判分（七把钥匙 + 三项话术失误 + 两条合规红线）、
输出安全层、12 个人格变体、复盘与分享卡全部在线。`python -m pytest` 332 个测试全绿。

判分完全由程序的规则表求值，模型只负责演（ADR-0001）——所以同一串输入
两次跑出同一个分，参数由两万局蒙特卡洛标定。

**两个场景**：荐股群（贪）与冒充公检法（怕）。它们共用同一张判分表，
而四个情绪档位上的最优解**全不一样**——这是"能力可迁移，不是背下一个剧本"
唯一能被直接验证的地方，由 `tests/test_balance.py` 两条测试守着。

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
│   ├── POSITIONING.md  产品身份 · 核心矛盾 / 成功标准 / 不做什么
│   ├── TECH-DESIGN.md  技术方案 · 施工图
│   ├── HANDOFF.md      交接说明 · 踩过的坑
│   └── adr/            五条不可轻易反转的决策及其理由
├── app/
│   ├── scoring.py      判分引擎（纯函数，可蒙特卡洛离线重跑）
│   ├── scenario.py     场景：剧本 / 人格 / 台词 / 界面素材 / 效力矩阵覆写
│   ├── gateway.py      三个模型操作与全部提示词
│   ├── persona.py      12 个人格变体（两个场景各一组）
│   ├── safety.py       输出安全层
│   └── main.py         FastAPI 入口
└── static/             首页工作台 / 聊天页 / 复盘（原生三件，零构建）
```

新会话先读 [docs/HANDOFF.md](docs/HANDOFF.md)；判断一个改动该不该做，读
[docs/POSITIONING.md](docs/POSITIONING.md)；动手改判分或流式粒度之前，先读
[docs/adr/](docs/adr/)——那里有几条看起来像 bug 的设计。

## 本地运行

需要 **Python 3.10+**。

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
