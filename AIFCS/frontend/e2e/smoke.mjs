/**
 * AIFCS dashboard end-to-end smoke test.
 *
 * Requires the backend (:8000) and the dev server (:5173) to be running:
 *   Terminal 1:  ./scripts/dev_backend.sh
 *   Terminal 2:  ./scripts/dev_frontend.sh
 *   Terminal 3:  cd frontend && npm run test:e2e
 *
 * Verifies that the dashboard renders real backend data, that unbuilt
 * subsystems are shown as NOT_IMPLEMENTED, that the mobile layout works, and
 * that a missing backend produces a clear error instead of a fake dashboard.
 */

import { chromium } from 'playwright'

const BASE_URL = process.env.AIFCS_UI_URL ?? 'http://127.0.0.1:5173'
const SHOT_DIR = process.env.AIFCS_SHOT_DIR ?? null
const EXECUTABLE = process.env.PLAYWRIGHT_CHROMIUM ?? undefined

const errors = []
const shot = async (page, name) => {
  if (SHOT_DIR) await page.screenshot({ path: `${SHOT_DIR}/${name}.png` })
}

const assert = (condition, message) => {
  if (!condition) throw new Error(`assertion failed: ${message}`)
}

const waitForEnterEnabled = (page) =>
  page.waitForFunction(
    () => {
      const button = [...document.querySelectorAll('button')].find((b) =>
        /ENTER COMMAND CENTER/i.test(b.textContent ?? ''),
      )
      return Boolean(button) && !button.disabled
    },
    null,
    { timeout: 20000 },
  )

const browser = await chromium.launch({ executablePath: EXECUTABLE })

try {
  // --- Desktop: boot screen -> Command Center ---
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } })
  page.on('console', (m) => m.type() === 'error' && errors.push(`console: ${m.text()}`))
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))

  await page.goto(BASE_URL, { waitUntil: 'networkidle' })
  assert((await page.locator('h1').first().innerText()) === 'AIFCS', 'boot screen shows AIFCS')
  await waitForEnterEnabled(page)

  const subsystemCount = await page.locator('li').count()
  assert(subsystemCount > 0, 'boot screen lists subsystems from the backend')
  await shot(page, '01-boot')

  await page.getByRole('button', { name: /ENTER COMMAND CENTER/i }).click()
  await page.getByText('COMMAND CENTER').first().waitFor()
  assert(
    await page.getByText('NOT IMPLEMENTED').first().isVisible(),
    'the 3D viewer declares itself NOT IMPLEMENTED',
  )
  assert(
    await page.getByText('60 Hz').first().isVisible(),
    'runtime configuration shows the real tick rate',
  )
  await shot(page, '02-command-center')

  // --- Mobile: bottom tab navigation ---
  const mobile = await browser.newPage({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  })
  mobile.on('pageerror', (e) => errors.push(`mobile pageerror: ${e.message}`))
  await mobile.goto(BASE_URL, { waitUntil: 'networkidle' })
  await waitForEnterEnabled(mobile)
  await mobile.getByRole('button', { name: /ENTER COMMAND CENTER/i }).click()

  // Scope to <main>: the desktop tree is also in the DOM, hidden by lg: classes.
  await mobile.getByRole('button', { name: /STATUS/i }).click()
  await mobile.locator('main').getByText('Simulation Engine').first().waitFor({ state: 'visible' })
  await shot(mobile, '03-mobile')

  await mobile.getByRole('button', { name: /INTEL/i }).click()
  await mobile.locator('main').getByText('Runtime Configuration').first().waitFor({ state: 'visible' })

  // --- Backend unreachable: an honest error, never a fake dashboard ---
  const offline = await browser.newPage()
  // Match only real API calls; '**/api/**' would also block /src/api/client.ts.
  await offline.route(
    (url) => url.pathname.startsWith('/api/') && !url.pathname.endsWith('.ts'),
    (route) => route.abort(),
  )
  await offline.goto(BASE_URL, { waitUntil: 'domcontentloaded' })
  await offline.getByText(/Cannot reach the AIFCS backend/).first().waitFor({ timeout: 20000 })
} finally {
  await browser.close()
}

if (errors.length > 0) {
  console.error(`E2E FAILED — page errors:\n${errors.join('\n')}`)
  process.exit(1)
}

console.log('E2E PASSED — dashboard renders live backend data on desktop and mobile.')
