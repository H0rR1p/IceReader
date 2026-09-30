import { useCallback, useEffect, useState } from 'react'
import {
  acceptCardCandidate, loadCardCandidates, loadCards, loadCardSummary, loadSavedCardViews, rejectCardCandidate,
  saveCardView, updateCardStatuses, updateCardTags,
} from '../../api'
import type { CardCandidate, CardSummary, SavedCardView, StudyCard } from '../../api'
import { toHiragana } from '../../text'
import { Modal } from '../settings/Dialogs'

export default function CardCenterDialog({ onClose, onNotice }: { onClose: () => void; onNotice: (value: string) => void }) {
  const [tab, setTab] = useState<'inbox' | 'cards'>('inbox')
  const [candidates, setCandidates] = useState<CardCandidate[]>([])
  const [cards, setCards] = useState<StudyCard[]>([])
  const [views, setViews] = useState<SavedCardView[]>([])
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [selected, setSelected] = useState(new Set<string>())
  const [loading, setLoading] = useState(true)
  const [summary, setSummary] = useState<CardSummary | null>(null)

  const reload = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    try {
      const [nextCandidates, nextCards, nextViews, nextSummary] = await Promise.all([
        loadCardCandidates(signal), loadCards({ q, status }, signal), loadSavedCardViews(signal), loadCardSummary(signal),
      ])
      setCandidates(nextCandidates); setCards(nextCards); setViews(nextViews)
      setSummary(nextSummary)
    } finally { setLoading(false) }
  }, [q, status])

  useEffect(() => {
    const controller = new AbortController()
    const timer = window.setTimeout(() => void reload(controller.signal).catch((error) => {
      if (!controller.signal.aborted) onNotice(error instanceof Error ? error.message : String(error))
    }), 180)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [onNotice, reload])

  async function handleCandidate(candidate: CardCandidate, accept: boolean) {
    if (accept) await acceptCardCandidate(candidate.id); else await rejectCardCandidate(candidate.id)
    await reload(); onNotice(accept ? '已加入今日复习队列。' : '已忽略候选卡。')
  }
  async function bulk(nextStatus: 'active' | 'suspended' | 'archived') {
    const result = await updateCardStatuses([...selected], nextStatus)
    setSelected(new Set()); await reload(); onNotice(`已更新 ${result.updated} 张卡片，可通过操作记录撤销。`)
  }
  async function createView() {
    const name = window.prompt('保存视图名称')?.trim()
    if (!name) return
    await saveCardView(name, { q, status }); await reload(); onNotice('筛选视图已保存。')
  }
  async function addTag() {
    const tag = window.prompt('为所选卡片添加标签')?.trim()
    if (!tag) return
    const result = await updateCardTags([...selected], tag)
    await reload(); onNotice(`已为 ${result.updated} 张卡片添加标签。`)
  }

  return <Modal title="卡片中心" onClose={onClose}>
    {summary && <div className="card-workload"><span><strong>{summary.due_now}</strong> 今日到期</span><span><strong>{summary.due_7_days}</strong> 未来 7 天</span><span><strong>{summary.due_30_days}</strong> 未来 30 天</span><small>每日新卡建议上限 {summary.daily_new_limit}</small></div>}
    <div className="card-center-tabs"><button className={tab === 'inbox' ? 'active' : ''} onClick={() => setTab('inbox')}>收件箱 <small>{candidates.length}</small></button><button className={tab === 'cards' ? 'active' : ''} onClick={() => setTab('cards')}>全部卡片 <small>{cards.length}</small></button></div>
    {tab === 'cards' && <>
      <div className="card-filter-bar">
        <input value={q} onChange={(event) => setQ(event.target.value)} placeholder="搜索词语、读音、释义、原句或书名" />
        <select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部状态</option><option value="active">学习中</option><option value="suspended">已暂停</option><option value="leech">难卡</option><option value="archived">已封存</option></select>
        <button className="button small" onClick={() => void createView()}>保存视图</button>
      </div>
      {!!views.length && <div className="saved-view-chips">{views.map((view) => <button key={view.id} onClick={() => { setQ(view.query.q ?? ''); setStatus(view.query.status ?? '') }}>{view.name}</button>)}</div>}
      {!!selected.size && <div className="bulk-card-actions"><span>已选 {selected.size} 项</span><button onClick={() => void addTag()}>加标签</button><button onClick={() => void bulk('active')}>恢复</button><button onClick={() => void bulk('suspended')}>暂停</button><button onClick={() => void bulk('archived')}>封存</button></div>}
    </>}
    {loading ? <p className="muted">正在读取卡片…</p> : tab === 'inbox'
      ? <div className="candidate-list">{candidates.length ? candidates.map((item) => <article key={item.id}><div><strong lang="ja">{item.lemma}</strong><span>{toHiragana(item.reading)}</span></div><p>{item.gloss || '等待补充本句义项'}</p><blockquote lang="ja">{item.sentence}</blockquote><small>{item.book_title}</small><div><button className="button primary small" onClick={() => void handleCandidate(item, true)}>加入学习</button><button className="button small" onClick={() => void handleCandidate(item, false)}>忽略</button></div></article>) : <p className="muted">阅读时可从词典卡片加入候选词卡。</p>}</div>
      : <div className="study-card-table">{cards.length ? cards.map((card) => <article key={card.id} className={selected.has(card.id) ? 'selected' : ''}><input type="checkbox" checked={selected.has(card.id)} onChange={() => setSelected((current) => { const next = new Set(current); if (next.has(card.id)) next.delete(card.id); else next.add(card.id); return next })} /><div><strong lang="ja">{card.lemma}</strong><small>{toHiragana(card.reading)} · {card.book_title || '手动添加'}</small>{!!card.tags.length && <div className="card-tags">{card.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>}<p>{card.gloss}</p><blockquote lang="ja">{card.sentence}</blockquote></div><div className="card-memory"><span>{card.status === 'active' ? '学习中' : card.status === 'leech' ? '难卡' : card.status === 'suspended' ? '已暂停' : '已封存'}</span><small>稳定度 {card.stability.toFixed(1)} 天</small><small>复习 {card.reps} · 遗忘 {card.lapses}</small></div></article>) : <p className="muted">没有匹配的卡片。</p>}</div>}
  </Modal>
}
