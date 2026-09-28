import { useEffect, useState } from 'react'
import { importYomitanDictionary } from '../../api'
import { loadStudyData } from '../../db'
import type { Lexeme } from '../../types'
import { toHiragana } from '../../text'
import { Modal } from '../settings/Dialogs'

function downloadJson(filename: string, value: unknown) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

export default function StudyDataDialog({ onClose }: { onClose: () => void }) {
  const [lexemes, setLexemes] = useState<Lexeme[]>([])
  const [query, setQuery] = useState('')
  const [dictionaryNotice, setDictionaryNotice] = useState('')
  const [importingDictionary, setImportingDictionary] = useState(false)

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
    void loadStudyData(controller.signal).then((snapshot) => {
      if (!controller.signal.aborted) {
        setLexemes([...snapshot.lexemes].sort((a, b) => a.reading.localeCompare(b.reading, 'ja')))
      }
    }).catch((error) => {
      if (!controller.signal.aborted) setDictionaryNotice(error instanceof Error ? error.message : String(error))
    })
    return () => controller.abort()
  }, [])

  const filteredLexemes = lexemes.filter((item) =>
    !query || item.lemma.includes(query) || item.reading.includes(query) || item.firstKana === query,
  )

  return (
    <Modal title="个人词库" onClose={onClose}>
      <div className="data-tools">
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="按词形、读音或首个片假名查找" />
        <label className="button small file-button"><input type="file" accept=".zip,application/zip" onChange={(event) => void importDictionary(event.target.files?.[0] ?? null)} />{importingDictionary ? '导入中…' : '导入日中词典'}</label>
        <button className="button small" onClick={() => downloadJson(`冰读个人词库-${new Date().toISOString().slice(0, 10)}.json`, { format: 'nichidoku-lexicon-v1', exportedAt: new Date().toISOString(), lexemes })}>导出共享</button>
      </div>
      {dictionaryNotice && <p className="muted">{dictionaryNotice}</p>}
      <div className="data-list">{filteredLexemes.length ? filteredLexemes.map((item) => <article key={item.key}>
        <div><strong lang="ja">{item.lemma}</strong><span>{toHiragana(item.reading)} · {item.part_of_speech}</span></div>
        <p>{item.senses_zh.join('；')}</p><small>{item.firstKana} · {item.source}</small>
      </article>) : <p className="muted">还没有匹配的词条。</p>}</div>
    </Modal>
  )
}
