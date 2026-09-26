import { useCallback, useEffect, useMemo, useState } from 'react'
import { analyzeChapter, checkHealth, importEpub, importPlainText, importYomitanDictionary, loadApiSettings, lookupDictionary, preprocessChapter, saveApiSettings } from './api'
import { db, persistProjectData, removeBook, restoreProjectData } from './db'
import type {
  Annotation,
  AnalyzeResponse,
  ApiSettings,
  Book,
  Chapter,
  ContextSense,
  ContentBlock,
  ImportedBook,
  Lexeme,
  Sentence,
  StudyCard,
  Token,
} from './types'

const DEFAULT_SETTINGS: ApiSettings = {
  apiKey: '',
  baseUrl: 'https://api.deepseek.com',
  model: 'deepseek-chat',
  hasStoredApiKey: false,
}

const ANNOTATION_LABELS: Record<Annotation['type'], string> = {
  grammar: '语法',
  pragmatics: '语气与表达',
  ellipsis: '省略与指代',
  culture: '文化背景',
}

function makeLexemeKey(token: Pick<Token, 'lemma' | 'reading' | 'part_of_speech'>) {
  return `${token.lemma}|${token.reading}|${token.part_of_speech}`
}

function containsKanji(value: string) {
  return /[一-龯々]/.test(value)
}

function toHiragana(value: string) {
  return Array.from(value).map((char) => {
    const code = char.charCodeAt(0)
    return code >= 0x30a1 && code <= 0x30f6 ? String.fromCharCode(code - 0x60) : char
  }).join('')
}

function newId(prefix: string) {
  return `${prefix}_${crypto.randomUUID()}`
}

function App() {
  const [books, setBooks] = useState<Book[]>([])
  const [activeBook, setActiveBook] = useState<Book | null>(null)
  const [activeChapter, setActiveChapter] = useState<Chapter | null>(null)
  const [showImport, setShowImport] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [showStudyData, setShowStudyData] = useState(false)
  const [settings, setSettings] = useState<ApiSettings>(DEFAULT_SETTINGS)
  const [serverReady, setServerReady] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')

  const refreshBooks = useCallback(async () => {
    const rows = await db.books.orderBy('updatedAt').reverse().toArray()
    setBooks(rows)
    if (activeBook) {
      setActiveBook(rows.find((book) => book.id === activeBook.id) ?? null)
    }
  }, [activeBook])

  useEffect(() => {
    void restoreProjectData().then(refreshBooks).catch((error) => setNotice(error instanceof Error ? error.message : String(error)))
    void checkHealth().then(setServerReady)
    void loadApiSettings().then(setSettings).catch(() => undefined)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  async function saveImportedBook(imported: ImportedBook) {
    const bookId = newId('book')
    const now = Date.now()
    const book: Book = {
      id: bookId,
      title: imported.title,
      author: imported.author,
      createdAt: now,
      updatedAt: now,
    }
    const chapters: Chapter[] = imported.chapters.map((chapter) => ({
      id: `${bookId}:${chapter.id}`,
      bookId,
      title: chapter.title,
      order: chapter.order,
      text: chapter.text,
      blocks: chapter.blocks,
      originalHtmlUrl: chapter.original_html_url,
      status: chapter.text.trim() ? 'pending' : 'complete',
    }))
    await db.transaction('rw', [db.books, db.chapters], async () => {
      await db.books.add(book)
      await db.chapters.bulkAdd(chapters)
    })
    await persistProjectData()
    setShowImport(false)
    setActiveBook(book)
    setActiveChapter(chapters[0])
    if (imported.import_report) {
      const report = imported.import_report
      setNotice(`导入完成：${report.imported_sections} 节、${report.images} 张原图${report.broken_image_references ? `，${report.broken_image_references} 个图片引用失效` : '，图片引用完整'}。`)
    }
    await refreshBooks()
  }

  async function openBook(book: Book) {
    const chapters = await db.chapters.where('bookId').equals(book.id).sortBy('order')
    const preferred = chapters.find((chapter) => chapter.id === book.currentChapterId) ?? chapters[0]
    setActiveBook(book)
    setActiveChapter(preferred ?? null)
    setNotice('')
  }

  async function storeChapterResult(chapter: Chapter, result: AnalyzeResponse, status: Chapter['status']) {
    const oldSentences = await db.sentences.where('chapter_id').equals(chapter.id).toArray()
    const oldSentenceIds = oldSentences.map((sentence) => sentence.id)
    const oldTokens = await db.tokens.where('sentence_id').anyOf(oldSentenceIds).toArray()
    const tokens: Token[] = result.tokens.map((token) => ({ ...token, lexemeKey: makeLexemeKey(token) }))
    await db.transaction('rw', [db.chapters, db.sentences, db.tokens, db.annotations, db.contextSenses, db.lexemes], async () => {
      await db.contextSenses.bulkDelete(oldTokens.map((token) => token.id))
      await db.annotations.where('sentence_id').anyOf(oldSentenceIds).delete()
      await db.tokens.where('sentence_id').anyOf(oldSentenceIds).delete()
      await db.sentences.where('chapter_id').equals(chapter.id).delete()
      await db.sentences.bulkPut(result.sentences)
      await db.tokens.bulkPut(tokens)
      await db.annotations.bulkPut(result.annotations)
      await db.contextSenses.bulkPut(result.context_senses)
      for (const incoming of result.lexemes) {
        const existing = await db.lexemes.get(incoming.key)
        if (!existing) await db.lexemes.put({ ...incoming, firstKana: incoming.reading[0] || '未', updatedAt: Date.now() })
      }
      await db.chapters.update(chapter.id, { status, error: result.warnings.join('\n') || undefined })
    })
    await persistProjectData()
    setActiveChapter((current) => current?.id === chapter.id ? { ...current, status, error: result.warnings.join('\n') || undefined } : current)
  }

  async function processChapter(chapter: Chapter): Promise<boolean> {
    await db.chapters.update(chapter.id, { status: 'processing', error: undefined })
    setActiveChapter((current) => current?.id === chapter.id ? { ...current, status: 'processing' } : current)
    let localReady = false
    try {
      const local = await preprocessChapter(chapter.id, chapter.text, settings)
      await storeChapterResult(chapter, local, 'local-ready')
      localReady = true
      if (!settings.apiKey.trim() && !settings.hasStoredApiKey) {
        setNotice(`《${chapter.title}》已完成本地分词；配置 API Key 后可继续语法分析。`)
        return true
      }
      await db.chapters.update(chapter.id, { status: 'processing' })
      setActiveChapter((current) => current?.id === chapter.id ? { ...current, status: 'processing' } : current)
      const knownLexemeKeys = await db.lexemes.toCollection().primaryKeys() as string[]
      const result = await analyzeChapter(chapter.id, chapter.text, knownLexemeKeys, settings)
      const status = result.sentences.some((sentence) => sentence.status === 'failed') ? 'partial-failed' : 'complete'
      await storeChapterResult(chapter, result, status)
      setNotice(status === 'complete' ? `《${chapter.title}》语法分析完成。` : `《${chapter.title}》部分语法分析失败，可以重试。`)
      return status === 'complete'
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      const fallbackStatus = localReady ? 'local-ready' : 'failed'
      await db.chapters.update(chapter.id, { status: fallbackStatus, error: message })
      await persistProjectData()
      setActiveChapter((current) => current?.id === chapter.id ? { ...current, status: fallbackStatus, error: message } : current)
      setNotice(message)
      return localReady
    }
  }

  async function processBook() {
    if (!activeBook || busy) return
    setBusy(true)
    const chapters = await db.chapters.where('bookId').equals(activeBook.id).sortBy('order')
    for (const chapter of chapters) {
      if (chapter.status === 'complete') continue
      setActiveChapter(chapter)
      const ok = await processChapter(chapter)
      if (!ok) break
    }
    await db.books.update(activeBook.id, { updatedAt: Date.now() })
    await persistProjectData()
    await refreshBooks()
    setBusy(false)
  }

  async function deleteBook(book: Book) {
    await removeBook(book.id)
    await persistProjectData()
    if (activeBook?.id === book.id) {
      setActiveBook(null)
      setActiveChapter(null)
    }
    await refreshBooks()
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="brand" onClick={() => { setActiveBook(null); setActiveChapter(null) }}>
          <span className="brand-mark">日</span>
          <span><strong>日读</strong><small>AI 日语精读器</small></span>
        </button>
        <div className="top-actions">
          <span className={`server-dot ${serverReady ? 'ready' : 'down'}`} title={serverReady ? '本地服务正常' : '本地服务未连接'} />
          <button className="button ghost" onClick={() => setShowStudyData(true)}>词库与词卡</button>
          <button className="button ghost" onClick={() => setShowSettings(true)}>AI 设置</button>
          <button className="button primary" onClick={() => setShowImport(true)}>导入书籍</button>
        </div>
      </header>

      {notice && <div className="notice" role="status">{notice}<button onClick={() => setNotice('')}>×</button></div>}

      {!activeBook ? (
        <Library books={books} onOpen={openBook} onDelete={deleteBook} onImport={() => setShowImport(true)} />
      ) : (
        <Workspace
          book={activeBook}
          activeChapter={activeChapter}
          onSelectChapter={async (chapter) => {
            setActiveChapter(chapter)
            await db.books.update(activeBook.id, { currentChapterId: chapter.id, updatedAt: Date.now() })
            await persistProjectData()
          }}
          onProcessChapter={async (chapter) => { setBusy(true); await processChapter(chapter); setBusy(false) }}
          onProcessBook={processBook}
          busy={busy}
        />
      )}

      {showImport && <ImportDialog onClose={() => setShowImport(false)} onImported={saveImportedBook} />}
      {showStudyData && <StudyDataDialog onClose={() => setShowStudyData(false)} />}
      {showSettings && (
        <SettingsDialog
          value={settings}
          onClose={() => setShowSettings(false)}
          onSave={async (next) => {
            const saved = await saveApiSettings(next)
            setSettings(saved)
            setShowSettings(false)
            setNotice(saved.hasStoredApiKey
              ? 'AI 设置已保存到本地项目。'
              : 'Base URL 和模型已保存；处理章节前还需要填写 API Key。')
          }}
        />
      )}
    </div>
  )
}

function Library({ books, onOpen, onDelete, onImport }: {
  books: Book[]
  onOpen: (book: Book) => void
  onDelete: (book: Book) => void
  onImport: () => void
}) {
  return (
    <main className="library page-width">
      <div className="page-heading">
        <div><p className="eyebrow">我的书架</p><h1>继续精读</h1></div>
        <p>电子书、学习数据和阅读进度保存在本地项目中。</p>
      </div>
      {books.length === 0 ? (
        <section className="empty-state">
          <div className="empty-glyph">文</div>
          <h2>导入第一篇日文</h2>
          <p>支持粘贴文本、UTF-8 TXT 和无 DRM EPUB。本地解析完成后即可阅读，AI 仅补充重点语法与语境注释。</p>
          <button className="button primary" onClick={onImport}>导入内容</button>
        </section>
      ) : (
        <div className="book-grid">
          {books.map((book) => (
            <article className="book-card" key={book.id}>
              <button className="book-cover" onClick={() => onOpen(book)}><span>読む</span></button>
              <div className="book-meta">
                <button className="book-title" onClick={() => onOpen(book)}>{book.title}</button>
                <p>{book.author || '作者未知'}</p>
                <div><button className="text-button" onClick={() => onOpen(book)}>打开</button><button className="text-button danger" onClick={() => onDelete(book)}>删除</button></div>
              </div>
            </article>
          ))}
        </div>
      )}
    </main>
  )
}

function Workspace({ book, activeChapter, onSelectChapter, onProcessChapter, onProcessBook, busy }: {
  book: Book
  activeChapter: Chapter | null
  onSelectChapter: (chapter: Chapter) => void
  onProcessChapter: (chapter: Chapter) => void
  onProcessBook: () => void
  busy: boolean
}) {
  const [chapters, setChapters] = useState<Chapter[]>([])
  const load = useCallback(async () => {
    setChapters(await db.chapters.where('bookId').equals(book.id).sortBy('order'))
  }, [book.id, activeChapter?.status])
  useEffect(() => { void load() }, [load])

  return (
    <div className="workspace">
      <aside className="chapter-nav">
        <div className="book-heading"><small>正在阅读</small><h2>{book.title}</h2>{book.author && <p>{book.author}</p>}</div>
        <button className="button primary full" disabled={busy} onClick={onProcessBook}>{busy ? '处理中…' : '处理未完成章节'}</button>
        <nav aria-label="章节">
          {chapters.map((chapter) => (
            <button key={chapter.id} className={`chapter-item ${activeChapter?.id === chapter.id ? 'active' : ''}`} onClick={() => onSelectChapter(chapter)}>
              <span>{chapter.title}</span><StatusBadge status={chapter.status} />
            </button>
          ))}
        </nav>
      </aside>
      <main className="reading-stage">
        {!activeChapter ? null : (
          <Reader book={book} chapter={activeChapter} onRetry={() => onProcessChapter(activeChapter)} />
        )}
      </main>
    </div>
  )
}

function StatusBadge({ status }: { status: Chapter['status'] }) {
  const labels: Record<Chapter['status'], string> = {
    pending: '待处理', 'local-ready': '本地可读', processing: '分析中', complete: '已完成', 'partial-failed': '部分失败', failed: '失败',
  }
  return <small className={`status ${status}`}>{labels[status]}</small>
}

function Reader({ book, chapter, onRetry }: { book: Book; chapter: Chapter; onRetry: () => void }) {
  const [sentences, setSentences] = useState<Sentence[]>([])
  const [tokens, setTokens] = useState<Token[]>([])
  const [annotations, setAnnotations] = useState<Annotation[]>([])
  const [contextSenses, setContextSenses] = useState<ContextSense[]>([])
  const [selectedSentenceId, setSelectedSentenceId] = useState<string | null>(null)
  const [selectedTokenId, setSelectedTokenId] = useState<string | null>(null)
  const [lexeme, setLexeme] = useState<Lexeme | null>(null)
  const [showFurigana, setShowFurigana] = useState(true)
  const [showTranslation, setShowTranslation] = useState(false)
  const [showAnnotations, setShowAnnotations] = useState(true)
  const [showImages, setShowImages] = useState(false)
  const [viewMode, setViewMode] = useState<'study' | 'original'>('study')

  useEffect(() => {
    void (async () => {
      const nextSentences = await db.sentences.where('chapter_id').equals(chapter.id).sortBy('start')
      const ids = nextSentences.map((sentence) => sentence.id)
      const nextTokens = await db.tokens.where('sentence_id').anyOf(ids).toArray()
      const nextAnnotations = await db.annotations.where('sentence_id').anyOf(ids).toArray()
      const tokenIds = nextTokens.map((token) => token.id)
      const nextSenses = await db.contextSenses.where('token_id').anyOf(tokenIds).toArray()
      setSentences(nextSentences)
      setTokens(nextTokens)
      setAnnotations(nextAnnotations)
      setContextSenses(nextSenses)
      const restored = nextSentences.find((sentence) => sentence.id === book.currentSentenceId)?.id
      setSelectedSentenceId(restored ?? nextSentences[0]?.id ?? null)
      setSelectedTokenId(null)
    })()
  }, [book.currentSentenceId, chapter.id])

  const tokensBySentence = useMemo(() => {
    const map = new Map<string, Token[]>()
    for (const token of tokens) map.set(token.sentence_id, [...(map.get(token.sentence_id) ?? []), token])
    for (const values of map.values()) values.sort((a, b) => a.start - b.start)
    return map
  }, [tokens])
  const selectedSentence = sentences.find((sentence) => sentence.id === selectedSentenceId) ?? null
  const selectedToken = tokens.find((token) => token.id === selectedTokenId) ?? null
  const currentSense = contextSenses.find((sense) => sense.token_id === selectedTokenId)
  const currentNotes = annotations.filter((annotation) => annotation.sentence_id === selectedSentenceId)
  const hasTranslations = sentences.some((sentence) => Boolean(sentence.translation_zh?.trim()))

  useEffect(() => {
    if (!selectedToken) { setLexeme(null); return }
    void db.lexemes.get(selectedToken.lexemeKey).then(async (value) => {
      if (value) setLexeme(value)
      else setLexeme(await lookupDictionary(selectedToken.lemma, selectedToken.reading))
    })
  }, [selectedToken])

  async function selectSentence(sentence: Sentence) {
    setSelectedSentenceId(sentence.id)
    setSelectedTokenId(null)
    await db.books.update(book.id, { currentChapterId: chapter.id, currentSentenceId: sentence.id, updatedAt: Date.now() })
            void persistProjectData()
  }

  async function saveLexeme(senses: string[]) {
    if (!selectedToken) return
    const next: Lexeme = {
      key: selectedToken.lexemeKey,
      lemma: selectedToken.lemma,
      reading: selectedToken.reading,
      firstKana: selectedToken.reading[0] || '未',
      part_of_speech: selectedToken.part_of_speech,
      senses_zh: senses,
      source: '用户修正',
      correctedByUser: true,
      updatedAt: Date.now(),
    }
    await db.lexemes.put(next)
    void persistProjectData()
    setLexeme(next)
  }

  async function addCard() {
    if (!selectedToken || !selectedSentence) return
    const card: StudyCard = {
      id: newId('card'), lexemeKey: selectedToken.lexemeKey, tokenId: selectedToken.id,
      bookId: book.id, chapterId: chapter.id, sentenceId: selectedSentence.id,
      surface: selectedToken.surface, lemma: selectedToken.lemma, reading: selectedToken.reading,
      glossZh: currentSense?.gloss_zh || lexeme?.senses_zh.join('；') || '',
      sentence: selectedSentence.original, translationZh: selectedSentence.translation_zh,
      sourceLabel: `${book.title} · ${chapter.title}`, createdAt: Date.now(),
    }
    await db.cards.put(card)
    await persistProjectData()
  }

  const renderSentence = (sentence: Sentence) => (
    <button
      key={sentence.id}
      className={`sentence ${selectedSentenceId === sentence.id ? 'selected' : ''} ${sentence.status === 'failed' ? 'failed' : ''}`}
      onClick={() => void selectSentence(sentence)}
    >
      {(tokensBySentence.get(sentence.id) ?? []).map((token) => (
        <span
          key={token.id}
          className={`token ${token.is_content ? 'content' : ''} ${selectedTokenId === token.id ? 'selected' : ''}`}
          onClick={(event) => { event.stopPropagation(); setSelectedSentenceId(sentence.id); if (token.is_content) setSelectedTokenId(token.id) }}
          tabIndex={token.is_content ? 0 : -1}
          onKeyDown={(event) => { if (token.is_content && (event.key === 'Enter' || event.key === ' ')) setSelectedTokenId(token.id) }}
        >
          {showFurigana && token.is_content && containsKanji(token.surface) ? <ruby>{token.surface}<rt>{toHiragana(token.reading)}</rt></ruby> : token.surface}
        </span>
      ))}
    </button>
  )

  const renderBlock = (block: ContentBlock) => {
    if (block.type === 'image') return showImages ? <figure key={block.id} className={`book-image ${block.placement ?? 'left'}`}><img src={block.asset_url ?? ''} alt={block.alt ?? ''} /></figure> : null
    if (block.type === 'page-break') return <div key={block.id} className="page-break" aria-hidden="true" />
    if (block.type === 'separator') return <hr key={block.id} />
    const matches = sentences.filter((sentence) => sentence.start >= block.start && sentence.end <= block.end)
    const content = matches.length ? matches.map(renderSentence) : block.text
    if (block.type === 'heading') return <h2 key={block.id} className="book-block heading">{content}</h2>
    if (block.type === 'quote') return <blockquote key={block.id} className="book-block quote">{content}</blockquote>
    if (block.type === 'list-item') return <div key={block.id} className="book-block list-item">{content}</div>
    return <p key={block.id} className="book-block paragraph">{content}</p>
  }

  return (
    <div className="reader-layout">
      <article className="reader-pane">
        <div className="reader-toolbar">
          <div><p className="eyebrow">{book.title}</p><h1>{chapter.title}</h1></div>
          <div className="display-toggles">
            {chapter.originalHtmlUrl && <button className={`mode-button ${viewMode === 'original' ? 'active' : ''}`} onClick={() => setViewMode(viewMode === 'study' ? 'original' : 'study')}>{viewMode === 'study' ? '原书预览' : '精读模式'}</button>}
            <Toggle label="振假名" value={showFurigana} onChange={setShowFurigana} />
            <Toggle label="插图" value={showImages} onChange={setShowImages} />
            {hasTranslations && <Toggle label="译文" value={showTranslation} onChange={setShowTranslation} />}
            <Toggle label="注释" value={showAnnotations} onChange={setShowAnnotations} />
          </div>
        </div>
        {(chapter.status === 'pending' || chapter.status === 'failed') && <div className="inline-warning">本章可预览，尚未完成本地分词和语法分析。<button onClick={onRetry}>{chapter.status === 'failed' ? '重试' : '开始处理'}</button></div>}
        {chapter.status === 'processing' && <div className="inline-warning">正在进行本地分词与 AI 语法分析，已完成内容会自动保留。</div>}
        {chapter.status === 'partial-failed' && <div className="inline-warning">本章有句子处理失败。<button onClick={onRetry}>重试</button></div>}
        {viewMode === 'original' && chapter.originalHtmlUrl
          ? <iframe className="original-preview" sandbox="" src={chapter.originalHtmlUrl} title={`${chapter.title} 原书预览`} />
          : <div className="japanese-text" lang="ja">{chapter.blocks?.length ? chapter.blocks.map(renderBlock) : sentences.length ? sentences.map(renderSentence) : chapter.text}</div>}
      </article>
      <aside className="study-panel">
        <p className="panel-kicker">当前句</p>
        {selectedSentence ? (
          <>
            <p className="panel-original" lang="ja">{selectedSentence.original}</p>
            {selectedSentence.status === 'failed' ? <div className="error-box">{selectedSentence.error || '本句处理失败'}</div> : (
              <>
                {showTranslation && selectedSentence.translation_zh && <section className="panel-section"><h3>AI 参考译文</h3><p>{selectedSentence.translation_zh}</p></section>}
                {selectedToken && <DictionaryCard token={selectedToken} lexeme={lexeme} contextGloss={currentSense?.gloss_zh ?? ''} onSave={saveLexeme} onAddCard={addCard} />}
                {showAnnotations && <section className="panel-section"><h3>学习注释</h3>{currentNotes.length ? currentNotes.map((note) => <div className="annotation" key={note.id}><span>{ANNOTATION_LABELS[note.type]}</span><strong lang="ja">{note.quote}</strong><p>{note.explanation_zh}</p></div>) : <p className="muted">本句没有需要补充的 N1 级注释。</p>}</section>}
              </>
            )}
          </>
        ) : <p className="muted">选择一个句子开始精读。</p>}
      </aside>
    </div>
  )
}

function DictionaryCard({ token, lexeme, contextGloss, onSave, onAddCard }: {
  token: Token; lexeme: Lexeme | null; contextGloss: string
  onSave: (senses: string[]) => void; onAddCard: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  useEffect(() => { setValue(lexeme?.senses_zh.join('\n') ?? '') }, [lexeme])
  return (
    <section className="dictionary-card">
      <div className="dictionary-head"><div><small>{token.part_of_speech}</small><h2>{token.lemma}</h2><p>{toHiragana(token.reading)}</p></div><button className="button small" onClick={() => void onAddCard()}>加入词卡</button></div>
      {contextGloss && <div className="context-gloss"><small>当前语境选择</small><p>{contextGloss}</p></div>}
      <div className="dictionary-senses">
        <div className="section-title"><h3>日中词典</h3><button className="text-button" onClick={() => setEditing(!editing)}>{editing ? '取消' : '修正'}</button></div>
        {editing ? <><textarea value={value} onChange={(event) => setValue(event.target.value)} rows={4} /><button className="button primary small" onClick={() => { void onSave(value.split('\n').map((x) => x.trim()).filter(Boolean)); setEditing(false) }}>保存到个人词库</button></> : <>{lexeme?.senses_zh.length ? <ol>{lexeme.senses_zh.map((sense, index) => <li key={index}>{sense}</li>)}</ol> : <p className="muted">本地词典未收录。可在“词库与词卡”中导入 Yomitan 格式日中词典。</p>}<small className="source">来源：{lexeme?.source ?? '未收录'}</small></>}
      </div>
    </section>
  )
}

function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (next: boolean) => void }) {
  return <label className="toggle"><input type="checkbox" checked={value} onChange={(event) => onChange(event.target.checked)} /><span>{label}</span></label>
}

function downloadJson(filename: string, value: unknown) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

function StudyDataDialog({ onClose }: { onClose: () => void }) {
  const [lexemes, setLexemes] = useState<Lexeme[]>([])
  const [cards, setCards] = useState<StudyCard[]>([])
  const [tab, setTab] = useState<'dictionary' | 'cards'>('dictionary')
  const [query, setQuery] = useState('')
  const [dictionaryNotice, setDictionaryNotice] = useState('')
  const [importingDictionary, setImportingDictionary] = useState(false)

  async function importDictionary(file: File | null) {
    if (!file) return
    setImportingDictionary(true); setDictionaryNotice('')
    try {
      const result = await importYomitanDictionary(file)
      setDictionaryNotice(`已导入 ${result.source}：${result.entries.toLocaleString()} 个词条。`)
    } catch (error) {
      setDictionaryNotice(error instanceof Error ? error.message : String(error))
    } finally { setImportingDictionary(false) }
  }

  useEffect(() => {
    void Promise.all([
      db.lexemes.orderBy('reading').toArray(),
      db.cards.orderBy('createdAt').reverse().toArray(),
    ]).then(([nextLexemes, nextCards]) => {
      setLexemes(nextLexemes)
      setCards(nextCards)
    })
  }, [])

  const filteredLexemes = lexemes.filter((item) =>
    !query || item.lemma.includes(query) || item.reading.includes(query) || item.firstKana === query,
  )

  return (
    <Modal title="个人词库与上下文词卡" onClose={onClose}>
      <div className="tab-row">
        <button className={tab === 'dictionary' ? 'active' : ''} onClick={() => setTab('dictionary')}>日中词库 {lexemes.length}</button>
        <button className={tab === 'cards' ? 'active' : ''} onClick={() => setTab('cards')}>上下文词卡 {cards.length}</button>
      </div>
      {tab === 'dictionary' ? <>
        <div className="data-tools">
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="按词形、读音或首个片假名查找" />
          <label className="button small file-button"><input type="file" accept=".zip,application/zip" onChange={(event) => void importDictionary(event.target.files?.[0] ?? null)} />{importingDictionary ? '导入中…' : '导入日中词典'}</label>
          <button className="button small" onClick={() => downloadJson(`日读个人词库-${new Date().toISOString().slice(0, 10)}.json`, { format: 'nichidoku-lexicon-v1', exportedAt: new Date().toISOString(), lexemes })}>导出共享</button>
        </div>
        {dictionaryNotice && <p className="muted">{dictionaryNotice}</p>}
        <div className="data-list">{filteredLexemes.length ? filteredLexemes.map((item) => <article key={item.key}>
          <div><strong lang="ja">{item.lemma}</strong><span>{toHiragana(item.reading)} · {item.part_of_speech}</span></div>
          <p>{item.senses_zh.join('；')}</p><small>{item.firstKana} · {item.source}</small>
        </article>) : <p className="muted">还没有匹配的词条。</p>}</div>
      </> : <>
        <div className="data-tools">
          <p>词卡保留书籍、章节、原句和当前语境义。</p>
          <button className="button small" onClick={() => downloadJson(`日读上下文词卡-${new Date().toISOString().slice(0, 10)}.json`, { format: 'nichidoku-cards-v1', exportedAt: new Date().toISOString(), cards })}>导出词卡</button>
        </div>
        <div className="data-list cards">{cards.length ? cards.map((card) => <article key={card.id}>
          <div><strong lang="ja">{card.surface}</strong><span>{toHiragana(card.reading)}</span></div><p>{card.glossZh}</p>
          <blockquote lang="ja">{card.sentence}</blockquote><small>{card.sourceLabel}</small>
        </article>) : <p className="muted">还没有词卡。阅读时点击词语即可收藏。</p>}</div>
      </>}
    </Modal>
  )
}

function ImportDialog({ onClose, onImported }: { onClose: () => void; onImported: (book: ImportedBook) => void }) {
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit() {
    setBusy(true); setError('')
    try {
      let imported: ImportedBook
      if (file?.name.toLowerCase().endsWith('.epub')) imported = await importEpub(file)
      else if (file) imported = await importPlainText(title || file.name.replace(/\.txt$/i, ''), await file.text())
      else imported = await importPlainText(title || '粘贴文本', text)
      await onImported(imported)
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  return (
    <Modal title="导入日文内容" onClose={onClose}>
      <p className="muted">支持无 DRM EPUB、UTF-8 TXT，或直接粘贴日文。EPUB 会保留段落结构和原图位置；精读模式默认隐藏插图，可随时开启。</p>
      <label className="field"><span>标题</span><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="可选" /></label>
      <label className="drop-zone"><input type="file" accept=".epub,.txt,text/plain,application/epub+zip" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><strong>{file ? file.name : '选择 EPUB 或 TXT'}</strong><small>也可以把文件拖到这里</small></label>
      <div className="divider"><span>或者粘贴文本</span></div>
      <label className="field"><textarea value={text} onChange={(event) => setText(event.target.value)} rows={9} placeholder="ここに日本語の文章を貼り付けてください。" /></label>
      {error && <div className="error-box">{error}</div>}
      <div className="modal-actions"><button className="button ghost" onClick={onClose}>取消</button><button className="button primary" disabled={busy || (!file && !text.trim())} onClick={() => void submit()}>{busy ? '导入中…' : '导入并预览'}</button></div>
    </Modal>
  )
}

function SettingsDialog({ value, onClose, onSave }: { value: ApiSettings; onClose: () => void; onSave: (next: ApiSettings) => void | Promise<void> }) {
  const [draft, setDraft] = useState(value)
  const canSave = Boolean(draft.baseUrl.trim() && draft.model.trim())
  const save = () => onSave({
    apiKey: draft.apiKey.trim(),
    baseUrl: draft.baseUrl.trim().replace(/\/$/, ''),
    model: draft.model.trim(),
    hasStoredApiKey: draft.hasStoredApiKey,
  })
  return (
    <Modal title="AI 设置" onClose={onClose}>
      <div className="privacy-note"><strong>配置保存在本地项目</strong><p>API Key 写入被 Git 忽略的 data/settings.json，仅在调用所配置的 AI 接口时发送。</p></div>
      <label className="field"><span>API Key（处理章节时必填）</span><input type="password" autoComplete="off" value={draft.apiKey} onChange={(event) => setDraft({ ...draft, apiKey: event.target.value })} placeholder={draft.hasStoredApiKey ? '已保存；留空保持不变' : 'sk-…'} /></label>
      <label className="field"><span>OpenAI 兼容 Base URL</span><input value={draft.baseUrl} onChange={(event) => setDraft({ ...draft, baseUrl: event.target.value })} /></label>
      <label className="field"><span>模型</span><input value={draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })} /></label>
      <div className="modal-actions"><button className="button ghost" onClick={onClose}>取消</button><button className="button primary" disabled={!canSave} onClick={save}>应用</button></div>
    </Modal>
  )
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}><div className="modal" role="dialog" aria-modal="true" aria-label={title}><div className="modal-head"><h2>{title}</h2><button onClick={onClose} aria-label="关闭">×</button></div>{children}</div></div>
}

export default App
