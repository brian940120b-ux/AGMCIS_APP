import { RadioTower, TriangleAlert } from 'lucide-react'
import { Panel } from '@/components/Panel'
import { useSimulationStore } from '@/stores/simulationStore'

function Row({ label, value, warn = false }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 px-3 py-1.5">
      <span className="hud-label shrink-0">{label}</span>
      <span className={`truncate text-right text-[11px] tabular-nums ${warn ? 'text-amber-hud' : 'text-ink-dim'}`}>
        {value}
      </span>
    </div>
  )
}

/**
 * Datalink readout (PHASE 6).
 *
 * The link is what lets a unit know where its teammates are when its own sensor
 * cannot see them. When it degrades, that shared picture degrades with it.
 */
export function DatalinkPanel() {
  const comms = useSimulationStore((s) => s.communications)
  const stats = comms?.stats

  const deliveryRate =
    stats && stats.sent > 0 ? (stats.delivered / stats.sent) * 100 : null

  return (
    <Panel
      title="Datalink"
      subtitle={comms?.enabled ? 'teammates share what they see' : 'link disabled'}
      actions={
        comms?.blackout_active ? (
          <TriangleAlert className="size-3.5 text-red-force" strokeWidth={1.5} />
        ) : (
          <RadioTower className="size-3.5 text-cyan-hud/70" strokeWidth={1.5} />
        )
      }
    >
      {!comms ? (
        <p className="px-3 py-4 text-[11px] text-ink-faint">Waiting for the link…</p>
      ) : (
        <div className="divide-y divide-edge/50">
          {comms.blackout_active && (
            <p className="bg-red-force/10 px-3 py-1.5 text-[10px] text-red-force">
              BLACKOUT — nothing is getting through
            </p>
          )}

          <Row label="Latency" value={`${(comms.latency_base_s * 1000).toFixed(0)} ms`} />
          <Row label="Jitter" value={`±${(comms.latency_jitter_s * 1000).toFixed(0)} ms`} />
          <Row label="Loss" value={`${(comms.packet_loss_probability * 100).toFixed(0)}%`} />
          <Row label="Report rate" value={`${comms.report_rate_hz} Hz`} />

          {stats && (
            <>
              <Row
                label="Delivered"
                value={
                  deliveryRate !== null
                    ? `${stats.delivered.toLocaleString()} (${deliveryRate.toFixed(0)}%)`
                    : '0'
                }
              />
              <Row label="Lost" value={stats.lost.toLocaleString()} warn={stats.lost > 0} />
              <Row
                label="Reordered"
                value={stats.reordered.toLocaleString()}
                warn={stats.reordered > 0}
              />
              {stats.dropped_bandwidth > 0 && (
                <Row label="Over bandwidth" value={stats.dropped_bandwidth.toLocaleString()} warn />
              )}
              {stats.blocked_blackout > 0 && (
                <Row label="Blacked out" value={stats.blocked_blackout.toLocaleString()} warn />
              )}
            </>
          )}

          <Row label="In flight" value={String(comms.in_flight)} />
        </div>
      )}
    </Panel>
  )
}
