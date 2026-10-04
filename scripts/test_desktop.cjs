const { _electron: electron } = require('playwright')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const http = require('node:http')
const { spawn } = require('node:child_process')
const path = require('node:path')
const root = path.resolve(__dirname, '..')
const profile = path.join(root, 'build', `desktop-test-${Date.now()}`)
const executable = process.env.BINGDU_TEST_EXECUTABLE
const pageErrors = []
let instance
async function launch() {
  instance = await electron.launch({
    ...(executable ? { executablePath: executable, args: [] } : { args: [root] }),
    env: { ...process.env, BINGDU_DESKTOP_USER_DATA: profile, BINGDU_LEGACY_DATA_DIR: '', BINGDU_DESKTOP_TEST: '1' },
    timeout: 150000,
  })
  const page = await instance.firstWindow({ timeout: 150000 })
  page.on('pageerror', error => pageErrors.push(error.message))
  await page.waitForFunction(() => location.protocol === 'bingdu:' && window.bingduDesktop && document.querySelector('h1'), { timeout: 30000 })
  return page
}
async function api(page, url, options) {
  return page.evaluate(async ({ url, options }) => {
    const response = await fetch(url, options)
    return { status: response.status, value: await response.json() }
  }, { url, options })
}
;(async () => {
  let page = await launch()
  assert.equal(await page.evaluate(() => typeof window.require), 'undefined')
  assert.equal((await api(page, '/api/health')).value.app, 'bingdu')
  const legal = await api(page, '/api/legal')
  assert.equal(legal.status, 200)
  assert.equal(legal.value.license, 'AGPL-3.0-or-later')
  await page.getByRole('button', { name: '源码与许可证', exact: true }).click()
  await page.locator('.legal-license-text').filter({ hasText: 'GNU AFFERO GENERAL PUBLIC LICENSE' }).waitFor()
  await page.locator('.legal-dialog').getByRole('button', { name: '关闭', exact: true }).click()
  await page.getByRole('button', { name: '下载对应源码', exact: true }).click()
  // Electron's session handles custom-protocol downloads outside Playwright's
  // page download events. Check the completed native file, as for migrations.
  const sourcePath = path.join(profile, 'test-download.zip')
  let sourceHash = ''
  for (let attempt = 0; attempt < 60; attempt++) {
    if (fs.existsSync(sourcePath)) sourceHash = require('node:crypto').createHash('sha256').update(fs.readFileSync(sourcePath)).digest('hex')
    if (sourceHash === legal.value.source_sha256) break
    await new Promise(resolve => setTimeout(resolve, 1000))
  }
  assert.ok(fs.existsSync(sourcePath), 'native corresponding-source download')
  assert.equal(sourceHash, legal.value.source_sha256)
  fs.unlinkSync(sourcePath)
  const user = (await api(page, '/api/me')).value
  const runtime = await instance.evaluate(({ app }) => app.__bingduTest)
  const refused = await new Promise((resolve, reject) => {
    http.get(`http://127.0.0.1:${runtime.port}/api/health`, response => { response.resume(); resolve(response.statusCode) }).on('error', reject)
  })
  assert.equal(refused, 403)
  const duplicate = spawn(executable || require('electron'), executable ? [] : [root], { windowsHide: true,
    env: { ...process.env, BINGDU_DESKTOP_USER_DATA: profile, BINGDU_LEGACY_DATA_DIR: '', BINGDU_DESKTOP_TEST: '1' } })
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { duplicate.kill(); reject(new Error('second instance did not exit')) }, 15000)
    duplicate.once('exit', code => { clearTimeout(timer); assert.equal(code, 0); resolve() })
  })
  assert.ok(user.user_id)
  const info = await page.evaluate(() => window.bingduDesktop.info())
  assert.ok(info.dataDirectory.startsWith(profile))
  assert.equal((await api(page, '/api/library/index')).status, 200)
  assert.equal((await page.evaluate(async () => (await fetch('/bingdu-logo.png')).status)), 200)
  assert.equal((await page.evaluate(async () => (await fetch('/bingdu-logo-click.wav', { headers: { range: 'bytes=0-100' } })).status)), 206)
  if (executable) assert.equal((await api(page, '/api/dictionary/sources')).value.some(item => item.package_id === 'greyindex/jitendex-yomitan-zh'), false)
  const security = await instance.evaluate(({ BrowserWindow }) => {
    const preferences = BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences()
    return { sandbox: preferences.sandbox, nodeIntegration: preferences.nodeIntegration, contextIsolation: preferences.contextIsolation }
  })
  assert.deepEqual(security, { sandbox: true, nodeIntegration: false, contextIsolation: true })
  const post = body => ({ method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
  assert.equal((await api(page, '/api/import/text', post({ title: '文本验收', text: '吾輩は猫である。' }))).status, 200)
  const JSZip = require('jszip')
  const epub = new JSZip()
  epub.file('mimetype', 'application/epub+zip')
  epub.file('META-INF/container.xml', '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
  epub.file('OEBPS/content.opf', '<?xml version="1.0"?><package version="3.0" unique-identifier="id" xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">desktop-test</dc:identifier><dc:title>客户端验收</dc:title><dc:language>ja</dc:language></metadata><manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/><item id="image" href="image.png" media-type="image/png"/></manifest><spine><itemref idref="chapter"/></spine></package>')
  epub.file('OEBPS/chapter.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>正文</title></head><body><p>吾輩は猫である。</p><p>名前はまだ無い。</p><img src="image.png"/></body></html>')
  epub.file('OEBPS/image.png', Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII=', 'base64'))
  const parsed = await page.evaluate(async bytes => {
    const form = new FormData(); form.append('file', new Blob([new Uint8Array(bytes)]), 'test.epub')
    const response = await fetch('/api/import/epub', { method: 'POST', body: form })
    return { status: response.status, value: await response.json() }
  }, [...await epub.generateAsync({ type: 'nodebuffer' })])
  assert.equal(parsed.status, 200)
  const chapter = parsed.value.chapters[0]
  const analyzed = await api(page, '/api/preprocess', post({ chapter_id: chapter.id, text: chapter.text }))
  assert.equal(analyzed.status, 200)
  assert.ok(analyzed.value.sentences.length >= 2)
  const sentence = analyzed.value.sentences[1]
  const book = { id: 'desktop-book', title: '客户端验收', author: '测试', createdAt: 1, updatedAt: 2, lastOpenedAt: 3, currentChapterId: chapter.id, currentSentenceId: sentence.id, showImages: false }
  const saved = await api(page, '/api/library', { ...post({ books: [book], chapters: [{ ...chapter, bookId: book.id, originalHtmlUrl: chapter.original_html_url, status: 'local-ready' }], sentences: analyzed.value.sentences, tokens: analyzed.value.tokens.map(token => ({ ...token, lexemeKey: `${token.lemma}|${token.reading}` })) }), method: 'PUT' })
  assert.equal(saved.status, 200)
  const illustration = chapter.blocks.find(block => block.type === 'image')
  assert.ok(illustration)
  assert.equal((await page.evaluate(async url => (await fetch(url)).status, illustration.asset_url)), 200)
  const candidate = await api(page, '/api/cards/candidates', post({ item: { id: 'desktop-knowledge', type: 'vocabulary', canonical_key: '猫|ねこ', lemma: '猫', reading: 'ねこ' }, lemma: '猫', reading: 'ねこ', gloss: '猫', sentence: chapter.text, book_id: book.id, sentence_id: sentence.id }))
  assert.equal(candidate.status, 200)
  assert.equal((await api(page, `/api/cards/candidates/${candidate.value.id}/accept`, post({}))).status, 200)
  assert.equal((await api(page, '/api/cards?q=' + encodeURIComponent('猫'))).value.length, 1)
  await page.evaluate(async () => {
    const response = await fetch('/api/data/book-transfer')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a'); link.href = url; link.download = 'transfer.zip'; link.click()
  })
  const download = path.join(profile, 'test-download.zip')
  for (let attempt = 0; attempt < 100 && !fs.existsSync(download); attempt++) await new Promise(resolve => setTimeout(resolve, 100))
  assert.ok(fs.existsSync(download), 'native migration download')
  assert.equal(fs.readFileSync(download).subarray(0, 2).toString(), 'PK')
  const uploaded = await page.evaluate(async bytes => {
    const form = new FormData(); form.append('file', new Blob([new Uint8Array(bytes)], { type: 'application/zip' }), 'transfer.zip')
    const response = await fetch('/api/data/book-transfer/import', { method: 'POST', body: form })
    return { status: response.status, value: await response.json() }
  }, [...fs.readFileSync(download)])
  assert.equal(uploaded.status, 200)
  assert.equal(uploaded.value.skipped_books, 1)
  // Same-account package import must not duplicate cards.
  assert.equal(uploaded.value.imported_cards, 0)
  await page.evaluate(id => localStorage.setItem(`bingdu:${id}:entered`, '1'), user.user_id)
  await page.reload()
  assert.equal((await api(page, '/api/me')).value.user_id, user.user_id)
  await api(page, '/api/library', { ...post({ upserts: { books: [{ ...book, id: 'unselected-book', title: '不分享的书' }] } }), method: 'PATCH' })
  await page.reload()
  await page.getByRole('button', { name: '分享书籍', exact: true }).click()
  await page.locator('.collection-book-picker label').filter({ hasText: '客户端验收' }).getByRole('checkbox').check()
  fs.unlinkSync(download)
  await page.getByRole('button', { name: '导出分享包（1 本）', exact: true }).click()
  for (let attempt = 0; attempt < 100 && !fs.existsSync(download); attempt++) await new Promise(resolve => setTimeout(resolve, 100))
  assert.ok(fs.existsSync(download), 'native selected book download')
  const shared = await JSZip.loadAsync(fs.readFileSync(download))
  const sharedPayload = JSON.parse(await shared.file('data.json').async('string'))
  assert.equal(JSON.parse(await shared.file('manifest.json').async('string')).book_count, 1)
  assert.deepEqual(sharedPayload.records.filter(item => item.table_name === 'books').map(item => item.record_key), [book.id])
  assert.deepEqual(sharedPayload.learning, {})
  await page.locator('.library-heading-actions input[type="file"]').setInputFiles(download)
  await page.getByRole('status').filter({ hasText: '已载入 0 本书及其切分、翻译结果，跳过 1 本已有书籍。' }).waitFor().catch(async error => {
    console.error('Share import UI:', await page.locator('body').innerText())
    throw error
  })
  await page.getByRole('button', { name: '打开《客户端验收》', exact: true }).click()
  await page.locator(`[data-sentence-id="${sentence.id}"].selected`).waitFor()
  await page.getByRole('button', { name: '原书预览', exact: true }).click()
  assert.ok((await page.frameLocator('iframe.original-preview').locator('body').innerText()).includes('猫'))
  let illustrationLoaded = false
  for (let attempt = 0; attempt < 100; attempt++) {
    try { illustrationLoaded = await page.frameLocator('iframe.original-preview').locator('img').evaluate(image => image.complete && image.naturalWidth > 0) }
    catch (error) { if (!String(error).includes('Frame was detached')) throw error }
    if (illustrationLoaded) break
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  assert.ok(illustrationLoaded, 'original illustration must finish loading')
  await page.getByRole('button', { name: '冰读模式', exact: true }).click()
  await page.getByRole('button', { name: '设置', exact: true }).click()
  await page.getByRole('button', { name: '继续阅读', exact: true }).click()
  await page.locator(`[data-sentence-id="${sentence.id}"].selected`).waitFor()
  await page.locator(`[data-sentence-id="${sentence.id}"] .token.content`).first().click()
  await page.getByRole('button', { name: 'AI 修正释义', exact: true }).click()
  await page.locator('.word-correction textarea').fill('测试修正提示')
  await page.evaluate(() => {
    const originalFetch = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      if (String(input) === '/api/words/correct') {
        const body = JSON.parse(init.body)
        return new Response(JSON.stringify({ context_sense: { token_id: body.token.id, gloss_zh: '本句修正语境义' },
          lexeme: { key: body.token.lexemeKey, lemma: body.token.lemma, reading: body.token.reading,
            part_of_speech: body.token.part_of_speech, senses_zh: ['修正词义'], source: 'AI 修正（用户确认）' } }), { headers: { 'Content-Type': 'application/json' } })
      }
      return originalFetch(input, init)
    }
  })
  await page.getByRole('button', { name: '生成修正', exact: true }).click()
  await page.locator('.word-correction .context-gloss').waitFor()
  assert.ok((await page.locator('.word-correction').innerText()).includes('本句修正语境义'))
  // Preview must not persist before the user confirms.
  assert.equal((await api(page, '/api/library/lexemes?q=' + encodeURIComponent('修正词义'))).value.total, 0)
  await page.getByRole('button', { name: '确认保存', exact: true }).click()
  await page.locator('.word-correction').waitFor({ state: 'hidden' })
  assert.ok((await page.locator('.dictionary-card .context-gloss').innerText()).includes('本句修正语境义'))
  assert.equal((await api(page, '/api/library/lexemes?q=' + encodeURIComponent('修正词义'))).value.total, 1)
  await instance.close(); instance = null
  assert.throws(() => process.kill(runtime.backendPid, 0), 'sidecar must stop after client exits')
  page = await launch()
  assert.equal((await api(page, '/api/me')).value.user_id, user.user_id)
  assert.equal((await api(page, '/api/library/index')).value.books[0].currentSentenceId, sentence.id)
  assert.equal((await api(page, '/api/cards?q=' + encodeURIComponent('猫'))).value.length, 1)
  await instance.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].show())
  await page.screenshot({ path: path.join(profile, 'desktop.png') })
  await instance.close(); instance = null
  assert.deepEqual(pageErrors, [])
  console.log(JSON.stringify({ status: 'passed', profile, user: user.user_id, security }))
})().catch(async error => { console.error(error); if (instance) await instance.close(); process.exitCode = 1 })
