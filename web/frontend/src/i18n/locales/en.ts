import type zh from './zh'

const en: typeof zh = {
  app: {
    title: 'AI Video Builder',
    subtitle: 'Script in · finished ad out · publish to TikTok',
    lang: 'Language',
    theme: 'Theme',
    themeLight: 'Light',
    themeDark: 'Dark',
  },

  hero: {
    title: 'Turn a product into a finished ad — without touching a timeline',
    sub: 'Describe the product, pick one of four ad angles, tweak the storyboard shot-by-shot, then render and publish. Nothing is billed until you confirm the copy.',
  },

  status: {
    title: 'Provider status',
    ready: 'ready',
    missing: 'not configured',
    videoNone: 'video · none',
    videoActive: 'video · {{name}}',
    voiceActive: 'voice · {{name}}',
    llmActive: 'LLM · {{provider}}/{{model}}',
    notReadyHint: 'No usable video provider. Add a key in the settings above.',
    envHint: 'Secrets live in: {{path}}',
  },

  providers: {
    title: 'Provider settings',
    required: 'required',
    video: 'Video model',
    videoHint: 'No offline fallback: every vendor is a hosted model and needs a key.',
    voice: 'Voiceover',
    llm: 'LLM (script parsing / edit routing)',
    publish: 'Publish to TikTok',
    publishHint: 'Apps that have not passed the Content Posting Audit can only post SELF_ONLY.',
    model: 'Model revision',
    modelCustom: 'Custom model id…',
    modelCustomHint: 'e.g. MiniMax-Hailuo-2.3 — vendors rev these often',
    baseUrl: 'Base URL',
    modelId: 'Model',
    save: 'Save settings',
    saving: 'Saving…',
    saved: 'Saved: {{keys}}',
    savedNone: 'Saved (no new keys)',
    notReadyAfterSave: 'Video provider still not ready — check the key',
    auto: 'Auto (first configured vendor)',
    getKey: 'Get a key ↗',
    keepEmpty: 'leave blank to keep',
    savedMask: '✓ saved {{mask}}',
    hint: 'Keys go to .env (gitignored), choices to avb.toml. Takes effect immediately — no restart.',
    notesFallback:
      'Auto = first configured vendor. To fill in a key for one, select it explicitly.',
    optional: '(optional)',
    fail: 'Save failed: {{msg}}',
  },

  vendor: {
    seedance: {
      label: 'Seedance (ByteDance / Volcengine Ark)',
      note: 'Best Chinese-language coverage; pro is steadier, mini is cheaper.',
    },
    kling: {
      label: 'Kling (Kuaishou)',
      note: 'Big motion, strong camera language — good for hooks.',
    },
    jimeng: {
      label: 'Jimeng (Volcengine CV)',
      note: 'HMAC two-key signing, unlike Seedance’s single Bearer key. Result URLs expire in 1h, concurrency 1.',
    },
    veo: {
      label: 'Veo (Google)',
      note: 'Best audio-visual coherence, and the most expensive.',
    },
    bailian: {
      label: 'Wan (Alibaba Bailian)',
      note: 'Cheap China option, average latency. The only major family with open weights.',
    },
    minimax: {
      label: 'Hailuo (MiniMax)',
      note: 'Strong on people and physics. Default host is the intl endpoint — set MINIMAX_BASE_URL for a mainland account.',
    },
    zhipu: {
      label: 'CogVideoX (Zhipu)',
      note: 'Open weights, self-hostable; hosted tier is cheap.',
    },
  },

  voiceVendor: {
    edge: { label: 'Edge TTS (free neural)', note: 'edge-tts, no key needed, word-level timings. ~400 voices.' },
    elevenlabs: { label: 'ElevenLabs (paid)', note: 'Most accurate word timings, billed per character.' },
    formant: { label: 'Formant (offline)', note: 'Offline Klatt synthesiser. Real audio, robotic.' },
    auto: 'Auto (ElevenLabs → Edge → offline)',
  },

  llmVendor: {
    deepseek: { label: 'DeepSeek (default)', note: 'OpenAI-compatible endpoint for parsing and edit routing.' },
    openai: { label: 'OpenAI', note: 'gpt-4o-mini is plenty for scene parsing.' },
    moonshot: { label: 'Moonshot / Kimi', note: 'Strong Chinese copy; OpenAI-compatible endpoint.' },
    zhipu: { label: 'Zhipu GLM', note: 'Shares ZHIPU_API_KEY with the CogVideoX video adapter.' },
    qwen: { label: 'Qwen (Alibaba Bailian)', note: 'Shares BAILIAN_API_KEY with the Wan video adapter.' },
    compat: { label: 'OpenAI-compatible (custom)', note: 'Point Base URL at vLLM / Ollama / any gateway.' },
  },

  env: {
    SEEDANCE_API_KEY: { label: 'Ark API Key', hint: 'Volcengine Ark console → API Key management; single Bearer key' },
    KLING_API_KEY: { label: 'API Key', hint: 'Kling open platform → API key (Bearer)' },
    VOLC_ACCESS_KEY: { label: 'Access Key', hint: 'Volcengine → Key management → AccessKey ID' },
    VOLC_SECRET_KEY: { label: 'Secret Key', hint: 'Pairs with the Access Key; HMAC-SHA256 signing' },
    JIMENG_REQ_KEY: { label: 'req_key', hint: 'Usually leave alone; set it to switch model revisions' },
    GOOGLE_API_KEY: { label: 'API Key', hint: 'Google AI Studio / Vertex, Veo access required' },
    BAILIAN_API_KEY: { label: 'DashScope API Key', hint: 'Alibaba Bailian console → API-KEY' },
    MINIMAX_API_KEY: { label: 'API Key', hint: 'MiniMax platform → Account management → API Keys' },
    MINIMAX_GROUP_ID: { label: 'Group ID', hint: 'Only for group-billed accounts; sent as the GroupId header' },
    ZHIPU_API_KEY: { label: 'API Key', hint: 'Zhipu open platform → API Keys' },
    ELEVENLABS_API_KEY: { label: 'API Key', hint: 'ElevenLabs → Profile → API Key' },
    DEEPSEEK_API_KEY: { label: 'API Key', hint: 'platform.deepseek.com → API Keys' },
    MOONSHOT_API_KEY: { label: 'API Key', hint: 'platform.moonshot.cn → API keys' },
    OPENAI_API_KEY: { label: 'API Key', hint: 'OpenAI, or any OpenAI-compatible gateway' },
    TIKTOK_CLIENT_KEY: { label: 'Client Key', hint: 'TikTok developer portal → App → Client Key' },
    TIKTOK_CLIENT_SECRET: { label: 'Client Secret', hint: 'Same place — never commit it' },
    TIKTOK_REDIRECT_URI: { label: 'Redirect URI', hint: 'Must match the callback registered in the portal' },
  },

  nav: {
    create: 'Create',
    settings: 'Settings',
  },

  settings: {
    whatTitle: 'What this page does',
    whatBody:
      'Settings does exactly one thing: tell the pipeline which vendor to fetch footage from. Keys go to .env (gitignored), choices go to avb.toml, and both take effect immediately — no restart.',
    point1: 'Video model: there is no offline fallback, so one must be configured',
    point2: 'Voiceover: Edge TTS is free and needs no key — start there',
    point3: 'LLM: scene planning and the intent router for conversational edits',
  },

  steps: {
    script: 'Script',
    confirm: 'Confirm copy',
    render: 'Render',
    refine: 'Refine',
    publish: 'Publish',
  },

  beat: {
    hook: 'hook',
    problem: 'problem',
    agitate: 'agitate',
    mechanism: 'mechanism',
    proof: 'proof',
    offer: 'offer',
    cta: 'CTA',
  },

  script: {
    title: 'Ad script',
    placeholder:
      'Paste your ad copy, one line per sentence. e.g.\n\nYour afternoon crash is not about willpower. It is about NAD.\nNAD is the molecule your cells use to turn food into energy.\nAfter thirty, it drops. That is why you fade at three PM.\nOur liposomal NMN restores your NAD levels. One capsule.\nForty percent off your first bottle. Tap below.',
    style: 'Style',
    aspect: 'Aspect',
    overrideVideo: 'Video model (this run only)',
    overrideVideoHint: 'Leave empty to use the default saved in settings',
    voice: 'Voiceover',
    useLlm: 'LLM scene planning',
    draft: 'Draft mode (half resolution, fast preview)',
    plan: 'Draft scene copy',
    planning: 'Planning…',
    submit: 'Generate video',
    submitting: 'Generating…',
  },

  brief: {
    title: 'Product brief → ad scripts',
    product: 'Product',
    productPlaceholder: 'e.g. Lumo sleep lamp',
    points: 'Selling points',
    pointsPlaceholder: 'One per line:\nfalls asleep 2x faster\nwarm sunrise wake-up light',
    style: 'Visual style',
    tone: 'Tone (optional)',
    tonePlaceholder: 'e.g. reassuring, premium, playful',
    audience: 'Audience (optional)',
    audiencePlaceholder: 'e.g. new parents, night-shift workers',
    duration: 'Video length',
    durationHint: 'Target total length, 10–60s (±5 per step). Scenes are stretched to fit; voice and captions stay in sync.',
    useLlm: 'LLM script writer',
    useLlmHint: 'Off = deterministic templates (works offline). On = richer, varied copy.',
    generate: 'Generate 4 ad scripts',
    generating: 'Writing…',
    choose: 'Pick one and edit it — then send to storyboard',
    toScenes: 'Next: storyboard',
    planning: 'Planning…',
    needInput: 'Enter a product name or at least one selling point',
  },

  scriptType: {
    pain_solution: 'Pain → Solution',
    grass_planting: 'Grass-planting / Soft sell',
    review_proof: 'Review / Proof',
    story_drama: 'Story / Drama',
  },

  confirm: {
    title: 'Confirm scene copy and captions',
    subtitle:
      "What the model produced is a first draft. Edit anything here — nothing is rendered until you confirm.",
    count: '{{n}} scenes',
    vo: 'Voiceover / caption text',
    captionNote:
      'Captions are driven by this line’s word-level timings, so editing it moves the caption too.',
    prompt: 'Visual prompt',
    shot: 'Shot',
    angle: 'Angle',
    elements: 'Elements (comma separated)',
    back: 'Back to script',
    reparse: 'Re-plan',
    confirm: 'Confirm and render',
    up: 'Move up',
    down: 'Move down',
    remove: 'Remove this scene',
    spendNote: 'Confirming is what calls the voice and video models — billing starts here.',
    planned: '{{n}} scenes drafted — confirm to render',
  },

  style: {
    dr_vsl: 'DR VSL (direct response)',
    comic: 'Comic',
    vector: 'Vector',
    claymation: 'Claymation',
    talking_head: 'Talking head',
    cinematic: 'Cinematic',
  },

  aspect: {
    '9:16': '9:16 vertical (TikTok)',
    '1:1': '1:1 square',
    '4:5': '4:5 feed',
    '16:9': '16:9 landscape',
  },

  stage: {
    queued: 'queued',
    confirming: 'applying confirmed copy',
    parsing: 'parsing script',
    synth: 'voiceover',
    rendering: 'rendering',
    compositing: 'compositing',
    done: 'done',
    error: 'failed',
  },

  preview: {
    title: 'Preview',
    empty: 'Your video appears here',
    emptySub: '9:16 vertical · H.264 + AAC',
    duration: 'duration',
    cost: 'cost',
    scenes: 'scenes',
    rendered: 'rendered {{n}} · reused {{m}}',
    reusedHint: 'The reuse count is the proof that "change one line" is not a full rebuild',
  },

  edit: {
    tab: 'Conversational edit',
    title: '4 · Refine',
    placeholder: 'Describe the change you want…',
    analyze: 'Analyze',
    apply: 'Apply and re-render',
    applySwitch: 'Apply the change and re-render',
    applySwitchHint: 'Turn off to see the analysis without touching the footage',
    rebuilding: 'Re-rendering {{n}} scene(s); the other {{total}} come from cache',
    needJob: 'Generate a video first',
    welcome: 'Try: punchier opening / make the middle comic / darker music',
    scenesAffected: '{{n}} scenes affected',
    hint: 'The model parses your instruction, locates the affected scenes, and re-renders only those.',
    fail: 'Analysis failed: {{msg}}',
    analysisOnly: 'Returns the plan only — it does not trigger a re-render yet',
  },

  publish: {
    tab: 'Publish',
    title: '5 · Publish to TikTok',
    caption: 'Title / caption',
    captionPlaceholder: 'Your ad copy #hashtags',
    privacy: 'Visibility',
    privacySelf: 'Only you (default until approved)',
    privacyPublic: 'Public (requires TikTok audit)',
    privacyFollowers: 'Followers only',
    auth: '① Authorize TikTok',
    upload: '② Publish to TikTok',
    hint: 'Needs TIKTOK_CLIENT_KEY / SECRET. Unaudited apps can only post "only you".',
    needVideo: 'Generate a video first',
    authorized: 'Authorization page opened — come back here to publish.',
  },

  log: {
    tab: 'Run log',
    waiting: 'Waiting for a job…',
  },

  errors: {
    noScript: 'Paste an ad script first',
    notConfigured:
      'No usable video provider yet: open Settings, pick a vendor, fill in its API key, and save.',
    generateFailed: 'Submit failed: {{msg}}',
  },
}

export default en
