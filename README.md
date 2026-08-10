# AI 反诈劝阻

> AI 扮演一位已被荐股骗局洗脑、正准备转账 30 万的股民，你有 12 轮对话劝住他。

一个 AI 对话游戏项目 · 骨架版本。

---

## 当前状态

骨架阶段，只包含**跑通部署链路**所需的最小内容：健康检查、静态首页、SSE 流式验证。
对局引擎（12 轮状态机 / 结构化判分 / 输出安全层）尚未接入。

## 目录结构

```
.
├── install.sh          生命周期脚本 · 安装（幂等）
├── start.sh            生命周期脚本 · 启动（健康检查通过才返回 0）
├── stop.sh             生命周期脚本 · 停止（未运行时也返回 0）
├── package.sh          打包为符合部署规范的 ZIP，含自检
├── requirements.txt
├── app/
│   ├── config.py       环境变量配置，密钥不入源码
│   ├── llm.py          大模型 provider 抽象（内网网关 / 公网接口可切换）
│   └── main.py         FastAPI 入口
└── static/index.html   部署自检页
```

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
