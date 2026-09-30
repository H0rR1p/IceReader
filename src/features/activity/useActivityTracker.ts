import { useEffect, useRef } from 'react'
import { sendActivityHeartbeat } from '../../api'
import type { ActivityType } from '../../api'


function localDate(timestamp: number) {
  const value = new Date(timestamp)
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function useActivityTracker(userId: string | undefined, activityType: ActivityType | null) {
  const lastInteractionRef = useRef(Date.now())
  const lastTickRef = useRef(Date.now())
  const sessionRef = useRef(crypto.randomUUID())

  useEffect(() => {
    const markActive = () => { lastInteractionRef.current = Date.now() }
    const events: Array<keyof WindowEventMap> = ['pointerdown', 'keydown', 'scroll', 'wheel']
    events.forEach((event) => window.addEventListener(event, markActive, { passive: true }))
    return () => events.forEach((event) => window.removeEventListener(event, markActive))
  }, [])

  useEffect(() => {
    lastTickRef.current = Date.now()
    const timer = window.setInterval(() => {
      const now = Date.now()
      const start = Math.max(lastTickRef.current, now - 15_000)
      lastTickRef.current = now
      if (!userId || !activityType || document.visibilityState !== 'visible' || !document.hasFocus()
        || now - lastInteractionRef.current > 60_000) return
      const payload = {
        id: crypto.randomUUID(), session_id: sessionRef.current, activity_type: activityType,
        window_start: start / 1000, window_end: now / 1000, local_date: localDate(now),
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'local',
      }
      void sendActivityHeartbeat(payload).catch(() => {
        const key = `bingdu-activity-pending-${userId}`
        const pending = JSON.parse(localStorage.getItem(key) || '[]') as Array<Record<string, unknown>>
        localStorage.setItem(key, JSON.stringify([...pending.slice(-39), payload]))
      })
    }, 15_000)
    return () => window.clearInterval(timer)
  }, [activityType, userId])

  useEffect(() => {
    if (!userId) return
    const key = `bingdu-activity-pending-${userId}`
    const pending = JSON.parse(localStorage.getItem(key) || '[]') as Array<Record<string, unknown>>
    if (!pending.length) return
    void Promise.allSettled(pending.map(sendActivityHeartbeat)).then((results) => {
      const failed = pending.filter((_value, index) => results[index].status === 'rejected')
      if (failed.length) localStorage.setItem(key, JSON.stringify(failed))
      else localStorage.removeItem(key)
    })
  }, [userId])
}
