import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { TerminalSquare } from 'lucide-react'
import { cn } from '@/lib/utils'

function lineClass(line: string): string {
  const s = line.trimStart()
  if (/^ERROR/i.test(s) || s.startsWith('✗') || s.startsWith('⛔')) return 'text-red-600 dark:text-red-400'
  if (/^WARN/i.test(s) || s.startsWith('⚠')) return 'text-amber-600 dark:text-amber-400'
  if (/^(\[done\]|✓|✔|done)/i.test(s)) return 'text-emerald-600 dark:text-emerald-400'
  return 'text-foreground/75'
}

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
      className="log-pane h-[240px] overflow-y-auto rounded-md border border-border/60 bg-muted/40 p-3 text-[11px] leading-relaxed"
    >
      {lines.length ? (
        lines.map((l, i) => (
          <div key={i} className={cn('whitespace-pre-wrap break-words', lineClass(l))}>
            {l}
          </div>
        ))
      ) : (
        <div className="flex h-full flex-col items-center justify-center gap-1.5 text-center text-muted-foreground/80">
          <TerminalSquare className="h-4 w-4" />
          <span className="text-[11px]">{t('log.waiting')}</span>
        </div>
      )}
    </div>
  )
}
