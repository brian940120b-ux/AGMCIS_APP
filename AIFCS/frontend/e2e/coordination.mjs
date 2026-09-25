/**
 * AIFCS multi-agent coordination end-to-end test (PHASE 14-15).
 *
 * Proves the Coordination panel reports what the backend actually allocated —
 * both teams, every unit's task and the reason code behind it — and that no
 * panel paints over the one below it, at two viewport heights and in all three
 * stage modes.
 *
 * That second check is here because of a real defect: a Panel sizes itself to
 * its content, so dropping one into a `flex-[3]` slot let the decision feed
 * render 6080 px tall inside a slot that had collapsed to zero.
 *
 * Requires both servers:
 *   ./scripts/dev_backend.sh   and   ./scripts/dev_frontend.sh
 *   then: cd frontend && npm run test:e2e:coordination
 */

import { chromium } from 'playwright'

const BASE_URL = process.env.AIFCS_UI_URL ?? 'http://127.0.0.1:5173'
const API_URL = process.env.AIFCS_API_URL ?? 'http://127.0.0.1:8080'
const SHOT_DIR = process.env.AIFCS_SHOT_DIR ?? null
const EXECUTABLE = process.env.PLAYWRIGHT_CHROMIUM ?? undefined

const errors = []
const assert = (condition, message) => {
  if (!condition) throw new Error(`assertion failed: ${message}`)
}
const shot = async (page, name) => {
  if (SHOT_DIR) await page.screenshot({ path: `${SHOT_DIR}/${name}.png` })
}

/**
 * Measure every panel against the slot it was given. A panel whose box extends
 * past its slot is drawing over whatever is below it; a slot with no height at
 * all has been crushed by its siblings.
 */
const measurePanels = () => {
  const grid = document.querySelector('div.grid.min-h-0')
  if (!grid) return { error: 'no desktop grid' }
  const all = []
  const walk = (node, column) => {
    for (const kid of node.children) {
      const panel = kid.matches('section.hud-panel, div.hud-panel')
        ? kid
        : kid.querySelector(':scope > section.hud-panel, :scope > div.hud-panel')
      if (panel) {
        const slot = kid.getBoundingClientRect()
        const box = panel.getBoundingClientRect()
        all.push({
          column,
          title: panel.querySelector('h2, header')?.textContent?.trim().slice(0, 28) ?? '?',
          slotHeight: Math.round(slot.height),
          overflowPx: Math.round(box.bottom - slot.bottom),
        })
      } else if (kid.children.length > 0) {
        walk(kid, column)
      }
    }
  }
  const columns = ['left', 'center', 'right']
  ;[...grid.children].forEach((child, i) => walk(child, columns[i] ?? `column${i}`))
  // 1 px of slack: sub-pixel layout rounding is not an overlap.
  return { all, bad: all.filter((p) => p.overflowPx > 1 || p.slotHeight < 1) }
}

// Eight units in two teams of four — the scenario the commander was built for.
await fetch(`${API_URL}/api/simulation/stop`, { method: 'POST' })
const started = await fetch(`${API_URL}/api/simulation/start`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ scenario: 'team_eight' }),
})
assert(started.ok, `starting team_eight should succeed, got HTTP ${started.status}`)

const browser = await chromium.launch({ executablePath: EXECUTABLE })

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } })
  page.on('console', (m) => m.type() === 'error' && errors.push(`console: ${m.text()}`))
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))

  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' })
  await page.waitForFunction(
    () => {
      const b = [...document.querySelectorAll('button')].find((x) =>
        /ENTER COMMAND CENTER/i.test(x.textContent ?? ''),
      )
      return Boolean(b) && !b.disabled
    },
    null,
    { timeout: 20000 },
  )
  // Dispatched, not clicked: with no GPU the 3D scene saturates the main thread
  // and Playwright's post-click settlement check starves (see PHASE 8).
  await page.getByRole('button', { name: /ENTER COMMAND CENTER/i }).dispatchEvent('click')
  await page.getByText('Tactical View').first().waitFor({ timeout: 20000 })

  // Scoped to the panel heading: the word "coordination" also appears in the
  // training panel as a reward term, and a text match picks up that one.
  const panel = page
    .locator('section')
    .filter({ has: page.getByRole('heading', { name: /^Coordination$/ }) })
    .first()
  await panel.waitFor({ timeout: 20000 })
  // One full poll cycle, so the panel holds a delivered picture rather than its
  // first empty render.
  await page.waitForTimeout(3000)

  const text = await panel.innerText()
  for (const team of ['BLUE', 'RED']) {
    assert(text.includes(team), `coordination panel should list team ${team}`)
  }
  for (let i = 1; i <= 4; i += 1) {
    for (const team of ['BLUE', 'RED']) {
      const unit = `${team}-0${i}`
      assert(text.includes(unit), `coordination panel should list ${unit}`)
    }
  }
  assert(text.includes('PATROL'), 'leaders should be on PATROL')
  assert(text.includes('ESCORT'), 'wingmen should be on ESCORT')
  // Reason codes are rendered as prose; a task shown without one would mean the
  // panel is displaying an allocation it cannot explain.
  assert(/has a route/.test(text), 'a patrol task should show why it was accepted')
  assert(/leader on the link/.test(text), 'an escort task should show why it was accepted')
  assert(/\d+\.\d+s old/.test(text), 'every position should carry the age of its report')
  assert(
    /no aircraft and no path to a control surface/i.test(text),
    'the panel should state that a commander cannot fly',
  )
  console.log('COORDINATION -> both teams, 8 units, tasks and reason codes rendered')
  await shot(page, '20-coordination')

  // The panel must agree with the backend, not merely look plausible.
  const teams = await (await fetch(`${API_URL}/api/teams`)).json()
  const tasks = await (await fetch(`${API_URL}/api/tasks`)).json()
  assert(teams.count === 2, `expected 2 teams, got ${teams.count}`)
  assert(tasks.issued === 8, `expected 8 tasks issued, got ${tasks.issued}`)
  const active = Object.values(tasks.active)
  assert(active.length === 8, `expected all 8 units to hold a task, got ${active.length}`)
  // Eight tasks issued in one pass used to share a millisecond-derived id, and
  // the register then showed a unit escorting itself.
  const ids = new Set(active.map((t) => t.task_id))
  assert(ids.size === 8, `task ids must be unique — got ${ids.size} of 8`)
  console.log(`API          -> ${teams.count} teams, ${ids.size} unique task ids`)

  // --- No panel may paint over the one below it ---
  for (const [width, height] of [
    [1440, 1050],
    [1280, 720],
  ]) {
    await page.setViewportSize({ width, height })
    for (const mode of ['LIVE', 'REPLAY', 'EDIT']) {
      await page.getByRole('button', { name: mode, exact: true }).dispatchEvent('click')
      await page.waitForTimeout(1200)
      const { all, bad, error } = await page.evaluate(measurePanels)
      assert(!error, `layout check: ${error}`)
      assert(all.length > 0, `${mode} at ${width}x${height} rendered no panels`)
      assert(
        bad.length === 0,
        `${mode} at ${width}x${height}: ${bad.length} panel(s) misfit their slot — ` +
          JSON.stringify(bad),
      )
      console.log(`LAYOUT       -> ${mode} ${width}x${height}: ${all.length} panels, none overflow`)
    }
  }
  await page.getByRole('button', { name: 'LIVE', exact: true }).dispatchEvent('click')
  await shot(page, '21-layout')

  // --- Mobile: the panel is reachable on the status tab ---
  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true })
  mobile.on('pageerror', (e) => errors.push(`mobile pageerror: ${e.message}`))
  await mobile.goto(BASE_URL, { waitUntil: 'domcontentloaded' })
  await mobile.waitForFunction(
    () => {
      const b = [...document.querySelectorAll('button')].find((x) =>
        /ENTER COMMAND CENTER/i.test(x.textContent ?? ''),
      )
      return Boolean(b) && !b.disabled
    },
    null,
    { timeout: 20000 },
  )
  await mobile.getByRole('button', { name: /ENTER COMMAND CENTER/i }).dispatchEvent('click')
  await mobile.getByRole('button', { name: /STATUS/i }).dispatchEvent('click')
  await mobile
    .locator('main')
    .getByRole('heading', { name: /^Coordination$/ })
    .first()
    .waitFor({ state: 'visible', timeout: 20000 })
  console.log('MOBILE       -> coordination panel reachable on the status tab')
  await shot(mobile, '22-mobile-coordination')
} finally {
  await browser.close()
  // Put the default scenario back: the other suites share this backend.
  await fetch(`${API_URL}/api/simulation/stop`, { method: 'POST' })
  await fetch(`${API_URL}/api/simulation/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scenario: 'demo_alpha' }),
  })
  await fetch(`${API_URL}/api/simulation/stop`, { method: 'POST' })
  await fetch(`${API_URL}/api/simulation/reset`, { method: 'POST' })
}

if (errors.length > 0) {
  console.error(`E2E FAILED — page errors:\n${errors.join('\n')}`)
  process.exit(1)
}
console.log('COORDINATION E2E PASSED — the panel reports the real allocation and nothing overlaps.')
