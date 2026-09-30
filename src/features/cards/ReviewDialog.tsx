import { useEffect, useState } from 'react'
import { loadDueCards, reviewCard } from '../../api'
import type { StudyCard } from '../../api'
import { toHiragana } from '../../text'
import { Modal } from '../settings/Dialogs'

export default function ReviewDialog({ onClose, onNotice }: { onClose: () => void; onNotice: (value: string) => void }) {
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
  return <Modal title="今日复习" onClose={onClose}>
    {loading ? <p className="muted">正在整理今日队列…</p> : !card ? <div className="review-empty"><strong>今天的到期卡已完成</strong><p>候选卡不会自动进入复习，先在卡片中心确认即可。</p></div> : <div className="review-card">
      <small>{queue.length} 张待复习 · {card.book_title}</small>
      <blockquote lang="ja">{card.sentence}</blockquote>
      <h2 lang="ja">{card.lemma}</h2>
      {revealed ? <><p className="reading">{toHiragana(card.reading)}</p><div className="review-answer">{card.gloss}</div><div className="review-ratings"><button onClick={() => void answer('again')}>忘记</button><button onClick={() => void answer('hard')}>困难</button><button onClick={() => void answer('good')}>记得</button><button onClick={() => void answer('easy')}>简单</button></div></> : <button className="button primary full" onClick={() => setRevealed(true)}>显示答案</button>}
    </div>}
  </Modal>
}
