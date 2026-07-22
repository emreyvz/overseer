// OVERSEER — system actions (shutdown). Electron-first, browser fallback.
import { api } from './api'

/** Quit the app: Electron IPC if available, else stop the server + close the tab. */
export async function quitApp(): Promise<void> {
  const w = window as unknown as { overseer?: { quit?: () => void } }
  if (w.overseer?.quit) { w.overseer.quit(); return }
  try { await api.shutdown() } catch { /* ignore */ }
  try { window.close() } catch { /* ignore */ }
}
