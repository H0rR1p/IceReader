import { useEffect, useRef, useState, type ReactNode } from 'react'
import { parseResponse } from '../../api'
import { db, loadChapterData, restoreProjectIndex } from '../../db'
import type { Book, Chapter } from '../../types'
import TaskProgress, { type DurableJob } from '../jobs/TaskProgress'

export default function ResegmentationControls({ book, chapter, children, onPauseTranslation, onReload, onNotice }: {
  book: Book; chapter: Chapter | null; children?: ReactNode; onPauseTranslation: () => void
  onReload: (chapter: Chapter, sentenceId?: string) => void; onNotice: (message: string) => void
}) {
  const [job, setJob] = useState<DurableJob | null>(null)
  const [busy, setBusy] = useState(false)
  const finished = useRef(new Set<string>())
  async function reload() {
    if (!chapter) return
    await restoreProjectIndex()
    const snapshot = await loadChapterData(chapter.id)
    const currentBook = await db.books.get(book.id)
    onReload(snapshot.chapter, currentBook?.currentSentenceId)
  }
  useEffect(() => {
    let canceled = false
    setJob(null); finished.current.clear()
    void fetch(`/api/jobs?book_id=${encodeURIComponent(book.id)}`).then(parseResponse<DurableJob[]>).then((rows) => {
      if (!canceled) setJob(rows.find((row) => row.kind === 'resegmentation-v1') ?? null)
    }).catch((error) => { if (!canceled) onNotice(String(error)) })
    return () => { canceled = true }
  }, [book.id, onNotice])
  useEffect(() => {
    if (!job || !['running','queued'].includes(job.status)) return
    const abort = new AbortController()
    const timer = window.setInterval(() => {
      void fetch(`/api/jobs/${encodeURIComponent(job.id)}`, { signal: abort.signal }).then(parseResponse<DurableJob>).then(async (next) => {
        if (abort.signal.aborted) return
        setJob(next)
        if (!['running','queued'].includes(next.status) && !finished.current.has(next.id)) {
          finished.current.add(next.id)
          await reload()
        }
      }).catch((error) => { if (!abort.signal.aborted) onNotice(String(error)) })
    }, 1000)
    return () => { window.clearInterval(timer); abort.abort() }
  }, [job?.id, job?.status, chapter?.id])
  async function start(scope: 'book' | 'chapter') {
    if (busy || scope === 'chapter' && !chapter) return
    setBusy(true)
    onPauseTranslation()
    try {
      const result = await fetch(`/api/books/${encodeURIComponent(book.id)}/resegment`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope, ...(scope === 'chapter' && chapter ? { chapter_id: chapter.id, expected_revision: chapter.analysis_revision ?? 1 } : {}) }) }).then(parseResponse<{ job: DurableJob }>)
      setJob(result.job)
    } catch (error) { onNotice(String(error)) } finally { setBusy(false) }
  }
  async function action(actionName: 'cancel' | 'resume') {
    if (!job || busy) return
    setBusy(true)
    try { finished.current.delete(job.id); setJob(await fetch(`/api/jobs/${encodeURIComponent(job.id)}/${actionName}`, { method: 'POST' }).then(parseResponse<DurableJob>)) } catch (error) { onNotice(String(error)) } finally { setBusy(false) }
  }
  async function restore() {
    const generation = job?.result.chapters?.find((row) => row.chapter_id === chapter?.id)
    if (!generation || !chapter) return
    setBusy(true)
    try {
      await fetch(`/api/segmentation-generations/${generation.generation_id}/restore`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expected_revision: chapter.analysis_revision ?? generation.target_revision }) }).then(parseResponse)
      await reload(); onNotice('已恢复上次切分前的结果，学习历史保留。')
    } catch (error) { onNotice(String(error)) } finally { setBusy(false) }
  }
  return <section aria-label="重新切分" className="resegmentation-controls">
    <div className="book-background-actions segmentation-actions">{children}<button className="button small" disabled={!chapter || busy || ['running','queued'].includes(job?.status ?? '')} onClick={() => void start('chapter')}>重新切分本章</button><button className="button small" disabled={busy || ['running','queued'].includes(job?.status ?? '')} onClick={() => void start('book')}>重新切分全书</button></div>
    {job && <TaskProgress job={job} busy={busy} onAction={(next) => void action(next)} />}
    {job?.result.chapters?.some((row) => row.chapter_id === chapter?.id && row.generation_id === chapter?.active_generation) && <button className="button small" disabled={busy} onClick={() => void restore()}>恢复本章重切前结果</button>}
  </section>
}
