/**
 * AIFCS training centre end-to-end test (PHASE 18).
 *
 * Trains a real policy from the browser. Not a mock: the button starts a job in
 * the backend, the page follows it to completion, and a model file exists
 * afterwards.
 *
 * `train.py` named the three things the dashboard had to be able to do before
 * it was allowed a START button — progress, cancellation, surviving a reload —
 * and this checks all three, plus the refusal that keeps a job from being
 * started on top of a running simulation.
 *
 * Requires both servers:
 *   ./scripts/dev_backend.sh   and   ./scripts/dev_frontend.sh
 *   then: cd frontend && npm run test:e2e:training
 */

import { chromium } from 'playwright'

const BASE_URL = process.env.AIFCS_UI_URL ?? 'http://127.0.0.1:5173'
const API_URL = process.env.AIFCS_API_URL ?? 'http://127.0.0.1:8080'
const SHOT_DIR = process.env.AIFCS_SHOT_DIR ?? null
const EXECUTABLE = process.env.PLAYWRIGHT_CHROMIUM ?? undefined

const errors = []
// The refusal step below provokes a 400 on purpose, and the browser logs every
// failed request to the console. Ignoring console noise wholesale would hide
// real breakage, so it is only ignored while a refusal is expected.
let expectingRefusal = false
const assert = (condition, message) => {
  if (!condition) throw new Error(`assertion failed: ${message}`)
}
const shot = async (page, name) => {
  if (SHOT_DIR) await page.screenshot({ path: `${SHOT_DIR}/${name}.png` })
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const jobs = async () => (await fetch(`${API_URL}/api/training/jobs`)).json()

const status = await (await fetch(`${API_URL}/api/training/status`)).json()
if (!status.available) {
  console.log('SKIPPED — the RL stack is not installed (pip install -r requirements-ml.txt)')
  process.exit(0)
}

// Start from a known state: nothing training, nothing flying.
await fetch(`${API_URL}/api/simulation/stop`, { method: 'POST' })
if ((await jobs()).busy) {
  await fetch(`${API_URL}/api/training/stop`, { method: 'POST' })
  while ((await jobs()).busy) await sleep(1000)
}

const browser = await chromium.launch({ executablePath: EXECUTABLE })

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 950 } })
  page.on('console', (m) => {
    if (m.type() !== 'error') return
    if (expectingRefusal && /Failed to load resource/.test(m.text())) return
    errors.push(`console: ${m.text()}`)
  })
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

  await page.getByRole('button', { name: 'TRAINING', exact: true }).dispatchEvent('click')
  await page.getByRole('heading', { name: /^Training Centre$/ }).waitFor({ timeout: 15000 })
  assert(
    !(await page.getByText('NOT IMPLEMENTED').count()),
    'the training centre must not say NOT IMPLEMENTED — the button is real',
  )
  await shot(page, '40-training-idle')

  // --- Progress: start a real job and watch it ---
  await page.getByLabel('timesteps').fill('3000')
  await page.getByRole('button', { name: /START TRAINING/ }).dispatchEvent('click')
  await page.waitForTimeout(4000)

  const running = await jobs()
  assert(running.busy || running.history.length > 0, 'the button should have started a job')
  const jobId = (running.current ?? running.history[0]).job_id
  console.log(`STARTED      -> ${jobId}`)

  // --- Surviving a reload: the job lives in the server, not the page ---
  await page.reload({ waitUntil: 'domcontentloaded' })
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
  await page.getByRole('button', { name: /ENTER COMMAND CENTER/i }).dispatchEvent('click')
  await page.getByRole('button', { name: 'TRAINING', exact: true }).dispatchEvent('click')
  await page.getByText(jobId).first().waitFor({ timeout: 20000 })
  console.log('RELOAD       -> the page rejoined the job already running')

  // --- The job runs to completion and the page shows it ---
  for (let i = 0; i < 90; i += 1) {
    if (!(await jobs()).busy) break
    await sleep(2000)
  }
  const done = await (await fetch(`${API_URL}/api/training/jobs/${jobId}`)).json()
  assert(done.finished, `the job should have finished, state ${done.state}`)
  assert(done.state === 'COMPLETED', `expected COMPLETED, got ${done.state}`)
  assert(done.timesteps >= 3000, `expected at least 3000 steps, got ${done.timesteps}`)
  assert(done.metrics.length > 0, 'the job should have reported a progress curve')
  console.log(`COMPLETED    -> ${done.timesteps} steps, ${done.metrics.length} samples`)

  await page.waitForTimeout(2500)
  const body = await page.locator('body').innerText()
  assert(/COMPLETED/.test(body), 'the page should show the job as completed')
  const charts = await page.locator('svg[role="img"]').count()
  assert(charts >= 1, 'the reward curve should be drawn')
  console.log(`REWARD CURVE -> drawn from ${done.metrics.length} reported samples`)
  await shot(page, '41-training-done')

  // --- A trained policy really exists ---
  const models = await (await fetch(`${API_URL}/api/training/models`)).json()
  assert(models.count > 0, 'training should have produced a saved policy')
  console.log(`MODELS       -> ${models.count} saved`)

  // --- And the model centre shows it, with no reload ---
  // The list used to refresh only when an *evaluation* finished. A training run
  // that had just completed left its new policy invisible, with the previous
  // run's model still listed above it — which reads as the new one and is not.
  const newest = done.result?.training_id ?? models.models[0].model_id
  await page.getByText(newest).first().waitFor({ timeout: 20000 })
  console.log(`MODEL LIST   -> ${newest} appeared without a reload`)

  // --- Refusal: a job must not start on top of a running simulation ---
  await fetch(`${API_URL}/api/simulation/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scenario: 'demo_alpha' }),
  })
  await sleep(500)
  expectingRefusal = true
  await page.getByRole('button', { name: /START TRAINING/ }).dispatchEvent('click')
  await page.getByText(/simulation is running/).first().waitFor({ timeout: 10000 })
  console.log('REFUSAL      -> explained on screen, not swallowed')
  await fetch(`${API_URL}/api/simulation/stop`, { method: 'POST' })
  await shot(page, '42-training-refused')
  expectingRefusal = false

  // --- Cancellation: stop a long job and keep what it trained ---
  await sleep(500)
  await page.getByLabel('timesteps').fill('200000')
  await page.getByRole('button', { name: /START TRAINING/ }).dispatchEvent('click')
  await sleep(6000)
  // Hold the id: the history is newest-first, and picking the wrong end of it
  // reads somebody else's job.
  const longJobId = (await jobs()).current.job_id
  await page.getByRole('button', { name: /^STOP$/ }).dispatchEvent('click')
  for (let i = 0; i < 60; i += 1) {
    if (!(await jobs()).busy) break
    await sleep(2000)
  }
  const cancelled = await (await fetch(`${API_URL}/api/training/jobs/${longJobId}`)).json()
  assert(cancelled.state === 'CANCELLED', `expected CANCELLED, got ${cancelled.state}`)
  assert(
    cancelled.timesteps < cancelled.requested_timesteps,
    'a cancelled job should have stopped short of its budget',
  )
  console.log(
    `CANCELLED    -> stopped at ${cancelled.timesteps} of ${cancelled.requested_timesteps} steps`,
  )
} finally {
  await browser.close()
  await fetch(`${API_URL}/api/training/stop`, { method: 'POST' }).catch(() => {})
  await fetch(`${API_URL}/api/simulation/stop`, { method: 'POST' }).catch(() => {})
}

if (errors.length > 0) {
  console.error(`E2E FAILED — page errors:\n${errors.join('\n')}`)
  process.exit(1)
}
console.log('TRAINING E2E PASSED — a real policy was trained, watched, and cancelled from the browser.')
