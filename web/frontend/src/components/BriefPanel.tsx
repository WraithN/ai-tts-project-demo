import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Sparkles, Wand2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Badge } from '@/components/ui/primitives'
import { cn } from '@/lib/utils'
import { api, toastError, type ScriptDraft, type Settings } from '@/lib/api'
import { STYLES } from './ScriptPanel'

/**
 * Step 1 (new): product brief -> four competing ad-script concepts.
 *
 * Replaces the free-text script box. The user describes the product, we
 * return four frameworks (pain→solution, grass-planting, review, story),
 * they pick one and can edit the narration, then we push that narration
 * into the existing plan/confirm/render pipeline.
 */
export function BriefPanel({
  settings,
  planBusy,
  onPlan,
}: {
  settings: Settings
  planBusy: boolean
  onPlan: (p: {
    script: string
    style: string
    aspect: string
    draft: boolean
    use_llm: boolean
    video_provider: string | null
    voice_provider: string | null
  }) => void
}) {
  const { t } = useTranslation()
  const [product, setProduct] = useState('')
  const [points, setPoints] = useState('')
  const [style, setStyle] = useState('dr_vsl')
  const [audience, setAudience] = useState('')
  const [tone, setTone] = useState('')
  const [useLlm, setUseLlm] = useState(true)

  const [videoOverride, setVideoOverride] = useState('__default__')

  const [busy, setBusy] = useState(false)
  const [drafts, setDrafts] = useState<ScriptDraft[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [edited, setEdited] = useState<Record<string, string>>({})

  const generate = async () => {
    if (!product.trim() && !points.trim()) {
      toastError(new Error(t('brief.needInput')))
      return
    }
    setBusy(true)
    try {
      const r = await api.scripts({
        product: product.trim(),
        selling_points: points,
        style,
        audience: audience.trim() || null,
        tone: tone.trim() || null,
        use_llm: useLlm,
      })
      setDrafts(r.scripts)
      setEdited(Object.fromEntries(r.scripts.map((s) => [s.type, s.narration])))
      setSelected(r.scripts[0]?.type ?? null)
    } catch (e) {
      toastError(e)
    } finally {
      setBusy(false)
    }
  }

  const narrationOf = (s: ScriptDraft) => edited[s.type] ?? s.narration

  const next = () => {
    const pick = drafts.find((s) => s.type === selected)
    if (!pick) return
    onPlan({
      script: narrationOf(pick).trim(),
      style,
      aspect: '9:16',
      draft: false,
      use_llm: useLlm,
      video_provider: videoOverride === '__default__' ? null : videoOverride,
      voice_provider: null,
    })
  }

  return (
    <section className="rounded-lg border bg-card shadow-sm">
      <div className="flex items-center justify-between p-5 pb-3">
        <h2 className="text-sm font-semibold">{t('brief.title')}</h2>
        <Wand2 className="h-4 w-4 text-muted-foreground" />
      </div>

      <div className="space-y-4 px-5 pb-5">
        <div className="space-y-1.5">
          <Label className="text-xs">{t('brief.product')}</Label>
          <Textarea
            className="min-h-[52px]"
            placeholder={t('brief.productPlaceholder')}
            value={product}
            onChange={(e) => setProduct(e.target.value)}
          />
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs">{t('brief.points')}</Label>
          <Textarea
            className="min-h-[80px]"
            placeholder={t('brief.pointsPlaceholder')}
            value={points}
            onChange={(e) => setPoints(e.target.value)}
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label className="text-xs">{t('brief.style')}</Label>
            <Select value={style} onValueChange={setStyle}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {STYLES.map((s) => (
                  <SelectItem key={s} value={s}>
                    {t(`style.${s}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">{t('brief.tone')}</Label>
            <Textarea
              className="min-h-[40px] resize-none"
              placeholder={t('brief.tonePlaceholder')}
              value={tone}
              onChange={(e) => setTone(e.target.value)}
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label className="text-xs">{t('brief.audience')}</Label>
            <Textarea
              className="min-h-[40px] resize-none"
              placeholder={t('brief.audiencePlaceholder')}
              value={audience}
              onChange={(e) => setAudience(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">{t('script.overrideVideo')}</Label>
            <Select value={videoOverride} onValueChange={setVideoOverride}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__default__">{t('script.overrideVideoHint')}</SelectItem>
                {settings.video.specs.map((s) => (
                  <SelectItem key={s.key} value={s.key}>
                    {t(`vendor.${s.key}.label`, { defaultValue: s.label })}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="space-y-2 rounded-md border p-3">
          <label className="flex items-center justify-between gap-3 text-sm">
            <span>{t('brief.useLlm')}</span>
            <input
              type="checkbox"
              checked={useLlm}
              onChange={(e) => setUseLlm(e.target.checked)}
              className="h-4 w-4"
            />
          </label>
          <p className="text-[11px] text-muted-foreground">{t('brief.useLlmHint')}</p>
        </div>

        <Button className="w-full" onClick={generate} disabled={busy}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
          {busy ? t('brief.generating') : t('brief.generate')}
        </Button>

        {drafts.length ? (
          <div className="space-y-3 pt-1">
            <p className="text-xs font-medium text-muted-foreground">{t('brief.choose')}</p>
            {drafts.map((s) => {
              const active = selected === s.type
              return (
                <div
                  key={s.type}
                  className={cn(
                    'cursor-pointer rounded-md border p-3 transition-colors',
                    active ? 'border-primary bg-primary/5' : 'bg-background/40 hover:bg-background',
                  )}
                  onClick={() => setSelected(s.type)}
                >
                  <div className="mb-2 flex items-center gap-2">
                    <span
                      className={cn(
                        'flex h-4 w-4 items-center justify-center rounded-full border',
                        active ? 'border-primary' : 'border-muted-foreground/40',
                      )}
                    >
                      {active ? <span className="h-2 w-2 rounded-full bg-primary" /> : null}
                    </span>
                    <span className="text-sm font-semibold">{t(`scriptType.${s.type}`, { defaultValue: s.title })}</span>
                    <Badge variant="outline" className="ml-auto text-[10px]">
                      {s.rationale}
                    </Badge>
                  </div>
                  <Textarea
                    className="min-h-[120px] resize-y text-xs leading-relaxed"
                    value={narrationOf(s)}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) =>
                      setEdited((v) => ({ ...v, [s.type]: e.target.value }))
                    }
                  />
                </div>
              )
            })}

            <Button className="w-full" onClick={next} disabled={!selected || planBusy}>
              {planBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
              {planBusy ? t('brief.planning') : t('brief.toScenes')}
            </Button>
          </div>
        ) : null}
      </div>
    </section>
  )
}
