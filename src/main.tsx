import { initializeAndroid } from './mobile/runtime'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import AppErrorBoundary from './app/AppErrorBoundary'
import './styles.css'
import './mobile/tablet.css'

async function start() {
await initializeAndroid()
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AppErrorBoundary><App /></AppErrorBoundary>
  </StrictMode>,
)


}
void start().catch((error) => { document.body.textContent = `冰读启动失败：${String(error)}` })
