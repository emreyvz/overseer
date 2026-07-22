// OVERSEER — external asset registry (brief §12–13).
// Icons8 icons used WITH attribution ("Icons by Icons8 — icons8.com" in About + README).
// Tunetank SFX/music loaded from CDN at runtime (verify license before bundling).

const TT = 'https://d1s1y0ui543e5o.cloudfront.net'

/** Icons8 icon by commonName → white PNG url (ios7 line style). Attribution required. */
export const icon8 = (commonName: string, size = 32, color = 'ffffff') =>
  `https://img.icons8.com/ios7/${size}/${color}/${commonName}.png`

/** Semantic icon → Icons8 commonName (see brief §13 table). */
export const ICONS = {
  target: 'define-location', accuracy: 'accuracy', radar: 'radar',
  warning: 'error--v3', cameraDome: 'dome-camera', cameraBullet: 'bullet-camera',
  eye: 'visible', pin: 'place-marker', network: 'network', search: 'search',
  record: 'record', signal: 'high-connection', cpu: 'processor', lock: 'lock',
  grid: 'grid', car: 'car', person: 'walking', history: 'time-machine',
  export: 'export', fullscreen: 'full-screen', shield: 'shield',
} as const

/** Semantic SFX → Tunetank preview url. */
export const SFX = {
  ambience: `${TT}/sfx/26804/hphk1.mp3`,     // tactical drone (boot/steady)
  storm: `${TT}/sfx/29359/sgbew.mp3`,        // digital hum & glitch
  type: `${TT}/sfx/27751/p2ygt.mp3`,         // warm typewriter
  glitch: `${TT}/sfx/29352/s5rzm.mp3`,       // data glitch (camera-switch)
  whoosh: `${TT}/sfx/26995/xslru.mp3`,       // deep whoosh (mode change)
  click: `${TT}/sfx/27285/oo41e.mp3`,        // interface click
  sonar: `${TT}/sfx/27140/jcx5u.mp3`,        // sonar pings (scan)
  ping: `${TT}/sfx/28598/v3oo4.mp3`,         // metallic notification (detection)
  warn: `${TT}/sfx/27535/y030u.mp3`,         // warning digital alarm
  alarm: `${TT}/sfx/27899/nm1bf.mp3`,        // emergency alarm (critical)
  shutter: `${TT}/sfx/29161/u6crb.mp3`,      // camera beep & shutter
  error: `${TT}/sfx/28213/d0efz.mp3`,        // glitch error (bad command)
} as const

export type SfxKey = keyof typeof SFX

export const MUSIC = {
  operations: `${TT}/tracks/5858/preview/3ve4r.mp3`, // Future Thoughts (Tech)
  alert: `${TT}/tracks/5004/preview/s524a.mp3`,       // End of Everything (Cinematic)
} as const
