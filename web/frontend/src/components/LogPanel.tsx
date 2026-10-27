import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'

export function LogPanel({ lines }: { lines: string[] }) {
  const { t } = useTranslation()
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = ref.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines])

  return (
    <div
      ref={ref}
      className="log-pane h-[280px] overflow-y-auto rounded-md bg-foreground/95 p-3 text-[11px] leading-relaxed text-background"
    >
      {lines.length ? lines.join('\n') : t('log.waiting')}
    </div>
  )
}
