import * as React from 'react'
import { cn } from '@/lib/utils'

/** Card shell — replaces the hand-rolled <section> cards across the app. */
export const Panel = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <section
    ref={ref}
    className={cn('rounded-xl border bg-card text-card-foreground shadow-sm', className)}
    {...props}
  />
))
Panel.displayName = 'Panel'

type PanelHeaderProps = React.HTMLAttributes<HTMLDivElement> & {
  title: React.ReactNode
  subtitle?: React.ReactNode
  icon?: React.ComponentType<{ className?: string }>
  /** Right-aligned action slot (badge, button, etc.). */
  action?: React.ReactNode
}

export function PanelHeader({
  title,
  subtitle,
  icon: Icon,
  action,
  className,
  ...props
}: PanelHeaderProps) {
  return (
    <div
      className={cn('flex items-start justify-between gap-3 p-5 pb-3', className)}
      {...props}
    >
      <div className="flex items-start gap-3">
        {Icon ? (
          <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand text-white shadow-sm">
            <Icon className="h-4 w-4" />
          </span>
        ) : null}
        <div className="min-w-0">
          <h2 className="text-sm font-semibold leading-tight">{title}</h2>
          {subtitle ? (
            <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{subtitle}</p>
          ) : null}
        </div>
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  )
}

export function PanelBody({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('px-5 pb-5', className)} {...props} />
}
