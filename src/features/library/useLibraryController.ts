import { useCallback, useEffect, useRef, useState } from 'react'

import { deleteBookCover, uploadBookCover } from '../../api'
import { db, loadChapterData, removeBook, restoreProjectIndex, syncRecords } from '../../db'
import type { Book, Chapter, ImportedBook, Sentence, Token } from '../../types'


function makeLexemeKey(token: Pick<Token, 'lemma' | 'reading' | 'part_of_speech'>) {
  return `${token.lemma}|${token.reading}|${token.part_of_speech}`
}

function newId(prefix: string) {
  return `${prefix}_${crypto.randomUUID()}`
}

export function useLibraryController(onNotice: (message: string) => void) {
  const [books, setBooks] = useState<Book[]>([])
  const [activeBook, setActiveBook] = useState<Book | null>(null)
  const [activeChapter, setActiveChapter] = useState<Chapter | null>(null)
  const [loadingBookId, setLoadingBookId] = useState<string | null>(null)
  const [loadingChapterId, setLoadingChapterId] = useState<string | null>(null)
  const [libraryLoading, setLibraryLoading] = useState(true)
  const navigationAbortRef = useRef<AbortController | null>(null)

  const refreshBooks = useCallback(async () => {
    const rows = await db.books.orderBy('updatedAt').reverse().toArray()
    const changedBooks: Book[] = []
    for (const book of rows) {
      if (book.coverUrl) continue
      const chapters = await db.chapters.where('bookId').equals(book.id).sortBy('order')
      const coverUrl = chapters.flatMap((chapter) => chapter.blocks ?? [])
        .find((block) => block.type === 'image' && block.asset_url)?.asset_url
      if (coverUrl) {
        book.coverUrl = coverUrl
        await db.books.put(book)
        changedBooks.push(book)
      }
    }
    if (changedBooks.length) await syncRecords({ books: changedBooks })
    setBooks(rows)
    setActiveBook((current) => current ? rows.find((book) => book.id === current.id) ?? null : null)
  }, [])

  const restoreLibrary = useCallback(async (signal?: AbortSignal) => {
    setLibraryLoading(true)
    try {
      await restoreProjectIndex(signal)
      if (!signal?.aborted) await refreshBooks()
    } finally {
      if (!signal?.aborted) setLibraryLoading(false)
    }
  }, [refreshBooks])

  useEffect(() => {
    const controller = new AbortController()
    void restoreLibrary(controller.signal).catch((error) => {
      if (!controller.signal.aborted) onNotice(error instanceof Error ? error.message : String(error))
    })
    return () => controller.abort()
  }, [onNotice, restoreLibrary])

  const abortNavigation = useCallback(() => {
    navigationAbortRef.current?.abort()
    navigationAbortRef.current = null
    setLoadingBookId(null)
    setLoadingChapterId(null)
  }, [])

  const returnToLibrary = useCallback(() => {
    abortNavigation()
    setActiveBook(null)
    setActiveChapter(null)
  }, [abortNavigation])

  const resetForAccount = useCallback(async () => {
    abortNavigation()
    setBooks([])
    setActiveBook(null)
    setActiveChapter(null)
    await restoreLibrary()
  }, [abortNavigation, restoreLibrary])

  async function saveImportedBook(imported: ImportedBook) {
    const bookId = newId('book')
    const now = Date.now()
    const book: Book = {
      id: bookId,
      title: imported.title,
      author: imported.author,
      coverUrl: imported.chapters.slice().sort((a, b) => a.order - b.order)
        .flatMap((chapter) => chapter.blocks ?? [])
        .find((block) => block.type === 'image' && block.asset_url)?.asset_url ?? undefined,
      createdAt: now,
      updatedAt: now,
      showImages: false,
    }
    const chapterIdMap = new Map(imported.chapters.map((chapter) => [chapter.id, `${bookId}:${chapter.id}`]))
    const sentenceIdMap = new Map(imported.chapters.flatMap((chapter) =>
      (chapter.sentences ?? []).map((sentence) => [sentence.id, `${bookId}:${sentence.id}`] as const),
    ))
    const chapters: Chapter[] = imported.chapters.map((chapter) => ({
      id: chapterIdMap.get(chapter.id)!,
      bookId,
      title: chapter.title,
      order: chapter.order,
      text: chapter.text,
      blocks: chapter.blocks,
      originalHtmlUrl: chapter.original_html_url,
      status: chapter.text.trim() ? ((chapter.sentences?.length ?? 0) > 0 ? 'local-ready' : 'pending') : 'complete',
    }))
    const sentences: Sentence[] = imported.chapters.flatMap((chapter) => (chapter.sentences ?? []).map((sentence) => ({
      ...sentence,
      id: sentenceIdMap.get(sentence.id)!,
      chapter_id: chapterIdMap.get(chapter.id)!,
      explanation_status: 'idle',
    })))
    const tokens: Token[] = imported.chapters.flatMap((chapter) => (chapter.tokens ?? []).map((token) => ({
      ...token,
      id: `${bookId}:${token.id}`,
      sentence_id: sentenceIdMap.get(token.sentence_id)!,
      lexemeKey: makeLexemeKey(token),
    })))
    await db.transaction('rw', [db.books, db.chapters, db.sentences, db.tokens], async () => {
      await db.books.add(book)
      await db.chapters.bulkAdd(chapters)
      if (sentences.length) await db.sentences.bulkAdd(sentences)
      if (tokens.length) await db.tokens.bulkAdd(tokens)
    })
    await syncRecords({ books: [book], chapters, sentences, tokens })
    returnToLibrary()
    await refreshBooks()
    if (imported.import_report) {
      const report = imported.import_report
      onNotice(`导入完成：${report.imported_sections} 节、${report.images} 张原图。打开需要阅读的章节后再单独切分。`)
    } else {
      onNotice(`《${imported.title}》导入完成。点击“切分本章”后只处理当前章节。`)
    }
  }

  async function openBook(book: Book) {
    if (loadingBookId) return
    navigationAbortRef.current?.abort()
    const controller = new AbortController()
    navigationAbortRef.current = controller
    setLoadingBookId(book.id)
    try {
      const chapters = await db.chapters.where('bookId').equals(book.id).sortBy('order')
      const preferred = chapters.find((chapter) => chapter.id === book.currentChapterId) ?? chapters[0]
      const openedBook = { ...book, lastOpenedAt: Date.now() }
      await db.books.put(openedBook)
      await syncRecords({ books: [openedBook] }, {}, controller.signal)
      setBooks((current) => current.map((item) => item.id === book.id ? openedBook : item))
      setActiveBook(openedBook)
      setActiveChapter(null)
      onNotice('')
      if (!preferred) return
      setLoadingChapterId(preferred.id)
      const snapshot = await loadChapterData(preferred.id, controller.signal)
      if (!controller.signal.aborted) setActiveChapter(snapshot.chapter)
    } catch (error) {
      if (!controller.signal.aborted) onNotice(error instanceof Error ? error.message : String(error))
    } finally {
      if (navigationAbortRef.current === controller) navigationAbortRef.current = null
      if (!controller.signal.aborted) {
        setLoadingBookId(null)
        setLoadingChapterId(null)
      }
    }
  }

  async function selectChapter(chapter: Chapter, sentenceId?: string) {
    if (!activeBook || loadingChapterId) return
    navigationAbortRef.current?.abort()
    const controller = new AbortController()
    navigationAbortRef.current = controller
    setLoadingChapterId(chapter.id)
    setActiveChapter(null)
    try {
      const snapshot = await loadChapterData(chapter.id, controller.signal)
      if (controller.signal.aborted) return
      const nextBook = {
        ...activeBook,
        currentChapterId: chapter.id,
        currentSentenceId: sentenceId,
        updatedAt: Date.now(),
      }
      await db.books.put(nextBook)
      await syncRecords({ books: [nextBook] }, {}, controller.signal)
      setActiveBook(nextBook)
      setActiveChapter(snapshot.chapter)
    } catch (error) {
      if (!controller.signal.aborted) onNotice(error instanceof Error ? error.message : String(error))
    } finally {
      if (navigationAbortRef.current === controller) navigationAbortRef.current = null
      if (!controller.signal.aborted) setLoadingChapterId(null)
    }
  }

  async function deleteBook(book: Book) {
    if (book.customCover) await deleteBookCover(book.id).catch(() => undefined)
    await removeBook(book.id)
    if (activeBook?.id === book.id) returnToLibrary()
    await refreshBooks()
  }

  async function changeBookCover(book: Book, file: File) {
    try {
      const coverUrl = await uploadBookCover(book.id, file)
      const next = { ...book, coverUrl, customCover: true, updatedAt: Date.now() }
      await db.books.put(next)
      await syncRecords({ books: [next] })
      setActiveBook((current) => current?.id === book.id ? next : current)
      await refreshBooks()
      onNotice(`《${book.title}》的封面已更新。`)
    } catch (error) {
      onNotice(error instanceof Error ? error.message : String(error))
    }
  }

  async function setBookImageVisibility(book: Book, showImages: boolean) {
    const next = { ...book, showImages, updatedAt: Date.now() }
    await db.books.put(next)
    await syncRecords({ books: [next] })
    setBooks((current) => current.map((item) => item.id === book.id ? next : item))
    setActiveBook((current) => current?.id === book.id ? next : current)
  }

  async function saveBookCollection(collectionId: string, collectionName: string, bookIds: string[]) {
    const selected = new Set(bookIds)
    const affected = books.filter((book) => selected.has(book.id) || book.collectionId === collectionId)
    const updated = affected.map((book) => {
      const next: Book = { ...book, updatedAt: Date.now() }
      if (selected.has(book.id)) {
        next.collectionId = collectionId
        next.collectionName = collectionName
      } else {
        delete next.collectionId
        delete next.collectionName
      }
      return next
    })
    await db.books.bulkPut(updated)
    await syncRecords({ books: updated })
    await refreshBooks()
    onNotice(`合集“${collectionName}”已保存。`)
  }

  async function dissolveBookCollection(collectionId: string) {
    const members = books.filter((book) => book.collectionId === collectionId).map((book) => {
      const next: Book = { ...book, updatedAt: Date.now() }
      delete next.collectionId
      delete next.collectionName
      return next
    })
    if (members.length) {
      await db.books.bulkPut(members)
      await syncRecords({ books: members })
    }
    await refreshBooks()
    onNotice('合集已解散，书籍仍保留在书架中。')
  }

  return {
    books,
    activeBook,
    activeChapter,
    loadingBookId,
    loadingChapterId,
    libraryLoading,
    setActiveBook,
    setActiveChapter,
    refreshBooks,
    resetForAccount,
    returnToLibrary,
    saveImportedBook,
    openBook,
    selectChapter,
    deleteBook,
    changeBookCover,
    setBookImageVisibility,
    saveBookCollection,
    dissolveBookCollection,
  }
}
