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
const DEFAULTS: VoicePrefs = { lang: 'en', tts: true }

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

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://127.0.0.1:8787'
let _backendTts: boolean | null = null   // null=unknown, true=works, false=use browser
let _audio: HTMLAudioElement | null = null

// Speak a reply. Prefer backend TTS (offline OS voice — reliable even when the browser has no
// voices, common in Electron); fall back to the browser voice. Caches which one works.
export function speak(text: string, lang: Lang) {
  if (!text) return
  if (_backendTts === false) { browserSpeak(text, lang); return }
  backendSpeak(text, lang).then((ok) => {
    _backendTts = ok
    if (!ok) browserSpeak(text, lang)
  })
}

async function backendSpeak(text: string, lang: Lang): Promise<boolean> {
  try {
    const url = `${API_BASE}/api/tts?text=${encodeURIComponent(text.slice(0, 400))}&lang=${lang}`
    stopSpeaking()
    const audio = new Audio(url)
    _audio = audio
    const prefs = loadPrefs()
    if (prefs.outId && 'setSinkId' in audio) { try { await (audio as unknown as { setSinkId(id: string): Promise<void> }).setSinkId(prefs.outId) } catch { /* not supported */ } }
    return await new Promise<boolean>((resolve) => {
      let done = false
      const finish = (v: boolean) => { if (!done) { done = true; resolve(v) } }
      audio.oncanplaythrough = () => audio.play().then(() => finish(true)).catch(() => finish(false))
      audio.onerror = () => finish(false)           // e.g. backend returned {disabled} JSON, not audio
      setTimeout(() => finish(false), 5000)
    })
  } catch { return false }
}

function browserSpeak(text: string, lang: Lang) {
  if (!ttsSupported() || !text) return
  const utter = () => {
    const u = new SpeechSynthesisUtterance(text)
    u.lang = BCP(lang)
    const vs = speechSynthesis.getVoices()
    const v = vs.find((x) => x.lang?.toLowerCase().startsWith(lang === 'tr' ? 'tr' : 'en')) ||
              vs.find((x) => x.default) || vs[0]
    if (v) u.voice = v
    u.rate = 1.03; u.volume = 1; u.pitch = 1
    try { speechSynthesis.cancel(); speechSynthesis.resume(); speechSynthesis.speak(u) } catch { /* noop */ }
  }
  // Voices load asynchronously in Chromium/Electron; the first speak() can fire before they exist,
  // which silently does nothing — so wait for them if needed.
  if (speechSynthesis.getVoices().length) { utter(); return }
  const on = () => { speechSynthesis.removeEventListener('voiceschanged', on); utter() }
  try { speechSynthesis.addEventListener('voiceschanged', on) } catch { /* noop */ }
  speechSynthesis.getVoices()
  setTimeout(() => { if (speechSynthesis.getVoices().length) { try { speechSynthesis.removeEventListener('voiceschanged', on) } catch { /* noop */ } utter() } }, 300)
}
export function stopSpeaking() {
  if (ttsSupported()) { try { speechSynthesis.cancel() } catch { /* noop */ } }
  if (_audio) { try { _audio.pause() } catch { /* noop */ } _audio = null }
}

// ---- microphone capture for offline STT: record the mic, then POST the audio to the backend
// (faster-whisper). This replaces the browser's Web Speech API, which fails in Electron and is
// cloud-based. Click to start, click to stop; the blob goes to /api/stt.
let recorder: MediaRecorder | null = null
let chunks: Blob[] = []
export const recordingSupported = () =>
  typeof MediaRecorder !== 'undefined' && !!navigator.mediaDevices?.getUserMedia

export async function startRecording(micId?: string): Promise<boolean> {
  if (!recordingSupported()) return false
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: micId ? { deviceId: { exact: micId } } : true,
    })
    recorder = new MediaRecorder(stream)
    chunks = []
    recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data) }
    recorder.start()
    return true
  } catch { return false }
}

export function stopRecording(): Promise<Blob | null> {
  return new Promise((resolve) => {
    const r = recorder
    if (!r) { resolve(null); return }
    r.onstop = () => {
      r.stream.getTracks().forEach((t) => t.stop())
      resolve(chunks.length ? new Blob(chunks, { type: r.mimeType || 'audio/webm' }) : null)
      recorder = null
    }
    try { r.stop() } catch { resolve(null); recorder = null }
  })
}

// Decode the recorded blob and re-encode as 16 kHz mono 16-bit WAV, so the backend can transcribe
// it without any ffmpeg/av dependency (the fragile part of offline STT). Falls back to the raw
// blob if the Web Audio API can't decode it.
export async function toWav16k(blob: Blob): Promise<Blob> {
  try {
    const AC = (globalThis as any).AudioContext || (globalThis as any).webkitAudioContext
    if (!AC) return blob
    const ctx = new AC()
    const decoded: AudioBuffer = await ctx.decodeAudioData(await blob.arrayBuffer())
    const chans = decoded.numberOfChannels, len = decoded.length
    const mono = new Float32Array(len)
    for (let c = 0; c < chans; c++) { const d = decoded.getChannelData(c); for (let i = 0; i < len; i++) mono[i] += d[i] / chans }
    try { await ctx.close() } catch { /* noop */ }
    const outRate = 16000, ratio = decoded.sampleRate / outRate
    const outLen = Math.max(1, Math.floor(len / ratio))
    const out = new Float32Array(outLen)
    for (let i = 0; i < outLen; i++) {
      const idx = i * ratio, i0 = Math.floor(idx), frac = idx - i0
      out[i] = (mono[i0] || 0) * (1 - frac) + (mono[i0 + 1] || 0) * frac
    }
    return encodeWav16(out, outRate)
  } catch { return blob }
}

function encodeWav16(samples: Float32Array, rate: number): Blob {
  const buf = new ArrayBuffer(44 + samples.length * 2)
  const dv = new DataView(buf)
  const w = (o: number, s: string) => { for (let i = 0; i < s.length; i++) dv.setUint8(o + i, s.charCodeAt(i)) }
  w(0, 'RIFF'); dv.setUint32(4, 36 + samples.length * 2, true); w(8, 'WAVE'); w(12, 'fmt ')
  dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true)
  dv.setUint32(24, rate, true); dv.setUint32(28, rate * 2, true); dv.setUint16(32, 2, true); dv.setUint16(34, 16, true)
  w(36, 'data'); dv.setUint32(40, samples.length * 2, true)
  let o = 44
  for (let i = 0; i < samples.length; i++) { const s = Math.max(-1, Math.min(1, samples[i])); dv.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true); o += 2 }
  return new Blob([buf], { type: 'audio/wav' })
}

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
