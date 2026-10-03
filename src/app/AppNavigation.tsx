import { useRef, useState } from 'react'
import type { CurrentUser } from '../types'
import UserAvatar from './UserAvatar'
import { isOnlineDeployment } from '../deployment'

export type AppPage = 'library' | 'dictionary' | 'cards' | 'review' | 'profile' | 'cloud' | 'admin' | 'settings'

export default function AppNavigation({ page, user, bookCount, canResumeReading, onNavigate, onResumeReading, onImport }: {
  page: AppPage
  user: CurrentUser
  bookCount: number
  canResumeReading?: boolean
  onNavigate: (page: AppPage) => void
  onResumeReading?: () => void
  onImport: () => void
}) {
  const accountLabel = user.cloud_connected ? '云端账号' : isOnlineDeployment ? '访客模式' : '本机模式'
  const [logoBouncing, setLogoBouncing] = useState(false)
  const logoAudiosRef = useRef(new Set<HTMLAudioElement>())
  function activateBrand() {
    const audio = new Audio('/bingdu-logo-click.wav')
    logoAudiosRef.current.add(audio)
    audio.addEventListener('ended', () => logoAudiosRef.current.delete(audio), { once: true })
    void audio.play().catch(() => logoAudiosRef.current.delete(audio))
    setLogoBouncing(false)
    window.requestAnimationFrame(() => setLogoBouncing(true))
    onNavigate('library')
  }
  const item = (target: AppPage, icon: string, label: string, detail?: string) => (
    <button className={`app-nav-item ${page === target ? 'active' : ''}`} onClick={() => onNavigate(target)}>
      <span aria-hidden="true">{icon}</span><strong>{label}</strong>{detail && <small>{detail}</small>}
    </button>
  )
  return <aside className="app-navigation">
    <button className={`nav-brand ${logoBouncing ? 'is-bouncing' : ''}`} onClick={activateBrand} onAnimationEnd={() => setLogoBouncing(false)} aria-label="返回书架并播放冰读语音">
      <img src="/bingdu-logo.png" alt="" />
      <span><strong>冰读</strong><small>日语学习阅读器</small></span>
    </button>
    <nav aria-label="主要页面">
      <p className="nav-section-label">阅读</p>
      {item('library', '▦', '我的书架', `${bookCount} 本`)}
      {canResumeReading && onResumeReading && <button className="app-nav-item resume-reading-item" onClick={onResumeReading}><span aria-hidden="true">▶</span><strong>继续阅读</strong></button>}
      <button className="app-nav-item" onClick={onImport}><span aria-hidden="true">＋</span><strong>导入书籍</strong></button>
      <p className="nav-section-label">学习</p>
      {item('dictionary', 'あ', '个人词库')}
      {item('cards', '◇', '词语卡片')}
      {item('review', '✓', '今日复习')}
      <p className="nav-section-label">账户</p>
      {item('profile', '○', '个人主页')}
      {item('cloud', '↻', '云端与同步')}
      {user.cloud_role === 'admin' && item('admin', '◎', '账号管理')}
      {item('settings', '⚙', '设置')}
    </nav>
    <button className="nav-user" onClick={() => onNavigate('profile')}>
      <UserAvatar user={user} className="nav-avatar" />
      <span><strong>{user.cloud_display_name || user.display_name}</strong><small>{accountLabel}</small></span>
    </button>
  </aside>
}
