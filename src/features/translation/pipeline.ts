import type { TranslationQueueItem } from '../../types'


export type BackgroundJob = {
  kind: 'segment' | 'translate'
  scope: 'book' | 'chapter'
  label: string
  completed: number
  total: number
  failed: number
  running: boolean
}

export type TranslationMode = 'meaning' | 'full'

export type TranslationBatch = {
  items: TranslationQueueItem[]
  contextBefore: string[]
  chapterTitle: string
}

export class AsyncQueue<T> {
  private items: T[] = []
  private waiters: Array<(value: T | null) => void> = []
  private closed = false

  get done() { return this.closed && this.items.length === 0 }

  push(value: T) {
    if (this.closed) return
    const waiter = this.waiters.shift()
    if (waiter) waiter(value)
    else this.items.push(value)
  }

  close() {
    this.closed = true
    for (const waiter of this.waiters.splice(0)) waiter(null)
  }

  async next(): Promise<T | null> {
    const value = this.items.shift()
    if (value !== undefined) return value
    if (this.closed) return null
    return new Promise((resolve) => this.waiters.push(resolve))
  }
}

function estimateTranslationTokens(item: TranslationQueueItem, mode: TranslationMode) {
  const source = Math.ceil(item.sentence.original.length * 1.15)
  if (mode === 'meaning') return source + Math.max(36, Math.ceil(item.sentence.original.length * .65))
  const contentTokens = item.tokens.reduce((count, token) => count + (token.is_content ? 1 : 0), 0)
  return source + Math.max(80, Math.ceil(item.sentence.original.length * .8)) + contentTokens * 14
}

export function makeDynamicTranslationBatches(
  items: TranslationQueueItem[], mode: TranslationMode, initialContext: string[] = [],
): TranslationBatch[] {
  const batches: TranslationBatch[] = []
  let current: TranslationQueueItem[] = []
  let estimatedTokens = 0
  let recent = initialContext.slice(-2)
  const flush = () => {
    if (!current.length) return
    batches.push({ items: current, contextBefore: recent.slice(-2), chapterTitle: current[0].chapterTitle })
    recent = [...recent, ...current.map((item) => item.sentence.original)].slice(-2)
    current = []
    estimatedTokens = 0
  }
  for (const item of items) {
    const itemTokens = estimateTranslationTokens(item, mode)
    if (current.length && (current.length >= 12 || estimatedTokens + itemTokens > 2500)) flush()
    current.push(item)
    estimatedTokens += itemTokens
  }
  flush()
  return batches
}
