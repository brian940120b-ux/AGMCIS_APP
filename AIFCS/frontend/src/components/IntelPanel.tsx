import { Panel } from '@/components/Panel'
import { useSystemStore } from '@/stores/systemStore'

function Row({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 px-3 py-1.5">
      <span className="hud-label shrink-0">{label}</span>
      <span className={`truncate text-right text-[11px] ${accent ? 'text-cyan-hud' : 'text-ink-dim'}`}>
        {value}
      </span>
    </div>
  )
}

/**
 * Right rail. In PHASE 0 this shows the resolved runtime configuration and the
 * compute device. Scenario/agent/decision intelligence arrives from PHASE 1
 * onwards, so those fields are not invented here.
 */
export function IntelPanel() {
  // Separate selectors: a new object per call would re-render on every store write.
  const config = useSystemStore((s) => s.config)
  const compute = useSystemStore((s) => s.compute)
  const health = useSystemStore((s) => s.health)

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <Panel title="Runtime Configuration" subtitle="configs/*.yaml">
        {config ? (
          <div className="divide-y divide-edge/50">
            <Row label="Tick Rate" value={`${config.simulation.tick_rate_hz} Hz`} accent />
            <Row label="Fixed dt" value={`${(config.simulation.dt * 1000).toFixed(2)} ms`} />
            <Row label="Broadcast" value={`${config.telemetry.broadcast_rate_hz} Hz`} />
            <Row label="Deterministic" value={config.simulation.deterministic ? 'ENABLED' : 'DISABLED'} accent={config.simulation.deterministic} />
            <Row label="Seed" value={String(config.simulation.seed)} />
            <Row label="Speeds" value={config.simulation.allowed_speeds.map((s) => `${s}x`).join(' ')} />
            <Row label="Agent Rate" value={`${config.agents.decision_rate_hz} Hz`} />
            <Row label="Config Hash" value={config.config_hash} />
          </div>
        ) : (
          <p className="px-3 py-4 text-[11px] text-ink-faint">Loading configuration…</p>
        )}
      </Panel>

      <Panel title="Compute" subtitle="training & inference device">
        {compute ? (
          <div className="divide-y divide-edge/50">
            <Row
              label="Device"
              value={compute.cuda_available ? `CUDA — ${compute.gpu_name}` : 'CPU'}
              accent={compute.cuda_available}
            />
            {compute.vram_total_mb && <Row label="VRAM" value={`${compute.vram_total_mb} MB`} />}
            <Row label="PyTorch" value={compute.torch_installed ? (compute.torch_version ?? 'installed') : 'not installed'} />
            <Row label="Python" value={compute.python} />
            {health && <Row label="Backend Uptime" value={`${health.uptime_s.toFixed(0)} s`} />}
          </div>
        ) : (
          <p className="px-3 py-4 text-[11px] text-ink-faint">Probing compute device…</p>
        )}
      </Panel>
    </div>
  )
}
