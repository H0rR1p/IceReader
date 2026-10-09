// Narrow regression for candidate completeness and manual head restoration.
// Uses original text and the owned loopback-only acceptance runtime.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { createWriteStream } from 'node:fs'
import { spawn, execFile } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const output = path.join(root, 'build/ui-linguistics-full', `candidates-${Date.now()}`)
const origin = 'http://127.0.0.1:8794'
const python = process.env.BINGDU_UI_TEST_PYTHON || path.resolve(root, '.venv64/Scripts/python.exe')
await fs.mkdir(output, { recursive: true })
assert.equal(await fetch(`${origin}/api/health`).then(() => true, () => false), false, '8794 must be free')
const server = spawn(python, [path.join(root, 'scripts/test_linguistics_runtime.py')], {
  cwd: root, windowsHide: true,
  env: { ...process.env, BINGDU_DATA_DIR: path.join(output, 'data'), BINGDU_UI_TEST_RUNTIME: '1', BINGDU_UI_TEST_PORT: '8794' },
})
const log = createWriteStream(path.join(output, 'backend.log'))
server.stdout.pipe(log); server.stderr.pipe(log)
const errors = []; const failedRequests = []; const assertions = []
let browser; let page
try {
  const deadline = Date.now() + 30000
  while (!await fetch(`${origin}/api/health`).then((response) => response.ok, () => false)) {
    assert.ok(Date.now() < deadline, 'owned runtime readiness')
    await new Promise((resolve) => setTimeout(resolve, 150))
  }
  browser = await chromium.launch({ headless: true, channel: 'chrome' })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  await context.route('**/*', (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/cloud/providers') return route.fulfill({ json: [] })
    return url.origin === origin ? route.continue() : route.abort('blockedbyclient')
  })
  page = await context.newPage()
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('response', (response) => {
    if (response.status() >= 400 && response.url().startsWith(origin + '/api/')) failedRequests.push({ url: response.url(), status: response.status() })
  })
  await page.goto(origin)
  await page.getByRole('button', { name: '使用本机模式进入', exact: true }).click()
  await page.getByRole('button', { name: '导入内容', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '导入日文内容' })
  const title = `原创候选验收 ${Date.now()}`
  await dialog.getByPlaceholder('可选').fill(title)
  await dialog.getByPlaceholder('ここに日本語の文章を貼り付けてください。').fill('先生に励まされた。\n書かれる。\n猫がいるのに気づいた。')
  await dialog.getByRole('button', { name: '导入书籍', exact: true }).click()
  await dialog.waitFor({ state: 'hidden' })
  await page.getByText(title, { exact: true }).first().click()
  await page.getByRole('button', { name: '切分本章', exact: true }).click()
  await page.getByRole('button', { name: '励まされた，完整活用', exact: true }).waitFor({ timeout: 20000 })
  await page.getByRole('button', { name: '励まされた，完整活用', exact: true }).click()
  const inspector = page.locator('.learning-inspector')
  const head = inspector.locator(':scope > p').first()
  assert.match(await head.innerText(), /励ます/)
  const candidates = inspector.locator('.morph-candidates > p')
  assert.ok(await candidates.filter({ hasText: '被别人这样做：励ます' }).count())
  const short = candidates.filter({ hasText: '被要求做：励む（简短说法）' })
  assert.equal(await short.count(), 1)
  assert.match(await short.innerText(), /励む → 励ませる/)
  assertions.push('lexical passive remains head, separate short-causative candidate and derivation')
  const chooseResponse = page.waitForResponse((response) => response.url().endsWith('/api/grammar/choices') && response.request().method() === 'POST')
  await short.getByRole('button', { name: '确认此解释', exact: true }).click()
  assert.equal((await chooseResponse).status(), 200)
  await page.waitForFunction(() => document.querySelector('.learning-inspector > p > span[lang="ja"]')?.textContent === '励む')
  assert.match(await head.innerText(), /励む/)
  assert.match(await inspector.locator('.morph-derivation').innerText(), /励む → 励ませる/)
  assertions.push('manual selection restores dictionary head and derivation')
  await inspector.getByRole('button', { name: '清除选择，恢复候选', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.learning-inspector > p > span[lang="ja"]')?.textContent === '励ます')
  assert.match(await head.innerText(), /励ます/)
  assertions.push('clear selection restores original lexical analysis')
  await page.screenshot({ path: path.join(output, '词典形双候选.png'), fullPage: true })
  await page.getByRole('button', { name: '書かれる，完整活用', exact: true }).click()
  assert.equal(await inspector.locator('.morph-candidates strong').filter({ hasText: /能够这样做/ }).count(), 0)
  assert.equal(await inspector.locator('.morph-candidates strong').filter({ hasText: /被别人这样做/ }).count(), 1)
  assertions.push('godan passive does not offer modern potential')
  await page.getByRole('button', { name: 'いるのに，语法结构', exact: true }).click()
  assert.match(await page.locator('.grammar-structure-panel').innerText(), /把前面的事/)
  assert.match(await inspector.locator('.morph-candidates').innerText(), /作为注意、发现等动作的对象/)
  assertions.push('noni contains nominalized-case interpretation')
  const state = await page.request.get(`${origin}/__test__/state`).then((response) => response.json())
  assert.equal(state.ai_calls.length, 0)
  assert.deepEqual(errors, [])
  assert.deepEqual(failedRequests, [])
  await fs.writeFile(path.join(output, 'acceptance.json'), JSON.stringify({ passed: true, output, assertions, errors, failedRequests, ai_calls: state.ai_calls }, null, 2))
  console.log(JSON.stringify({ passed: true, output, assertions }))
} catch (error) {
  if (page) {
    await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => undefined)
    await fs.writeFile(path.join(output, 'failure.txt'), `${error.stack}\n${await page.locator('body').innerText()}`)
  }
  throw error
} finally {
  await browser?.close()
  if (process.platform === 'win32') await new Promise((resolve) => execFile('taskkill', ['/PID', String(server.pid), '/T', '/F'], { windowsHide: true }, () => resolve()))
  else server.kill()
  log.end()
}
