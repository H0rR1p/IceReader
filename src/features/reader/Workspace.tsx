import { useCallback, useEffect, useState } from 'react'
import { db, loadBookBookmarks, syncRecords } from '../../db'
import type { AnalyzeResponse, Book, Chapter, Sentence, SentenceBookmark, Token } from '../../types'
import type { BackgroundJob } from '../translation/pipeline'
import Reader from './Reader'
import ResegmentationControls from './ResegmentationControls'


export default function Workspace({ userId, book, activeChapter, loadingChapterId, onSelectChapter, onProcessChapter, onExplainSentence, backgroundJob, dataRevision, onBackgroundBook, onBackgroundChapter, onCancelBackground, onBookImageVisibility, onNotice }: {
  userId: string
  book: Book
  activeChapter: Chapter | null
  loadingChapterId: string | null
  onSelectChapter: (chapter: Chapter, sentenceId?: string) => void
  onProcessChapter: (chapter: Chapter) => void
  onExplainSentence: (sentence: Sentence, tokens: Token[], persist?: boolean, signal?: AbortSignal, annotationMode?: 'none' | 'grammar') => Promise<AnalyzeResponse>
  backgroundJob: BackgroundJob | null
  dataRevision: number
  onBackgroundBook: (kind: BackgroundJob['kind']) => void
  onBackgroundChapter: (kind: BackgroundJob['kind'], chapter: Chapter) => void
  onCancelBackground: () => void
  onBookImageVisibility: (book: Book, visible: boolean) => Promise<void>
  onNotice: (message: string) => void
}) {
  const [chapters, setChapters] = useState<Chapter[]>([])
  const [bookmarks, setBookmarks] = useState<SentenceBookmark[]>([])
  const [navMode, setNavMode] = useState<'chapters' | 'bookmarks'>('chapters')
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const showBookImages = book.showImages !== false
  const load = useCallback(async () => {
    setChapters(await db.chapters.where('bookId').equals(book.id).sortBy('order'))
  }, [book.id, activeChapter?.status, backgroundJob?.completed, dataRevision])
  useEffect(() => { void load() }, [load])
  useEffect(() => {
    const controller = new AbortController()
    void loadBookBookmarks(book.id, controller.signal)
      .then((rows) => { if (!controller.signal.aborted) setBookmarks(rows) })
      .catch((error) => { if (!controller.signal.aborted) onNotice(error instanceof Error ? error.message : String(error)) })
    return () => controller.abort()
  }, [book.id, activeChapter?.analysis_revision, dataRevision, onNotice])

  async function toggleBookmark(sentence: Sentence) {
    const existing = bookmarks.find((bookmark) => bookmark.sentenceId === sentence.id)
    if (existing) {
      await db.bookmarks.delete(existing.id)
      await syncRecords({}, { bookmarks: [existing.id] })
      setBookmarks((current) => current.filter((bookmark) => bookmark.id !== existing.id))
      return
    }
    if (!activeChapter) return
    const bookmark: SentenceBookmark = {
      id: sentence.id, bookId: book.id, chapterId: activeChapter.id, sentenceId: sentence.id,
      chapterTitle: activeChapter.title, chapterOrder: activeChapter.order,
      sentenceStart: sentence.start, text: sentence.original, createdAt: Date.now(),
    }
    await db.bookmarks.put(bookmark)
    await syncRecords({ bookmarks: [bookmark] })
    setBookmarks((current) => [...current, bookmark].sort((a, b) => a.chapterOrder - b.chapterOrder || a.sentenceStart - b.sentenceStart))
  }

  function openBookmark(bookmark: SentenceBookmark) {
    const target = chapters.find((chapter) => chapter.id === bookmark.chapterId)
    if (target) onSelectChapter(target, bookmark.sentenceId)
  }

  const activeChapterIndex = activeChapter ? chapters.findIndex((chapter) => chapter.id === activeChapter.id) : -1
  const previousChapter = activeChapterIndex > 0 ? chapters[activeChapterIndex - 1] : null
  const nextChapter = activeChapterIndex >= 0 && activeChapterIndex < chapters.length - 1 ? chapters[activeChapterIndex + 1] : null

  return (
    <div className={`workspace ${leftCollapsed ? 'left-collapsed' : ''}`}>
      <aside className="chapter-nav">
        <button className="sidebar-collapse left" type="button" aria-label={leftCollapsed ? '展开左侧栏' : '收起左侧栏'} title={leftCollapsed ? '展开左侧栏' : '收起左侧栏'} onClick={() => setLeftCollapsed((value) => !value)}>{leftCollapsed ? '›' : '‹'}</button>
        {!leftCollapsed && <div className="chapter-nav-content">
          <div className="book-heading"><small>正在阅读</small><h2>{book.title}</h2>{book.author && <p>{book.author}</p>}</div>
          <label className="reader-image-toggle">
            <input type="checkbox" checked={showBookImages} onChange={(event) => void onBookImageVisibility(book, event.target.checked).catch((error) => onNotice(error instanceof Error ? error.message : String(error)))} />
            <span><strong>显示全书插图</strong><small>换章后继续沿用</small></span>
          </label>
          <ResegmentationControls book={book} chapter={activeChapter} onPauseTranslation={onCancelBackground} onReload={onSelectChapter} onNotice={onNotice}>
            <button className="button small" disabled={backgroundJob?.running} onClick={() => onBackgroundBook('segment')}>后台切分全书</button>
            <button className="button small" disabled={backgroundJob?.running} onClick={() => onBackgroundBook('translate')}>后台翻译全书</button>
          </ResegmentationControls>
          {backgroundJob && <BackgroundProgress job={backgroundJob} onCancel={onCancelBackground} />}
          <div className="nav-mode-tabs" role="tablist" aria-label="左侧栏模式">
            <button className={navMode === 'chapters' ? 'active' : ''} onClick={() => setNavMode('chapters')}>目录</button>
            <button className={navMode === 'bookmarks' ? 'active' : ''} onClick={() => setNavMode('bookmarks')}>书签 <small>{bookmarks.length}</small></button>
          </div>
          {navMode === 'chapters' ? <nav aria-label="章节">
            {chapters.map((chapter) => (
              <button key={chapter.id} disabled={Boolean(loadingChapterId)} className={`chapter-item ${activeChapter?.id === chapter.id ? 'active' : ''}`} onClick={() => onSelectChapter(chapter)}>
                <span>{chapter.title}</span>{loadingChapterId === chapter.id ? <small className="status">加载中…</small> : <StatusBadge status={chapter.status} />}
              </button>
            ))}
          </nav> : <nav className="bookmark-list" aria-label="句子书签">
            {bookmarks.length ? bookmarks.map((bookmark) => (
              <button key={bookmark.id} disabled={Boolean(loadingChapterId)} className={`bookmark-item ${activeChapter?.id === bookmark.chapterId ? 'active' : ''}`} onClick={() => openBookmark(bookmark)}>
                <small>{bookmark.chapterTitle}</small><span lang="ja">{bookmark.text}</span>
              </button>
            )) : <p className="empty-bookmarks">还没有句子书签。</p>}
          </nav>}
        </div>}
      </aside>
      <main className="reading-stage">
        {!activeChapter && loadingChapterId ? <section className="processing-panel loading-chapter" aria-live="polite"><div className="loading-dango" aria-hidden="true" /><p className="eyebrow">按章读取</p><h1>正在加载章节</h1><p>正在读取本章句子、分词和注释。点击左上角头像可以安全返回书架。</p></section> : !activeChapter ? null : (
          <Reader
            userId={userId}
            book={book} chapter={activeChapter} previousChapter={previousChapter} nextChapter={nextChapter}
            chapterNavigationLoading={Boolean(loadingChapterId)} showImages={showBookImages}
            onNavigateChapter={onSelectChapter} onNotice={onNotice} onRetry={() => onProcessChapter(activeChapter)}
            onExplainSentence={onExplainSentence} backgroundJob={backgroundJob} dataRevision={dataRevision}
            onBackground={(kind) => onBackgroundChapter(kind, activeChapter)} bookmarks={bookmarks}
            onToggleBookmark={toggleBookmark}
          />
        )}
      </main>
    </div>
  )
}

function BackgroundProgress({ job, onCancel }: { job: BackgroundJob; onCancel: () => void }) {
  const value = job.total ? Math.round((job.completed / job.total) * 100) : (job.running ? 0 : 100)
  return <div className={`background-progress ${job.running ? 'running' : 'done'}`} role="status">
    <div><span>{job.label}</span><small>{job.total ? `${job.completed}/${job.total}` : '无待处理内容'}</small></div>
    <progress max="100" value={value} />
    <div className="background-progress-footer">
      {job.failed > 0 ? <small>{job.failed} 项失败</small> : <span />}
      {job.running && <button type="button" onClick={onCancel}>取消任务</button>}
    </div>
  </div>
}

function StatusBadge({ status }: { status: Chapter['status'] }) {
  const labels: Record<Chapter['status'], string> = {
    pending: '待切分', 'local-ready': '已切分', processing: '切分中', complete: '已切分', 'partial-failed': '部分失败', failed: '失败',
  }
  return <small className={`status ${status}`}>{labels[status]}</small>
}
