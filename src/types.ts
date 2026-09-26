export type ChapterStatus = 'pending' | 'processing' | 'complete' | 'partial-failed' | 'failed'

export interface Book {
  id: string
  title: string
  author: string
  createdAt: number
  updatedAt: number
  currentChapterId?: string
  currentSentenceId?: string
}

export interface Chapter {
  id: string
  bookId: string
  title: string
  order: number
  text: string
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
}

export interface StudyCard {
  id: string
  lexemeKey: string
  tokenId: string
  bookId: string
  chapterId: string
  sentenceId: string
  surface: string
  lemma: string
  reading: string
  glossZh: string
  sentence: string
  translationZh: string
  sourceLabel: string
  createdAt: number
}

export interface ImportedBook {
  title: string
  author: string
  chapters: Array<{ id: string; title: string; order: number; text: string }>
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
}
