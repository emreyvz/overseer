// Web Speech API wrapper for the AI Operator: browser speech-to-text (voice commands) + spoken
// replies (TTS) + audio-device enumeration. Turkish and English. Everything degrades gracefully
// when the browser lacks support (Electron/Chromium has it; the buttons hide otherwise).
//
// Note on device selection: the Web Speech API always captures from the system default mic and
// SpeechSynthesis always plays to the default output — neither accepts a deviceId. So the device
// picker persists a preference and (for the mic) requests that device via getUserMedia to nudge
// the OS default; true per-app routing needs a local STT engine (a Whisper model, a later phase).
export type Lang = 'tr' | 'en'
const LS = 'overseer.voice'
export type VoicePrefs = { lang: Lang; tts: boolean; micId?: string; outId?: string }
const DEFAULTS: VoicePrefs = { lang: 'tr', tts: true }

export function loadPrefs(): VoicePrefs {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(LS) || '{}') } } catch { return { ...DEFAULTS } }
}
export function savePrefs(p: Partial<VoicePrefs>) {
  try { localStorage.setItem(LS, JSON.stringify({ ...loadPrefs(), ...p })) } catch { /* private mode */ }
}

const SR: any = (globalThis as any).SpeechRecognition || (globalThis as any).webkitSpeechRecognition
export const sttSupported = () => !!SR
export const ttsSupported = () => typeof globalThis.speechSynthesis !== 'undefined'
const BCP = (l: Lang) => (l === 'tr' ? 'tr-TR' : 'en-US')

export type STTHandlers = {
  interim?: (t: string) => void
  final?: (t: string) => void
  end?: () => void
  error?: (e: string) => void
}

let rec: any = null
export function startSTT(lang: Lang, h: STTHandlers): boolean {
  if (!SR) { h.error?.('unsupported'); return false }
  stopSTT()
  rec = new SR()
  rec.lang = BCP(lang)
  rec.interimResults = true
  rec.continuous = false
  rec.maxAlternatives = 1
  let finalText = ''
  rec.onresult = (e: any) => {
    let interim = ''
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i]
      if (r.isFinal) finalText += r[0].transcript
      else interim += r[0].transcript
    }
    if (interim) h.interim?.(interim)
    if (finalText) h.final?.(finalText.trim())
  }
  rec.onerror = (e: any) => h.error?.(String(e?.error || 'error'))
  rec.onend = () => { const r = rec; rec = null; if (r) h.end?.() }
  try { rec.start(); return true } catch { rec = null; h.error?.('start-failed'); return false }
}
export function stopSTT() { if (rec) { try { rec.stop() } catch { /* already stopped */ } rec = null } }
export const isListening = () => !!rec

let voices: SpeechSynthesisVoice[] = []
function ensureVoices() { if (ttsSupported() && !voices.length) voices = speechSynthesis.getVoices() }
if (ttsSupported()) { try { speechSynthesis.onvoiceschanged = () => { voices = speechSynthesis.getVoices() } } catch { /* noop */ } }

export function speak(text: string, lang: Lang) {
  if (!ttsSupported() || !text) return
  ensureVoices()
  const u = new SpeechSynthesisUtterance(text)
  u.lang = BCP(lang)
  const v = voices.find((x) => x.lang?.toLowerCase().startsWith(lang === 'tr' ? 'tr' : 'en'))
  if (v) u.voice = v
  u.rate = 1.03
  try { speechSynthesis.cancel(); speechSynthesis.speak(u) } catch { /* noop */ }
}
export function stopSpeaking() { if (ttsSupported()) { try { speechSynthesis.cancel() } catch { /* noop */ } } }

export type AudioDev = { id: string; label: string }
export async function listAudioDevices(): Promise<{ inputs: AudioDev[]; outputs: AudioDev[] }> {
  if (!navigator.mediaDevices?.enumerateDevices) return { inputs: [], outputs: [] }
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true })   // permission → labels
    s.getTracks().forEach((t) => t.stop())
  } catch { /* denied: labels may be blank, ids still enumerate */ }
  try {
    const devs = await navigator.mediaDevices.enumerateDevices()
    const pick = (kind: string) => devs.filter((d) => d.kind === kind)
      .map((d) => ({ id: d.deviceId, label: d.label || `${kind.replace('audio', '')} ${d.deviceId.slice(0, 5)}` }))
    return { inputs: pick('audioinput'), outputs: pick('audiooutput') }
  } catch { return { inputs: [], outputs: [] } }
}
