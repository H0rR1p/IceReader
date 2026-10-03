const { contextBridge, ipcRenderer } = require('electron')
contextBridge.exposeInMainWorld('bingduDesktop', Object.freeze({
  info: () => ipcRenderer.invoke('desktop:info'),
  openDataDirectory: () => ipcRenderer.invoke('desktop:open-data'),
  startOidc: provider => ipcRenderer.invoke('desktop:oidc', provider),
}))
