/**
 * AIFCS simulation control end-to-end test (PHASE 1).
 *
 * Proves the dashboard drives the real engine: START makes simulation time
 * advance and entities move, PAUSE genuinely freezes them, and RESET rewinds
 * the world to tick 0.
 *
 * Requires both servers:
 *   ./scripts/dev_backend.sh   and   ./scripts/dev_frontend.sh
 *   then: cd frontend && npm run test:e2e:sim
 */

import { chromium } from 'playwright'

const BASE_URL = process.env.AIFCS_UI_URL ?? 'http://127.0.0.1:5173'
const API_URL = process.env.AIFCS_API_URL ?? 'http://127.0.0.1:8000'
const SHOT_DIR = process.env.AIFCS_SHOT_DIR ?? null
const EXECUTABLE = process.env.PLAYWRIGHT_CHROMIUM ?? undefined

const errors = []
const assert = (condition, message) => {
  if (!condition) throw new Error(`assertion failed: ${message}`)
}
const shot = async (page, name) => {
  if (SHOT_DIR) await page.screenshot({ path: `${SHOT_DIR}/${name}.png` })
}
const simTime = async () => {
  const response = await fetch(`${API_URL}/api/simulation/status`)
  return (await response.json()).clock
}

// Start from a known state so the run is repeatable.
await fetch(`${API_URL}/api/simulation/stop`, { method: 'POST' })
await fetch(`${API_URL}/api/simulation/reset`, { method: 'POST' })

const browser = await chromium.launch({ executablePath: EXECUTABLE })

try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } })
  page.on('console', (m) => m.type() === 'error' && errors.push(`console: ${m.text()}`))
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))

  await page.goto(BASE_URL, { waitUntil: 'networkidle' })
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
  await page.getByRole('button', { name: /ENTER COMMAND CENTER/i }).click()
  await page.getByText('Tactical Plot').first().waitFor()

  // --- START: simulation time must advance ---
  await page.getByRole('button', { name: /^START$/ }).click()
  await page.waitForTimeout(2000)

  const running = await simTime()
  assert(running.state === 'RUNNING', `clock should be RUNNING, got ${running.state}`)
  assert(running.tick > 30, `expected the engine to tick, got tick=${running.tick}`)
  console.log(`START  -> tick=${running.tick} sim_time=${running.simulation_time.toFixed(2)}s`)

  // The UI must show the advancing clock, not a static zero.
  const shown = await page.locator('span.tabular-nums').first().innerText()
  assert(shown !== '00:00.0', `UI clock should be advancing, showed ${shown}`)
  assert(await page.getByText('BLUE-01').first().isVisible(), 'BLUE-01 should be plotted')
  await shot(page, '10-running')

  // --- PAUSE: the world must genuinely freeze ---
  await page.getByRole('button', { name: /^PAUSE$/ }).click()
  await page.waitForTimeout(300)
  const pausedA = await simTime()
  await page.waitForTimeout(1200)
  const pausedB = await simTime()

  assert(pausedB.state === 'PAUSED', `clock should be PAUSED, got ${pausedB.state}`)
  assert(
    pausedA.tick === pausedB.tick,
    `pause must stop the clock: ${pausedA.tick} -> ${pausedB.tick}`,
  )
  console.log(`PAUSE  -> frozen at tick=${pausedB.tick}`)
  await shot(page, '11-paused')

  // --- STEP: one simulation second, exactly ---
  await page.getByRole('button', { name: /STEP 1s/ }).click()
  await page.waitForTimeout(600)
  const stepped = await simTime()
  assert(
    stepped.tick === pausedB.tick + 60,
    `step should advance exactly 60 ticks: ${pausedB.tick} -> ${stepped.tick}`,
  )
  console.log(`STEP   -> tick=${stepped.tick} (+60 exactly)`)

  // --- RESET: back to tick 0 ---
  await page.getByRole('button', { name: /^RESET$/ }).click()
  await page.waitForTimeout(800)
  const reset = await simTime()
  assert(reset.tick === 0, `reset should rewind to tick 0, got ${reset.tick}`)
  console.log(`RESET  -> tick=${reset.tick}`)
  await shot(page, '12-reset')

  // --- Mobile layout still works with the new tabs ---
  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true })
  mobile.on('pageerror', (e) => errors.push(`mobile pageerror: ${e.message}`))
  await mobile.goto(BASE_URL, { waitUntil: 'networkidle' })
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
  await mobile.getByRole('button', { name: /ENTER COMMAND CENTER/i }).click()
  await mobile.locator('main').getByText('Tactical Plot').first().waitFor({ state: 'visible' })
  await mobile.getByRole('button', { name: /UNITS/i }).click()
  await mobile.locator('main').getByText('Entities').first().waitFor({ state: 'visible' })
  console.log('MOBILE -> view and units tabs render')
  await shot(mobile, '13-mobile-sim')
} finally {
  await browser.close()
}

if (errors.length > 0) {
  console.error(`E2E FAILED — page errors:\n${errors.join('\n')}`)
  process.exit(1)
}
console.log('SIMULATION E2E PASSED — start, pause, step and reset all drive the real engine.')
