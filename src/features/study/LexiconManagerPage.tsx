import { useEffect, useMemo, useRef, useState } from 'react'
import { bulkUpdateLexemes, importYomitanDictionary, installBuiltinDictionary, loadBlindspots, loadBuiltinDictionaryStatus, loadLexemePage, recordLearningEvents } from '../../api'
import type { Blindspot, BuiltinDictionaryStatus, LexemePage } from '../../api'
import { loadStudyData } from '../../db'
import { toHiragana } from '../../text'

const EMPTY: LexemePage = { items: [], total: 0, limit: 80, offset: 0, facets: { kana: [], sources: [], parts: [], groups: [] } }
const KANA_ROWS = [
  ['ア','イ','ウ','エ','オ'], ['カ','キ','ク','ケ','コ','ガ','ギ','グ','ゲ','ゴ'],
  ['サ','シ','ス','セ','ソ','ザ','ジ','ズ','ゼ','ゾ'], ['タ','チ','ツ','テ','ト','ダ','ヂ','ヅ','デ','ド'],
  ['ナ','ニ','ヌ','ネ','ノ'], ['ハ','ヒ','フ','ヘ','ホ','バ','ビ','ブ','ベ','ボ','パ','ピ','プ','ペ','ポ'],
  ['マ','ミ','ム','メ','モ'], ['ヤ','ユ','ヨ'], ['ラ','リ','ル','レ','ロ'], ['ワ','ヲ','ン'],
]
type BulkOperation = 'replace_senses'|'add_group'|'remove_group'|'mark_corrected'

function downloadJson(filename: string, value: unknown) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob); const anchor = document.createElement('a')
  anchor.href = url; anchor.download = filename; anchor.click(); URL.revokeObjectURL(url)
}

export default function LexiconManagerPage() {
  const [page, setPage] = useState<LexemePage>(EMPTY); const [offset, setOffset] = useState(0)
  const [query, setQuery] = useState(''); const [kana, setKana] = useState(''); const [source, setSource] = useState(''); const [part, setPart] = useState(''); const [group, setGroup] = useState('')
  const [selected, setSelected] = useState(new Set<string>()); const [scrollTop, setScrollTop] = useState(0)
  const [notice, setNotice] = useState(''); const [loading, setLoading] = useState(false); const [importing, setImporting] = useState(false)
  const [blindspots, setBlindspots] = useState<Blindspot[]>([]); const [builtin, setBuiltin] = useState<BuiltinDictionaryStatus | null>(null)
  const scroller = useRef<HTMLDivElement>(null); const rowHeight = 112; const viewport = 620

  async function refresh(signal?: AbortSignal, nextOffset = offset) {
    setLoading(true)
    try { setPage(await loadLexemePage({ q: query, kana, source, partOfSpeech: part, group, limit: 80, offset: nextOffset }, signal)) }
    finally { if (!signal?.aborted) setLoading(false) }
  }
  useEffect(() => {
    const controller = new AbortController(); const timer = window.setTimeout(() => { setOffset(0); void refresh(controller.signal, 0).catch((reason) => setNotice(reason instanceof Error ? reason.message : String(reason))) }, 180)
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [query, kana, source, part, group])
  useEffect(() => { const controller = new AbortController(); void Promise.all([loadBlindspots(controller.signal), loadBuiltinDictionaryStatus(controller.signal)]).then(([rows, state]) => { setBlindspots(rows); setBuiltin(state) }).catch(() => undefined); return () => controller.abort() }, [])
  useEffect(() => {
    if (!builtin || !['downloading','importing'].includes(builtin.job.status)) return
    const timer = window.setInterval(() => void loadBuiltinDictionaryStatus().then((state) => { setBuiltin(state); if (state.job.status === 'complete') void refresh(undefined, 0) }), 1000)
    return () => window.clearInterval(timer)
  }, [builtin?.job.status])

  async function importDictionary(file: File | null) {
    if (!file) return; setImporting(true); setNotice('')
    try { const result = await importYomitanDictionary(file); setNotice(`已导入 ${result.source}：${result.entries.toLocaleString()} 个词条。`) }
    catch (reason) { setNotice(reason instanceof Error ? reason.message : String(reason)) }
    finally { setImporting(false) }
  }
  async function mutate(operation: BulkOperation, value: unknown) {
    if (!selected.size) return
    try { const result = await bulkUpdateLexemes([...selected], operation, value); setNotice(`已更新 ${result.updated} 个词条`); setSelected(new Set()); await refresh() }
    catch (reason) { setNotice(reason instanceof Error ? reason.message : String(reason)) }
  }
  async function markBlindspot(item: Blindspot, mastered: boolean) {
    await recordLearningEvents([{ id: crypto.randomUUID(), item: { id: item.knowledge_item_id, type: item.type, canonical_key: item.canonical_key, lemma: item.lemma, reading: item.reading, grammar_pattern: item.grammar_pattern }, event_type: mastered ? 'mark_mastered' : 'mark_unknown', occurred_at: Date.now() / 1000, context: { source: 'blindspot-panel' } }])
    setBlindspots(await loadBlindspots())
  }
  async function exportAll() { const snapshot = await loadStudyData(); downloadJson(`冰读个人词库-${new Date().toISOString().slice(0,10)}.json`, { format:'bingdu-lexicon-v2', exportedAt:new Date().toISOString(), lexemes:snapshot.lexemes }) }

  const counts = useMemo(() => new Map(page.facets.kana.map((item) => [item.kana, item.count])), [page.facets.kana])
  const start = Math.max(0, Math.floor(scrollTop / rowHeight) - 3); const visible = page.items.slice(start, start + Math.ceil(viewport / rowHeight) + 6)
  function toggle(key: string) { setSelected((before) => { const next = new Set(before); if (next.has(key)) next.delete(key); else next.add(key); return next }) }
  function move(next: number) { setOffset(next); setScrollTop(0); if (scroller.current) scroller.current.scrollTop = 0; void refresh(undefined, next) }

  return <main className="app-page study-page">
    <header className="page-heading"><div><span>学习资料</span><h1>个人词库</h1><p>按假名、来源、词性和自定义分组管理已经保存的词义。</p></div></header>
    <section className="page-surface builtin-dictionary-card"><header><div><h2>内置日中词典</h2><p>{builtin?.package.title ?? 'Jitendex 日中简体词典'} · {builtin?.package.license ?? 'CC BY-SA 4.0'}</p></div>{builtin?.installed ? <span className="status-pill ok">已安装 {builtin.installed.entries.toLocaleString()} 条</span> : <button className="button primary small" onClick={() => void installBuiltinDictionary().then(setBuiltin)}>安装内置词典</button>}</header>
      {builtin?.job.message && <p className="muted">{builtin.job.message}{builtin.job.total ? ` · ${Math.round(builtin.job.downloaded / builtin.job.total * 100)}%` : ''}</p>}
      <small>来源：<a href={builtin?.package.homepage} target="_blank" rel="noreferrer">greyindex/jitendex-yomitan-zh</a>，经 MarvNC 词典目录筛选；派生数据遵循 CC BY-SA 4.0。</small>
    </section>
    <section className="page-surface lexicon-manager">
      <div className="data-tools"><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索词形、读音或释义" /><select value={source} onChange={(event) => setSource(event.target.value)}><option value="">全部来源</option>{page.facets.sources.map((item) => <option key={item.name}>{item.name}</option>)}</select><select value={part} onChange={(event) => setPart(event.target.value)}><option value="">全部词性</option>{page.facets.parts.map((item) => <option key={item.name}>{item.name}</option>)}</select><select value={group} onChange={(event) => setGroup(event.target.value)}><option value="">全部分组</option>{page.facets.groups.map((item) => <option key={item.name}>{item.name}</option>)}</select><label className="button small file-button"><input type="file" accept=".zip,application/zip" onChange={(event) => void importDictionary(event.target.files?.[0] ?? null)} />{importing ? '导入中…' : '导入其他词典'}</label><button className="button small" onClick={() => void exportAll()}>导出共享</button></div>
      {notice && <div className="info-box">{notice}</div>}
      <div className="lexicon-layout"><aside className="kana-tree"><button className={!kana ? 'active' : ''} onClick={() => setKana('')}>全部 <small>{page.facets.kana.reduce((sum,item) => sum + item.count,0)}</small></button>{KANA_ROWS.map((row) => <details key={row[0]} open={row.includes(kana)}><summary>{row[0]}行</summary>{row.filter((item) => counts.has(item)).map((item) => <button className={kana === item ? 'active' : ''} key={item} onClick={() => setKana(kana === item ? '' : item)}>{item}<small>{counts.get(item)}</small></button>)}</details>)}<details><summary>其他</summary>{page.facets.kana.filter((item) => !KANA_ROWS.flat().includes(item.kana)).map((item) => <button key={item.kana} className={kana === item.kana ? 'active' : ''} onClick={() => setKana(item.kana)}>{item.kana}<small>{item.count}</small></button>)}</details></aside>
        <div className="lexicon-results"><div className="lexicon-summary"><span>{loading ? '正在读取…' : `${page.total.toLocaleString()} 个词条`}</span><label><input type="checkbox" checked={!!page.items.length && selected.size === page.items.length} onChange={(event) => setSelected(event.target.checked ? new Set(page.items.map((item) => item.key)) : new Set())} />选择本页</label></div>
          {!!selected.size && <BulkToolbar count={selected.size} onRun={mutate} />}
          <div className="virtual-lexeme-list" ref={scroller} onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)} style={{ height: viewport }}><div style={{ height: page.items.length * rowHeight, position:'relative' }}>{visible.map((item, index) => <article className={selected.has(item.key) ? 'selected' : ''} key={item.key} style={{ position:'absolute', top:(start + index) * rowHeight, height:rowHeight - 8, left:0, right:0 }}><input type="checkbox" checked={selected.has(item.key)} onChange={() => toggle(item.key)} /><div><strong lang="ja">{item.lemma}</strong><span>{toHiragana(item.reading)} · {item.part_of_speech}</span><p>{item.senses_zh.join('；')}</p><small>{item.source}{item.correctedByUser ? ' · 用户已修正' : ''}{item.groups?.length ? ` · ${item.groups.join(' / ')}` : ''}</small></div></article>)}</div></div>
          <div className="card-pagination"><button disabled={offset === 0} onClick={() => move(Math.max(0,offset-page.limit))}>上一页</button><span>{Math.floor(offset/page.limit)+1} / {Math.max(1,Math.ceil(page.total/page.limit))}</span><button disabled={offset+page.limit >= page.total} onClick={() => move(offset+page.limit)}>下一页</button></div>
        </div></div>
    </section>
    <section className="page-surface blindspot-section"><h3>知识盲区</h3><p className="muted">根据查词、句意和语法求助记录生成。</p><div className="blindspot-list">{blindspots.length ? blindspots.map((item) => <article key={item.knowledge_item_id}><div><strong lang="ja">{item.grammar_pattern || item.lemma || item.canonical_key}</strong><span>掌握状态 {Math.round(item.mastery*100)}%</span></div><p>{item.reasons.join('；') || '证据仍少'}</p><div className="blindspot-actions"><button className="button small" onClick={() => void markBlindspot(item,false)}>仍不认识</button><button className="button small" onClick={() => void markBlindspot(item,true)}>已经掌握</button></div></article>) : <p className="muted">目前没有已识别的知识盲区。</p>}</div></section>
  </main>
}

function BulkToolbar({ count, onRun }: { count: number; onRun: (operation:BulkOperation, value:unknown) => Promise<void> }) {
  const [group, setGroup] = useState(''); const [senses, setSenses] = useState('')
  return <div className="lexeme-bulk"><strong>已选 {count} 项</strong><input value={group} onChange={(event) => setGroup(event.target.value)} placeholder="分组名称" /><button onClick={() => void onRun('add_group',group)}>加入分组</button><button onClick={() => void onRun('remove_group',group)}>移出分组</button><input value={senses} onChange={(event) => setSenses(event.target.value)} placeholder="统一释义，用 / 分隔" /><button onClick={() => void onRun('replace_senses',senses.split('/'))}>批量修正释义</button><button onClick={() => void onRun('mark_corrected',true)}>标记已校对</button></div>
}

