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

export async function removeBook(bookId: string) {
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
