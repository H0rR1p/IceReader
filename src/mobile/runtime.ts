import { Capacitor, registerPlugin } from '@capacitor/core'
import { App } from '@capacitor/app'
import { Filesystem, Directory } from '@capacitor/filesystem'
import type { PluginListenerHandle } from '@capacitor/core'

type NativeResponse = { status: number; headers: Record<string, string>; body: string }
const Reader = registerPlugin<{
  request(value: { method: string; path: string; headers: string; body: string; id: string }): Promise<NativeResponse>
  cancel(value: { id: string }): Promise<void>
  uploadStart(): Promise<{ id: string }>
  uploadChunk(value: { id: string; body: string }): Promise<void>
  uploadComplete(value: { id: string; path: string; name: string; mime: string; fields: string }): Promise<NativeResponse>
  uploadCancel(value: { id: string }): Promise<void>
  exportFile(value: { path: string; method: string; body: string }): Promise<{ uri: string; filename: string }>
  saveFile(value: { uri: string; filename: string; mime?: string }): Promise<{ cancelled?: boolean }>
  startOidc(value: { provider: string }): Promise<void>
  setKeepAwake(value: { enabled: boolean }): Promise<void>
  syncPreferences(value?: { enabled: boolean }): Promise<{ enabled: boolean }>
  addListener(name: string, callback: (event: { message?: string }) => void): Promise<PluginListenerHandle>
}>('Reader')
const encode = (bytes: Uint8Array) => {
  let text = ''
  for (let start = 0; start < bytes.length; start += 16384) text += String.fromCharCode(...bytes.subarray(start, start + 16384))
  return btoa(text)
}
export async function initializeAndroid() {
  if (!Capacitor.isNativePlatform()) return
  await Reader.addListener('accountChanged', () => location.reload())
  await Reader.addListener('oauthError', (event) => window.alert(event.message || '云端登录未完成，请重试。'))
  const originalFetch = window.fetch.bind(window)
  const originalAnchorClick = HTMLAnchorElement.prototype.click
  HTMLAnchorElement.prototype.click = function () {
    if (this.download && this.href.startsWith('blob:')) {
      const filename = this.download
      void originalFetch(this.href).then(response => response.blob()).then(blob => saveAndroidDownload(blob, filename))
        .catch(error => window.alert(error instanceof Error ? error.message : String(error)))
    } else originalAnchorClick.call(this)
  }
  window.fetch = async (input, init) => {
    const request = input instanceof Request ? new Request(input, init) : new Request(new URL(input instanceof URL ? input.href : input, location.href), init)
    const url = new URL(request.url)
    if (url.origin !== location.origin || !url.pathname.startsWith('/api/')) return originalFetch(input, init)
    if (request.signal.aborted) throw new DOMException('请求已取消', 'AbortError')
    const id = crypto.randomUUID()
    const abort = () => { void Reader.cancel({ id }) }
    request.signal.addEventListener('abort', abort, { once: true })
    let response: NativeResponse
    try {
      if (init?.body instanceof FormData && init.body.get('file') instanceof File) {
        const file = init.body.get('file') as File
        const upload = await Reader.uploadStart()
        try {
          for (let offset = 0; offset < file.size; offset += 524288) {
            if (request.signal.aborted) throw new DOMException('请求已取消', 'AbortError')
            await Reader.uploadChunk({ id: upload.id, body: encode(new Uint8Array(await file.slice(offset, offset + 524288).arrayBuffer())) })
          }
          const fields = Object.fromEntries([...init.body.entries()].filter(([, value]) => typeof value === 'string'))
          response = await Reader.uploadComplete({ id: upload.id, path: url.pathname + url.search, name: file.name, mime: file.type || 'application/octet-stream', fields: JSON.stringify(fields) })
        } finally { await Reader.uploadCancel({ id: upload.id }) }
      } else {
        response = await Reader.request({ id, method: request.method, path: url.pathname + url.search,
          headers: JSON.stringify(Object.fromEntries(request.headers.entries())), body: encode(new Uint8Array(await request.arrayBuffer())) })
      }
    } catch (error) {
      if (request.signal.aborted) throw new DOMException('请求已取消', 'AbortError')
      throw error
    } finally { request.signal.removeEventListener('abort', abort) }
    if (request.signal.aborted) throw new DOMException('请求已取消', 'AbortError')
    const body = Uint8Array.from(atob(response.body), (char) => char.charCodeAt(0))
    return new Response(response.status === 204 ? null : body, { status: response.status, headers: response.headers })
  }
  await App.addListener('backButton', ({ canGoBack }) => {
    const close = document.querySelector<HTMLButtonElement>('.modal-head button, .dialog header button, .modal-actions .ghost')
    if (close) close.click()
    else if (document.querySelector('.reader-topbar')) document.querySelector<HTMLButtonElement>('.reader-topbar .brand')?.click()
    else if (canGoBack) history.back()
    else if (location.hash !== '#/library') location.hash = '#/library'
    else void App.minimizeApp()
  })
}
export const startAndroidOidc = (provider: string) => Reader.startOidc({ provider })
export const setAndroidKeepAwake = (enabled: boolean) => Reader.setKeepAwake({ enabled })
export const androidSyncPreferences = (enabled?: boolean) => Reader.syncPreferences(enabled === undefined ? undefined : { enabled })
export async function exportAndroidPackage(path: string, bookIds?: string[]) {
  const result = await Reader.exportFile({ path, method: bookIds ? 'POST' : 'GET', body: bookIds ? JSON.stringify({ book_ids: bookIds }) : '' })
  const saved = await Reader.saveFile({ uri: result.uri, filename: result.filename })
  if (saved?.cancelled) throw new DOMException('已取消保存', 'AbortError')
}
export async function saveAndroidDownload(blob: Blob, filename: string) {
  const result = await Filesystem.writeFile({ path: `exports/${filename}`, directory: Directory.Cache,
    data: encode(new Uint8Array(await blob.arrayBuffer())), recursive: true })
  const saved = await Reader.saveFile({ uri: result.uri, filename, mime: blob.type || 'application/octet-stream' })
  if (saved?.cancelled) throw new DOMException('已取消保存', 'AbortError')
}
