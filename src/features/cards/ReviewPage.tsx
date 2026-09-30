import { useEffect, useState } from 'react'
import { loadDueCards, reviewCard } from '../../api'
import type { StudyCard } from '../../api'
import { toHiragana } from '../../text'

export default function ReviewPage({ onNotice, onManageCards }: { onNotice: (value: string) => void; onManageCards: () => void }) {
  const [queue, setQueue] = useState<StudyCard[]>([])
  const [revealed, setRevealed] = useState(false)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    const controller = new AbortController()
    void loadDueCards(controller.signal).then(setQueue).catch((error) => {
      if (!controller.signal.aborted) onNotice(error instanceof Error ? error.message : String(error))
    }).finally(() => setLoading(false))
    return () => controller.abort()
  }, [onNotice])
  const card = queue[0]
  async function answer(rating: 'again' | 'hard' | 'good' | 'easy') {
    if (!card) return
    await reviewCard(card.id, rating)
    setQueue((current) => current.slice(1)); setRevealed(false)
  }
  const completed = !loading && !card
  return <main className="app-page review-page">
    <header className="page-heading"><div><span>间隔复习</span><h1>今日复习</h1><p>{loading ? '正在整理今日队列…' : completed ? '今天的到期卡已经完成。' : `还剩 ${queue.length} 张到期卡片。`}</p></div><button className="button" onClick={onManageCards}>管理卡片</button></header>
    <section className="page-surface review-stage">
    {loading ? <p className="muted">正在整理今日队列…</p> : !card ? <div className="review-empty"><strong>今天的到期卡已完成</strong><p>候选卡不会自动进入复习，请先在词语卡片页面确认。</p><button className="button primary" onClick={onManageCards}>查看词语卡片</button></div> : <div className="review-card">
      <small>{queue.length} 张待复习 · {card.book_title}</small>
      <blockquote lang="ja">{card.sentence}</blockquote>
      <h2 lang="ja">{card.lemma}</h2>
      {revealed ? <><p className="reading">{toHiragana(card.reading)}</p><div className="review-answer">{card.gloss}</div><div className="review-ratings"><button onClick={() => void answer('again')}>忘记</button><button onClick={() => void answer('hard')}>困难</button><button onClick={() => void answer('good')}>记得</button><button onClick={() => void answer('easy')}>简单</button></div></> : <button className="button primary full" onClick={() => setRevealed(true)}>显示答案</button>}
    </div>}
    </section>
  </main>
}

