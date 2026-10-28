import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, Loader2, Pencil, RefreshCw, Video } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/primitives'
import type { PlanScene } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * Step 2: the confirmation gate.
 *
 * Everything here is editable and nothing is rendered until the user hits
 * confirm. That is the point -- the model's first pass at scene copy is a
 * draft, and paying for eight clips before reading the hook is how you burn
 * a budget.
 */
export function SceneConfirm({
  scenes,
  busy,
  onBack,
  onReparse,
  onConfirm,
}: {
  scenes: PlanScene[]
  busy: boolean
  onBack: () => void
  onReparse: () => void
  onConfirm: (scenes: PlanScene[]) => void
}) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState<PlanScene[]>(scenes)

  // Re-seed when a fresh plan arrives (re-parse, or a new script).
  useEffect(() => setDraft(scenes), [scenes])

  const patch = (id: string, key: 'vo' | 'prompt' | 'shot' | 'angle', value: string) =>
    setDraft((v) => v.map((s) => (s.id === id ? { ...s, [key]: value } : s)))

  const patchElements = (id: string, raw: string) =>
    setDraft((v) =>
      v.map((s) =>
        s.id === id
          ? { ...s, elements: raw.split(',').map((x) => x.trim()).filter(Boolean) }
          : s,
      ),
    )

  const remove = (id: string) => setDraft((v) => v.filter((s) => s.id !== id))
  const move = (id: string, dir: -1 | 1) =>
    setDraft((v) => {
      const i = v.findIndex((s) => s.id === id)
      const j = i + dir
      if (i < 0 || j < 0 || j >= v.length) return v
      const next = [...v]
      ;[next[i], next[j]] = [next[j], next[i]]
      return next.map((s, k) => ({ ...s, index: k }))
    })

  const totalWords = draft.reduce((n, s) => n + s.vo.trim().split(/\s+/).filter(Boolean).length, 0)
  // ~2.6 words/sec is the usual short-form VO rate; good enough to warn on.
  const estSeconds = Math.round(totalWords / 2.6)

  return (
    <section className="rounded-lg border bg-card shadow-sm">
      <div className="flex items-center justify-between p-5 pb-3">
        <div>
          <h2 className="text-sm font-semibold">{t('confirm.title')}</h2>
          <p className="mt-1 text-xs text-muted-foreground">{t('confirm.subtitle')}</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline">
            {t('confirm.count', { n: draft.length })}
          </Badge>
          <Badge variant="outline">~{estSeconds}s</Badge>
        </div>
      </div>

      <div className="space-y-3 px-5 pb-5">
        <div className="max-h-[520px] space-y-3 overflow-y-auto pr-1">
          {draft.map((s, i) => (
            <div key={s.id} className="rounded-md border bg-background/40 p-3">
              <div className="mb-2 flex items-center gap-2">
                <span className="font-mono text-xs text-muted-foreground">
                  {String(i + 1).padStart(2, '0')}
                </span>
                <Badge variant="secondary">{t(`beat.${s.beat}`, { defaultValue: s.beat })}</Badge>
                <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
                  <Pencil className="h-3 w-3" />
                  {s.id}
                </span>
                <div className="ml-auto flex items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-1.5 text-xs"
                    disabled={i === 0}
                    onClick={() => move(s.id, -1)}
                    title={t('confirm.up')}
                  >
                    ↑
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-1.5 text-xs"
                    disabled={i === draft.length - 1}
                    onClick={() => move(s.id, 1)}
                    title={t('confirm.down')}
                  >
                    ↓
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-1.5 text-xs text-muted-foreground hover:text-destructive"
                    onClick={() => remove(s.id)}
                    title={t('confirm.remove')}
                  >
                    ✕
                  </Button>
                </div>
              </div>

              <div className="space-y-2">
                <div className="space-y-1">
                  <Label className="text-[11px] text-muted-foreground">
                    {t('confirm.vo')}
                  </Label>
                  <Textarea
                    className={cn('min-h-[56px] resize-y text-sm leading-relaxed')}
                    value={s.vo}
                    onChange={(e) => patch(s.id, 'vo', e.target.value)}
                  />
                  <p className="text-[11px] text-muted-foreground">{t('confirm.captionNote')}</p>
                </div>

                <div className="space-y-1">
                  <Label className="text-[11px] text-muted-foreground">
                    {t('confirm.prompt')}
                  </Label>
                  <Textarea
                    className="min-h-[52px] resize-y text-sm text-muted-foreground"
                    value={s.prompt}
                    onChange={(e) => patch(s.id, 'prompt', e.target.value)}
                  />
                </div>

                <details className="group rounded-md border border-dashed border-muted-foreground/30 px-2 py-1.5">
                  <summary className="cursor-pointer text-[11px] text-muted-foreground">
                    {t('confirm.shot')} · {t('confirm.angle')} · {t('confirm.elements')}
                  </summary>
                  <div className="mt-2 grid grid-cols-2 gap-2">
                    <div className="space-y-1">
                      <Label className="text-[10px] text-muted-foreground">{t('confirm.shot')}</Label>
                      <input
                        className="h-7 w-full rounded border bg-background px-2 text-xs"
                        value={s.shot}
                        onChange={(e) => patch(s.id, 'shot', e.target.value)}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-[10px] text-muted-foreground">{t('confirm.angle')}</Label>
                      <input
                        className="h-7 w-full rounded border bg-background px-2 text-xs"
                        value={s.angle}
                        onChange={(e) => patch(s.id, 'angle', e.target.value)}
                      />
                    </div>
                    <div className="col-span-2 space-y-1">
                      <Label className="text-[10px] text-muted-foreground">{t('confirm.elements')}</Label>
                      <input
                        className="h-7 w-full rounded border bg-background px-2 text-xs"
                        value={(s.elements || []).join(', ')}
                        onChange={(e) => patchElements(s.id, e.target.value)}
                      />
                    </div>
                  </div>
                </details>
              </div>
            </div>
          ))}
        </div>

        <div className="flex flex-col gap-2 pt-1 sm:flex-row">
          <Button variant="ghost" onClick={onBack} disabled={busy} className="sm:w-auto">
            {t('confirm.back')}
          </Button>
          <Button variant="secondary" onClick={onReparse} disabled={busy} className="sm:w-auto">
            <RefreshCw className={cn('h-4 w-4', busy && 'animate-spin')} />
            {t('confirm.reparse')}
          </Button>
          <Button
            className="sm:ml-auto sm:w-auto"
            onClick={() => onConfirm(draft)}
            disabled={busy || draft.length === 0}
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
            {t('confirm.confirm')}
          </Button>
        </div>

        <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
          <Video className="mt-0.5 h-3 w-3 shrink-0" />
          {t('confirm.spendNote')}
        </p>
      </div>
    </section>
  )
}
