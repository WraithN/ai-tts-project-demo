import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CornerDownLeft, Loader2, MessageSquare } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { api, errorText } from '@/lib/api'
import { Switch } from '@/components/ui/primitives'
import { cn } from '@/lib/utils'

interface Turn {
  role: 'user' | 'system'
  text: string
  meta?: string
}

export function EditPanel({
  jobId,
  onRender,
}: {
  jobId: string | null
  /** Called with the new job id when an edit triggers a partial re-render. */
  onRender: (jobId: string) => void
}) {
  const { t } = useTranslation()
  const [instruction, setInstruction] = useState('')
  const [busy, setBusy] = useState(false)
  const [apply, setApply] = useState(true)
  const [turns, setTurns] = useState<Turn[]>([{ role: 'system', text: t('edit.welcome') }])

  const send = async () => {
    const text = instruction.trim()
    if (!text) return
    if (!jobId) {
      setTurns((v) => [...v, { role: 'system', text: t('edit.needJob') }])
      return
    }
    setTurns((v) => [...v, { role: 'user', text }, { role: 'system', text: '…' }])
    setInstruction('')
    setBusy(true)
    try {
      const r = await api.planEdit({
        job_id: jobId,
        instruction: text,
        use_llm: true,
        apply,
      })
      let meta = r.explanation || t('edit.analysisOnly')
      if (r.job_id) {
        const rebuilt = r.rebuilt?.length ?? 0
        meta = t('edit.rebuilding', { n: rebuilt, total: r.scene_ids.length || rebuilt })
        onRender(r.job_id)
      }
      setTurns((v) => [
        ...v.slice(0, -1),
        {
          role: 'system',
          text: `${r.intent} · ${t('edit.scenesAffected', { n: r.scene_ids.length })}\n${r.ops
            .map((o) => `${o.path} = ${JSON.stringify(o.value)}`)
            .join('\n')}`,
          meta,
        },
      ])
    } catch (e) {
      setTurns((v) => [...v.slice(0, -1), { role: 'system', text: t('edit.fail', { msg: errorText(e) }) }])
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3">
      <div className="max-h-[280px] space-y-2 overflow-y-auto">
        {turns.map((turn, i) => (
          <div
            key={i}
            className={cn(
              'max-w-[88%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm',
              turn.role === 'user'
                ? 'ml-auto bg-primary text-primary-foreground'
                : 'bg-muted text-foreground',
            )}
          >
            {turn.text}
            {turn.meta ? (
              <div className="mt-1 text-[11px] opacity-70">{turn.meta}</div>
            ) : null}
          </div>
        ))}
      </div>

      <div className="flex items-center gap-2">
        <Input
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void send()
          }}
          placeholder={t('edit.placeholder')}
        />
        <Button onClick={send} disabled={busy || !instruction.trim()} className="shrink-0">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <MessageSquare className="h-4 w-4" />}
          {apply ? t('edit.apply') : t('edit.analyze')}
        </Button>
      </div>

      <label className="flex items-center justify-between gap-3 rounded-md border p-2.5 text-xs">
        <span>
          <span className="font-medium">{t('edit.applySwitch')}</span>
          <span className="mt-0.5 block text-muted-foreground">{t('edit.applySwitchHint')}</span>
        </span>
        <Switch checked={apply} onCheckedChange={setApply} />
      </label>

      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <CornerDownLeft className="h-3 w-3" />
        {t('edit.hint')}
      </p>
    </div>
  )
}
