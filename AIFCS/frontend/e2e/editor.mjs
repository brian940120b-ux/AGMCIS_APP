/**
 * AIFCS scenario editor test (PHASE 10).
 *
 * Builds a scenario through the UI — names it, edits a unit, adds a second one
 * and puts it in formation — then saves it and runs it. Every assertion is
 * about what the backend actually stored or did; a control that merely exists
 * satisfies none of them.
 *
 *   cd frontend && npm run test:e2e:editor
 *
 * On a headless machine without a GPU:
 *   PLAYWRIGHT_GL=swiftshader npm run test:e2e:editor
 */

import { chromium } from 'playwright'

const BASE_URL = process.env.AIFCS_UI_URL ?? 'http://127.0.0.1:5173'
const API_URL = process.env.AIFCS_API_URL ?? 'http://127.0.0.1:8080'
const SHOT_DIR = process.env.AIFCS_SHOT_DIR ?? null
const EXECUTABLE = process.env.PLAYWRIGHT_CHROMIUM ?? undefined

// A name this test owns, so a leftover from a previous run is never mistaken
// for a scenario someone cares about.
const NAME = 'e2e_editor_probe'

const assert = (condition, message) => {
  if (!condition) throw new Error(`assertion failed: ${message}`)
}
const shot = async (page, name) => {
  if (SHOT_DIR) await page.screenshot({ path: `${SHOT_DIR}/${name}.png` })
}
const click = async (page, text, { exact = true } = {}) => {
  await page.getByRole('button', { name: text, exact }).first().click()
}

async function main() {
  const browser = await chromium.launch({
    executablePath: EXECUTABLE,
    args:
      process.env.PLAYWRIGHT_GL === 'swiftshader'
        ? ['--use-gl=swiftshader', '--enable-unsafe-swiftshader']
        : [],
  })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  const failures = []
  page.on('pageerror', (e) => failures.push(String(e)))

  try {
    // Known state, and no leftovers from an earlier run.
    await page.request.post(`${API_URL}/api/simulation/reset`)
    await page.request.delete(`${API_URL}/api/scenarios/${NAME}`)

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
    await page.getByText('COMMAND CENTER').first().waitFor()

    // --- 1. Enter edit mode. ------------------------------------------
    await click(page, 'EDIT')
    await page.getByText('Scenario Editor').first().waitFor({ timeout: 15000 })
    // The panel renders before the catalogue request resolves, so wait for the
    // data rather than for the panel — otherwise this races the fetch.
    const defaultRow = page.getByRole('button', { name: /demo_alpha/ }).last()
    await defaultRow.waitFor({ state: 'visible', timeout: 15000 })
    assert(await defaultRow.isVisible(), 'the editor should list the existing scenarios')
    await shot(page, '40-editor-list')

    // --- 2. Start a new scenario from the backend's template. ----------
    await click(page, 'NEW SCENARIO', { exact: false })
    await page.waitForTimeout(1200)
    await page.getByLabel('Scenario name').fill(NAME)
    await page.waitForTimeout(900)

    // The preview draws the scenario's starting positions.
    assert(
      await page.getByText('PREVIEW').first().isVisible(),
      'the tactical view should be labelled PREVIEW in edit mode',
    )
    await shot(page, '41-editor-new')

    // --- 3. Add a second unit and put it in formation. -----------------
    await page.getByLabel('Add unit').click()
    await page.waitForTimeout(900)

    // Unit 2 is the one just added; put it in formation on unit 1.
    const formationSelect = page.getByLabel('Unit 2 formation leader')
    await formationSelect.waitFor({ state: 'visible', timeout: 10000 })
    await formationSelect.selectOption({ index: 1 })
    await page.waitForTimeout(900)

    assert(
      await page.getByText(/^Valid ·/).first().isVisible(),
      'the draft should validate once the second unit has a leader',
    )
    await shot(page, '42-editor-formation')

    // --- 4. Save, and check the backend really stored it. --------------
    await click(page, 'SAVE', { exact: false })
    await page.waitForTimeout(1500)

    const stored = await (await page.request.get(`${API_URL}/api/scenarios/${NAME}`)).json()
    assert(stored.name === NAME, `expected ${NAME}, got ${stored.name}`)
    assert(stored.entity_count === 2, `expected 2 units, got ${stored.entity_count}`)
    const wingman = stored.entities.find((e) => e.formation_leader)
    assert(wingman, 'the saved scenario should contain a unit in formation')
    console.log(
      `  saved ${stored.name}: ${stored.entity_count} units, ` +
        `${wingman.id} on ${wingman.formation_leader}`,
    )

    // --- 5. An invalid draft is refused, with the reason. --------------
    const invalid = JSON.parse(JSON.stringify(stored.document))
    invalid.entities[1].formation.leader = 'GHOST-99'
    const verdict = await (
      await page.request.post(`${API_URL}/api/scenarios/validate`, { data: invalid })
    ).json()
    assert(verdict.valid === false, 'a dangling formation leader should not validate')
    assert(/GHOST-99/.test(verdict.error), `the error should name the problem: ${verdict.error}`)
    console.log(`  invalid draft refused: ${verdict.error}`)

    // --- 6. Export and import round-trip. ------------------------------
    const exported = await (
      await page.request.get(`${API_URL}/api/scenarios/${NAME}/export`)
    ).json()
    assert(exported.yaml.includes(NAME), 'the export should contain the scenario name')
    const imported = await page.request.post(`${API_URL}/api/scenarios/import`, {
      data: { yaml_text: exported.yaml, name: `${NAME}_copy`, overwrite: true },
    })
    assert(imported.ok(), `import should succeed, got ${imported.status()}`)
    console.log('  export / import round-trips')

    // --- 7. The scenario made in the editor actually runs. -------------
    const started = await page.request.post(`${API_URL}/api/simulation/start`, {
      data: { scenario: NAME },
    })
    assert(started.ok(), `starting the new scenario should work, got ${started.status()}`)
    await page.waitForTimeout(2000)
    const status = await (await page.request.get(`${API_URL}/api/simulation/status`)).json()
    assert(status.scenario === NAME, `engine should be flying ${NAME}, got ${status.scenario}`)
    assert(status.clock.tick > 0, 'the clock should have advanced')
    assert(status.entity_count === 2, `expected 2 entities, got ${status.entity_count}`)
    console.log(`  runs: tick ${status.clock.tick}, ${status.entity_count} units`)

    // A scenario in use cannot be deleted out from under the run.
    const refused = await page.request.delete(`${API_URL}/api/scenarios/${NAME}`)
    assert(refused.status() === 409, `delete while running should be 409, got ${refused.status()}`)
    await page.request.post(`${API_URL}/api/simulation/stop`)
    console.log('  delete refused while the run was in progress')

    // --- 8. Clean up after ourselves. ----------------------------------
    for (const name of [NAME, `${NAME}_copy`]) {
      const removed = await page.request.delete(`${API_URL}/api/scenarios/${name}`)
      assert(removed.ok(), `cleanup of ${name} failed with ${removed.status()}`)
    }
    const remaining = await (await page.request.get(`${API_URL}/api/scenarios`)).json()
    assert(
      !remaining.available.includes(NAME),
      'the probe scenario should be gone after cleanup',
    )

    // The default scenario is protected.
    const protectedDelete = await page.request.delete(`${API_URL}/api/scenarios/demo_alpha`)
    assert(
      protectedDelete.status() === 422,
      `deleting the default should be refused, got ${protectedDelete.status()}`,
    )
    console.log('  default scenario is protected')

    assert(failures.length === 0, `page errors: ${failures.join(' | ')}`)
    console.log('\nEDITOR E2E PASSED')
  } finally {
    await browser.close()
  }
}

main().catch((error) => {
  console.error(`\nEDITOR E2E FAILED: ${error.message}`)
  process.exit(1)
})
