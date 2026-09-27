import type { AnalyzeResponse, ApiSettings, ContentBlock, ImportedBook, Lexeme, VoiceJob, VoiceSettings } from './types'

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `请求失败（${response.status}）`
    try {
      const body = await response.json()
      message = body.detail || message
    } catch {
      // Keep the status-based message.
    }
    throw new Error(message)
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
  const data = await parseResponse<{ base_url: string; model: string; has_api_key: boolean }>(response)
  return { apiKey: '', baseUrl: data.base_url, model: data.model, hasStoredApiKey: data.has_api_key }
}

export async function saveApiSettings(settings: ApiSettings): Promise<ApiSettings> {
  const response = await fetch('/api/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: settings.apiKey || null, base_url: settings.baseUrl, model: settings.model }),
  })
  const data = await parseResponse<{ base_url: string; model: string; has_api_key: boolean }>(response)
  return { apiKey: '', baseUrl: data.base_url, model: data.model, hasStoredApiKey: data.has_api_key }
}

function mapVoiceSettings(data: {
  ymm_path: string; ymm_found: boolean; template_found: boolean; character_name: string
  playback_rate: number; volume: number; ready: boolean
}): VoiceSettings {
  return {
    ymmPath: data.ymm_path,
    ymmFound: data.ymm_found,
    templateFound: data.template_found,
    characterName: data.character_name,
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
): Promise<AnalyzeResponse> {
  const response = await fetch('/api/sentences/explain', {
    method: 'POST',
    headers: settings.apiKey
      ? { 'Content-Type': 'application/json', 'X-API-Key': settings.apiKey }
      : { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      sentence,
      tokens,
      settings: { base_url: settings.baseUrl, model: settings.model },
    }),
    signal,
  })
  return parseResponse(response)
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
