import type { AiUsageSummary, AnalyzeResponse, ApiSettings, ContentBlock, ImportedBook, Lexeme, Sentence, Token, TranslationQueuePage, VoiceJob, VoiceSettings } from './types'
import type { AnalysisCapabilities, AnalysisPreferences, SentenceStructure } from './types'

export async function loadAnalysisCapabilities(signal?: AbortSignal): Promise<AnalysisCapabilities> {
  return parseResponse(await fetch('/api/analysis/capabilities', { signal }))
}
export async function loadAnalysisPreferences(signal?: AbortSignal): Promise<AnalysisPreferences> {
  return parseResponse(await fetch('/api/analysis/preferences', { signal }))
}
export async function saveAnalysisPreferences(preferences: AnalysisPreferences, signal?: AbortSignal): Promise<AnalysisPreferences> {
  return parseResponse(await fetch('/api/analysis/preferences', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(preferences), signal,
  }))
}
export async function requestSentenceStructures(sentenceIds: string[], signal?: AbortSignal, requiredVersion?: string, force = false): Promise<{ results: SentenceStructure[]; version: string }> {
  return parseResponse(await fetch('/api/sentences/structure', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
    body: JSON.stringify({ sentence_ids: sentenceIds, required_version: requiredVersion, force }),
  }))
}

export class ApiRequestError extends Error {
  constructor(message: string, public status: number, public retryAfterMs: number | null = null) {
    super(message)
    this.name = 'ApiRequestError'
  }
}

export async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `请求失败（${response.status}）`
    try {
      const body = await response.json()
      message = body.detail || message
    } catch {
      // Keep the status-based message.
    }
    const retryAfter = Number(response.headers.get('Retry-After'))
    throw new ApiRequestError(
      message,
      response.status,
      Number.isFinite(retryAfter) && retryAfter >= 0 ? retryAfter * 1000 : null,
    )
  }
  return response.json() as Promise<T>
}

export async function checkHealth(): Promise<boolean> {
  try {
    const response = await fetch('/api/health')
    return response.ok
  } catch {
    return false
  }
}

export async function loadApiSettings(): Promise<ApiSettings> {
  const response = await fetch('/api/settings')
  const data = await parseResponse<{
    base_url: string; model: string; has_api_key: boolean
    cache_hit_usd_per_million: number; cache_miss_usd_per_million: number; output_usd_per_million: number
  }>(response)
  return {
    apiKey: '', baseUrl: data.base_url, model: data.model, hasStoredApiKey: data.has_api_key,
    cacheHitUsdPerMillion: data.cache_hit_usd_per_million,
    cacheMissUsdPerMillion: data.cache_miss_usd_per_million,
    outputUsdPerMillion: data.output_usd_per_million,
  }
}

export async function saveApiSettings(settings: ApiSettings): Promise<ApiSettings> {
  const response = await fetch('/api/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      api_key: settings.apiKey || null, base_url: settings.baseUrl, model: settings.model,
      cache_hit_usd_per_million: settings.cacheHitUsdPerMillion,
      cache_miss_usd_per_million: settings.cacheMissUsdPerMillion,
      output_usd_per_million: settings.outputUsdPerMillion,
    }),
  })
  const data = await parseResponse<{
    base_url: string; model: string; has_api_key: boolean
    cache_hit_usd_per_million: number; cache_miss_usd_per_million: number; output_usd_per_million: number
  }>(response)
  return {
    apiKey: '', baseUrl: data.base_url, model: data.model, hasStoredApiKey: data.has_api_key,
    cacheHitUsdPerMillion: data.cache_hit_usd_per_million,
    cacheMissUsdPerMillion: data.cache_miss_usd_per_million,
    outputUsdPerMillion: data.output_usd_per_million,
  }
}

export async function loadAiUsage(): Promise<AiUsageSummary> {
  return parseResponse(await fetch('/api/ai/usage'))
}

function mapVoiceSettings(data: {
  ymm_path: string; ymm_found: boolean; template_found: boolean; character_name: string
  character_names?: string[]; playback_rate: number; volume: number; ready: boolean
}): VoiceSettings {
  return {
    ymmPath: data.ymm_path,
    ymmFound: data.ymm_found,
    templateFound: data.template_found,
    characterName: data.character_name,
    characterNames: data.character_names ?? (data.character_name ? [data.character_name] : []),
    playbackRate: data.playback_rate,
    volume: data.volume,
    ready: data.ready,
  }
}

export async function loadVoiceSettings(): Promise<VoiceSettings> {
  return mapVoiceSettings(await parseResponse(await fetch('/api/voice/settings')))
}

export async function saveVoiceSettings(settings: Pick<VoiceSettings, 'ymmPath' | 'characterName' | 'playbackRate' | 'volume'>): Promise<VoiceSettings> {
  const response = await fetch('/api/voice/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ymm_path: settings.ymmPath, character_name: settings.characterName, playback_rate: settings.playbackRate, volume: settings.volume }),
  })
  return mapVoiceSettings(await parseResponse(response))
}

export async function uploadVoiceTemplate(file: File): Promise<VoiceSettings> {
  const form = new FormData()
  form.append('file', file)
  return mapVoiceSettings(await parseResponse(await fetch('/api/voice/template', { method: 'POST', body: form })))
}

function mapVoiceJob(data: {
  id: string; status: VoiceJob['status']; message: string
  audio_url?: string | null; cached: boolean
}): VoiceJob {
  return {
    id: data.id,
    status: data.status,
    message: data.message,
    audioUrl: data.audio_url,
    cached: data.cached,
  }
}

export async function startVoiceJob(text: string, force = false): Promise<VoiceJob> {
  return mapVoiceJob(await parseResponse(await fetch('/api/voice/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, force }),
  })))
}

export async function loadVoiceJob(jobId: string, signal?: AbortSignal): Promise<VoiceJob> {
  return mapVoiceJob(await parseResponse(await fetch(`/api/voice/jobs/${encodeURIComponent(jobId)}`, { signal })))
}

export async function cancelVoiceJob(jobId: string): Promise<VoiceJob> {
  return mapVoiceJob(await parseResponse(await fetch(`/api/voice/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' })))
}

export async function importPlainText(title: string, text: string): Promise<ImportedBook> {
  const response = await fetch('/api/import/text', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, text }),
  })
  return parseResponse(response)
}

export async function importEpub(file: File): Promise<ImportedBook> {
  const form = new FormData()
  form.append('file', file)
  const response = await fetch('/api/import/epub', { method: 'POST', body: form })
  return parseResponse(response)
}

export async function uploadBookCover(bookId: string, file: File): Promise<string> {
  const form = new FormData()
  form.append('file', file)
  const response = await fetch(`/api/books/${encodeURIComponent(bookId)}/cover`, { method: 'POST', body: form })
  const result = await parseResponse<{ url: string }>(response)
  return result.url
}

export async function deleteBookCover(bookId: string): Promise<void> {
  const response = await fetch(`/api/books/${encodeURIComponent(bookId)}/cover`, { method: 'DELETE' })
  await parseResponse(response)
}

export async function importYomitanDictionary(file: File): Promise<{ source: string; entries: number }> {
  const form = new FormData()
  form.append('file', file)
  return parseResponse(await fetch('/api/dictionary/import', { method: 'POST', body: form }))
}

export type DictionarySource = { source: string; package_id: string; version: string; license: string; homepage: string; entries: number; installed_at: number }
export type LexemePage = {
  items: Lexeme[]; total: number; limit: number; offset: number
  facets: { kana: { kana: string; count: number }[]; sources: { name: string; count: number }[]; parts: { name: string; count: number }[]; groups: { name: string; count: number }[] }
}
export async function loadLexemePage(filters: { q?: string; kana?: string; source?: string; partOfSpeech?: string; group?: string; corrected?: boolean; limit?: number; offset?: number }, signal?: AbortSignal): Promise<LexemePage> {
  const query = new URLSearchParams()
  if (filters.q) query.set('q', filters.q)
  if (filters.kana) query.set('kana', filters.kana)
  if (filters.source) query.set('source', filters.source)
  if (filters.partOfSpeech) query.set('part_of_speech', filters.partOfSpeech)
  if (filters.group) query.set('group', filters.group)
  if (filters.corrected !== undefined) query.set('corrected', String(filters.corrected))
  query.set('limit', String(filters.limit ?? 80)); query.set('offset', String(filters.offset ?? 0))
  return parseResponse(await fetch(`/api/library/lexemes?${query}`, { signal }))
}
export async function bulkUpdateLexemes(keys: string[], operation: 'replace_senses' | 'add_group' | 'remove_group' | 'mark_corrected', value: unknown): Promise<{ updated: number; items: Lexeme[] }> {
  return parseResponse(await fetch('/api/library/lexemes/bulk', {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ keys, operation, value }),
  }))
}

export async function lookupDictionary(lemma: string, reading: string, surface = '', signal?: AbortSignal): Promise<Lexeme | null> {
  const params = new URLSearchParams({ lemma, reading, surface })
  const response = await parseResponse<{ entry: { lemma: string; reading: string; senses_zh: string[]; source: string } | null }>(await fetch(`/api/dictionary/lookup?${params}`, { signal }))
  return response.entry ? {
    key: `${response.entry.lemma}|${response.entry.reading}|词典`,
    lemma: response.entry.lemma,
    reading: response.entry.reading,
    firstKana: response.entry.reading[0] || '未',
    part_of_speech: '本地词典',
    senses_zh: response.entry.senses_zh,
    source: response.entry.source,
    updatedAt: 0,
  } : null
}

export async function explainSentence(
  sentence: AnalyzeResponse['sentences'][number],
  tokens: AnalyzeResponse['tokens'],
  settings: ApiSettings,
  signal?: AbortSignal,
  annotationMode: 'none' | 'grammar' = 'none',
  detailMode: 'meaning' | 'full' = 'full',
  _contextBefore: string[] = [],
): Promise<AnalyzeResponse> {
  const response = await fetch('/api/sentences/explain', {
    method: 'POST',
    headers: settings.apiKey
      ? { 'Content-Type': 'application/json', 'X-API-Key': settings.apiKey }
      : { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      sentence,
      tokens,
      annotation_mode: annotationMode,
      detail_mode: detailMode,
      settings: { base_url: settings.baseUrl, model: settings.model },
    }),
    signal,
  })
  return parseResponse(response)
}

export type WordCorrection = { context_sense: { token_id: string; gloss_zh: string }; lexeme: Omit<Lexeme, 'firstKana' | 'updatedAt'> }
export async function correctWordSense(sentence: Sentence, token: Token, currentSenses: string[], hint: string, signal?: AbortSignal): Promise<WordCorrection> {
  return parseResponse(await fetch('/api/words/correct', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
    body: JSON.stringify({ sentence, token, current_senses: currentSenses.slice(0, 20), hint }),
  }))
}

export async function explainSentences(
  items: Array<{ sentence: Sentence; tokens: Token[] }>,
  settings: ApiSettings,
  annotationMode: 'none' | 'grammar' = 'none',
  signal?: AbortSignal,
  detailMode: 'meaning' | 'full' = 'full',
  _contextBefore: string[] = [],
): Promise<AnalyzeResponse> {
  const response = await fetch('/api/sentences/explain-batch', {
    method: 'POST',
    headers: settings.apiKey
      ? { 'Content-Type': 'application/json', 'X-API-Key': settings.apiKey }
      : { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      items: items.map(({ sentence, tokens }) => ({
        sentence,
        tokens: tokens.map(({ lexemeKey: _lexemeKey, ...token }) => token),
      })),
      annotation_mode: annotationMode,
      detail_mode: detailMode,
      settings: { base_url: settings.baseUrl, model: settings.model },
    }),
    signal,
  })
  return parseResponse(response)
}

export async function loadTranslationQueue(
  bookId: string,
  chapterId: string,
  detailMode: 'meaning' | 'full',
  includeTokens: boolean,
  cursor = '',
  signal?: AbortSignal,
): Promise<TranslationQueuePage> {
  const params = new URLSearchParams({
    chapter_id: chapterId,
    detail_mode: detailMode,
    include_tokens: String(includeTokens),
    limit: '160',
  })
  if (cursor) params.set('cursor', cursor)
  return parseResponse(await fetch(
    `/api/library/books/${encodeURIComponent(bookId)}/translation-queue?${params}`,
    { signal },
  ))
}

export async function segmentChapter(chapterId: string, text: string, blocks: ContentBlock[], settings: ApiSettings, signal?: AbortSignal): Promise<AnalyzeResponse> {
  const response = await fetch('/api/chapters/segment', {
    method: 'POST',
    headers: settings.apiKey
      ? { 'Content-Type': 'application/json', 'X-API-Key': settings.apiKey }
      : { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      chapter_id: chapterId,
      text,
      blocks,
      settings: { base_url: settings.baseUrl, model: settings.model },
    }),
    signal,
  })
  return parseResponse(response)
}

export type LearningEventInput = {
  id: string
  item: {
    id: string
    type: 'vocabulary' | 'sense' | 'expression' | 'grammar'
    canonical_key: string
    lemma?: string
    reading?: string
    grammar_pattern?: string
  }
  event_type: 'lookup' | 'repeated_lookup' | 'translation_reveal' | 'grammar_reveal' | 'mark_unknown' | 'mark_mastered' | 'natural_exposure' | 'srs_again' | 'srs_hard' | 'srs_good' | 'srs_easy'
  occurred_at: number
  context?: Record<string, unknown>
}

export async function recordLearningEvents(events: LearningEventInput[]): Promise<void> {
  if (!events.length) return
  await parseResponse(await fetch('/api/learning/events', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ events }),
  }))
}

export type Blindspot = {
  knowledge_item_id: string
  type: 'vocabulary' | 'sense' | 'expression' | 'grammar'
  canonical_key: string
  lemma: string
  reading: string
  grammar_pattern: string
  mastery: number
  confidence: number
  lookup_count: number
  reasons: string[]
}

export async function loadBlindspots(signal?: AbortSignal): Promise<Blindspot[]> {
  return parseResponse(await fetch('/api/learning/blindspots?limit=100', { signal }))
}

export type KnowledgeState = {
  type: 'vocabulary' | 'sense' | 'expression' | 'grammar'; canonical_key: string
  lemma: string; reading: string; grammar_pattern: string; mastery: number; confidence: number
  exposure_count: number; lookup_count: number; last_seen_at: number | null
}

export async function loadKnowledgeStates(items: LearningEventInput['item'][], signal?: AbortSignal): Promise<KnowledgeState[]> {
  if (!items.length) return []
  return parseResponse(await fetch('/api/learning/states', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ items }), signal,
  }))
}

export type CardCandidate = {
  source?: GrammarCardSource
  id: string; knowledge_item_id: string; lemma: string; reading: string; gloss: string
  sentence: string; book_id: string; book_title: string; chapter_id: string; sentence_id: string
  card_template: string; status: string; created_at: number; updated_at: number
}

export type StudyCard = {
  source?: GrammarCardSource
  id: string; note_id: string; knowledge_item_id: string; lemma: string; reading: string; gloss: string
  sentence: string; book_id: string; book_title: string; chapter_id: string; sentence_id: string
  card_template: string; status: string; tags: string[]; difficulty: number; stability: number
  retrievability: number; due_at: number; reps: number; lapses: number; updated_at: number
}

export interface GrammarCardSource {
  kind: 'grammar'; grammar_id: string; span_id: string; quote: string; start: number; end: number
  chapter_anchor: number; analysis_revision: number; rules_version: string; original_sentence: string
  question: string; answer: string; base_form: string; explanation: string; derivation: string[]
}

export async function createCardCandidate(payload: Record<string, unknown>): Promise<CardCandidate> {
  return parseResponse(await fetch('/api/cards/candidates', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }))
}

export async function loadCardCandidates(signal?: AbortSignal): Promise<CardCandidate[]> {
  return parseResponse(await fetch('/api/cards/candidates', { signal }))
}

export async function acceptCardCandidate(id: string): Promise<StudyCard> {
  return parseResponse(await fetch(`/api/cards/candidates/${encodeURIComponent(id)}/accept`, { method: 'POST' }))
}

export async function rejectCardCandidate(id: string): Promise<void> {
  await parseResponse(await fetch(`/api/cards/candidates/${encodeURIComponent(id)}/reject`, { method: 'POST' }))
}

export type CardPage = { items: StudyCard[]; total: number; limit: number; offset: number }

export async function loadCards(filters: { q?: string; status?: string; due?: string; limit?: number; offset?: number } = {}, signal?: AbortSignal): Promise<CardPage> {
  const query = new URLSearchParams()
  if (filters.q) query.set('q', filters.q)
  if (filters.status) query.set('status', filters.status)
  if (filters.due) query.set('due', filters.due)
  if (filters.limit) query.set('limit', String(filters.limit))
  if (filters.offset) query.set('offset', String(filters.offset))
  return parseResponse(await fetch(`/api/cards/page?${query}`, { signal }))
}

export async function updateCard(cardId: string, fields: Pick<StudyCard, 'lemma' | 'reading' | 'gloss' | 'sentence'>): Promise<StudyCard & { undo_id: string }> {
  return parseResponse(await fetch(`/api/cards/${encodeURIComponent(cardId)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(fields),
  }))
}

export async function undoCardAction(undoId: string): Promise<{ restored: number }> {
  return parseResponse(await fetch(`/api/cards/undo/${encodeURIComponent(undoId)}`, { method: 'POST' }))
}

export async function mergeCards(targetCardId: string, sourceCardIds: string[]): Promise<{ target_card_id: string; merged: number }> {
  return parseResponse(await fetch('/api/cards/merge', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target_card_id: targetCardId, source_card_ids: sourceCardIds }),
  }))
}

export type CardTagSummary = { name: string; count: number }
export async function loadCardTags(signal?: AbortSignal): Promise<CardTagSummary[]> {
  return parseResponse(await fetch('/api/cards/tags', { signal }))
}

export type CardPreferences = { daily_new_limit: number; daily_review_limit: number }
export async function loadCardPreferences(signal?: AbortSignal): Promise<CardPreferences> {
  return parseResponse(await fetch('/api/cards/preferences', { signal }))
}
export async function saveCardPreferences(value: CardPreferences): Promise<CardPreferences> {
  return parseResponse(await fetch('/api/cards/preferences', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
  }))
}

export async function updateCardStatuses(cardIds: string[], status: 'active' | 'suspended' | 'archived'): Promise<{ updated: number; undo_id: string }> {
  return parseResponse(await fetch('/api/cards/bulk-status', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ card_ids: cardIds, status }),
  }))
}

export async function updateCardTags(cardIds: string[], tag: string, remove = false): Promise<{ updated: number; undo_id: string }> {
  return parseResponse(await fetch('/api/cards/bulk-tags', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ card_ids: cardIds, tag, remove }),
  }))
}

export type CardSummary = { candidates: number; active: number; due_now: number; due_7_days: number; due_30_days: number; new_today: number; daily_new_limit: number; daily_review_limit: number }

export async function loadCardSummary(signal?: AbortSignal): Promise<CardSummary> {
  return parseResponse(await fetch('/api/cards/summary', { signal }))
}

export type SavedCardView = { id: string; name: string; query: { q?: string; status?: string; due?: string }; updated_at: number }

export async function loadSavedCardViews(signal?: AbortSignal): Promise<SavedCardView[]> {
  return parseResponse(await fetch('/api/cards/views/saved', { signal }))
}

export async function saveCardView(name: string, query: SavedCardView['query']): Promise<SavedCardView> {
  return parseResponse(await fetch('/api/cards/views/saved', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, query }),
  }))
}

export async function deleteCardView(id: string): Promise<void> {
  await parseResponse(await fetch(`/api/cards/views/saved/${encodeURIComponent(id)}`, { method: 'DELETE' }))
}

export async function loadDueCards(signal?: AbortSignal): Promise<StudyCard[]> {
  return parseResponse(await fetch('/api/cards/due', { signal }))
}

export async function reviewCard(cardId: string, rating: 'again' | 'hard' | 'good' | 'easy'): Promise<void> {
  await parseResponse(await fetch(`/api/cards/${encodeURIComponent(cardId)}/review`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: crypto.randomUUID(), rating, reviewed_at: Date.now() / 1000 }),
  }))
  await recordActivityMetric('review', { cards_reviewed: 1 }).catch(() => undefined)
}

export async function downloadFullBackup(schemaVersion?: 1 | 3): Promise<void> {
  const response = await fetch(`/api/data/backup${schemaVersion === undefined ? '' : `?schema_version=${schemaVersion}`}`)
  if (!response.ok) throw new Error((await response.text()) || '无法创建备份')
  const blob = await response.blob()
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const encoded = disposition.match(/filename\*=utf-8''([^;]+)/i)?.[1]
  const filename = encoded ? decodeURIComponent(encoded) : `冰读备份-${new Date().toISOString().slice(0, 10)}.zip`
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url; link.download = filename; link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export async function restoreFullBackup(file: File): Promise<{ restored_rows: number; safety_backup: string }> {
  const form = new FormData(); form.append('file', file)
  return parseResponse(await fetch('/api/data/restore', { method: 'POST', body: form }))
}

export async function downloadBookTransfer(bookIds?: string[], schemaVersion?: 1 | 2 | 3): Promise<void> {
  const response = bookIds ? await fetch('/api/data/book-share', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ book_ids: bookIds }),
  }) : await fetch(`/api/data/book-transfer${schemaVersion === undefined ? '' : `?schema_version=${schemaVersion}`}`)
  if (!response.ok) throw new Error((await response.text()) || '无法创建书籍迁移包')
  const blob = await response.blob()
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const encoded = disposition.match(/filename\*=utf-8''([^;]+)/i)?.[1]
  const filename = encoded ? decodeURIComponent(encoded) : `冰读书籍迁移包-${new Date().toISOString().slice(0, 10)}.zip`
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url; link.download = filename; link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export async function importBookTransfer(file: File): Promise<{ imported_books: number; skipped_books: number; imported_records: number; imported_cards?: number; imported_learning_records?: number }> {
  const form = new FormData(); form.append('file', file)
  return parseResponse(await fetch('/api/data/book-transfer/import', { method: 'POST', body: form }))
}

export type ActivityType = 'reading' | 'cards' | 'review' | 'dictionary'
export type ActivityDay = {
  local_date: string; active_seconds: number; reading_seconds: number; review_seconds: number
  card_seconds: number; cards_reviewed: number; sentences_read: number; lookup_count: number
}
export type ActivitySummary = {
  active_seconds: number; reading_seconds: number; review_seconds: number; card_seconds: number
  cards_reviewed: number; sentences_read: number; lookup_count: number
  days: number; current_streak: number; longest_streak: number
}

export async function sendActivityHeartbeat(payload: Record<string, unknown>): Promise<void> {
  await parseResponse(await fetch('/api/me/activity/heartbeat', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }))
}

export async function recordActivityMetric(activityType: ActivityType, counters: { cards_reviewed?: number; sentences_read?: number; lookup_count?: number }): Promise<void> {
  const now = new Date()
  const localDate = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
  await sendActivityHeartbeat({
    id: crypto.randomUUID(), session_id: `metric-${crypto.randomUUID()}`, activity_type: activityType,
    window_start: now.getTime() / 1000, window_end: now.getTime() / 1000, local_date: localDate,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'local', ...counters,
  })
}

export async function loadActivityHeatmap(signal?: AbortSignal): Promise<ActivityDay[]> {
  return parseResponse(await fetch('/api/me/activity/heatmap', { signal }))
}

export async function loadActivitySummary(days = 7, signal?: AbortSignal): Promise<ActivitySummary> {
  return parseResponse(await fetch(`/api/me/activity/summary?days=${days}`, { signal }))
}

export type SyncStatus = { cursor: number; entities: number; tombstones: number; conflicts: number; devices: number; bindings: number; content_scope: string }

export async function loadSyncStatus(signal?: AbortSignal): Promise<SyncStatus> {
  return parseResponse(await fetch('/api/sync/status', { signal }))
}

export type CloudAccountStatus = {
  connected: boolean; base_url: string; cloud_user_id?: string; email?: string; display_name?: string
  role?: 'user' | 'admin'
  email_verified?: boolean; remote_cursor?: number; local_cursor?: number; last_sync_at?: number | null
  last_error?: string | null; development_verification_token?: string | null
}
export type AdminCloudUser = {
  id: string; email: string; display_name: string; email_verified: boolean; role: 'user' | 'admin'
  created_at: number; disabled: boolean; active_sessions: number; sync_entities: number
}
export type AdminCloudUsersPage = { items: AdminCloudUser[]; total: number; limit: number; offset: number }
export async function loadAdminCloudUsers(query = '', limit = 50, offset = 0): Promise<AdminCloudUsersPage> {
  return parseResponse(await fetch(`/api/cloud/admin/users?query=${encodeURIComponent(query)}&limit=${limit}&offset=${offset}`))
}
export async function setAdminCloudUserDisabled(userId: string, disabled: boolean): Promise<AdminCloudUser> {
  return parseResponse(await fetch(`/api/cloud/admin/users/${encodeURIComponent(userId)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ disabled }),
  }))
}
export type CloudProvider = { id: string; name: string }
export type CloudConflict = { id: string; entity_type: string; versions: { entity_id: string; payload: Record<string, unknown>; updated_at: number; source_device_id: string }[] }
export async function loadCloudStatus(signal?: AbortSignal): Promise<CloudAccountStatus> { return parseResponse(await fetch('/api/cloud/status', { signal })) }
export async function saveCloudSettings(baseUrl: string): Promise<{ base_url: string; remote: Record<string, unknown> }> {
  return parseResponse(await fetch('/api/cloud/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ base_url: baseUrl }) }))
}
export async function loadCloudProviders(signal?: AbortSignal): Promise<CloudProvider[]> { return parseResponse(await fetch('/api/cloud/providers', { signal })) }
export async function registerCloudAccount(email: string, password: string, displayName: string): Promise<CloudAccountStatus> {
  return parseResponse(await fetch('/api/cloud/register', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password, display_name: displayName }) }))
}
export async function loginCloudAccount(email: string, password: string): Promise<CloudAccountStatus> {
  return parseResponse(await fetch('/api/cloud/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }) }))
}
export async function logoutCloudAccount(): Promise<void> { await parseResponse(await fetch('/api/cloud/logout', { method: 'POST' })) }
export async function updateCloudDisplayName(displayName: string): Promise<CloudAccountStatus> {
  return parseResponse(await fetch('/api/cloud/profile', {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ display_name: displayName }),
  }))
}
export async function startCloudOidc(providerId: string): Promise<string> {
  const callback = `${window.location.origin}/api/cloud/oidc/complete`
  const result = await parseResponse<{ url: string }>(await fetch(`/api/cloud/oidc/start/${encodeURIComponent(providerId)}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ callback_url: callback }) }))
  return result.url
}
export async function requestCloudEmailVerification(email: string): Promise<Record<string, unknown>> { return parseResponse(await fetch('/api/cloud/email/request-verification', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email }) })) }
export async function verifyCloudEmail(token: string): Promise<CloudAccountStatus> { return parseResponse(await fetch('/api/cloud/email/verify', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }) })) }
export async function requestCloudPasswordReset(email: string): Promise<Record<string, unknown>> { return parseResponse(await fetch('/api/cloud/password/request-reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email }) })) }
export async function resetCloudPassword(token: string, newPassword: string): Promise<Record<string, unknown>> { return parseResponse(await fetch('/api/cloud/password/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token, new_password: newPassword }) })) }
export async function syncCloud(pullOnly = false): Promise<Record<string, unknown> & CloudAccountStatus> { return parseResponse(await fetch(pullOnly ? '/api/cloud/pull' : '/api/cloud/sync', { method: 'POST' })) }
export async function loadCloudConflicts(signal?: AbortSignal): Promise<CloudConflict[]> { return parseResponse(await fetch('/api/cloud/conflicts', { signal })) }
export async function resolveCloudConflict(groupId: string, winnerEntityId: string): Promise<Record<string, unknown>> { return parseResponse(await fetch(`/api/cloud/conflicts/${encodeURIComponent(groupId)}/resolve`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ winner_entity_id: winnerEntityId }) })) }
export type CloudSession = { id: string; device_id: string; device_name: string; created_at: number; last_used_at: number; current?: boolean }
export async function loadCloudSessions(signal?: AbortSignal): Promise<CloudSession[]> { return parseResponse(await fetch('/api/cloud/sessions', { signal })) }
export async function revokeCloudSession(id: string): Promise<void> { await parseResponse(await fetch(`/api/cloud/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' })) }
