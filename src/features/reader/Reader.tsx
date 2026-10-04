import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { correctWordSense, createCardCandidate, loadKnowledgeStates, lookupDictionary, recordActivityMetric, recordLearningEvents } from '../../api'
import type { KnowledgeState, WordCorrection } from '../../api'
import { db, loadChapterDetails, syncRecords } from '../../db'
import type {
  Annotation,
  AnalyzeResponse,
  Book,
  Chapter,
  ContentBlock,
  ContextSense,
  Lexeme,
  Sentence,
  SentenceBookmark,
  Token,
} from '../../types'
import { toHiragana } from '../../text'
import { Capacitor } from '@capacitor/core'
import { setAndroidKeepAwake } from '../../mobile/runtime'

type ReaderBackgroundJob = {
  kind: 'segment' | 'translate'
  running: boolean
}

function containsKanji(value: string) {
  return /[一-龯々]/.test(value)
}

function normalizeGrammar(value: string) {
  return value.normalize('NFKC').replace(/[「」『』\s]/g, '').replace(/[～〜]/g, '~').toLowerCase()
}

export default function Reader({ userId, book, chapter, previousChapter, nextChapter, chapterNavigationLoading, showImages, onNavigateChapter, onNotice, onRetry, onExplainSentence, backgroundJob, dataRevision, onBackground, bookmarks, onToggleBookmark }: {
  userId: string
  book: Book
  chapter: Chapter
  previousChapter: Chapter | null
  nextChapter: Chapter | null
  chapterNavigationLoading: boolean
  showImages: boolean
  onNavigateChapter: (chapter: Chapter) => void
  onNotice: (message: string) => void
  onRetry: () => void
  onExplainSentence: (sentence: Sentence, tokens: Token[], persist?: boolean, signal?: AbortSignal, annotationMode?: 'none' | 'grammar') => Promise<AnalyzeResponse>
  backgroundJob: ReaderBackgroundJob | null
  dataRevision: number
  onBackground: (kind: ReaderBackgroundJob['kind']) => void
  bookmarks: SentenceBookmark[]
  onToggleBookmark: (sentence: Sentence) => Promise<void>
}) {
  const [keepAwake, setKeepAwake] = useState(() => localStorage.getItem(`bingdu:${userId}:${book.id}:keep-awake`) === '1')
  useEffect(() => {
    if (!Capacitor.isNativePlatform()) return
    void setAndroidKeepAwake(keepAwake)
    return () => { void setAndroidKeepAwake(false) }
  }, [keepAwake])
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
  const [assistanceMode, setAssistanceMode] = useState<'auto' | 'always' | 'challenge'>(() => {
    const saved=localStorage.getItem(`bingdu:${userId}:assistance-mode`)
    return saved === 'always' || saved === 'challenge' ? saved : 'auto'
  })
  const [knowledgeStates, setKnowledgeStates] = useState<Record<string,KnowledgeState>>({})
  const [showAnnotations, setShowAnnotations] = useState(true)
  const [viewMode, setViewMode] = useState<'study' | 'original'>('study')
  const [readerLoading, setReaderLoading] = useState(true)
  const [visibleSentenceCount, setVisibleSentenceCount] = useState(120)
  const [rightCollapsed, setRightCollapsed] = useState(false)
  const loadMoreRef = useRef<HTMLDivElement | null>(null)
  const lookupHistoryRef = useRef(new Set<string>())
  const forcedAssistanceRef = useRef(new Set<string>())
  const assistedSentenceIdsRef = useRef(new Set<string>())
  const exposedSentenceIdsRef = useRef(new Set<string>())
  const detailsLoadedRef = useRef(0)

  useEffect(() => {
    let canceled = false
    setReaderLoading(true)
    detailsLoadedRef.current = 0
    setSentences([])
    setTokens([])
    setAnnotations([])
    setContextSenses([])
    void (async () => {
      const nextSentences = await db.sentences.where('chapter_id').equals(chapter.id).sortBy('start')
      if (canceled) return
      setSentences(nextSentences)
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

  useEffect(() => {
    if (!sentences.length || sentences[0]?.chapter_id !== chapter.id || detailsLoadedRef.current >= Math.min(visibleSentenceCount, sentences.length)) return
    const controller = new AbortController()
    void (async () => {
      while (!controller.signal.aborted && detailsLoadedRef.current < Math.min(visibleSentenceCount, sentences.length)) {
        const offset = detailsLoadedRef.current
        const limit = Math.min(120, visibleSentenceCount - offset)
        const page = await loadChapterDetails(chapter.id, offset, limit, controller.signal)
        if (controller.signal.aborted) return
        setTokens((current) => {
          const merged = new Map(current.map((row) => [row.id, row]))
          for (const row of page.tokens) merged.set(row.id, row)
          return [...merged.values()]
        })
        setAnnotations((current) => {
          const merged = new Map(current.map((row) => [row.id, row]))
          for (const row of page.annotations) merged.set(row.id, row)
          return [...merged.values()]
        })
        setContextSenses((current) => {
          const merged = new Map(current.map((row) => [row.token_id, row]))
          for (const row of page.contextSenses) merged.set(row.token_id, row)
          return [...merged.values()]
        })
        detailsLoadedRef.current = offset + page.sentence_ids.length
        if (!page.sentence_ids.length) break
      }
    })().catch((error) => {
      if (!controller.signal.aborted) onNotice(error instanceof Error ? error.message : String(error))
    })
    return () => controller.abort()
  }, [chapter.id, onNotice, sentences.length, visibleSentenceCount])



  const tokensBySentence = useMemo(() => {
    const map = new Map<string, Token[]>()
    for (const token of tokens) map.set(token.sentence_id, [...(map.get(token.sentence_id) ?? []), token])
    for (const values of map.values()) values.sort((a, b) => a.start - b.start)
    return map
  }, [tokens])

  useEffect(() => {
    const unique = new Map<string,{ id: string; type: 'vocabulary' | 'grammar'; canonical_key: string; lemma?: string; reading?: string; grammar_pattern?: string }>()
    for (const token of tokens.filter((value) => value.is_content)) unique.set(`vocabulary:${token.lexemeKey}`,{
      id:crypto.randomUUID(),type:'vocabulary',canonical_key:token.lexemeKey,lemma:token.lemma,reading:token.reading,
    })
    for (const note of annotations.filter((value) => value.type === 'grammar' && value.structure)) {
      const key=normalizeGrammar(note.structure ?? '')
      if (key) unique.set(`grammar:${key}`,{id:crypto.randomUUID(),type:'grammar',canonical_key:key,grammar_pattern:note.structure})
    }
    if (!unique.size) { setKnowledgeStates({}); return }
    const controller=new AbortController()
    void loadKnowledgeStates([...unique.values()],controller.signal).then((rows) => {
      if (!controller.signal.aborted) setKnowledgeStates(Object.fromEntries(rows.map((row) => [`${row.type}:${row.canonical_key}`,row])))
    }).catch(() => undefined)
    return () => controller.abort()
  }, [annotations,tokens])
  const selectedSentence = sentences.find((sentence) => sentence.id === selectedSentenceId) ?? null
  const selectedToken = tokens.find((token) => token.id === selectedTokenId) ?? null
  const selectedSentenceTokens = selectedSentenceId ? (tokensBySentence.get(selectedSentenceId) ?? []) : []
  const selectedContentTokens = selectedSentenceTokens.filter((token) => token.is_content)
  const lexeme = selectedTokenId ? (lexemesByToken[selectedTokenId] ?? null) : null
  const currentSense = contextSenses.find((sense) => sense.token_id === selectedTokenId)
  const currentNotes = annotations.filter((annotation) => annotation.sentence_id === selectedSentenceId && annotation.type === 'grammar')
  const selectedSentenceBookmarked = bookmarks.some((bookmark) => bookmark.sentenceId === selectedSentenceId)
  const sentenceExplained = selectedSentence?.explanation_status === 'complete'
  const visibleSentences = useMemo(() => sentences.slice(0, visibleSentenceCount), [sentences, visibleSentenceCount])
  const visibleSentenceIds = useMemo(() => new Set(visibleSentences.map((sentence) => sentence.id)), [visibleSentences])
  const { sentencesByBlock, coveredBlockIds } = useMemo(() => {
    const map = new Map<string, Sentence[]>()
    const covered = new Set<string>()
    let sentenceIndex = 0
    const textBlocks = (chapter.blocks ?? []).filter((block) => block.type !== 'image' && block.type !== 'page-break' && block.type !== 'separator')
    for (let blockIndex = 0; blockIndex < textBlocks.length; blockIndex += 1) {
      const block = textBlocks[blockIndex]
      if (block.type === 'image' || block.type === 'page-break' || block.type === 'separator') continue
      while (sentenceIndex < sentences.length && sentences[sentenceIndex].end <= block.start) sentenceIndex += 1
      const matches: Sentence[] = []
      let cursor = sentenceIndex
      while (cursor < sentences.length && sentences[cursor].start < block.end) {
        const sentence = sentences[cursor]
        if (sentence.start >= block.start && sentence.start < block.end) {
          matches.push(sentence)
          for (let coveredIndex = blockIndex + 1; coveredIndex < textBlocks.length && textBlocks[coveredIndex].start < sentence.end; coveredIndex += 1) {
            covered.add(textBlocks[coveredIndex].id)
          }
        }
        cursor += 1
      }
      map.set(block.id, matches)
    }
    return { sentencesByBlock: map, coveredBlockIds: covered }
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
    if (readerLoading || viewMode !== 'study') return
    const visibleIds = new Set<string>()
    const timers = new Map<string, number>()
    const exposureSeconds = 8

    const stopTimer = (sentenceId: string) => {
      const timer = timers.get(sentenceId)
      if (timer !== undefined) window.clearTimeout(timer)
      timers.delete(sentenceId)
    }
    const startTimer = (sentenceId: string) => {
      if (!document.hasFocus() || document.visibilityState !== 'visible'
        || assistedSentenceIdsRef.current.has(sentenceId)
        || exposedSentenceIdsRef.current.has(sentenceId)
        || timers.has(sentenceId)) return
      const sentenceTokens = tokensBySentence.get(sentenceId)?.filter((token) => token.is_content) ?? []
      if (!sentenceTokens.length) return
      timers.set(sentenceId, window.setTimeout(() => {
        timers.delete(sentenceId)
        if (!visibleIds.has(sentenceId) || !document.hasFocus() || document.visibilityState !== 'visible'
          || assistedSentenceIdsRef.current.has(sentenceId) || exposedSentenceIdsRef.current.has(sentenceId)) return
        exposedSentenceIdsRef.current.add(sentenceId)
        void recordLearningEvents(sentenceTokens.map((token) => ({
          id: crypto.randomUUID(),
          item: {
            id: crypto.randomUUID(), type: 'vocabulary' as const, canonical_key: token.lexemeKey,
            lemma: token.lemma, reading: token.reading,
          },
          event_type: 'natural_exposure' as const,
          occurred_at: Date.now() / 1000,
          context: {
            book_id: book.id, chapter_id: chapter.id, sentence_id: sentenceId,
            token_id: token.id, visible_seconds: exposureSeconds,
          },
        }))).catch(() => undefined)
        void recordActivityMetric('reading', { sentences_read: 1 }).catch(() => undefined)
      }, exposureSeconds * 1000))
    }
    const reconcileTimers = () => {
      for (const sentenceId of visibleIds) startTimer(sentenceId)
      if (!document.hasFocus() || document.visibilityState !== 'visible') {
        for (const sentenceId of [...timers.keys()]) stopTimer(sentenceId)
      }
    }
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const sentenceId = (entry.target as HTMLElement).dataset.sentenceId
        if (!sentenceId) continue
        if (entry.isIntersecting && entry.intersectionRatio >= 0.6) {
          visibleIds.add(sentenceId)
          startTimer(sentenceId)
        } else {
          visibleIds.delete(sentenceId)
          stopTimer(sentenceId)
        }
      }
    }, { threshold: [0, 0.6, 1] })
    document.querySelectorAll<HTMLElement>('[data-sentence-id]').forEach((element) => observer.observe(element))
    document.addEventListener('visibilitychange', reconcileTimers)
    window.addEventListener('focus', reconcileTimers)
    window.addEventListener('blur', reconcileTimers)
    return () => {
      observer.disconnect()
      for (const timer of timers.values()) window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', reconcileTimers)
      window.removeEventListener('focus', reconcileTimers)
      window.removeEventListener('blur', reconcileTimers)
    }
  }, [book.id, chapter.id, readerLoading, tokensBySentence, viewMode, visibleSentenceCount])

  useEffect(() => {
    if (!selectedSentenceId) return
    const index = sentences.findIndex((sentence) => sentence.id === selectedSentenceId)
    if (index >= visibleSentenceCount) {
      setVisibleSentenceCount(Math.min(sentences.length, index + 30))
      return
    }
    const frame = window.requestAnimationFrame(() => {
      document.querySelector<HTMLElement>(`[data-sentence-id="${CSS.escape(selectedSentenceId)}"]`)
        ?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [selectedSentenceId, sentences, visibleSentenceCount])

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



  async function selectSentence(sentence: Sentence) {
    setSelectedSentenceId(sentence.id)
    setSelectedTokenId(null)
    const latestBook = (await db.books.get(book.id)) ?? book
    const nextBook = { ...latestBook, currentChapterId: chapter.id, currentSentenceId: sentence.id, updatedAt: Date.now() }
    await db.books.put(nextBook)
    void syncRecords({ books: [nextBook] }).catch((error) => onNotice(error instanceof Error ? error.message : String(error)))
  }

  function selectToken(sentence: Sentence, token: Token) {
    void selectSentence(sentence)
    if (!token.is_content) return
    assistedSentenceIdsRef.current.add(sentence.id)
    forcedAssistanceRef.current.add(token.lexemeKey)
    setSelectedTokenId(token.id)
    const repeated = lookupHistoryRef.current.has(token.lexemeKey)
    lookupHistoryRef.current.add(token.lexemeKey)
    void recordActivityMetric('dictionary', { lookup_count: 1 }).catch(() => undefined)
    void recordLearningEvents([{
      id: crypto.randomUUID(),
      item: {
        id: crypto.randomUUID(), type: 'vocabulary', canonical_key: token.lexemeKey,
        lemma: token.lemma, reading: token.reading,
      },
      event_type: repeated ? 'repeated_lookup' : 'lookup',
      occurred_at: Date.now() / 1000,
      context: { book_id: book.id, chapter_id: chapter.id, sentence_id: sentence.id, token_id: token.id },
    }]).catch(() => undefined)
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

  async function addCurrentCard() {
    if (!selectedToken || !selectedSentence) return
    await createCardCandidate({
      item: {
        id: crypto.randomUUID(), type: 'vocabulary', canonical_key: selectedToken.lexemeKey,
        lemma: selectedToken.lemma, reading: selectedToken.reading,
      },
      lemma: selectedToken.lemma, reading: selectedToken.reading,
      gloss: currentSense?.gloss_zh || lexeme?.senses_zh[0] || '', sentence: selectedSentence.original,
      book_id: book.id, book_title: book.title, chapter_id: chapter.id, sentence_id: selectedSentence.id,
    })
    onNotice('已加入卡片收件箱。')
  }

  async function requestCorrection(hint: string, signal: AbortSignal) {
    if (!selectedToken || !selectedSentence) throw new Error('请先选择词语')
    return correctWordSense(selectedSentence, selectedToken, lexeme?.senses_zh ?? [], hint, signal)
  }

  async function acceptCorrection(result: WordCorrection) {
    if (!selectedToken || result.context_sense.token_id !== selectedToken.id) throw new Error('所选词语已变化，请重新生成')
    const next: Lexeme = { ...result.lexeme, key: selectedToken.lexemeKey,
      firstKana: selectedToken.reading[0] || '未', correctedByUser: true, updatedAt: Date.now() }
    await syncRecords({ lexemes: [next], contextSenses: [result.context_sense] })
    await db.transaction('rw', [db.lexemes, db.contextSenses], async () => {
      await db.lexemes.put(next)
      await db.contextSenses.put(result.context_sense)
    })
    setContextSenses((current) => [...current.filter((value) => value.token_id !== selectedToken.id), result.context_sense])
    setLexemesByToken((current) => ({ ...current, [selectedToken.id]: next }))
    onNotice('已修正本句语境义，并保存词义到个人词库。')
  }

  async function explainCurrentSentence(annotationMode: 'none' | 'grammar' = 'none') {
    if (!selectedSentence || explaining) return
    assistedSentenceIdsRef.current.add(selectedSentence.id)
    setExplaining(true)
    setExplainError('')
    setSentences((current) => current.map((sentence) => sentence.id === selectedSentence.id
      ? { ...sentence, explanation_status: 'processing', error: null }
      : sentence))
    try {
      const result = await onExplainSentence(selectedSentence, selectedSentenceTokens, true, undefined, annotationMode)
      const nextSentence = result.sentences[0]
      const tokenIds = selectedSentenceTokens.map((token) => token.id)
      setSentences((current) => current.map((sentence) => sentence.id === nextSentence.id ? nextSentence : sentence))
      if (annotationMode === 'grammar') {
        setAnnotations((current) => [...current.filter((note) => note.sentence_id !== selectedSentence.id), ...result.annotations])
      }
      if (annotationMode === 'none') {
        setContextSenses((current) => [...current.filter((sense) => !tokenIds.includes(sense.token_id)), ...result.context_senses])
      }
      const nextLexemes = { ...lexemesByToken }
      for (const token of annotationMode === 'none' ? selectedContentTokens : []) {
        const incoming = result.lexemes.find((item) => item.key === token.lexemeKey)
        const existing = await db.lexemes.get(token.lexemeKey)
        if (existing) nextLexemes[token.id] = existing
        else if (incoming) nextLexemes[token.id] = { ...incoming, firstKana: incoming.reading[0] || '未', updatedAt: Date.now() }
      }
      setLexemesByToken(nextLexemes)
      if (annotationMode === 'grammar') {
        void recordLearningEvents(result.annotations.filter((note) => note.structure).map((note) => ({
          id: crypto.randomUUID(),
          item: {
            id: crypto.randomUUID(), type: 'grammar' as const,
            canonical_key: normalizeGrammar(note.structure ?? note.quote),
            grammar_pattern: note.structure ?? note.quote,
          },
          event_type: 'grammar_reveal' as const,
          occurred_at: Date.now() / 1000,
          context: { book_id: book.id, chapter_id: chapter.id, sentence_id: selectedSentence.id },
        }))).catch(() => undefined)
      } else {
        void recordLearningEvents([{
          id: crypto.randomUUID(),
          item: {
            id: crypto.randomUUID(), type: 'expression',
            canonical_key: `sentence:${selectedSentence.id}`, lemma: selectedSentence.original,
          },
          event_type: 'translation_reveal',
          occurred_at: Date.now() / 1000,
          context: { book_id: book.id, chapter_id: chapter.id, sentence_id: selectedSentence.id },
        }]).catch(() => undefined)
      }
      onNotice(result.warnings.length ? `本句释义完成；${result.warnings.join('；')}` : annotationMode === 'grammar' ? '本句语法句法分析已保存。' : '本句句意与词典结果已保存。')
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

  function assistanceLevel(token: Token) {
    if (assistanceMode === 'always' || forcedAssistanceRef.current.has(token.lexemeKey)) return 3
    if (assistanceMode === 'challenge') return 0
    const state=knowledgeStates[`vocabulary:${token.lexemeKey}`]
    if (!state || state.confidence < 0.2) return 3
    if (state.mastery < 0.25) return 3
    if (state.mastery < 0.55) return 2
    if (state.mastery < 0.8 || state.confidence < 0.5) return 1
    return 0
  }

  const chapterInsight = useMemo(() => {
    const content=[...new Map(tokens.filter((token) => token.is_content).map((token) => [token.lexemeKey,token])).values()]
    if (!content.length) return null
    const unknown=content.filter((token) => {
      const state=knowledgeStates[`vocabulary:${token.lexemeKey}`]
      return !state || state.confidence < 0.2 || state.mastery < 0.55
    })
    const vocabularyCoverage=1-unknown.length/content.length
    const grammarKeys=[...new Set(annotations.filter((note) => note.type==='grammar' && note.structure).map((note) => normalizeGrammar(note.structure ?? '')).filter(Boolean))]
    const unknownGrammar=grammarKeys.filter((key) => {
      const state=knowledgeStates[`grammar:${key}`]
      return !state || state.confidence < 0.2 || state.mastery < 0.55
    })
    const unknownGrammarRatio=grammarKeys.length ? unknownGrammar.length/grammarKeys.length : null
    const averageLength=sentences.length ? sentences.reduce((sum,sentence) => sum+sentence.original.length,0)/sentences.length : 0
    const sentencePenalty=Math.min(1,Math.max(0,(averageLength-18)/35))
    const syntaxComplexity=grammarKeys.length ? Math.min(1,grammarKeys.length/Math.max(1,sentences.length)*3) : 0
    const score=unknownGrammarRatio === null
      ? (0.75*(1-vocabularyCoverage)+0.15*sentencePenalty+0.1*syntaxComplexity)
      : (0.55*(1-vocabularyCoverage)+0.25*unknownGrammarRatio+0.1*sentencePenalty+0.1*syntaxComplexity)
    return { vocabularyCoverage,grammarCoverage:unknownGrammarRatio === null ? null : 1-unknownGrammarRatio,score,
      suggestions:unknown.slice(0,5).map((token) => token.lemma) }
  },[annotations,knowledgeStates,sentences,tokens])

  const renderSentence = (sentence: Sentence) => (
    <span
      key={sentence.id}
      data-sentence-id={sentence.id}
      className={`sentence ${selectedSentenceId === sentence.id ? 'selected' : ''} ${sentence.status === 'failed' ? 'failed' : ''}`}
      role="button"
      tabIndex={0}
      onClick={() => void selectSentence(sentence)}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget || (event.key !== 'Enter' && event.key !== ' ')) return
        event.preventDefault()
        void selectSentence(sentence)
      }}
    >
      {(tokensBySentence.get(sentence.id) ?? []).length ? (tokensBySentence.get(sentence.id) ?? []).map((token) => (
        <span
          key={token.id}
          className={`token ${token.is_content ? 'content' : ''} ${token.is_content ? `assist-level-${assistanceLevel(token)}` : ''} ${selectedTokenId === token.id ? 'selected' : ''}`}
          onClick={(event) => { event.stopPropagation(); selectToken(sentence, token) }}
          tabIndex={token.is_content ? 0 : -1}
          onKeyDown={(event) => {
            if (!token.is_content || (event.key !== 'Enter' && event.key !== ' ')) return
            event.preventDefault()
            event.stopPropagation()
            selectToken(sentence, token)
          }}
        >
          {showFurigana && token.is_content && containsKanji(token.surface) && assistanceLevel(token) > 0 ? <ruby>{token.surface}<rt className={assistanceLevel(token) === 1 ? 'faint' : ''}>{toHiragana(token.reading)}</rt></ruby> : token.surface}
        </span>
      )) : sentence.original}
    </span>
  )

  const renderBlock = (block: ContentBlock) => {
    if (block.type === 'image') return showImages ? <figure key={block.id} className={`book-image ${block.placement ?? 'left'}`}><img src={block.asset_url ?? ''} alt={block.alt ?? ''} /></figure> : null
    if (block.type === 'page-break') return <div key={block.id} className="page-break" aria-hidden="true" />
    if (block.type === 'separator') return <hr key={block.id} />
    const blockSentences = sentencesByBlock.get(block.id) ?? []
    if (coveredBlockIds.has(block.id) && !blockSentences.length) return null
    const matches = blockSentences.filter((sentence) => visibleSentenceIds.has(sentence.id))
    if (blockSentences.length && !matches.length) return null
    const content = matches.length ? matches.map(renderSentence) : block.text
    if (block.type === 'heading') return <h2 key={block.id} className="book-block heading">{content}</h2>
    if (block.type === 'quote') return <blockquote key={block.id} className="book-block quote">{content}</blockquote>
    if (block.type === 'list-item') return <div key={block.id} className="book-block list-item">{content}</div>
    return <p key={block.id} className="book-block paragraph">{content}</p>
  }

  function navigateChapter(target: Chapter | null) {
    if (!target || chapterNavigationLoading) return
    window.scrollTo({ top: 0 })
    onNavigateChapter(target)
  }

  return (
    <div className={`reader-layout ${rightCollapsed ? 'right-collapsed' : ''}`}>
      <article className="reader-pane">
        <div className="reader-toolbar">
          <div><p className="eyebrow">{book.title}</p><h1>{chapter.title}</h1></div>
          <div className="display-toggles">
            {Capacitor.isNativePlatform() && <label className="assistance-select"><input type="checkbox" checked={keepAwake} onChange={(event) => { setKeepAwake(event.target.checked); localStorage.setItem(`bingdu:${userId}:${book.id}:keep-awake`, event.target.checked ? '1' : '0') }} /><span>保持亮屏</span></label>}
            {chapter.originalHtmlUrl && <button className={`mode-button ${viewMode === 'original' ? 'active' : ''}`} onClick={() => setViewMode(viewMode === 'study' ? 'original' : 'study')}>{viewMode === 'study' ? '原书预览' : '冰读模式'}</button>}
            <label className="assistance-select"><span>阅读辅助</span><select value={assistanceMode} onChange={(event) => { const value=event.target.value as typeof assistanceMode; setAssistanceMode(value); setShowFurigana(true); localStorage.setItem(`bingdu:${userId}:assistance-mode`,value) }}><option value="auto">自动渐退</option><option value="always">始终显示</option><option value="challenge">挑战模式</option></select></label>
            <Toggle label="语法" value={showAnnotations} onChange={setShowAnnotations} />
          </div>
        </div>
        <div className="chapter-background-actions" aria-label="本章后台处理">
          <button className="button small" disabled={backgroundJob?.running || chapter.status === 'processing'} onClick={() => onBackground('segment')}>后台切分本章</button>
          <button className="button small" disabled={backgroundJob?.running || chapter.status === 'processing'} onClick={() => onBackground('translate')}>后台翻译本章</button>
        </div>
        {chapterInsight && <div className="chapter-insight"><div><strong>{chapterInsight.score < .28 ? '个人难度：舒适' : chapterInsight.score < .55 ? '个人难度：适中' : '个人难度：较难'}</strong><span>词汇覆盖 {Math.round(chapterInsight.vocabularyCoverage*100)}% · 语法覆盖 {chapterInsight.grammarCoverage === null ? '待分析' : `${Math.round(chapterInsight.grammarCoverage*100)}%`}</span></div>{!!chapterInsight.suggestions.length && <small>当前范围建议预习：{chapterInsight.suggestions.join('、')}</small>}</div>}
        {(chapter.status === 'pending' || chapter.status === 'failed') && <div className="inline-warning">本章尚未切分。<button disabled={backgroundJob?.running} onClick={onRetry}>{chapter.status === 'failed' ? '重试切分' : '切分本章'}</button></div>}
        {chapter.status === 'processing' && <div className="inline-warning">正在切分本章。</div>}
        {readerLoading ? <div className="reader-loading" aria-live="polite"><div className="loading-dango" aria-hidden="true" /><span>正在整理本章内容…</span></div> : viewMode === 'original' && chapter.originalHtmlUrl
          ? <iframe className="original-preview" sandbox="" src={chapter.originalHtmlUrl} title={`${chapter.title} 原书预览`} />
          : <div className="japanese-text" lang="ja">
              {chapter.blocks?.length ? chapter.blocks.map(renderBlock) : sentences.length ? visibleSentences.map(renderSentence) : chapter.text}
              {visibleSentenceCount < sentences.length && <div ref={loadMoreRef} className="load-more-sentinel">继续加载 · {visibleSentenceCount}/{sentences.length}</div>}
            </div>}
        {!readerLoading && <nav className="chapter-page-navigation" aria-label="章节翻页">
          <button type="button" disabled={!previousChapter || chapterNavigationLoading} onClick={() => navigateChapter(previousChapter)}>
            <span>← 上一节</span><small>{previousChapter?.title ?? '已经是第一节'}</small>
          </button>
          <button type="button" disabled={!nextChapter || chapterNavigationLoading} onClick={() => navigateChapter(nextChapter)}>
            <span>下一节 →</span><small>{nextChapter?.title ?? '已经是最后一节'}</small>
          </button>
        </nav>}
      </article>
      <aside className="study-panel">
        <button className="sidebar-collapse right" type="button" aria-label={rightCollapsed ? '展开右侧栏' : '收起右侧栏'} title={rightCollapsed ? '展开右侧栏' : '收起右侧栏'} onClick={() => setRightCollapsed((value) => !value)}>{rightCollapsed ? '‹' : '›'}</button>
        {!rightCollapsed && <div className="study-panel-content">
        <div className="panel-title-row">
          <p className="panel-kicker">当前句</p>
          {selectedSentence && <button className={`sentence-bookmark ${selectedSentenceBookmarked ? 'active' : ''}`} type="button" onClick={() => void onToggleBookmark(selectedSentence)}>{selectedSentenceBookmarked ? '★ 已加书签' : '☆ 加书签'}</button>}
        </div>
        {selectedSentence ? (
          <>
            <p className="panel-original" lang="ja">{selectedSentence.original}</p>
            <button className="button primary full explain-button" disabled={explaining} onClick={() => void explainCurrentSentence('none')}>
              {explaining ? '正在释义…' : sentenceExplained ? '重新释义本句' : '释义本句'}
            </button>
            <button className="button full grammar-analysis-button" disabled={explaining} onClick={() => void explainCurrentSentence('grammar')}>语法句法分析</button>
            {(explainError || selectedSentence.explanation_status === 'failed') && <div className="error-box">{explainError || selectedSentence.error}</div>}
            {sentenceExplained && selectedSentence.translation_zh && <section className="panel-section"><h3>句意</h3><p>{selectedSentence.translation_zh}</p></section>}
            {selectedToken && <DictionaryCard token={selectedToken} lexeme={lexeme} contextGloss={currentSense?.gloss_zh ?? ''} knowledge={knowledgeStates[`vocabulary:${selectedToken.lexemeKey}`]} assistanceMode={assistanceMode} assistanceLevel={assistanceLevel(selectedToken)} forced={forcedAssistanceRef.current.has(selectedToken.lexemeKey)} onSave={saveLexeme} onAddCard={addCurrentCard} onAiCorrect={requestCorrection} onAcceptCorrection={acceptCorrection} />}
            {showAnnotations && currentNotes.length > 0 && <section className="panel-section"><h3>语法句法</h3>{currentNotes.map((note) => <div className="annotation" key={note.id}><span>语法结构</span><strong>{note.structure || note.quote}</strong><small className="annotation-quote" lang="ja">{note.quote}</small><p>{note.explanation_zh}</p></div>)}</section>}
          </>
        ) : <p className="muted">选择一个句子开始冰读。</p>}
        </div>}
      </aside>
    </div>
  )
}

function DictionaryCard({ token, lexeme, contextGloss, knowledge, assistanceMode, assistanceLevel, forced, onSave, onAddCard, onAiCorrect, onAcceptCorrection }: {
  token: Token; lexeme: Lexeme | null; contextGloss: string
  knowledge?: KnowledgeState; assistanceMode: 'auto' | 'always' | 'challenge'; assistanceLevel: number; forced: boolean
  onSave: (senses: string[]) => void
  onAddCard: () => Promise<void>
  onAiCorrect: (hint: string, signal: AbortSignal) => Promise<WordCorrection>
  onAcceptCorrection: (result: WordCorrection) => Promise<void>
}) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const [correcting, setCorrecting] = useState(false)
  const [correctionHint, setCorrectionHint] = useState('')
  const [correctionResult, setCorrectionResult] = useState<WordCorrection | null>(null)
  const [correctionBusy, setCorrectionBusy] = useState(false)
  const [correctionError, setCorrectionError] = useState('')
  const correctionAbort = useRef<AbortController | null>(null)
  useEffect(() => {
    correctionAbort.current?.abort()
    setCorrecting(false); setCorrectionHint(''); setCorrectionResult(null); setCorrectionBusy(false); setCorrectionError('')
    return () => correctionAbort.current?.abort()
  }, [token.id])
  async function generateCorrection() {
    const controller = new AbortController(); correctionAbort.current?.abort(); correctionAbort.current = controller
    setCorrectionBusy(true); setCorrectionError(''); setCorrectionResult(null)
    try { const result = await onAiCorrect(correctionHint, controller.signal); if (!controller.signal.aborted) setCorrectionResult(result) }
    catch (error) { if (!controller.signal.aborted) setCorrectionError(error instanceof Error ? error.message : String(error)) }
    finally { if (!controller.signal.aborted) setCorrectionBusy(false) }
  }
  async function saveCorrection() {
    if (!correctionResult) return
    setCorrectionBusy(true); setCorrectionError('')
    try { await onAcceptCorrection(correctionResult); setCorrecting(false); setCorrectionResult(null) }
    catch (error) { setCorrectionError(error instanceof Error ? error.message : String(error)) }
    finally { setCorrectionBusy(false) }
  }
  return (
    <section className="dictionary-card">
      <div className="dictionary-head"><div><small>{token.part_of_speech}</small><h2>{token.lemma}</h2><p>{toHiragana(token.reading)}</p></div><div className="dictionary-actions"><button className="button small" onClick={() => void onAddCard()}>加入词卡</button></div></div>
      {contextGloss && <div className="context-gloss"><small>当前语境选择</small><p>{contextGloss}</p></div>}
      <div className="dictionary-senses">
        <div className="section-title"><h3>日中词典</h3><div><button className="text-button" onClick={() => { setCorrecting(true); setEditing(false) }}>AI 修正释义</button><button className="text-button" onClick={() => setEditing(!editing)}>{editing ? '取消' : '修正'}</button></div></div>
        {editing ? <><textarea value={value} onChange={(event) => setValue(event.target.value)} rows={4} /><button className="button primary small" onClick={() => { void onSave(value.split('\n').map((x) => x.trim()).filter(Boolean)); setEditing(false) }}>保存到个人词库</button></> : <>{lexeme?.senses_zh.length ? <ol>{lexeme.senses_zh.map((sense, index) => <li key={index}>{sense}</li>)}</ol> : <p className="muted">本地词典未命中。点击“释义本句”后由 AI 补充并保存到个人词库。</p>}<small className="source">来源：{lexeme?.source ?? '等待释义'}</small></>}
      </div>
      {correcting && <div className="word-correction">
        <label>指出错误或补充正确含义（可选）<textarea value={correctionHint} maxLength={1000} disabled={correctionBusy} onChange={(event) => setCorrectionHint(event.target.value)} placeholder="例如：这里是「目にする」，表示看见，不是第几次。" rows={3} /></label>
        <small className="muted">AI 会结合原句重新判断。生成需要使用你设置的 AI 服务，确认后才保存。</small>
        {correctionResult && <div className="context-gloss"><small>修正预览 · 本句语境义</small><p>{correctionResult.context_sense.gloss_zh}</p><small>个人词库词义</small><ol>{correctionResult.lexeme.senses_zh.map((sense, index) => <li key={index}>{sense}</li>)}</ol></div>}
        {correctionError && <p role="alert" className="voice-status failed">{correctionError}</p>}
        <div className="dictionary-actions"><button className="button small" disabled={correctionBusy} onClick={() => void generateCorrection()}>{correctionBusy ? '处理中…' : correctionResult ? '重新生成' : '生成修正'}</button>{correctionResult && <button className="button primary small" disabled={correctionBusy} onClick={() => void saveCorrection()}>确认保存</button>}<button className="text-button" disabled={correctionBusy && !!correctionResult} onClick={() => { correctionAbort.current?.abort(); setCorrectionBusy(false); setCorrecting(false); setCorrectionResult(null) }}>取消</button></div>
      </div>}
      <div className="assistance-explanation"><small>辅助依据</small><p>{forced ? '你刚刚主动查询了这个词，本句内会保留完整辅助并记录为一次学习证据。' : assistanceMode === 'always' ? '当前选择始终显示辅助。' : assistanceMode === 'challenge' ? '挑战模式已隐藏自动辅助。' : !knowledge || knowledge.confidence < .2 ? '学习证据还不充分，暂时保留完整辅助。' : assistanceLevel >= 2 ? `熟练度约 ${Math.round(knowledge.mastery * 100)}%，继续显示读音和释义提示。` : assistanceLevel === 1 ? `熟练度约 ${Math.round(knowledge.mastery * 100)}%，辅助已减弱。` : `熟练度约 ${Math.round(knowledge.mastery * 100)}%，当前词已自动隐藏辅助。`}</p></div>
    </section>
  )
}

function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (next: boolean) => void }) {
  return <label className="toggle"><input type="checkbox" checked={value} onChange={(event) => onChange(event.target.checked)} /><span>{label}</span></label>
}

