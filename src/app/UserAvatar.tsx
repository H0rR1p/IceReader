import type { CurrentUser } from '../types'

export default function UserAvatar({ user, className = '' }: { user: CurrentUser; className?: string }) {
  const fallback = user.display_name.trim().slice(0, 1).toUpperCase() || '冰'
  return <span className={`user-avatar ${className}`.trim()} aria-hidden="true">
    {user.avatar_url ? <img src={user.avatar_url} alt="" /> : fallback}
  </span>
}
