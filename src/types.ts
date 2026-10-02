export type ChapterStatus = 'pending' | 'local-ready' | 'processing' | 'complete' | 'partial-failed' | 'failed'

export interface CurrentUser {
  user_id: string
  display_name: string
  avatar_url?: string | null
  created_at: number
  session_id: string
  device_id: string
  auth_provider: string
  username?: string | null
  is_guest?: boolean
}

export interface ContentBlock {
  id: string
  type: 'heading' | 'paragraph' | 'quote' | 'list-item' | 'image' | 'page-break' | 'separator'
  start: number
  end: number
  text: string
  level?: number | null
  asset_url?: string | null
  alt?: string
  placement?: 'left' | 'center' | 'right' | 'inline'
  width?: number | null
  height?: number | null
}

export interface Book {
  id: string
  title: string
  author: string
  coverUrl?: string
  customCover?: boolean
  createdAt: number
  updatedAt: number
  lastOpenedAt?: number
  currentChapterId?: string
  currentSentenceId?: string
  collectionId?: string
  collectionName?: string
  translationComplete?: boolean
  showImages?: boolean
}

export interface Chapter {
  id: string
  bookId: string
  title: string
  order: number
  text: string
  blocks?: ContentBlock[]
  originalHtmlUrl?: string
  status: ChapterStatus
  error?: string
}

export interface Sentence {
  id: string
  chapter_id: string
  start: number
  end: number
  original: string
  translation_zh: string
  status: 'complete' | 'failed'
  error?: string | null
  explanation_status?: 'idle' | 'processing' | 'complete' | 'failed'
  explanation_detail?: 'meaning' | 'full' | null
}

export interface TranslationQueueItem {
  sentence: Sentence
  tokens: Token[]
  chapterTitle: string
  chapterOrder: number
}

export interface TranslationQueuePage {
  items: TranslationQueueItem[]
  nextCursor: string | null
  contextBefore: string[]
}

export interface Token {
  id: string
  sentence_id: string
  start: number
  end: number
  surface: string
  lemma: string
  reading: string
  part_of_speech: string
  is_content: boolean
  lexemeKey: string
}

export interface Annotation {
  id: string
  sentence_id: string
  type: 'grammar' | 'pragmatics' | 'ellipsis' | 'culture'
  anchor_start: number
  anchor_end: number
  quote: string
  structure?: string
  explanation_zh: string
}

export interface ContextSense {
  token_id: string
  gloss_zh: string
}

export interface Lexeme {
  key: string
  lemma: string
  reading: string
  firstKana: string
  part_of_speech: string
  senses_zh: string[]
  source: string
  updatedAt: number
  correctedByUser?: boolean
  groups?: string[]
}

export interface SentenceBookmark {
  id: string
  bookId: string
  chapterId: string
  sentenceId: string
  chapterTitle: string
  chapterOrder: number
  sentenceStart: number
  text: string
  createdAt: number
}

export interface ImportedBook {
  title: string
  author: string
  chapters: Array<{
    id: string; title: string; order: number; text: string; blocks?: ContentBlock[]; original_html_url?: string
    sentences?: Sentence[]; tokens?: Omit<Token, 'lexemeKey'>[]; segmentation_source?: 'ai-reviewed' | 'local-fallback' | 'empty'
  }>
  import_report?: {
    source_documents: number; imported_sections: number; images: number; image_references: number
    image_only_sections: number; broken_image_references: number
  } | null
}

export interface AnalyzeResponse {
  sentences: Sentence[]
  tokens: Omit<Token, 'lexemeKey'>[]
  annotations: Annotation[]
  context_senses: ContextSense[]
  lexemes: Omit<Lexeme, 'updatedAt' | 'firstKana'>[]
  warnings: string[]
}

export interface ApiSettings {
  apiKey: string
  baseUrl: string
  model: string
  hasStoredApiKey: boolean
  cacheHitUsdPerMillion: number
  cacheMissUsdPerMillion: number
  outputUsdPerMillion: number
}

export interface AiUsageSummary {
  requests: number
  items: number
  prompt_tokens: number
  cache_hit_tokens: number
  cache_miss_tokens: number
  completion_tokens: number
  duration_ms: number
  failures: number
  response_cache_entries: number
  response_cache_hits: number
  estimated_cost_usd: number
  pricing_configured: boolean
  operations: Array<{
    operation: string
    requests: number
    items: number
    cache_hit_tokens: number
    cache_miss_tokens: number
    completion_tokens: number
    duration_ms: number
  }>
}

export interface VoiceSettings {
  ymmPath: string
  ymmFound: boolean
  templateFound: boolean
  characterName: string
  characterNames: string[]
  playbackRate: number
  volume: number
  ready: boolean
}

export interface VoiceJob {
  id: string
  status: 'queued' | 'running' | 'complete' | 'failed' | 'canceled'
  message: string
  audioUrl?: string | null
  cached: boolean
}

