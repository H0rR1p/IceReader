import { useEffect, useState } from 'react'
import { loadAiUsage } from '../../api'
import type { AiUsageSummary, ApiSettings, Book, VoiceSettings } from '../../types'
import type { TranslationMode } from '../translation/pipeline'

function SettingsSection({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  return <section className="settings-section"><header><h2>{title}</h2><p>{description}</p></header><div className="settings-section-body">{children}</div></section>
}

export default function SettingsPage({ apiSettings, voiceSettings, books, translationMode, translationConcurrency, onSaveApi, onSaveVoice, onBookImageVisibility, onTranslationModeChange, onTranslationConcurrencyChange, onNotice }: {
  apiSettings: ApiSettings
  voiceSettings: VoiceSettings
  books: Book[]
  translationMode: TranslationMode
  translationConcurrency: number
  onSaveApi: (next: ApiSettings) => Promise<void>
  onSaveVoice: (next: Pick<VoiceSettings, 'ymmPath' | 'characterName' | 'playbackRate' | 'volume'>, template: File | null) => Promise<void>
  onBookImageVisibility: (book: Book, visible: boolean) => Promise<void>
  onTranslationModeChange: (mode: TranslationMode) => void
  onTranslationConcurrencyChange: (value: number) => void
  onNotice: (message: string) => void
}) {
  const [apiDraft, setApiDraft] = useState(apiSettings)
  const [voiceDraft, setVoiceDraft] = useState(voiceSettings)
  const [template, setTemplate] = useState<File | null>(null)
  const [usage, setUsage] = useState<AiUsageSummary | null>(null)
  const [saving, setSaving] = useState<'api' | 'voice' | null>(null)
  const [error, setError] = useState('')
  useEffect(() => setApiDraft(apiSettings), [apiSettings])
  useEffect(() => setVoiceDraft(voiceSettings), [voiceSettings])
  useEffect(() => { void loadAiUsage().then(setUsage).catch(() => undefined) }, [])

  async function chooseTemplate(file: File | null) {
    setTemplate(file)
    if (!file) return
    try {
      const project = JSON.parse(await file.text()) as { Characters?: Array<{ Name?: string }>; Timelines?: Array<{ Items?: Array<{ $type?: string; CharacterName?: string }> }> }
      const names = [...(project.Characters ?? []).map((item) => item.Name?.trim() ?? ''), ...(project.Timelines ?? []).flatMap((timeline) => (timeline.Items ?? []).filter((item) => item.$type?.includes('VoiceItem')).map((item) => item.CharacterName?.trim() ?? ''))].filter((name, index, all) => name && all.indexOf(name) === index)
      setVoiceDraft((current) => ({ ...current, characterNames: names, characterName: names.includes(current.characterName) ? current.characterName : (names[0] ?? '') }))
      setError(names.length ? '' : '这个模板中没有可选择的配音角色')
    } catch { setError('无法读取这个配音模板') }
  }

  async function saveApi() {
    setSaving('api'); setError('')
    try {
      await onSaveApi({ ...apiDraft, apiKey: apiDraft.apiKey.trim(), baseUrl: apiDraft.baseUrl.trim().replace(/\/$/, ''), model: apiDraft.model.trim(), cacheHitUsdPerMillion: Math.max(0, apiDraft.cacheHitUsdPerMillion || 0), cacheMissUsdPerMillion: Math.max(0, apiDraft.cacheMissUsdPerMillion || 0), outputUsdPerMillion: Math.max(0, apiDraft.outputUsdPerMillion || 0) })
      onNotice('AI 设置已保存。')
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setSaving(null) }
  }

  async function saveVoice() {
    setSaving('voice'); setError('')
    try { await onSaveVoice(voiceDraft, template); setTemplate(null); onNotice('配音设置已保存。') }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setSaving(null) }
  }

  return <main className="app-page settings-page">
    <header className="page-heading"><div><p className="eyebrow">偏好与连接</p><h1>设置</h1><span>集中管理阅读处理、AI 服务和本机配音。</span></div></header>
    {error && <div className="error-box page-error">{error}</div>}
    <SettingsSection title="阅读处理" description="这些选项用于后台翻译，修改后立即生效。">
      <div className="setting-row"><div><strong>后台翻译模式</strong><span>快速句意只生成译文；完整释义还会补充词典未命中的词义。</span></div><select value={translationMode} onChange={(event) => onTranslationModeChange(event.target.value as TranslationMode)}><option value="meaning">快速句意</option><option value="full">完整释义</option></select></div>
      <div className="setting-row"><div><strong>并发任务数</strong><span>网络不稳定或触发限流时建议调低。</span></div><select value={translationConcurrency} onChange={(event) => onTranslationConcurrencyChange(Number(event.target.value))}>{Array.from({ length: 8 }, (_, index) => <option key={index + 1} value={index + 1}>{index + 1}</option>)}</select></div>
    </SettingsSection>
    <SettingsSection title="书籍插图" description="统一管理每本书的插图显示状态，换章后会继续沿用。">
      {books.length ? <div className="book-image-settings-list">{books.map((book) => <div className="setting-row" key={book.id}><div><strong>{book.title}</strong><span>{book.author || '作者未知'}</span></div><select value={book.showImages === false ? 'hidden' : 'visible'} onChange={(event) => void onBookImageVisibility(book, event.target.value === 'visible').catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))}><option value="visible">显示插图</option><option value="hidden">隐藏插图</option></select></div>)}</div> : <p className="settings-empty">导入书籍后可以在这里统一管理插图。</p>}
    </SettingsSection>
    <SettingsSection title="AI 服务" description="API Key 只保存在本机，并仅发送给你配置的兼容接口。">
      <div className="settings-form-grid"><label className="field wide"><span>API Key</span><input type="password" autoComplete="off" value={apiDraft.apiKey} onChange={(event) => setApiDraft({ ...apiDraft, apiKey: event.target.value })} placeholder={apiDraft.hasStoredApiKey ? '已保存；留空保持不变' : 'sk-…'} /></label><label className="field"><span>Base URL</span><input value={apiDraft.baseUrl} onChange={(event) => setApiDraft({ ...apiDraft, baseUrl: event.target.value })} /></label><label className="field"><span>模型</span><input value={apiDraft.model} onChange={(event) => setApiDraft({ ...apiDraft, model: event.target.value })} /></label><label className="field"><span>缓存命中价 / 百万 token</span><input type="number" min="0" step="0.001" value={apiDraft.cacheHitUsdPerMillion} onChange={(event) => setApiDraft({ ...apiDraft, cacheHitUsdPerMillion: Number(event.target.value) })} /></label><label className="field"><span>缓存未命中价</span><input type="number" min="0" step="0.001" value={apiDraft.cacheMissUsdPerMillion} onChange={(event) => setApiDraft({ ...apiDraft, cacheMissUsdPerMillion: Number(event.target.value) })} /></label><label className="field"><span>输出价</span><input type="number" min="0" step="0.001" value={apiDraft.outputUsdPerMillion} onChange={(event) => setApiDraft({ ...apiDraft, outputUsdPerMillion: Number(event.target.value) })} /></label></div>
      {usage && <div className="settings-usage"><span><b>{usage.requests}</b> 次请求</span><span><b>{usage.cache_miss_tokens.toLocaleString()}</b> 未命中输入</span><span><b>{usage.completion_tokens.toLocaleString()}</b> 输出</span><span><b>{usage.response_cache_hits}</b> 次缓存命中</span><small>{usage.pricing_configured ? `估算费用 $${usage.estimated_cost_usd.toFixed(6)}` : '配置单价后显示费用估算'}</small></div>}
      <div className="settings-actions"><button className="button primary" disabled={saving !== null || !apiDraft.baseUrl.trim() || !apiDraft.model.trim()} onClick={() => void saveApi()}>{saving === 'api' ? '正在保存…' : '保存 AI 设置'}</button></div>
    </SettingsSection>
    <SettingsSection title="YMM4 配音" description="通过本机配音桥生成日语朗读，音频只写入本地缓存。">
      <div className="settings-form-grid"><label className="field wide"><span>YukkuriMovieMaker.exe 路径</span><input value={voiceDraft.ymmPath} onChange={(event) => setVoiceDraft({ ...voiceDraft, ymmPath: event.target.value })} placeholder="留空时自动查找工作区内的编辑器" /></label><label className="field"><span>配音模板</span><input type="file" accept=".ymmp" onChange={(event) => void chooseTemplate(event.target.files?.[0] ?? null)} /></label><label className="field"><span>配音角色</span><select value={voiceDraft.characterName} disabled={!voiceDraft.characterNames.length} onChange={(event) => setVoiceDraft({ ...voiceDraft, characterName: event.target.value })}>{voiceDraft.characterNames.length ? voiceDraft.characterNames.map((name) => <option key={name}>{name}</option>) : <option value="">请先导入模板</option>}</select></label><label className="field"><span>语速：{(voiceDraft.playbackRate / 100).toFixed(2)}×</span><input type="range" min="50" max="150" step="5" value={voiceDraft.playbackRate} onChange={(event) => setVoiceDraft({ ...voiceDraft, playbackRate: Number(event.target.value) })} /></label><div className="field fixed-setting"><span>音高</span><strong>1.00×（固定）</strong></div><label className="field"><span>音量：{voiceDraft.volume}</span><input type="range" min="0" max="100" value={voiceDraft.volume} onChange={(event) => setVoiceDraft({ ...voiceDraft, volume: Number(event.target.value) })} /></label></div>
      <div className="setting-health"><span className={voiceSettings.ymmFound ? 'ok' : ''}>YMM4 {voiceSettings.ymmFound ? '已连接' : '未找到'}</span><span className={voiceSettings.templateFound ? 'ok' : ''}>模板 {voiceSettings.templateFound ? '已导入' : '未导入'}</span></div>
      <div className="settings-actions"><button className="button primary" disabled={saving !== null} onClick={() => void saveVoice()}>{saving === 'voice' ? '正在保存…' : '保存配音设置'}</button></div>
    </SettingsSection>
  </main>
}
