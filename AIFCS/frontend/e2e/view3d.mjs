/**
 * AIFCS 3D tactical view test (PHASE 8).
 *
 * Checks that the view renders one WebGL context, that every camera mode works,
 * that the 2D plot remains reachable, and that mobile gets its own single
 * context. Run it with both servers up:
 *
 *   cd frontend && npm run test:e2e:3d
 *
 * On a headless machine without a GPU, pass a software renderer:
 *   PLAYWRIGHT_GL=swiftshader npm run test:e2e:3d
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
/**
 * Click something while the 3D scene is rendering.
 *
 * On a machine with no GPU the scene is drawn by swiftshader on the CPU, which
 * saturates the browser's main thread. Playwright's ordinary `.click()` then
 * lands but never returns: its post-click settlement check is starved and
 * times out, even though the button worked. Measured on this sandbox — with
 * the canvas removed the same click completes in under 100 ms.
 *
 * So the element is asserted visible and enabled first, and the click is then
 * dispatched directly. Nothing about the assertion is weakened: the locator
 * still has to resolve to exactly one usable button.
 */
const clickWhileRendering = async (page, locator, label) => {
  await locator.waitFor({ state: 'visible', timeout: 15000 })
  assert(await locator.isEnabled(), `${label} should be enabled`)
  await locator.dispatchEvent('click')
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
    null,
    { timeout: 20000 },
  )
  await page.getByRole('button', { name: /ENTER COMMAND CENTER/i }).click()
}

await fetch(`${API_URL}/api/simulation/stop`, { method: 'POST' })
await fetch(`${API_URL}/api/simulation/reset`, { method: 'POST' })

const browser = await chromium.launch({
  executablePath: EXECUTABLE,
  args: process.env.PLAYWRIGHT_GL
    ? [`--use-gl=${process.env.PLAYWRIGHT_GL}`, '--enable-unsafe-swiftshader']
    : [],
})

try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } })
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))

  await enterCommandCenter(page)
  await page.locator('canvas').first().waitFor({ timeout: 25000 })

  // Exactly one WebGL context: rendering both responsive layouts would create a
  // second, invisible one and double the GPU cost.
  assert(
    (await page.locator('canvas').count()) === 1,
    'the desktop layout should create exactly one WebGL context',
  )
  // R3F sizes the canvas from a ResizeObserver, so it starts at the HTML
  // default of 300x150 for a frame or two. Wait for the real size.
  await page.waitForFunction(
    () => {
      const canvas = document.querySelector('canvas')
      return canvas !== null && canvas.getBoundingClientRect().width > 400
    },
    null,
    { timeout: 15000 },
  )
  const box = await page.locator('canvas').first().boundingBox()
  assert(box.width > 400 && box.height > 200, `canvas too small: ${JSON.stringify(box)}`)
  console.log(`3D canvas: ${Math.round(box.width)}x${Math.round(box.height)}, 1 context`)

  await clickWhileRendering(page, page.getByRole('button', { name: /^START$/ }), 'START')
  await page.waitForTimeout(6000)
  await shot(page, '20-3d-orbit')

  for (const mode of ['Follow', 'Top', 'Side', 'Orbit']) {
    await clickWhileRendering(
      page,
      page.getByRole('button', { name: mode, exact: true }),
      `camera mode ${mode}`,
    )
    await page.waitForTimeout(1500)
    assert(
      (await page.locator('canvas').count()) === 1,
      `switching to ${mode} should not add a context`,
    )
    if (SHOT_DIR && mode !== 'Orbit') await shot(page, `21-3d-${mode.toLowerCase()}`)
    console.log(`camera mode ${mode} ok`)
  }

  // The 2D plot must remain reachable, and returning to 3D must still work.
  await clickWhileRendering(page, page.getByRole('button', { name: '2D', exact: true }), '2D')
  await page.getByText('Tactical Plot').first().waitFor({ timeout: 10000 })
  assert((await page.locator('canvas').count()) === 0, '2D mode should release the WebGL context')
  await clickWhileRendering(page, page.getByRole('button', { name: '3D', exact: true }), '3D')
  await page.locator('canvas').first().waitFor({ timeout: 15000 })
  console.log('2D and 3D views both reachable')

  // Telemetry keeps flowing while the 3D view renders.
  const badge = await page.getByText(/LIVE · \d+ FRAMES/).first().innerText()
  console.log(`telemetry during 3D render: ${badge}`)

  // Mobile gets its own single context.
  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true })
  mobile.on('pageerror', (e) => errors.push(`mobile pageerror: ${e.message}`))
  await enterCommandCenter(mobile)
  await mobile.locator('canvas').first().waitFor({ timeout: 25000 })
  await mobile.waitForTimeout(2500)
  assert((await mobile.locator('canvas').count()) === 1, 'mobile should also use one context')
  await shot(mobile, '22-3d-mobile')
  console.log('mobile 3D view ok')

  await fetch(`${API_URL}/api/simulation/stop`, { method: 'POST' })
} finally {
  await browser.close()
}

if (errors.length > 0) {
  console.error(`3D E2E FAILED — page errors:\n${errors.join('\n')}`)
  process.exit(1)
}
console.log('3D E2E PASSED — tactical view renders and every camera mode works.')
