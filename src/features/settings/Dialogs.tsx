import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { importEpub, importPlainText, loadAiUsage } from '../../api'
import type { AiUsageSummary, ApiSettings, ImportedBook, VoiceSettings } from '../../types'

export function ImportDialog({ onClose, onImported }: { onClose: () => void; onImported: (book: ImportedBook) => void }) {
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit() {
    setBusy(true); setError('')
    try {
      let imported: ImportedBook
      if (file?.name.toLowerCase().endsWith('.epub')) imported = await importEpub(file)
      else if (file) imported = await importPlainText(title || file.name.replace(/\.txt$/i, ''), await file.text())
      else imported = await importPlainText(title || '粘贴文本', text)
      await onImported(imported)
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  return (
    <Modal title="导入日文内容" onClose={onClose}>
      <p className="muted">导入只解析书籍结构、正文和图片，不等待全书 AI 处理。阅读时按需切分当前章节，再逐句释义。</p>
      <label className="field"><span>标题</span><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="可选" /></label>
      <label className="drop-zone"><input type="file" accept=".epub,.txt,text/plain,application/epub+zip" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><strong>{file ? file.name : '选择 EPUB 或 TXT'}</strong><small>也可以把文件拖到这里</small></label>
      <div className="divider"><span>或者粘贴文本</span></div>
      <label className="field"><textarea value={text} onChange={(event) => setText(event.target.value)} rows={9} placeholder="ここに日本語の文章を貼り付けてください。" /></label>
      {error && <div className="error-box">{error}</div>}
      <div className="modal-actions"><button className="button ghost" onClick={onClose}>取消</button><button className="button primary" disabled={busy || (!file && !text.trim())} onClick={() => void submit()}>{busy ? '正在导入…' : '导入书籍'}</button></div>
    </Modal>
  )
}

export function SettingsDialog({ value, onClose, onSave }: { value: ApiSettings; onClose: () => void; onSave: (next: ApiSettings) => void | Promise<void> }) {
  const [draft, setDraft] = useState(value)
  const [usage, setUsage] = useState<AiUsageSummary | null>(null)
  useEffect(() => { void loadAiUsage().then(setUsage).catch(() => undefined) }, [])
  const canSave = Boolean(draft.baseUrl.trim() && draft.model.trim())
  const save = () => onSave({
    apiKey: draft.apiKey.trim(),
    baseUrl: draft.baseUrl.trim().replace(/\/$/, ''),
    model: draft.model.trim(),
    hasStoredApiKey: draft.hasStoredApiKey,
    cacheHitUsdPerMillion: Math.max(0, draft.cacheHitUsdPerMillion || 0),
    cacheMissUsdPerMillion: Math.max(0, draft.cacheMissUsdPerMillion || 0),
    outputUsdPerMillion: Math.max(0, draft.outputUsdPerMillion || 0),
  })
  return (
    <Modal title="AI 设置" onClose={onClose}>
      <div className="privacy-note"><strong>配置保存在本地项目</strong><p>API Key 写入被 Git 忽略的 data/settings.json，仅在调用所配置的 AI 接口时发送。</p></div>
      <label className="field"><span>API Key（可疑句界审校和句意分析使用）</span><input type="password" autoComplete="off" value={draft.apiKey} onChange={(event) => setDraft({ ...draft, apiKey: event.target.value })} placeholder={draft.hasStoredApiKey ? '已保存；留空保持不变' : 'sk-…'} /></label>
      <label className="field"><span>OpenAI 兼容 Base URL</span><input value={draft.baseUrl} onChange={(event) => setDraft({ ...draft, baseUrl: event.target.value })} /></label>
      <label className="field"><span>模型</span><input value={draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })} /></label>
      <div className="pricing-grid">
        <label className="field"><span>缓存命中价（美元/百万 token）</span><input type="number" min="0" step="0.001" value={draft.cacheHitUsdPerMillion} onChange={(event) => setDraft({ ...draft, cacheHitUsdPerMillion: Number(event.target.value) })} /></label>
        <label className="field"><span>缓存未命中价</span><input type="number" min="0" step="0.001" value={draft.cacheMissUsdPerMillion} onChange={(event) => setDraft({ ...draft, cacheMissUsdPerMillion: Number(event.target.value) })} /></label>
        <label className="field"><span>输出价</span><input type="number" min="0" step="0.001" value={draft.outputUsdPerMillion} onChange={(event) => setDraft({ ...draft, outputUsdPerMillion: Number(event.target.value) })} /></label>
      </div>
      {usage && <section className="usage-card">
        <div><strong>AI 用量</strong><span>{usage.requests} 次请求 · {usage.items} 句/边界</span></div>
        <div className="usage-grid"><span>未命中输入<b>{usage.cache_miss_tokens.toLocaleString()}</b></span><span>缓存输入<b>{usage.cache_hit_tokens.toLocaleString()}</b></span><span>输出<b>{usage.completion_tokens.toLocaleString()}</b></span><span>本地结果缓存<b>{usage.response_cache_hits.toLocaleString()} 次</b></span></div>
        <small>{usage.pricing_configured ? `估算费用 $${usage.estimated_cost_usd.toFixed(6)}` : '填写上方单价后显示估算费用'} · 累计耗时 {(usage.duration_ms / 1000).toFixed(1)} 秒 · 失败 {usage.failures} 次</small>
      </section>}
      <div className="modal-actions"><button className="button ghost" onClick={onClose}>取消</button><button className="button primary" disabled={!canSave} onClick={save}>应用</button></div>
    </Modal>
  )
}

export function VoiceSettingsDialog({ value, onClose, onSave }: {
  value: VoiceSettings
  onClose: () => void
  onSave: (next: Pick<VoiceSettings, 'ymmPath' | 'characterName' | 'playbackRate' | 'volume'>, template: File | null) => void | Promise<void>
}) {
  const [draft, setDraft] = useState(value)
  const [template, setTemplate] = useState<File | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  async function chooseTemplate(file: File | null) {
    setTemplate(file)
    if (!file) return
    try {
      const project = JSON.parse(await file.text()) as {
        Characters?: Array<{ Name?: string }>
        Timelines?: Array<{ Items?: Array<{ $type?: string; CharacterName?: string }> }>
      }
      const names = [
        ...(project.Characters ?? []).map((item) => item.Name?.trim() ?? ''),
        ...(project.Timelines ?? []).flatMap((timeline) => (timeline.Items ?? [])
          .filter((item) => item.$type?.includes('VoiceItem'))
          .map((item) => item.CharacterName?.trim() ?? '')),
      ].filter((name, index, all) => name && all.indexOf(name) === index)
      setDraft((current) => ({
        ...current,
        characterNames: names,
        characterName: names.includes(current.characterName) ? current.characterName : (names[0] ?? ''),
      }))
      setError(names.length ? '' : '这个模板中没有可选择的配音角色')
    } catch {
      setError('无法读取这个配音模板')
    }
  }
  async function save() {
    setSaving(true)
    setError('')
    try {
      await onSave(draft, template)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSaving(false)
    }
  }
  return (
    <Modal title="YMM4 配音设置" onClose={onClose}>
      <div className="privacy-note"><strong>本机后台配音</strong><p>冰读通过本机配音桥调用 YMM4 中已获许可的语音角色。模板中的角色和音色会被保留，生成结果只写入本地缓存。</p></div>
      <label className="field"><span>YukkuriMovieMaker.exe 路径</span><input value={draft.ymmPath} onChange={(event) => setDraft({ ...draft, ymmPath: event.target.value })} placeholder="留空时自动查找工作区内的幻想乡口音剪辑器" /></label>
      <label className="field"><span>配音模板（.ymmp）</span><input type="file" accept=".ymmp" onChange={(event) => void chooseTemplate(event.target.files?.[0] ?? null)} /></label>
      <label className="field"><span>配音角色</span><select value={draft.characterName} disabled={draft.characterNames.length === 0} onChange={(event) => setDraft({ ...draft, characterName: event.target.value })}>{draft.characterNames.length === 0 ? <option value="">请先导入包含角色的配音模板</option> : draft.characterNames.map((name) => <option key={name} value={name}>{name}</option>)}</select></label>
      <p className="setting-status">YMM4：{value.ymmFound ? '已找到' : '未找到'}　模板：{value.templateFound ? `已导入${value.characterName ? `（${value.characterName}）` : ''}` : '未导入'}</p>
      <div className="voice-setting-grid">
        <label className="field"><span>语速：{(draft.playbackRate / 100).toFixed(2)}×</span><input type="range" min="50" max="150" step="5" value={draft.playbackRate} onChange={(event) => setDraft({ ...draft, playbackRate: Number(event.target.value) })} /></label>
        <div className="field fixed-setting"><span>音高</span><strong>1.00×（固定）</strong></div>
        <label className="field"><span>音量：{draft.volume}</span><input type="range" min="0" max="100" value={draft.volume} onChange={(event) => setDraft({ ...draft, volume: Number(event.target.value) })} /></label>
      </div>
      {error && <div className="error-box">{error}</div>}
      <div className="modal-actions"><button className="button ghost" onClick={onClose}>取消</button><button className="button primary" disabled={saving} onClick={() => void save()}>{saving ? '正在保存…' : '应用'}</button></div>
    </Modal>
  )
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}><div className="modal" role="dialog" aria-modal="true" aria-label={title}><div className="modal-head"><h2>{title}</h2><button onClick={onClose} aria-label="关闭">×</button></div>{children}</div></div>
}

