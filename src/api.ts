import type { AnalyzeResponse, ApiSettings, ImportedBook } from './types'

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

export async function analyzeChapter(
  chapterId: string,
  text: string,
  knownLexemeKeys: string[],
  settings: ApiSettings,
  onlySentenceIds: string[] = [],
): Promise<AnalyzeResponse> {
  const response = await fetch('/api/analyze', {
    method: 'POST',
    headers: settings.apiKey
      ? { 'Content-Type': 'application/json', 'X-API-Key': settings.apiKey }
      : { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      chapter_id: chapterId,
      text,
      only_sentence_ids: onlySentenceIds,
      known_lexeme_keys: knownLexemeKeys,
      settings: { base_url: settings.baseUrl, model: settings.model },
    }),
  })
  return parseResponse(response)
}
