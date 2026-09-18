import { Panel } from '@/components/Panel'
import { StateBadge } from '@/components/StateBadge'
import { useSystemStore } from '@/stores/systemStore'

/** Left rail: live subsystem states straight from GET /api/system/status. */
export function SystemStatusPanel() {
  const status = useSystemStore((s) => s.status)
  const connection = useSystemStore((s) => s.connection)
  const error = useSystemStore((s) => s.error)

  return (
    <Panel
      title="System Status"
      subtitle={status ? `${status.phase} · cfg ${status.config_hash}` : 'awaiting backend'}
      actions={
        <StateBadge
          state={connection === 'online' ? 'ONLINE' : connection === 'error' ? 'ERROR' : 'OFFLINE'}
          label={connection === 'online' ? 'LINK' : connection === 'error' ? 'NO LINK' : 'SYNC'}
          pulse
        />
      }
      className="max-h-[38vh]"
    >
      {error && <p className="border-b border-red-force/30 bg-red-force/5 p-3 text-[11px] text-red-force">{error}</p>}

      <ul className="divide-y divide-edge/60">
        {status?.subsystems.map((sub) => (
          <li key={sub.key} className="px-3 py-2">
            <div className="flex items-start justify-between gap-2">
              <p className="text-xs leading-tight text-ink">{sub.label}</p>
              <StateBadge state={sub.state} pulse />
            </div>
            <p className="mt-0.5 text-[10px] leading-snug text-ink-faint">{sub.detail}</p>
          </li>
        ))}
        {!status && !error && (
          <li className="px-3 py-4 text-[11px] text-ink-faint">Querying subsystems…</li>
        )}
      </ul>
    </Panel>
  )
}
