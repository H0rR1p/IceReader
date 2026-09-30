import { useEffect, useState } from 'react'
import { importYomitanDictionary, loadBlindspots, recordLearningEvents } from '../../api'
import type { Blindspot } from '../../api'
import { loadStudyData } from '../../db'
import type { Lexeme } from '../../types'
import { toHiragana } from '../../text'

function downloadJson(filename: string, value: unknown) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

export default function StudyDataPage() {
  const [lexemes, setLexemes] = useState<Lexeme[]>([])
  const [query, setQuery] = useState('')
  const [dictionaryNotice, setDictionaryNotice] = useState('')
  const [importingDictionary, setImportingDictionary] = useState(false)
  const [blindspots, setBlindspots] = useState<Blindspot[]>([])

  async function importDictionary(file: File | null) {
    if (!file) return
    setImportingDictionary(true)
    setDictionaryNotice('')
    try {
      const result = await importYomitanDictionary(file)
      setDictionaryNotice(`已导入 ${result.source}：${result.entries.toLocaleString()} 个词条。`)
    } catch (error) {
      setDictionaryNotice(error instanceof Error ? error.message : String(error))
    } finally {
      setImportingDictionary(false)
    }
  }

  useEffect(() => {
    const controller = new AbortController()
    void Promise.all([loadStudyData(controller.signal), loadBlindspots(controller.signal)]).then(([snapshot, blindspotRows]) => {
      if (!controller.signal.aborted) {
        setLexemes([...snapshot.lexemes].sort((a, b) => a.reading.localeCompare(b.reading, 'ja')))
        setBlindspots(blindspotRows)
      }
    }).catch((error) => {
      if (!controller.signal.aborted) setDictionaryNotice(error instanceof Error ? error.message : String(error))
    })
    return () => controller.abort()
  }, [])

  async function markBlindspot(item: Blindspot, mastered: boolean) {
    await recordLearningEvents([{
      id: crypto.randomUUID(),
      item: {
        id: item.knowledge_item_id,
        type: item.type,
        canonical_key: item.canonical_key,
        lemma: item.lemma,
        reading: item.reading,
        grammar_pattern: item.grammar_pattern,
      },
      event_type: mastered ? 'mark_mastered' : 'mark_unknown',
      occurred_at: Date.now() / 1000,
      context: { source: 'blindspot-panel' },
    }])
    setBlindspots(await loadBlindspots())
  }

  const filteredLexemes = lexemes.filter((item) =>
    !query || item.lemma.includes(query) || item.reading.includes(query) || item.firstKana === query,
  )

  return (
    <main className="app-page study-page">
      <header className="page-heading"><div><span>学习资料</span><h1>个人词库</h1><p>查找已保存的词义，管理系统识别出的知识盲区。</p></div></header>
      <section className="page-surface">
      <div className="data-tools">
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="按词形、读音或首个片假名查找" />
        <label className="button small file-button"><input type="file" accept=".zip,application/zip" onChange={(event) => void importDictionary(event.target.files?.[0] ?? null)} />{importingDictionary ? '导入中…' : '导入日中词典'}</label>
        <button className="button small" onClick={() => downloadJson(`冰读个人词库-${new Date().toISOString().slice(0, 10)}.json`, { format: 'nichidoku-lexicon-v1', exportedAt: new Date().toISOString(), lexemes })}>导出共享</button>
      </div>
      {dictionaryNotice && <p className="muted">{dictionaryNotice}</p>}
      <section className="blindspot-section">
        <h3>知识盲区</h3>
        <p className="muted">根据查词、句意和语法求助记录生成；停留时间不会直接降低掌握度。</p>
        <div className="blindspot-list">{blindspots.length ? blindspots.map((item) => <article key={item.knowledge_item_id}>
          <div><strong lang="ja">{item.grammar_pattern || item.lemma || item.canonical_key}</strong><span>掌握状态 {Math.round(item.mastery * 100)}%</span></div>
          <p>{item.reasons.join('；') || '证据仍少'}</p>
          <div className="blindspot-actions"><button className="button small" onClick={() => void markBlindspot(item, false)}>仍不认识</button><button className="button small" onClick={() => void markBlindspot(item, true)}>已经掌握</button></div>
        </article>) : <p className="muted">目前没有已识别的知识盲区。</p>}</div>
      </section>
      <div className="data-list">{filteredLexemes.length ? filteredLexemes.map((item) => <article key={item.key}>
        <div><strong lang="ja">{item.lemma}</strong><span>{toHiragana(item.reading)} · {item.part_of_speech}</span></div>
        <p>{item.senses_zh.join('；')}</p><small>{item.firstKana} · {item.source}</small>
      </article>) : <p className="muted">还没有匹配的词条。</p>}</div>
      </section>
    </main>
  )
}

