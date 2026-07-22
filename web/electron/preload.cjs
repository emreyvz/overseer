// OVERSEER — preload bridge: expose a minimal, safe API to the page.
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('overseer', {
  quit: () => ipcRenderer.send('app-quit'),
})
