# Web App

浏览器里跑完整条流水线：贴脚本 → 选模型 → 生成 → 对话式改片 → 发 TikTok。

```
python -m uvicorn web.server:app --port 8787
# 打开 http://127.0.0.1:8787
```

需要 `pip install -e ".[web]"`。

依赖里必须是 **`uvicorn[standard]` 而不是裸 `uvicorn`**：前者会带上 `websockets`，
没有它 uvicorn 完全处理不了 WebSocket 升级，每个 `/ws/...` 请求都会返回 404，
进度条就只能退化成前端轮询（功能还在，但进度不实时）。

等价命令：`pip install fastapi "uvicorn[standard]" python-multipart`。

## 前端技术栈

`web/frontend/` 是一个独立的前端工程：

| | |
|---|---|
| 构建 | Vite 6 + React 18 + TypeScript（strict） |
| 样式 | Tailwind CSS 3 + CSS 变量主题（浅/深） |
| 组件 | shadcn/ui 风格组件（Radix 原语 + CVA），源码在 `src/components/ui/` |
| 图标 | lucide-react |
| 多语言 | react-i18next，中/英两套文案，自动检测 + localStorage 记忆 |
| Toast | sonner |

构建产物输出到 `web/static/dist/`，由 FastAPI 直接托管（`/` 与 `/assets`），
**已提交进仓库**，所以 clone 下来不装 node 也能跑。旧的单文件页面仍在
`/legacy` 作为回退。

```bash
cd web/frontend
npm install
npm run dev        # http://127.0.0.1:5273，/api 与 /ws 代理到 8787
npm run build      # -> web/static/dist
npm run typecheck
```

改文案只动 `src/i18n/locales/{zh,en}.ts`；加厂商只动
`avb/provider_specs.py`（服务端）+ 对应语言文件里的 `vendor.*` 键。

---

## 界面

顶部两个标签页：**创作** / **设置**。设置只管"去哪家拿素材"，创作是一条
五步流水线。

### 创作页的五步

| 步骤 | 干什么 | 花不花钱 |
|---|---|---|
| 1 脚本 | 贴广告文案，选风格/画幅/模型 | 不花钱 |
| 2 确认文案 | **看并改**模型产出的场景口播、字幕与画面提示词 | 不花钱 |
| 3 生成视频 | 确认后才真正调用配音 + 视频模型 | **从这里开始计费** |
| 4 微调 | 自然语言改片，只重渲受影响的场景 | 按重渲场景数计费 |
| 5 发布 | 授权 TikTok 后直传 | — |

为什么要在第 2 步停下来：模型的分镜初稿经常读起来不对，而重新生成八段素材
是真金白银。**先确认再烧钱**是这个流程存在的唯一理由。

第 2 步每一场都可以：改口播/字幕文案、改画面提示词、上下移动、删除，
也能「重新分镜」让模型再跑一次。字幕没有独立输入框——它靠这一行的词级
时间轴驱动，两者是同一份文本，改一个另一个跟着变。

第 4 步的「应用改动并重渲染」开关默认打开：关掉只返回分析结果不动画面。
打开时服务端 diff 出受影响的场景，只重渲这些，其余走缓存。

### 设置页

左侧是 Provider 配置与状态，右侧是一页说明 + 运行日志。
没配视频 Provider 时标签上会挂一个红点，点「生成场景文案」也会被拦下并跳回设置页。

| 区块 | 说明 |
|---|---|
| 视频生成模型 | Seedance / Kling / 即梦 / Veo / Wan / Hailuo / CogVideoX / 自动。选中哪家就显示哪家需要的 key（即梦是 AccessKey+SecretKey 双 key，其余单 key） |
| 模型版本 | 有得选的 provider（如 Seedance 的 pro / mini）在这里挑 |
| 配音 | Edge TTS（免费）/ ElevenLabs / Formant（离线） |
| LLM | DeepSeek 或任意 OpenAI 兼容端点，可改 Base URL 与 model |
| 发布 TikTok | Client Key / Secret / 回调地址（折叠） |

- 已配置的 key 只显示掩码，输入框**留空 = 不修改**，不会清空已有值。
- Key 写入 `.env`（不进 git），模型选择写入 `avb.toml`。
- 保存后立即生效：服务端同步更新 `os.environ` 并重建 LLM client，不用重启。

#### 第 1 步的控件

| 控件 | 说明 |
|---|---|
| 广告脚本 | 每行一句。空行分段。 |
| 风格 | `dr_vsl` 促销直效 / `comic` 漫画 / `vector` 矢量 / `claymation` 黏土 / `talking_head` 口播 / `cinematic` 电影感 |
| 画幅 | 9:16 竖屏（TikTok）、1:1、4:5、16:9 |
| 视频模型 | 单次运行的覆盖选择，留空=用「0 · Provider 配置」里保存的默认值 |
| 配音 | Edge TTS（免费神经音）/ ElevenLabs / Formant（离线） |
| LLM 分镜 | 勾上走 DeepSeek 智能分镜；不勾走规则解析（离线、更快） |
| Draft | 半分辨率快速预览，成片时关掉 |

点「生成场景文案」→ 进入第 2 步；确认后进度条推进，完成后右侧出现播放器。

### 预览与统计

成片直接内嵌播放。下方三格统计：**时长 / 成本 / 场景数**。日志标签页里有更细的
`渲染 N 复用 M` —— 复用数就是「改一行不重渲全片」的直接证据。

### 微调（第 4 步）

用自然语言描述改动，例如：

- `开头再炸一点`
- `中段换成漫画风`
- `音乐换暗黑一点`
- `throughout the video, make it feel more premium`

后端返回解析结果：意图类型、受影响场景数、将要打的 patch 列表、以及模型给的理由。

> **当前状态**：改片端点只做**分析**（plan），还没有把 patch 应用后触发重渲染。
> 接上重渲染只需在 `/api/edit` 里调用 `apply_plan()` 再跑一次 `pipe.run()`。
> 保留分析态是为了让你先看清楚它会改什么，再决定要不要花钱重渲。

### 发布 TikTok（第 5 步）

两步：先授权，再发布。

```
① 授权 TikTok 账号  →  弹出 TikTok OAuth 页，确认后回调存 token
② 发布到 TikTok     →  分片上传 + 发布
```

**前置条件**（缺一不可）：

1. 在 [developers.tiktok.com](https://developers.tiktok.com) 注册 app，申请 `video.publish` 权限
2. `.env` 里填 `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET`
3. 回调地址与 `.env` 里的 `TIKTOK_REDIRECT_URI` 一致（默认 `http://localhost:8787/api/tiktok/callback`）

**重要限制**：未通过 TikTok 的 Content Posting Audit（审核 2–6 周），所有发布强制为
`SELF_ONLY`（仅自己可见）。审核要求 app 面向广泛受众，明确拒绝"个人账号管理工具"。
所以先按「仅自己可见」跑通流程，再考虑提审。

其他限制：access token 24 小时过期（refresh token 365 天，已自动续期）、
发布 6 次/分钟、每账号约 15 条/天、视频 H.264 MP4。

---

## API

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/` | 前端页面 |
| POST | `/api/generate` | 提交生成任务，返回 `job_id` |
| GET | `/api/jobs/{id}` | 轮询任务状态 |
| WS | `/ws/jobs/{id}` | 实时进度推送 |
| GET | `/api/video/{file}` | 取成片 |
| POST | `/api/edit` | 对话式改片（分析） |
| POST | `/api/plan` | 第 1→2 步：脚本 → 场景文案，不生成任何素材 |
| GET | `/api/plan/{id}` | 取回一个规划 |
| POST | `/api/render` | 第 2→3 步：按确认后的场景渲染成片（**这一步开始计费**） |
| GET | `/api/settings` | Provider 配置页数据：可选厂商、需要的 key、是否已配置（掩码） |
| POST | `/api/settings` | 保存厂商选择与密钥（写 `avb.toml` + `.env`，热生效） |
| GET/POST | `/api/config` | 读/写 `avb.toml` |
| GET | `/api/providers` | Provider 可用状态 |
| POST | `/api/tts-preview` | 试听配音 |
| GET | `/api/tiktok/auth-url` | 获取授权链接 |
| POST | `/api/tiktok/upload` | 发布到 TikTok |

---

## 架构说明

**为什么 Project IR 放内存**：`/api/edit` 需要拿到上一步的 IR 才能定位场景。
`avb/ir.py` 有 `to_dict()` 但没有 `from_dict()`，而本服务是本地单进程，
所以直接把 Project 对象存在 `_projects[job_id]` 里 —— 简单且精确。
若要让任务跨重启存活，给 `avb/ir.py` 补 `from_dict()` 再落盘。

**跨线程进度推送**：管线跑在 `run_in_executor` 的 worker 线程里，而
WebSocket 客户端在主 asyncio 循环上。所以启动时捕获主循环，worker 用
`asyncio.run_coroutine_threadsafe()` 调度广播。
直接在 worker 里调 `asyncio.get_event_loop()` 会抛
`There is no current event loop in thread` —— 这是踩过的坑。

**为什么密钥不进 `avb.toml`**：toml 是会提交进 git 的配置文件，
里面只写"用哪个 env var 存 key"。密钥统一落到 `.env`（已 gitignore），
写入时按行替换，保留文件里原有的注释与分组。

**并发**：一次一个任务最稳。多个任务同时跑会争 ffmpeg 和缓存目录；
真要并发需要给每个 job 独立的 workdir。
