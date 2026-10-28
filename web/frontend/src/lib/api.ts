import { toast } from 'sonner'
import i18n from '@/i18n'

export interface EnvSpec {
  label: string
  secret: boolean
  required: boolean
  hint: string
  default: string
  set: boolean
  masked: string
}

export interface ProviderSpec {
  key: string
  label: string
  kind: string
  ready: boolean
  missing: string[]
  console: string
  notes: string
  models: string[]
  model_env: string
  model_value: string
  envs: Record<string, EnvSpec>
  selected: boolean
}

export interface Settings {
  video: {
    selected: string
    resolved: string
    active: string | null
    ready: boolean
    error: string
    specs: ProviderSpec[]
  }
  voice: { selected: string; resolved: string; active: string; ready: boolean; specs: ProviderSpec[] }
  llm: {
    selected: string
    model: string
    base_url: string
    api_key_env: string
    api_key_set: boolean
    api_key_masked: string
    specs: ProviderSpec[]
  }
  publish: { specs: ProviderSpec[] }
  env_file: string
  toml_file: string
  written?: { toml: string[]; env: string[] }
}

export interface Job {
  job_id: string
  stage: string
  progress: number
  message: string
  done: boolean
  error: string | null
  video_url: string | null
  /** Absolute local path, needed by the TikTok upload step. */
  video_path?: string | null
  cost: number | null
  total_duration?: number
  scenes?: number
  rendered?: number
  reused?: number
}

export interface PlanScene {
  id: string
  index: number
  beat: string
  /** Spoken line. Also drives the kinetic caption (one source of truth). */
  vo: string
  prompt: string
  motion: string
  style: string
  shot: string
  angle: string
  elements: string[]
}

export interface ScriptDraft {
  type: string
  title: string
  narration: string
  rationale: string
  outline?: { beat: string; line: string }[]
}

export interface ScriptsResponse {
  brief: { product: string; style: string }
  scripts: ScriptDraft[]
}

export interface Plan {
  plan_id: string
  name: string
  style: string
  scenes: PlanScene[]
}

export interface EditPlanResult {
  intent: string
  scene_ids: string[]
  ops: { path: string; value: unknown }[]
  explanation: string
  job_id?: string
  rebuilt?: string[]
}

async function json<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const r = await fetch(input, init)
  const text = await r.text()
  let data: unknown = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = null
  }
  if (!r.ok) {
    const detail = (data as { detail?: string } | null)?.detail ?? r.statusText
    throw new Error(detail)
  }
  return data as T
}

export const api = {
  getSettings: () => json<Settings>('/api/settings'),

  saveSettings: (payload: {
    video_provider?: string | null
    voice_provider?: string | null
    llm_provider?: string | null
    llm_model?: string | null
    llm_base_url?: string | null
    video_model?: string | null
    voice_model?: string | null
    keys?: Record<string, string>
  }) =>
    json<Settings>('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  /** Top of the funnel: brief -> four competing ad-script concepts. */
  scripts: (payload: {
    product: string
    selling_points: string[] | string
    style: string
    audience?: string | null
    tone?: string | null
    use_llm: boolean
  }) =>
    json<ScriptsResponse>('/api/scripts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  /** Step 1: script -> scenes. Free; nothing is generated yet. */
  plan: (payload: { script: string; name: string; style: string; use_llm: boolean }) =>
    json<Plan>('/api/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  getPlan: (id: string) => json<Plan>(`/api/plan/${id}`),

  /** Step 3: render the confirmed scenes. This is the step that spends money. */
  render: (payload: {
    plan_id: string
    scenes: { id: string; vo: string; prompt: string; shot?: string; angle?: string; elements?: string[] }[]
    name: string
    style: string
    aspect: string
    draft: boolean
    video_provider: string | null
    voice_provider: string | null
  }) =>
    json<{ job_id: string }>('/api/render', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  generate: (payload: {
    script: string
    name: string
    style: string
    aspect: string
    draft: boolean
    use_llm: boolean
    video_provider: string | null
    voice_provider: string | null
  }) =>
    json<{ job_id: string }>('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  getJob: (id: string) => json<Job>(`/api/jobs/${id}`),

  planEdit: (payload: {
    job_id: string
    instruction: string
    use_llm: boolean
    apply?: boolean
    draft?: boolean
  }) =>
    json<EditPlanResult>('/api/edit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  tiktokAuthUrl: () => json<{ auth_url: string }>('/api/tiktok/auth-url'),

  uploadTiktok: (payload: { video_path: string; title: string; privacy_level: string }) =>
    json<{ ok: boolean; result?: unknown; detail?: string }>('/api/tiktok/upload', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
}

/** Toast on failure, return the message so callers can also show it inline. */
export function errorText(e: unknown) {
  return e instanceof Error ? e.message : String(e)
}

export function toastError(e: unknown) {
  toast.error(errorText(e))
}

/** Localised label for a stage id returned by the backend. */
export function stageLabel(stage: string) {
  return i18n.t(`stage.${stage}`, { defaultValue: stage }) as string
}
