import { useTranslation } from 'react-i18next'
import { Film, Loader2 } from 'lucide-react'
import { Badge } from '@/components/ui/primitives'
import { Panel, PanelHeader, PanelBody } from '@/components/ui/panel'
import type { Job } from '@/lib/api'
import { formatDuration, formatUsd } from '@/lib/utils'

export function PreviewCard({ job }: { job: Job | null }) {
  const { t } = useTranslation()
  const done = job?.done && job.video_url
  const rendering = job && !job.done && !job.error

  return (
    <Panel>
      <PanelHeader
        title={t('preview.title')}
        icon={Film}
        action={
          done ? (
            <Badge variant="success">{t('stage.done')}</Badge>
          ) : rendering ? (
            <Badge variant="outline" className="gap-1">
              <Loader2 className="h-3 w-3 animate-spin" />
              {t('stage.rendering')}
            </Badge>
          ) : (
            <Badge variant="secondary">{t('preview.empty')}</Badge>
          )
        }
      />
      <PanelBody>
        <div className="overflow-hidden rounded-xl bg-black p-[2px] bg-brand shadow-sm">
          <div className="overflow-hidden rounded-[10px] bg-black">
            {done ? (
              <video
                key={job!.video_url!}
                className="mx-auto block max-h-[520px] w-full"
                controls
                src={job!.video_url!}
              />
            ) : (
              <div className="flex min-h-[380px] items-center justify-center bg-black/60 p-4">
                <div className="relative flex aspect-[9/16] h-[360px] max-w-full flex-col items-center justify-center gap-2 overflow-hidden rounded-xl bg-muted/40 text-muted-foreground">
                  {rendering ? (
                    <>
                      <div className="absolute inset-0 animate-shimmer" />
                      <Loader2 className="relative h-8 w-8 animate-spin text-primary" />
                      <span className="relative text-sm">{t('stage.rendering')}…</span>
                    </>
                  ) : (
                    <>
                      <Film className="h-8 w-8" />
                      <span className="text-sm">{t('preview.empty')}</span>
                      <span className="text-xs">{t('preview.emptySub')}</span>
                    </>
                  )}
                </div>
              </div>
            )}
          </div>
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
      </PanelBody>
    </Panel>
  )
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="rounded-lg bg-brand-soft px-3 py-2.5 text-center ring-1 ring-brand/10">
      <div className="text-base font-semibold">{value}</div>
      <div className="text-[11px] text-muted-foreground">{label}</div>
    </div>
  )
}
