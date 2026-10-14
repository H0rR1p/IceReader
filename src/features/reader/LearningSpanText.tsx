import { Fragment, useMemo, useRef, useState, type MouseEvent, type KeyboardEvent, type ReactNode } from 'react'
import type { LearningSpan, Token } from '../../types'
import { toHiragana } from '../../text'
import { buildSelectionIndex, isSelectableToken, isVocabularyToken, selectionCandidates, selectionLabel, type LearningFragment } from './learningSpans'
import './linguistics.css'

function codepointAtPointer(element: HTMLElement, fragment: LearningFragment, x: number, y: number): number {
  const documentWithCaret = element.ownerDocument as Document & {
    caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null
    caretRangeFromPoint?: (x: number, y: number) => Range | null
  }
  const caret = documentWithCaret.caretPositionFromPoint?.(x, y)
  const range = caret ? null : documentWithCaret.caretRangeFromPoint?.(x, y)
  const node = caret?.offsetNode ?? range?.startContainer
  const offset = caret?.offset ?? range?.startOffset ?? 0
  if (!node || node.nodeType !== Node.TEXT_NODE || !element.contains(node)) return fragment.start
  const content = node.textContent ?? ''
  const utf16Boundaries = [0]
  for (const character of content) utf16Boundaries.push(utf16Boundaries[utf16Boundaries.length - 1] + character.length)
  const caretIndex = utf16Boundaries.findIndex((boundary) => boundary >= offset)
  for (const candidate of [caretIndex - 1, caretIndex]) {
    if (candidate < 0 || candidate >= utf16Boundaries.length - 1) continue
    const glyph = element.ownerDocument.createRange()
    glyph.setStart(node, utf16Boundaries[candidate]); glyph.setEnd(node, utf16Boundaries[candidate + 1])
    if ([...glyph.getClientRects()].some((rect) => x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom)) {
      return Math.min(fragment.end - 1, fragment.start + candidate)
    }
  }
  return Math.min(fragment.end - 1, fragment.start + Math.max(0, caretIndex))
}

export default function LearningSpanText({ text, tokens, spans, selectedSpanId, selectedTokenId, showFurigana, showGrammar, assistanceLevel, onSelectSpan, onSelectToken, onSelectPosition }: {
  text: string; tokens: Token[]; spans: LearningSpan[]; selectedSpanId: string | null; selectedTokenId: string | null
  showFurigana: boolean; showGrammar: boolean; assistanceLevel: (token: Token) => number
  onSelectSpan: (span: LearningSpan) => void; onSelectToken: (token: Token) => void
  onSelectPosition?: (position: number, candidates: LearningSpan[], token?: Token) => void
}) {
  const index = useMemo(() => buildSelectionIndex(text, tokens, spans), [text, tokens, spans])
  const selectedSpan = selectedSpanId ? index.byId.get(selectedSpanId) : undefined
  const meaningful = index.fragments.filter((part) => part.spanIds.length || part.token && isSelectableToken(part.token))
  const [focusStart, setFocusStart] = useState<number | null>(null)
  const controls = useRef(new Map<number, HTMLSpanElement>())
  const pointerOrigin = useRef<{ x: number; y: number } | null>(null)
  const pointerDragged = useRef(false)
  const tabStart = meaningful.some((part) => part.start === focusStart) ? focusStart : meaningful[0]?.start

  const activate = (fragment: LearningFragment, position: number) => {
    const candidates = selectionCandidates(index, position)
    if (onSelectPosition) { onSelectPosition(position, candidates, fragment.token); return }
    const span = candidates[0]
    if (span && span.unit_role !== 'lexical') onSelectSpan(span)
    else if (fragment.token) onSelectToken(fragment.token)
  }
  const click = (event: MouseEvent<HTMLSpanElement>, fragment: LearningFragment, reading = false) => {
    event.stopPropagation()
    if (pointerDragged.current || window.getSelection()?.isCollapsed === false) return
    const position = reading ? fragment.token?.start ?? fragment.start
      : codepointAtPointer(event.currentTarget, fragment, event.clientX, event.clientY)
    activate(fragment, position)
  }
  const key = (event: KeyboardEvent<HTMLSpanElement>, fragment: LearningFragment) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault(); event.stopPropagation(); activate(fragment, fragment.start); return
    }
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    event.preventDefault(); event.stopPropagation()
    const current = meaningful.findIndex((part) => part.start === fragment.start)
    const target = event.key === 'Home' ? 0 : event.key === 'End' ? meaningful.length - 1
      : Math.max(0, Math.min(meaningful.length - 1, current + (event.key === 'ArrowRight' ? 1 : -1)))
    const next = meaningful[target]
    if (next) { setFocusStart(next.start); controls.current.get(next.start)?.focus() }
  }
  const renderFragment = (part: LearningFragment) => {
    const selectable = part.spanIds.length > 0 || part.token && isSelectableToken(part.token)
    const primary = selectable ? selectionCandidates(index, part.start)[0] : undefined
    const chosen = selectedSpan && selectedSpan.start <= part.start && part.end <= selectedSpan.end
    const grammar = showGrammar && part.spanIds.some((id) => index.byId.get(id)?.kind !== 'lexical')
    return <span key={part.start} ref={(node) => { if (node) controls.current.set(part.start, node); else controls.current.delete(part.start) }}
      data-learning-start={part.start} data-learning-end={part.end}
      data-default-surface={primary?.surface}
      className={`learning-fragment ${grammar ? 'learning-span' : ''} ${chosen ? 'selected' : ''} ${selectedSpanId ? '' : selectedTokenId === part.token?.id ? 'token-selected' : ''}`}
      role={selectable ? 'button' : undefined} tabIndex={selectable ? tabStart === part.start ? 0 : -1 : undefined}
      aria-label={selectable ? `${part.text}，${primary ? selectionLabel(primary) : '词语'}` : undefined}
      onFocus={selectable ? () => setFocusStart(part.start) : undefined}
      onClick={selectable ? (event) => click(event, part) : undefined}
      onKeyDown={selectable ? (event) => key(event, part) : undefined}>{part.text}</span>
  }

  const output: ReactNode[] = []
  for (let cursor = 0; cursor < index.fragments.length;) {
    const first = index.fragments[cursor]
    if (!first.token) { output.push(renderFragment(first)); cursor += 1; continue }
    const token = first.token
    const parts: LearningFragment[] = []
    while (cursor < index.fragments.length && index.fragments[cursor].token?.id === token.id) parts.push(index.fragments[cursor++])
    const vocabulary = isVocabularyToken(token)
    const level = assistanceLevel(token)
    const body = parts.map(renderFragment)
    const completeToken = first.start === token.start && parts[parts.length - 1].end === token.end
    output.push(<span key={`token-${token.id}-${first.start}`} data-token-id={token.id}
      className={`token learning-token ${vocabulary ? 'content' : 'grammatical'} ${vocabulary ? `assist-level-${level}` : ''}`}>
      {showFurigana && vocabulary && completeToken && /[一-龯々𠮷]/u.test(token.surface) && level > 0
        ? <ruby>{body}<rt className={level === 1 ? 'faint' : ''} onClick={(event) => click(event, first, true)}>{toHiragana(token.surface_reading || token.reading)}</rt></ruby>
        : body}
    </span>)
  }
  return <span className="learning-text" onPointerDown={(event) => {
    pointerOrigin.current = { x: event.clientX, y: event.clientY }; pointerDragged.current = false
  }} onPointerMove={(event) => {
    const origin = pointerOrigin.current
    if (origin && event.buttons && Math.hypot(event.clientX - origin.x, event.clientY - origin.y) > 4) pointerDragged.current = true
  }} onPointerUp={() => { pointerOrigin.current = null }}><Fragment>{output}</Fragment></span>
}
