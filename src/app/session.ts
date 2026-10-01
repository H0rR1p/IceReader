import { parseResponse } from '../api'
import type { CurrentUser } from '../types'


let currentUserPromise: Promise<CurrentUser> | null = null


export function loadCurrentUser(force = false): Promise<CurrentUser> {
  if (force || !currentUserPromise) {
    currentUserPromise = fetch('/api/me')
      .then((response) => parseResponse<CurrentUser>(response))
      .catch((error) => {
        currentUserPromise = null
        throw error
      })
  }
  return currentUserPromise
}


export function resetCurrentUserSession(): void {
  currentUserPromise = null
}


async function changeSession(path: string, payload?: Record<string, string>): Promise<CurrentUser> {
  const response = await fetch(path, {
    method: 'POST',
    headers: payload ? { 'Content-Type': 'application/json' } : undefined,
    body: payload ? JSON.stringify(payload) : undefined,
  })
  await parseResponse(response)
  resetCurrentUserSession()
  return loadCurrentUser(true)
}


export function registerLocalAccount(displayName: string, username: string, password: string) {
  return changeSession('/api/auth/local/register', {
    display_name: displayName, username, password,
  })
}


export function loginLocalAccount(username: string, password: string) {
  return changeSession('/api/auth/local/login', { username, password })
}


export function logoutCurrentAccount() {
  return changeSession('/api/auth/logout')
}


export async function updateCurrentUserProfile(displayName: string): Promise<CurrentUser> {
  const response = await fetch('/api/me', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ display_name: displayName }),
  })
  await parseResponse(response)
  resetCurrentUserSession()
  return loadCurrentUser(true)
}


export async function uploadCurrentUserAvatar(file: File): Promise<CurrentUser> {
  const form = new FormData()
  form.append('file', file)
  const response = await fetch('/api/me/avatar', { method: 'POST', body: form })
  await parseResponse(response)
  resetCurrentUserSession()
  return loadCurrentUser(true)
}


export type LocalSession = {
  id: string; device_id: string; label: string; auth_provider: string
  created_at: number; last_seen_at: number; expires_at: number; current: boolean
}

export async function loadLocalSessions(signal?: AbortSignal): Promise<LocalSession[]> {
  return parseResponse(await fetch('/api/me/sessions', { signal }))
}

export async function revokeLocalSession(sessionId: string): Promise<void> {
  await parseResponse(await fetch(`/api/me/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' }))
}

export async function revokeOtherLocalSessions(): Promise<number> {
  const result = await parseResponse<{ revoked: number }>(await fetch('/api/me/sessions', { method: 'DELETE' }))
  return result.revoked
}

export async function changeLocalPassword(currentPassword: string, newPassword: string): Promise<void> {
  await parseResponse(await fetch('/api/me/password', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  }))
}
