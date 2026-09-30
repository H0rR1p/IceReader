import Dexie, { type EntityTable } from 'dexie'
import { loadCurrentUser } from './app/session'
import type { Annotation, Book, Chapter, ContextSense, Lexeme, Sentence, SentenceBookmark, Token } from './types'

class ReaderDatabase extends Dexie {
  books!: EntityTable<Book, 'id'>
  chapters!: EntityTable<Chapter, 'id'>
  sentences!: EntityTable<Sentence, 'id'>
  tokens!: EntityTable<Token, 'id'>
  annotations!: EntityTable<Annotation, 'id'>
  contextSenses!: EntityTable<ContextSense, 'token_id'>
  lexemes!: EntityTable<Lexeme, 'key'>
  bookmarks!: EntityTable<SentenceBookmark, 'id'>

  constructor(databaseName: string) {
    super(databaseName)
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
    this.version(3).stores({
      books: 'id, updatedAt, title',
      chapters: 'id, bookId, [bookId+order], status',
      sentences: 'id, chapter_id, [chapter_id+start]',
      tokens: 'id, sentence_id, lexemeKey',
      annotations: 'id, sentence_id',
      contextSenses: 'token_id',
      lexemes: 'key, lemma, reading, firstKana, part_of_speech, correctedByUser',
      cards: 'id, lexemeKey, bookId, chapterId, createdAt',
      bookmarks: 'id, bookId, chapterId, sentenceId, [bookId+chapterOrder+sentenceStart], createdAt',
    })
    this.version(4).stores({
      books: 'id, updatedAt, title',
      chapters: 'id, bookId, [bookId+order], status',
      sentences: 'id, chapter_id, [chapter_id+start]',
      tokens: 'id, sentence_id, lexemeKey',
      annotations: 'id, sentence_id',
      contextSenses: 'token_id',
      lexemes: 'key, lemma, reading, firstKana, part_of_speech, correctedByUser',
      cards: null,
      bookmarks: 'id, bookId, chapterId, sentenceId, [bookId+chapterOrder+sentenceStart], createdAt',
    })
  }
}

export let db = new ReaderDatabase('bingdu-reader-bootstrap')
let databaseUserId = ''

export async function ensureUserDatabase() {
  const user = await loadCurrentUser()
  if (databaseUserId === user.user_id) return user
  db.close()
  db = new ReaderDatabase(`bingdu-reader-${user.user_id}`)
  databaseUserId = user.user_id
  return user
}

const DATA_TABLES = ['books', 'chapters', 'sentences', 'tokens', 'annotations', 'contextSenses', 'lexemes', 'bookmarks'] as const
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
  await ensureUserDatabase()
  const response = await expectOk(await fetch('/api/library/index', { signal }), '无法读取书籍索引')
  const snapshot = await response.json() as { books: Book[]; chapters: Array<Omit<Chapter, 'text' | 'blocks'> & Partial<Pick<Chapter, 'text' | 'blocks'>>> }
  const cached = await db.chapters.bulkGet(snapshot.chapters.map((chapter) => chapter.id))
  const chapters: Chapter[] = snapshot.chapters.map((chapter, index) => ({
    ...chapter,
    text: cached[index]?.text ?? chapter.text ?? '',
    blocks: cached[index]?.blocks ?? chapter.blocks ?? [],
    originalHtmlUrl: cached[index]?.originalHtmlUrl ?? chapter.originalHtmlUrl,
  }))
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
  await db.transaction('rw', [db.chapters, db.sentences], async () => {
    await db.chapters.put(chapter)
    if (snapshot.sentences.length) await db.sentences.bulkPut(snapshot.sentences)
  })
  return { ...snapshot, chapter }
}

export async function loadChapterDetails(chapterId: string, offset: number, limit: number, signal?: AbortSignal) {
  const sentences = await db.sentences.where('chapter_id').equals(chapterId).sortBy('start')
  const sentenceIds = sentences.slice(offset, offset + limit).map((sentence) => sentence.id)
  const cachedTokens = sentenceIds.length
    ? await db.tokens.where('sentence_id').anyOf(sentenceIds).toArray()
    : []
  const covered = new Set(cachedTokens.map((token) => token.sentence_id))
  if (sentenceIds.length && sentenceIds.every((id) => covered.has(id))) {
    const annotations = await db.annotations.where('sentence_id').anyOf(sentenceIds).toArray()
    const tokenIds = cachedTokens.map((token) => token.id)
    const contextSenses = tokenIds.length
      ? await db.contextSenses.where('token_id').anyOf(tokenIds).toArray()
      : []
    return { offset, limit, sentence_ids: sentenceIds, tokens: cachedTokens, annotations, contextSenses, lexemes: [] as Lexeme[] }
  }

  const response = await expectOk(await fetch(
    `/api/library/chapters/${encodeURIComponent(chapterId)}/details?offset=${offset}&limit=${limit}`,
    { signal },
  ), '无法读取章节词元')
  const snapshot = await response.json() as {
    offset: number; limit: number; sentence_ids: string[]; tokens: Token[]; annotations: Annotation[]
    contextSenses: ContextSense[]; lexemes: Lexeme[]
  }
  await db.transaction('rw', [db.tokens, db.annotations, db.contextSenses, db.lexemes], async () => {
    if (snapshot.tokens.length) await db.tokens.bulkPut(snapshot.tokens)
    if (snapshot.annotations.length) await db.annotations.bulkPut(snapshot.annotations)
    if (snapshot.contextSenses.length) await db.contextSenses.bulkPut(snapshot.contextSenses)
    if (snapshot.lexemes.length) await db.lexemes.bulkPut(snapshot.lexemes)
  })
  return snapshot
}

export async function loadStudyData(signal?: AbortSignal) {
  const response = await expectOk(await fetch('/api/library/study-data', { signal }), '无法读取个人词库')
  const snapshot = await response.json() as { lexemes: Lexeme[] }
  await db.transaction('rw', db.lexemes, async () => {
    await db.lexemes.clear()
    if (snapshot.lexemes.length) await db.lexemes.bulkPut(snapshot.lexemes)
  })
  return snapshot
}

export async function loadBookBookmarks(bookId: string, signal?: AbortSignal) {
  const params = new URLSearchParams({ book_id: bookId })
  const response = await expectOk(await fetch(`/api/library/bookmarks?${params}`, { signal }), '无法读取书签')
  const bookmarks = await response.json() as SentenceBookmark[]
  await db.transaction('rw', db.bookmarks, async () => {
    await db.bookmarks.where('bookId').equals(bookId).delete()
    if (bookmarks.length) await db.bookmarks.bulkPut(bookmarks)
  })
  return bookmarks
}

export async function removeBook(bookId: string) {
  await expectOk(await fetch(`/api/library/books/${encodeURIComponent(bookId)}`, { method: 'DELETE' }), '无法删除书籍')
  const chapters = await db.chapters.where('bookId').equals(bookId).toArray()
  const chapterIds = chapters.map((chapter) => chapter.id)
  const sentences = await db.sentences.where('chapter_id').anyOf(chapterIds).toArray()
  const sentenceIds = sentences.map((sentence) => sentence.id)
  const tokens = await db.tokens.where('sentence_id').anyOf(sentenceIds).toArray()
  await db.transaction('rw', [db.books, db.chapters, db.sentences, db.tokens, db.annotations, db.contextSenses, db.bookmarks], async () => {
    await db.contextSenses.bulkDelete(tokens.map((token) => token.id))
    await db.annotations.where('sentence_id').anyOf(sentenceIds).delete()
    await db.tokens.where('sentence_id').anyOf(sentenceIds).delete()
    await db.sentences.where('chapter_id').anyOf(chapterIds).delete()
    await db.bookmarks.where('bookId').equals(bookId).delete()
    await db.chapters.where('bookId').equals(bookId).delete()
    await db.books.delete(bookId)
  })
}
