# Web App

浏览器里跑完整条流水线：贴脚本 → 选模型 → 生成 → 对话式改片 → 发 TikTok。

```
python -m uvicorn web.server:app --port 8787
# 打开 http://127.0.0.1:8787
```

需要 `pip install fastapi uvicorn`（或 `pip install -e ".[web]"`）。

---

## 界面

左侧是输入与配置，右侧是预览与操作。

### 0 · Provider 配置

第一次用必须先在这里配一家视频模型 —— 项目里**没有离线兜底**，
没配 key 点「生成视频」会直接被拦下并提示你回来配置。

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

### 1 · 脚本与配置

| 控件 | 说明 |
|---|---|
| 广告脚本 | 每行一句。空行分段。 |
| 风格 | `dr_vsl` 促销直效 / `comic` 漫画 / `vector` 矢量 / `claymation` 黏土 / `talking_head` 口播 / `cinematic` 电影感 |
| 画幅 | 9:16 竖屏（TikTok）、1:1、4:5、16:9 |
| 视频模型 | 单次运行的覆盖选择，留空=用「0 · Provider 配置」里保存的默认值 |
| 配音 | Edge TTS（免费神经音）/ ElevenLabs / Formant（离线） |
| LLM 分镜 | 勾上走 DeepSeek 智能分镜；不勾走规则解析（离线、更快） |
| Draft | 半分辨率快速预览，成片时关掉 |

点「生成视频」后进度条推进，完成后右侧出现播放器。

### 2 · 预览与统计

成片直接内嵌播放。下方三格统计：**时长 / 成本 / 场景数**。日志标签页里有更细的
`渲染 N 复用 M` —— 复用数就是「改一行不重渲全片」的直接证据。

### 3 · 对话式改片

用自然语言描述改动，例如：

- `开头再炸一点`
- `中段换成漫画风`
- `音乐换暗黑一点`
- `throughout the video, make it feel more premium`

后端返回解析结果：意图类型、受影响场景数、将要打的 patch 列表、以及模型给的理由。

> **当前状态**：改片端点只做**分析**（plan），还没有把 patch 应用后触发重渲染。
> 接上重渲染只需在 `/api/edit` 里调用 `apply_plan()` 再跑一次 `pipe.run()`。
> 保留分析态是为了让你先看清楚它会改什么，再决定要不要花钱重渲。

### 4 · 发布 TikTok

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
