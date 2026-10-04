import { writeFile } from 'node:fs/promises'

export async function connect(port = 9222) {
  const pages = await (await fetch(`http://127.0.0.1:${port}/json`)).json()
  const page = pages.find(p => p.url.startsWith('https://localhost')) ?? pages[0]
  const socket = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject })
  let id = 0
  const pending = new Map()
  socket.onmessage = event => {
    const message = JSON.parse(event.data)
    if (message.id && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id); pending.delete(message.id)
      message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result)
    }
  }
  const call = (method, params) => new Promise((resolve, reject) => {
    const current = ++id; pending.set(current, { resolve, reject }); socket.send(JSON.stringify({ id: current, method, params }))
  })
  return {
    async evaluate(expression) {
      const result = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
      if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails))
      return result.result.value
    },
    async screenshot(path) {
      const { data } = await call('Page.captureScreenshot', { format: 'png' }); await writeFile(path, Buffer.from(data, 'base64'))
    },
    close() { socket.close() },
  }
}

if (process.argv[2]) {
  const page = await connect()
  try { console.log(JSON.stringify(await page.evaluate(process.argv[2]), null, 2)) }
  finally { page.close() }
}
