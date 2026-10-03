import { useEffect, useState } from 'react'
import {
  loadCloudConflicts, loadCloudProviders, loadCloudSessions, loadCloudStatus,
  loginCloudAccount, logoutCloudAccount, registerCloudAccount, requestCloudEmailVerification,
  requestCloudPasswordReset, resetCloudPassword, resolveCloudConflict, revokeCloudSession,
  saveCloudSettings, startCloudOidc, syncCloud, verifyCloudEmail,
} from '../../api'
import type { CloudAccountStatus, CloudConflict, CloudProvider, CloudSession } from '../../api'

export default function CloudAccountPage({ onNotice }: { onNotice: (value: string) => void }) {
  const [status, setStatus] = useState<CloudAccountStatus | null>(null)
  const [providers, setProviders] = useState<CloudProvider[]>([])
  const [sessions, setSessions] = useState<CloudSession[]>([])
  const [conflicts, setConflicts] = useState<CloudConflict[]>([])
  const [baseUrl, setBaseUrl] = useState('http://127.0.0.1:8010')
  const [email, setEmail] = useState(''); const [password, setPassword] = useState(''); const [displayName, setDisplayName] = useState('')
  const [token, setToken] = useState(''); const [newPassword, setNewPassword] = useState('')
  const [registering, setRegistering] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState('')

  async function refresh() {
    const next = await loadCloudStatus(); setStatus(next); setBaseUrl(next.base_url)
    if (next.connected) {
      const [sessionRows, conflictRows] = await Promise.all([loadCloudSessions(), next.email_verified ? loadCloudConflicts() : Promise.resolve([])])
      setSessions(sessionRows); setConflicts(conflictRows)
    } else {
      setSessions([]); setConflicts([])
      setProviders(await loadCloudProviders().catch(() => []))
    }
  }
  useEffect(() => { void refresh().catch((reason) => setError(reason instanceof Error ? reason.message : String(reason))) }, [])
  async function run(work: () => Promise<unknown>, success: string) {
    setBusy(true); setError('')
    try {
      const result = await work()
      if (result && typeof result === 'object' && 'development_verification_token' in result) {
        setToken(String((result as CloudAccountStatus).development_verification_token || ''))
      }
      if (result && typeof result === 'object' && 'development_token' in result) {
        setToken(String((result as { development_token?: string }).development_token || ''))
      }
      await refresh(); onNotice(success)
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }
  async function openProvider(id: string) { try { if (window.bingduDesktop) await window.bingduDesktop.startOidc(id); else window.location.href = await startCloudOidc(id) } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) } }
  function submitAccount() {
    if (password.length < 10) {
      setError(`云端账号密码至少需要 10 个字符，当前为 ${password.length} 个字符。`)
      return
    }
    void run(() => registering ? registerCloudAccount(email, password, displayName) : loginCloudAccount(email, password), registering ? '云端账号已创建' : '云端账号已绑定')
  }

  return <main className="app-page cloud-page">
    <header className="page-heading"><div><span>账户与数据</span><h1>云端与同步</h1><p>绑定账号后，可在自己的设备之间同步学习数据和阅读进度。</p></div></header>
    <section className="page-surface cloud-settings-card">
      <header><div><h2>云端服务</h2><p>远程服务必须使用 HTTPS；本机测试可使用 localhost。</p></div><span className={status?.connected ? 'status-pill ok' : 'status-pill'}>{status?.connected ? '已绑定' : '未绑定'}</span></header>
      <div className="cloud-endpoint"><label className="field"><span>服务地址</span><input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /></label><button className="button" disabled={busy} onClick={() => void run(() => saveCloudSettings(baseUrl), '云端地址已验证')}>验证并保存</button></div>
      {error && <div className="error-box">{error}</div>}
    </section>
    {!status?.connected ? <section className="page-surface cloud-auth-card">
      <header><div><h2>{registering ? '创建云端账号' : '绑定云端账号'}</h2><p>同一邮箱登录其他设备后即可拉取学习数据。</p></div><button className="text-button" onClick={() => setRegistering(!registering)}>{registering ? '已有账号' : '创建账号'}</button></header>
      {registering && <label className="field"><span>昵称</span><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label>}
      <label className="field"><span>邮箱</span><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" /></label>
      <label className="field"><span>密码</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={registering ? 'new-password' : 'current-password'} placeholder="至少 10 个字符" aria-describedby={registering ? 'cloud-password-help' : undefined} />{registering && <small id="cloud-password-help" className={`field-help ${password.length > 0 && password.length < 10 ? 'invalid' : ''}`}>{password.length >= 10 ? '密码长度符合要求' : `至少需要 10 个字符${password.length ? `，还差 ${10 - password.length} 个` : ''}`}</small>}</label>
      <button className="button primary" disabled={busy || !email || (registering && !displayName)} onClick={submitAccount}>{registering ? '创建并绑定' : '登录并绑定'}</button>
      {!!providers.length && <div className="provider-list">{providers.map((provider) => <button className="button" key={provider.id} onClick={() => void openProvider(provider.id)}>使用 {provider.name} 登录</button>)}</div>}
      <details className="account-recovery"><summary>忘记密码或恢复账号</summary><div><label className="field"><span>邮箱</span><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} /></label><button className="button small" onClick={() => void run(() => requestCloudPasswordReset(email), '如果账号存在，重置邮件已发送')}>发送重置邮件</button><label className="field"><span>恢复令牌</span><input value={token} onChange={(event) => setToken(event.target.value)} /></label><label className="field"><span>新密码</span><input type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} /></label><button className="button small" disabled={token.length < 20 || newPassword.length < 10} onClick={() => void run(() => resetCloudPassword(token, newPassword), '密码已重置')}>完成账号恢复</button></div></details>
    </section> : <>
      <section className="page-surface sync-actions-card"><header><div><h2>{status.display_name}</h2><p>{status.email} · {status.email_verified ? '邮箱已验证' : '邮箱待验证'}</p></div><button className="text-button" onClick={() => void run(logoutCloudAccount, '已解除本机绑定')}>解除绑定</button></header>
        {!status.email_verified && <div className="verify-row"><button className="button small" onClick={() => void run(() => requestCloudEmailVerification(status.email || ''), '验证邮件已发送')}>发送验证邮件</button><input placeholder="粘贴验证令牌" value={token} onChange={(event) => setToken(event.target.value)} /><button className="button small" disabled={token.length < 20} onClick={() => void run(() => verifyCloudEmail(token), '邮箱验证完成')}>验证</button></div>}
        <div className="sync-action-grid"><button className="button primary" disabled={busy || !status.email_verified} onClick={() => void run(() => syncCloud(false), '上传与拉取完成')}>立即同步</button><button className="button" disabled={busy || !status.email_verified} onClick={() => void run(() => syncCloud(true), '云端数据已拉取')}>拉取数据</button><span>上次同步：{status.last_sync_at ? new Date(status.last_sync_at * 1000).toLocaleString() : '尚未同步'}</span><span>本地游标 {status.local_cursor ?? 0} · 云端游标 {status.remote_cursor ?? 0}</span></div>
        {status.last_error && <div className="error-box">上次同步：{status.last_error}</div>}
      </section>
      <section className="page-surface"><header><div><h2>冲突处理</h2><p>仅并发修改的内容会出现在这里，选择要保留的版本。</p></div><span>{conflicts.length} 组</span></header>
        <div className="conflict-list">{conflicts.length ? conflicts.map((conflict) => <article key={conflict.id}><strong>{conflict.entity_type}</strong>{conflict.versions.map((version) => <button key={version.entity_id} onClick={() => void run(() => resolveCloudConflict(conflict.id, version.entity_id), '冲突已解决')}><span>{JSON.stringify(version.payload).slice(0, 160)}</span><small>{new Date(version.updated_at * 1000).toLocaleString()} · 保留此版本</small></button>)}</article>) : <p className="muted">没有待处理冲突。</p>}</div>
      </section>
      <section className="page-surface"><header><div><h2>登录设备</h2><p>发现不认识的设备时可以撤销其会话。</p></div></header><div className="session-list">{sessions.map((session) => <div key={session.id}><span><strong>{session.device_name}</strong><small>{new Date(session.last_used_at * 1000).toLocaleString()}</small></span><button disabled={busy} onClick={() => void run(() => revokeCloudSession(session.id), '设备会话已撤销')}>撤销</button></div>)}</div></section>
    </>}
  </main>
}

