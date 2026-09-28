import { useState } from 'react'
import type { Book } from '../../types'

export default function Library({ books, loading, loadingBookId, onOpen, onDelete, onChangeCover, onImport }: {
  books: Book[]
  loading: boolean
  loadingBookId: string | null
  onOpen: (book: Book) => void
  onDelete: (book: Book) => Promise<void>
  onChangeCover: (book: Book, file: File) => Promise<void>
  onImport: () => void
}) {
  const [pendingDelete, setPendingDelete] = useState<Book | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [changingCoverId, setChangingCoverId] = useState<string | null>(null)
  const [showCovers, setShowCovers] = useState(false)

  async function changeCover(book: Book, file: File | null) {
    if (!file) return
    setChangingCoverId(book.id)
    await onChangeCover(book, file)
    setChangingCoverId(null)
  }

  async function confirmDelete() {
    if (!pendingDelete || deleting) return
    setDeleting(true)
    await onDelete(pendingDelete)
    setDeleting(false)
    setPendingDelete(null)
  }

  return (
    <><main className="library page-width">
      <div className="page-heading">
        <div><p className="eyebrow">我的书架</p><h1>继续冰读</h1></div>
        <div className="library-heading-side">
          <p>电子书、学习数据和阅读进度保存在本地项目中。</p>
          {books.length > 0 && <button className="button ghost cover-visibility-toggle" aria-pressed={showCovers} onClick={() => setShowCovers((visible) => !visible)}>{showCovers ? '隐藏封面' : '显示封面'}</button>}
        </div>
      </div>
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
        <div className="book-grid">
          {books.map((book) => (
            <article className="book-card" key={book.id}>
              <button className="book-cover" disabled={Boolean(loadingBookId)} aria-label={`打开《${book.title}》`} onClick={() => onOpen(book)}>
                <span className="book-cover-default" aria-hidden="true"><img src="/bingdu-logo.png" alt="" /></span>
                {showCovers && book.coverUrl && <img className="book-cover-image" src={book.coverUrl} alt="" onError={(event) => { event.currentTarget.hidden = true }} />}
              </button>
              <div className="book-meta">
                <button className="book-title" disabled={Boolean(loadingBookId)} onClick={() => onOpen(book)}>{book.title}</button>
                <p>{book.author || '作者未知'}</p>
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
        </div>
      )}
    </main>
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

