// Real API + browser acceptance on an owned, isolated server; external calls are blocked.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { createWriteStream } from 'node:fs'
import { spawn, execFile } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { chromium } from 'playwright'
import JSZip from 'jszip'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const output = path.join(root, 'build/ui-linguistics-full', `basic-${Date.now()}`)
await fs.mkdir(output, { recursive: true })
const module = await import(pathToFileURL(path.join(root, 'src/features/reader/learningSpans.ts')).href)
assert.equal(module.codepointSlice('🙂𠮷田さん', 1, 3), '𠮷田')
assert.equal(module.codepointToUtf16('🙂𠮷田さん', 3), 5)
const base = { kind: 'morphology', id: 'a', start: 0, end: 4, surface: '読みました' }
const text = '読みました。'
const whole = { ...base, end: 5, surface: '読みました', id: 'whole' }
const nested = { ...base, id: 'inner', end: 2, surface: '読み' }
const crossing = { ...base, id: 'cross', start: 2, end: 6, surface: 'ました。' }
assert.deepEqual(module.topLevelSpans(text, [crossing, nested, whole]).map((x) => x.id), ['whole'])
assert.equal(module.isVocabularyToken({ role: 'grammatical', is_content: true, part_of_speech: '助動詞' }), false)
assert.equal(module.isVocabularyToken({ is_content: true, part_of_speech: '助詞-格助詞' }), false)
assert.equal(module.isVocabularyToken({ role: 'lexical', is_content: true, part_of_speech: '動詞-一般' }), true)

const origin = 'http://127.0.0.1:8793'
const python = process.env.BINGDU_UI_TEST_PYTHON || path.resolve(root, '.venv64/Scripts/python.exe')
const up = await fetch(`${origin}/api/health`).then(() => true, () => false)
assert.equal(up, false, '8793 must be free; never attach acceptance to an unowned server')
const server = spawn(python, [path.join(root, 'scripts/test_linguistics_runtime.py')], { cwd: root, windowsHide: true, env: { ...process.env, BINGDU_DATA_DIR: path.join(output, 'data'), BINGDU_UI_TEST_RUNTIME: '1', BINGDU_UI_TEST_PORT: '8793' } })
const serverLog = createWriteStream(path.join(output, 'backend.log'))
server.stdout.pipe(serverLog); server.stderr.pipe(serverLog)
await fs.writeFile(path.join(output, 'backend.pid'), String(server.pid))
let browser
let page
const errors = []
const requests = []
const title = `原创语法 UI 验收 ${Date.now()}`
try {
  const deadline = Date.now() + 30000
  while (!await fetch(`${origin}/api/health`).then((response) => response.ok, () => false)) {
    assert.ok(Date.now() < deadline, 'isolated acceptance server readiness')
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
  page.on('response', (response) => { if (response.url().startsWith(origin + '/api/')) requests.push({ url: new URL(response.url()).pathname, status: response.status() }) })
  await page.goto(origin)
  await page.getByRole('button', { name: '使用本机模式进入' }).click()
  await page.getByRole('button', { name: '导入内容', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '导入日文内容' })
  await dialog.getByPlaceholder('可选').fill(title)
  await dialog.getByPlaceholder('ここに日本語の文章を貼り付けてください。').fill('🙂先生が生徒に本を読ませました。\n私は嫌いな野菜を食べさせられなかった。\nこの光景を目にした。\n今日は休むわけではない。')
  await dialog.getByRole('button', { name: '导入书籍', exact: true }).click()
  await dialog.waitFor({ state: 'hidden' })
  await page.getByText(title, { exact: true }).first().click()
  await page.getByRole('button', { name: '切分本章', exact: true }).click()
  await page.locator('.learning-span').first().waitFor({ timeout: 20000 })
  const full = page.getByRole('button', { name: '読ませました，完整活用', exact: true })
  await full.click()
  await page.getByRole('heading', { name: '完整活用', exact: true }).waitFor()
  const inspector = page.locator('.learning-inspector')
  assert.match(await inspector.innerText(), /読む/)
  assert.match(await inspector.innerText(), /使役/)
  assert.equal(await page.getByText(/查看原始词素|分析依据|辅助依据/).count(), 0)
  assert.equal(await page.getByRole('button', { name: /AI.*消歧|AI 判定/ }).count(), 0)
  const style = await full.evaluate((node) => {
    const selected = getComputedStyle(node)
    const unselected = getComputedStyle(document.querySelector('.learning-span:not(.selected)'))
    const token = getComputedStyle(document.querySelector('.token.content'))
    return { border: selected.borderBottomStyle, outline: selected.outlineStyle, background: selected.backgroundColor, ordinary: unselected.borderBottomWidth, word: token.textDecorationLine }
  })
  assert.equal(style.border, 'dashed')
  assert.equal(style.outline, 'none')
  assert.equal(style.background, 'rgba(0, 0, 0, 0)')
  assert.equal(style.ordinary, '0px')
  assert.equal(style.word, 'none')
  await full.locator('.token.content').first().hover()
  const childStyle = await full.locator('.token.content').first().evaluate((node) => ({ background: getComputedStyle(node).backgroundColor, shadow: getComputedStyle(node).boxShadow }))
  assert.deepEqual(childStyle, { background: 'rgba(0, 0, 0, 0)', shadow: 'none' }, 'hovering a selected grammar atom must not add a second box')
  const layout = await page.evaluate(() => {
    const dictionary = document.querySelector('.dictionary-card')
    const grammar = document.querySelector('.grammar-card')
    const image = document.querySelector('.reader-image-toggle')
    const buttons = document.querySelector('.segmentation-actions')
    return { grammarAfterWord: !!(dictionary.compareDocumentPosition(grammar) & Node.DOCUMENT_POSITION_FOLLOWING), background: getComputedStyle(grammar).backgroundColor, imageBeforeButtons: !!(image.compareDocumentPosition(buttons) & Node.DOCUMENT_POSITION_FOLLOWING), buttons: [...buttons.querySelectorAll('button')].map((button) => button.textContent) }
  })
  assert.equal(layout.grammarAfterWord, true)
  assert.equal(layout.background, 'rgb(255, 255, 255)')
  assert.equal(layout.imageBeforeButtons, true)
  assert.deepEqual(layout.buttons, ['后台切分全书', '后台翻译全书', '重新切分本章', '重新切分全书'])
  const chooser = inspector.getByRole('combobox', { name: '学习的语法', exact: true })
  const fieldLayout = await chooser.evaluate((node) => {
    const field = node.closest('label'); const label = field.querySelector('span'); const actions = field.nextElementSibling
    const card = node.closest('.grammar-card').getBoundingClientRect()
    const box = node.getBoundingClientRect(); const style = getComputedStyle(node)
    return { labelAbove: label.getBoundingClientRect().bottom <= box.top, insideCard: box.left >= card.left && box.right <= card.right, actionsBelow: actions.getBoundingClientRect().top >= box.bottom, font: style.fontSize, borderRadius: style.borderRadius, background: style.backgroundColor }
  })
  assert.equal(fieldLayout.labelAbove, true)
  assert.equal(fieldLayout.insideCard, true)
  assert.equal(fieldLayout.actionsBelow, true)
  assert.equal(fieldLayout.font, '12px')
  assert.equal(fieldLayout.borderRadius, '5px')
  await chooser.selectOption({ index: 1 })
  assert.equal(await chooser.evaluate((node) => node.selectedIndex), 1)
  await chooser.focus()
  await page.keyboard.press('ArrowUp')
  assert.equal(await chooser.evaluate((node) => node.selectedIndex), 0)
  await page.keyboard.press('Tab')

  const ordering = await page.evaluate(() => {
    const dictionary = document.querySelector('.dictionary-card')
    const actions = document.querySelector('.sentence-analysis-actions')
    return dictionary && actions ? !!(dictionary.compareDocumentPosition(actions) & Node.DOCUMENT_POSITION_FOLLOWING) : false
  })
  assert.equal(ordering, true, 'sentence actions must follow the entire dictionary card')
  const source = await page.locator('.japanese-text .sentence').first().evaluate((node) => [...node.childNodes].map((child) => child.nodeType === Node.TEXT_NODE ? child.textContent : child.querySelectorAll ? (() => { const clone = child.cloneNode(true); clone.querySelectorAll('rt').forEach((x) => x.remove()); return clone.textContent })() : child.textContent).join(''))
  assert.equal(source, '🙂先生が生徒に本を読ませました。')
  await page.screenshot({ path: path.join(output, '完整活用.png'), fullPage: true })
  await page.getByRole('button', { name: '食べさせられなかった，完整活用', exact: true }).click()
  assert.match(await inspector.innerText(), /食べる/)
  assert.match(await inspector.innerText(), /否定/)
  await page.screenshot({ path: path.join(output, '使役被动链.png'), fullPage: true })
  await page.getByRole('button', { name: '目にした，语法结构', exact: true }).click()
  await page.getByRole('heading', { name: '本地语法结构' }).waitFor()
  assert.match(await page.locator('.grammar-structure-panel').innerText(), /目/)
  await page.screenshot({ path: path.join(output, '固定搭配.png'), fullPage: true })
  await page.reload()
  await page.getByRole('button', { name: `打开《${title}》`, exact: true }).click()
  await page.locator('.learning-span').first().waitFor({ timeout: 20000 })
  assert.equal(await page.getByRole('button', { name: '読ませました，完整活用', exact: true }).count(), 1)
  await page.getByRole('button', { name: '设置', exact: true }).click()
  await page.getByRole('heading', { name: '上下文与语法学习', exact: true }).waitFor()
  const section = page.locator('.settings-section').filter({ has: page.getByRole('heading', { name: '上下文与语法学习', exact: true }) })
  await section.locator('select').selectOption('4')
  const savedPreferences = page.waitForResponse((response) => response.url().endsWith('/api/analysis/preferences') && response.request().method() === 'PUT')
  await section.getByRole('button', { name: '保存语言分析偏好' }).click()
  await savedPreferences
  const preferences = await page.evaluate(async () => (await fetch('/api/analysis/preferences')).json())
  assert.equal(preferences.context_policy.preceding_sentences, 4)
  const portability = page.locator('.settings-section').filter({ has: page.getByRole('heading', { name: '数据备份与恢复', exact: true }) })
  const transferFormat = portability.getByRole('combobox', { name: '迁移包格式', exact: true })
  const backupFormat = portability.getByRole('combobox', { name: '备份格式', exact: true })
  assert.equal(await transferFormat.inputValue(), '3')
  assert.equal(await backupFormat.inputValue(), '3')
  assert.deepEqual(await backupFormat.locator('option').evaluateAll((rows) => rows.map((row) => row.value)), ['3', '1'])
  const dataCapabilities = await page.request.get(`${origin}/api/data/capabilities`).then((response) => response.json())
  const exports = []
  const exportPackage = async (name, endpoint, version) => {
    const waitResponse = page.waitForResponse((response) => new URL(response.url()).pathname === endpoint && response.request().method() === 'GET')
    const waitDownload = page.waitForEvent('download')
    await portability.getByRole('button', { name, exact: true }).click()
    const [response, download] = await Promise.all([waitResponse, waitDownload])
    assert.equal(response.status(), 200)
    assert.equal(new URL(response.url()).searchParams.get('schema_version'), String(version))
    const file = path.join(output, `${endpoint.endsWith('backup') ? 'backup' : 'migration'}-v${version}.zip`)
    await download.saveAs(file)
    const archive = await JSZip.loadAsync(await fs.readFile(file))
    const manifest = JSON.parse(await archive.file('manifest.json').async('string'))
    assert.equal(manifest.schema_version, version)
    assert.equal(manifest.complete_user_data, version === 3)
    if (version < 3) for (const omitted of dataCapabilities.legacy_export_omissions) assert.ok(manifest.omitted_data.includes(omitted), omitted)
    exports.push({ url: response.url(), schema_version: manifest.schema_version, complete_user_data: manifest.complete_user_data, omitted_data: manifest.omitted_data })
  }
  await exportPackage('下载迁移包', '/api/data/book-transfer', 3)
  await exportPackage('导出 ZIP', '/api/data/backup', 3)
  await transferFormat.selectOption('2')
  const migrationWarning = portability.getByRole('note').first()
  assert.match(await migrationWarning.innerText(), /不包含新语法修订、内部上下文分析、历史切分结果和语法卡来源快照/)
  await migrationWarning.locator('summary').click()
  assert.match(await migrationWarning.innerText(), /上下文修订记录/)
  assert.match(await migrationWarning.innerText(), /卡片撤销记录/)
  await exportPackage('下载迁移包', '/api/data/book-transfer', 2)
  await transferFormat.selectOption('1')
  await exportPackage('下载迁移包', '/api/data/book-transfer', 1)
  await backupFormat.selectOption('1')
  assert.equal(await portability.getByRole('note').count(), 2)
  await portability.getByRole('note').last().locator('summary').click()
  assert.match(await portability.getByRole('note').last().innerText(), /历史原文锚点映射/)
  await exportPackage('导出 ZIP', '/api/data/backup', 1)
  await portability.scrollIntoViewIfNeeded()
  await page.screenshot({ path: path.join(output, '兼容导出格式与省略说明.png'), fullPage: true })
  await fs.writeFile(path.join(output, 'export-acceptance.json'), JSON.stringify({ passed: true, defaultSchema: 3, dataCapabilities, exports, assertions: ['v3 defaults', 'explicit v1/v2 selection', 'capability omission disclosure', 'schema URL matches selection', 'downloaded ZIP manifests match schema', 'backup excludes unsupported v2 option'] }, null, 2))
  await page.screenshot({ path: path.join(output, '上下文设置.png'), fullPage: true })
  await page.setViewportSize({ width: 800, height: 1280 })
  await page.screenshot({ path: path.join(output, '竖屏设置.png'), fullPage: true })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true)
  await page.setViewportSize({ width: 480, height: 1000 })
  const navigation = page.locator('.app-navigation > nav')
  assert.ok((await navigation.boundingBox()).width >= 100, 'narrow layout must preserve visible primary navigation')
  assert.equal(await page.getByRole('button', { name: '人物与证据', exact: true }).count(), 0)
  await navigation.getByRole('button', { name: '个人词库', exact: true }).click()
  await page.getByRole('heading', { name: '个人词库', exact: true }).waitFor()
  await page.screenshot({ path: path.join(output, '窄屏导航与词库.png'), fullPage: true })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true)
  assert.deepEqual(errors, [])
  assert.deepEqual(requests.filter((row) => row.status >= 400), [])
  assert.ok(requests.some((row) => row.url === '/api/sentences/structure' && row.status === 200))
  await fs.writeFile(path.join(output, 'acceptance.json'), JSON.stringify({ passed: true, title, origin, requests, errors, assertions: ['Unicode anchors', 'noncrossing DOM', 'full causative chain', 'head dictionary', 'clean reading marks and selected dashed underline', 'white grammar card below dictionary', 'illustrations above four grouped controls', 'removed diagnostic and AI disambiguation UI', 'actions after dictionary', 'idiom structures', 'reload cache', 'persisted context preferences', 'explicit compatibility exports and actual ZIP manifests', 'portrait layout', '480px accessible primary navigation'] }, null, 2))
  console.log(JSON.stringify({ passed: true, output }))
} catch (error) {
  if (page) {
    await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => undefined)
    await fs.writeFile(path.join(output, 'failure.txt'), `${error.stack}\n${JSON.stringify({ errors, requests }, null, 2)}\n${await page.locator('body').innerText()}`)
  }
  throw error
} finally {
  await browser?.close()
  if (process.platform === 'win32') await new Promise((resolve) => execFile('taskkill', ['/PID', String(server.pid), '/T', '/F'], { windowsHide: true }, () => resolve()))
  else server.kill()
  serverLog.end()
}
