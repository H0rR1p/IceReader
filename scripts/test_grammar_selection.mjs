// Independent boundary/candidate and rendered interaction checks. No user data or AI calls.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { chromium } from 'playwright'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const { buildSelectionIndex, selectionCandidates, isVocabularyToken, codepointSlice } = await import(pathToFileURL(path.join(root, 'src/features/reader/learningSpans.ts')))
const output = path.join(root, 'build/grammar-selection-check', String(Date.now()))
await fs.mkdir(output, { recursive: true })
function token(id, start, surface, pos = '助詞-終助詞', reading = '') {
  return { id, sentence_id: 'sentence', start, end: start + Array.from(surface).length, surface, lemma: surface,
    reading, part_of_speech: pos, is_content: pos.startsWith('名詞') || pos.startsWith('動詞'),
    role: pos.startsWith('名詞') || pos.startsWith('動詞') ? 'lexical' : 'grammatical', lexemeKey: `${surface}|${reading}|${pos}` }
}
function span(text, id, start, end, role, extras = {}) {
  return { id, sentence_id: 'sentence', start, end, surface: codepointSlice(text, start, end), lemma: '', reading: '',
    kind: role === 'morphology' ? 'morphology' : role === 'lexical' ? 'lexical' : 'construction', unit_role: role,
    grammar_ids: [], features: [], token_ids: [], steps: [], candidates: [], explanation_zh: '原创检查说明',
    children: [], derivation: [], source: 'rule', version: 'test', status: 'determined', captures: {}, evidence: [],
    marker_ranges: [{ start, end }], boundary_status: 'supported', ...extras }
}
const text = '𠮷田んじゃないか？'
const tokens = [token('name', 0, '𠮷田', '名詞-固有名詞', 'ヨシダ'), token('nominal', 2, 'ん'),
  token('copula', 3, 'じゃ'), token('negative', 5, 'ない', '助動詞'), token('ending', 7, 'か'), token('punctuation', 8, '？', '補助記号', '')]
const spans = [span(text, 'outer', 2, 8, 'ending'), span(text, 'whole', 3, 8, 'ending', { status: 'ambiguous', captures: { grammar_label: '确认' } }),
  span(text, 'whole-other', 3, 8, 'ending', { status: 'ambiguous', captures: { grammar_label: '感叹' } }),
  span(text, 'inner', 3, 7, 'construction'), span(text, 'cross', 0, 6, 'construction')]
const index = buildSelectionIndex(text, tokens, spans)
assert.equal(index.fragments.map((part) => part.text).join(''), text, 'Unicode original must be neither swallowed nor repeated')
for (const position of [3, 4, 5, 6, 7]) assert.equal(selectionCandidates(index, position)[0].id, 'whole')
assert.equal(selectionCandidates(index, 2)[0].id, 'outer')
assert.ok(selectionCandidates(index, 5).some((item) => item.id === 'cross'), 'crossing nodes remain candidates')
assert.ok(selectionCandidates(index, 5).some((item) => item.id === 'inner'), 'internal nodes remain candidates')
assert.ok(selectionCandidates(index, 5).some((item) => item.id === 'whole-other'), 'same-boundary meanings remain candidates')
assert.equal(isVocabularyToken(tokens[3]), false)
for (const atom of tokens.slice(0, -1)) for (let p = atom.start; p < atom.end; p += 1) {
  assert.ok(selectionCandidates(index, p).length, `no meaningful position may be unreachable: ${p}`)
}
const splitText = 'じゃないか'
const split = buildSelectionIndex(splitText, [token('merged', 0, splitText, '助動詞')], [span(splitText, 'partial', 1, 4, 'construction')])
assert.equal(split.fragments.map((part) => part.text).join(''), splitText)
assert.ok(split.fragments.every((part) => selectionCandidates(split, part.start).length), 'boundaries cutting a token must stay interactive')
const inflectionText = '食べさせられなかった'
const inflectionTokens = [token('verb', 0, '食べ', '動詞-一般', 'タベ'), token('chain', 2, 'させられなかった', '助動詞')]
const inflectionSpans = [span(inflectionText, 'morph', 0, Array.from(inflectionText).length, 'morphology', { lemma: '食べる' })]
const morphology = buildSelectionIndex(inflectionText, inflectionTokens, inflectionSpans)
for (let p = 0; p < Array.from(inflectionText).length; p += 1) assert.equal(selectionCandidates(morphology, p)[0].id, 'morph')
const uncached = buildSelectionIndex('でも', [token('de', 0, 'で'), token('mo', 1, 'も')], [])
assert.equal(selectionCandidates(uncached, 0)[0].unit_role, 'particle')
assert.equal(selectionCandidates(uncached, 1)[0].unit_role, 'particle')

// These checks use the actual tokenizer, complete resource catalogue and graph.
// They catch recommendation failures which fabricated browser fixtures cannot.
const liveSamples = [
  { text: '聞くんじゃなかった。', expression: 'じゃなかった' },
  { text: '彼は学生ではありません。', expression: 'ではありません' },
  { text: '読んだらよかったんじゃないか？', expression: 'じゃないか' },
  { text: '何か問題でもあるか？', expression: 'でも' },
  { text: 'これくらいなら遊べる。', expression: 'くらいなら' },
  { text: '私は野菜を食べさせられなかった。', expression: '食べさせられなかった' },
  { text: '本を読んでおけばよかった。', expression: '読んでおけば' },
]
const livePython = `import json,sys\nfrom backend.nlp import tokenize_sentence\nfrom backend.modules.linguistics.service import analyze_sentence\nrows=[]\nfor i,sample in enumerate(json.loads(sys.argv[1])):\n text=sample['text'];tokens=tokenize_sentence(str(i),text);rows.append(dict(**sample,tokens=tokens,spans=analyze_sentence(str(i),text,tokens)['learning_spans']))\nprint(json.dumps(rows,ensure_ascii=False))`
const python = process.env.BINGDU_UI_TEST_PYTHON || path.join(root, '.venv64/Scripts/python.exe')
const live = await promisify(execFile)(python, ['-c', livePython, JSON.stringify(liveSamples)], {
  cwd: root, windowsHide: true, encoding: 'utf8', maxBuffer: 12 * 1024 * 1024,
  env: { ...process.env, PYTHONIOENCODING: 'utf-8', BINGDU_DATA_DIR: path.join(output, 'data') },
})
const liveGraphs = JSON.parse(live.stdout)
const liveReport = []
for (const row of liveGraphs) {
  const liveIndex = buildSelectionIndex(row.text, row.tokens, row.spans)
  const start = Array.from(row.text.slice(0, row.text.indexOf(row.expression))).length
  const end = start + Array.from(row.expression).length
  for (let p = start; p < end; p += 1) {
    const candidates = selectionCandidates(liveIndex, p)
    assert.equal(candidates[0]?.surface, row.expression, `complete real graph expression at ${p}: ${row.text}`)
  }
  assert.equal(liveIndex.fragments.map((part) => part.text).join(''), row.text)
  for (const atomic of row.spans.filter((span) => span.features.includes('atomic'))) {
    for (let p = atomic.start; p < atomic.end; p += 1) assert.ok(selectionCandidates(liveIndex, p).some((span) => span.id === atomic.id), 'atomic entry survives all defaults')
  }
  liveReport.push({ text: row.text, expression: row.expression, checkedCodepoints: end - start, graphNodes: row.spans.length })
}
await fs.writeFile(path.join(output, 'live-graph-acceptance.json'), JSON.stringify({ passed: true, samples: liveReport }, null, 2))

const harness = `import React, { useState } from 'react'; import { createRoot } from 'react-dom/client';
import LearningSpanText from '/src/features/reader/LearningSpanText.tsx';
import { LearningSelectionChoices } from '/src/features/reader/GrammarStructurePanel.tsx';
import '/src/styles.css';
const data=${JSON.stringify({ text, tokens, spans, splitText, splitTokens: [token('merged', 0, splitText, '助動詞')], splitSpans: [span(splitText, 'partial', 1, 4, 'construction')] })};
function App(){const [selected,setSelected]=useState(null),[choices,setChoices]=useState([]),[grammar,setGrammar]=useState(true);
window.__hits ??=[]; return <><label><input id="grammar" type="checkbox" checked={grammar} onChange={e=>setGrammar(e.target.checked)}/>语法提示</label>
<div id="main" className="japanese-text" style={{fontSize:28,padding:30,width:700}}><LearningSpanText text={data.text} tokens={data.tokens} spans={data.spans} selectedSpanId={selected} selectedTokenId={null}
showFurigana={true} showGrammar={grammar} assistanceLevel={()=>3} onSelectSpan={()=>{}} onSelectToken={()=>{}} onSelectPosition={(position,candidates)=>{window.__hits.push({position,ids:candidates.map(n=>n.id)});setSelected(candidates[0]?.id);setChoices(candidates);}}/></div>
<LearningSelectionChoices candidates={choices} selectedId={selected} onSelect={s=>setSelected(s.id)}/><output id="selected">{selected}</output>
<div id="split" className="japanese-text" style={{fontSize:28,padding:30}}><LearningSpanText text={data.splitText} tokens={data.splitTokens} spans={data.splitSpans} selectedSpanId={null} selectedTokenId={null} showFurigana={false} showGrammar={grammar} assistanceLevel={()=>0} onSelectSpan={()=>{}} onSelectToken={()=>{}} onSelectPosition={(position,candidates)=>window.__splitHit={position,ids:candidates.map(n=>n.id)}}/></div></>};createRoot(document.getElementById('root')).render(<App/>);`
await fs.writeFile(path.join(output, 'harness.tsx'), harness)
const harnessUrl = '/' + path.relative(root, path.join(output, 'harness.tsx')).split(path.sep).join('/')
const vite = await createServer({ root, configFile: false, plugins: [react(), { name: 'selection-check-html', configureServer(server) {
  server.middlewares.use('/__selection_check', (req, res) => { res.setHeader('Content-Type', 'text/html'); res.end(`<html><head><script type="module">import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script></head><body><div id="root"></div><script type="module" src="${harnessUrl}"></script></body></html>`) })
} }], optimizeDeps: { noDiscovery: true, include: ['react', 'react-dom/client', 'react/jsx-dev-runtime'] }, server: { host: '127.0.0.1', port: 0 } })
let browser
const errors = []
try {
  await vite.listen()
  const address = vite.httpServer.address()
  browser = await chromium.launch({ headless: true, channel: 'chrome' })
  const page = await browser.newPage({ viewport: { width: 1100, height: 800 } })
  page.on('pageerror', e => errors.push(e.message))
  await page.goto(`http://127.0.0.1:${address.port}/__selection_check`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await page.locator('#main .learning-fragment[role="button"]').first().waitFor({ timeout: 60000 })
  assert.equal(await page.locator('#main').evaluate(node=>{const clone=node.cloneNode(true);clone.querySelectorAll('rt').forEach(rt=>rt.remove());return clone.textContent}), text)
  for (const start of [3, 5, 7]) {
    await page.locator(`#main [data-learning-start="${start}"]`).click()
    assert.equal(await page.locator('#selected').innerText(), 'whole')
  }
  await page.locator('#main [data-learning-start="5"]').click()
  const chooser = page.getByRole('combobox', { name: '查看层级' })
  for (const id of ['inner', 'cross', 'whole-other', 'outer', 'reader-token:negative']) {
    await chooser.selectOption(id); assert.equal(await page.locator('#selected').innerText(), id)
  }
  assert.equal(await page.locator('#main [role="button"] [role="button"]').count(), 0, 'no nested button semantics')
  assert.equal(await page.locator('#main [tabindex="0"]').count(), 1, 'one Tab stop per sentence, arrows navigate its parts')
  await page.locator('#main [data-learning-start="5"]').focus()
  await page.keyboard.press('Enter'); assert.equal(await page.locator('#selected').innerText(), 'whole')
  await page.keyboard.press(' '); assert.equal(await page.locator('#selected').innerText(), 'whole')
  await page.keyboard.press('ArrowRight'); assert.equal(await page.evaluate(()=>document.activeElement.dataset.learningStart), '6')
  await page.locator('#grammar').uncheck()
  await page.locator('#main [data-learning-start="7"]').click(); assert.equal(await page.locator('#selected').innerText(), 'whole')
  await page.locator('#main rt').click()
  assert.equal(await page.evaluate(()=>window.__hits.at(-1).position), 0, 'reading maps back to base text, not a reading offset')
  const before = await page.evaluate(()=>window.__hits.length)
  const first = await page.locator('#main [data-learning-start="3"]').boundingBox()
  const last = await page.locator('#main [data-learning-start="7"]').boundingBox()
  await page.mouse.move(first.x + 2, first.y + first.height/2); await page.mouse.down()
  await page.mouse.move(last.x+last.width-2,last.y+last.height/2,{steps:12}); await page.mouse.up()
  assert.equal(await page.evaluate(()=>window.__hits.length), before, 'drag selection must not trigger a jump')
  await page.evaluate(()=>window.getSelection().removeAllRanges())
  for (const part of split.fragments) {
    await page.locator(`#split [data-learning-start="${part.start}"]`).click()
    assert.ok((await page.evaluate(()=>window.__splitHit.ids)).length, 'partial-token fragments all handle clicks')
  }
  await page.setViewportSize({ width: 420, height: 850 })
  await page.locator('#main').evaluate(node=>{node.style.width='100%';node.style.boxSizing='border-box'})
  await page.locator('#main [data-learning-start="7"]').click()
  await page.screenshot({ path: path.join(output, 'selection.png'), fullPage: true })
  assert.deepEqual(errors, [])
  const report={passed:true,output,assertions:['all meaningful source positions reachable','Unicode exact source','partial token boundaries clickable','same-range ambiguity retained','internal and crossing candidates retained','complete endings and long inflections recommended','one Tab stop and arrow movement','Enter and Space share selection','ruby maps to base','grammar toggle preserves selection','drag does not jump'],errors}
  await fs.writeFile(path.join(output,'acceptance.json'),JSON.stringify(report,null,2))
  console.log(JSON.stringify(report))
} finally { await browser?.close(); await vite.close() }
