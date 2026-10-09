import { useCallback, useEffect, useState } from 'react'
import {
  acceptCardCandidate, deleteCardView, loadCardCandidates, loadCardPreferences, loadCards, loadCardSummary,
  loadCardTags, loadSavedCardViews, mergeCards, rejectCardCandidate, saveCardPreferences, saveCardView,
  undoCardAction, updateCard, updateCardStatuses, updateCardTags,
} from '../../api'
import type { CardCandidate, CardPreferences, CardSummary, CardTagSummary, SavedCardView, StudyCard } from '../../api'
import { toHiragana } from '../../text'

type CardSort = 'due' | 'updated' | 'difficulty' | 'alphabetical'

export default function CardCenterPage({ onNotice, onStartReview, onSourceRead }: { onNotice: (value: string) => void; onStartReview: () => void; onSourceRead?: (bookId: string, chapterId: string, start: number) => void }) {
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
  const [tags, setTags] = useState<CardTagSummary[]>([])
  const [preferences, setPreferences] = useState<CardPreferences>({ daily_new_limit: 20, daily_review_limit: 200 })
  const [offset, setOffset] = useState(0)
  const [total, setTotal] = useState(0)
  const [editing, setEditing] = useState<StudyCard | null>(null)
  const [textAction, setTextAction] = useState<'view' | 'add-tag' | 'remove-tag' | null>(null)
  const [undoId, setUndoId] = useState('')
  const [busyCandidate, setBusyCandidate] = useState('')
  const [error, setError] = useState('')
  const pageSize = 40

  const reload = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    try {
      const [nextCandidates, cardPage, nextViews, nextSummary, nextTags, nextPreferences] = await Promise.all([
        loadCardCandidates(signal), loadCards({ q, status, due, limit: pageSize, offset }, signal), loadSavedCardViews(signal), loadCardSummary(signal),
        loadCardTags(signal), loadCardPreferences(signal),
      ])
      setCandidates(nextCandidates); setCards(cardPage.items); setTotal(cardPage.total); setViews(nextViews)
      setSummary(nextSummary); setTags(nextTags); setPreferences(nextPreferences)
    } finally { setLoading(false) }
  }, [q, status, due, offset])

  useEffect(() => { setOffset(0) }, [q, status, due])

  useEffect(() => {
    const controller = new AbortController()
    const timer = window.setTimeout(() => void reload(controller.signal).catch((error) => {
      if (!controller.signal.aborted) onNotice(error instanceof Error ? error.message : String(error))
    }), 180)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [onNotice, reload])

  async function handleCandidate(candidate: CardCandidate, accept: boolean) {
    if (busyCandidate) return
    setBusyCandidate(candidate.id); setError('')
    try {
      if (accept) await acceptCardCandidate(candidate.id); else await rejectCardCandidate(candidate.id)
      await reload(); onNotice(accept ? '已加入今日复习队列。' : '已忽略候选卡。')
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) } finally { setBusyCandidate('') }
  }
  async function bulk(nextStatus: 'active' | 'suspended' | 'archived') {
    const result = await updateCardStatuses([...selected], nextStatus)
    setUndoId(result.undo_id); setSelected(new Set()); await reload(); onNotice(`已更新 ${result.updated} 张卡片。`)
  }
  async function createView(name: string) {
    if (!name) return
    await saveCardView(name, { q, status, due }); await reload(); onNotice('筛选视图已保存。')
  }
  async function addTag(tag: string) {
    if (!tag) return
    const result = await updateCardTags([...selected], tag)
    setUndoId(result.undo_id); await reload(); onNotice(`已为 ${result.updated} 张卡片添加标签。`)
  }

  async function removeTag(tag: string) {
    const result = await updateCardTags([...selected], tag, true)
    setUndoId(result.undo_id); await reload(); onNotice(`已从 ${result.updated} 张卡片移除标签。`)
  }
  async function undo() {
    if (!undoId) return
    await undoCardAction(undoId); setUndoId(''); await reload(); onNotice('上一次卡片操作已撤销。')
  }
  async function mergeSelected() {
    const ids = [...selected]
    if (ids.length < 2) return
    const result = await mergeCards(ids[0], ids.slice(1))
    setSelected(new Set()); await reload(); onNotice(`已合并 ${result.merged} 张重复卡片，复习记录已保留。`)
  }
  async function saveEdit(fields: Pick<StudyCard, 'lemma' | 'reading' | 'gloss' | 'sentence'>) {
    if (!editing) return
    const result = await updateCard(editing.id, fields)
    setUndoId(result.undo_id); setEditing(null); await reload(); onNotice('卡片内容已保存。')
  }
  async function saveLimits() {
    setPreferences(await saveCardPreferences(preferences)); await reload(); onNotice('每日学习上限已保存。')
  }

  const visibleCards = [...cards].sort((a, b) => {
    if (sort === 'updated') return b.updated_at - a.updated_at
    if (sort === 'difficulty') return b.difficulty - a.difficulty
    if (sort === 'alphabetical') return a.reading.localeCompare(b.reading, 'ja')
    return a.due_at - b.due_at
  })

  return <main className="app-page cards-page">
    <header className="page-heading"><div><span>记忆管理</span><h1>词语与语法卡片</h1><p>先确认阅读中收集的候选卡，再用筛选和标签整理已进入学习的卡片。</p></div><button className="button primary" onClick={onStartReview} disabled={!summary?.due_now}>开始今日复习{summary?.due_now ? ` · ${Math.min(summary.due_now, summary.daily_review_limit)}` : ''}</button></header>
    {error && <p role="alert" className="error-message">{error}</p>}
    {undoId && <div className="card-undo-bar" role="status"><span>操作已完成</span><button onClick={() => void undo()}>撤销</button><button aria-label="关闭撤销提示" onClick={() => setUndoId('')}>×</button></div>}
    {summary && <div className="card-summary-grid"><button className={due === 'today' ? 'active' : ''} onClick={() => { setTab('cards'); setDue(due === 'today' ? '' : 'today') }}><strong>{summary.due_now}</strong><span>今日到期</span></button><div><strong>{summary.due_7_days}</strong><span>未来 7 天</span></div><div><strong>{summary.active}</strong><span>学习中</span></div><button className={tab === 'inbox' ? 'active' : ''} onClick={() => setTab('inbox')}><strong>{summary.candidates}</strong><span>待确认</span></button></div>}
    <section className="page-surface">
    <div className="card-center-tabs"><button className={tab === 'inbox' ? 'active' : ''} onClick={() => setTab('inbox')}>待确认 <small>{candidates.length}</small></button><button className={tab === 'cards' ? 'active' : ''} onClick={() => setTab('cards')}>卡片库 <small>{cards.length}</small></button></div>
    {tab === 'cards' && <>
      <div className="card-filter-bar">
        <input value={q} onChange={(event) => setQ(event.target.value)} placeholder="搜索词语、读音、释义、原句或书名" />
        <select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部状态</option><option value="active">学习中</option><option value="suspended">已暂停</option><option value="leech">难卡</option><option value="archived">已封存</option></select>
        <select value={sort} onChange={(event) => setSort(event.target.value as CardSort)}><option value="due">按到期时间</option><option value="updated">最近更新</option><option value="difficulty">难度最高</option><option value="alphabetical">按读音</option></select>
        <button className="button small" onClick={() => setTextAction('view')}>保存视图</button>
      </div>
      <div className="filter-shortcuts"><button className={!status && !due ? 'active' : ''} onClick={() => { setStatus(''); setDue('') }}>全部</button><button className={due === 'today' ? 'active' : ''} onClick={() => setDue(due === 'today' ? '' : 'today')}>今日到期</button><button className={status === 'active' && !due ? 'active' : ''} onClick={() => { setStatus('active'); setDue('') }}>学习中</button><button className={status === 'leech' ? 'active' : ''} onClick={() => { setStatus('leech'); setDue('') }}>难卡</button><button className={status === 'suspended' ? 'active' : ''} onClick={() => { setStatus('suspended'); setDue('') }}>已暂停</button></div>
      {!!views.length && <div className="saved-view-chips"><span>已保存</span>{views.map((view) => <span className="saved-view" key={view.id}><button onClick={() => { setQ(view.query.q ?? ''); setStatus(view.query.status ?? ''); setDue(view.query.due ?? '') }}>{view.name}</button><button aria-label={`删除视图${view.name}`} onClick={() => void deleteCardView(view.id).then(() => reload())}>×</button></span>)}</div>}
      {!!tags.length && <div className="saved-view-chips"><span>标签</span>{tags.slice(0, 12).map((tag) => <button key={tag.name} onClick={() => setQ(tag.name)}>{tag.name} · {tag.count}</button>)}</div>}
      {!!selected.size && <div className="bulk-card-actions"><span>已选 {selected.size} 项</span><button onClick={() => setTextAction('add-tag')}>加标签</button><button onClick={() => setTextAction('remove-tag')}>移除标签</button>{selected.size > 1 && <button onClick={() => void mergeSelected()}>合并重复卡</button>}<button onClick={() => void bulk('active')}>恢复</button><button onClick={() => void bulk('suspended')}>暂停</button><button onClick={() => void bulk('archived')}>封存</button></div>}
    </>}
    {loading ? <p className="muted">正在读取卡片…</p> : tab === 'inbox'
      ? <div className="candidate-list">{candidates.length ? candidates.map((item) => <article key={item.id} data-template={item.card_template}><div><strong lang="ja">{item.lemma}</strong><span>{toHiragana(item.reading)}</span></div>{item.source?.kind === 'grammar' && <p>{item.card_template === 'form-restoration' ? '词形恢复' : '结构识别'} · 语法候选</p>}<p>{item.gloss || '等待补充本句义项'}</p><blockquote lang="ja">{item.source?.question || item.sentence}</blockquote>{item.source && <GrammarSourceDetails card={item} onSourceRead={onSourceRead} />}<small>{item.book_title}</small><div><button className="button primary small" disabled={Boolean(busyCandidate)} onClick={() => void handleCandidate(item, true)}>加入学习</button><button className="button small" disabled={Boolean(busyCandidate)} onClick={() => void handleCandidate(item, false)}>忽略</button></div></article>) : <p className="muted">阅读时可从词典或语法结构加入候选卡。</p>}</div>
      : <div className="study-card-table">{visibleCards.length ? visibleCards.map((card) => <article key={card.id} data-template={card.card_template} className={selected.has(card.id) ? 'selected' : ''}><input type="checkbox" aria-label={`选择${card.lemma}`} checked={selected.has(card.id)} onChange={() => setSelected((current) => { const next = new Set(current); if (next.has(card.id)) next.delete(card.id); else next.add(card.id); return next })} /><div><strong lang="ja">{card.lemma}</strong><small>{card.source?.kind === 'grammar' ? card.card_template === 'form-restoration' ? '词形恢复' : '结构识别' : toHiragana(card.reading)} · {card.book_title || '手动添加'}</small>{!!card.tags.length && <div className="card-tags">{card.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>}<p>{card.gloss}</p><blockquote lang="ja">{card.source?.question || card.sentence}</blockquote>{card.source && <GrammarSourceDetails card={card} onSourceRead={onSourceRead} />}</div><div className="card-memory"><span>{card.status === 'active' ? '学习中' : card.status === 'leech' ? '难卡' : card.status === 'suspended' ? '已暂停' : '已封存'}</span><small>稳定度 {card.stability.toFixed(1)} 天</small><small>复习 {card.reps} · 遗忘 {card.lapses}</small><button onClick={() => setEditing(card)}>编辑</button></div></article>) : <p className="muted">没有匹配的卡片。</p>}</div>}
    {tab === 'cards' && total > pageSize && <div className="card-pagination"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - pageSize))}>上一页</button><span>{Math.floor(offset / pageSize) + 1} / {Math.ceil(total / pageSize)} · 共 {total} 张</span><button disabled={offset + pageSize >= total} onClick={() => setOffset(offset + pageSize)}>下一页</button></div>}
    <details className="card-limit-settings"><summary>每日学习上限</summary><div><label>每日新卡<input type="number" min="0" max="200" value={preferences.daily_new_limit} onChange={(event) => setPreferences((current) => ({ ...current, daily_new_limit: Number(event.target.value) }))} /></label><label>每日复习<input type="number" min="1" max="1000" value={preferences.daily_review_limit} onChange={(event) => setPreferences((current) => ({ ...current, daily_review_limit: Number(event.target.value) }))} /></label><button className="button small" onClick={() => void saveLimits()}>保存上限</button>{summary && <small>今日已加入 {summary.new_today}/{summary.daily_new_limit} 张新卡</small>}</div></details>
    </section>
    {editing && <CardEditor card={editing} onClose={() => setEditing(null)} onSave={saveEdit} />}
    {textAction && <CardTextActionDialog action={textAction} onClose={() => setTextAction(null)} onSubmit={async (value) => {
      if (textAction === 'view') await createView(value)
      else if (textAction === 'add-tag') await addTag(value)
      else await removeTag(value)
      setTextAction(null)
    }} />}
  </main>
}

function GrammarSourceDetails({ card, onSourceRead }: { card: CardCandidate | StudyCard; onSourceRead?: (bookId: string, chapterId: string, start: number) => void }) {
  if (!card.source) return null
  return <details><summary>原始例句与来源</summary><blockquote lang="ja">{card.source.original_sentence}</blockquote><p>{card.source.explanation}</p><p lang="ja">{card.source.derivation.join(' → ')}</p><small>创建时句子版本 {card.source.analysis_revision} · 规则 {card.source.rules_version}</small>{onSourceRead && <button className="button small" onClick={() => onSourceRead(card.book_id, card.chapter_id, card.source!.chapter_anchor)}>定位来源原文</button>}</details>
}

function CardTextActionDialog({ action, onClose, onSubmit }: { action: 'view' | 'add-tag' | 'remove-tag'; onClose: () => void; onSubmit: (value: string) => Promise<void> }) {
  const [value, setValue] = useState('')
  const [saving, setSaving] = useState(false)
  const labels = action === 'view'
    ? { title: '保存筛选视图', field: '视图名称', button: '保存视图' }
    : action === 'add-tag'
      ? { title: '添加标签', field: '标签名称', button: '添加标签' }
      : { title: '移除标签', field: '要移除的标签', button: '移除标签' }
  const submit = () => {
    const normalized = value.trim()
    if (!normalized || saving) return
    setSaving(true)
    void onSubmit(normalized).catch(() => setSaving(false))
  }
  return <div className="dialog-backdrop"><section className="dialog card-text-dialog" role="dialog" aria-modal="true" aria-labelledby="card-text-action-title"><header><h2 id="card-text-action-title">{labels.title}</h2><button aria-label="关闭" onClick={onClose}>×</button></header><label className="field"><span>{labels.field}</span><input autoFocus value={value} maxLength={40} onChange={(event) => setValue(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') submit() }} /></label><footer><button className="button" onClick={onClose}>取消</button><button className="button primary" disabled={saving || !value.trim()} onClick={submit}>{saving ? '处理中…' : labels.button}</button></footer></section></div>
}

function CardEditor({ card, onClose, onSave }: { card: StudyCard; onClose: () => void; onSave: (fields: Pick<StudyCard, 'lemma' | 'reading' | 'gloss' | 'sentence'>) => Promise<void> }) {
  const [fields, setFields] = useState({ lemma: card.lemma, reading: card.reading, gloss: card.gloss, sentence: card.sentence })
  const [saving, setSaving] = useState(false)
  return <div className="dialog-backdrop"><section className="dialog card-editor" role="dialog" aria-modal="true" aria-labelledby="card-editor-title"><header><div><small>卡片详情</small><h2 id="card-editor-title">编辑词语卡片</h2></div><button aria-label="关闭" onClick={onClose}>×</button></header><label className="field"><span>词语</span><input value={fields.lemma} onChange={(event) => setFields({ ...fields, lemma: event.target.value })} /></label><label className="field"><span>读音</span><input value={fields.reading} onChange={(event) => setFields({ ...fields, reading: event.target.value })} /></label><label className="field"><span>释义</span><textarea value={fields.gloss} onChange={(event) => setFields({ ...fields, gloss: event.target.value })} /></label><label className="field"><span>原句</span><textarea lang="ja" value={fields.sentence} onChange={(event) => setFields({ ...fields, sentence: event.target.value })} /></label><footer><button className="button" onClick={onClose}>取消</button><button className="button primary" disabled={saving || !fields.lemma.trim()} onClick={() => { setSaving(true); void onSave(fields).finally(() => setSaving(false)) }}>{saving ? '保存中…' : '保存'}</button></footer></section></div>
}
