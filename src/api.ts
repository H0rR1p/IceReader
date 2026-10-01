import type { AiUsageSummary, AnalyzeResponse, ApiSettings, ContentBlock, ImportedBook, Lexeme, Sentence, Token, TranslationQueuePage, VoiceJob, VoiceSettings } from './types'

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
  contextBefore: string[] = [],
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
      context_before: contextBefore,
      settings: { base_url: settings.baseUrl, model: settings.model },
    }),
    signal,
  })
  return parseResponse(response)
}

export async function explainSentences(
  items: Array<{ sentence: Sentence; tokens: Token[] }>,
  settings: ApiSettings,
  annotationMode: 'none' | 'grammar' = 'none',
  signal?: AbortSignal,
  detailMode: 'meaning' | 'full' = 'full',
  contextBefore: string[] = [],
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
      context_before: contextBefore,
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
  id: string; knowledge_item_id: string; lemma: string; reading: string; gloss: string
  sentence: string; book_id: string; book_title: string; chapter_id: string; sentence_id: string
  card_template: string; status: string; created_at: number; updated_at: number
}

export type StudyCard = {
  id: string; note_id: string; knowledge_item_id: string; lemma: string; reading: string; gloss: string
  sentence: string; book_id: string; book_title: string; chapter_id: string; sentence_id: string
  card_template: string; status: string; tags: string[]; difficulty: number; stability: number
  retrievability: number; due_at: number; reps: number; lapses: number; updated_at: number
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

export async function downloadFullBackup(): Promise<void> {
  const response = await fetch('/api/data/backup')
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
