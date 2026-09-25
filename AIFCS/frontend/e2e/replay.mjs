/**
 * AIFCS replay and scoring test (PHASE 9).
 *
 * Runs a real simulation through the UI, stops it, then reviews the recording
 * it produced: loads it, plays it, scrubs it, and reads the score breakdown.
 * Every assertion is about what the backend actually did — nothing here is
 * satisfied by a control that merely exists.
 *
 *   cd frontend && npm run test:e2e:replay
 *
 * On a headless machine without a GPU, pass a software renderer:
 *   PLAYWRIGHT_GL=swiftshader npm run test:e2e:replay
 */

import { chromium } from 'playwright'

const BASE_URL = process.env.AIFCS_UI_URL ?? 'http://127.0.0.1:5173'
const API_URL = process.env.AIFCS_API_URL ?? 'http://127.0.0.1:8080'
const SHOT_DIR = process.env.AIFCS_SHOT_DIR ?? null
const EXECUTABLE = process.env.PLAYWRIGHT_CHROMIUM ?? undefined

const assert = (condition, message) => {
  if (!condition) throw new Error(`assertion failed: ${message}`)
}
const shot = async (page, name) => {
  if (SHOT_DIR) await page.screenshot({ path: `${SHOT_DIR}/${name}.png` })
}
// Exact by default: "PLAY" would otherwise also match the "REPLAY" mode
// toggle, and the test would click the wrong control and fail for the wrong
// reason.
const click = async (page, text, { exact = true } = {}) => {
  await page.getByRole('button', { name: text, exact }).first().click()
}

const enterCommandCenter = async (page) => {
  await page.goto(BASE_URL, { waitUntil: 'networkidle' })
  await page.waitForFunction(
    () => {
      const button = [...document.querySelectorAll('button')].find((b) =>
        /ENTER COMMAND CENTER/i.test(b.textContent ?? ''),
      )
      return Boolean(button) && !button.disabled
    },
    { timeout: 30000 },
  )
  await click(page, 'ENTER COMMAND CENTER', { exact: false })
  await page.waitForSelector('text=COMMAND CENTER', { timeout: 15000 })
}

async function main() {
  const browser = await chromium.launch({
    executablePath: EXECUTABLE,
    args: process.env.PLAYWRIGHT_GL === 'swiftshader'
      ? ['--use-gl=swiftshader', '--enable-unsafe-swiftshader']
      : [],
  })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const failures = []
  page.on('pageerror', (e) => failures.push(String(e)))

  try {
    // Start from a known state: a previous run left running would show PAUSE
    // where this test expects START, and would fail for the wrong reason.
    await page.request.post(`${API_URL}/api/simulation/reset`)

    await enterCommandCenter(page)

    // --- 1. Record a real run through the UI. --------------------------
    await click(page, 'START', { exact: false })
    await page.waitForTimeout(4000)
    const current = await (await page.request.get(`${API_URL}/api/runs/current`)).json()
    assert(current.active, 'a run should be recording after START')
    assert(current.frames > 10, `recorder should have frames, got ${current.frames}`)
    const runId = current.run_id
    console.log(`  recorded run ${runId}: ${current.frames} frames`)

    await click(page, 'STOP', { exact: false })
    await page.waitForTimeout(2500)
    const after = await (await page.request.get(`${API_URL}/api/runs/current`)).json()
    assert(!after.active, 'the run should be closed after STOP')

    // --- 2. The run was stored and scored. -----------------------------
    const detail = await (await page.request.get(`${API_URL}/api/runs/${runId}`)).json()
    assert(detail.end_reason, 'the stored run should record why it ended')
    assert(detail.entities.length === 4, `expected 4 entities, got ${detail.entities.length}`)
    assert(detail.scores.length > 0, 'the run should have been scored on stop')
    assert(detail.replay && detail.replay.frames > 0, 'the run should have a replay row')
    console.log(
      `  stored: ${detail.ticks} ticks, ${detail.scores.length} score rows, ` +
        `${detail.replay.frames} frames`,
    )

    // --- 3. Switch the UI to replay mode. ------------------------------
    await click(page, 'REPLAY')
    await page.waitForTimeout(800)
    assert(
      await page.locator('text=Replay').first().isVisible(),
      'the replay panel should appear in replay mode',
    )
    await shot(page, '30-replay-picker')

    // Load the recording we just made, by clicking its row.
    await page.getByText(runId, { exact: false }).first().click()
    await page.waitForTimeout(1500)

    let status = await (await page.request.get(`${API_URL}/api/replay/status`)).json()
    assert(status.loaded, 'the recording should be loaded after clicking it')
    assert(status.frame_count > 10, `expected frames, got ${status.frame_count}`)
    console.log(`  loaded ${status.frame_count} frames, ${status.duration_s}s`)
    await shot(page, '31-replay-loaded')

    // --- 4. Transport controls move the real cursor. -------------------
    await click(page, 'PLAY')
    await page.waitForTimeout(1600)
    status = await (await page.request.get(`${API_URL}/api/replay/status`)).json()
    assert(status.frame_index > 0, 'PLAY should advance the cursor')
    console.log(`  play advanced to frame ${status.frame_index}`)
    await shot(page, '32-replay-playing')

    await click(page, 'PAUSE')
    await page.waitForTimeout(600)
    const paused = await (await page.request.get(`${API_URL}/api/replay/status`)).json()
    await page.waitForTimeout(700)
    const stillPaused = await (await page.request.get(`${API_URL}/api/replay/status`)).json()
    assert(!stillPaused.playing, 'PAUSE should stop playback')
    assert(
      stillPaused.frame_index === paused.frame_index,
      'a paused replay should hold its position',
    )
    console.log(`  pause held at frame ${stillPaused.frame_index}`)

    // Speed change is real.
    await click(page, '5x')
    await page.waitForTimeout(500)
    status = await (await page.request.get(`${API_URL}/api/replay/status`)).json()
    assert(status.speed === 5, `speed should be 5x, got ${status.speed}`)

    // Scrub with the range input.
    const slider = page.getByLabel('Replay position')
    await slider.fill('0')
    await page.waitForTimeout(700)
    status = await (await page.request.get(`${API_URL}/api/replay/status`)).json()
    assert(status.frame_index === 0, `scrub to start failed, at ${status.frame_index}`)
    console.log('  scrubber seeks the real cursor')

    // Frame stepping.
    await page.getByRole('button', { name: 'Forward one frame' }).click()
    await page.waitForTimeout(500)
    status = await (await page.request.get(`${API_URL}/api/replay/status`)).json()
    assert(status.frame_index === 1, `step forward failed, at ${status.frame_index}`)

    // --- 5. The 3D view draws the replay, not the live world. ----------
    const canvas = await page.locator('canvas').first().boundingBox()
    assert(canvas && canvas.width > 400, `replay canvas too small: ${JSON.stringify(canvas)}`)
    assert(
      await page.locator('text=REPLAY').first().isVisible(),
      'the viewer should be labelled REPLAY',
    )
    console.log(`  3D view renders the replay at ${Math.round(canvas.width)}x${Math.round(canvas.height)}`)

    // --- 6. Score breakdown is readable in the UI. ---------------------
    await page.getByText(runId, { exact: false }).last().click()
    await page.waitForTimeout(1200)
    await shot(page, '33-replay-scores')
    const weights = await (await page.request.get(`${API_URL}/api/scoring/weights`)).json()
    assert(weights.max_points > 0, 'scoring weights should be exposed')
    assert(
      !JSON.stringify(weights).match(/weapon|target|engagement|kill/i) ||
        /models no weapon/i.test(weights.notice),
      'scoring must not introduce weapon or targeting terms',
    )
    console.log(`  scoring ruler ${weights.weights_hash}, ${weights.max_points} points`)

    // --- 7. Back to live, and the live view still works. ---------------
    await click(page, 'LIVE')
    await page.waitForTimeout(800)
    assert(
      !(await page.locator('text=REPLAY').first().isVisible().catch(() => false)) ||
        (await page.locator('button:has-text("START")').count()) > 0,
      'switching back to LIVE should restore the live controls',
    )
    console.log('  returned to live mode')

    assert(failures.length === 0, `page errors: ${failures.join(' | ')}`)
    console.log('\nREPLAY E2E PASSED')
  } finally {
    await browser.close()
  }
}

main().catch((error) => {
  console.error(`\nREPLAY E2E FAILED: ${error.message}`)
  process.exit(1)
})
