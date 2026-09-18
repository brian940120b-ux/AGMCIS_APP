import type { ReactNode } from 'react'

interface PanelProps {
  title: string
  subtitle?: string
  actions?: ReactNode
  children: ReactNode
  className?: string
}

/** Standard HUD panel: corner ticks, uppercase title bar, scrollable body. */
export function Panel({ title, subtitle, actions, children, className = '' }: PanelProps) {
  return (
    <section className={`hud-panel flex min-h-0 flex-col ${className}`}>
      <header className="flex shrink-0 items-center justify-between border-b border-edge px-3 py-2">
        <div className="min-w-0">
          <h2 className="hud-label text-ink-dim">{title}</h2>
          {subtitle && <p className="truncate text-[10px] text-ink-faint">{subtitle}</p>}
        </div>
        {actions}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      {/* Decorative corner ticks. */}
      <span className="pointer-events-none absolute -top-px -left-px size-2 border-t border-l border-cyan-hud/60" />
      <span className="pointer-events-none absolute -right-px -bottom-px size-2 border-r border-b border-cyan-hud/60" />
    </section>
  )
}
