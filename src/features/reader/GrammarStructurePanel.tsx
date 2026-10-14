import type { LearningSpan } from '../../types'
import { isBaseSpan, selectionLabel, spanUnitRole } from './learningSpans'

export function LearningSelectionChoices({ candidates, selectedId, onSelect }: {
  candidates: LearningSpan[]; selectedId: string | null; onSelect: (span: LearningSpan) => void
}) {
  const groups = [
    { label: '完整表达', rows: candidates.filter((span) => !isBaseSpan(span) && span.kind !== 'morphology') },
    { label: '完整活用', rows: candidates.filter((span) => span.kind === 'morphology' && !isBaseSpan(span)) },
    { label: '组成用法', rows: candidates.filter(isBaseSpan) },
  ]
  return <section className="learning-selection-choices panel-section" aria-label="表达层级选择">
    <label className="grammar-rule-field"><span>查看层级</span><select aria-label="查看层级" value={selectedId ?? candidates[0]?.id ?? ''}
      onChange={(event) => { const span = candidates.find((value) => value.id === event.target.value); if (span) onSelect(span) }}>
      {groups.filter((group) => group.rows.length).map((group) => <optgroup key={group.label} label={group.label}>
        {group.rows.map((span) => {
          const detail = span.captures.grammar_label || span.candidates.find((value) => value.status === 'selected')?.label
          return <option key={span.id} value={span.id}>{span.surface}{detail && detail !== span.surface ? ` · ${detail}` : ` · ${selectionLabel(span)}`}</option>
        })}
      </optgroup>)}
    </select></label>
  </section>
}

export default function GrammarStructurePanel({ spans, selectedSpanId, onSelectSpan }: { spans: LearningSpan[]; selectedSpanId: string | null; onSelectSpan: (span: LearningSpan) => void }) {
  const grammar = spans.filter((span) => !isBaseSpan(span) && (span.kind === 'construction' || span.kind === 'idiom'))
  if (!grammar.length) return null
  return <details className="panel-section grammar-structure-panel"><summary>本句其他语法结构 <small>{grammar.length}</small></summary>
    {grammar.map((span) => <button key={span.id} type="button" className={`structure-choice ${selectedSpanId === span.id ? 'active' : ''}`} onClick={() => onSelectSpan(span)}>
      <strong lang="ja">{span.surface}</strong>{span.captures.grammar_label && <b>{span.captures.grammar_label}</b>}
      <span>{span.explanation_zh}</span><small>{spanUnitRole(span) === 'ending' ? '句末表达' : '本地识别'}</small>
    </button>)}
  </details>
}
