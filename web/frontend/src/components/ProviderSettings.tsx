import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { ExternalLink, KeyRound, Loader2, Save } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Badge, Separator } from '@/components/ui/primitives'
import { CollapsibleSection } from '@/components/ui/collapsible'
import { api, errorText, toastError, type ProviderSpec, type Settings } from '@/lib/api'

const LLM_PRESETS: Record<string, { base_url: string; model: string }> = {
  deepseek: { base_url: 'https://api.deepseek.com/v1', model: 'deepseek-flash' },
  openai: { base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
}

/** Labels and hints live in i18n; the server copy is only a fallback. */
function vendorLabel(t: TFunction, kind: string, spec: ProviderSpec) {
  const prefix = kind === 'video' ? 'vendor' : kind === 'voice' ? 'voiceVendor' : 'llmVendor'
  return t(`${prefix}.${spec.key}.label`, { defaultValue: spec.label }) as string
}

function vendorNote(t: TFunction, kind: string, spec: ProviderSpec) {
  const prefix = kind === 'video' ? 'vendor' : kind === 'voice' ? 'voiceVendor' : 'llmVendor'
  return t(`${prefix}.${spec.key}.note`, { defaultValue: spec.notes }) as string
}

export function ProviderSettings({
  settings,
  onSaved,
}: {
  settings: Settings
  onSaved: (s: Settings) => void
}) {
  const { t } = useTranslation()
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  const videoSpecs = settings.video.specs
  const voiceSpecs = settings.voice.specs
  const llmSpecs = settings.llm.specs

  // Land on the first vendor when nothing is configured, so the key field is
  // one click away instead of hiding behind "auto".
  const initialVideo =
    settings.video.selected === 'auto' && !settings.video.ready
      ? videoSpecs[0]?.key ?? 'auto'
      : settings.video.selected

  const [videoKey, setVideoKey] = useState(initialVideo)
  const [voiceKey, setVoiceKey] = useState(settings.voice.selected)
  const [llmKey, setLlmKey] = useState(settings.llm.selected)
  const [llmModel, setLlmModel] = useState(settings.llm.model)
  const [llmBase, setLlmBase] = useState(settings.llm.base_url)
  const [videoModel, setVideoModel] = useState('')
  const [keys, setKeys] = useState<Record<string, string>>({})

  // Re-sync whenever the server sends a fresh snapshot (initial load, save).
  useEffect(() => {
    setVideoKey(initialVideo)
    setVoiceKey(settings.voice.selected)
    setLlmKey(settings.llm.selected)
    setLlmModel(settings.llm.model)
    setLlmBase(settings.llm.base_url)
    setKeys({})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings])

  const videoSpec = useMemo(
    () => videoSpecs.find((s) => s.key === videoKey) ?? null,
    [videoSpecs, videoKey],
  )
  const voiceSpec = useMemo(
    () => voiceSpecs.find((s) => s.key === voiceKey) ?? null,
    [voiceSpecs, voiceKey],
  )
  const llmSpec = useMemo(() => llmSpecs.find((s) => s.key === llmKey) ?? null, [llmSpecs, llmKey])

  useEffect(() => {
    setVideoModel(videoSpec?.model_value || videoSpec?.models[0] || '')
  }, [videoSpec])

  const onPickLlm = (key: string) => {
    setLlmKey(key)
    const other = llmSpecs.filter((s) => s.key !== key)
    const preset = LLM_PRESETS[key]
    // Don't leave the old vendor's model id / endpoint behind.
    if (other.some((s) => s.models.includes(llmModel)) || !llmModel) {
      if (preset) setLlmModel(preset.model)
    }
    if (preset && other.some((s) => LLM_PRESETS[s.key]?.base_url === llmBase)) {
      setLlmBase(preset.base_url)
    }
  }

  const save = async () => {
    setSaving(true)
    setMsg('')
    try {
      const fresh = await api.saveSettings({
        video_provider: videoKey,
        voice_provider: voiceKey,
        llm_provider: llmKey,
        llm_model: llmModel.trim() || null,
        llm_base_url: llmBase.trim() || null,
        video_model: videoSpec?.model_env ? videoModel.trim() || null : null,
        keys,
      })
      onSaved(fresh)
      const written = fresh.written?.env ?? []
      setMsg(
        written.length
          ? t('providers.saved', { keys: written.join(', ') })
          : t('providers.savedNone'),
      )
      if (!fresh.video.ready) setMsg((m) => `${m} · ${t('providers.notReadyAfterSave')}`)
    } catch (e) {
      const m = errorText(e)
      setMsg(t('providers.fail', { msg: m }))
      toastError(e)
    } finally {
      setSaving(false)
    }
  }

  const envFields = (spec: ProviderSpec | null) =>
    !spec
      ? null
      : Object.entries(spec.envs).map(([name, e]) => (
          <div key={name} className="space-y-1.5">
            <Label className="flex items-center gap-1.5 text-xs">
              <KeyRound className="h-3 w-3" />
              {t(`env.${name}.label`, { defaultValue: e.label })}
              {!e.required ? (
                <span className="font-normal text-muted-foreground">{t('providers.optional')}</span>
              ) : null}
            </Label>
            <Input
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={keys[name] ?? ''}
              onChange={(ev) => setKeys((k) => ({ ...k, [name]: ev.target.value }))}
              placeholder={e.set ? t('providers.savedMask', { mask: e.masked }) : t(`env.${name}.hint`, { defaultValue: e.hint })}
            />
            {e.set ? (
              <p className="text-xs text-muted-foreground">{t('providers.keepEmpty')}</p>
            ) : e.hint ? (
              <p className="text-xs text-muted-foreground">{t(`env.${name}.hint`, { defaultValue: e.hint })}</p>
            ) : null}
          </div>
        ))

  return (
    <section className="rounded-lg border bg-card shadow-sm">
      <div className="flex items-start justify-between gap-3 p-5 pb-3">
        <div>
          <h2 className="text-sm font-semibold">{t('providers.title')}</h2>
          <p className="mt-1 text-xs text-muted-foreground">{t('providers.videoHint')}</p>
        </div>
        <Badge variant={settings.video.ready ? 'success' : 'destructive'}>
          {settings.video.ready ? t('status.ready') : t('providers.required')}
        </Badge>
      </div>

      <div className="space-y-4 px-5 pb-5">
        {/* ---------------------------------------------------------- video */}
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label className="text-xs">{t('providers.video')}</Label>
            <Select value={videoKey} onValueChange={setVideoKey}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {videoSpecs.map((s) => (
                  <SelectItem key={s.key} value={s.key}>
                    {vendorLabel(t, 'video', s)} {s.ready ? '✓' : ''}
                  </SelectItem>
                ))}
                <SelectItem value="auto">{t('providers.auto')}</SelectItem>
              </SelectContent>
            </Select>
            {videoSpec ? (
              <p className="text-xs text-muted-foreground">
                {vendorNote(t, 'video', videoSpec)}
                {videoSpec.console ? (
                  <>
                    {' '}
                    <a
                      className="inline-flex items-center gap-0.5 text-primary hover:underline"
                      href={videoSpec.console}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {t('providers.getKey')}
                      <ExternalLink className="h-3 w-3" />
                    </a>
                  </>
                ) : null}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">{t('providers.notesFallback')}</p>
            )}
          </div>

          {videoSpec && videoSpec.models.length ? (
            <div className="space-y-1.5">
              <Label className="text-xs">{t('providers.model')}</Label>
              <Select value={videoModel} onValueChange={setVideoModel}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {videoSpec.models.map((m) => (
                    <SelectItem key={m} value={m}>
                      {m}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : null}

          {envFields(videoSpec)}
        </div>

        <Separator />

        {/* ---------------------------------------------------------- voice */}
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label className="text-xs">{t('providers.voice')}</Label>
            <Select value={voiceKey} onValueChange={setVoiceKey}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {voiceSpecs.map((s) => (
                  <SelectItem key={s.key} value={s.key}>
                    {vendorLabel(t, 'voice', s)} {s.ready ? '✓' : ''}
                  </SelectItem>
                ))}
                <SelectItem value="auto">{t('voiceVendor.auto')}</SelectItem>
              </SelectContent>
            </Select>
            {voiceSpec ? (
              <p className="text-xs text-muted-foreground">{vendorNote(t, 'voice', voiceSpec)}</p>
            ) : null}
          </div>
          {envFields(voiceSpec)}
        </div>

        <Separator />

        {/* ------------------------------------------------------------ llm */}
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label className="text-xs">{t('providers.llm')}</Label>
            <Select value={llmKey} onValueChange={onPickLlm}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {llmSpecs.map((s) => (
                  <SelectItem key={s.key} value={s.key}>
                    {vendorLabel(t, 'llm', s)} {s.ready ? '✓' : ''}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label className="text-xs">{t('providers.baseUrl')}</Label>
              <Input value={llmBase} onChange={(e) => setLlmBase(e.target.value)} spellCheck={false} />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">{t('providers.modelId')}</Label>
              <Input value={llmModel} onChange={(e) => setLlmModel(e.target.value)} spellCheck={false} />
            </div>
          </div>
          {envFields(llmSpec)}
        </div>

        <Separator />

        {/* -------------------------------------------------------- publish */}
        <CollapsibleSection title={t('providers.publish')} description={t('providers.publishHint')}>
          <div className="space-y-3 pt-1">{envFields(settings.publish.specs[0] ?? null)}</div>
        </CollapsibleSection>

        <Button className="w-full" onClick={save} disabled={saving}>
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          {saving ? t('providers.saving') : t('providers.save')}
        </Button>

        {msg ? <p className="text-xs text-muted-foreground">{msg}</p> : null}
        <p className="text-xs text-muted-foreground">{t('providers.hint')}</p>
      </div>
    </section>
  )
}
