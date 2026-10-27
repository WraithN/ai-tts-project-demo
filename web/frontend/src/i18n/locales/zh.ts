export default {
  app: {
    title: 'AI Video Builder',
    subtitle: '脚本进 · 成片出 · 一键发 TikTok',
    lang: '语言',
    theme: '主题',
    themeLight: '浅色',
    themeDark: '深色',
  },

  status: {
    title: 'Provider 状态',
    ready: '就绪',
    missing: '未配置',
    videoNone: '视频 · 未配置',
    videoActive: '视频 · {{name}}',
    voiceActive: '配音 · {{name}}',
    llmActive: 'LLM · {{provider}}/{{model}}',
    notReadyHint: '没有可用的视频 Provider，无法生成。请在上方配置里填一个 Key。',
    envHint: '密钥文件：{{path}}',
  },

  providers: {
    title: '0 · Provider 配置',
    required: '必填',
    video: '视频生成模型',
    videoHint: '没有离线兜底：每家都是真实模型，必须配 Key 才能出片。',
    voice: '配音',
    llm: 'LLM（分镜解析 / 改片路由）',
    publish: '发布 TikTok',
    publishHint: '未通过 Content Posting Audit 的应用只能发「仅自己可见」。',
    model: '模型版本',
    baseUrl: 'Base URL',
    modelId: 'Model',
    save: '保存配置',
    saving: '保存中…',
    saved: '已保存：{{keys}}',
    savedNone: '已保存（无新增密钥）',
    notReadyAfterSave: '视频 Provider 仍未就绪，请检查 Key',
    auto: '自动（按已配置顺序）',
    getKey: '获取 Key ↗',
    keepEmpty: '留空 = 不修改',
    savedMask: '✓ 已配置 {{mask}}',
    hint: '密钥写入 .env（不进 git），模型选择写入 avb.toml。保存后立刻生效，无需重启。',
    notesFallback: '「自动」= 按已配置顺序挑第一个可用的模型。要给某家填 Key，请直接选中它。',
    optional: '（可选）',
    fail: '保存失败：{{msg}}',
  },

  vendor: {
    seedance: {
      label: 'Seedance（字节火山方舟）',
      note: '国内可用，中文场景表现最好；pro 更稳，mini 更便宜。',
    },
    kling: {
      label: 'Kling 可灵（快手）',
      note: '运动幅度大、镜头语言强，适合 hook 段。',
    },
    jimeng: {
      label: '即梦 Jimeng（火山视觉）',
      note: 'HMAC 双 key 签名，与 Seedance 的 Bearer 单 key 不同。结果 URL 1 小时有效、并发 1。',
    },
    veo: { label: 'Veo（Google）', note: '海外首选，音画一致性最好，价格也最高。' },
    bailian: { label: 'Wan 通义万相（阿里百炼）', note: '国内备选，便宜，速度一般。' },
    minimax: { label: 'Hailuo 海螺（MiniMax）', note: '人物表现好，适合口播类。' },
    zhipu: { label: 'CogVideoX（智谱）', note: '开源可自部署，云端版便宜。' },
  },

  voiceVendor: {
    edge: { label: 'Edge TTS（免费神经语音）', note: 'edge-tts，免费无需 key，带词级时间轴。' },
    elevenlabs: { label: 'ElevenLabs（付费）', note: '词级时间轴最准，按字符计费。' },
    formant: { label: 'Formant（离线共振峰）', note: '离线 Klatt 合成器，真音频，机械音。' },
    auto: '自动（ElevenLabs → Edge → 离线）',
  },

  llmVendor: {
    deepseek: { label: 'DeepSeek（默认）', note: 'OpenAI 兼容端点，用于分镜解析与改片路由。' },
    openai: { label: 'OpenAI 兼容端点', note: '改 Base URL 可接任何 OpenAI 兼容服务。' },
  },

  env: {
    SEEDANCE_API_KEY: { label: 'Ark API Key', hint: '火山方舟控制台 → API Key 管理；Bearer 单 key' },
    KLING_API_KEY: { label: 'API Key', hint: '可灵开放平台 → API 密钥（Bearer）' },
    VOLC_ACCESS_KEY: { label: 'Access Key', hint: '火山引擎 → 密钥管理 → AccessKey ID' },
    VOLC_SECRET_KEY: { label: 'Secret Key', hint: '与 Access Key 成对；签名用 HMAC-SHA256' },
    JIMENG_REQ_KEY: { label: 'req_key', hint: '一般不用改；换模型版本时填' },
    GOOGLE_API_KEY: { label: 'API Key', hint: 'Google AI Studio / Vertex，需开通 Veo 权限' },
    BAILIAN_API_KEY: { label: 'DashScope API Key', hint: '阿里云百炼控制台 → API-KEY' },
    MINIMAX_API_KEY: { label: 'API Key', hint: 'MiniMax 开放平台 → 接口密钥' },
    ZHIPU_API_KEY: { label: 'API Key', hint: '智谱开放平台 → API Keys' },
    ELEVENLABS_API_KEY: { label: 'API Key', hint: 'ElevenLabs → Profile → API Key' },
    DEEPSEEK_API_KEY: { label: 'API Key', hint: 'platform.deepseek.com → API Keys' },
    OPENAI_API_KEY: { label: 'API Key', hint: 'OpenAI / Moonshot / vLLM / Ollama 皆可' },
    TIKTOK_CLIENT_KEY: { label: 'Client Key', hint: 'TikTok 开发者后台 → App → Client Key' },
    TIKTOK_CLIENT_SECRET: { label: 'Client Secret', hint: '同上，注意不要进 git' },
    TIKTOK_REDIRECT_URI: { label: 'Redirect URI', hint: '必须与开发者后台配置的回调地址一致' },
  },

  script: {
    title: '1 · 广告脚本',
    placeholder:
      '粘贴广告文案，每行一句。例如：\n\nYour afternoon crash is not about willpower. It is about NAD.\nNAD is the molecule your cells use to turn food into energy.\nAfter thirty, it drops. That is why you fade at three PM.\nOur liposomal NMN restores your NAD levels. One capsule.\nForty percent off your first bottle. Tap below.',
    style: '风格',
    aspect: '画幅',
    overrideVideo: '视频模型（仅本次覆盖）',
    overrideVideoHint: '留空则用上方配置里保存的默认值',
    voice: '配音',
    useLlm: '用 LLM 智能分镜',
    draft: 'Draft 模式（半分辨率快速预览）',
    submit: '生成视频',
    submitting: '生成中…',
  },

  style: {
    dr_vsl: 'DR VSL 促销直效',
    comic: 'Comic 漫画',
    vector: 'Vector 矢量',
    claymation: 'Claymation 黏土',
    talking_head: 'Talking Head 口播',
    cinematic: 'Cinematic 电影感',
  },

  aspect: {
    '9:16': '9:16 竖屏（TikTok）',
    '1:1': '1:1 方形',
    '4:5': '4:5 信息流',
    '16:9': '16:9 横屏',
  },

  stage: {
    queued: '排队中',
    parsing: '解析脚本',
    synth: '合成配音',
    rendering: '渲染场景',
    compositing: '合成成片',
    done: '完成',
    error: '失败',
  },

  preview: {
    title: '2 · 成片预览',
    empty: '生成后在此预览',
    emptySub: '9:16 竖屏 · H.264 + AAC',
    duration: '时长',
    cost: '成本',
    scenes: '场景数',
    rendered: '渲染 {{n}} · 复用 {{m}}',
    reusedHint: '复用数是「改一行不重渲全片」的直接证据',
  },

  edit: {
    tab: '对话式改片',
    placeholder: '描述你想要的改动…',
    analyze: '分析',
    needJob: '请先生成一个视频',
    welcome: '试试：开头再炸一点 / 中段换成漫画风 / 音乐换暗黑一点',
    scenesAffected: '影响 {{n}} 个场景',
    hint: 'AI 解析指令 → 定位受影响场景 → 只重渲这些（其余复用缓存）',
    fail: '分析失败：{{msg}}',
    analysisOnly: '当前只返回分析结果，不会触发重渲染',
  },

  publish: {
    tab: '发布 TikTok',
    caption: '视频标题 / 文案',
    captionPlaceholder: '你的广告文案 #话题标签',
    privacy: '可见性',
    privacySelf: '仅自己可见（未过审默认）',
    privacyPublic: '公开（需 TikTok 审核）',
    privacyFollowers: '仅粉丝',
    auth: '① 授权 TikTok 账号',
    upload: '② 发布到 TikTok',
    hint: '需要 TIKTOK_CLIENT_KEY / SECRET。未过审核的 app 只能发「仅自己可见」。',
    needVideo: '请先生成视频',
    authorized: '已打开授权页，完成后回到这里发布。',
  },

  log: {
    tab: '运行日志',
    waiting: '等待任务…',
  },

  errors: {
    noScript: '请先粘贴广告脚本',
    notConfigured: '还没有可用的视频 Provider：请在「Provider 配置」里选一家并填入 API Key 后保存。',
    generateFailed: '提交失败：{{msg}}',
  },
}
