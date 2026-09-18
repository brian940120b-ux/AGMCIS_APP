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

  // PHASE 3: agents must be deciding, and the feed must show real reason codes.
  const agents = await (await fetch(`${API_URL}/api/agents`)).json()
  assert(agents.agent_count === 4, `expected 4 agents, got ${agents.agent_count}`)
  assert(agents.total_decisions > 0, 'agents should have decided by now')

  const decisions = await (await fetch(`${API_URL}/api/decisions?limit=5`)).json()
  assert(decisions.decisions.length > 0, 'the decision log should not be empty')
  assert(
    decisions.decisions.every((d) => d.reason_codes.length > 0),
    'every decision must carry at least one reason code',
  )
  console.log(
    `AGENTS -> ${agents.agent_count} agents, ${agents.total_decisions} decisions, ` +
      `latest: ${decisions.decisions.at(-1).entity_id} ${decisions.decisions.at(-1).behaviour}`,
  )
  assert(
    await page.getByText('AI Decision Feed').first().isVisible(),
    'the decision feed panel should be visible',
  )

  // PHASE 4: every command must have gone through the safety layer.
  const controller = await (await fetch(`${API_URL}/api/controller`)).json()
  assert(
    controller.commands_applied > 0,
    'the flight controller should have applied commands by now',
  )
  assert(
    typeof controller.limits.max_load_factor === 'number',
    'the safety limits should be reported',
  )
  console.log(
    `SAFETY -> ${controller.commands_applied} applied, ` +
      `${controller.commands_rejected} rejected, ` +
      `corrections: ${JSON.stringify(controller.violations)}`,
  )
  assert(
    await page.getByText('Safety Layer').first().isVisible(),
    'the safety panel should be visible',
  )

  // PHASE 5: agents must be perceiving an estimate, not the truth.
  const sensors = await (await fetch(`${API_URL}/api/sensors`)).json()
  assert(sensors.enabled, 'the sensor model should be active')
  assert(
    Object.keys(sensors.tracked_contacts).length > 0,
    'units should be holding tracks by now',
  )
  const degraded = decisions.decisions.every((d) => d.observation.confidence <= 1.0)
  assert(degraded, 'observation confidence must be a real number in [0, 1]')
  const anyImperfect = decisions.decisions.some((d) => d.observation.confidence < 1.0)
  assert(anyImperfect, 'a degraded observation cannot be perfectly confident')
  console.log(
    `SENSORS -> range ${(sensors.max_range_m / 1000).toFixed(0)} km, ` +
      `latency ${sensors.latency_s}s, dropout ${sensors.dropout_probability}, ` +
      `confidence ${decisions.decisions.at(-1).observation.confidence.toFixed(3)}`,
  )
  assert(
    await page.getByText('Perception').first().isVisible(),
    'the perception panel should be visible',
  )
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
