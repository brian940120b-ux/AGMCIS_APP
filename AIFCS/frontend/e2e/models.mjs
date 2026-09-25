/**
 * AIFCS model centre end-to-end test (PHASE 19).
 *
 * Evaluates a real saved policy from the browser and checks the score lands on
 * its card, then archives and restores it.
 *
 * The important part is the refusal. A policy trained against an older
 * observation layout loads cleanly and produces actions from numbers that
 * stopped meaning what they meant, so it must be marked INCOMPATIBLE and must
 * not be evaluable. To test that, this writes a policy with a stale card — it
 * reaches the model directory directly, which only works because the e2e and
 * the backend share a machine, and it cleans up after itself.
 *
 * Requires both servers:
 *   ./scripts/dev_backend.sh   and   ./scripts/dev_frontend.sh
 *   then: cd frontend && npm run test:e2e:models
 */

import { copyFileSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const BASE_URL = process.env.AIFCS_UI_URL ?? 'http://127.0.0.1:5173'
const API_URL = process.env.AIFCS_API_URL ?? 'http://127.0.0.1:8000'
const SHOT_DIR = process.env.AIFCS_SHOT_DIR ?? null
const EXECUTABLE = process.env.PLAYWRIGHT_CHROMIUM ?? undefined
const MODEL_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', 'models')
const STALE_ID = 'ppo-e2e-stale-layout'

const errors = []
const assert = (condition, message) => {
  if (!condition) throw new Error(`assertion failed: ${message}`)
}
const shot = async (page, name) => {
  if (SHOT_DIR) await page.screenshot({ path: `${SHOT_DIR}/${name}.png` })
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const models = async (archived = false) =>
  (await fetch(`${API_URL}/api/models?include_archived=${archived}`)).json()
const jobs = async () => (await fetch(`${API_URL}/api/training/jobs`)).json()

const cleanup = () => {
  for (const suffix of ['.zip', '.json']) {
    rmSync(join(MODEL_DIR, `${STALE_ID}${suffix}`), { force: true })
    rmSync(join(MODEL_DIR, 'archive', `${STALE_ID}${suffix}`), { force: true })
  }
}

const catalogue = await models()
if (!catalogue.available) {
  console.log('SKIPPED — the RL stack is not installed (pip install -r requirements-ml.txt)')
  process.exit(0)
}

// Need a real policy to evaluate. Train the shortest useful one if there is none.
let subject = catalogue.models.find((m) => m.compatibility.runnable && !m.archived)
if (!subject) {
  console.log('TRAINING     -> no policy saved yet, training a short one first')
  await fetch(`${API_URL}/api/training/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ algorithm: 'ppo', timesteps: 2000 }),
  })
  for (let i = 0; i < 120; i += 1) {
    if (!(await jobs()).busy) break
    await sleep(2000)
  }
  subject = (await models()).models.find((m) => m.compatibility.runnable && !m.archived)
}
assert(subject, 'there should be a runnable policy to evaluate')
console.log(`SUBJECT      -> ${subject.model_id} (${subject.compatibility.verdict})`)

// A policy whose observation layout has moved on. Same weights, stale card.
cleanup()
const staleCard = JSON.parse(readFileSync(join(MODEL_DIR, `${subject.model_id}.json`), 'utf8'))
staleCard.environment.observation_layout_version = catalogue.current_layout + 1
mkdirSync(MODEL_DIR, { recursive: true })
copyFileSync(join(MODEL_DIR, `${subject.model_id}.zip`), join(MODEL_DIR, `${STALE_ID}.zip`))
writeFileSync(join(MODEL_DIR, `${STALE_ID}.json`), JSON.stringify(staleCard, null, 2))

const browser = await chromium.launch({ executablePath: EXECUTABLE })

try {
  // --- The refusal, at the API ---
  const stale = await (await fetch(`${API_URL}/api/models/${STALE_ID}`)).json()
  assert(
    stale.compatibility.verdict === 'INCOMPATIBLE',
    `a stale layout should read INCOMPATIBLE, got ${stale.compatibility.verdict}`,
  )
  assert(stale.compatibility.runnable === false, 'an incompatible policy must not be runnable')
  const refused = await fetch(`${API_URL}/api/models/${STALE_ID}/evaluate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ episodes: 1 }),
  })
  assert(refused.status === 409, `evaluating it should be refused, got HTTP ${refused.status}`)
  console.log('REFUSAL      -> a stale-layout policy is INCOMPATIBLE and cannot be evaluated')

  const page = await browser.newPage({ viewport: { width: 1280, height: 1100 } })
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
  await page.getByRole('button', { name: 'TRAINING', exact: true }).dispatchEvent('click')
  await page.getByRole('heading', { name: /^Model Centre$/ }).waitFor({ timeout: 15000 })
  await page.waitForTimeout(1500)

  // --- The refusal, on screen ---
  const staleEvaluate = page.getByRole('button', { name: `Evaluate ${STALE_ID}` })
  await staleEvaluate.waitFor({ timeout: 10000 })
  assert(await staleEvaluate.isDisabled(), 'the evaluate button must be disabled for it')
  assert(
    (await page.getByText('INCOMPATIBLE').count()) > 0,
    'the incompatible policy should be labelled on screen',
  )
  console.log('UI           -> marked INCOMPATIBLE, its evaluate button disabled')
  await shot(page, '50-model-centre')

  // --- Evaluate a real one, from the browser ---
  await page.getByLabel('Evaluation episodes').fill('1')
  await page.getByRole('button', { name: `Evaluate ${subject.model_id}` }).dispatchEvent('click')
  await page.waitForTimeout(3000)
  const running = await jobs()
  assert(
    running.busy || running.history.some((j) => j.kind === 'EVALUATE'),
    'the button should have started an evaluation job',
  )
  for (let i = 0; i < 90; i += 1) {
    if (!(await jobs()).busy) break
    await sleep(2000)
  }
  const done = (await jobs()).history.find((j) => j.kind === 'EVALUATE')
  assert(done, 'an evaluation job should be in the history')
  assert(done.state === 'COMPLETED', `expected COMPLETED, got ${done.state}`)
  console.log(`EVALUATED    -> ${done.model_id} over ${done.result.evaluation.episodes} episode(s)`)

  // --- And the score is on the card, not just in memory ---
  const scored = await (await fetch(`${API_URL}/api/models/${subject.model_id}`)).json()
  assert(scored.evaluation, 'the evaluation should be written onto the card')
  assert(
    typeof scored.evaluation.mean_reward === 'number',
    'the card should carry a real mean reward',
  )
  console.log(`CARD         -> mean reward ${scored.evaluation.mean_reward} persisted`)

  // --- The list refreshes itself when the job ends ---
  await page.waitForTimeout(3000)
  await page
    .getByText(new RegExp(`scored .* over ${done.result.evaluation.episodes} ep`))
    .first()
    .waitFor({ timeout: 15000 })
  console.log('LIST         -> refreshed itself with the new score, no reload needed')

  // --- Archive and restore ---
  await page.getByRole('button', { name: `Archive ${STALE_ID}` }).dispatchEvent('click')
  await page.waitForTimeout(1500)

  const active = await models(false)
  assert(
    !active.models.some((m) => m.model_id === STALE_ID),
    'an archived policy should leave the active list',
  )
  const all = await models(true)
  assert(
    all.models.some((m) => m.model_id === STALE_ID && m.archived),
    'but it should still be on disk, marked archived',
  )
  console.log('ARCHIVE      -> out of the list, still on disk')

  const restored = await fetch(`${API_URL}/api/models/${STALE_ID}/restore`, { method: 'POST' })
  assert(restored.ok, 'restoring should succeed')
  assert(
    (await models(false)).models.some((m) => m.model_id === STALE_ID),
    'a restored policy should come back to the active list',
  )
  console.log('RESTORE      -> back in the list')
  await shot(page, '51-model-archived')
} finally {
  await browser.close()
  cleanup()
  await fetch(`${API_URL}/api/training/stop`, { method: 'POST' }).catch(() => {})
}

if (errors.length > 0) {
  console.error(`E2E FAILED — page errors:\n${errors.join('\n')}`)
  process.exit(1)
}
console.log('MODELS E2E PASSED — a real policy was measured, and a stale one was refused.')
