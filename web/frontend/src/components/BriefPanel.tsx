import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Clapperboard, ImagePlus, Leaf, Loader2, Minus, Plus, Sparkles, ThumbsUp, Wand2, X, Zap } from 'lucide-react'
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
import { Panel, PanelHeader, PanelBody } from '@/components/ui/panel'
import { cn } from '@/lib/utils'
import { api, toastError, type ScriptDraft, type Settings, type UploadedImage } from '@/lib/api'
import { STYLES } from './ScriptPanel'

const TYPE_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  pain_solution: Zap,
  grass_planting: Leaf,
  review_proof: ThumbsUp,
  story_drama: Clapperboard,
}

const DURATION_MIN = 10
const DURATION_MAX = 60
const DURATION_STEP = 5

const clampDuration = (v: number) =>
  Math.min(DURATION_MAX, Math.max(DURATION_MIN, Math.round(v)))

const SAMPLES: { product: string; points: string }[] = [
  {
    product: 'Lumo sleep lamp',
    points: 'falls asleep 2x faster\nwarm sunrise wake-up light\nno blue light after dark',
  },
  {
    product: 'Pico portable blender',
    points: 'blend anywhere, USB-C\n14 oz cup doubles as bottle\ncrushes ice in 20s',
  },
]

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
  scriptsBusy,
  runScripts,
  onScriptsReady,
  onPlan,
}: {
  settings: Settings
  planBusy: boolean
  scriptsBusy: boolean
  /** Brief -> 4 scripts, run as a tracked background job by the app shell. */
  runScripts: (payload: {
    product: string
    selling_points: string[] | string
    style: string
    audience: string | null
    tone: string | null
    use_llm: boolean
  }) => Promise<{ scripts: ScriptDraft[] }>
  onScriptsReady: () => void
  onPlan: (p: {
    script: string
    style: string
    aspect: string
    draft: boolean
    use_llm: boolean
    video_provider: string | null
    voice_provider: string | null
    target_seconds: number | null
    reference_images?: string[]
  }) => void
}) {
  const { t } = useTranslation()
  const [product, setProduct] = useState('')
  const [points, setPoints] = useState('')
  const [style, setStyle] = useState('dr_vsl')
  const [audience, setAudience] = useState('')
  const [tone, setTone] = useState('')
  const [useLlm, setUseLlm] = useState(true)
  const [targetSec, setTargetSec] = useState(30)

  // Product three-view reference images. Purely additive: empty list = the
  // old text-to-video behaviour, so nothing breaks if the user skips them.
  const [refImages, setRefImages] = useState<UploadedImage[]>([])
  const [uploading, setUploading] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const MAX_REFS = 9

  const addRefImages = async (files: FileList | null) => {
    if (!files?.length) return
    const room = MAX_REFS - refImages.length
    const picked = Array.from(files).slice(0, Math.max(0, room))
    if (!picked.length) {
      toastError(new Error(t('brief.refMax', { n: MAX_REFS })))
      return
    }
    setUploading(true)
    try {
      const r = await api.uploadImages(picked)
      setRefImages((v) => [...v, ...r.files].slice(0, MAX_REFS))
    } catch (e) {
      toastError(e)
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const [videoOverride, setVideoOverride] = useState('__default__')

  const [drafts, setDrafts] = useState<ScriptDraft[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [edited, setEdited] = useState<Record<string, string>>({})

  const generate = async () => {
    if (!product.trim() && !points.trim()) {
      toastError(new Error(t('brief.needInput')))
      return
    }
    try {
      const r = await runScripts({
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
      onScriptsReady()
    } catch (e) {
      toastError(e)
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
      target_seconds: targetSec,
      reference_images: refImages.map((r) => r.path),
    })
  }

  const applySample = (s: { product: string; points: string }) => {
    setProduct(s.product)
    setPoints(s.points)
  }

  return (
    <Panel>
      <PanelHeader title={t('brief.title')} icon={Wand2} />
      <PanelBody className="space-y-4">
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
          <div className="flex flex-wrap gap-1.5 pt-0.5">
            {SAMPLES.map((s, i) => (
              <button
                key={i}
                type="button"
                onClick={() => applySample(s)}
                className="rounded-full border border-dashed border-muted-foreground/30 px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:border-primary hover:text-foreground"
              >
                {s.product}
              </button>
            ))}
          </div>
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

        <div className="space-y-1.5">
          <Label className="text-xs">{t('brief.duration')}</Label>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="icon"
              className="h-9 w-9 shrink-0"
              disabled={targetSec <= DURATION_MIN}
              onClick={() => setTargetSec((v) => clampDuration(v - DURATION_STEP))}
              title={`-${DURATION_STEP}s`}
            >
              <Minus className="h-4 w-4" />
            </Button>
            <div className="relative flex-1">
              <input
                type="number"
                min={DURATION_MIN}
                max={DURATION_MAX}
                step={DURATION_STEP}
                value={targetSec}
                onChange={(e) => {
                  const v = parseInt(e.target.value, 10)
                  if (!Number.isNaN(v)) setTargetSec(clampDuration(v))
                }}
                onBlur={() => setTargetSec((v) => clampDuration(v))}
                className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm outline-none transition-colors focus:border-primary focus:ring-2 focus:ring-primary/20"
              />
              <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">
                s
              </span>
            </div>
            <Button
              type="button"
              variant="outline"
              size="icon"
              className="h-9 w-9 shrink-0"
              disabled={targetSec >= DURATION_MAX}
              onClick={() => setTargetSec((v) => clampDuration(v + DURATION_STEP))}
              title={`+${DURATION_STEP}s`}
            >
              <Plus className="h-4 w-4" />
            </Button>
          </div>
          <p className="text-[11px] text-muted-foreground">{t('brief.durationHint')}</p>
        </div>

        {/* Product reference images: three-view uploads that steer the
            video provider (MiniMax H3 v2 reference-to-video). */}
        <div className="space-y-1.5">
          <Label className="text-xs">{t('brief.refTitle')}</Label>
          <input
            ref={fileInput}
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif,image/bmp"
            multiple
            hidden
            onChange={(e) => void addRefImages(e.target.files)}
          />
          <div className="flex flex-wrap gap-2">
            {refImages.map((img) => (
              <div
                key={img.path}
                className="group relative h-16 w-16 overflow-hidden rounded-lg border bg-muted"
              >
                <img src={img.url} alt={img.name} className="h-full w-full object-cover" />
                <button
                  type="button"
                  aria-label={`remove ${img.name}`}
                  onClick={() => setRefImages((v) => v.filter((r) => r.path !== img.path))}
                  className="absolute right-0.5 top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-black/60 text-white opacity-0 transition-opacity group-hover:opacity-100"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
            {refImages.length < MAX_REFS ? (
              <button
                type="button"
                onClick={() => fileInput.current?.click()}
                disabled={uploading}
                className="flex h-16 w-16 flex-col items-center justify-center gap-1 rounded-lg border border-dashed text-muted-foreground transition-colors hover:border-primary hover:text-foreground disabled:opacity-50"
                title={t('brief.refMax', { n: MAX_REFS })}
              >
                {uploading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <ImagePlus className="h-4 w-4" />
                )}
                <span className="text-[10px] leading-none">{t('brief.refAdd')}</span>
              </button>
            ) : null}
          </div>
          <p className="text-[11px] text-muted-foreground">{t('brief.refHint')}</p>
        </div>

        <div className="space-y-2 rounded-lg border border-dashed bg-muted/30 p-3">
          <label className="flex items-center justify-between gap-3 text-sm">
            <span>{t('brief.useLlm')}</span>
            <input
              type="checkbox"
              checked={useLlm}
              onChange={(e) => setUseLlm(e.target.checked)}
              className="h-4 w-4 accent-[hsl(var(--primary))]"
            />
          </label>
          <p className="text-[11px] text-muted-foreground">{t('brief.useLlmHint')}</p>
        </div>

        <Button className="w-full bg-brand hover:opacity-90" onClick={generate} disabled={scriptsBusy}>
          {scriptsBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
          {scriptsBusy ? t('brief.generating') : t('brief.generate')}
        </Button>

        {drafts.length ? (
          <div className="space-y-3 pt-1">
            <p className="text-xs font-medium text-muted-foreground">{t('brief.choose')}</p>
            {drafts.map((s) => {
              const active = selected === s.type
              const Icon = TYPE_ICONS[s.type]
              // A real <button> would nest the editable <textarea> inside
              // interactive-inside-interactive (invalid HTML), and clicks on
              // the textarea never reached the selection handler -- which is
              // why picking a card kept the first one selected. A div with
              // proper a11y wiring + textarea focus selects the card.
              return (
                <div
                  key={s.type}
                  role="button"
                  tabIndex={0}
                  aria-pressed={active}
                  onClick={() => setSelected(s.type)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      setSelected(s.type)
                    }
                  }}
                  className={cn(
                    'block w-full cursor-pointer rounded-lg border p-3 text-left transition-all',
                    active
                      ? 'border-primary bg-brand-soft shadow-sm ring-1 ring-primary/30'
                      : 'border-border bg-background/40 hover:bg-background',
                  )}
                >
                  <div className="mb-2 flex items-center gap-2">
                    <span
                      className={cn(
                        'flex h-5 w-5 items-center justify-center rounded-full border text-[10px]',
                        active
                          ? 'border-transparent bg-primary text-primary-foreground'
                          : 'border-muted-foreground/40 text-muted-foreground',
                      )}
                    >
                      {active ? '✓' : ''}
                    </span>
                    {Icon ? (
                      <Icon className={cn('h-4 w-4', active ? 'text-primary' : 'text-muted-foreground')} />
                    ) : null}
                    <span className="text-sm font-semibold">
                      {t(`scriptType.${s.type}`, { defaultValue: s.title })}
                    </span>
                    <Badge variant="outline" className="ml-auto text-[10px]">
                      {s.rationale}
                    </Badge>
                  </div>
                  <Textarea
                    className="min-h-[120px] resize-y cursor-text border-transparent bg-transparent text-xs leading-relaxed focus-visible:border-input focus-visible:bg-background"
                    value={narrationOf(s)}
                    onClick={(e) => e.stopPropagation()}
                    onFocus={() => setSelected(s.type)}
                    onChange={(e) =>
                      setEdited((v) => ({ ...v, [s.type]: e.target.value }))
                    }
                  />
                </div>
              )
            })}

            <Button
              className="w-full bg-brand hover:opacity-90"
              onClick={next}
              disabled={!selected || planBusy}
            >
              {planBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
              {planBusy ? t('brief.planning') : t('brief.toScenes')}
            </Button>
          </div>
        ) : null}
      </PanelBody>
    </Panel>
  )
}
