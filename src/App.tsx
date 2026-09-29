import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiRequestError, checkHealth, deleteBookCover, explainSentence, explainSentences, loadApiSettings, loadTranslationQueue, loadVoiceSettings, saveApiSettings, saveVoiceSettings, segmentChapter, uploadBookCover, uploadVoiceTemplate } from './api'
import { db, loadBookBookmarks, loadChapterData, removeBook, restoreProjectIndex, syncRecords } from './db'
import type { RecordChanges, RecordDeletes } from './db'
import Library from './features/library/Library'
import StudyDataDialog from './features/study/StudyDataDialog'
import { ImportDialog, SettingsDialog, VoiceSettingsDialog } from './features/settings/Dialogs'
import Reader from './features/reader/Reader'
import type {
  AnalyzeResponse,
  ApiSettings,
  Book,
  Chapter,
  ImportedBook,
  Lexeme,
  Sentence,
  SentenceBookmark,
  Token,
  TranslationQueueItem,
  VoiceSettings,
} from './types'

const DEFAULT_SETTINGS: ApiSettings = {
  apiKey: '',
  baseUrl: 'https://api.deepseek.com',
  model: 'deepseek-chat',
  hasStoredApiKey: false,
  cacheHitUsdPerMillion: 0,
  cacheMissUsdPerMillion: 0,
  outputUsdPerMillion: 0,
}

const DEFAULT_VOICE_SETTINGS: VoiceSettings = {
  ymmPath: '', ymmFound: false, templateFound: false, characterName: '',
  characterNames: [], playbackRate: 85, volume: 50, ready: false,
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

type TranslationMode = 'meaning' | 'full'

type TranslationBatch = {
  items: TranslationQueueItem[]
  contextBefore: string[]
  chapterTitle: string
}

type SaveDelta = { upserts: RecordChanges; deletes: RecordDeletes }

class AsyncQueue<T> {
  private items: T[] = []
  private waiters: Array<(value: T | null) => void> = []
  private closed = false

  get done() { return this.closed && this.items.length === 0 }

  push(value: T) {
    if (this.closed) return
    const waiter = this.waiters.shift()
    if (waiter) waiter(value)
    else this.items.push(value)
  }

  close() {
    this.closed = true
    for (const waiter of this.waiters.splice(0)) waiter(null)
  }

  async next(): Promise<T | null> {
    const value = this.items.shift()
    if (value !== undefined) return value
    if (this.closed) return null
    return new Promise((resolve) => this.waiters.push(resolve))
  }
}

function estimateTranslationTokens(item: TranslationQueueItem, mode: TranslationMode) {
  const source = Math.ceil(item.sentence.original.length * 1.15)
  if (mode === 'meaning') return source + Math.max(36, Math.ceil(item.sentence.original.length * .65))
  const contentTokens = item.tokens.reduce((count, token) => count + (token.is_content ? 1 : 0), 0)
  return source + Math.max(80, Math.ceil(item.sentence.original.length * .8)) + contentTokens * 14
}

function makeDynamicTranslationBatches(
  items: TranslationQueueItem[], mode: TranslationMode, initialContext: string[] = [],
): TranslationBatch[] {
  const batches: TranslationBatch[] = []
  let current: TranslationQueueItem[] = []
  let estimatedTokens = 0
  let recent = initialContext.slice(-2)
  const flush = () => {
    if (!current.length) return
    batches.push({ items: current, contextBefore: recent.slice(-2), chapterTitle: current[0].chapterTitle })
    recent = [...recent, ...current.map((item) => item.sentence.original)].slice(-2)
    current = []
    estimatedTokens = 0
  }
  for (const item of items) {
    const itemTokens = estimateTranslationTokens(item, mode)
    if (current.length && (current.length >= 12 || estimatedTokens + itemTokens > 2500)) flush()
    current.push(item)
    estimatedTokens += itemTokens
  }
  flush()
  return batches
}

function makeLexemeKey(token: Pick<Token, 'lemma' | 'reading' | 'part_of_speech'>) {
  return `${token.lemma}|${token.reading}|${token.part_of_speech}`
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
  const [translationMode, setTranslationMode] = useState<TranslationMode>(() =>
    localStorage.getItem('bingdu-translation-mode') === 'full' ? 'full' : 'meaning',
  )
  const [translationConcurrency, setTranslationConcurrency] = useState(() => {
    const stored = Number(localStorage.getItem('bingdu-translation-concurrency') || 4)
    return Math.min(8, Math.max(1, Number.isFinite(stored) ? stored : 4))
  })
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
    navigationAbortRef.current?.abort()
    navigationAbortRef.current = null
    setLoadingBookId(null)
    setLoadingChapterId(null)
    setActiveBook(null)
    setActiveChapter(null)
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
      const openedBook = { ...book, lastOpenedAt: Date.now() }
      await db.books.put(openedBook)
      await syncRecords({ books: [openedBook] }, {}, controller.signal)
      setBooks((current) => current.map((item) => item.id === book.id ? openedBook : item))
      setActiveBook(openedBook)
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

  async function selectChapter(chapter: Chapter, sentenceId?: string) {
    if (!activeBook || loadingChapterId) return
    navigationAbortRef.current?.abort()
    const controller = new AbortController()
    navigationAbortRef.current = controller
    setLoadingChapterId(chapter.id)
    setActiveChapter(null)
    try {
      const snapshot = await loadChapterData(chapter.id, controller.signal)
      if (controller.signal.aborted) return
      const nextBook = {
        ...activeBook,
        currentChapterId: chapter.id,
        currentSentenceId: sentenceId,
        updatedAt: Date.now(),
      }
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
      if (error instanceof ApiRequestError && error.status === 429) {
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

  async function storeExplanationResult(
    result: AnalyzeResponse,
    sourceTokens: Token[],
    persist = true,
    signal?: AbortSignal,
    replaceAnnotations = false,
    replaceLexical = true,
    onDeferred?: (delta: SaveDelta) => void,
  ) {
    const sentenceIds = result.sentences.map((sentence) => sentence.id)
    const tokenIds = replaceLexical ? sourceTokens.map((token) => token.id) : []
    const oldAnnotations = replaceAnnotations && sentenceIds.length
      ? await db.annotations.where('sentence_id').anyOf(sentenceIds).toArray()
      : []
    const storedLexemes: Lexeme[] = []
    await db.transaction('rw', [db.sentences, db.annotations, db.contextSenses, db.lexemes], async () => {
      if (result.sentences.length) await db.sentences.bulkPut(result.sentences)
      if (replaceAnnotations && sentenceIds.length) await db.annotations.where('sentence_id').anyOf(sentenceIds).delete()
      if (tokenIds.length) await db.contextSenses.bulkDelete(tokenIds)
      if (result.annotations.length) await db.annotations.bulkPut(result.annotations)
      if (replaceLexical && result.context_senses.length) await db.contextSenses.bulkPut(result.context_senses)
      for (const incoming of (replaceLexical ? result.lexemes : [])) {
        const existing = await db.lexemes.get(incoming.key)
        if (!existing?.correctedByUser) {
          const next = { ...incoming, firstKana: incoming.reading[0] || '未', updatedAt: Date.now() }
          await db.lexemes.put(next)
          storedLexemes.push(next)
        }
      }
    })
    const delta: SaveDelta = {
      upserts: {
        sentences: result.sentences,
        annotations: result.annotations,
        contextSenses: replaceLexical ? result.context_senses : [],
        lexemes: storedLexemes,
      },
      deletes: {
        annotations: oldAnnotations.map((item) => item.id),
        contextSenses: tokenIds,
      },
    }
    if (persist) await syncRecords(delta.upserts, delta.deletes, signal)
    else onDeferred?.(delta)
    return result
  }

  async function explainAndStoreSentence(
    sentence: Sentence, tokens: Token[], persist = true, signal?: AbortSignal, annotationMode: 'none' | 'grammar' = 'none',
  ) {
    const result = await explainSentence(
      { ...sentence, explanation_status: 'processing' },
      tokens.map(({ lexemeKey: _lexemeKey, ...token }) => token),
      settings,
      signal,
      annotationMode,
    )
    return storeExplanationResult(
      result, tokens, persist, signal, annotationMode === 'grammar', annotationMode !== 'grammar',
    )
  }

  async function explainAndStoreBatch(
    items: Array<{ sentence: Sentence; tokens: Token[] }>,
    persist = true,
    signal?: AbortSignal,
    detailMode: TranslationMode = 'full',
    contextBefore: string[] = [],
    onDeferred?: (delta: SaveDelta) => void,
  ) {
    const result = await explainSentences(
      items.map(({ sentence, tokens }) => ({ sentence: { ...sentence, explanation_status: 'processing' }, tokens })),
      settings,
      'none',
      signal,
      detailMode,
      contextBefore,
    )
    return storeExplanationResult(
      result, items.flatMap((item) => item.tokens), persist, signal, false,
      detailMode === 'full', onDeferred,
    )
  }

  async function updateBookTranslationStatus(bookId: string) {
    const textChapters = (await db.chapters.where('bookId').equals(bookId).toArray())
      .filter((chapter) => chapter.text.trim())
    let complete = textChapters.length > 0
    for (const chapter of textChapters) {
      const sentences = await db.sentences.where('chapter_id').equals(chapter.id).toArray()
      if (!sentences.length || sentences.some((sentence) =>
        !sentence.translation_zh.trim() || sentence.explanation_status !== 'complete')) {
        complete = false
        break
      }
    }
    const stored = await db.books.get(bookId)
    if (!stored || stored.translationComplete === complete) return
    const next = { ...stored, translationComplete: complete, updatedAt: Date.now() }
    await db.books.put(next)
    await syncRecords({ books: [next] })
    setActiveBook((current) => current?.id === bookId ? next : current)
    await refreshBooks()
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
    let nothingToTranslate = false
    setBackgroundJob({ kind, scope, label: `准备后台${jobName}`, completed: 0, total: targets.length, failed: 0, running: true })

    const processChapterWithBackoff = async (target: Chapter) => {
      let attempt = 0
      while (true) {
        try {
          return await processChapter(target, { quiet: true, persist: true, signal: controller.signal })
        } catch (error) {
          if (!(error instanceof ApiRequestError) || error.status !== 429 || attempt >= 3) throw error
          const scheduledDelay = Math.min(8000, 1000 * (2 ** attempt))
          const delay = Math.min(60_000, Math.max(scheduledDelay, error.retryAfterMs ?? 0))
          attempt += 1
          setBackgroundJob((current) => current && ({
            ...current,
            label: `后台切分限流，${Math.ceil(delay / 1000)} 秒后重试：${target.title}`,
          }))
          const resumeAt = Date.now() + delay
          while (Date.now() < resumeAt) {
            ensureNotCanceled()
            await new Promise((resolve) => window.setTimeout(resolve, 180))
          }
        }
      }
    }

    try {
      if (kind === 'segment') {
        for (let index = 0; index < targets.length; index += 1) {
          ensureNotCanceled()
          const targetIndex = (await db.chapters.get(targets[index].id)) ?? targets[index]
          const loaded = await loadChapterData(targetIndex.id, controller.signal)
          const target = loaded.chapter
          const ready = loaded.sentences.length > 0 && target.status !== 'pending' && target.status !== 'failed'
          if (!ready && target.text.trim()) {
            setBackgroundJob((current) => current && ({ ...current, label: `后台切分：${target.title}`, completed: index, failed }))
            if (!await processChapterWithBackoff(target)) failed += 1
          }
          setBackgroundJob((current) => current && ({ ...current, completed: index + 1, failed }))
        }
      } else {
        const queue = new AsyncQueue<TranslationBatch>()
        const configuredConcurrency = translationConcurrency
        let effectiveConcurrency = configuredConcurrency
        let successfulSinceAdjustment = 0
        let pauseUntil = 0
        let discovered = 0
        let completed = 0
        let producerError: unknown = null

        const primaryKeys: Record<string, string> = {
          books: 'id', chapters: 'id', sentences: 'id', tokens: 'id', annotations: 'id',
          contextSenses: 'token_id', lexemes: 'key', bookmarks: 'id',
        }
        const bufferedUpserts = new Map<string, Map<string, unknown>>()
        const bufferedDeletes = new Map<string, Set<string>>()
        let bufferedBatches = 0
        let flushTimer: number | null = null
        let flushChain: Promise<void> = Promise.resolve()
        let saveError: unknown = null

        const flushSaveBuffer = () => {
          if (flushTimer !== null) {
            window.clearTimeout(flushTimer)
            flushTimer = null
          }
          const upserts: Record<string, unknown[]> = {}
          const deletes: Record<string, string[]> = {}
          for (const [table, rows] of bufferedUpserts) {
            if (rows.size) upserts[table] = [...rows.values()]
          }
          for (const [table, keys] of bufferedDeletes) {
            if (keys.size) deletes[table] = [...keys]
          }
          bufferedUpserts.clear()
          bufferedDeletes.clear()
          bufferedBatches = 0
          if (!Object.keys(upserts).length && !Object.keys(deletes).length) return flushChain
          flushChain = flushChain.then(() => syncRecords(upserts as RecordChanges, deletes as RecordDeletes))
          return flushChain
        }

        const bufferDelta = (delta: SaveDelta) => {
          for (const [table, values] of Object.entries(delta.upserts)) {
            const keyName = primaryKeys[table]
            if (!keyName || !values) continue
            const rows = bufferedUpserts.get(table) ?? new Map<string, unknown>()
            for (const value of values) {
              const key = String((value as Record<string, unknown>)[keyName] ?? '')
              if (key) rows.set(key, value)
            }
            bufferedUpserts.set(table, rows)
          }
          for (const [table, values] of Object.entries(delta.deletes)) {
            if (!values) continue
            const keys = bufferedDeletes.get(table) ?? new Set<string>()
            for (const value of values) keys.add(value)
            bufferedDeletes.set(table, keys)
          }
          bufferedBatches += 1
          if (bufferedBatches >= 5) {
            void flushSaveBuffer().catch((error) => { saveError = error })
          } else if (flushTimer === null) {
            flushTimer = window.setTimeout(() => {
              flushTimer = null
              void flushSaveBuffer().catch((error) => { saveError = error })
            }, 2000)
          }
        }

        const updateProgress = (chapterTitle: string) => setBackgroundJob((current) => current && ({
          ...current,
          label: `后台翻译：${chapterTitle} · 并发 ${effectiveConcurrency}`,
          completed,
          total: discovered,
          failed,
        }))

        const waitForWorkerTurn = async (workerIndex: number) => {
          while (workerIndex >= effectiveConcurrency || Date.now() < pauseUntil) {
            ensureNotCanceled()
            if (queue.done) return false
            await new Promise((resolve) => window.setTimeout(resolve, 180))
          }
          return true
        }

        const producer = async () => {
          try {
            for (const originalTarget of targets) {
              ensureNotCanceled()
              const targetIndex = (await db.chapters.get(originalTarget.id)) ?? originalTarget
              const loaded = await loadChapterData(targetIndex.id, controller.signal)
              const target = loaded.chapter
              let ready = loaded.sentences.length > 0 && target.status !== 'pending' && target.status !== 'failed'
              if (!ready && target.text.trim()) {
                setBackgroundJob((current) => current && ({ ...current, label: `后台切分：${target.title}` }))
                ready = await processChapterWithBackoff(target)
                if (!ready) failed += 1
              }
              if (!ready) continue
              let cursor = ''
              do {
                ensureNotCanceled()
                const page = await loadTranslationQueue(
                  activeBook.id, target.id, translationMode, translationMode === 'full', cursor, controller.signal,
                )
                if (page.items.length) {
                  await db.sentences.bulkPut(page.items.map((item) => item.sentence))
                  if (translationMode === 'full') {
                    const tokens = page.items.flatMap((item) => item.tokens)
                    if (tokens.length) await db.tokens.bulkPut(tokens)
                  }
                  for (const batch of makeDynamicTranslationBatches(page.items, translationMode, page.contextBefore)) {
                    discovered += batch.items.length
                    queue.push(batch)
                  }
                  updateProgress(target.title)
                }
                cursor = page.nextCursor ?? ''
              } while (cursor)
            }
          } catch (error) {
            producerError = error
          } finally {
            queue.close()
          }
        }

        const worker = async (workerIndex: number) => {
          while (true) {
            if (!await waitForWorkerTurn(workerIndex)) return
            const batch = await queue.next()
            if (!batch) return
            ensureNotCanceled()
            const sourceSentences = batch.items.map((item) => item.sentence)
            const processingSentences = sourceSentences.map((sentence) => ({
              ...sentence, explanation_status: 'processing' as const, error: null,
            }))
            await db.sentences.bulkPut(processingSentences)
            let attempt = 0
            while (true) {
              try {
                await explainAndStoreBatch(
                  batch.items.map((item) => ({ sentence: item.sentence, tokens: item.tokens })),
                  false,
                  controller.signal,
                  translationMode,
                  batch.contextBefore,
                  bufferDelta,
                )
                completed += batch.items.length
                successfulSinceAdjustment += 1
                if (successfulSinceAdjustment >= 10 && effectiveConcurrency < configuredConcurrency) {
                  effectiveConcurrency += 1
                  successfulSinceAdjustment = 0
                }
                updateProgress(batch.chapterTitle)
                break
              } catch (error) {
                if (controller.signal.aborted) {
                  await db.sentences.bulkPut(sourceSentences.map((sentence) => ({
                    ...sentence, explanation_status: 'idle' as const, error: null,
                  })))
                  throw error
                }
                const message = error instanceof Error ? error.message : String(error)
                const overloaded = /(?:429|503|服务过载|overload)/i.test(message)
                if (overloaded && attempt < 3) {
                  effectiveConcurrency = Math.max(1, Math.floor(effectiveConcurrency / 2))
                  successfulSinceAdjustment = 0
                  const scheduledDelay = Math.min(8000, 1000 * (2 ** attempt))
                  const serverDelay = error instanceof ApiRequestError ? error.retryAfterMs ?? 0 : 0
                  pauseUntil = Date.now() + Math.min(60_000, Math.max(scheduledDelay, serverDelay))
                  attempt += 1
                  updateProgress(batch.chapterTitle)
                  while (Date.now() < pauseUntil) {
                    ensureNotCanceled()
                    await new Promise((resolve) => window.setTimeout(resolve, 180))
                  }
                  continue
                }
                failed += batch.items.length
                completed += batch.items.length
                const failedSentences = sourceSentences.map((sentence) => ({
                  ...sentence, explanation_status: 'failed' as const, error: message,
                }))
                await db.sentences.bulkPut(failedSentences)
                bufferDelta({ upserts: { sentences: failedSentences }, deletes: {} })
                updateProgress(batch.chapterTitle)
                break
              }
            }
          }
        }

        const producerPromise = producer()
        const workerResults = await Promise.allSettled(
          Array.from({ length: configuredConcurrency }, (_, index) => worker(index)),
        )
        await producerPromise
        await flushSaveBuffer()
        await flushChain
        if (saveError) throw saveError
        if (producerError) throw producerError
        const rejectedWorker = workerResults.find((result) => result.status === 'rejected')
        if (rejectedWorker?.status === 'rejected') throw rejectedWorker.reason
        nothingToTranslate = discovered === 0
      }

      if (kind === 'translate' && scope === 'book') await updateBookTranslationStatus(activeBook.id)
      setDataRevision((value) => value + 1)
      setBackgroundJob(null)
      setNotice(nothingToTranslate
        ? '没有需要翻译的句子。'
        : failed ? `后台${jobName}完成，${failed} 项失败，可稍后重试。` : `后台${jobName}完成。`)
    } catch (error) {
      setDataRevision((value) => value + 1)
      if (controller.signal.aborted) {
        setBackgroundJob(null)
        setNotice(`后台${jobName}已取消，已完成的结果已保存。`)
      } else {
        const message = error instanceof Error ? error.message : String(error)
        setBackgroundJob(null)
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

  async function setBookImageVisibility(book: Book, showImages: boolean) {
    const next = { ...book, showImages, updatedAt: Date.now() }
    await db.books.put(next)
    await syncRecords({ books: [next] })
    setBooks((current) => current.map((item) => item.id === book.id ? next : item))
    setActiveBook((current) => current?.id === book.id ? next : current)
  }

  async function saveBookCollection(collectionId: string, collectionName: string, bookIds: string[]) {
    const selected = new Set(bookIds)
    const affected = books.filter((book) => selected.has(book.id) || book.collectionId === collectionId)
    const updated = affected.map((book) => {
      const next: Book = { ...book, updatedAt: Date.now() }
      if (selected.has(book.id)) {
        next.collectionId = collectionId
        next.collectionName = collectionName
      } else {
        delete next.collectionId
        delete next.collectionName
      }
      return next
    })
    await db.books.bulkPut(updated)
    await syncRecords({ books: updated })
    await refreshBooks()
    setNotice(`合集“${collectionName}”已保存。`)
  }

  async function dissolveBookCollection(collectionId: string) {
    const members = books.filter((book) => book.collectionId === collectionId).map((book) => {
      const next: Book = { ...book, updatedAt: Date.now() }
      delete next.collectionId
      delete next.collectionName
      return next
    })
    if (members.length) {
      await db.books.bulkPut(members)
      await syncRecords({ books: members })
    }
    await refreshBooks()
    setNotice('合集已解散，书籍仍保留在书架中。')
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
          <button className="button ghost" onClick={() => setShowStudyData(true)}>个人词库</button>
          <button className="button ghost" onClick={() => setShowVoiceSettings(true)}>配音设置</button>
          <button className="button ghost" onClick={() => setShowSettings(true)}>AI 设置</button>
          <button className="button primary" onClick={() => setShowImport(true)}>导入书籍</button>
        </div>
      </header>

      {notice && <div className="notice" role="status">{notice}<button onClick={() => setNotice('')}>×</button></div>}

      {!activeBook ? (
        <Library
          books={books}
          loading={libraryLoading}
          loadingBookId={loadingBookId}
          onOpen={openBook}
          onDelete={deleteBook}
          onChangeCover={changeBookCover}
          onImport={() => setShowImport(true)}
          onSaveCollection={saveBookCollection}
          onDissolveCollection={dissolveBookCollection}
        />
      ) : (
        <Workspace
          book={activeBook}
          activeChapter={activeChapter}
          loadingChapterId={loadingChapterId}
          onSelectChapter={(chapter, sentenceId) => void selectChapter(chapter, sentenceId)}
          onProcessChapter={processChapter}
          onExplainSentence={explainAndStoreSentence}
          backgroundJob={backgroundJob}
          onCancelBackground={cancelBackground}
          dataRevision={dataRevision}
          onBackgroundBook={(kind) => void runBackground(kind, 'book')}
          onBackgroundChapter={(kind, chapter) => void runBackground(kind, 'chapter', chapter)}
          translationMode={translationMode}
          translationConcurrency={translationConcurrency}
          onTranslationModeChange={(mode) => {
            setTranslationMode(mode)
            localStorage.setItem('bingdu-translation-mode', mode)
          }}
          onTranslationConcurrencyChange={(value) => {
            setTranslationConcurrency(value)
            localStorage.setItem('bingdu-translation-concurrency', String(value))
          }}
          onBookImageVisibility={(visible) => setBookImageVisibility(activeBook, visible)}
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
            if (template) await uploadVoiceTemplate(template)
            const saved = await saveVoiceSettings(next)
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

function Workspace({ book, activeChapter, loadingChapterId, onSelectChapter, onProcessChapter, onExplainSentence, backgroundJob, dataRevision, onBackgroundBook, onBackgroundChapter, onCancelBackground, translationMode, translationConcurrency, onTranslationModeChange, onTranslationConcurrencyChange, onBookImageVisibility, onNotice }: {
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
  translationMode: TranslationMode
  translationConcurrency: number
  onTranslationModeChange: (mode: TranslationMode) => void
  onTranslationConcurrencyChange: (value: number) => void
  onBookImageVisibility: (visible: boolean) => Promise<void>
  onNotice: (message: string) => void
}) {
  const [chapters, setChapters] = useState<Chapter[]>([])
  const [bookmarks, setBookmarks] = useState<SentenceBookmark[]>([])
  const [navMode, setNavMode] = useState<'chapters' | 'bookmarks'>('chapters')
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const [showBookImages, setShowBookImages] = useState(() => book.showImages !== false)
  const load = useCallback(async () => {
    setChapters(await db.chapters.where('bookId').equals(book.id).sortBy('order'))
  }, [book.id, activeChapter?.status, backgroundJob?.completed, dataRevision])
  useEffect(() => { void load() }, [load])
  useEffect(() => {
    setShowBookImages(book.showImages !== false)
  }, [book.id, book.showImages])
  useEffect(() => {
    const controller = new AbortController()
    void loadBookBookmarks(book.id, controller.signal)
      .then((rows) => { if (!controller.signal.aborted) setBookmarks(rows) })
      .catch((error) => { if (!controller.signal.aborted) onNotice(error instanceof Error ? error.message : String(error)) })
    return () => controller.abort()
  }, [book.id, onNotice])

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
      id: sentence.id,
      bookId: book.id,
      chapterId: activeChapter.id,
      sentenceId: sentence.id,
      chapterTitle: activeChapter.title,
      chapterOrder: activeChapter.order,
      sentenceStart: sentence.start,
      text: sentence.original,
      createdAt: Date.now(),
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
          <div className="book-background-actions">
            <button className="button small" disabled={backgroundJob?.running} onClick={() => onBackgroundBook('segment')}>后台切分全书</button>
            <button className="button small" disabled={backgroundJob?.running} onClick={() => onBackgroundBook('translate')}>后台翻译全书</button>
          </div>
          <div className="background-options" aria-label="后台翻译设置">
            <label><span>模式</span><select disabled={backgroundJob?.running} value={translationMode} onChange={(event) => onTranslationModeChange(event.target.value as TranslationMode)}><option value="meaning">快速句意</option><option value="full">完整释义</option></select></label>
            <label><span>并发</span><select disabled={backgroundJob?.running} value={translationConcurrency} onChange={(event) => onTranslationConcurrencyChange(Number(event.target.value))}>{Array.from({ length: 8 }, (_, index) => <option key={index + 1} value={index + 1}>{index + 1}</option>)}</select></label>
          </div>
          <p className="background-mode-hint">{translationMode === 'meaning' ? '只生成句意；词义与语法阅读时按需生成。' : '同时补充本地词典未命中的词义。'}</p>
          <label className="book-image-setting">
            <input type="checkbox" checked={showBookImages} onChange={(event) => {
              const visible = event.target.checked
              setShowBookImages(visible)
              void onBookImageVisibility(visible).catch((error) => {
                setShowBookImages(!visible)
                onNotice(error instanceof Error ? error.message : String(error))
              })
            }} />
            <span><strong>显示全书插图</strong><small>{showBookImages ? '换节后继续显示' : '当前全书隐藏'}</small></span>
          </label>
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
                <small>{bookmark.chapterTitle}</small>
                <span lang="ja">{bookmark.text}</span>
              </button>
            )) : <p className="empty-bookmarks">还没有句子书签。</p>}
          </nav>}
        </div>}
      </aside>
      <main className="reading-stage">
        {!activeChapter && loadingChapterId ? <section className="processing-panel loading-chapter" aria-live="polite"><div className="loading-dango" aria-hidden="true" /><p className="eyebrow">按章读取</p><h1>正在加载章节</h1><p>正在读取本章句子、分词和注释。点击左上角头像可以安全返回书架。</p></section> : !activeChapter ? null : (
          <Reader
            book={book}
            chapter={activeChapter}
            previousChapter={previousChapter}
            nextChapter={nextChapter}
            chapterNavigationLoading={Boolean(loadingChapterId)}
            showImages={showBookImages}
            onNavigateChapter={onSelectChapter}
            onNotice={onNotice}
            onRetry={() => onProcessChapter(activeChapter)}
            onExplainSentence={onExplainSentence}
            backgroundJob={backgroundJob}
            dataRevision={dataRevision}
            onBackground={(kind) => onBackgroundChapter(kind, activeChapter)}
            bookmarks={bookmarks}
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

export default App

