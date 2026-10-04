import { useEffect, useState } from 'react'
import { downloadBookTransfer, downloadFullBackup, importBookTransfer, loadAiUsage, restoreFullBackup } from '../../api'
import type { AiUsageSummary, ApiSettings, Book } from '../../types'
import type { TranslationMode } from '../translation/pipeline'
import LegalLinks from '../../app/LegalLinks'

function SettingsSection({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  return <section className="settings-section"><header><h2>{title}</h2><p>{description}</p></header><div className="settings-section-body">{children}</div></section>
}

export default function SettingsPage({ apiSettings, books, translationMode, translationConcurrency, onSaveApi, onBookImageVisibility, onTranslationModeChange, onTranslationConcurrencyChange, onNotice }: {
  apiSettings: ApiSettings
  books: Book[]
  translationMode: TranslationMode
  translationConcurrency: number
  onSaveApi: (next: ApiSettings) => Promise<void>
  onBookImageVisibility: (book: Book, visible: boolean) => Promise<void>
  onTranslationModeChange: (mode: TranslationMode) => void
  onTranslationConcurrencyChange: (value: number) => void
  onNotice: (message: string) => void
}) {
  const [apiDraft, setApiDraft] = useState(apiSettings)
  const [usage, setUsage] = useState<AiUsageSummary | null>(null)
  const [saving, setSaving] = useState<'api' | null>(null)
  const [error, setError] = useState('')
  const [backupBusy, setBackupBusy] = useState<'export' | 'restore' | 'transfer-export' | 'transfer-import' | null>(null)
  const [pendingRestore, setPendingRestore] = useState<File | null>(null)
  const [desktopInfo, setDesktopInfo] = useState<{ version: string; dataDirectory: string } | null>(null)
  useEffect(() => { void window.bingduDesktop?.info().then(setDesktopInfo).catch(() => undefined) }, [])
  useEffect(() => setApiDraft(apiSettings), [apiSettings])
  useEffect(() => { void loadAiUsage().then(setUsage).catch(() => undefined) }, [])

  async function saveApi() {
    setSaving('api'); setError('')
    try {
      await onSaveApi({ ...apiDraft, apiKey: apiDraft.apiKey.trim(), baseUrl: apiDraft.baseUrl.trim().replace(/\/$/, ''), model: apiDraft.model.trim(), cacheHitUsdPerMillion: Math.max(0, apiDraft.cacheHitUsdPerMillion || 0), cacheMissUsdPerMillion: Math.max(0, apiDraft.cacheMissUsdPerMillion || 0), outputUsdPerMillion: Math.max(0, apiDraft.outputUsdPerMillion || 0) })
      onNotice('AI 设置已保存。')
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
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
    try { await downloadBookTransfer(); onNotice('数据迁移包已导出。') }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBackupBusy(null) }
  }

  async function importBookMigration(file: File | null) {
    if (!file) return
    setBackupBusy('transfer-import'); setError('')
    try {
      const result = await importBookTransfer(file)
      onNotice(`已载入 ${result.imported_books} 本书、${result.imported_cards ?? 0} 张卡片和 ${result.imported_learning_records ?? 0} 条学习记录，跳过 ${result.skipped_books} 本已有书籍。`)
      window.setTimeout(() => window.location.reload(), 800)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBackupBusy(null) }
  }

  return <main className="app-page settings-page">
    <header className="page-heading"><div><p className="eyebrow">偏好与连接</p><h1>设置</h1><span>集中管理阅读处理、AI 服务和本地数据。</span></div></header>
    {error && <div className="error-box page-error">{error}</div>}
    <SettingsSection title="源码与许可证" description="冰读采用 AGPL-3.0-or-later；对应源码包含当前版本的应用代码、构建脚本与依赖源码。"><LegalLinks /></SettingsSection>
    {desktopInfo && <SettingsSection title="桌面客户端" description={`冰读 ${desktopInfo.version} · 数据与安装目录独立保存。`}><div className="setting-row"><div><strong>数据目录</strong><span>{desktopInfo.dataDirectory}</span></div><button className="button" onClick={() => void window.bingduDesktop?.openDataDirectory()}>打开数据目录</button></div></SettingsSection>}
    <SettingsSection title="阅读处理" description="这些选项用于后台翻译，修改后立即生效。">
      <div className="setting-row"><div><strong>后台翻译模式</strong><span>快速句意只生成译文；完整释义还会补充词典未命中的词义。</span></div><select value={translationMode} onChange={(event) => onTranslationModeChange(event.target.value as TranslationMode)}><option value="meaning">快速句意</option><option value="full">完整释义</option></select></div>
      <div className="setting-row"><div><strong>并发任务数</strong><span>网络不稳定或触发限流时建议调低。</span></div><select value={translationConcurrency} onChange={(event) => onTranslationConcurrencyChange(Number(event.target.value))}>{Array.from({ length: 8 }, (_, index) => <option key={index + 1} value={index + 1}>{index + 1}</option>)}</select></div>
    </SettingsSection>
    <SettingsSection title="书籍插图" description="统一管理每本书的插图显示状态，换章后会继续沿用。">
      {books.length ? <div className="book-image-settings-list">{books.map((book) => <div className="setting-row" key={book.id}><div><strong>{book.title}</strong><span>{book.author || '作者未知'}</span></div><select value={book.showImages === false ? 'hidden' : 'visible'} onChange={(event) => void onBookImageVisibility(book, event.target.value === 'visible').catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))}><option value="visible">显示插图</option><option value="hidden">隐藏插图</option></select></div>)}</div> : <p className="settings-empty">导入书籍后可以在这里统一管理插图。</p>}
    </SettingsSection>
    <SettingsSection title="数据备份与恢复" description="导出当前账号的书籍资源、阅读进度、词库、卡片、复习记录和本机设置。安卓加密凭据不会导出，迁移后请重新配置 AI 密钥。备份含私人学习数据，请妥善保存。">
      <div className="setting-row"><div><strong>跨账号数据迁移包</strong><span>将书籍、插图、阅读进度、书签、词库、卡片、复习与学习记录合并到其他账号，包括云端账号。不包含密码、会话和 API 密钥，保留目标账号原有数据。</span></div><div className="settings-inline-actions"><button className="button" disabled={backupBusy !== null} onClick={() => void exportBookMigration()}>{backupBusy === 'transfer-export' ? '正在打包…' : '下载迁移包'}</button><label className={`button primary ${backupBusy ? 'disabled' : ''}`}>{backupBusy === 'transfer-import' ? '正在载入…' : '载入迁移包'}<input hidden type="file" accept=".zip,application/zip" disabled={backupBusy !== null} onChange={(event) => { void importBookMigration(event.target.files?.[0] ?? null); event.currentTarget.value = '' }} /></label></div></div>
      <div className="setting-row"><div><strong>完整本地备份</strong><span>备份带版本清单和 SHA-256 校验；恢复前会自动保留当前数据副本。</span></div><div className="settings-inline-actions"><button className="button" disabled={backupBusy !== null} onClick={() => void exportBackup()}>{backupBusy === 'export' ? '正在导出…' : '导出 ZIP'}</button><label className={`button primary ${backupBusy ? 'disabled' : ''}`}>恢复备份<input hidden type="file" accept=".zip,application/zip" disabled={backupBusy !== null} onChange={(event) => { setPendingRestore(event.target.files?.[0] ?? null); event.currentTarget.value = '' }} /></label></div></div>
    </SettingsSection>
    <SettingsSection title="AI 服务" description="API Key 只保存在本机，并仅发送给你配置的兼容接口。">
      <div className="settings-form-grid"><label className="field wide"><span>API Key</span><input type="password" autoComplete="off" value={apiDraft.apiKey} onChange={(event) => setApiDraft({ ...apiDraft, apiKey: event.target.value })} placeholder={apiDraft.hasStoredApiKey ? '已保存；留空保持不变' : 'sk-…'} /></label><label className="field"><span>Base URL</span><input value={apiDraft.baseUrl} onChange={(event) => setApiDraft({ ...apiDraft, baseUrl: event.target.value })} /></label><label className="field"><span>模型</span><input value={apiDraft.model} onChange={(event) => setApiDraft({ ...apiDraft, model: event.target.value })} /></label><label className="field"><span>缓存命中价 / 百万 token</span><input type="number" min="0" step="0.001" value={apiDraft.cacheHitUsdPerMillion} onChange={(event) => setApiDraft({ ...apiDraft, cacheHitUsdPerMillion: Number(event.target.value) })} /></label><label className="field"><span>缓存未命中价</span><input type="number" min="0" step="0.001" value={apiDraft.cacheMissUsdPerMillion} onChange={(event) => setApiDraft({ ...apiDraft, cacheMissUsdPerMillion: Number(event.target.value) })} /></label><label className="field"><span>输出价</span><input type="number" min="0" step="0.001" value={apiDraft.outputUsdPerMillion} onChange={(event) => setApiDraft({ ...apiDraft, outputUsdPerMillion: Number(event.target.value) })} /></label></div>
      {usage && <div className="settings-usage"><span><b>{usage.requests}</b> 次请求</span><span><b>{usage.cache_miss_tokens.toLocaleString()}</b> 未命中输入</span><span><b>{usage.completion_tokens.toLocaleString()}</b> 输出</span><span><b>{usage.response_cache_hits}</b> 次缓存命中</span><small>{usage.pricing_configured ? `估算费用 $${usage.estimated_cost_usd.toFixed(6)}` : '配置单价后显示费用估算'}</small></div>}
      <div className="settings-actions"><button className="button primary" disabled={saving !== null || !apiDraft.baseUrl.trim() || !apiDraft.model.trim()} onClick={() => void saveApi()}>{saving === 'api' ? '正在保存…' : '保存 AI 设置'}</button></div>
    </SettingsSection>

    {pendingRestore && <div className="dialog-backdrop"><section className="dialog restore-confirm" role="alertdialog" aria-modal="true" aria-labelledby="restore-confirm-title"><header><div><small>数据恢复</small><h2 id="restore-confirm-title">替换当前账号的数据？</h2></div><button aria-label="关闭" onClick={() => setPendingRestore(null)}>×</button></header><p>将从“{pendingRestore.name}”恢复书库、学习记录、卡片和设置。开始前会自动创建当前数据的安全备份。</p><footer><button className="button" onClick={() => setPendingRestore(null)}>取消</button><button className="button primary" disabled={backupBusy !== null} onClick={() => void importBackup(pendingRestore)}>{backupBusy === 'restore' ? '正在恢复…' : '确认恢复'}</button></footer></section></div>}
  </main>
}
