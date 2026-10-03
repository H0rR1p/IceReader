import { useEffect, useMemo, useState } from 'react'
import type { CSSProperties } from 'react'
import type { Book } from '../../types'
import { downloadBookTransfer, importBookTransfer } from '../../api'
import { restoreProjectIndex } from '../../db'

type Collection = { id: string; name: string; books: Book[] }

function CoverArtwork({ src, className = '' }: { src?: string; className?: string }) {
  const [attempt, setAttempt] = useState(0)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    setAttempt(0)
    setFailed(false)
  }, [src])
  if (!src || failed) return null
  const separator = src.includes('?') ? '&' : '?'
  return <img
    className={className}
    src={attempt ? `${src}${separator}cover_retry=${attempt}` : src}
    alt=""
    onError={() => {
      if (attempt === 0) window.setTimeout(() => setAttempt(1), 250)
      else setFailed(true)
    }}
  />
}

export default function Library({
  books, loading, loadingBookId, onOpen, onDelete, onChangeCover, onImport,
  onSaveCollection, onDissolveCollection, onNotice, onRefresh,
}: {
  books: Book[]
  loading: boolean
  loadingBookId: string | null
  onOpen: (book: Book) => void
  onDelete: (book: Book) => Promise<void>
  onChangeCover: (book: Book, file: File) => Promise<void>
  onImport: () => void
  onSaveCollection: (id: string, name: string, bookIds: string[]) => Promise<void>
  onDissolveCollection: (id: string) => Promise<void>
  onNotice: (message: string) => void
  onRefresh: () => Promise<void>
}) {
  const [pendingDelete, setPendingDelete] = useState<Book | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [changingCoverId, setChangingCoverId] = useState<string | null>(null)
  const [showCovers, setShowCovers] = useState(false)
  const [activeCollectionId, setActiveCollectionId] = useState<string | null>(null)
  const [collectionDraft, setCollectionDraft] = useState<{ id: string; name: string; bookIds: string[] } | null>(null)
  const [savingCollection, setSavingCollection] = useState(false)
  const [shareSelection, setShareSelection] = useState<string[] | null>(null)
  const [shareQuery, setShareQuery] = useState('')
  const [shareBusy, setShareBusy] = useState<'export' | 'import' | null>(null)
  const [shareError, setShareError] = useState('')

  async function exportSelectedBooks() {
    if (!shareSelection?.length) return
    setShareBusy('export'); setShareError('')
    try {
      await downloadBookTransfer(shareSelection)
      onNotice(`已生成 ${shareSelection.length} 本书的分享包，包含书籍资源、切分与翻译结果。`)
      setShareSelection(null)
    } catch (error) { setShareError(error instanceof Error ? error.message : String(error)) }
    finally { setShareBusy(null) }
  }

  async function importSharedBooks(file: File | null) {
    if (!file) return
    setShareBusy('import'); setShareError('')
    try {
      const result = await importBookTransfer(file)
      await restoreProjectIndex()
      await onRefresh()
      onNotice(`已载入 ${result.imported_books} 本书及其切分、翻译结果，跳过 ${result.skipped_books} 本已有书籍。`)
    } catch (error) { setShareError(error instanceof Error ? error.message : String(error)) }
    finally { setShareBusy(null) }
  }

  const collections = useMemo(() => {
    const groups = new Map<string, Collection>()
    for (const book of books) {
      if (!book.collectionId) continue
      const group = groups.get(book.collectionId) ?? {
        id: book.collectionId,
        name: book.collectionName?.trim() || '未命名合集',
        books: [],
      }
      group.books.push(book)
      groups.set(group.id, group)
    }
    return [...groups.values()]
  }, [books])
  const activeCollection = collections.find((collection) => collection.id === activeCollectionId) ?? null
  const visibleBooks = activeCollection ? activeCollection.books : books.filter((book) => !book.collectionId)
  const recentBooks = useMemo(() => books
    .filter((book) => Boolean(book.lastOpenedAt || book.currentChapterId))
    .sort((left, right) => (right.lastOpenedAt ?? right.updatedAt) - (left.lastOpenedAt ?? left.updatedAt))
    .slice(0, 4), [books])

  async function changeCover(book: Book, file: File | null) {
    if (!file) return
    setChangingCoverId(book.id)
    try {
      await onChangeCover(book, file)
    } finally {
      setChangingCoverId(null)
    }
  }

  async function confirmDelete() {
    if (!pendingDelete || deleting) return
    setDeleting(true)
    await onDelete(pendingDelete)
    setDeleting(false)
    setPendingDelete(null)
  }

  function editCollection(collection?: Collection) {
    setCollectionDraft(collection
      ? { id: collection.id, name: collection.name, bookIds: collection.books.map((book) => book.id) }
      : { id: `collection_${crypto.randomUUID()}`, name: '', bookIds: [] })
  }

  async function saveCollection() {
    if (!collectionDraft?.name.trim() || !collectionDraft.bookIds.length) return
    setSavingCollection(true)
    try {
      await onSaveCollection(collectionDraft.id, collectionDraft.name.trim(), collectionDraft.bookIds)
      setActiveCollectionId(collectionDraft.id)
      setCollectionDraft(null)
    } finally {
      setSavingCollection(false)
    }
  }

  async function dissolveCollection() {
    if (!collectionDraft) return
    setSavingCollection(true)
    try {
      await onDissolveCollection(collectionDraft.id)
      setActiveCollectionId(null)
      setCollectionDraft(null)
    } finally {
      setSavingCollection(false)
    }
  }

  return (
    <>
      <main className="library page-width">
        <div className="page-heading">
          <div>
            <h1>{activeCollection ? activeCollection.name : '书架'}</h1>
          </div>
          <div className="library-heading-side">
            {activeCollection && <p>{activeCollection.books.length} 本书</p>}
            <div className="library-heading-actions">
              {books.length > 0 && <button className="button ghost" disabled={!!shareBusy} onClick={() => { setShareSelection([]); setShareQuery(''); setShareError('') }}>分享书籍</button>}
              <label className={`button ghost file-button ${shareBusy ? 'disabled' : ''}`}>{shareBusy === 'import' ? '正在载入…' : '载入分享包'}<input hidden type="file" accept=".zip,application/zip" disabled={!!shareBusy} onChange={(event) => { void importSharedBooks(event.target.files?.[0] ?? null); event.currentTarget.value = '' }} /></label>
              {activeCollection && <button className="button ghost" onClick={() => setActiveCollectionId(null)}>返回书架</button>}
              <button className="button ghost" onClick={() => editCollection(activeCollection ?? undefined)}>{activeCollection ? '管理合集' : '新建合集'}</button>
              {books.length > 0 && <button className="button ghost cover-visibility-toggle" aria-pressed={showCovers} onClick={() => setShowCovers((visible) => !visible)}>{showCovers ? '隐藏封面' : '显示封面'}</button>}
            </div>
          </div>
        </div>
        {shareError && shareSelection === null && <p className="error-box" role="alert">{shareError}</p>}
        {loading ? (
          <section className="empty-state loading-state" aria-live="polite">
            <div className="loading-dango" aria-hidden="true" />
            <h2>正在读取书架</h2>
            <p>只加载书籍与章节索引，不读取正文、词元和释义。</p>
          </section>
        ) : books.length === 0 ? (
          <section className="empty-state">
            <div className="empty-glyph">文</div>
            <h2>导入第一篇日文</h2>
            <p>支持粘贴文本、UTF-8 TXT 和无 DRM EPUB。导入后按需切分当前章节，再逐句生成句意、词义和注释。</p>
            <button className="button primary" onClick={onImport}>导入内容</button>
          </section>
        ) : (
          <>
          {!activeCollection && recentBooks.length > 0 && <section className="recent-section" aria-labelledby="recent-heading">
            <h2 id="recent-heading">最近阅读</h2>
            <div className="recent-grid">
              {recentBooks.map((book) => (
                <button className="recent-book" key={book.id} disabled={Boolean(loadingBookId)} onClick={() => onOpen(book)}>
                  <span className="recent-book-cover" aria-hidden="true">
                    <span className="book-cover-default"><img src="/bingdu-logo.png" alt="" /></span>
                    {showCovers && <CoverArtwork className="book-cover-image" src={book.coverUrl} />}
                  </span>
                  <span className="recent-book-meta">
                    <strong>{book.title}</strong>
                    <small>{book.author || '作者未知'}</small>
                    <span>{loadingBookId === book.id ? '正在加载…' : '继续阅读'}</span>
                  </span>
                </button>
              ))}
            </div>
          </section>}
          {!activeCollection && <h2 className="shelf-title">我的书架</h2>}
          <div className="book-grid">
            {!activeCollection && collections.map((collection) => (
              <article className="collection-card" key={collection.id}>
                <button className="collection-visual" onClick={() => setActiveCollectionId(collection.id)} aria-label={`打开合集“${collection.name}”`}>
                  <span className="collection-previews" aria-hidden="true">
                    {collection.books.slice(0, 3).map((book, index) => (
                      <span className="collection-preview" style={{ '--preview-index': index } as CSSProperties} key={book.id}>
                        {showCovers && book.coverUrl ? <CoverArtwork src={book.coverUrl} /> : <span />}
                      </span>
                    ))}
                  </span>
                  <span className="collection-folder-front" aria-hidden="true"><img src="/bingdu-logo.png" alt="" /></span>
                </button>
                <div className="book-meta collection-meta">
                  <button className="book-title" onClick={() => setActiveCollectionId(collection.id)}>{collection.name}</button>
                  <p>{collection.books.length} 本书</p>
                  <button className="text-button" onClick={() => editCollection(collection)}>管理</button>
                </div>
              </article>
            ))}
            {visibleBooks.map((book) => (
              <article className="book-card" key={book.id}>
                <button className="book-cover" disabled={Boolean(loadingBookId)} aria-label={`打开《${book.title}》`} onClick={() => onOpen(book)}>
                  <span className="book-cover-default" aria-hidden="true"><img src="/bingdu-logo.png" alt="" /></span>
                  {showCovers && <CoverArtwork className="book-cover-image" src={book.coverUrl} />}
                </button>
                <div className="book-meta">
                  <button className="book-title" disabled={Boolean(loadingBookId)} onClick={() => onOpen(book)}>{book.title}</button>
                  <div className="book-byline"><p>{book.author || '作者未知'}</p>{book.translationComplete && <small className="translated-badge">已翻译</small>}</div>
                  <div className="book-card-actions">
                    <button className="text-button" disabled={Boolean(loadingBookId)} onClick={() => onOpen(book)}>{loadingBookId === book.id ? '正在加载…' : '打开'}</button>
                    <label className={`text-button cover-picker ${changingCoverId === book.id ? 'disabled' : ''}`}>
                      {changingCoverId === book.id ? '上传中…' : '更换封面'}
                      <input type="file" accept="image/jpeg,image/png,image/webp,image/gif" disabled={changingCoverId === book.id} onChange={(event) => { void changeCover(book, event.target.files?.[0] ?? null); event.target.value = '' }} />
                    </label>
                    <button className="text-button danger" onClick={() => setPendingDelete(book)}>删除</button>
                  </div>
                </div>
              </article>
            ))}
            {activeCollection && visibleBooks.length === 0 && <section className="empty-state"><h2>这个合集还是空的</h2><button className="button ghost" onClick={() => editCollection(activeCollection)}>添加书籍</button></section>}
          </div>
          </>
        )}
      </main>

      {shareSelection !== null && <div className="modal-backdrop" role="presentation">
        <section className="modal collection-dialog" role="dialog" aria-modal="true" aria-labelledby="share-books-title">
          <h2 id="share-books-title">导出书籍分享包</h2>
          <p>选择一本或多本书，包含正文、封面、插图、切分、翻译与相关词义。不会包含个人阅读进度、书签、词卡和学习记录。已有书籍载入时会跳过。</p>
          <input autoFocus value={shareQuery} disabled={!!shareBusy} onChange={(event) => setShareQuery(event.target.value)} placeholder="搜索书名或作者" aria-label="搜索分享书籍" />
          <div className="modal-actions"><span>已选 {shareSelection.length} 本</span><button className="text-button" disabled={!!shareBusy} onClick={() => setShareSelection(books.map((book) => book.id))}>全选</button><button className="text-button" disabled={!!shareBusy} onClick={() => setShareSelection([])}>清空</button></div>
          <fieldset className="collection-book-picker"><legend>选择要分享的书籍</legend>{books.filter((book) => `${book.title} ${book.author ?? ''}`.toLowerCase().includes(shareQuery.toLowerCase())).map((book) => <label key={book.id}><input type="checkbox" disabled={!!shareBusy} checked={shareSelection.includes(book.id)} onChange={(event) => setShareSelection(event.target.checked ? [...shareSelection, book.id] : shareSelection.filter((id) => id !== book.id))} /><span><strong>{book.title}</strong><small>{book.author || '作者未知'}{book.translationComplete ? ' · 已翻译' : ''}</small></span></label>)}</fieldset>
          {shareError && <p className="error-box" role="alert">{shareError}</p>}
          <div className="modal-actions"><button className="button" disabled={!!shareBusy} onClick={() => setShareSelection(null)}>取消</button><button className="button primary" disabled={!!shareBusy || !shareSelection.length} onClick={() => void exportSelectedBooks()}>{shareBusy === 'export' ? '正在打包…' : `导出分享包（${shareSelection.length} 本）`}</button></div>
        </section>
      </div>}

      {collectionDraft && <div className="modal-backdrop" role="presentation">
        <section className="modal collection-dialog" role="dialog" aria-modal="true" aria-labelledby="collection-dialog-title">
          <h2 id="collection-dialog-title">{collections.some((item) => item.id === collectionDraft.id) ? '管理合集' : '新建合集'}</h2>
          <label className="field"><span>合集名称</span><input autoFocus value={collectionDraft.name} onChange={(event) => setCollectionDraft({ ...collectionDraft, name: event.target.value })} placeholder="例如：夏目漱石" /></label>
          <fieldset className="collection-book-picker">
            <legend>选择书籍</legend>
            {books.map((book) => {
              const checked = collectionDraft.bookIds.includes(book.id)
              return <label key={book.id}><input type="checkbox" checked={checked} onChange={() => setCollectionDraft({
                ...collectionDraft,
                bookIds: checked ? collectionDraft.bookIds.filter((id) => id !== book.id) : [...collectionDraft.bookIds, book.id],
              })} /><span><strong>{book.title}</strong><small>{book.author || '作者未知'}{book.collectionName && book.collectionId !== collectionDraft.id ? ` · 当前在“${book.collectionName}”` : ''}</small></span></label>
            })}
          </fieldset>
          <div className="modal-actions collection-dialog-actions">
            {collections.some((item) => item.id === collectionDraft.id) && <button className="button ghost dissolve-button" disabled={savingCollection} onClick={() => void dissolveCollection()}>解散合集</button>}
            <button className="button" disabled={savingCollection} onClick={() => setCollectionDraft(null)}>取消</button>
            <button className="button primary" disabled={savingCollection || !collectionDraft.name.trim() || !collectionDraft.bookIds.length} onClick={() => void saveCollection()}>{savingCollection ? '正在保存…' : '保存合集'}</button>
          </div>
        </section>
      </div>}

      {pendingDelete && <div className="modal-backdrop" role="presentation">
        <section className="modal confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="delete-book-title">
          <div className="confirm-icon">删</div>
          <h2 id="delete-book-title">确认删除这本书？</h2>
          <p>《{pendingDelete.title}》的正文、阅读进度和逐句结果将从本地项目中删除。</p>
          <div className="modal-actions">
            <button className="button" autoFocus disabled={deleting} onClick={() => setPendingDelete(null)}>取消</button>
            <button className="button danger-solid" disabled={deleting} onClick={() => void confirmDelete()}>{deleting ? '正在删除…' : '确认删除'}</button>
          </div>
        </section>
      </div>}
    </>
  )
}
