import { useEffect, useState } from 'react'
import { loadAdminCloudUsers, setAdminCloudUserDisabled } from '../../api'
import type { AdminCloudUser } from '../../api'

export default function AdminPage({ onNotice }: { onNotice: (value: string) => void }) {
  const [users, setUsers] = useState<AdminCloudUser[]>([])
  const [total, setTotal] = useState(0)
  const [query, setQuery] = useState('')
  const [offset, setOffset] = useState(0)
  const [busyId, setBusyId] = useState('')
  const [error, setError] = useState('')
  const limit = 50

  async function refresh(nextOffset = offset, nextQuery = query) {
    setError('')
    try {
      const page = await loadAdminCloudUsers(nextQuery, limit, nextOffset)
      setUsers(page.items); setTotal(page.total); setOffset(page.offset)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }
  useEffect(() => { void refresh(0, '') }, [])

  async function toggle(user: AdminCloudUser) {
    setBusyId(user.id); setError('')
    try {
      await setAdminCloudUserDisabled(user.id, !user.disabled)
      await refresh(); onNotice(user.disabled ? '账号已启用' : '账号已禁用，活动会话已撤销')
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusyId('') }
  }

  return <main className="app-page admin-page">
    <header className="page-heading"><div><span>服务器管理</span><h1>账号管理</h1><p>查看云端账号、验证状态和数据规模，并控制账号访问。</p></div></header>
    <section className="page-surface admin-summary"><strong>{total}</strong><span>个云端账号</span></section>
    <section className="page-surface">
      <form className="admin-search" onSubmit={(event) => { event.preventDefault(); void refresh(0, query) }}><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索邮箱或昵称" /><button className="button" type="submit">搜索</button></form>
      {error && <div className="error-box">{error}</div>}
      <div className="admin-user-list">{users.map((user) => <article key={user.id} className={user.disabled ? 'disabled' : ''}>
        <div><strong>{user.display_name}</strong><span>{user.email}</span></div>
        <div><small>{user.role === 'admin' ? '管理员' : '普通账号'} · {user.email_verified ? '邮箱已验证' : '邮箱未验证'}</small><small>{user.active_sessions} 个活动设备 · {user.sync_entities} 条同步数据</small></div>
        <div><small>注册于 {new Date(user.created_at * 1000).toLocaleString('zh-CN')}</small><button className="button small" disabled={busyId === user.id || user.role === 'admin'} onClick={() => void toggle(user)}>{busyId === user.id ? '处理中…' : user.disabled ? '启用' : '禁用'}</button></div>
      </article>)}</div>
      <div className="admin-pagination"><button className="button small" disabled={offset === 0} onClick={() => void refresh(Math.max(0, offset - limit))}>上一页</button><span>{total ? `${offset + 1}–${Math.min(offset + limit, total)} / ${total}` : '暂无账号'}</span><button className="button small" disabled={offset + limit >= total} onClick={() => void refresh(offset + limit)}>下一页</button></div>
    </section>
  </main>
}
