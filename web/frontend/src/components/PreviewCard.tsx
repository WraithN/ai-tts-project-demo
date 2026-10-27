import { useTranslation } from 'react-i18next'
import { Film } from 'lucide-react'
import { Badge } from '@/components/ui/primitives'
import type { Job } from '@/lib/api'
import { formatDuration, formatUsd } from '@/lib/utils'

export function PreviewCard({ job }: { job: Job | null }) {
  const { t } = useTranslation()
  const done = job?.done && job.video_url

  return (
    <section className="rounded-lg border bg-card shadow-sm">
      <div className="flex items-center justify-between p-5 pb-3">
        <h2 className="text-sm font-semibold">{t('preview.title')}</h2>
        {done ? (
          <Badge variant="success">{t('stage.done')}</Badge>
        ) : (
          <Badge variant="secondary">{t('preview.empty')}</Badge>
        )}
      </div>

      <div className="px-5 pb-5">
        <div className="overflow-hidden rounded-md bg-black">
          {done ? (
            <video
              key={job!.video_url!}
              className="mx-auto max-h-[520px] w-full"
              controls
              src={job!.video_url!}
            />
          ) : (
            <div className="flex aspect-[9/16] max-h-[380px] flex-col items-center justify-center gap-2 bg-muted/40 text-muted-foreground">
              <Film className="h-8 w-8" />
              <span className="text-sm">{t('preview.empty')}</span>
              <span className="text-xs">{t('preview.emptySub')}</span>
            </div>
          )}
        </div>

        {done ? (
          <>
            <div className="mt-4 grid grid-cols-3 gap-3">
              <Stat value={formatDuration(job!.total_duration)} label={t('preview.duration')} />
              <Stat value={formatUsd(job!.cost)} label={t('preview.cost')} />
              <Stat value={String(job!.scenes ?? '—')} label={t('preview.scenes')} />
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              {t('preview.rendered', { n: job!.rendered ?? 0, m: job!.reused ?? 0 })} ·{' '}
              {t('preview.reusedHint')}
            </p>
          </>
        ) : null}
      </div>
    </section>
  )
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="rounded-md bg-muted/50 px-3 py-2 text-center">
      <div className="text-base font-semibold">{value}</div>
      <div className="text-[11px] text-muted-foreground">{label}</div>
    </div>
  )
}
