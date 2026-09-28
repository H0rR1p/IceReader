import { useEffect, useRef, useState } from 'react'
import { cancelVoiceJob, importYomitanDictionary, loadVoiceJob, startVoiceJob } from '../../api'
import { loadStudyData } from '../../db'
import type { Lexeme, StudyCard, VoiceJob } from '../../types'
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
  const [cards, setCards] = useState<StudyCard[]>([])
  const [tab, setTab] = useState<'dictionary' | 'cards'>('dictionary')
  const [query, setQuery] = useState('')
  const [dictionaryNotice, setDictionaryNotice] = useState('')
  const [importingDictionary, setImportingDictionary] = useState(false)
  const [cardVoice, setCardVoice] = useState<{ cardId: string; job: VoiceJob } | null>(null)
  const cardVoiceAbortRef = useRef<AbortController | null>(null)
  const cardVoiceJobIdRef = useRef<string | null>(null)
  const cardAudioRef = useRef<HTMLAudioElement | null>(null)

  function playCardAudio(url: string) {
    cardAudioRef.current?.pause()
    const audio = new Audio(url)
    cardAudioRef.current = audio
    void audio.play()
  }

  async function voiceCard(card: StudyCard, force = false) {
    if (!force && cardVoice?.cardId === card.id && cardVoice.job.status === 'complete' && cardVoice.job.audioUrl) {
      playCardAudio(cardVoice.job.audioUrl)
      return
    }
    cardVoiceAbortRef.current?.abort()
    const previousJobId = cardVoiceJobIdRef.current
    if (previousJobId) void cancelVoiceJob(previousJobId).catch(() => undefined)
    try {
      let job = await startVoiceJob(card.surface, force)
      setCardVoice({ cardId: card.id, job })
      cardVoiceJobIdRef.current = job.status === 'complete' ? null : job.id
      if (job.status === 'complete' && job.audioUrl) {
        playCardAudio(job.audioUrl)
        return
      }
      const controller = new AbortController()
      cardVoiceAbortRef.current = controller
      while (!controller.signal.aborted && (job.status === 'queued' || job.status === 'running')) {
        await new Promise((resolve) => window.setTimeout(resolve, 500))
        job = await loadVoiceJob(job.id, controller.signal)
        setCardVoice({ cardId: card.id, job })
      }
      if (job.status === 'complete' && job.audioUrl) {
        cardVoiceJobIdRef.current = null
        playCardAudio(job.audioUrl)
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return
      setCardVoice({ cardId: card.id, job: { id: '', status: 'failed', message: error instanceof Error ? error.message : String(error), cached: false } })
      cardVoiceJobIdRef.current = null
    }
  }

  async function importDictionary(file: File | null) {
    if (!file) return
    setImportingDictionary(true); setDictionaryNotice('')
    try {
      const result = await importYomitanDictionary(file)
      setDictionaryNotice(`已导入 ${result.source}：${result.entries.toLocaleString()} 个词条。`)
    } catch (error) {
      setDictionaryNotice(error instanceof Error ? error.message : String(error))
    } finally { setImportingDictionary(false) }
  }

  useEffect(() => {
    const controller = new AbortController()
    void loadStudyData(controller.signal).then((snapshot) => {
      if (controller.signal.aborted) return
      setLexemes([...snapshot.lexemes].sort((a, b) => a.reading.localeCompare(b.reading, 'ja')))
      setCards([...snapshot.cards].sort((a, b) => b.createdAt - a.createdAt))
    }).catch((error) => {
      if (!controller.signal.aborted) setDictionaryNotice(error instanceof Error ? error.message : String(error))
    })
    return () => controller.abort()
  }, [])

  useEffect(() => () => {
    cardVoiceAbortRef.current?.abort()
    cardAudioRef.current?.pause()
    if (cardVoiceJobIdRef.current) void cancelVoiceJob(cardVoiceJobIdRef.current).catch(() => undefined)
  }, [])

  const filteredLexemes = lexemes.filter((item) =>
    !query || item.lemma.includes(query) || item.reading.includes(query) || item.firstKana === query,
  )

  return (
    <Modal title="个人词库与上下文词卡" onClose={onClose}>
      <div className="tab-row">
        <button className={tab === 'dictionary' ? 'active' : ''} onClick={() => setTab('dictionary')}>日中词库 {lexemes.length}</button>
        <button className={tab === 'cards' ? 'active' : ''} onClick={() => setTab('cards')}>上下文词卡 {cards.length}</button>
      </div>
      {tab === 'dictionary' ? <>
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
      </> : <>
        <div className="data-tools">
          <p>词卡保留书籍、章节、原句和当前语境义。</p>
          <button className="button small" onClick={() => downloadJson(`冰读上下文词卡-${new Date().toISOString().slice(0, 10)}.json`, { format: 'nichidoku-cards-v1', exportedAt: new Date().toISOString(), cards })}>导出词卡</button>
        </div>
        <div className="data-list cards">{cards.length ? cards.map((card) => <article key={card.id}>
          <div className="card-title-row"><div className="card-word"><strong lang="ja">{card.surface}</strong><span>{toHiragana(card.reading)}</span></div><div className="card-voice-actions"><button className="button small" disabled={cardVoice?.cardId === card.id && (cardVoice.job.status === 'queued' || cardVoice.job.status === 'running')} onClick={() => void voiceCard(card)}>{cardVoice?.cardId === card.id && (cardVoice.job.status === 'queued' || cardVoice.job.status === 'running') ? '配音中…' : cardVoice?.cardId === card.id && cardVoice.job.status === 'complete' ? '再次播放' : '播放读音'}</button>{cardVoice?.cardId === card.id && cardVoice.job.status === 'complete' && <button className="text-button" onClick={() => void voiceCard(card, true)}>重新生成</button>}</div></div><p>{card.glossZh}</p>
          <blockquote lang="ja">{card.sentence}</blockquote><small>{card.sourceLabel}</small>
          {cardVoice?.cardId === card.id && cardVoice.job.status === 'failed' && <small className="voice-status failed">{cardVoice.job.message}</small>}
        </article>) : <p className="muted">还没有词卡。阅读时点击词语即可收藏。</p>}</div>
      </>}
    </Modal>
  )
}

