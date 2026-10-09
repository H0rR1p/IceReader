import type { LearningSpan, Token } from '../../types'

/** Backend anchors count Unicode codepoints; slicing JS UTF-16 directly corrupts emoji. */
export function codepointSlice(text: string, start: number, end: number): string {
  return Array.from(text).slice(start, end).join('')
}
export function codepointToUtf16(text: string, position: number): number {
  return Array.from(text).slice(0, position).join('').length
}
export function isVocabularyToken(token: Token): boolean {
  if (token.role) return token.role === 'lexical' && token.is_content
  return token.is_content && !/^(助詞|助動詞|補助記号|空白|記号)/.test(token.part_of_speech)
}

export function topLevelSpans(text: string, spans: LearningSpan[]): LearningSpan[] {
  const length = Array.from(text).length
  const ordered = spans.filter((span) => span.start >= 0 && span.end > span.start && span.end <= length
    && codepointSlice(text, span.start, span.end) === span.surface)
    .sort((a, b) => a.start - b.start || b.end - a.end
      || Number(b.kind === 'construction' || b.kind === 'idiom') - Number(a.kind === 'construction' || a.kind === 'idiom')
      || a.id.localeCompare(b.id))
  const result: LearningSpan[] = []
  let end = 0
  for (const span of ordered) {
    if (span.start < end) continue // Nested/crossing analyses remain in inspector, not overlapping DOM.
    result.push(span); end = span.end
  }
  return result
}

export function headToken(span: LearningSpan, tokens: Token[]): Token | undefined {
  const covered = tokens.filter((token) => span.token_ids.includes(token.id)
    || token.start >= span.start && token.end <= span.end)
  return covered.find((token) => isVocabularyToken(token) && token.lemma === span.lemma)
    ?? covered.find(isVocabularyToken)
    ?? covered[0]
}
