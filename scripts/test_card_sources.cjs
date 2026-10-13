const { _electron: electron } = require('playwright')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.resolve(__dirname, '..')
const profile = fs.mkdtempSync(path.join(root, 'build', 'card-source-ui-'))
const executable = process.env.BINGDU_TEST_EXECUTABLE
const errors = []
let instance, page
const post = body => ({ method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
async function api(url, options) {
  return page.evaluate(async ({ url, options }) => {
    const response = await fetch(url, options)
    if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`)
    return response.json()
  }, { url, options })
}

;(async () => {
  instance = await electron.launch({
    ...(executable ? { executablePath: executable, args: [] } : { args: [root] }),
    env: { ...process.env, BINGDU_DESKTOP_USER_DATA: profile,
      BINGDU_DATA_DIR: path.join(profile, 'data'), BINGDU_LEGACY_DATA_DIR: '', BINGDU_DESKTOP_TEST: '1' },
    timeout: 150000,
  })
  page = await instance.firstWindow({ timeout: 150000 })
  page.setDefaultTimeout(20000)
  page.on('pageerror', error => errors.push(error.message))
  await page.waitForFunction(() => location.protocol === 'bingdu:' && document.querySelector('h1'), { timeout: 30000 })
  const me = await api('/api/me')
  const candidate = await api('/api/cards/candidates', post({
    item: { id: 'card-source-test', type: 'vocabulary', canonical_key: '猫|ねこ', lemma: '猫', reading: 'ねこ' },
    lemma: '猫', reading: 'ねこ', gloss: '猫', sentence: '猫が本を読む。', sentence_id: 'source-sentence',
  }))
  assert.equal(candidate.source, null)
  assert.equal((await api('/api/cards/candidates'))[0].source, null)
  const accepted = await api(`/api/cards/candidates/${candidate.id}/accept`, post({}))
  assert.equal(accepted.source, null)
  assert.equal((await api('/api/cards/page')).items[0].source, null)
  assert.equal((await api('/api/cards/due'))[0].source, null)

  // Simulate historical API responses independently of the backend fix.  An
  // empty object and a grammar source without derivation must both be safe.
  const base = { ...accepted, book_id: '', chapter_id: '', sentence_id: '', book_title: '原创测试',
    tags: [], status: 'active', stability: 1, difficulty: 5, reps: 0, lapses: 0 }
  const source = { kind: 'grammar', grammar_id: 'ja.morph.causative', span_id: 'span', quote: '読ませる',
    start: 0, end: 4, chapter_anchor: 0, analysis_revision: 1, rules_version: 'test',
    original_sentence: '先生が本を読ませる。', question: '先生が本を読ませる。', answer: '让人读',
    base_form: '読む', explanation: '让别人做这件事。' }
  const cards = [
    { ...base, id: 'legacy-word', lemma: '猫', source: {} },
    { ...base, id: 'legacy-grammar', lemma: '読ませる', card_template: 'grammar-recognition', source },
    { ...base, id: 'valid-grammar', lemma: '読ませる', card_template: 'grammar-recognition',
      source: { ...source, derivation: ['読む', '読ませる'] } },
  ]
  await page.evaluate(id => localStorage.setItem(`bingdu:${id}:entered`, '1'), me.user_id)
  await page.addInitScript(({ cards }) => {
    const original = window.fetch.bind(window)
    window.fetch = (input, init) => {
      const pathname = new URL(typeof input === 'string' ? input : input.url, location.href).pathname
      const method = init?.method || (typeof input === 'object' && input.method) || 'GET'
      let value
      if (method === 'GET' && pathname === '/api/cards/candidates') value = cards
      if (method === 'GET' && pathname === '/api/cards/page') value = { items: cards, total: cards.length, offset: 0, limit: 50 }
      if (method === 'GET' && pathname === '/api/cards/due') value = cards
      if (method === 'GET' && pathname === '/api/cards/summary') value = { candidates: 3, active: 3, due_now: 3,
        due_7_days: 3, due_30_days: 3, new_today: 3, daily_new_limit: 20, daily_review_limit: 200 }
      if (method === 'POST' && /^\/api\/cards\/[^/]+\/review$/.test(pathname)) value = {}
      return value === undefined ? original(input, init) : Promise.resolve(new Response(JSON.stringify(value),
        { headers: { 'content-type': 'application/json' } }))
    }
  }, { cards })
  await page.goto('bingdu://app/#/cards')
  await page.reload()
  await page.getByRole('heading', { name: '词语与语法卡片', exact: true }).waitFor()
  await page.locator('.candidate-list article').last().waitFor()
  assert.equal(await page.locator('.candidate-list article').count(), 3)
  assert.equal(await page.locator('.candidate-list details').count(), 2)
  const valid = page.locator('.candidate-list article').last()
  await valid.getByText('原始例句与来源', { exact: true }).click()
  assert.ok((await valid.innerText()).includes('読む → 読ませる'))
  await page.getByRole('button', { name: /卡片库/ }).click()
  assert.equal(await page.locator('.study-card-table article').count(), 3)
  assert.equal(await page.locator('.study-card-table details').count(), 2)
  await page.getByRole('button', { name: /开始今日复习/ }).click()
  for (let index = 0; index < cards.length; index++) {
    await page.getByRole('button', { name: '显示答案', exact: true }).click()
    await page.locator('.review-answer').waitFor()
    if (index === 2) assert.ok((await page.locator('.review-card').innerText()).includes('読む → 読ませる'))
    await page.getByRole('button', { name: '记得', exact: true }).click()
  }
  await page.getByText('今天的到期卡已完成', { exact: true }).waitFor()
  assert.deepEqual(errors, [])
  const result = { passed: true, executable: executable || 'source', checks: [
    'Vocabulary source is null on create/list/accept/page/due APIs',
    'Legacy empty source is safe in candidates and accepted cards',
    'Grammar source without derivation is safe in cards and review',
    'Valid grammar derivation remains visible',
    'Three review answers complete without JavaScript errors',
  ] }
  fs.writeFileSync(path.join(profile, 'acceptance.json'), JSON.stringify(result, null, 2))
  console.log(JSON.stringify({ ...result, output: profile }))
})().catch(async error => {
  console.error(error)
  if (page) await page.screenshot({ path: path.join(profile, 'failure.png') }).catch(() => {})
  process.exitCode = 1
}).finally(async () => { await instance?.close() })
