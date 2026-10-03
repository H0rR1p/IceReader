const { app, BrowserWindow, protocol, session, ipcMain, dialog, Menu, shell, safeStorage } = require('electron')
const { spawn } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const http = require('node:http')
const { Readable } = require('node:stream')

app.setName('IceReader')
if (process.env.BINGDU_DESKTOP_USER_DATA) app.setPath('userData', process.env.BINGDU_DESKTOP_USER_DATA)
protocol.registerSchemesAsPrivileged([{ scheme: 'bingdu', privileges: {
  standard: true, secure: true, supportFetchAPI: true, stream: true, corsEnabled: true,
} }])
const root = path.resolve(__dirname, '..')
const secret = randomBytes(32).toString('hex')
let child, window, port, stopping = false, cookies = {}
let oauthServer
const cookieFile = () => path.join(app.getPath('userData'), 'session.enc')
const dataDirectory = () => process.env.BINGDU_DATA_DIR || path.join(app.getPath('userData'), 'data')

function saveCookies(values) {
  if (!values?.length) return
  for (const value of values || []) {
    const [pair] = value.split(';')
    const index = pair.indexOf('=')
    const key = pair.slice(0, index)
    if (['bingdu_session', 'bingdu_device'].includes(key)) cookies[key] = pair.slice(index + 1)
  }
  if (!safeStorage.isEncryptionAvailable()) return
  const file = cookieFile()
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file + '.tmp', safeStorage.encryptString(JSON.stringify(cookies)))
  fs.renameSync(file + '.tmp', file)
}

function requestBackend(urlPath, { method = 'GET', headers = {}, body, signal } = {}) {
  return new Promise((resolve, reject) => {
    const request = http.request({ hostname: '127.0.0.1', port, path: urlPath, method,
      headers: { ...headers, 'x-bingdu-desktop-secret': secret,
        cookie: Object.entries(cookies).map(([key, value]) => `${key}=${value}`).join('; ') },
    }, response => {
      saveCookies(response.headers['set-cookie'])
      resolve(response)
    })
    request.on('error', reject)
    if (signal) {
      const cancel = () => request.destroy(new Error('请求已取消'))
      signal.addEventListener('abort', cancel, { once: true })
      request.once('close', () => signal.removeEventListener('abort', cancel))
      if (signal.aborted) cancel()
    }
    request.setTimeout(120000, () => request.destroy(new Error('内部服务请求超时')))
    if (body && !Buffer.isBuffer(body)) {
      const stream = Readable.fromWeb(body)
      stream.on('error', error => request.destroy(error))
      stream.pipe(request)
    } else { if (body) request.write(body); request.end() }
  })
}

async function startBackend() {
  const legacyCandidates = process.env.BINGDU_LEGACY_DATA_DIR !== undefined ? [process.env.BINGDU_LEGACY_DATA_DIR] : [
    path.join(path.dirname(app.getPath('exe')), 'data'),
    !app.isPackaged && path.join(root, 'data'),
    !app.isPackaged && path.join(root, 'build', 'release', '冰读', 'data')].filter(Boolean)
  const legacy = legacyCandidates.find(value => fs.existsSync(path.join(value, 'library.sqlite3')))
  const executable = app.isPackaged ? path.join(process.resourcesPath, 'backend', 'bingdu-service.exe')
    : process.env.BINGDU_PYTHON || path.join(root, '.venv64', 'Scripts', 'python.exe')
  const args = app.isPackaged ? [] : [path.join(root, 'backend', 'desktop_launcher.py')]
  fs.mkdirSync(dataDirectory(), { recursive: true })
  const log = fs.createWriteStream(path.join(dataDirectory(), 'desktop-service.log'), { flags: 'a' })
  child = spawn(executable, args, { windowsHide: true, cwd: app.isPackaged ? process.resourcesPath : root,
    env: { ...process.env, BINGDU_DATA_DIR: dataDirectory(), BINGDU_DESKTOP_SECRET: secret,
      BINGDU_LEGACY_DATA_DIR: legacy || '', BINGDU_PUBLIC_ORIGIN: '', PYTHONUTF8: '1', PYTHONUNBUFFERED: '1' },
    stdio: ['pipe', 'pipe', 'pipe'],
  })
  child.stderr.pipe(log)
  child.once('exit', code => {
    log.end()
    if (!stopping && window) {
      dialog.showErrorBox('冰读内部服务已退出', `退出代码 ${code}。请重新启动冰读。日志：${dataDirectory()}`)
      app.quit()
    }
  })
  await new Promise((resolve, reject) => {
    let output = ''
    const timeout = setTimeout(() => reject(new Error('内部服务启动超过 120 秒，请查看数据目录中的日志')), 120000)
    const onData = chunk => {
      output += chunk.toString()
      for (const line of output.split('\n')) {
        try { const result = JSON.parse(line); if (result.port) {
          port = result.port; clearTimeout(timeout); child.stdout.off('data', onData); resolve()
        } } catch {}
      }
    }
    child.stdout.on('data', onData)
    child.once('error', error => { clearTimeout(timeout); reject(error) })
    child.once('exit', code => { clearTimeout(timeout); reject(new Error(`内部服务启动失败 (${code})`)) })
  })
  // Wait for lifespan initialization, not just an allocated port.
  const deadline = Date.now() + 120000
  while (Date.now() < deadline) {
    try {
      const response = await requestBackend('/api/health')
      let content = ''; for await (const chunk of response) content += chunk
      if (JSON.parse(content).app === 'bingdu') return
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  throw new Error('内部服务健康检查失败')
}

async function proxy(request) {
  const url = new URL(request.url)
  if (url.host !== 'app') return new Response('Forbidden', { status: 403 })
  const headers = {}
  for (const name of ['content-type', 'content-length', 'accept', 'range', 'if-none-match']) {
    if (request.headers.has(name)) headers[name] = request.headers.get(name)
  }
  const body = ['GET', 'HEAD'].includes(request.method) ? undefined : request.body
  try {
    const response = await requestBackend(url.pathname + url.search, { method: request.method, headers, body, signal: request.signal })
    const responseHeaders = new Headers()
    for (const [key, value] of Object.entries(response.headers)) {
      if (value && !['set-cookie', 'transfer-encoding', 'connection'].includes(key)) responseHeaders.set(key, String(value))
    }
    const empty = request.method === 'HEAD' || [204, 304].includes(response.statusCode)
    if (empty) response.resume()
    return new Response(empty ? null : Readable.toWeb(response), { status: response.statusCode, headers: responseHeaders })
  } catch (error) { return new Response(JSON.stringify({ detail: '客户端内部服务暂不可用' }), { status: 503, headers: { 'content-type': 'application/json' } }) }
}

function trusted(event) {
  if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame ||
    !event.senderFrame.url.startsWith('bingdu://app/')) throw new Error('Invalid IPC sender')
}

function externalUrl(value) {
  const url = new URL(value)
  if (url.username || url.password) throw new Error('不支持带凭据的链接')
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname))) throw new Error('不支持的外部链接')
  return url.href
}

async function startOidc(provider) {
  if (typeof provider !== 'string' || !/^[a-zA-Z0-9_-]{1,100}$/.test(provider)) throw new Error('无效的登录提供商')
  oauthServer?.close()
  const callbackPath = '/oauth/' + randomBytes(24).toString('hex')
  oauthServer = http.createServer(async (request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1')
    if (request.method !== 'GET' || url.pathname !== callbackPath) { response.writeHead(404); response.end(); return }
    const code = url.searchParams.get('code')
    if (!code) { response.writeHead(400); response.end('Login was not completed.'); return }
    try {
      const result = await requestBackend('/api/cloud/oidc/complete?code=' + encodeURIComponent(code))
      result.resume()
      const target = result.headers.location || ''
      response.writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' })
      response.end('登录已完成，可以关闭此页并返回冰读。')
      window?.loadURL('bingdu://app/' + (target.includes('cloud_error=') ? '#/profile?' + target.split('?')[1] : '#/profile?cloud=connected'))
      oauthServer.close()
    } catch { response.writeHead(502); response.end('Login failed. Please retry in IceReader.'); }
  })
  await new Promise((resolve, reject) => { oauthServer.once('error', reject); oauthServer.listen(0, '127.0.0.1', resolve) })
  const callback = `http://127.0.0.1:${oauthServer.address().port}${callbackPath}`
  const result = await requestBackend(`/api/cloud/oidc/start/${provider}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: Buffer.from(JSON.stringify({ callback_url: callback })) })
  let body = ''; for await (const chunk of result) body += chunk
  const payload = JSON.parse(body)
  if (result.statusCode >= 400) { oauthServer.close(); throw new Error(payload.detail || '无法启动第三方登录') }
  await shell.openExternal(externalUrl(payload.url))
  const listener = oauthServer
  const timer = setTimeout(() => listener.close(), 10 * 60 * 1000)
  listener.once('close', () => clearTimeout(timer))
}

function createWindow() {
  window = new BrowserWindow({ width: 1440, height: 940, minWidth: 900, minHeight: 640,
    backgroundColor: '#f3faff', title: '冰读', show: process.env.BINGDU_DESKTOP_TEST !== '1',
    icon: app.isPackaged ? path.join(process.resourcesPath, 'bingdu.ico') : path.join(root, 'assets', 'bingdu.ico'),
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false,
      contextIsolation: true, sandbox: true, webSecurity: true, partition: 'persist:bingdu' },
  })
  window.webContents.setWindowOpenHandler(({ url }) => {
    try { void shell.openExternal(externalUrl(url)) } catch {}
    return { action: 'deny' }
  })
  window.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith('bingdu://app/')) event.preventDefault()
  })
  window.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false))
  window.webContents.session.on('will-download', (_event, item) => {
    // Electron supplies the native save dialog; never writes into the installation folder.
    if (process.env.BINGDU_DESKTOP_TEST === '1') item.setSavePath(path.join(app.getPath('userData'), 'test-download.zip'))
    else item.setSaveDialogOptions({ title: '保存冰读文件', defaultPath: path.join(app.getPath('downloads'), item.getFilename()) })
    item.once('done', (_event, state) => {
      if (state === 'interrupted') dialog.showErrorBox('下载未完成', '请重新下载文件。')
    })
  })
  window.webContents.on('render-process-gone', () => {
    dialog.showErrorBox('冰读界面意外退出', '请重启冰读，已保存的数据仍在数据目录中。')
    app.quit()
  })
  window.loadFile(path.join(__dirname, 'loading.html'))
}

if (!app.requestSingleInstanceLock()) app.quit()
else {
  app.on('second-instance', () => { if (window) { if (window.isMinimized()) window.restore(); window.show(); window.focus() } })
  app.whenReady().then(async () => {
    try {
      createWindow()
      if (fs.existsSync(cookieFile()) && safeStorage.isEncryptionAvailable()) {
        try { cookies = JSON.parse(safeStorage.decryptString(fs.readFileSync(cookieFile()))) } catch {}
      }
      await startBackend()
      if (process.env.BINGDU_DESKTOP_TEST === '1') app.__bingduTest = { port, backendPid: child.pid }
      session.fromPartition('persist:bingdu').protocol.handle('bingdu', proxy)
      ipcMain.handle('desktop:info', event => { trusted(event); return { version: app.getVersion(), dataDirectory: dataDirectory(), desktop: true } })
      ipcMain.handle('desktop:open-data', event => { trusted(event); return shell.openPath(dataDirectory()) })
      ipcMain.handle('desktop:oidc', (event, provider) => { trusted(event); return startOidc(provider) })
      Menu.setApplicationMenu(Menu.buildFromTemplate([
        { label: '冰读', submenu: [{ label: '打开数据目录', click: () => shell.openPath(dataDirectory()) }, { type: 'separator' }, { role: 'quit', label: '退出' }] },
        { label: '编辑', submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' }, { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] },
        { label: '视图', submenu: [{ role: 'reload', label: '重新载入' }, { role: 'resetZoom', label: '实际大小' }, { role: 'zoomIn', label: '放大' }, { role: 'zoomOut', label: '缩小' }, { role: 'togglefullscreen', label: '全屏' }] },
      ]))
      await window.loadURL('bingdu://app/')
    } catch (error) { dialog.showErrorBox('冰读无法启动', `${error.message}\n日志目录：${dataDirectory()}`); app.quit() }
  })
  app.on('window-all-closed', () => app.quit())
  app.on('before-quit', event => {
    if (stopping || !child || child.exitCode !== null) return
    event.preventDefault(); stopping = true
    oauthServer?.close()
    child.stdin.end('shutdown\n')
    const timer = setTimeout(() => { child.kill(); app.quit() }, 8000)
    child.once('exit', () => { clearTimeout(timer); app.quit() })
  })
}
