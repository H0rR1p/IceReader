import { useCallback, useEffect, useState } from 'react'
import {
  acceptCardCandidate, loadCardCandidates, loadCards, loadCardSummary, loadSavedCardViews, rejectCardCandidate,
  saveCardView, updateCardStatuses, updateCardTags,
} from '../../api'
import type { CardCandidate, CardSummary, SavedCardView, StudyCard } from '../../api'
import { toHiragana } from '../../text'

type CardSort = 'due' | 'updated' | 'difficulty' | 'alphabetical'

export default function CardCenterPage({ onNotice, onStartReview }: { onNotice: (value: string) => void; onStartReview: () => void }) {
  const [tab, setTab] = useState<'inbox' | 'cards'>('inbox')
  const [candidates, setCandidates] = useState<CardCandidate[]>([])
  const [cards, setCards] = useState<StudyCard[]>([])
  const [views, setViews] = useState<SavedCardView[]>([])
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [due, setDue] = useState('')
  const [sort, setSort] = useState<CardSort>('due')
  const [selected, setSelected] = useState(new Set<string>())
  const [loading, setLoading] = useState(true)
  const [summary, setSummary] = useState<CardSummary | null>(null)

  const reload = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    try {
      const [nextCandidates, nextCards, nextViews, nextSummary] = await Promise.all([
        loadCardCandidates(signal), loadCards({ q, status, due }, signal), loadSavedCardViews(signal), loadCardSummary(signal),
      ])
      setCandidates(nextCandidates); setCards(nextCards); setViews(nextViews)
      setSummary(nextSummary)
    } finally { setLoading(false) }
  }, [q, status, due])

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
    await saveCardView(name, { q, status, due }); await reload(); onNotice('筛选视图已保存。')
  }
  async function addTag() {
    const tag = window.prompt('为所选卡片添加标签')?.trim()
    if (!tag) return
    const result = await updateCardTags([...selected], tag)
    await reload(); onNotice(`已为 ${result.updated} 张卡片添加标签。`)
  }

  const visibleCards = [...cards].sort((a, b) => {
    if (sort === 'updated') return b.updated_at - a.updated_at
    if (sort === 'difficulty') return b.difficulty - a.difficulty
    if (sort === 'alphabetical') return a.reading.localeCompare(b.reading, 'ja')
    return a.due_at - b.due_at
  })

  return <main className="app-page cards-page">
    <header className="page-heading"><div><span>记忆管理</span><h1>词语卡片</h1><p>先确认阅读中收集的候选词，再用筛选和标签整理已进入学习的卡片。</p></div><button className="button primary" onClick={onStartReview} disabled={!summary?.due_now}>开始今日复习{summary?.due_now ? ` · ${summary.due_now}` : ''}</button></header>
    {summary && <div className="card-summary-grid"><button className={due === 'today' ? 'active' : ''} onClick={() => { setTab('cards'); setDue(due === 'today' ? '' : 'today') }}><strong>{summary.due_now}</strong><span>今日到期</span></button><div><strong>{summary.due_7_days}</strong><span>未来 7 天</span></div><div><strong>{summary.active}</strong><span>学习中</span></div><button className={tab === 'inbox' ? 'active' : ''} onClick={() => setTab('inbox')}><strong>{summary.candidates}</strong><span>待确认</span></button></div>}
    <section className="page-surface">
    <div className="card-center-tabs"><button className={tab === 'inbox' ? 'active' : ''} onClick={() => setTab('inbox')}>待确认 <small>{candidates.length}</small></button><button className={tab === 'cards' ? 'active' : ''} onClick={() => setTab('cards')}>卡片库 <small>{cards.length}</small></button></div>
    {tab === 'cards' && <>
      <div className="card-filter-bar">
        <input value={q} onChange={(event) => setQ(event.target.value)} placeholder="搜索词语、读音、释义、原句或书名" />
        <select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部状态</option><option value="active">学习中</option><option value="suspended">已暂停</option><option value="leech">难卡</option><option value="archived">已封存</option></select>
        <select value={sort} onChange={(event) => setSort(event.target.value as CardSort)}><option value="due">按到期时间</option><option value="updated">最近更新</option><option value="difficulty">难度最高</option><option value="alphabetical">按读音</option></select>
        <button className="button small" onClick={() => void createView()}>保存视图</button>
      </div>
      <div className="filter-shortcuts"><button className={!status && !due ? 'active' : ''} onClick={() => { setStatus(''); setDue('') }}>全部</button><button className={due === 'today' ? 'active' : ''} onClick={() => setDue(due === 'today' ? '' : 'today')}>今日到期</button><button className={status === 'active' && !due ? 'active' : ''} onClick={() => { setStatus('active'); setDue('') }}>学习中</button><button className={status === 'leech' ? 'active' : ''} onClick={() => { setStatus('leech'); setDue('') }}>难卡</button><button className={status === 'suspended' ? 'active' : ''} onClick={() => { setStatus('suspended'); setDue('') }}>已暂停</button></div>
      {!!views.length && <div className="saved-view-chips"><span>已保存</span>{views.map((view) => <button key={view.id} onClick={() => { setQ(view.query.q ?? ''); setStatus(view.query.status ?? ''); setDue(view.query.due ?? '') }}>{view.name}</button>)}</div>}
      {!!selected.size && <div className="bulk-card-actions"><span>已选 {selected.size} 项</span><button onClick={() => void addTag()}>加标签</button><button onClick={() => void bulk('active')}>恢复</button><button onClick={() => void bulk('suspended')}>暂停</button><button onClick={() => void bulk('archived')}>封存</button></div>}
    </>}
    {loading ? <p className="muted">正在读取卡片…</p> : tab === 'inbox'
      ? <div className="candidate-list">{candidates.length ? candidates.map((item) => <article key={item.id}><div><strong lang="ja">{item.lemma}</strong><span>{toHiragana(item.reading)}</span></div><p>{item.gloss || '等待补充本句义项'}</p><blockquote lang="ja">{item.sentence}</blockquote><small>{item.book_title}</small><div><button className="button primary small" onClick={() => void handleCandidate(item, true)}>加入学习</button><button className="button small" onClick={() => void handleCandidate(item, false)}>忽略</button></div></article>) : <p className="muted">阅读时可从词典卡片加入候选词卡。</p>}</div>
      : <div className="study-card-table">{visibleCards.length ? visibleCards.map((card) => <article key={card.id} className={selected.has(card.id) ? 'selected' : ''}><input type="checkbox" checked={selected.has(card.id)} onChange={() => setSelected((current) => { const next = new Set(current); if (next.has(card.id)) next.delete(card.id); else next.add(card.id); return next })} /><div><strong lang="ja">{card.lemma}</strong><small>{toHiragana(card.reading)} · {card.book_title || '手动添加'}</small>{!!card.tags.length && <div className="card-tags">{card.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>}<p>{card.gloss}</p><blockquote lang="ja">{card.sentence}</blockquote></div><div className="card-memory"><span>{card.status === 'active' ? '学习中' : card.status === 'leech' ? '难卡' : card.status === 'suspended' ? '已暂停' : '已封存'}</span><small>稳定度 {card.stability.toFixed(1)} 天</small><small>复习 {card.reps} · 遗忘 {card.lapses}</small></div></article>) : <p className="muted">没有匹配的卡片。</p>}</div>}
    </section>
  </main>
}
