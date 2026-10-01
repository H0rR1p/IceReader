import { useRef, useState } from 'react'
import { loginLocalAccount, logoutCurrentAccount, registerLocalAccount } from '../../app/session'
import type { CurrentUser } from '../../types'

export default function LoginPage({ currentUser, serverReady, onEnter }: {
  currentUser: CurrentUser | null
  serverReady: boolean | null
  onEnter: (user: CurrentUser) => Promise<void> | void
}) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [displayName, setDisplayName] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [logoBouncing, setLogoBouncing] = useState(false)
  const logoAudiosRef = useRef(new Set<HTMLAudioElement>())

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
      const user = mode === 'register'
        ? await registerLocalAccount(displayName, username, password)
        : await loginLocalAccount(username, password)
      await onEnter(user)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally { setBusy(false) }
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
      <div className="login-feature-list"><span>逐句阅读与释义</span><span>知识盲区与复习</span><span>本地优先的数据边界</span></div>
    </section>
    <section className="login-panel">
      <div className="login-form-wrap">
        <div className="login-status"><i className={serverReady ? 'ready' : ''} />{serverReady === null ? '正在连接本地服务' : serverReady ? '本地服务已连接' : '本地服务未连接'}</div>
        <div className="local-mode-card">
          <div><strong>本机模式</strong><span>无需创建账号，数据只保存在这台电脑</span></div>
          <button className="button primary" disabled={!serverReady || busy} onClick={() => void enterLocalMode()}>{busy ? '正在切换…' : '使用本机模式进入'}</button>
        </div>
        <h2>{mode === 'login' ? '欢迎回来' : '创建本地账号'}</h2>
        <p>{mode === 'login' ? '登录后继续你的阅读与学习记录。' : '账号只保存在这台电脑，之后可以绑定云账号。'}</p>
        <div className="login-tabs"><button className={mode === 'login' ? 'active' : ''} onClick={() => setMode('login')}>登录</button><button className={mode === 'register' ? 'active' : ''} onClick={() => setMode('register')}>注册</button></div>
        {mode === 'register' && <label className="field"><span>显示名称</span><input autoFocus value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="阅读时显示的名字" /></label>}
        <label className="field"><span>用户名</span><input autoFocus={mode === 'login'} value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" placeholder="请输入用户名" /></label>
        <label className="field"><span>密码</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} placeholder="至少 8 个字符" onKeyDown={(event) => { if (event.key === 'Enter') void submit() }} /></label>
        {error && <div className="error-box">{error}</div>}
        <button className="button primary login-submit" disabled={busy || !serverReady || !username.trim() || password.length < 8 || (mode === 'register' && !displayName.trim())} onClick={() => void submit()}>{busy ? '正在处理…' : mode === 'login' ? '进入冰读' : '创建账号并进入'}</button>
        {currentUser && !currentUser.is_guest && <div className="remembered-account" aria-label={`已记住账号 ${currentUser.display_name}`}><span><small>已记住的本地账号</small><strong>{currentUser.display_name}</strong>{currentUser.username && <em>@{currentUser.username}</em>}</span><button className="button ghost small" disabled={!serverReady || busy} onClick={() => void onEnter(currentUser)}>继续进入</button></div>}
      </div>
    </section>
  </main>
}
