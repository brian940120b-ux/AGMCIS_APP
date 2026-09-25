/**
 * Why the training stack cannot be used, said once, in one place.
 *
 * There are two ways for it to be unavailable and they need opposite advice.
 * Not installed: install it. Installed and will not load: installing it again
 * is the one thing that certainly will not help — on Windows this is usually a
 * missing Visual C++ runtime, and `torch` raises WinError 1114 out of c10.dll.
 *
 * Three panels used to say "not installed" unconditionally, so a broken install
 * was reported as an absent one.
 */

interface Props {
  /** What this particular panel cannot do, e.g. "nothing can be trained". */
  consequence: string
  installHint: string | null
  reason: string | null
}

export function RLUnavailable({ consequence, installHint, reason }: Props) {
  if (installHint) {
    return (
      <>
        <p className="text-[11px] text-amber-hud">
          The reinforcement-learning stack is not installed, so {consequence}.
        </p>
        <p className="mt-1 font-mono text-[10px] break-all text-ink-faint">{installHint}</p>
      </>
    )
  }

  return (
    <>
      <p className="text-[11px] text-amber-hud">
        The reinforcement-learning stack is installed but will not load, so {consequence}.
      </p>
      {reason && (
        <p className="mt-1 font-mono text-[10px] break-all text-ink-faint">{reason}</p>
      )}
      <p className="mt-2 text-[10px] leading-relaxed text-ink-dim">
        Run <code className="text-ink-faint">aifcs doctor</code> for what to do about it. On
        Windows this is usually the Microsoft Visual C++ Redistributable (x64).
      </p>
    </>
  )
}
