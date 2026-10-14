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

/** Clicking is independent of whether a token contributes to vocabulary mastery. */
export function isSelectableToken(token: Token): boolean {
  return token.role !== 'punctuation' && !/^(補助記号|空白|記号)/.test(token.part_of_speech) && /\S/u.test(token.surface)
}

export function validSpans(text: string, spans: LearningSpan[]): LearningSpan[] {
  const characters = Array.from(text)
  const seen = new Set<string>()
  return spans.filter((span) => {
    if (seen.has(span.id) || !Number.isInteger(span.start) || !Number.isInteger(span.end)
      || span.start < 0 || span.end <= span.start || span.end > characters.length
      || characters.slice(span.start, span.end).join('') !== span.surface) return false
    seen.add(span.id)
    return true
  })
}

/** Kept for visual consumers only. It must never decide what is selectable. */
export function topLevelSpans(text: string, spans: LearningSpan[]): LearningSpan[] {
  const ordered = validSpans(text, spans).sort((a, b) => a.start - b.start || b.end - a.end || a.id.localeCompare(b.id))
  const result: LearningSpan[] = []
  let end = 0
  for (const span of ordered) {
    if (span.start < end) continue
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

export function spanUnitRole(span: LearningSpan): NonNullable<LearningSpan['unit_role']> {
  return span.unit_role ?? (span.kind === 'lexical' ? 'lexical' : span.kind === 'morphology' ? 'morphology' : 'construction')
}
export function isBaseSpan(span: LearningSpan): boolean {
  return ['lexical', 'particle', 'auxiliary'].includes(spanUnitRole(span))
}

function fallbackExplanation(token: Token): string {
  if (token.part_of_speech.startsWith('助詞')) {
    if (token.part_of_speech.includes('格助詞')) return '连接词语，说明它和句中其他内容的关系。'
    if (token.part_of_speech.includes('終助詞')) return '放在句末，表达提问、确认等说话语气。'
    if (token.part_of_speech.includes('接続助詞')) return '连接前后内容，表示条件、原因、转折等关系。'
    if (token.part_of_speech.includes('準体助詞')) return '把前面的内容当作一个整体，接到后面的表达中。'
    return '给前面的内容补充范围、强调或其他语气。'
  }
  if (token.part_of_speech.startsWith('助動詞')) return '接在词语后面，补充否定、时态、判断或说话语气；完整用法可切换到上层表达查看。'
  return '结合所在句子查看这个词的意思。'
}

/** A stale or loading structure cache still has a local entry for every meaningful token. */
export function ensureTokenSpans(text: string, tokens: Token[], spans: LearningSpan[]): LearningSpan[] {
  const result = validSpans(text, spans)
  for (const token of tokens) {
    if (!isSelectableToken(token) || token.start < 0 || token.end <= token.start
      || codepointSlice(text, token.start, token.end) !== token.surface
      || result.some((span) => isBaseSpan(span) && span.start === token.start && span.end === token.end)) continue
    const unitRole = token.part_of_speech.startsWith('助動詞') ? 'auxiliary' : token.part_of_speech.startsWith('助詞') ? 'particle' : 'lexical'
    result.push({
      id: `reader-token:${token.id}`, sentence_id: token.sentence_id, start: token.start, end: token.end,
      surface: token.surface, lemma: token.lemma, reading: token.reading,
      kind: unitRole === 'lexical' ? 'lexical' : 'construction', unit_role: unitRole,
      grammar_ids: [], features: [], token_ids: [token.id], steps: [], candidates: [],
      explanation_zh: fallbackExplanation(token), children: [], derivation: [], source: 'rule',
      version: 'reader-token-1', status: 'determined', captures: {}, evidence: [], boundary_status: 'supported',
    })
  }
  return result
}

export interface LearningFragment {
  start: number; end: number; text: string; token?: Token; spanIds: string[]
}
export interface LearningSelectionIndex {
  text: string; spans: LearningSpan[]; byId: Map<string, LearningSpan>; fragments: LearningFragment[]
  tokenRoles: Map<string, NonNullable<LearningSpan['unit_role']>>
  tokenStarts: Map<number, Token>
  markers: Map<string, Array<{ start: number; end: number; tokenCount: number; lexicalContext: number }>>
  openBoundaries: Set<string>
  candidateCache: Map<number, LearningSpan[]>
}

/** The display partition preserves every boundary; overlapping analyses stay in its index. */
export function buildSelectionIndex(text: string, tokens: Token[], spans: LearningSpan[]): LearningSelectionIndex {
  const characters = Array.from(text)
  const nodes = ensureTokenSpans(text, tokens, spans)
  const byId = new Map(nodes.map((span) => [span.id, span]))
  const validTokens = tokens.filter((token) => token.start >= 0 && token.end > token.start && token.end <= characters.length
    && characters.slice(token.start, token.end).join('') === token.surface).sort((a, b) => a.start - b.start || a.end - b.end)
  const boundaries = new Set([0, characters.length])
  const starts = new Map<number, LearningSpan[]>()
  const ends = new Map<number, string[]>()
  for (const span of nodes) {
    boundaries.add(span.start); boundaries.add(span.end)
    starts.set(span.start, [...(starts.get(span.start) ?? []), span])
    ends.set(span.end, [...(ends.get(span.end) ?? []), span.id])
    for (const range of markerRanges(span)) {
      if (range.start >= span.start && range.end <= span.end) { boundaries.add(range.start); boundaries.add(range.end) }
    }
  }
  for (const token of validTokens) { boundaries.add(token.start); boundaries.add(token.end) }
  const positions = [...boundaries].sort((a, b) => a - b)
  const active = new Map<string, LearningSpan>()
  const fragments: LearningFragment[] = []
  let tokenCursor = 0
  for (let i = 0; i < positions.length - 1; i += 1) {
    const start = positions[i], end = positions[i + 1]
    for (const id of ends.get(start) ?? []) active.delete(id)
    for (const span of starts.get(start) ?? []) active.set(span.id, span)
    while (tokenCursor < validTokens.length && validTokens[tokenCursor].end <= start) tokenCursor += 1
    const token = validTokens[tokenCursor]
    fragments.push({ start, end, text: characters.slice(start, end).join(''),
      token: token && token.start <= start && end <= token.end ? token : undefined,
      spanIds: [...active.keys()] })
  }
  const tokenRoles = new Map<string, NonNullable<LearningSpan['unit_role']>>()
  for (const token of validTokens) {
    const base = nodes.find((span) => isBaseSpan(span) && span.start === token.start && span.end === token.end)
    tokenRoles.set(token.id, base ? spanUnitRole(base) : isVocabularyToken(token) ? 'lexical' : 'particle')
  }
  const markers = new Map(nodes.map((span) => [span.id, markerRanges(span).map((range) => {
    const covered = validTokens.filter((token) => token.start < range.end && range.start < token.end)
    return { ...range, tokenCount: covered.length, lexicalContext: covered.filter((token) => isVocabularyToken(token) && tokenRoles.get(token.id) === 'lexical').length }
  })]))
  const tokenEnds = new Map(validTokens.map((token) => [token.end, token]))
  const tokenStarts = new Map(validTokens.map((token) => [token.start, token]))
  const openBoundaries = new Set(nodes.filter((span) => {
    const last = tokenEnds.get(span.end)
    const nextToken = tokenStarts.get(span.end)
    const unfinished = /^(未然形|連用形)/.test(last?.conjugation_form ?? '')
      || /^仮定形/.test(last?.conjugation_form ?? '') && nextToken?.part_of_speech.startsWith('助詞-接続助詞')
    if (!last || !unfinished) return false
    return nodes.some((next) => next.kind === 'morphology' && next.start <= last.start && next.end > span.end
      && next.boundary_status !== 'unknown')
  }).map((span) => span.id))
  return { text, spans: nodes, byId, fragments, tokenRoles, tokenStarts, markers, openBoundaries, candidateCache: new Map() }
}

export function markerRanges(span: LearningSpan): Array<{ start: number; end: number }> {
  const supplied = span.marker_ranges?.filter((range) => Number.isInteger(range.start) && Number.isInteger(range.end)
    && range.start >= span.start && range.end > range.start && range.end <= span.end)
  if (supplied?.length) return supplied
  const marker = span.captures?.marker
  if (marker) {
    const start = span.surface.lastIndexOf(marker)
    if (start >= 0) {
      const offset = Array.from(span.surface.slice(0, start)).length
      return [{ start: span.start + offset, end: span.start + offset + Array.from(marker).length }]
    }
  }
  return [{ start: span.start, end: span.end }]
}

/** Structure confidence concerns its boundary, not its still-ambiguous meaning. */
export function selectionCandidates(index: LearningSelectionIndex, position: number): LearningSpan[] {
  const cached = index.candidateCache.get(position)
  if (cached) return cached
  const fragment = index.fragments.find((part) => part.start <= position && position < part.end)
  if (!fragment) return []
  const covering = fragment.spanIds.map((id) => index.byId.get(id)!)
  const incompleteExpressions = index.spans.filter((span) => index.openBoundaries.has(span.id)
    && ['construction', 'ending'].includes(spanUnitRole(span))
    && (span.start <= position && position < span.end || span.end === fragment.start))
  const vocabulary = fragment.token ? isVocabularyToken(fragment.token) && index.tokenRoles.get(fragment.token.id) === 'lexical' : false
  const rank = (span: LearningSpan): number[] => {
    const role = spanUnitRole(span)
    const marker = index.markers.get(span.id)?.find((range) => range.start <= position && position < range.end)
    const functional = role === 'construction' || role === 'ending' || span.kind === 'idiom'
    const lexicalContext = marker?.lexicalContext ?? 0
    const standalone = fragment.token && span.start === fragment.token.start && span.end === fragment.token.end && functional
    const complete = functional && !standalone && (span.end - span.start > (fragment.token?.end ?? position + 1) - (fragment.token?.start ?? position)
      || (marker?.tokenCount ?? 0) > 1)
    const boundary = span.boundary_status === 'unknown' ? 2 : span.boundary_status === 'possible' ? 1 : 0
    // Extend a recognised auxiliary stem to its closed chain before recommending
    // it. The stem remains selectable as an internal component. This uses actual
    // conjugation boundaries, so it works for past, polite negative and new forms.
    const closesExpression = !vocabulary && role === 'morphology' && !index.openBoundaries.has(span.id)
      && incompleteExpressions.some((stem) => stem.start === span.start && stem.end < span.end
        && !index.tokenStarts.get(stem.start)?.part_of_speech.startsWith('助詞-接続助詞'))
    const closure = index.openBoundaries.has(span.id) ? 2 : closesExpression ? 0 : 1
    const category = functional && marker && complete && (span.kind === 'idiom' || lexicalContext === 0
      && (!vocabulary || span.features.includes('functional_expression'))) ? 0
      : role === 'morphology' ? 1 : role === 'lexical' ? 2 : functional ? 3 : 4
    // A sentence ending has priority over its negative constituent. Among endings,
    // the tighter left boundary avoids swallowing a preceding nominalised clause.
    const terminal = role === 'ending' ? 0 : 1
    const sourceQuality = span.features.includes('functional_expression') ? 0 : span.features.includes('composed') ? 2 : 1
    return [boundary, closure, category, terminal, lexicalContext,
      closesExpression ? marker ? position - marker.start : span.end - span.start : 0,
      sourceQuality, marker ? position - marker.start : span.end - span.start,
      -(marker?.end ?? span.end), -(span.confidence ?? 0)]
  }
  const rankings = new Map(covering.map((span) => [span.id, rank(span)]))
  const result = covering.sort((a, b) => {
    const left = rankings.get(a.id)!, right = rankings.get(b.id)!
    for (let i = 0; i < left.length; i += 1) if (left[i] !== right[i]) return left[i] - right[i]
    return a.id.localeCompare(b.id)
  })
  index.candidateCache.set(position, result)
  return result
}

export function selectionLabel(span: LearningSpan): string {
  const role = spanUnitRole(span)
  return role === 'lexical' ? '词语' : role === 'particle' || role === 'auxiliary' ? '组成用法'
    : role === 'morphology' ? '完整活用' : role === 'ending' ? '句末表达' : '完整表达'
}
