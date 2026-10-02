import { useEffect, useState } from 'react'
import { downloadBookTransfer, downloadFullBackup, importBookTransfer, loadAiUsage, restoreFullBackup } from '../../api'
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
  const [backupBusy, setBackupBusy] = useState<'export' | 'restore' | 'transfer-export' | 'transfer-import' | null>(null)
  const [pendingRestore, setPendingRestore] = useState<File | null>(null)
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

  async function exportBackup() {
    setBackupBusy('export'); setError('')
    try { await downloadFullBackup(); onNotice('完整备份已导出。') }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBackupBusy(null) }
  }

  async function importBackup(file: File | null) {
    if (!file) return
    setBackupBusy('restore'); setError('')
    try {
      const result = await restoreFullBackup(file)
      onNotice(`已恢复 ${result.restored_rows} 条数据，恢复前备份为 ${result.safety_backup}。`)
      window.setTimeout(() => window.location.reload(), 800)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBackupBusy(null); setPendingRestore(null) }
  }

  async function exportBookMigration() {
    setBackupBusy('transfer-export'); setError('')
    try { await downloadBookTransfer(); onNotice('书籍迁移包已导出。') }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBackupBusy(null) }
  }

  async function importBookMigration(file: File | null) {
    if (!file) return
    setBackupBusy('transfer-import'); setError('')
    try {
      const result = await importBookTransfer(file)
      onNotice(`已载入 ${result.imported_books} 本书，跳过 ${result.skipped_books} 本已有书籍。`)
      window.setTimeout(() => window.location.reload(), 800)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBackupBusy(null) }
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
    <SettingsSection title="数据备份与恢复" description="导出当前账号的书籍资源、阅读进度、词库、卡片、复习记录和本机设置。备份包含 API Key，请妥善保存。">
      <div className="setting-row"><div><strong>书籍资源迁移包</strong><span>用于把当前资料空间的书籍、章节、译文、封面和插图载入其他账号；不会覆盖目标账号的学习记录和设置。</span></div><div className="settings-inline-actions"><button className="button" disabled={backupBusy !== null || !books.length} onClick={() => void exportBookMigration()}>{backupBusy === 'transfer-export' ? '正在打包…' : '下载迁移包'}</button><label className={`button primary ${backupBusy ? 'disabled' : ''}`}>{backupBusy === 'transfer-import' ? '正在载入…' : '载入迁移包'}<input hidden type="file" accept=".zip,application/zip" disabled={backupBusy !== null} onChange={(event) => { void importBookMigration(event.target.files?.[0] ?? null); event.currentTarget.value = '' }} /></label></div></div>
      <div className="setting-row"><div><strong>完整本地备份</strong><span>备份带版本清单和 SHA-256 校验；恢复前会自动保留当前数据副本。</span></div><div className="settings-inline-actions"><button className="button" disabled={backupBusy !== null} onClick={() => void exportBackup()}>{backupBusy === 'export' ? '正在导出…' : '导出 ZIP'}</button><label className={`button primary ${backupBusy ? 'disabled' : ''}`}>恢复备份<input hidden type="file" accept=".zip,application/zip" disabled={backupBusy !== null} onChange={(event) => { setPendingRestore(event.target.files?.[0] ?? null); event.currentTarget.value = '' }} /></label></div></div>
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
    {pendingRestore && <div className="dialog-backdrop"><section className="dialog restore-confirm" role="alertdialog" aria-modal="true" aria-labelledby="restore-confirm-title"><header><div><small>数据恢复</small><h2 id="restore-confirm-title">替换当前账号的数据？</h2></div><button aria-label="关闭" onClick={() => setPendingRestore(null)}>×</button></header><p>将从“{pendingRestore.name}”恢复书库、学习记录、卡片和设置。开始前会自动创建当前数据的安全备份。</p><footer><button className="button" onClick={() => setPendingRestore(null)}>取消</button><button className="button primary" disabled={backupBusy !== null} onClick={() => void importBackup(pendingRestore)}>{backupBusy === 'restore' ? '正在恢复…' : '确认恢复'}</button></footer></section></div>}
  </main>
}
