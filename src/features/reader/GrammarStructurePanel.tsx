import type { LearningSpan } from '../../types'

export default function GrammarStructurePanel({ spans, selectedSpanId, onSelectSpan }: { spans: LearningSpan[]; selectedSpanId: string | null; onSelectSpan: (span: LearningSpan) => void }) {
  const grammar = spans.filter((span) => span.kind === 'construction' || span.kind === 'idiom')
  if (!grammar.length) return null
  return <section className="panel-section grammar-structure-panel"><h3>本地语法结构</h3><p className="muted">原文中的完整结构；重叠或内部结构可分别查看。</p>{grammar.map((span) => <button key={span.id} type="button" className={`structure-choice ${selectedSpanId === span.id ? 'active' : ''}`} onClick={() => onSelectSpan(span)}><strong lang="ja">{span.surface}</strong>{span.captures.grammar_label && <b>{span.captures.grammar_label}</b>}<span>{span.explanation_zh}</span><small>{span.status === 'ambiguous' || span.status === 'unknown' ? '尚不确定' : '规则识别'}</small></button>)}</section>
}
