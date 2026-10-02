import { useEffect, useRef, useState } from 'react'
import { loginLocalAccount, logoutCurrentAccount, registerLocalAccount } from '../../app/session'
import { loadCloudProviders, loginCloudAccount, registerCloudAccount, requestCloudPasswordReset, resetCloudPassword, startCloudOidc } from '../../api'
import type { CloudProvider } from '../../api'
import type { CurrentUser } from '../../types'
import { isOnlineDeployment } from '../../deployment'

export default function LoginPage({ currentUser, serverReady, onEnter }: {
  currentUser: CurrentUser | null
  serverReady: boolean | null
  onEnter: (user: CurrentUser) => Promise<void> | void
}) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [accountKind, setAccountKind] = useState<'cloud' | 'local'>('cloud')
  const [displayName, setDisplayName] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [logoBouncing, setLogoBouncing] = useState(false)
  const logoAudiosRef = useRef(new Set<HTMLAudioElement>())
  const [providers, setProviders] = useState<CloudProvider[]>([])
  const [recoveryOpen, setRecoveryOpen] = useState(false); const [recoveryToken, setRecoveryToken] = useState(''); const [recoveryPassword, setRecoveryPassword] = useState('')

  useEffect(() => {
    void loadCloudProviders().then(setProviders).catch(() => setProviders([]))
    if (window.location.hash.includes('cloud=connected') && currentUser) void onEnter(currentUser)
  }, [currentUser])

  function playLogoSound() {
    const audio = new Audio('/bingdu-logo-click.wav')
    logoAudiosRef.current.add(audio)
    audio.addEventListener('ended', () => logoAudiosRef.current.delete(audio), { once: true })
    void audio.play().catch(() => logoAudiosRef.current.delete(audio))
    setLogoBouncing(false)
    window.requestAnimationFrame(() => setLogoBouncing(true))
  }

  async function submit() {
    setBusy(true); setError('')
    try {
      if (accountKind === 'cloud') {
        if (!currentUser) throw new Error(isOnlineDeployment ? '服务仍在初始化' : '本地服务仍在初始化')
        if (mode === 'register') await registerCloudAccount(username, password, displayName)
        else await loginCloudAccount(username, password)
        await onEnter(currentUser)
        return
      }
      const user = mode === 'register' ? await registerLocalAccount(displayName, username, password) : await loginLocalAccount(username, password)
      await onEnter(user)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally { setBusy(false) }
  }

  async function openProvider(providerId: string) {
    setBusy(true); setError('')
    try { window.location.href = await startCloudOidc(providerId) }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); setBusy(false) }
  }

  async function enterLocalMode() {
    if (!serverReady || busy) return
    setBusy(true); setError('')
    try {
      const user = currentUser?.is_guest ? currentUser : await logoutCurrentAccount()
      await onEnter(user)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally { setBusy(false) }
  }

  return <main className="login-page">
    <section className="login-intro">
      <button className={`login-brand ${logoBouncing ? 'is-bouncing' : ''}`} onClick={playLogoSound} onAnimationEnd={() => setLogoBouncing(false)} aria-label="播放冰读语音"><img src="/bingdu-logo.png" alt="" /><span><strong>冰读</strong><small>baka都能用的日语学习阅读器</small></span></button>
      <div className="login-copy"><h1>让baka都能<br />好好读书</h1></div>
      <div className="login-feature-list"><span>逐句阅读与释义</span><span>知识盲区与复习</span><span>{isOnlineDeployment ? '账号隔离的数据空间' : '本地优先的数据边界'}</span></div>
    </section>
    <section className="login-panel">
      <div className="login-form-wrap">
        <div className="login-status"><i className={serverReady ? 'ready' : ''} />{serverReady === null ? `正在连接${isOnlineDeployment ? '服务' : '本地服务'}` : serverReady ? `${isOnlineDeployment ? '服务' : '本地服务'}已连接` : `${isOnlineDeployment ? '服务' : '本地服务'}未连接`}</div>
        <h2>{mode === 'login' ? '欢迎回来' : `创建${accountKind === 'cloud' ? '云端' : '本地'}账号`}</h2>
        <p>{accountKind === 'cloud' ? '在不同设备使用同一账号，继续阅读与学习。' : '账号与数据只保存在这台电脑。'}</p>
        <div className="login-tabs account-kind-tabs"><button className={accountKind === 'cloud' ? 'active' : ''} onClick={() => setAccountKind('cloud')}>云端账号</button><button className={accountKind === 'local' ? 'active' : ''} onClick={() => setAccountKind('local')}>本地账号</button></div>
        <div className="login-tabs"><button className={mode === 'login' ? 'active' : ''} onClick={() => setMode('login')}>登录</button><button className={mode === 'register' ? 'active' : ''} onClick={() => setMode('register')}>注册</button></div>
        {mode === 'register' && <label className="field"><span>显示名称</span><input autoFocus value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="阅读时显示的名字" /></label>}
        <label className="field"><span>{accountKind === 'cloud' ? '邮箱' : '用户名'}</span><input type={accountKind === 'cloud' ? 'email' : 'text'} autoFocus={mode === 'login'} value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" placeholder={accountKind === 'cloud' ? 'name@example.com' : '请输入用户名'} /></label>
        <label className="field"><span>密码</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} placeholder="至少 8 个字符" onKeyDown={(event) => { if (event.key === 'Enter') void submit() }} /></label>
        {error && <div className="error-box">{error}</div>}
        <button className="button primary login-submit" disabled={busy || !serverReady || !username.trim() || password.length < (accountKind === 'cloud' ? 10 : 8) || (mode === 'register' && !displayName.trim())} onClick={() => void submit()}>{busy ? '正在处理…' : mode === 'login' ? '进入冰读' : '创建账号并进入'}</button>
        {accountKind === 'cloud' && <>{!!providers.length && <div className="provider-list">{providers.map((provider) => <button className="button ghost small" key={provider.id} onClick={() => void openProvider(provider.id)}>使用 {provider.name} 登录</button>)}</div>}<button className="login-recovery-link" onClick={() => setRecoveryOpen(!recoveryOpen)}>忘记密码或恢复账号</button>{recoveryOpen && <div className="login-recovery-panel"><button className="button ghost small" disabled={!username.includes('@') || busy} onClick={() => void requestCloudPasswordReset(username).then(() => setError('如果账号存在，重置邮件已发送。')).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))}>向当前邮箱发送重置邮件</button><label className="field"><span>邮件中的恢复令牌</span><input value={recoveryToken} onChange={(event) => setRecoveryToken(event.target.value)} /></label><label className="field"><span>新密码</span><input type="password" value={recoveryPassword} onChange={(event) => setRecoveryPassword(event.target.value)} /></label><button className="button small" disabled={recoveryToken.length < 20 || recoveryPassword.length < 10 || busy} onClick={() => { setBusy(true); void resetCloudPassword(recoveryToken,recoveryPassword).then(() => { setError('密码已重置，请使用新密码登录。'); setRecoveryOpen(false) }).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason))).finally(() => setBusy(false)) }}>重设密码</button></div>}</>}
        {currentUser && !currentUser.is_guest && <div className="remembered-account" aria-label={`已记住账号 ${currentUser.display_name}`}><span><small>已记住的本地账号</small><strong>{currentUser.display_name}</strong>{currentUser.username && <em>@{currentUser.username}</em>}</span><button className="button ghost small" disabled={!serverReady || busy} onClick={() => void onEnter(currentUser)}>继续进入</button></div>}
        <div className="local-entry"><span>{currentUser?.is_guest ? '不想创建账号？' : isOnlineDeployment ? '以独立访客空间临时使用' : '只在这台电脑阅读和学习'}</span><button disabled={!serverReady || busy} onClick={() => void enterLocalMode()}>{busy ? '正在切换…' : `使用${isOnlineDeployment ? '访客' : '本机'}模式进入`}</button></div>
      </div>
    </section>
  </main>
}

