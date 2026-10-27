import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/primitives'
import type { Settings } from '@/lib/api'

export function StatusCard({ settings }: { settings: Settings }) {
  const { t } = useTranslation()

  const rows: { label: string; value: string; ok: boolean }[] = [
    settings.video.ready
      ? { label: t('status.videoActive', { name: settings.video.active ?? '' }), value: t('status.ready'), ok: true }
      : { label: t('status.videoNone'), value: t('status.missing'), ok: false },
    ...settings.video.specs.map((s) => ({
      label: t(`vendor.${s.key}.label`, { defaultValue: s.label }),
      value: s.ready ? t('status.ready') : t('status.missing'),
      ok: s.ready,
    })),
    {
      label: t('status.voiceActive', { name: settings.voice.active }),
      value: settings.voice.ready ? t('status.ready') : t('status.missing'),
      ok: settings.voice.ready,
    },
    {
      label: t('status.llmActive', { provider: settings.llm.selected, model: settings.llm.model }),
      value: settings.llm.api_key_set ? t('status.ready') : t('status.missing'),
      ok: settings.llm.api_key_set,
    },
  ]

  return (
    <section className="rounded-lg border bg-card shadow-sm">
      <div className="p-5 pb-3">
        <h2 className="text-sm font-semibold">{t('status.title')}</h2>
      </div>
      <div className="space-y-1 px-5 pb-5">
        {rows.map((r) => (
          <div
            key={r.label}
            className="flex items-center justify-between gap-3 rounded-md bg-muted/40 px-3 py-2 text-xs"
          >
            <span className="truncate">{r.label}</span>
            <Badge variant={r.ok ? 'success' : 'secondary'}>{r.value}</Badge>
          </div>
        ))}
        <p className="pt-1 text-xs text-muted-foreground">
          {settings.video.ready
            ? t('status.envHint', { path: settings.env_file })
            : t('status.notReadyHint')}
        </p>
      </div>
    </section>
  )
}
