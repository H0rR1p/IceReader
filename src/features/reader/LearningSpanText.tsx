import { Fragment, type ReactNode } from 'react'
import type { LearningSpan, Token } from '../../types'
import { toHiragana } from '../../text'
import { codepointSlice, isVocabularyToken, topLevelSpans } from './learningSpans'
import './linguistics.css'

export default function LearningSpanText({ text, tokens, spans, selectedSpanId, selectedTokenId, showFurigana, showGrammar, assistanceLevel, onSelectSpan, onSelectToken }: {
  text: string; tokens: Token[]; spans: LearningSpan[]; selectedSpanId: string | null; selectedTokenId: string | null
  showFurigana: boolean; showGrammar: boolean; assistanceLevel: (token: Token) => number
  onSelectSpan: (span: LearningSpan) => void; onSelectToken: (token: Token) => void
}) {
  const selected = topLevelSpans(text, spans.filter((span) => showGrammar || span.kind === 'morphology' || span.kind === 'lexical'))
  const renderAtoms = (start: number, end: number, grouped: boolean) => {
    const output: ReactNode[] = []
    let cursor = start
    for (const token of tokens.filter((value) => value.start >= start && value.end <= end).sort((a, b) => a.start - b.start)) {
      if (token.start < cursor || codepointSlice(text, token.start, token.end) !== token.surface) continue
      if (token.start > cursor) output.push(<Fragment key={`gap-${cursor}`}>{codepointSlice(text, cursor, token.start)}</Fragment>)
      const vocabulary = isVocabularyToken(token)
      const level = assistanceLevel(token)
      output.push(<span key={token.id} className={`token ${vocabulary ? 'content' : 'grammatical'} ${vocabulary ? `assist-level-${level}` : ''} ${selectedTokenId === token.id ? 'selected' : ''}`}
        role={!grouped && vocabulary ? 'button' : undefined} tabIndex={!grouped && vocabulary ? 0 : undefined}
        onClick={!grouped && vocabulary ? (event) => { event.stopPropagation(); onSelectToken(token) } : undefined}
        onKeyDown={!grouped && vocabulary ? (event) => { if (event.key !== 'Enter' && event.key !== ' ') return; event.preventDefault(); event.stopPropagation(); onSelectToken(token) } : undefined}>
        {showFurigana && vocabulary && /[一-龯々𠮷]/u.test(token.surface) && level > 0
          ? <ruby>{token.surface}<rt className={level === 1 ? 'faint' : ''}>{toHiragana(token.surface_reading || token.reading)}</rt></ruby>
          : token.surface}
      </span>)
      cursor = token.end
    }
    if (cursor < end) output.push(<Fragment key={`tail-${cursor}`}>{codepointSlice(text, cursor, end)}</Fragment>)
    return output
  }
  const output: ReactNode[] = []
  let cursor = 0
  for (const span of selected) {
    if (span.start > cursor) output.push(<Fragment key={`plain-${cursor}`}>{renderAtoms(cursor, span.start, false)}</Fragment>)
    output.push(<span key={span.id} role="button" tabIndex={0} aria-label={`${span.surface}，${span.kind === 'morphology' ? '完整活用' : '语法结构'}`}
      className={`learning-span ${span.kind} ${selectedSpanId === span.id ? 'selected' : ''} ${span.status === 'ambiguous' || span.status === 'unknown' ? 'uncertain' : ''}`}
      onClick={(event) => { event.stopPropagation(); onSelectSpan(span) }}
      onKeyDown={(event) => { if (event.key !== 'Enter' && event.key !== ' ') return; event.preventDefault(); event.stopPropagation(); onSelectSpan(span) }}>
      {renderAtoms(span.start, span.end, true)}
    </span>)
    cursor = span.end
  }
  if (cursor < Array.from(text).length) output.push(<Fragment key={`plain-${cursor}`}>{renderAtoms(cursor, Array.from(text).length, false)}</Fragment>)
  return <>{output}</>
}
