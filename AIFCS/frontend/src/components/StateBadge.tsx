import type { SubsystemState } from '@/types/api'

/** Colour mapping for subsystem states. NOT_IMPLEMENTED is deliberately muted,
 *  never green — an unbuilt subsystem must not look operational. */
const STATE_STYLES: Record<SubsystemState, { dot: string; text: string; border: string }> = {
  ONLINE: { dot: 'bg-green-hud', text: 'text-green-hud', border: 'border-green-hud/40' },
  READY: { dot: 'bg-cyan-hud', text: 'text-cyan-hud', border: 'border-cyan-hud/40' },
  WARNING: { dot: 'bg-amber-hud', text: 'text-amber-hud', border: 'border-amber-hud/40' },
  ERROR: { dot: 'bg-red-force', text: 'text-red-force', border: 'border-red-force/40' },
  OFFLINE: { dot: 'bg-ink-faint', text: 'text-ink-faint', border: 'border-edge' },
  NOT_IMPLEMENTED: { dot: 'bg-ink-faint/50', text: 'text-ink-faint', border: 'border-edge' },
}

interface StateBadgeProps {
  state: SubsystemState
  label?: string
  pulse?: boolean
}

export function StateBadge({ state, label, pulse = false }: StateBadgeProps) {
  const style = STATE_STYLES[state]
  return (
    <span
      className={`inline-flex items-center gap-1.5 border ${style.border} px-1.5 py-0.5 text-[10px] tracking-[0.12em] ${style.text}`}
    >
      <span
        className={`size-1.5 rounded-full ${style.dot} ${pulse && state === 'ONLINE' ? 'animate-hud-pulse' : ''}`}
      />
      {label ?? state}
    </span>
  )
}
