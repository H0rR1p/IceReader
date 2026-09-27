import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { cancelVoiceJob, checkHealth, deleteBookCover, explainSentence, importEpub, importPlainText, importYomitanDictionary, loadApiSettings, loadVoiceJob, loadVoiceSettings, lookupDictionary, saveApiSettings, saveVoiceSettings, segmentChapter, startVoiceJob, uploadBookCover, uploadVoiceTemplate } from './api'
import { db, loadChapterData, loadStudyData, removeBook, restoreProjectIndex, syncRecords } from './db'
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
  VoiceJob,
  VoiceSettings,
} from './types'

const DEFAULT_SETTINGS: ApiSettings = {
  apiKey: '',
  baseUrl: 'https://api.deepseek.com',
  model: 'deepseek-chat',
  hasStoredApiKey: false,
}

const DEFAULT_VOICE_SETTINGS: VoiceSettings = {
  ymmPath: '', ymmFound: false, templateFound: false, characterName: '',
  playbackRate: 85, volume: 50, ready: false,
}

const ANNOTATION_LABELS: Record<Annotation['type'], string> = {
  grammar: '语法',
  pragmatics: '语气与表达',
  ellipsis: '省略与指代',
  culture: '文化背景',
}

type BackgroundJob = {
  kind: 'segment' | 'translate'
  scope: 'book' | 'chapter'
  label: string
  completed: number
  total: number
  failed: number
  running: boolean
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
  const [showVoiceSettings, setShowVoiceSettings] = useState(false)
  const [showStudyData, setShowStudyData] = useState(false)
  const [settings, setSettings] = useState<ApiSettings>(DEFAULT_SETTINGS)
  const [voiceSettings, setVoiceSettings] = useState<VoiceSettings>(DEFAULT_VOICE_SETTINGS)
  const [serverReady, setServerReady] = useState<boolean | null>(null)
  const [notice, setNotice] = useState('')
  const [backgroundJob, setBackgroundJob] = useState<BackgroundJob | null>(null)
  const [dataRevision, setDataRevision] = useState(0)
  const [logoBouncing, setLogoBouncing] = useState(false)
  const [loadingBookId, setLoadingBookId] = useState<string | null>(null)
  const [loadingChapterId, setLoadingChapterId] = useState<string | null>(null)
  const [libraryLoading, setLibraryLoading] = useState(true)
  const logoAudiosRef = useRef(new Set<HTMLAudioElement>())
  const backgroundAbortRef = useRef<AbortController | null>(null)
  const navigationAbortRef = useRef<AbortController | null>(null)

  const refreshBooks = useCallback(async () => {
    const rows = await db.books.orderBy('updatedAt').reverse().toArray()
    const changedBooks: Book[] = []
    for (const book of rows) {
      if (book.coverUrl) continue
      const chapters = await db.chapters.where('bookId').equals(book.id).sortBy('order')
      const coverUrl = chapters.flatMap((chapter) => chapter.blocks ?? [])
        .find((block) => block.type === 'image' && block.asset_url)?.asset_url
      if (coverUrl) {
        book.coverUrl = coverUrl
        await db.books.put(book)
        changedBooks.push(book)
      }
    }
    if (changedBooks.length) await syncRecords({ books: changedBooks })
    setBooks(rows)
    if (activeBook) {
      setActiveBook(rows.find((book) => book.id === activeBook.id) ?? null)
    }
  }, [activeBook])

  useEffect(() => {
    const controller = new AbortController()
    void restoreProjectIndex(controller.signal)
      .then(refreshBooks)
      .catch((error) => { if (!controller.signal.aborted) setNotice(error instanceof Error ? error.message : String(error)) })
      .finally(() => { if (!controller.signal.aborted) setLibraryLoading(false) })
    void checkHealth().then(setServerReady)
    void loadApiSettings().then(setSettings).catch(() => undefined)
    void loadVoiceSettings().then(setVoiceSettings).catch(() => undefined)
    return () => controller.abort()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!notice) return
    const timer = window.setTimeout(() => setNotice(''), 5000)
    return () => window.clearTimeout(timer)
  }, [notice])

  async function saveImportedBook(imported: ImportedBook) {
    const bookId = newId('book')
    const now = Date.now()
    const book: Book = {
      id: bookId,
      title: imported.title,
      author: imported.author,
      coverUrl: imported.chapters.slice().sort((a, b) => a.order - b.order)
        .flatMap((chapter) => chapter.blocks ?? [])
        .find((block) => block.type === 'image' && block.asset_url)?.asset_url ?? undefined,
      createdAt: now,
      updatedAt: now,
    }
    const chapterIdMap = new Map(imported.chapters.map((chapter) => [chapter.id, `${bookId}:${chapter.id}`]))
    const sentenceIdMap = new Map(imported.chapters.flatMap((chapter) =>
      (chapter.sentences ?? []).map((sentence) => [sentence.id, `${bookId}:${sentence.id}`] as const),
    ))
    const chapters: Chapter[] = imported.chapters.map((chapter) => ({
      id: chapterIdMap.get(chapter.id)!,
      bookId,
      title: chapter.title,
      order: chapter.order,
      text: chapter.text,
      blocks: chapter.blocks,
      originalHtmlUrl: chapter.original_html_url,
      status: chapter.text.trim() ? ((chapter.sentences?.length ?? 0) > 0 ? 'local-ready' : 'pending') : 'complete',
    }))
    const sentences: Sentence[] = imported.chapters.flatMap((chapter) => (chapter.sentences ?? []).map((sentence) => ({
      ...sentence,
      id: sentenceIdMap.get(sentence.id)!,
      chapter_id: chapterIdMap.get(chapter.id)!,
      explanation_status: 'idle',
    })))
    const tokens: Token[] = imported.chapters.flatMap((chapter) => (chapter.tokens ?? []).map((token) => ({
      ...token,
      id: `${bookId}:${token.id}`,
      sentence_id: sentenceIdMap.get(token.sentence_id)!,
      lexemeKey: makeLexemeKey(token),
    })))
    await db.transaction('rw', [db.books, db.chapters, db.sentences, db.tokens], async () => {
      await db.books.add(book)
      await db.chapters.bulkAdd(chapters)
      if (sentences.length) await db.sentences.bulkAdd(sentences)
      if (tokens.length) await db.tokens.bulkAdd(tokens)
    })
    await syncRecords({ books: [book], chapters, sentences, tokens })
    setShowImport(false)
    setActiveBook(book)
    setActiveChapter(chapters[0])
    if (imported.import_report) {
      const report = imported.import_report
      setNotice(`导入完成：${report.imported_sections} 节、${report.images} 张原图。打开需要阅读的章节后再单独切分。`)
    } else setNotice(`《${imported.title}》导入完成。点击“切分本章”后只处理当前章节。`)
    await refreshBooks()
  }

  async function openBook(book: Book) {
    if (loadingBookId) return
    navigationAbortRef.current?.abort()
    const controller = new AbortController()
    navigationAbortRef.current = controller
    setLoadingBookId(book.id)
    try {
      const chapters = await db.chapters.where('bookId').equals(book.id).sortBy('order')
      const preferred = chapters.find((chapter) => chapter.id === book.currentChapterId) ?? chapters[0]
      setActiveBook(book)
      setActiveChapter(null)
      setNotice('')
      if (!preferred) return
      setLoadingChapterId(preferred.id)
      const snapshot = await loadChapterData(preferred.id, controller.signal)
      if (!controller.signal.aborted) setActiveChapter(snapshot.chapter)
    } catch (error) {
      if (!controller.signal.aborted) setNotice(error instanceof Error ? error.message : String(error))
    } finally {
      if (navigationAbortRef.current === controller) navigationAbortRef.current = null
      if (!controller.signal.aborted) {
        setLoadingBookId(null)
        setLoadingChapterId(null)
      }
    }
  }

  async function selectChapter(chapter: Chapter) {
    if (!activeBook || loadingChapterId) return
    navigationAbortRef.current?.abort()
    const controller = new AbortController()
    navigationAbortRef.current = controller
    setLoadingChapterId(chapter.id)
    setActiveChapter(null)
    try {
      const snapshot = await loadChapterData(chapter.id, controller.signal)
      if (controller.signal.aborted) return
      const nextBook = { ...activeBook, currentChapterId: chapter.id, updatedAt: Date.now() }
      await db.books.put(nextBook)
      await syncRecords({ books: [nextBook] }, {}, controller.signal)
      setActiveBook(nextBook)
      setActiveChapter(snapshot.chapter)
    } catch (error) {
      if (!controller.signal.aborted) setNotice(error instanceof Error ? error.message : String(error))
    } finally {
      if (navigationAbortRef.current === controller) navigationAbortRef.current = null
      if (!controller.signal.aborted) setLoadingChapterId(null)
    }
  }

  async function storeChapterResult(chapter: Chapter, result: AnalyzeResponse, status: Chapter['status'], persist = true) {
    const oldSentences = await db.sentences.where('chapter_id').equals(chapter.id).toArray()
    const oldSentenceIds = oldSentences.map((sentence) => sentence.id)
    const oldTokens = await db.tokens.where('sentence_id').anyOf(oldSentenceIds).toArray()
    const oldAnnotations = await db.annotations.where('sentence_id').anyOf(oldSentenceIds).toArray()
    const tokens: Token[] = result.tokens.map((token) => ({ ...token, lexemeKey: makeLexemeKey(token) }))
    const storedLexemes: Lexeme[] = []
    const nextChapter = { ...chapter, status, error: result.warnings.join('\n') || undefined }
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
        if (!existing) {
          const next = { ...incoming, firstKana: incoming.reading[0] || '未', updatedAt: Date.now() }
          await db.lexemes.put(next)
          storedLexemes.push(next)
        }
      }
      await db.chapters.put(nextChapter)
    })
    if (persist) await syncRecords(
      {
        chapters: [nextChapter], sentences: result.sentences, tokens,
        annotations: result.annotations, contextSenses: result.context_senses, lexemes: storedLexemes,
      },
      {
        sentences: oldSentenceIds,
        tokens: oldTokens.map((token) => token.id),
        annotations: oldAnnotations.map((annotation) => annotation.id),
        contextSenses: oldTokens.map((token) => token.id),
      },
    )
    setActiveChapter((current) => current?.id === chapter.id ? { ...current, status, error: result.warnings.join('\n') || undefined } : current)
  }

  async function processChapter(chapter: Chapter, options: { quiet?: boolean; persist?: boolean; signal?: AbortSignal } = {}): Promise<boolean> {
    await db.chapters.update(chapter.id, { status: 'processing', error: undefined })
    const processingChapter = { ...chapter, status: 'processing' as const, error: undefined }
    if (options.persist ?? true) await syncRecords({ chapters: [processingChapter] }, {}, options.signal)
    setActiveChapter((current) => current?.id === chapter.id ? { ...current, status: 'processing' } : current)
    let localReady = false
    try {
      const segmented = await segmentChapter(chapter.id, chapter.text, chapter.blocks ?? [], settings, options.signal)
      await storeChapterResult(chapter, segmented, 'local-ready', options.persist ?? true)
      localReady = true
      if (!options.quiet) {
        setNotice(segmented.warnings.length
          ? `《${chapter.title}》已使用本地备用边界完成切分：${segmented.warnings.join('；')}`
          : `《${chapter.title}》已完成 AI 句界审校和分词。请选择句子后在右栏按需释义。`)
      }
      return true
    } catch (error) {
      if (options.signal?.aborted) {
        const restoredStatus = chapter.status === 'processing' ? 'pending' : chapter.status
        await db.chapters.update(chapter.id, { status: restoredStatus, error: undefined })
        if (options.persist ?? true) await syncRecords({ chapters: [{ ...chapter, status: restoredStatus, error: undefined }] })
        setActiveChapter((current) => current?.id === chapter.id ? { ...current, status: restoredStatus, error: undefined } : current)
        throw error
      }
      const message = error instanceof Error ? error.message : String(error)
      const fallbackStatus = localReady ? 'local-ready' : 'failed'
      await db.chapters.update(chapter.id, { status: fallbackStatus, error: message })
      if (options.persist ?? true) await syncRecords({ chapters: [{ ...chapter, status: fallbackStatus, error: message }] })
      setActiveChapter((current) => current?.id === chapter.id ? { ...current, status: fallbackStatus, error: message } : current)
      if (!options.quiet) setNotice(message)
      return localReady
    }
  }

  async function explainAndStoreSentence(sentence: Sentence, tokens: Token[], persist = true, signal?: AbortSignal) {
    const result = await explainSentence(
      { ...sentence, explanation_status: 'processing' },
      tokens.map(({ lexemeKey: _lexemeKey, ...token }) => token),
      settings,
      signal,
    )
    const nextSentence = result.sentences[0]
    const tokenIds = tokens.map((token) => token.id)
    const oldAnnotations = await db.annotations.where('sentence_id').equals(sentence.id).toArray()
    const storedLexemes: Lexeme[] = []
    await db.transaction('rw', [db.sentences, db.annotations, db.contextSenses, db.lexemes], async () => {
      await db.sentences.put(nextSentence)
      await db.annotations.where('sentence_id').equals(sentence.id).delete()
      await db.contextSenses.bulkDelete(tokenIds)
      if (result.annotations.length) await db.annotations.bulkPut(result.annotations)
      if (result.context_senses.length) await db.contextSenses.bulkPut(result.context_senses)
      for (const incoming of result.lexemes) {
        const existing = await db.lexemes.get(incoming.key)
        if (!existing?.correctedByUser) {
          const next = { ...incoming, firstKana: incoming.reading[0] || '未', updatedAt: Date.now() }
          await db.lexemes.put(next)
          storedLexemes.push(next)
        }
      }
    })
    if (persist) await syncRecords(
      {
        sentences: [nextSentence], annotations: result.annotations,
        contextSenses: result.context_senses, lexemes: storedLexemes,
      },
      { annotations: oldAnnotations.map((item) => item.id), contextSenses: tokenIds },
      signal,
    )
    return result
  }

  async function runBackground(kind: BackgroundJob['kind'], scope: BackgroundJob['scope'], chapter?: Chapter) {
    if (backgroundJob?.running || !activeBook) return
    const controller = new AbortController()
    backgroundAbortRef.current = controller
    const ensureNotCanceled = () => controller.signal.throwIfAborted()
    const targets = chapter
      ? [chapter]
      : await db.chapters.where('bookId').equals(activeBook.id).sortBy('order')
    const jobName = kind === 'segment' ? '切分' : '翻译'
    let failed = 0
    setBackgroundJob({ kind, scope, label: `准备后台${jobName}`, completed: 0, total: targets.length, failed: 0, running: true })

    try {
      const readyChapters: Chapter[] = []
      for (let index = 0; index < targets.length; index += 1) {
        ensureNotCanceled()
        const targetIndex = (await db.chapters.get(targets[index].id)) ?? targets[index]
        const loaded = await loadChapterData(targetIndex.id, controller.signal)
        const target = loaded.chapter
        ensureNotCanceled()
        const sentenceCount = await db.sentences.where('chapter_id').equals(target.id).count()
        let ready = sentenceCount > 0 && target.status !== 'pending' && target.status !== 'failed'
        if (!ready && target.text.trim()) {
          setBackgroundJob((current) => current && ({ ...current, label: `后台切分：${target.title}`, completed: index, total: targets.length, failed }))
          ready = await processChapter(target, { quiet: true, persist: true, signal: controller.signal })
          if (!ready) failed += 1
        }
        if (ready) readyChapters.push(target)
        setBackgroundJob((current) => current && ({ ...current, completed: index + 1, failed }))
      }

      if (kind === 'translate') {
        const pendingSentences: Sentence[] = []
        for (const target of readyChapters) {
          ensureNotCanceled()
          const rows = await db.sentences.where('chapter_id').equals(target.id).sortBy('start')
          pendingSentences.push(...rows.filter((sentence) => sentence.explanation_status !== 'complete'))
        }
        setBackgroundJob((current) => current && ({ ...current, label: '准备后台逐句翻译', completed: 0, total: pendingSentences.length, failed }))
        for (let index = 0; index < pendingSentences.length; index += 1) {
          ensureNotCanceled()
          const sentence = pendingSentences[index]
          const target = targets.find((item) => item.id === sentence.chapter_id)
          setBackgroundJob((current) => current && ({ ...current, label: `后台翻译：${target?.title ?? '当前章节'}`, completed: index, failed }))
          const sentenceTokens = await db.tokens.where('sentence_id').equals(sentence.id).toArray()
          try {
            await db.sentences.update(sentence.id, { explanation_status: 'processing', error: null })
            await explainAndStoreSentence(sentence, sentenceTokens, true, controller.signal)
          } catch (error) {
            if (controller.signal.aborted) {
              await db.sentences.update(sentence.id, { explanation_status: 'idle', error: null })
              throw error
            }
            failed += 1
            const message = error instanceof Error ? error.message : String(error)
            await db.sentences.update(sentence.id, { explanation_status: 'failed', error: message })
            const failedSentence = { ...sentence, explanation_status: 'failed' as const, error: message }
            await syncRecords({ sentences: [failedSentence] })
          }
          setBackgroundJob((current) => current && ({ ...current, completed: index + 1, failed }))
        }
      }

      setDataRevision((value) => value + 1)
      setBackgroundJob((current) => current && ({ ...current, label: `后台${jobName}完成`, running: false, failed }))
      setNotice(failed ? `后台${jobName}完成，${failed} 项失败，可稍后重试。` : `后台${jobName}完成。`)
    } catch (error) {
      setDataRevision((value) => value + 1)
      if (controller.signal.aborted) {
        setBackgroundJob((current) => current && ({ ...current, label: `后台${jobName}已取消`, running: false }))
        setNotice(`后台${jobName}已取消，已完成的结果已保存。`)
      } else {
        const message = error instanceof Error ? error.message : String(error)
        setBackgroundJob((current) => current && ({ ...current, label: `后台${jobName}已停止`, running: false, failed: current.failed + 1 }))
        setNotice(message)
      }
    } finally {
      if (backgroundAbortRef.current === controller) backgroundAbortRef.current = null
    }
  }

  function cancelBackground() {
    if (!backgroundAbortRef.current || !backgroundJob?.running) return
    backgroundAbortRef.current.abort()
    setBackgroundJob((current) => current && ({ ...current, label: '正在取消后台任务…' }))
  }

  async function deleteBook(book: Book) {
    if (book.customCover) await deleteBookCover(book.id).catch(() => undefined)
    await removeBook(book.id)
    if (activeBook?.id === book.id) {
      setActiveBook(null)
      setActiveChapter(null)
    }
    await refreshBooks()
  }

  async function changeBookCover(book: Book, file: File) {
    try {
      const coverUrl = await uploadBookCover(book.id, file)
      const next = { ...book, coverUrl, customCover: true, updatedAt: Date.now() }
      await db.books.put(next)
      await syncRecords({ books: [next] })
      setActiveBook((current) => current?.id === book.id ? next : current)
      await refreshBooks()
      setNotice(`《${book.title}》的封面已更新。`)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    }
  }

  function bounceLogoAndOpenLibrary() {
    navigationAbortRef.current?.abort()
    navigationAbortRef.current = null
    setLoadingBookId(null)
    setLoadingChapterId(null)
    const audio = new Audio('/bingdu-logo-click.wav')
    logoAudiosRef.current.add(audio)
    audio.addEventListener('ended', () => logoAudiosRef.current.delete(audio), { once: true })
    void audio.play().catch(() => logoAudiosRef.current.delete(audio))
    setLogoBouncing(false)
    window.requestAnimationFrame(() => setLogoBouncing(true))
    setActiveBook(null)
    setActiveChapter(null)
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="brand" onClick={bounceLogoAndOpenLibrary}>
          <span className={`brand-mark ${logoBouncing ? 'is-bouncing' : ''}`} onAnimationEnd={() => setLogoBouncing(false)}><img src="/bingdu-logo.png" alt="" /></span>
          <span><strong>冰读</strong><small>baka都能用的日语学习阅读器</small></span>
        </button>
        <div className="top-actions">
          <span className={`server-dot ${serverReady ? 'ready' : 'down'}`} title={serverReady ? '本地服务正常' : '本地服务未连接'} />
          <button className="button ghost" onClick={() => setShowStudyData(true)}>词库与词卡</button>
          <button className="button ghost" onClick={() => setShowVoiceSettings(true)}>配音设置</button>
          <button className="button ghost" onClick={() => setShowSettings(true)}>AI 设置</button>
          <button className="button primary" onClick={() => setShowImport(true)}>导入书籍</button>
        </div>
      </header>

      {notice && <div className="notice" role="status">{notice}<button onClick={() => setNotice('')}>×</button></div>}

      {!activeBook ? (
        <Library books={books} loading={libraryLoading} loadingBookId={loadingBookId} onOpen={openBook} onDelete={deleteBook} onChangeCover={changeBookCover} onImport={() => setShowImport(true)} />
      ) : (
        <Workspace
          book={activeBook}
          activeChapter={activeChapter}
          loadingChapterId={loadingChapterId}
          onSelectChapter={(chapter) => void selectChapter(chapter)}
          onProcessChapter={processChapter}
          onExplainSentence={explainAndStoreSentence}
          backgroundJob={backgroundJob}
          onCancelBackground={cancelBackground}
          dataRevision={dataRevision}
          onBackgroundBook={(kind) => void runBackground(kind, 'book')}
          onBackgroundChapter={(kind, chapter) => void runBackground(kind, 'chapter', chapter)}
          onNotice={setNotice}
        />
      )}

      {showImport && <ImportDialog onClose={() => setShowImport(false)} onImported={saveImportedBook} />}
      {showStudyData && <StudyDataDialog onClose={() => setShowStudyData(false)} />}
      {showVoiceSettings && (
        <VoiceSettingsDialog
          value={voiceSettings}
          onClose={() => setShowVoiceSettings(false)}
          onSave={async (next, template) => {
            let saved = await saveVoiceSettings(next)
            if (template) saved = await uploadVoiceTemplate(template)
            setVoiceSettings(saved)
            setShowVoiceSettings(false)
            setNotice(saved.ready
              ? `配音设置已保存${saved.characterName ? `，当前角色：${saved.characterName}` : ''}。`
              : '设置已保存；还需要有效的 YMM4 路径和配音模板。')
          }}
        />
      )}
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
              : 'Base URL 和模型已保存；AI 句界审校和逐句释义前还需要填写 API Key。')
          }}
        />
      )}
    </div>
  )
}

function Library({ books, loading, loadingBookId, onOpen, onDelete, onChangeCover, onImport }: {
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
        <p>电子书、学习数据和阅读进度保存在本地项目中。</p>
      </div>
      {loading ? (
        <section className="empty-state loading-state" aria-live="polite">
          <div className="loading-spinner" />
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
                <span aria-hidden="true">読む</span>
                {book.coverUrl && <img src={book.coverUrl} alt="" onError={(event) => { event.currentTarget.hidden = true }} />}
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
        <p>《{pendingDelete.title}》的正文、阅读进度、逐句结果和词卡将从本地项目中删除。</p>
        <div className="modal-actions">
          <button className="button" autoFocus disabled={deleting} onClick={() => setPendingDelete(null)}>取消</button>
          <button className="button danger-solid" disabled={deleting} onClick={() => void confirmDelete()}>{deleting ? '正在删除…' : '确认删除'}</button>
        </div>
      </section>
    </div>}
    </>
  )
}

function Workspace({ book, activeChapter, loadingChapterId, onSelectChapter, onProcessChapter, onExplainSentence, backgroundJob, dataRevision, onBackgroundBook, onBackgroundChapter, onCancelBackground, onNotice }: {
  book: Book
  activeChapter: Chapter | null
  loadingChapterId: string | null
  onSelectChapter: (chapter: Chapter) => void
  onProcessChapter: (chapter: Chapter) => void
  onExplainSentence: (sentence: Sentence, tokens: Token[]) => Promise<AnalyzeResponse>
  backgroundJob: BackgroundJob | null
  dataRevision: number
  onBackgroundBook: (kind: BackgroundJob['kind']) => void
  onBackgroundChapter: (kind: BackgroundJob['kind'], chapter: Chapter) => void
  onCancelBackground: () => void
  onNotice: (message: string) => void
}) {
  const [chapters, setChapters] = useState<Chapter[]>([])
  const load = useCallback(async () => {
    setChapters(await db.chapters.where('bookId').equals(book.id).sortBy('order'))
  }, [book.id, activeChapter?.status, backgroundJob?.completed, dataRevision])
  useEffect(() => { void load() }, [load])

  return (
    <div className="workspace">
      <aside className="chapter-nav">
        <div className="book-heading"><small>正在阅读</small><h2>{book.title}</h2>{book.author && <p>{book.author}</p>}</div>
        <div className="book-background-actions">
          <button className="button small" disabled={backgroundJob?.running} onClick={() => onBackgroundBook('segment')}>后台切分全书</button>
          <button className="button small" disabled={backgroundJob?.running} onClick={() => onBackgroundBook('translate')}>后台翻译全书</button>
        </div>
        {backgroundJob && <BackgroundProgress job={backgroundJob} onCancel={onCancelBackground} />}
        <nav aria-label="章节">
          {chapters.map((chapter) => (
            <button key={chapter.id} disabled={Boolean(loadingChapterId)} className={`chapter-item ${activeChapter?.id === chapter.id ? 'active' : ''}`} onClick={() => onSelectChapter(chapter)}>
              <span>{chapter.title}</span>{loadingChapterId === chapter.id ? <small className="status">加载中…</small> : <StatusBadge status={chapter.status} />}
            </button>
          ))}
        </nav>
      </aside>
      <main className="reading-stage">
        {!activeChapter && loadingChapterId ? <section className="processing-panel loading-chapter" aria-live="polite"><div className="loading-spinner" /><p className="eyebrow">按章读取</p><h1>正在加载章节</h1><p>正在读取本章句子、分词和注释。点击左上角头像可以安全返回书架。</p></section> : !activeChapter ? null : (
          <Reader
            book={book}
            chapter={activeChapter}
            onNotice={onNotice}
            onRetry={() => onProcessChapter(activeChapter)}
            onExplainSentence={onExplainSentence}
            backgroundJob={backgroundJob}
            dataRevision={dataRevision}
            onBackground={(kind) => onBackgroundChapter(kind, activeChapter)}
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

function Reader({ book, chapter, onNotice, onRetry, onExplainSentence, backgroundJob, dataRevision, onBackground }: {
  book: Book
  chapter: Chapter
  onNotice: (message: string) => void
  onRetry: () => void
  onExplainSentence: (sentence: Sentence, tokens: Token[]) => Promise<AnalyzeResponse>
  backgroundJob: BackgroundJob | null
  dataRevision: number
  onBackground: (kind: BackgroundJob['kind']) => void
}) {
  const [sentences, setSentences] = useState<Sentence[]>([])
  const [tokens, setTokens] = useState<Token[]>([])
  const [annotations, setAnnotations] = useState<Annotation[]>([])
  const [contextSenses, setContextSenses] = useState<ContextSense[]>([])
  const [selectedSentenceId, setSelectedSentenceId] = useState<string | null>(null)
  const [selectedTokenId, setSelectedTokenId] = useState<string | null>(null)
  const [lexemesByToken, setLexemesByToken] = useState<Record<string, Lexeme | null>>({})
  const [explaining, setExplaining] = useState(false)
  const [explainError, setExplainError] = useState('')
  const [showFurigana, setShowFurigana] = useState(true)
  const [showAnnotations, setShowAnnotations] = useState(true)
  const [showImages, setShowImages] = useState(false)
  const [viewMode, setViewMode] = useState<'study' | 'original'>('study')
  const [readerLoading, setReaderLoading] = useState(true)
  const [visibleSentenceCount, setVisibleSentenceCount] = useState(120)
  const [voiceJob, setVoiceJob] = useState<VoiceJob | null>(null)
  const [voicePlaying, setVoicePlaying] = useState(false)
  const loadMoreRef = useRef<HTMLDivElement | null>(null)
  const voiceJobIdRef = useRef<string | null>(null)
  const voicePollAbortRef = useRef<AbortController | null>(null)
  const sentenceAudioRef = useRef<HTMLAudioElement | null>(null)

  useEffect(() => {
    let canceled = false
    setReaderLoading(true)
    void (async () => {
      const nextSentences = await db.sentences.where('chapter_id').equals(chapter.id).sortBy('start')
      const ids = nextSentences.map((sentence) => sentence.id)
      const nextTokens = ids.length ? await db.tokens.where('sentence_id').anyOf(ids).toArray() : []
      const nextAnnotations = ids.length ? await db.annotations.where('sentence_id').anyOf(ids).toArray() : []
      const tokenIds = nextTokens.map((token) => token.id)
      const nextSenses = tokenIds.length ? await db.contextSenses.where('token_id').anyOf(tokenIds).toArray() : []
      if (canceled) return
      setSentences(nextSentences)
      setTokens(nextTokens)
      setAnnotations(nextAnnotations)
      setContextSenses(nextSenses)
      const restored = nextSentences.find((sentence) => sentence.id === book.currentSentenceId)?.id
      const restoredIndex = restored ? nextSentences.findIndex((sentence) => sentence.id === restored) : -1
      setVisibleSentenceCount(Math.max(120, restoredIndex + 30))
      setSelectedSentenceId(restored ?? nextSentences[0]?.id ?? null)
      setSelectedTokenId(null)
      setLexemesByToken({})
      setExplainError('')
      setReaderLoading(false)
    })().catch((error) => {
      if (!canceled) {
        setReaderLoading(false)
        onNotice(error instanceof Error ? error.message : String(error))
      }
    })
    return () => { canceled = true }
  }, [book.currentSentenceId, chapter.id, chapter.status, dataRevision])

  useEffect(() => () => {
    voicePollAbortRef.current?.abort()
    sentenceAudioRef.current?.pause()
    if (voiceJobIdRef.current) void cancelVoiceJob(voiceJobIdRef.current).catch(() => undefined)
  }, [])

  const tokensBySentence = useMemo(() => {
    const map = new Map<string, Token[]>()
    for (const token of tokens) map.set(token.sentence_id, [...(map.get(token.sentence_id) ?? []), token])
    for (const values of map.values()) values.sort((a, b) => a.start - b.start)
    return map
  }, [tokens])
  const selectedSentence = sentences.find((sentence) => sentence.id === selectedSentenceId) ?? null
  const selectedToken = tokens.find((token) => token.id === selectedTokenId) ?? null
  const selectedSentenceTokens = selectedSentenceId ? (tokensBySentence.get(selectedSentenceId) ?? []) : []
  const selectedContentTokens = selectedSentenceTokens.filter((token) => token.is_content)
  const lexeme = selectedTokenId ? (lexemesByToken[selectedTokenId] ?? null) : null
  const currentSense = contextSenses.find((sense) => sense.token_id === selectedTokenId)
  const currentNotes = annotations.filter((annotation) => annotation.sentence_id === selectedSentenceId)
  const sentenceExplained = selectedSentence?.explanation_status === 'complete'
  const visibleSentences = useMemo(() => sentences.slice(0, visibleSentenceCount), [sentences, visibleSentenceCount])
  const visibleSentenceIds = useMemo(() => new Set(visibleSentences.map((sentence) => sentence.id)), [visibleSentences])
  const sentencesByBlock = useMemo(() => {
    const map = new Map<string, Sentence[]>()
    for (const block of chapter.blocks ?? []) {
      if (block.type === 'image' || block.type === 'page-break' || block.type === 'separator') continue
      map.set(block.id, sentences.filter((sentence) => sentence.start >= block.start && sentence.end <= block.end))
    }
    return map
  }, [chapter.blocks, sentences])

  useEffect(() => {
    const target = loadMoreRef.current
    if (!target || visibleSentenceCount >= sentences.length) return
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setVisibleSentenceCount((value) => Math.min(value + 120, sentences.length))
      }
    }, { rootMargin: '500px 0px' })
    observer.observe(target)
    return () => observer.disconnect()
  }, [sentences.length, visibleSentenceCount])

  useEffect(() => {
    if (!selectedSentenceId) return
    const controller = new AbortController()
    const rows = tokensBySentence.get(selectedSentenceId)?.filter((token) => token.is_content) ?? []
    void Promise.all(rows.map(async (token) => {
      const personal = await db.lexemes.get(token.lexemeKey)
      const value = personal ?? await lookupDictionary(token.lemma, token.reading, token.surface, controller.signal).catch(() => null)
      return [token.id, value] as const
    })).then((values) => { if (!controller.signal.aborted) setLexemesByToken(Object.fromEntries(values)) })
    return () => controller.abort()
  }, [selectedSentenceId, tokensBySentence, contextSenses])

  useEffect(() => {
    voicePollAbortRef.current?.abort()
    voicePollAbortRef.current = null
    sentenceAudioRef.current?.pause()
    sentenceAudioRef.current = null
    setVoicePlaying(false)
    setVoiceJob(null)
    const runningId = voiceJobIdRef.current
    voiceJobIdRef.current = null
    if (runningId) void cancelVoiceJob(runningId).catch(() => undefined)
  }, [selectedSentenceId])

  function playVoiceAudio(url: string) {
    sentenceAudioRef.current?.pause()
    const audio = new Audio(`${url}${url.includes('?') ? '&' : '?'}v=${Date.now()}`)
    sentenceAudioRef.current = audio
    audio.addEventListener('play', () => setVoicePlaying(true))
    audio.addEventListener('pause', () => setVoicePlaying(false))
    audio.addEventListener('ended', () => setVoicePlaying(false))
    void audio.play().catch((error) => onNotice(error instanceof Error ? error.message : String(error)))
  }

  async function waitForVoice(initial: VoiceJob) {
    let current = initial
    setVoiceJob(current)
    if (current.status === 'complete' && current.audioUrl) {
      voiceJobIdRef.current = null
      playVoiceAudio(current.audioUrl)
      return
    }
    const controller = new AbortController()
    voicePollAbortRef.current?.abort()
    voicePollAbortRef.current = controller
    while (!controller.signal.aborted && (current.status === 'queued' || current.status === 'running')) {
      await new Promise((resolve) => window.setTimeout(resolve, 500))
      current = await loadVoiceJob(current.id, controller.signal)
      setVoiceJob(current)
    }
    if (current.status === 'complete' && current.audioUrl) {
      voiceJobIdRef.current = null
      playVoiceAudio(current.audioUrl)
    }
  }

  async function synthesizeVoice(force = false) {
    if (!selectedSentence || voiceJob?.status === 'queued' || voiceJob?.status === 'running') return
    try {
      const job = await startVoiceJob(selectedSentence.original, force)
      voiceJobIdRef.current = job.status === 'complete' ? null : job.id
      await waitForVoice(job)
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return
      const message = error instanceof Error ? error.message : String(error)
      setVoiceJob({ id: '', status: 'failed', message, cached: false })
    }
  }

  async function cancelCurrentVoice() {
    voicePollAbortRef.current?.abort()
    const jobId = voiceJobIdRef.current
    if (!jobId) return
    try {
      const canceled = await cancelVoiceJob(jobId)
      setVoiceJob(canceled)
    } finally {
      voiceJobIdRef.current = null
    }
  }

  function toggleVoicePlayback() {
    const audio = sentenceAudioRef.current
    if (!audio) return
    if (audio.paused) void audio.play()
    else audio.pause()
  }

  async function selectSentence(sentence: Sentence) {
    setSelectedSentenceId(sentence.id)
    setSelectedTokenId(null)
    const nextBook = { ...book, currentChapterId: chapter.id, currentSentenceId: sentence.id, updatedAt: Date.now() }
    await db.books.put(nextBook)
    void syncRecords({ books: [nextBook] })
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
    void syncRecords({ lexemes: [next] })
    setLexemesByToken((current) => ({ ...current, [selectedToken.id]: next }))
  }

  async function explainCurrentSentence() {
    if (!selectedSentence || explaining) return
    setExplaining(true)
    setExplainError('')
    setSentences((current) => current.map((sentence) => sentence.id === selectedSentence.id
      ? { ...sentence, explanation_status: 'processing', error: null }
      : sentence))
    try {
      const result = await onExplainSentence(selectedSentence, selectedSentenceTokens)
      const nextSentence = result.sentences[0]
      const tokenIds = selectedSentenceTokens.map((token) => token.id)
      setSentences((current) => current.map((sentence) => sentence.id === nextSentence.id ? nextSentence : sentence))
      setAnnotations((current) => [...current.filter((note) => note.sentence_id !== selectedSentence.id), ...result.annotations])
      setContextSenses((current) => [...current.filter((sense) => !tokenIds.includes(sense.token_id)), ...result.context_senses])
      const nextLexemes = { ...lexemesByToken }
      for (const token of selectedContentTokens) {
        const incoming = result.lexemes.find((item) => item.key === token.lexemeKey)
        const existing = await db.lexemes.get(token.lexemeKey)
        if (existing) nextLexemes[token.id] = existing
        else if (incoming) nextLexemes[token.id] = { ...incoming, firstKana: incoming.reading[0] || '未', updatedAt: Date.now() }
      }
      setLexemesByToken(nextLexemes)
      onNotice(result.warnings.length ? `本句释义完成；${result.warnings.join('；')}` : '本句释义、词典结果与学习注释已保存。')
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setExplainError(message)
      const failed = { ...selectedSentence, explanation_status: 'failed' as const, error: message }
      await db.sentences.put(failed)
      setSentences((current) => current.map((sentence) => sentence.id === failed.id ? failed : sentence))
      void syncRecords({ sentences: [failed] })
    } finally {
      setExplaining(false)
    }
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
    await syncRecords({ cards: [card] })
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
    const blockSentences = sentencesByBlock.get(block.id) ?? []
    const matches = blockSentences.filter((sentence) => visibleSentenceIds.has(sentence.id))
    if (blockSentences.length && !matches.length) return null
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
            {chapter.originalHtmlUrl && <button className={`mode-button ${viewMode === 'original' ? 'active' : ''}`} onClick={() => setViewMode(viewMode === 'study' ? 'original' : 'study')}>{viewMode === 'study' ? '原书预览' : '冰读模式'}</button>}
            <Toggle label="振假名" value={showFurigana} onChange={setShowFurigana} />
            <Toggle label="插图" value={showImages} onChange={setShowImages} />
            <Toggle label="注释" value={showAnnotations} onChange={setShowAnnotations} />
          </div>
        </div>
        <div className="chapter-background-actions" aria-label="本章后台处理">
          <button className="button small" disabled={backgroundJob?.running || chapter.status === 'processing'} onClick={() => onBackground('segment')}>后台切分本章</button>
          <button className="button small" disabled={backgroundJob?.running || chapter.status === 'processing'} onClick={() => onBackground('translate')}>后台翻译本章</button>
        </div>
        {(chapter.status === 'pending' || chapter.status === 'failed') && <div className="inline-warning">本章尚未切分。<button disabled={backgroundJob?.running} onClick={onRetry}>{chapter.status === 'failed' ? '重试切分' : '切分本章'}</button></div>}
        {chapter.status === 'processing' && <div className="inline-warning">正在切分本章。</div>}
        {readerLoading ? <div className="reader-loading" aria-live="polite"><div className="loading-spinner" /><span>正在整理本章内容…</span></div> : viewMode === 'original' && chapter.originalHtmlUrl
          ? <iframe className="original-preview" sandbox="" src={chapter.originalHtmlUrl} title={`${chapter.title} 原书预览`} />
          : <div className="japanese-text" lang="ja">
              {chapter.blocks?.length ? chapter.blocks.map(renderBlock) : sentences.length ? visibleSentences.map(renderSentence) : chapter.text}
              {visibleSentenceCount < sentences.length && <div ref={loadMoreRef} className="load-more-sentinel">继续加载 · {visibleSentenceCount}/{sentences.length}</div>}
            </div>}
      </article>
      <aside className="study-panel">
        <p className="panel-kicker">当前句</p>
        {selectedSentence ? (
          <>
            <p className="panel-original" lang="ja">{selectedSentence.original}</p>
            <button className="button primary full explain-button" disabled={explaining} onClick={() => void explainCurrentSentence()}>
              {explaining ? '正在释义…' : sentenceExplained ? '重新释义本句' : '释义本句'}
            </button>
            <div className="voice-controls" aria-live="polite">
              <div className="voice-actions">
                <button className="button full" disabled={voiceJob?.status === 'queued' || voiceJob?.status === 'running'} onClick={() => void synthesizeVoice(false)}>
                  {voiceJob?.status === 'queued' ? '等待配音…' : voiceJob?.status === 'running' ? '正在配音…' : voiceJob?.status === 'complete' ? '再次播放' : '配音本句'}
                </button>
                {voiceJob?.status === 'complete' && <button className="button small" onClick={toggleVoicePlayback}>{voicePlaying ? '暂停' : '播放'}</button>}
                {(voiceJob?.status === 'queued' || voiceJob?.status === 'running') && <button className="button small" onClick={() => void cancelCurrentVoice()}>取消</button>}
                {voiceJob?.status === 'complete' && <button className="text-button" onClick={() => void synthesizeVoice(true)}>重新生成</button>}
              </div>
              {voiceJob && <small className={`voice-status ${voiceJob.status}`}>{voiceJob.message}{voiceJob.cached ? ' · 缓存' : ''}</small>}
            </div>
            {(explainError || selectedSentence.explanation_status === 'failed') && <div className="error-box">{explainError || selectedSentence.error}</div>}
            {sentenceExplained && selectedSentence.translation_zh && <section className="panel-section"><h3>句意</h3><p>{selectedSentence.translation_zh}</p></section>}
            {selectedToken && <DictionaryCard token={selectedToken} lexeme={lexeme} contextGloss={currentSense?.gloss_zh ?? ''} onSave={saveLexeme} onAddCard={addCard} />}
            {showAnnotations && currentNotes.length > 0 && <section className="panel-section"><h3>学习注释</h3>{currentNotes.map((note) => <div className="annotation" key={note.id}><span>{ANNOTATION_LABELS[note.type]}</span><strong lang="ja">{note.quote}</strong><p>{note.explanation_zh}</p></div>)}</section>}
          </>
        ) : <p className="muted">选择一个句子开始冰读。</p>}
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
  const [wordVoice, setWordVoice] = useState<VoiceJob | null>(null)
  const wordVoiceAbortRef = useRef<AbortController | null>(null)
  const wordVoiceJobIdRef = useRef<string | null>(null)
  const wordAudioRef = useRef<HTMLAudioElement | null>(null)
  useEffect(() => { setValue(lexeme?.senses_zh.join('\n') ?? '') }, [lexeme])
  useEffect(() => {
    wordVoiceAbortRef.current?.abort()
    wordAudioRef.current?.pause()
    setWordVoice(null)
  }, [token.id])
  useEffect(() => () => {
    wordVoiceAbortRef.current?.abort()
    wordAudioRef.current?.pause()
    if (wordVoiceJobIdRef.current) void cancelVoiceJob(wordVoiceJobIdRef.current).catch(() => undefined)
  }, [])

  function playWord(url: string) {
    wordAudioRef.current?.pause()
    const audio = new Audio(url)
    wordAudioRef.current = audio
    void audio.play()
  }

  async function voiceWord(force = false) {
    if (!force && wordVoice?.status === 'complete' && wordVoice.audioUrl) {
      playWord(wordVoice.audioUrl)
      return
    }
    try {
      let job = await startVoiceJob(token.surface, force)
      setWordVoice(job)
      wordVoiceJobIdRef.current = job.status === 'complete' ? null : job.id
      if (job.status === 'complete' && job.audioUrl) {
        playWord(job.audioUrl)
        return
      }
      const controller = new AbortController()
      wordVoiceAbortRef.current = controller
      while (!controller.signal.aborted && (job.status === 'queued' || job.status === 'running')) {
        await new Promise((resolve) => window.setTimeout(resolve, 500))
        job = await loadVoiceJob(job.id, controller.signal)
        setWordVoice(job)
      }
      if (job.status === 'complete' && job.audioUrl) {
        wordVoiceJobIdRef.current = null
        playWord(job.audioUrl)
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return
      setWordVoice({ id: '', status: 'failed', message: error instanceof Error ? error.message : String(error), cached: false })
      wordVoiceJobIdRef.current = null
    }
  }
  return (
    <section className="dictionary-card">
      <div className="dictionary-head"><div><small>{token.part_of_speech}</small><h2>{token.lemma}</h2><p>{toHiragana(token.reading)}</p></div><div className="dictionary-actions"><button className="button small" disabled={wordVoice?.status === 'queued' || wordVoice?.status === 'running'} onClick={() => void voiceWord()}>{wordVoice?.status === 'queued' || wordVoice?.status === 'running' ? '配音中…' : wordVoice?.status === 'complete' ? '再次播放' : '播放读音'}</button>{wordVoice?.status === 'complete' && <button className="text-button" onClick={() => void voiceWord(true)}>重新生成</button>}<button className="button small" onClick={() => void onAddCard()}>加入词卡</button></div></div>
      {wordVoice?.status === 'failed' && <small className="voice-status failed">{wordVoice.message}</small>}
      {contextGloss && <div className="context-gloss"><small>当前语境选择</small><p>{contextGloss}</p></div>}
      <div className="dictionary-senses">
        <div className="section-title"><h3>日中词典</h3><button className="text-button" onClick={() => setEditing(!editing)}>{editing ? '取消' : '修正'}</button></div>
        {editing ? <><textarea value={value} onChange={(event) => setValue(event.target.value)} rows={4} /><button className="button primary small" onClick={() => { void onSave(value.split('\n').map((x) => x.trim()).filter(Boolean)); setEditing(false) }}>保存到个人词库</button></> : <>{lexeme?.senses_zh.length ? <ol>{lexeme.senses_zh.map((sense, index) => <li key={index}>{sense}</li>)}</ol> : <p className="muted">本地词典未命中。点击“释义本句”后由 AI 补充并保存到个人词库。</p>}<small className="source">来源：{lexeme?.source ?? '等待释义'}</small></>}
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
  const [cardVoice, setCardVoice] = useState<{ cardId: string; job: VoiceJob } | null>(null)
  const cardVoiceAbortRef = useRef<AbortController | null>(null)
  const cardVoiceJobIdRef = useRef<string | null>(null)
  const cardAudioRef = useRef<HTMLAudioElement | null>(null)

  function playCardAudio(url: string) {
    cardAudioRef.current?.pause()
    const audio = new Audio(url)
    cardAudioRef.current = audio
    void audio.play()
  }

  async function voiceCard(card: StudyCard, force = false) {
    if (!force && cardVoice?.cardId === card.id && cardVoice.job.status === 'complete' && cardVoice.job.audioUrl) {
      playCardAudio(cardVoice.job.audioUrl)
      return
    }
    cardVoiceAbortRef.current?.abort()
    const previousJobId = cardVoiceJobIdRef.current
    if (previousJobId) void cancelVoiceJob(previousJobId).catch(() => undefined)
    try {
      let job = await startVoiceJob(card.surface, force)
      setCardVoice({ cardId: card.id, job })
      cardVoiceJobIdRef.current = job.status === 'complete' ? null : job.id
      if (job.status === 'complete' && job.audioUrl) {
        playCardAudio(job.audioUrl)
        return
      }
      const controller = new AbortController()
      cardVoiceAbortRef.current = controller
      while (!controller.signal.aborted && (job.status === 'queued' || job.status === 'running')) {
        await new Promise((resolve) => window.setTimeout(resolve, 500))
        job = await loadVoiceJob(job.id, controller.signal)
        setCardVoice({ cardId: card.id, job })
      }
      if (job.status === 'complete' && job.audioUrl) {
        cardVoiceJobIdRef.current = null
        playCardAudio(job.audioUrl)
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return
      setCardVoice({ cardId: card.id, job: { id: '', status: 'failed', message: error instanceof Error ? error.message : String(error), cached: false } })
      cardVoiceJobIdRef.current = null
    }
  }

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
    const controller = new AbortController()
    void loadStudyData(controller.signal).then((snapshot) => {
      if (controller.signal.aborted) return
      setLexemes([...snapshot.lexemes].sort((a, b) => a.reading.localeCompare(b.reading, 'ja')))
      setCards([...snapshot.cards].sort((a, b) => b.createdAt - a.createdAt))
    }).catch((error) => {
      if (!controller.signal.aborted) setDictionaryNotice(error instanceof Error ? error.message : String(error))
    })
    return () => controller.abort()
  }, [])

  useEffect(() => () => {
    cardVoiceAbortRef.current?.abort()
    cardAudioRef.current?.pause()
    if (cardVoiceJobIdRef.current) void cancelVoiceJob(cardVoiceJobIdRef.current).catch(() => undefined)
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
          <button className="button small" onClick={() => downloadJson(`冰读个人词库-${new Date().toISOString().slice(0, 10)}.json`, { format: 'nichidoku-lexicon-v1', exportedAt: new Date().toISOString(), lexemes })}>导出共享</button>
        </div>
        {dictionaryNotice && <p className="muted">{dictionaryNotice}</p>}
        <div className="data-list">{filteredLexemes.length ? filteredLexemes.map((item) => <article key={item.key}>
          <div><strong lang="ja">{item.lemma}</strong><span>{toHiragana(item.reading)} · {item.part_of_speech}</span></div>
          <p>{item.senses_zh.join('；')}</p><small>{item.firstKana} · {item.source}</small>
        </article>) : <p className="muted">还没有匹配的词条。</p>}</div>
      </> : <>
        <div className="data-tools">
          <p>词卡保留书籍、章节、原句和当前语境义。</p>
          <button className="button small" onClick={() => downloadJson(`冰读上下文词卡-${new Date().toISOString().slice(0, 10)}.json`, { format: 'nichidoku-cards-v1', exportedAt: new Date().toISOString(), cards })}>导出词卡</button>
        </div>
        <div className="data-list cards">{cards.length ? cards.map((card) => <article key={card.id}>
          <div className="card-title-row"><div className="card-word"><strong lang="ja">{card.surface}</strong><span>{toHiragana(card.reading)}</span></div><div className="card-voice-actions"><button className="button small" disabled={cardVoice?.cardId === card.id && (cardVoice.job.status === 'queued' || cardVoice.job.status === 'running')} onClick={() => void voiceCard(card)}>{cardVoice?.cardId === card.id && (cardVoice.job.status === 'queued' || cardVoice.job.status === 'running') ? '配音中…' : cardVoice?.cardId === card.id && cardVoice.job.status === 'complete' ? '再次播放' : '播放读音'}</button>{cardVoice?.cardId === card.id && cardVoice.job.status === 'complete' && <button className="text-button" onClick={() => void voiceCard(card, true)}>重新生成</button>}</div></div><p>{card.glossZh}</p>
          <blockquote lang="ja">{card.sentence}</blockquote><small>{card.sourceLabel}</small>
          {cardVoice?.cardId === card.id && cardVoice.job.status === 'failed' && <small className="voice-status failed">{cardVoice.job.message}</small>}
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
      <p className="muted">导入只解析书籍结构、正文和图片，不等待全书 AI 处理。阅读时按需切分当前章节，再逐句释义。</p>
      <label className="field"><span>标题</span><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="可选" /></label>
      <label className="drop-zone"><input type="file" accept=".epub,.txt,text/plain,application/epub+zip" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><strong>{file ? file.name : '选择 EPUB 或 TXT'}</strong><small>也可以把文件拖到这里</small></label>
      <div className="divider"><span>或者粘贴文本</span></div>
      <label className="field"><textarea value={text} onChange={(event) => setText(event.target.value)} rows={9} placeholder="ここに日本語の文章を貼り付けてください。" /></label>
      {error && <div className="error-box">{error}</div>}
      <div className="modal-actions"><button className="button ghost" onClick={onClose}>取消</button><button className="button primary" disabled={busy || (!file && !text.trim())} onClick={() => void submit()}>{busy ? '正在导入…' : '导入书籍'}</button></div>
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
      <label className="field"><span>API Key（AI 句界审校和逐句释义使用）</span><input type="password" autoComplete="off" value={draft.apiKey} onChange={(event) => setDraft({ ...draft, apiKey: event.target.value })} placeholder={draft.hasStoredApiKey ? '已保存；留空保持不变' : 'sk-…'} /></label>
      <label className="field"><span>OpenAI 兼容 Base URL</span><input value={draft.baseUrl} onChange={(event) => setDraft({ ...draft, baseUrl: event.target.value })} /></label>
      <label className="field"><span>模型</span><input value={draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })} /></label>
      <div className="modal-actions"><button className="button ghost" onClick={onClose}>取消</button><button className="button primary" disabled={!canSave} onClick={save}>应用</button></div>
    </Modal>
  )
}

function VoiceSettingsDialog({ value, onClose, onSave }: {
  value: VoiceSettings
  onClose: () => void
  onSave: (next: Pick<VoiceSettings, 'ymmPath' | 'characterName' | 'playbackRate' | 'volume'>, template: File | null) => void | Promise<void>
}) {
  const [draft, setDraft] = useState(value)
  const [template, setTemplate] = useState<File | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  async function save() {
    setSaving(true)
    setError('')
    try {
      await onSave(draft, template)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSaving(false)
    }
  }
  return (
    <Modal title="YMM4 配音设置" onClose={onClose}>
      <div className="privacy-note"><strong>本机后台配音</strong><p>冰读通过本机配音桥调用 YMM4 中已获许可的语音角色。模板中的角色和音色会被保留，生成结果只写入本地缓存。</p></div>
      <label className="field"><span>YukkuriMovieMaker.exe 路径</span><input value={draft.ymmPath} onChange={(event) => setDraft({ ...draft, ymmPath: event.target.value })} placeholder="留空时自动查找工作区内的幻想乡口音剪辑器" /></label>
      <label className="field"><span>配音模板（.ymmp）</span><input type="file" accept=".ymmp" onChange={(event) => setTemplate(event.target.files?.[0] ?? null)} /></label>
      <label className="field"><span>配音角色</span><input value={draft.characterName} onChange={(event) => setDraft({ ...draft, characterName: event.target.value })} placeholder="例如：琪露诺；留空时使用模板角色" /></label>
      <p className="setting-status">YMM4：{value.ymmFound ? '已找到' : '未找到'}　模板：{value.templateFound ? `已导入${value.characterName ? `（${value.characterName}）` : ''}` : '未导入'}</p>
      <div className="voice-setting-grid">
        <label className="field"><span>语速：{(draft.playbackRate / 100).toFixed(2)}×</span><input type="range" min="50" max="150" step="5" value={draft.playbackRate} onChange={(event) => setDraft({ ...draft, playbackRate: Number(event.target.value) })} /></label>
        <div className="field fixed-setting"><span>音高</span><strong>1.00×（固定）</strong></div>
        <label className="field"><span>音量：{draft.volume}</span><input type="range" min="0" max="100" value={draft.volume} onChange={(event) => setDraft({ ...draft, volume: Number(event.target.value) })} /></label>
      </div>
      {error && <div className="error-box">{error}</div>}
      <div className="modal-actions"><button className="button ghost" onClick={onClose}>取消</button><button className="button primary" disabled={saving} onClick={() => void save()}>{saving ? '正在保存…' : '应用'}</button></div>
    </Modal>
  )
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}><div className="modal" role="dialog" aria-modal="true" aria-label={title}><div className="modal-head"><h2>{title}</h2><button onClick={onClose} aria-label="关闭">×</button></div>{children}</div></div>
}

export default App
