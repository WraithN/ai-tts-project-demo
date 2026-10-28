import { useTranslation } from 'react-i18next'
import { Check } from 'lucide-react'
import { cn } from '@/lib/utils'

export type StepId = 'script' | 'confirm' | 'render' | 'refine' | 'publish'

const ORDER: StepId[] = ['script', 'confirm', 'render', 'refine', 'publish']

export function Stepper({
  current,
  maxReached,
  onSelect,
}: {
  current: StepId
  /** Highest step the user is allowed to jump to. */
  maxReached: StepId
  onSelect: (s: StepId) => void
}) {
  const { t } = useTranslation()
  const curIdx = ORDER.indexOf(current)
  const maxIdx = ORDER.indexOf(maxReached)

  return (
    <ol className="flex w-full items-center gap-1 overflow-x-auto">
      {ORDER.map((s, i) => {
        const done = i < curIdx
        const active = i === curIdx
        const unlocked = i <= maxIdx
        return (
          <li key={s} className="flex min-w-0 flex-1 items-center gap-1">
            <button
              type="button"
              disabled={!unlocked}
              onClick={() => unlocked && onSelect(s)}
              className={cn(
                'flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors',
                active && 'bg-accent',
                unlocked ? 'cursor-pointer hover:bg-accent/60' : 'cursor-not-allowed opacity-45',
              )}
            >
              <span
                className={cn(
                  'flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold',
                  done && 'border-primary bg-primary text-primary-foreground',
                  active && !done && 'border-primary text-primary',
                  !done && !active && 'border-muted-foreground/40 text-muted-foreground',
                )}
              >
                {done ? <Check className="h-3 w-3" /> : i + 1}
              </span>
              <span
                className={cn(
                  'truncate text-xs',
                  active ? 'font-medium text-foreground' : 'text-muted-foreground',
                )}
              >
                {t(`steps.${s}`)}
              </span>
            </button>
            {i < ORDER.length - 1 ? (
              <span
                className={cn(
                  'h-px w-4 shrink-0',
                  i < curIdx ? 'bg-primary' : 'bg-border',
                )}
              />
            ) : null}
          </li>
        )
      })}
    </ol>
  )
}
