/**
 * AIFCS analytics end-to-end test (PHASE 17).
 *
 * Records a real run, then proves the charts drawn from it describe that run:
 * the right number of series, the right units on them, and a heatmap cell for
 * every unit against every scoring term.
 *
 * It also checks the honest-empty path. A run too short to have been sampled
 * must say so rather than draw a flat line at zero, because an empty chart is a
 * claim about the run.
 *
 * Requires both servers:
 *   ./scripts/dev_backend.sh   and   ./scripts/dev_frontend.sh
 *   then: cd frontend && npm run test:e2e:analytics
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
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

// Record a run long enough to be sampled: telemetry is stored once a second.
await fetch(`${API_URL}/api/simulation/stop`, { method: 'POST' })
const started = await fetch(`${API_URL}/api/simulation/start`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ scenario: 'demo_alpha' }),
})
assert(started.ok, `starting demo_alpha should succeed, got HTTP ${started.status}`)
const runId = (await (await fetch(`${API_URL}/api/runs/current`)).json()).run_id
await sleep(4000)
await fetch(`${API_URL}/api/simulation/stop`, { method: 'POST' })
console.log(`RECORDED     -> ${runId}`)

// The API must agree with what the charts will show.
const analytics = await (await fetch(`${API_URL}/api/analytics/runs/${runId}`)).json()
const keys = analytics.charts.map((c) => c.key)
assert(
  JSON.stringify(keys) === JSON.stringify(['altitude', 'speed', 'survival', 'coordination']),
  `expected four charts, got ${keys.join(',')}`,
)
assert(analytics.charts[0].available, 'the altitude chart should have samples')
assert(analytics.scores.terms.length > 0, 'the run should have been scored')
console.log(
  `API          -> ${analytics.sampled.telemetry_samples} samples, ` +
    `${analytics.sampled.decisions} decisions, ${analytics.scores.terms.length} scoring terms`,
)

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
  // Dispatched rather than clicked: with no GPU the 3D scene saturates the main
  // thread and Playwright's post-click settlement check starves (see PHASE 8).
  await page.getByRole('button', { name: /ENTER COMMAND CENTER/i }).dispatchEvent('click')
  await page.getByText('Tactical View').first().waitFor({ timeout: 20000 })

  await page.getByRole('button', { name: 'ANALYTICS', exact: true }).dispatchEvent('click')
  await page.getByRole('heading', { name: /^Runs$/ }).waitFor({ timeout: 15000 })

  // Open the run this test recorded, rather than whatever opened by default.
  await page.getByRole('checkbox', { name: `Compare run ${runId}` }).waitFor({ timeout: 15000 })
  await page
    .locator('li')
    .filter({ has: page.getByRole('checkbox', { name: `Compare run ${runId}` }) })
    .getByRole('button')
    .dispatchEvent('click')
  await page.waitForTimeout(2500)

  // --- Four charts, and they are drawn, not announced ---
  const charts = page.locator('svg[role="img"]')
  assert((await charts.count()) === 4, `expected 4 charts drawn, got ${await charts.count()}`)
  const lines = await page.locator('svg[role="img"] polyline').count()
  assert(lines >= 8, `expected at least 8 unit lines, got ${lines}`)
  console.log(`CHARTS       -> 4 drawn, ${lines} lines`)

  // --- Every unit against every term ---
  const cells = await page.locator('table td div[title]').count()
  const expected = analytics.scores.entities.length * analytics.scores.terms.length
  assert(cells === expected, `heatmap should have ${expected} cells, got ${cells}`)
  console.log(`HEATMAP      -> ${cells} cells for ${analytics.scores.entities.length} units`)

  // --- Hovering a chart reads a real value off it ---
  const box = await charts.first().boundingBox()
  assert(box, 'the first chart should be on screen')
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5)
  await page.waitForTimeout(300)
  const tooltip = await page.locator('text=/t\\+[0-9.]+s/').first().innerText()
  assert(/t\+[0-9.]+s/.test(tooltip), `the crosshair should report a time, got ${tooltip}`)
  console.log(`CROSSHAIR    -> reads ${tooltip.trim()}`)
  await shot(page, '30-analytics')

  // --- Comparing two runs ---
  const boxes = page.getByRole('checkbox')
  await boxes.nth(0).check()
  await boxes.nth(1).check()
  await page.getByRole('button', { name: 'COMPARE' }).dispatchEvent('click')
  await page.waitForTimeout(1500)
  const compareRows = await page.locator('table tbody tr').count()
  assert(compareRows > 0, 'the comparison should list the ticked runs')
  console.log(`COMPARE      -> ${compareRows} rows`)
  await shot(page, '31-compare')

  // --- A run too short to be sampled says so, rather than charting zero ---
  await fetch(`${API_URL}/api/simulation/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scenario: 'demo_alpha' }),
  })
  const shortId = (await (await fetch(`${API_URL}/api/runs/current`)).json()).run_id
  await fetch(`${API_URL}/api/simulation/stop`, { method: 'POST' })
  const shortBody = await (await fetch(`${API_URL}/api/analytics/runs/${shortId}`)).json()
  const altitude = shortBody.charts.find((c) => c.key === 'altitude')
  assert(altitude.available === false, 'a run with no samples should report no altitude chart')
  assert(
    /stored no telemetry/.test(altitude.detail),
    `the empty chart should say why, got ${altitude.detail}`,
  )
  console.log('EMPTY RUN    -> reported as unavailable with a reason, not charted as zero')

  // --- Mobile: the analytics stage is reachable and stacks ---
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
  await mobile.getByRole('button', { name: 'ANALYTICS', exact: true }).dispatchEvent('click')
  await mobile.getByRole('heading', { name: /^Runs$/ }).waitFor({ timeout: 15000 })
  const overflow = await mobile.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  assert(overflow <= 1, `the analytics stage should not scroll sideways on mobile, got ${overflow}px`)
  console.log('MOBILE       -> analytics stage reachable, no sideways scroll')
  await shot(mobile, '32-mobile-analytics')
} finally {
  await browser.close()
  await fetch(`${API_URL}/api/simulation/stop`, { method: 'POST' })
}

if (errors.length > 0) {
  console.error(`E2E FAILED — page errors:\n${errors.join('\n')}`)
  process.exit(1)
}
console.log('ANALYTICS E2E PASSED — the charts describe the run they were drawn from.')
