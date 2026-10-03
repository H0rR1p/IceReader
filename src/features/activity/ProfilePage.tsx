import { useEffect, useMemo, useState } from 'react'
import { loadActivityHeatmap, loadActivitySummary, loadCardSummary, loadSyncStatus, updateCloudDisplayName } from '../../api'
import { loadLocalSessions, revokeLocalSession, revokeOtherLocalSessions, updateCurrentUserProfile, uploadCurrentUserAvatar } from '../../app/session'
import type { LocalSession } from '../../app/session'
import UserAvatar from '../../app/UserAvatar'
import AvatarCropDialog from './AvatarCropDialog'
import type { ActivityDay, ActivitySummary, CardSummary, SyncStatus } from '../../api'
import type { Book, CurrentUser } from '../../types'
import { isOnlineDeployment } from '../../deployment'

function dateKey(value: Date) { return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}` }
function heatLevel(seconds: number) { const minutes = seconds / 60; return !minutes ? 0 : minutes < 15 ? 1 : minutes < 30 ? 2 : minutes < 60 ? 3 : 4 }

export default function ProfilePage({ user, books, onUserChange, onLogout, onOpenCards, onOpenReview }: {
  user: CurrentUser
  books: Book[]
  onUserChange: (user: CurrentUser) => void
  onLogout: () => Promise<void>
  onOpenCards: () => void
  onOpenReview: () => void
}) {
  const [days, setDays] = useState<ActivityDay[]>([])
  const [summary, setSummary] = useState<ActivitySummary | null>(null)
  const [cards, setCards] = useState<CardSummary | null>(null)
  const [sync, setSync] = useState<SyncStatus | null>(null)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)
  const [editingProfile, setEditingProfile] = useState(false)
  const [displayName, setDisplayName] = useState(user.cloud_display_name || user.display_name)
  const [profileSaving, setProfileSaving] = useState(false)
  const [avatarSource, setAvatarSource] = useState<File | null>(null)
  const [sessions, setSessions] = useState<LocalSession[]>([])
  const accountLabel = user.cloud_connected ? '云端账号' : isOnlineDeployment ? '访客模式' : '本机模式'
  useEffect(() => {
    const controller = new AbortController()
    void Promise.all([loadActivityHeatmap(controller.signal), loadActivitySummary(30, controller.signal), loadCardSummary(controller.signal), loadSyncStatus(controller.signal), loadLocalSessions(controller.signal)])
      .then(([nextDays, nextSummary, nextCards, nextSync, nextSessions]) => { setDays(nextDays); setSummary(nextSummary); setCards(nextCards); setSync(nextSync); setSessions(nextSessions) })
      .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)) })
    return () => controller.abort()
  }, [])
  useEffect(() => setDisplayName(user.cloud_display_name || user.display_name), [user.cloud_display_name, user.display_name])
  const cells = useMemo(() => {
    const values = new Map(days.map((item) => [item.local_date, item])); const today = new Date(); const start = new Date(today); start.setDate(today.getDate() - 364 - today.getDay())
    return Array.from({ length: 371 }, (_value, index) => { const current = new Date(start); current.setDate(start.getDate() + index); const key = dateKey(current); return { key, future: current > today, value: values.get(key) } })
  }, [days])
  const translated = books.filter((book) => book.translationComplete).length
  const recent = books.filter((book) => book.lastOpenedAt).sort((a, b) => (b.lastOpenedAt ?? 0) - (a.lastOpenedAt ?? 0))[0]
  const weeklyTrend = useMemo(() => {
    const values = new Map(days.map((item) => [item.local_date, item]))
    const end = new Date(); const weeks: Array<{ label: string; total: number; reading: number; review: number; cards: number }> = []
    for (let week = 11; week >= 0; week -= 1) {
      const start = new Date(end); start.setHours(0, 0, 0, 0); start.setDate(end.getDate() - week * 7 - 6)
      let total = 0; let reading = 0; let review = 0; let cards = 0
      for (let day = 0; day < 7; day += 1) {
        const current = new Date(start); current.setDate(start.getDate() + day)
        const value = values.get(dateKey(current)); total += value?.active_seconds ?? 0
        reading += value?.reading_seconds ?? 0; review += value?.review_seconds ?? 0; cards += value?.card_seconds ?? 0
      }
      weeks.push({ label: `${start.getMonth() + 1}/${start.getDate()}`, total, reading, review, cards })
    }
    return weeks
  }, [days])
  const maxWeeklySeconds = Math.max(1, ...weeklyTrend.map((week) => week.total))

  async function saveDisplayName() {
    setProfileSaving(true); setError('')
    try {
      if (user.cloud_connected) await updateCloudDisplayName(displayName)
      const next = await updateCurrentUserProfile(displayName)
      onUserChange(next); setEditingProfile(false)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setProfileSaving(false) }
  }

  async function saveAvatar(file: File) {
    setProfileSaving(true); setError('')
    try { onUserChange(await uploadCurrentUserAvatar(file)); setAvatarSource(null) }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setProfileSaving(false) }
  }

  async function revokeSession(sessionId: string) {
    await revokeLocalSession(sessionId); setSessions((current) => current.filter((session) => session.id !== sessionId))
  }

  async function revokeOthers() {
    await revokeOtherLocalSessions(); setSessions((current) => current.filter((session) => session.current))
  }

  return <main className="app-page profile-page">
    <section className="profile-hero">
      <label className="profile-avatar-control" title="更换头像"><UserAvatar user={user} className="profile-avatar" /><input type="file" accept="image/jpeg,image/png,image/webp,image/gif" disabled={profileSaving} onChange={(event) => { setAvatarSource(event.target.files?.[0] ?? null); event.currentTarget.value = '' }} /><span>更换头像</span></label>
      <div><p className="eyebrow">个人主页</p><h1>{user.cloud_display_name || user.display_name}</h1><span>{accountLabel} · 加入于 {new Date(user.created_at * 1000).toLocaleDateString('zh-CN')}</span></div>
      <div className="profile-hero-actions"><button className="button ghost" onClick={() => setEditingProfile((value) => !value)}>修改昵称</button><button className="button ghost" onClick={() => { void navigator.clipboard.writeText(user.user_id); setCopied(true); window.setTimeout(() => setCopied(false), 1600) }}>{copied ? '已复制 ID' : '复制用户 ID'}</button><button className="button ghost" onClick={() => void onLogout()}>退出登录</button></div>
    </section>
    {avatarSource && <AvatarCropDialog file={avatarSource} onCancel={() => setAvatarSource(null)} onConfirm={saveAvatar} />}
    {editingProfile && <section className="profile-editor"><label className="field"><span>昵称</span><input maxLength={40} value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label><button className="button ghost" onClick={() => { setDisplayName(user.cloud_display_name || user.display_name); setEditingProfile(false) }}>取消</button><button className="button primary" disabled={profileSaving || !displayName.trim()} onClick={() => void saveDisplayName()}>{profileSaving ? '正在保存…' : '保存昵称'}</button></section>}
    {error && <div className="error-box page-error">{error}</div>}
    <section className="profile-stat-grid">
      <article><small>近 30 天学习</small><strong>{Math.round((summary?.active_seconds ?? 0) / 60)} 分钟</strong><span>保持真实有效的阅读时间</span></article>
      <article><small>连续学习</small><strong>{summary?.current_streak ?? 0} 天</strong><span>最长 {summary?.longest_streak ?? 0} 天</span></article>
      <article><small>已完成翻译</small><strong>{translated} / {books.length} 本</strong><span>{recent ? `最近阅读《${recent.title}》` : '还没有阅读记录'}</span></article>
      <article><small>今日待复习</small><strong>{cards?.due_now ?? 0} 张</strong><button onClick={onOpenReview}>开始复习</button></article>
    </section>
    <section className="profile-content-grid">
      <article className="profile-panel heatmap-panel"><header><div><h2>学习活动</h2><p>最近一年的有效学习时间</p></div><span>当前连续 {summary?.current_streak ?? 0} 天</span></header><div className="heatmap-scroll"><div className="learning-heatmap" role="grid" aria-label="最近一年学习时间">{cells.map((cell) => { const minutes = Math.round((cell.value?.active_seconds ?? 0) / 60); const title = `${cell.key}：${minutes} 分钟；复习 ${cell.value?.cards_reviewed ?? 0} 张`; return <span key={cell.key} className={`heat-cell level-${heatLevel(cell.value?.active_seconds ?? 0)} ${cell.future ? 'future' : ''} ${cell.key === dateKey(new Date()) ? 'today' : ''}`} title={title} /> })}</div></div><div className="heatmap-legend"><span>少</span>{[0,1,2,3,4].map((value) => <i key={value} className={`heat-cell level-${value}`} />)}<span>多</span></div></article>
      <article className="profile-panel trend-panel"><header><div><h2>12 周趋势</h2><p>每周有效学习时间与活动构成</p></div><span>{Math.round((weeklyTrend[weeklyTrend.length - 1]?.total ?? 0) / 60)} 分钟</span></header><div className="weekly-trend" role="img" aria-label="最近十二周学习时间趋势">{weeklyTrend.map((week) => <div key={week.label} title={`${week.label} 起：${Math.round(week.total / 60)} 分钟`}><span className="weekly-bar" style={{ height: `${Math.max(3, week.total / maxWeeklySeconds * 100)}%` }}><i style={{ height: `${week.total ? week.reading / week.total * 100 : 0}%` }} /><b style={{ height: `${week.total ? week.review / week.total * 100 : 0}%` }} /><em style={{ height: `${week.total ? week.cards / week.total * 100 : 0}%` }} /></span><small>{week.label}</small></div>)}</div><div className="trend-legend"><span><i className="reading" />阅读与词典</span><span><i className="review" />复习</span><span><i className="cards" />卡片管理</span></div></article>
      <article className="profile-panel learning-panel"><header><div><h2>词语卡片</h2><p>你的积累与近期安排</p></div><button onClick={onOpenCards}>管理卡片</button></header><dl><div><dt>学习中</dt><dd>{cards?.active ?? 0}</dd></div><div><dt>待确认卡片</dt><dd>{cards?.candidates ?? 0}</dd></div><div><dt>未来 7 天</dt><dd>{cards?.due_7_days ?? 0}</dd></div><div><dt>本期复习</dt><dd>{Math.round(summary?.cards_reviewed ?? 0)}</dd></div></dl></article>
      <article className="profile-panel account-panel"><header><div><h2>资料空间与数据</h2><p>{user.cloud_connected ? '当前资料空间已绑定云端账号' : isOnlineDeployment ? '当前访客空间和云端同步边界' : '当前本机资料空间和云端同步边界'}</p></div><span className="account-type-badge">{accountLabel}</span></header><div className="account-detail-row"><span>显示名称</span><strong>{user.cloud_display_name || user.display_name}</strong></div>{user.cloud_email && <div className="account-detail-row"><span>云端邮箱</span><strong>{user.cloud_email}</strong></div>}<div className="account-detail-row"><span>资料空间 ID</span><code>{user.user_id}</code></div><div className="account-detail-row"><span>当前设备</span><strong>{user.device_id.slice(0, 8)}</strong></div></article>
      <article className="profile-panel sync-panel"><header><div><h2>同步状态</h2><p>目前仍以本机数据为主</p></div><span className={sync?.bindings ? 'sync-ready' : ''}>{sync?.bindings ? '已绑定' : '仅本机'}</span></header><p>知识状态、卡片、书签和阅读进度已建立增量同步边界。书籍正文、插图和 API Key 始终保存在本机。</p><div><span>同步实体 <b>{sync?.entities ?? 0}</b></span><span>待处理冲突 <b>{sync?.conflicts ?? 0}</b></span><span>数据游标 <b>{sync?.cursor ?? 0}</b></span></div></article>
    </section>
    <section className="profile-panel local-security-panel"><header><div><h2>{user.cloud_connected ? '设备会话' : isOnlineDeployment ? '访客会话' : '本机资料空间'}</h2><p>{user.cloud_connected ? '管理这台设备上的冰读会话；云端登录设备请前往“云端与同步”。' : '本机资料空间不使用密码；可管理当前设备上的活动会话。'}</p></div>{sessions.length > 1 && <button onClick={() => void revokeOthers()}>退出其他会话</button>}</header><div className="session-list">{sessions.map((session) => <div key={session.id}><span><strong>{session.label}{session.current ? '（当前）' : ''}</strong><small>最近使用 {new Date(session.last_seen_at * 1000).toLocaleString('zh-CN')} · 设备 {session.device_id.slice(0, 8)}</small></span>{!session.current && <button onClick={() => void revokeSession(session.id)}>退出</button>}</div>)}</div></section>
  </main>
}
