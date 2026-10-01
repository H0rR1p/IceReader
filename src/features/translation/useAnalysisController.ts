import { useRef, useState } from 'react'
import type { Dispatch, SetStateAction } from 'react'

import {
  ApiRequestError,
  explainSentence,
  explainSentences,
  loadTranslationQueue,
  segmentChapter,
} from '../../api'
import { db, loadChapterData, syncRecords } from '../../db'
import type { RecordChanges, RecordDeletes } from '../../db'
import type { AnalyzeResponse, ApiSettings, Book, Chapter, Lexeme, Sentence, Token } from '../../types'
import { AsyncQueue, makeDynamicTranslationBatches } from './pipeline'
import type { BackgroundJob, TranslationBatch, TranslationMode } from './pipeline'


type SaveDelta = { upserts: RecordChanges; deletes: RecordDeletes }

type AnalysisControllerOptions = {
  userId: string
  activeBook: Book | null
  settings: ApiSettings
  setActiveBook: Dispatch<SetStateAction<Book | null>>
  setActiveChapter: Dispatch<SetStateAction<Chapter | null>>
  refreshBooks: () => Promise<void>
  setNotice: (message: string) => void
}

function makeLexemeKey(token: Pick<Token, 'lemma' | 'reading' | 'part_of_speech'>) {
  return `${token.lemma}|${token.reading}|${token.part_of_speech}`
}

export function useAnalysisController({
  userId,
  activeBook,
  settings,
  setActiveBook,
  setActiveChapter,
  refreshBooks,
  setNotice,
}: AnalysisControllerOptions) {
  const [backgroundJob, setBackgroundJob] = useState<BackgroundJob | null>(null)
  const [dataRevision, setDataRevision] = useState(0)
  const [translationMode, setTranslationMode] = useState<TranslationMode>(() =>
    localStorage.getItem(`bingdu:${userId}:translation-mode`) === 'full' ? 'full' : 'meaning',
  )
  const [translationConcurrency, setTranslationConcurrency] = useState(() => {
    const stored = Number(localStorage.getItem(`bingdu:${userId}:translation-concurrency`) || 4)
    return Math.min(8, Math.max(1, Number.isFinite(stored) ? stored : 4))
  })
  const backgroundAbortRef = useRef<AbortController | null>(null)

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

  return {
    backgroundJob,
    dataRevision,
    translationMode,
    translationConcurrency,
    setTranslationMode,
    setTranslationConcurrency,
    processChapter,
    explainAndStoreSentence,
    runBackground,
    cancelBackground,
  }
}
