import { useEffect, useState } from 'react'
import { loadDueCards, reviewCard } from '../../api'
import type { StudyCard } from '../../api'
import { toHiragana } from '../../text'

export default function ReviewPage({ onNotice, onManageCards, onSourceRead }: { onNotice: (value: string) => void; onManageCards: () => void; onSourceRead?: (bookId: string, chapterId: string, start: number) => void }) {
  const [queue, setQueue] = useState<StudyCard[]>([])
  const [revealed, setRevealed] = useState(false)
  const [loading, setLoading] = useState(true)
  const [answering, setAnswering] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    void loadDueCards(controller.signal).then(setQueue).catch((error) => {
      if (!controller.signal.aborted) onNotice(error instanceof Error ? error.message : String(error))
    }).finally(() => setLoading(false))
    return () => controller.abort()
  }, [onNotice])
  const card = queue[0]
  async function answer(rating: 'again' | 'hard' | 'good' | 'easy') {
    if (!card || answering) return
    setAnswering(true); setError('')
    try {
      await reviewCard(card.id, rating)
      setQueue((current) => current.slice(1)); setRevealed(false)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) } finally { setAnswering(false) }
  }
  const completed = !loading && !card
  return <main className="app-page review-page">
    <header className="page-heading"><div><span>间隔复习</span><h1>今日复习</h1><p>{loading ? '正在整理今日队列…' : completed ? '今天的到期卡已经完成。' : `还剩 ${queue.length} 张到期卡片。`}</p></div><button className="button" onClick={onManageCards}>管理卡片</button></header>
    {error && <p role="alert" className="error-message">{error}</p>}
    <section className="page-surface review-stage" aria-busy={answering}>
    {loading ? <p className="muted">正在整理今日队列…</p> : !card ? <div className="review-empty"><strong>今天的到期卡已完成</strong><p>候选卡不会自动进入复习，请先在卡片页面确认。</p><button className="button primary" onClick={onManageCards}>查看学习卡片</button></div> : <div className="review-card">
      <small>{queue.length} 张待复习 · {card.book_title}</small>
      {card.source?.kind === 'grammar' && <p>{card.card_template === 'form-restoration' ? '词形恢复：根据上下文写出空缺处的完整活用' : '结构识别：说明所选结构的作用'}</p>}
      <blockquote lang="ja">{card.source?.question || card.sentence}</blockquote>
      <h2 lang="ja">{card.source?.kind === 'grammar' ? card.card_template === 'form-restoration' ? card.source.base_form : card.source.quote : card.lemma}</h2>
      {revealed ? <><p className="reading">{toHiragana(card.reading)}</p><div className="review-answer">{card.source?.answer || card.gloss}</div>{card.source?.kind === 'grammar' && <><p>{card.source.explanation}</p><p lang="ja">{card.source.derivation.join(' → ')}</p><small>来源规则 {card.source.rules_version} · 句子版本 {card.source.analysis_revision}</small><details><summary>创建时原始例句</summary><blockquote lang="ja">{card.source.original_sentence}</blockquote>{onSourceRead && <button className="button small" onClick={() => onSourceRead(card.book_id, card.chapter_id, card.source!.chapter_anchor)}>定位来源原文</button>}</details></>}<div className="review-ratings"><button disabled={answering} onClick={() => void answer('again')}>忘记</button><button disabled={answering} onClick={() => void answer('hard')}>困难</button><button disabled={answering} onClick={() => void answer('good')}>记得</button><button disabled={answering} onClick={() => void answer('easy')}>简单</button></div></> : <button className="button primary full" onClick={() => setRevealed(true)}>显示答案</button>}
    </div>}
    </section>
  </main>
}
