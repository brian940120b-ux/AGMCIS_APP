import { useState } from 'react'
import { Download, FileCode2, Upload } from 'lucide-react'
import { Panel } from '@/components/Panel'
import { useScenarioStore } from '@/stores/scenarioStore'

/**
 * IMPORT / EXPORT (PHASE 10).
 *
 * Export hands back the file's own text, comments included, rather than a
 * re-serialisation — re-dumping a hand-written scenario would silently strip
 * the comments that explain it.
 *
 * Import goes through the same validator as everything else, so pasted YAML
 * that would not load is refused with the reason rather than written and
 * discovered later.
 */
export function ScenarioYamlPanel() {
  const { catalogue, openName, yamlText, busy, error, notice, exportYaml, importYaml, setYamlText } =
    useScenarioStore()
  const [importName, setImportName] = useState('')

  const download = () => {
    // A real download, not a placeholder: the text becomes a file the browser
    // saves, so a scenario can leave the machine it was written on.
    const blob = new Blob([yamlText], { type: 'application/x-yaml' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${openName ?? 'scenario'}.yaml`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  return (
    <Panel
      title="Import / Export"
      subtitle="scenario files as text"
      actions={<FileCode2 className="size-3.5 text-cyan-hud" strokeWidth={1.5} />}
    >
      <div className="space-y-2 px-3 py-2">
        {error && <p className="text-[10px] text-rose-400">{error}</p>}
        {notice && !error && <p className="text-[10px] text-green-hud">{notice}</p>}

        <div className="flex flex-wrap items-center gap-1">
          <span className="hud-label mr-1">export</span>
          {catalogue.slice(0, 8).map((entry) => (
            <button
              key={entry.name}
              type="button"
              disabled={busy || !entry.readable}
              onClick={() => void exportYaml(entry.name)}
              className="border border-edge px-1.5 py-0.5 font-mono text-[9px] text-ink-faint transition hover:border-cyan-hud/60 hover:text-cyan-hud disabled:opacity-40"
            >
              {entry.name}
            </button>
          ))}
        </div>

        <textarea
          rows={12}
          value={yamlText}
          spellCheck={false}
          placeholder="Export a scenario to see its YAML here, or paste one to import."
          onChange={(e) => setYamlText(e.target.value)}
          aria-label="Scenario YAML"
          className="w-full resize-y border border-edge bg-void px-2 py-1.5 font-mono text-[10px] leading-relaxed text-ink outline-none focus:border-cyan-hud/60"
        />

        <div className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            disabled={!yamlText}
            onClick={download}
            className="inline-flex items-center gap-1 border border-edge px-2 py-1 text-[10px] text-ink-faint transition hover:border-cyan-hud/60 hover:text-cyan-hud disabled:opacity-40"
          >
            <Download className="size-3" strokeWidth={1.5} /> DOWNLOAD
          </button>
          <input
            value={importName}
            placeholder="name (optional)"
            onChange={(e) => setImportName(e.target.value)}
            className="w-32 border border-edge bg-void px-1.5 py-1 font-mono text-[10px] text-ink outline-none focus:border-cyan-hud/60"
          />
          <button
            type="button"
            disabled={busy || !yamlText.trim()}
            onClick={() => void importYaml(yamlText, importName || undefined)}
            title="Validate this YAML and store it as a scenario"
            className="inline-flex items-center gap-1 border border-cyan-hud/60 bg-cyan-hud/10 px-2 py-1 text-[10px] text-cyan-hud transition hover:bg-cyan-hud/20 disabled:opacity-40"
          >
            <Upload className="size-3" strokeWidth={1.5} /> IMPORT
          </button>
        </div>

        <p className="text-[9px] leading-relaxed text-ink-faint">
          Import is validated by the same parser the simulation uses. YAML that would not load is
          refused with the reason, rather than written and found broken later.
        </p>
      </div>
    </Panel>
  )
}
