// Single source of truth for LLM availability. Every AI affordance in the UI gates on
// this store: if the provider is unreachable/unconfigured, or a feature is switched off,
// the affordance simply isn't shown — the rest of the app is unaffected. Refreshed at
// startup and whenever settings are saved.
import { writable } from 'svelte/store'
import { api, type AiStatus, type AiFeatureKey } from './api'

export const aiStatus = writable<AiStatus>({ enabled: false, features: {} })

export async function refreshAiStatus(): Promise<AiStatus> {
  try {
    const s = await api.aiStatus()
    aiStatus.set(s)
    return s
  } catch {
    const off: AiStatus = { enabled: false, features: {} }
    aiStatus.set(off)
    return off
  }
}

// True only when the provider is usable AND this capability is switched on. A missing
// flag counts as on (features default enabled), a disabled provider counts as off.
export function aiOn(s: AiStatus, feature: AiFeatureKey): boolean {
  return !!s.enabled && s.features?.[feature] !== false
}

// Feature catalogue for the settings panel — order shown top-to-bottom.
export const AI_FEATURES: { key: AiFeatureKey; label: string; desc: string }[] = [
  { key: 'operate', label: 'AI OPERATOR', desc: 'Drives the whole system from a spoken or typed command' },
  { key: 'search', label: 'NATURAL-LANGUAGE SEARCH', desc: 'Turns a sentence into a forensic filter' },
  { key: 'chat', label: 'ASSISTANT CHAT', desc: 'Free-form question and answer' },
  { key: 'summarize', label: 'SUMMARY / BRIEFING', desc: 'Summarizes events in plain language' },
  { key: 'explain', label: 'ALERT EXPLANATION', desc: 'Why an alert matters' },
  { key: 'advise', label: 'ACTION ADVICE', desc: 'Recommended operator action for an alert' },
  { key: 'correlate', label: 'ALERT CORRELATION', desc: 'Links multiple alerts into one incident' },
  { key: 'semantic', label: 'SEMANTIC EVENT SEARCH', desc: 'Meaning-based search over the timeline' },
  { key: 'rules', label: 'NATURAL-LANGUAGE RULE', desc: 'Turns a sentence into an alert rule' },
  { key: 'vision', label: 'VLM SCENE DESCRIPTION', desc: 'Describes the frame with a vision model' },
]
