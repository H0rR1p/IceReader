import { useState } from 'react'
import type { LearningSpan } from '../../types'

const sourceName = { rule: '本地规则识别', parser: '本地解析器', ai: 'AI 语境判定', user: '用户确认' }
export default function LearningInspector({ span, contextGloss, onAddCard, onChoose }: {
  span: LearningSpan; contextGloss?: string
  onAddCard?: (grammarId: string, template: 'grammar-recognition' | 'form-restoration') => Promise<void>
  onChoose?: (choiceId: string | null) => Promise<void>
}) {
  const [selectedRule, setSelectedRule] = useState(span.grammar_ids[0] ?? '')
  const [saving, setSaving] = useState(false)
  async function add(template: 'grammar-recognition' | 'form-restoration') {
    if (!onAddCard || saving) return
    setSaving(true)
    try { await onAddCard(selectedRule, template) } finally { setSaving(false) }
  }
  return <section className="learning-inspector panel-section" aria-label="完整学习单位">
    <div className="inspector-heading"><h3>{span.kind === 'morphology' ? '完整活用' : span.kind === 'idiom' ? '固定搭配' : '语法结构'}</h3><small>{sourceName[span.source]}</small></div>
    <strong className="inspector-surface" lang="ja">{span.surface}</strong>
    <p><small>词典形</small> <span lang="ja">{span.lemma || '尚未确定'}</span></p>
    {(span.status === 'ambiguous' || span.status === 'unknown') && <p role="status" className="analysis-unknown">{span.status === 'ambiguous' ? '这段表达有几种可能的意思，请结合前后文选择。' : '暂时无法确定这段表达的意思。'}</p>}
    {span.explanation_zh && <p>{span.explanation_zh}</p>}
    {span.derivation?.length > 1 && <div className="morph-derivation"><h4>词形怎样变化</h4><p lang="ja">{span.derivation.join(' → ')}</p></div>}
    {contextGloss && <div className="context-gloss"><small>本句语境义 · AI</small><p>{contextGloss}</p></div>}
    {!!span.steps.length && <ol className="morph-steps">{span.steps.map((step, index) => <li key={`${index}-${step.feature}`}><span lang="ja">{step.surface}</span><p>{step.explanation_zh || step.feature}</p>{step.lemma && step.lemma !== step.surface && <small lang="ja">词典形：{step.lemma}</small>}</li>)}</ol>}
    {span.candidates.length > 1 && <div className="morph-candidates"><h4>可能的解释</h4>{span.candidates.map((candidate) => <p key={candidate.id}><strong>{candidate.label}</strong><small>{candidate.status === 'selected' ? '用户确认' : candidate.status === 'rejected' ? '已排除' : '待确认'}</small>{!!candidate.derivation?.length && <small lang="ja">{candidate.derivation.join(' → ')}</small>}{onChoose && <button className="button small" onClick={() => void onChoose(candidate.id)}>确认此解释</button>}</p>)}{span.override_revision && onChoose ? <button className="button small" onClick={() => void onChoose(null)}>清除选择，恢复候选</button> : null}</div>}
    {!!span.grammar_ids.length && onAddCard && <section className="grammar-card-controls" aria-label="创建语法学习卡"><label className="grammar-rule-field"><span>学习的语法</span><select value={selectedRule} onChange={(event) => setSelectedRule(event.target.value)}>{span.grammar_ids.map((id) => <option key={id} value={id}>{span.grammar_ids.length === 1 ? span.captures.grammar_label || span.surface : span.steps.find((step) => id === `ja.morph.${step.feature}`)?.explanation_zh || id}</option>)}</select></label><div className="book-background-actions"><button className="button small" disabled={saving} onClick={() => void add('grammar-recognition')}>加入结构识别卡</button>{span.kind === 'morphology' && span.derivation.length > 1 && <button className="button small" disabled={saving} onClick={() => void add('form-restoration')}>加入词形恢复卡</button>}</div></section>}
  </section>
}
