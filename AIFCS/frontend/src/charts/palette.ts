/**
 * Chart colour, as parameters rather than taste (PHASE 17).
 *
 * Every set here was run through the dataviz validator against the panel
 * surface `#0c1322`: lightness band, chroma floor, CVD separation,
 * normal-vision separation and contrast. The hex values live in `index.css` as
 * tokens; this module maps a *job* onto them, so a chart asks for "the colour
 * of team BLUE" and never picks a hue itself.
 *
 * Two findings from that validation shaped the charts themselves:
 *
 * - **No six-hue categorical set survives an all-pairs check.** Blue and violet
 *   are ΔE 0.3 apart under deuteranopia whichever way they are stepped. So the
 *   six scoring terms are a heatmap on one hue, not six colours.
 * - **Eight units cannot each have a hue either.** Unit lines are coloured by
 *   team — two hues, safe on every pair — and identity comes from a direct
 *   label on the line.
 */

/** Team series. Safe on every pair: worst ΔE 24.1 under protanopia. */
export const TEAM_COLOUR: Record<string, string> = {
  BLUE: 'var(--color-chart-blue)',
  RED: 'var(--color-chart-red)',
}

/** Anything whose group is not a known team. Neutral, never a generated hue. */
export const UNGROUPED_COLOUR = 'var(--color-ink-faint)'

export function groupColour(group: string): string {
  return TEAM_COLOUR[group] ?? UNGROUPED_COLOUR
}

/**
 * Behaviour series, in the stacking order they were validated in. The check is
 * on adjacent pairs, so a stack that reorders these loses the guarantee — the
 * backend emits them in this order for the same reason.
 */
export const BEHAVIOUR_ORDER = ['HOLD', 'NAVIGATE', 'PATROL', 'FORMATION', 'AVOID'] as const

export const BEHAVIOUR_COLOUR: Record<string, string> = {
  HOLD: 'var(--color-chart-hold)',
  NAVIGATE: 'var(--color-chart-navigate)',
  PATROL: 'var(--color-chart-patrol)',
  FORMATION: 'var(--color-chart-formation)',
  AVOID: 'var(--color-chart-avoid)',
}

/** A behaviour the backend reported that has no assigned step. */
export const UNKNOWN_BEHAVIOUR_COLOUR = 'var(--color-ink-faint)'

export function behaviourColour(behaviour: string): string {
  return BEHAVIOUR_COLOUR[behaviour] ?? UNKNOWN_BEHAVIOUR_COLOUR
}

/** Sequential ramp: one hue, light to dark, monotone in lightness. */
export const RAMP = [
  'var(--color-ramp-1)',
  'var(--color-ramp-2)',
  'var(--color-ramp-3)',
  'var(--color-ramp-4)',
  'var(--color-ramp-5)',
]

/** The raw hex of the ramp, for deciding label ink against a filled cell. */
const RAMP_LUMINANCE = [0.1, 0.19, 0.31, 0.51, 0.76]

/** Pick a ramp step for a 0..1 magnitude. */
export function rampStep(fraction: number): number {
  if (!Number.isFinite(fraction)) return 0
  const clamped = Math.max(0, Math.min(1, fraction))
  return Math.min(RAMP.length - 1, Math.floor(clamped * RAMP.length))
}

export function rampColour(fraction: number): string {
  return RAMP[rampStep(fraction)]
}

/**
 * Ink for a label sitting inside a filled cell. Text never wears the data
 * colour, but a label on top of a fill has to clear the fill it sits on.
 */
export function inkOnRamp(fraction: number): string {
  return RAMP_LUMINANCE[rampStep(fraction)] > 0.45 ? 'var(--color-void)' : 'var(--color-ink)'
}
