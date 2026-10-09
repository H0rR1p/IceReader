// Product flow acceptance on an original EPUB, an isolated backend, and simulated AI.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { createWriteStream } from 'node:fs'
import { spawn, execFile } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import JSZip from 'jszip'
import { chromium } from 'playwright'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const output = path.join(root, 'build/ui-linguistics-full', String(Date.now()))
const origin = 'http://127.0.0.1:8792'
const python = process.env.BINGDU_UI_TEST_PYTHON || path.resolve(root, '.venv64/Scripts/python.exe')
await fs.mkdir(output, { recursive: true })
const up = await fetch(`${origin}/api/health`).then(() => true, () => false)
assert.equal(up, false, '8792 must be free; never attach a test to an unowned server')
const server = spawn(python, [path.join(root, 'scripts/test_linguistics_runtime.py')], { cwd: root, windowsHide: true, env: { ...process.env, BINGDU_DATA_DIR: path.join(output, 'data'), BINGDU_UI_TEST_RUNTIME: '1', BINGDU_UI_TEST_PORT: '8792' } })
const serverLog = createWriteStream(path.join(output, 'backend.log'))
server.stdout.pipe(serverLog); server.stderr.pipe(serverLog)
await fs.writeFile(path.join(output, 'backend.pid'), String(server.pid))
const assertions = []
const failures = []
const apiFailures = []
const record = (name) => { assertions.push(name); console.log(`PASS ${name}`) }
const poll = async (read, check, label, timeout = 30000) => {
  const deadline = Date.now() + timeout
  let value
  while (Date.now() < deadline) {
    value = await read()
    if (check(value)) return value
    await new Promise((resolve) => setTimeout(resolve, 150))
  }
  throw new Error(`${label}: ${JSON.stringify(value)}`)
}
let browser
let page
try {
  await poll(() => fetch(`${origin}/api/health`).then((response) => response.ok, () => false), Boolean, 'isolated server readiness')
  browser = await chromium.launch({ headless: true, channel: 'chrome' })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  await context.route('**/*', (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/cloud/providers') return route.fulfill({ json: [] })
    return url.origin === origin ? route.continue() : route.abort('blockedbyclient')
  })
  page = await context.newPage()
  page.on('pageerror', (error) => failures.push(error.stack || error.message))
  page.on('response', (response) => {
    if (response.status() >= 400 && response.url().startsWith(origin + '/api/')) apiFailures.push({ url: response.url(), status: response.status(), body: response.request().postData() })
  })
  const api = async (endpoint, body, method = body === undefined ? 'GET' : 'POST') => {
    const response = await page.request.fetch(`${origin}${endpoint}`, { method, ...(body === undefined ? {} : { data: body }) })
    assert.ok(response.ok(), `${method} ${endpoint} ${response.status()} ${await response.text()}`)
    return response.json()
  }
  const snapshot = () => api('/api/library/index')
  const screenshot = (name) => page.screenshot({ path: path.join(output, `${name}.png`), fullPage: true })
  const openCards = async () => {
    if (await page.locator('.reader-topbar').count()) await page.getByRole('button', { name: '设置', exact: true }).click()
    await page.getByRole('button', { name: '词语卡片', exact: true }).click()
  }
  const title = `原创流程验收 ${Date.now()}`
  const bodies = [
    ['🙂先生が生徒に本を読ませました。', '私は嫌いな野菜を食べさせられなかった。', 'この光景を目にした。', '田中さんは「僕は男です」と言った。', '私は先生に褒められた。'],
    ['田中さんは図書館で本を読んだ。', '今日は休むわけではない。'],
    ['田中さんは雨の日も歩いた。', '窓の外を見つめていた。'],
    ['田中さんは最後の頁を閉じた。', '明日は新しい物語を読むつもりだ。'],
  ]
  const zip = new JSZip()
  zip.file('mimetype', 'application/epub+zip')
  zip.file('META-INF/container.xml', '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
  zip.file('OEBPS/content.opf', `<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">${title}</dc:identifier><dc:title>${title}</dc:title><dc:language>ja</dc:language></metadata><manifest>${bodies.map((_, i) => `<item id="c${i}" href="c${i}.xhtml" media-type="application/xhtml+xml"/>`).join('')}</manifest><spine>${bodies.map((_, i) => `<itemref idref="c${i}"/>`).join('')}</spine></package>`)
  bodies.forEach((lines, i) => zip.file(`OEBPS/c${i}.xhtml`, `<html xmlns="http://www.w3.org/1999/xhtml"><head><title>第${i + 1}章</title></head><body><h1>第${i + 1}章</h1>${lines.map((text) => `<p>${text}</p>`).join('')}</body></html>`))
  const epub = path.join(output, 'original.epub')
  await fs.writeFile(epub, await zip.generateAsync({ type: 'nodebuffer' }))
  await page.goto(origin)
  await page.getByRole('button', { name: '使用本机模式进入' }).click()
  await page.getByRole('button', { name: '导入内容', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '导入日文内容' })
  await dialog.locator('input[type=file]').setInputFiles(epub)
  await dialog.getByRole('button', { name: '导入书籍', exact: true }).click()
  await dialog.waitFor({ state: 'hidden' })
  await page.getByRole('button', { name: `打开《${title}》`, exact: true }).click()
  await page.getByRole('button', { name: '切分本章', exact: true }).click()
  await page.getByRole('button', { name: '読ませました，完整活用', exact: true }).waitFor({ timeout: 20000 })
  let index = await snapshot()
  const book = index.books.find((row) => row.title === title)
  const chapters = index.chapters.filter((row) => row.bookId === book.id).sort((a, b) => a.order - b.order)
  assert.equal(chapters.length, 4)
  await page.getByRole('button', { name: '読ませました，完整活用', exact: true }).click()
  assert.match(await page.locator('.learning-inspector').innerText(), /読む/)
  await page.getByRole('button', { name: '加入结构识别卡', exact: true }).click()
  await poll(() => api('/api/cards/candidates'), (rows) => rows.length === 1, 'recognition candidate')
  await page.getByRole('button', { name: '加入词形恢复卡', exact: true }).click()
  const candidates = await poll(() => api('/api/cards/candidates'), (rows) => rows.length === 2, 'restoration candidate')
  assert.deepEqual(new Set(candidates.map((row) => row.card_template)), new Set(['grammar-recognition', 'form-restoration']))
  assert.equal((await api('/api/cards/due')).length, 0, 'candidates cannot enter SRS before confirmation')
  await page.getByRole('button', { name: /加书签/ }).click()
  await poll(() => api(`/api/library/bookmarks?book_id=${book.id}`), (rows) => rows.length === 1, 'bookmark saved')
  record('Original EPUB import, complete span click, two grammar candidates, explicit SRS confirmation boundary')
  await screenshot('01-完整活用与语法卡')
  await openCards()
  await page.locator('.candidate-list article').first().waitFor()
  assert.equal(await page.locator('.candidate-list article[data-template="form-restoration"]').count(), 1)
  assert.match(await page.locator('.candidate-list article[data-template="form-restoration"]').innerText(), /【____】/)
  for (const template of ['grammar-recognition', 'form-restoration']) await page.locator(`.candidate-list article[data-template="${template}"]`).getByRole('button', { name: '加入学习', exact: true }).click()
  await poll(() => api('/api/cards/due'), (rows) => rows.length === 2, 'two accepted grammar cards')
  await page.getByRole('button', { name: /开始今日复习/ }).click()
  for (let i = 0; i < 2; i++) {
    await page.getByRole('button', { name: '显示答案', exact: true }).click()
    await screenshot(`02-语法复习${i + 1}`)
    await page.getByRole('button', { name: '记得', exact: true }).click()
  }
  await page.getByText('今天的到期卡已完成', { exact: true }).waitFor()
  const afterReview = await api('/__test__/state')
  assert.equal(afterReview.events.filter((row) => row.event_type === 'srs_good' && row.type === 'grammar').length, 2)
  assert.ok(afterReview.events.some((row) => row.event_type === 'grammar_reveal' && row.type === 'grammar'))
  record('Both grammar templates render and SRS ratings remain grammar learning events')
  await page.getByRole('button', { name: '继续阅读', exact: true }).click()
  await api('/api/settings', { api_key: 'ui-test-only', base_url: `${origin}/mock-ai`, model: 'mock-model' }, 'PUT')
  await page.getByRole('button', { name: '褒められた，完整活用', exact: true }).click()
  const inspector = page.locator('.learning-inspector')
  await inspector.getByRole('button', { name: '确认此解释', exact: true }).first().click()
  await poll(() => inspector.locator('.inspector-heading small').innerText(), (label) => label === '用户确认', 'manual ambiguity confirmation')
  const beforeManual = (await api('/__test__/state')).ai_calls.length
  await inspector.getByRole('button', { name: '清除选择，恢复候选', exact: true }).click()
  await poll(() => inspector.locator('.inspector-heading small').innerText(), (label) => label === '本地规则识别', 'clear manual choice')
  await inspector.getByRole('button', { name: '确认此解释', exact: true }).first().click()
  await poll(() => inspector.locator('.inspector-heading small').innerText(), (label) => label === '用户确认', 'manual candidate confirmation')
  assert.equal((await api('/__test__/state')).ai_calls.length, beforeManual)
  assert.equal(await page.getByRole('button', { name: /AI.*消歧|AI 判定/ }).count(), 0)
  record('Manual candidate selection and clearing remain available without AI disambiguation calls')
  const failedTask = await api('/__test__/failed-task', { book_id: book.id })
  await page.reload()
  await page.getByRole('button', { name: `打开《${title}》`, exact: true }).click()
  const taskRegion = page.getByRole('region', { name: '重新切分' })
  await taskRegion.getByText('未完成', { exact: true }).waitFor()
  const compactPanel = taskRegion.locator('.task-progress')
  assert.equal(await compactPanel.locator('p').evaluate((node) => getComputedStyle(node).fontSize), '12px')
  assert.equal(await compactPanel.locator('strong').evaluate((node) => getComputedStyle(node).fontSize), '12px')
  assert.equal(await compactPanel.getByRole('button', { name: '从断点继续' }).evaluate((node) => getComputedStyle(node).fontSize), '11px')
  assert.equal(await compactPanel.evaluate((node) => node.scrollWidth <= node.clientWidth), true)
  await screenshot('02b-精简任务面板')
  await taskRegion.getByRole('button', { name: '取消任务', exact: true }).click()
  await poll(() => api(`/api/jobs/${failedTask.id}`), (job) => job.status === 'canceled', 'failed task canceled through UI')
  await compactPanel.waitFor({ state: 'hidden' })
  await page.reload()
  await page.getByRole('button', { name: `打开《${title}》`, exact: true }).click()
  assert.equal(await taskRegion.locator('.task-progress').count(), 0, 'canceled status must remain hidden after reopening')
  record('Compact unfinished task panel disappears after cancellation and stays hidden after reopening')
  await page.getByRole('button', { name: '読ませました，完整活用', exact: true }).click()
  const seededChapter = await api(`/api/library/chapters/${chapters[0].id}`)
  const translatedSentence = seededChapter.sentences.find((row) => row.original.includes('読ませました'))
  const failedChapter = await api(`/api/library/chapters/${chapters[3].id}`)
  await api('/api/library', { upserts: { sentences: [{ ...translatedSentence, translation_zh: '原创验收译文：老师让学生读了书。' }], chapters: [{ ...failedChapter.chapter, status: 'failed', error: '模拟待重切失败章节' }] } }, 'PATCH')
  await page.getByRole('button', { name: '重新切分全书', exact: true }).click()
  assert.equal(await page.getByRole('dialog', { name: '重切影响范围' }).count(), 0, 'resegmentation starts directly')
  let job = await poll(() => api(`/api/jobs?book_id=${book.id}`), (rows) => rows.some((row) => row.kind === 'resegmentation-v1' && row.id !== failedTask.id && row.progress_current >= 1), 'first chapter committed')
  job = job.find((row) => row.kind === 'resegmentation-v1' && row.id !== failedTask.id)
  await page.getByRole('region', { name: '重新切分' }).getByRole('button', { name: '取消任务', exact: true }).click()
  job = await poll(() => api(`/api/jobs/${job.id}`), (row) => row.status === 'canceled', 'safe cancellation')
  assert.ok(job.progress_current >= 1 && job.progress_current < 4)
  await taskRegion.locator('.task-progress').waitFor({ state: 'hidden' })
  await page.reload()
  await page.getByRole('button', { name: `打开《${title}》`, exact: true }).click()
  assert.equal(await taskRegion.locator('.task-progress').count(), 0)
  await api(`/api/jobs/${job.id}/resume`, {})
  await page.reload()
  await page.getByRole('button', { name: `打开《${title}》`, exact: true }).click()
  job = await poll(() => api(`/api/jobs/${job.id}`), (row) => row.status === 'complete', 'resume after browser reload')
  assert.equal(job.progress_current, 4)
  assert.equal(new Set(job.result.chapters.map((row) => row.chapter_id)).size, 4)
  const recalculated = await api(`/api/library/chapters/${chapters[0].id}`)
  assert.ok(recalculated.sentences.every((row) => !row.translation_zh), 'resegmentation clears active old translations')
  record('Running cancellation hides the panel, retains commits and keeps the backend checkpoint recoverable')
  await page.getByRole('button', { name: '恢复本章重切前结果', exact: true }).waitFor()
  await screenshot('03-重切完成与恢复')
  let chapter = (await api(`/api/library/chapters/${chapters[0].id}`)).chapter
  const oldRevision = chapter.analysis_revision
  await page.getByRole('button', { name: '恢复本章重切前结果', exact: true }).click()
  await poll(() => api(`/api/library/chapters/${chapter.id}`), (next) => next.chapter.analysis_revision > oldRevision, 'restore chapter generation')
  const bookmark = (await api(`/api/library/bookmarks?book_id=${book.id}`))[0]
  const current = (await snapshot()).books.find((row) => row.id === book.id)
  assert.equal(current.currentChapterId, bookmark.chapterId)
  assert.equal(current.currentSentenceId, bookmark.sentenceId)
  const chapterData = await api(`/api/library/chapters/${chapters[0].id}`)
  assert.ok(chapterData.sentences.some((row) => row.id === bookmark.sentenceId && row.original.includes('読ませました')))
  assert.equal(chapterData.sentences.find((row) => row.original.includes('読ませました')).translation_zh, '原创验收译文：老师让学生读了书。')
  const cards = (await api('/api/cards/page')).items
  assert.equal(cards.length, 2)
  assert.ok(cards.every((row) => row.reps === 1 && row.source.original_sentence === candidates[0].source.original_sentence))
  await page.getByRole('button', { name: /书签\s*1/ }).click()
  await page.locator('.bookmark-item').click()
  await page.getByRole('button', { name: '読ませました，完整活用', exact: true }).waitFor()
  record('Generation restoration preserves reading anchor, mapped bookmark, grammar card old example, and SRS history')
  await page.getByRole('button', { name: '重新切分全书', exact: true }).click()
  const second = await poll(() => api(`/api/jobs?book_id=${book.id}`), (rows) => rows.some((row) => row.kind === 'resegmentation-v1' && row.id !== job.id && row.id !== failedTask.id), 'force all segmented chapters')
  const nextJob = second.find((row) => row.kind === 'resegmentation-v1' && row.id !== job.id && row.id !== failedTask.id)
  const finished = await poll(() => api(`/api/jobs/${nextJob.id}`), (row) => row.status === 'complete', 'already segmented chapters forced')
  assert.equal(finished.result.chapters.length, 4)
  assert.ok((await Promise.all(chapters.map((row) => api(`/api/library/chapters/${row.id}`)))).every((row) => row.chapter.analysis_revision >= 3))
  record('All four already segmented chapters are forcibly recalculated')
  assert.equal(await page.getByRole('button', { name: '人物与证据', exact: true }).count(), 0)
  await page.getByRole('button', { name: '设置', exact: true }).click()
  assert.equal(await page.getByText('人物证据预算', { exact: true }).count(), 0)
  assert.equal(await page.getByRole('checkbox', { name: '允许使用未读人物事实' }).count(), 0)
  await page.goto(`${origin}/#/book-memory`)
  await page.getByRole('heading', { name: '书架', exact: true }).waitFor()
  assert.equal(new URL(page.url()).hash, '#/library')
  record('Removed character/evidence UI, settings controls and legacy route; internal context service remains available')
  await openCards()
  await page.getByRole('button', { name: /卡片库/ }).click()
  await page.locator('.study-card-table article').first().getByText('原始例句与来源', { exact: true }).click()
  await screenshot('07-重切后保留旧例句')
  await page.locator('.study-card-table article').first().getByRole('button', { name: '定位来源原文', exact: true }).click()
  await page.getByRole('button', { name: '読ませました，完整活用', exact: true }).waitFor()
  record('Grammar card old source snapshot navigates to current original-text anchor')
  await page.setViewportSize({ width: 800, height: 1280 })
  await page.getByRole('button', { name: '设置', exact: true }).click()
  await screenshot('08-竖屏设置')
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true)
  await page.setViewportSize({ width: 480, height: 1000 })
  await screenshot('09-窄屏设置')
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true)
  assert.deepEqual(failures, [])
  record('Portrait layout and zero browser runtime errors')
  assert.deepEqual(apiFailures, [])
  await fs.writeFile(path.join(output, 'acceptance.json'), JSON.stringify({ passed: true, origin, title, assertions, failures, apiFailures, test_state: await api('/__test__/state') }, null, 2))
  console.log(JSON.stringify({ passed: true, output, count: assertions.length }))
} catch (error) {
  if (page) {
    await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => undefined)
    await fs.writeFile(path.join(output, 'failure.txt'), `${error.stack}\n${JSON.stringify({ failures, apiFailures }, null, 2)}\n${await page.locator('body').innerText()}`)
  }
  throw error
} finally {
  await browser?.close()
  if (process.platform === 'win32') await new Promise((resolve) => execFile('taskkill', ['/PID', String(server.pid), '/T', '/F'], { windowsHide: true }, () => resolve()))
  else server.kill()
  serverLog.end()
}
