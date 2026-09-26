import Dexie, { type EntityTable } from 'dexie'
import type { Annotation, Book, Chapter, ContextSense, Lexeme, Sentence, StudyCard, Token } from './types'

class ReaderDatabase extends Dexie {
  books!: EntityTable<Book, 'id'>
  chapters!: EntityTable<Chapter, 'id'>
  sentences!: EntityTable<Sentence, 'id'>
  tokens!: EntityTable<Token, 'id'>
  annotations!: EntityTable<Annotation, 'id'>
  contextSenses!: EntityTable<ContextSense, 'token_id'>
  lexemes!: EntityTable<Lexeme, 'key'>
  cards!: EntityTable<StudyCard, 'id'>

  constructor() {
    super('nichidoku-reader')
    this.version(1).stores({
      books: 'id, updatedAt, title',
      chapters: 'id, bookId, [bookId+order], status',
      sentences: 'id, chapter_id, [chapter_id+start]',
      tokens: 'id, sentence_id, lexemeKey',
      annotations: 'id, sentence_id',
      contextSenses: 'token_id',
      lexemes: 'key, lemma, reading, firstKana, part_of_speech, correctedByUser',
      cards: 'id, lexemeKey, bookId, chapterId, createdAt',
    })
    this.version(2).stores({
      books: 'id, updatedAt, title',
      chapters: 'id, bookId, [bookId+order], status',
      sentences: 'id, chapter_id, [chapter_id+start]',
      tokens: 'id, sentence_id, lexemeKey',
      annotations: 'id, sentence_id',
      contextSenses: 'token_id',
      lexemes: 'key, lemma, reading, firstKana, part_of_speech, correctedByUser',
      cards: 'id, lexemeKey, bookId, chapterId, createdAt',
    }).upgrade(async (transaction) => {
      await transaction.table('lexemes').toCollection().modify((lexeme: Lexeme) => {
        lexeme.firstKana = lexeme.firstKana || lexeme.reading?.[0] || '未'
      })
    })
  }
}

export const db = new ReaderDatabase()

const DATA_TABLES = ['books', 'chapters', 'sentences', 'tokens', 'annotations', 'contextSenses', 'lexemes', 'cards'] as const
export type DataTableName = typeof DATA_TABLES[number]
export type RecordChanges = Partial<Record<DataTableName, unknown[]>>
export type RecordDeletes = Partial<Record<DataTableName, string[]>>

async function expectOk(response: Response, message: string) {
  if (!response.ok) throw new Error(`${message}（${response.status}）`)
  return response
}

export async function syncRecords(upserts: RecordChanges = {}, deletes: RecordDeletes = {}, signal?: AbortSignal) {
  const response = await fetch('/api/library', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ upserts, deletes }),
    signal,
  })
  await expectOk(response, '无法保存项目数据')
}

export async function restoreProjectIndex(signal?: AbortSignal) {
  const response = await expectOk(await fetch('/api/library/index', { signal }), '无法读取书籍索引')
  const snapshot = await response.json() as { books: Book[]; chapters: Array<Omit<Chapter, 'text' | 'blocks'> & Partial<Pick<Chapter, 'text' | 'blocks'>>> }
  const chapters: Chapter[] = snapshot.chapters.map((chapter) => ({ ...chapter, text: '', blocks: [] }))
  await db.transaction('rw', [db.books, db.chapters], async () => {
    await db.books.clear()
    await db.chapters.clear()
    if (snapshot.books.length) await db.books.bulkPut(snapshot.books)
    if (chapters.length) await db.chapters.bulkPut(chapters)
  })
}

export async function loadChapterData(chapterId: string, signal?: AbortSignal) {
  const response = await expectOk(
    await fetch(`/api/library/chapters/${encodeURIComponent(chapterId)}`, { signal }),
    '无法读取章节数据',
  )
  const snapshot = await response.json() as {
    chapter: Chapter | null
    sentences: Sentence[]
    tokens: Token[]
    annotations: Annotation[]
    contextSenses: ContextSense[]
    lexemes: Lexeme[]
  }
  const chapter = snapshot.chapter
  if (!chapter) throw new Error('章节不存在或已被删除')
  const oldSentences = await db.sentences.where('chapter_id').equals(chapterId).toArray()
  const oldSentenceIds = oldSentences.map((sentence) => sentence.id)
  const oldTokens = oldSentenceIds.length
    ? await db.tokens.where('sentence_id').anyOf(oldSentenceIds).toArray()
    : []
  await db.transaction('rw', [db.chapters, db.sentences, db.tokens, db.annotations, db.contextSenses, db.lexemes], async () => {
    await db.chapters.put(chapter)
    if (oldTokens.length) await db.contextSenses.bulkDelete(oldTokens.map((token) => token.id))
    if (oldSentenceIds.length) {
      await db.annotations.where('sentence_id').anyOf(oldSentenceIds).delete()
      await db.tokens.where('sentence_id').anyOf(oldSentenceIds).delete()
      await db.sentences.bulkDelete(oldSentenceIds)
    }
    if (snapshot.sentences.length) await db.sentences.bulkPut(snapshot.sentences)
    if (snapshot.tokens.length) await db.tokens.bulkPut(snapshot.tokens)
    if (snapshot.annotations.length) await db.annotations.bulkPut(snapshot.annotations)
    if (snapshot.contextSenses.length) await db.contextSenses.bulkPut(snapshot.contextSenses)
    if (snapshot.lexemes.length) await db.lexemes.bulkPut(snapshot.lexemes)
  })
  return { ...snapshot, chapter }
}

export async function loadStudyData(signal?: AbortSignal) {
  const response = await expectOk(await fetch('/api/library/study-data', { signal }), '无法读取词库与词卡')
  const snapshot = await response.json() as { lexemes: Lexeme[]; cards: StudyCard[] }
  await db.transaction('rw', [db.lexemes, db.cards], async () => {
    await db.lexemes.clear()
    await db.cards.clear()
    if (snapshot.lexemes.length) await db.lexemes.bulkPut(snapshot.lexemes)
    if (snapshot.cards.length) await db.cards.bulkPut(snapshot.cards)
  })
  return snapshot
}

export async function removeBook(bookId: string) {
  await expectOk(await fetch(`/api/library/books/${encodeURIComponent(bookId)}`, { method: 'DELETE' }), '无法删除书籍')
  const chapters = await db.chapters.where('bookId').equals(bookId).toArray()
  const chapterIds = chapters.map((chapter) => chapter.id)
  const sentences = await db.sentences.where('chapter_id').anyOf(chapterIds).toArray()
  const sentenceIds = sentences.map((sentence) => sentence.id)
  const tokens = await db.tokens.where('sentence_id').anyOf(sentenceIds).toArray()
  await db.transaction('rw', [db.books, db.chapters, db.sentences, db.tokens, db.annotations, db.contextSenses, db.cards], async () => {
    await db.contextSenses.bulkDelete(tokens.map((token) => token.id))
    await db.annotations.where('sentence_id').anyOf(sentenceIds).delete()
    await db.tokens.where('sentence_id').anyOf(sentenceIds).delete()
    await db.sentences.where('chapter_id').anyOf(chapterIds).delete()
    await db.cards.where('bookId').equals(bookId).delete()
    await db.chapters.where('bookId').equals(bookId).delete()
    await db.books.delete(bookId)
  })
}
