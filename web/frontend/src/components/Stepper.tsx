import { useTranslation } from 'react-i18next'
import { Check, FileText, ClipboardCheck, Clapperboard, Wand2, Send } from 'lucide-react'
import { cn } from '@/lib/utils'

export type StepId = 'script' | 'confirm' | 'render' | 'refine' | 'publish'

const ORDER: StepId[] = ['script', 'confirm', 'render', 'refine', 'publish']

const ICONS: Record<StepId, React.ComponentType<{ className?: string }>> = {
  script: FileText,
  confirm: ClipboardCheck,
  render: Clapperboard,
  refine: Wand2,
  publish: Send,
}

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
    <ol className="flex w-full items-stretch gap-1 overflow-x-auto">
      {ORDER.map((s, i) => {
        const done = i < curIdx
        const active = i === curIdx
        const unlocked = i <= maxIdx
        const Icon = ICONS[s]
        return (
          <li key={s} className="flex min-w-0 flex-1 items-center">
            <button
              type="button"
              disabled={!unlocked}
              onClick={() => unlocked && onSelect(s)}
              className={cn(
                'group flex min-w-0 flex-1 items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-all',
                active && 'bg-brand-soft',
                unlocked ? 'cursor-pointer hover:bg-accent/60' : 'cursor-not-allowed opacity-50',
              )}
            >
              <span
                className={cn(
                  'flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-xs font-semibold transition-all',
                  done && 'border-transparent bg-primary text-primary-foreground',
                  active &&
                    'border-transparent bg-brand text-white shadow-md ring-4 ring-brand/25',
                  !done && !active && 'border-muted-foreground/30 text-muted-foreground',
                )}
              >
                {done ? <Check className="h-4 w-4" /> : <Icon className="h-4 w-4" />}
              </span>
              <span className="min-w-0">
                <span
                  className={cn(
                    'block truncate text-xs font-medium leading-tight',
                    active ? 'text-foreground' : 'text-muted-foreground',
                  )}
                >
                  {t(`steps.${s}`)}
                </span>
                <span className="block text-[10px] uppercase tracking-wide text-muted-foreground/70">
                  {String(i + 1).padStart(2, '0')}
                </span>
              </span>
            </button>
            {i < ORDER.length - 1 ? (
              <span
                className={cn(
                  'h-0.5 w-5 shrink-0 rounded-full transition-colors',
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
