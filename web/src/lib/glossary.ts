// OVERSEER — one plain sentence for every term the perception screens use.
//
// The screens were built the wrong way round: jargon in front, meaning hidden in a tooltip you
// had to already suspect was there. An operator opened a screen, met a wall of terse capitals
// like DECIDEDNESS, TRANSIENT and PROMINENCE, and could not work out what any of it was.
// "DECIDEDNESS" was not even a real word; I invented it.
//
// So: every technical term appears exactly once here, explained in ONE sentence, written for
// someone who has never seen it. The <Explain> component marks any term that has an entry so it
// is visibly askable, which means the operator never has to guess whether help exists.
//
// Rules for writing these: no other jargon inside the explanation, no marketing, and say what it
// means for the operator rather than how it is computed.

export interface Term {
  /** The plain-language label to lead with, when the term itself is unhelpful. */
  plain?: string
  /** One sentence. This is what the operator reads. */
  what: string
}

export const GLOSSARY: Record<string, Term> = {
  // ── FOG OF WAR ────────────────────────────────────────────────────────────────────────────
  coverage: {
    plain: 'how much you actually see',
    what: 'The share of the ground in this view that the camera can genuinely watch, rather than '
      + 'just point at.',
  },
  dori: {
    plain: 'how close is close enough',
    what: 'A published standard (EN 62676-4) for how many pixels you need on a person to detect, '
      + 'observe, recognise or identify them.',
  },
  detect: { what: 'Close enough to tell that somebody is there at all.' },
  observe: { what: 'Close enough to describe what somebody is wearing and doing.' },
  recognise: { what: 'Close enough to recognise somebody you already know.' },
  identify: { what: 'Close enough to say who a stranger is.' },
  'blind spot': {
    what: 'A patch of ground the camera cannot usefully watch, for a reason worth fixing.',
  },
  persistent: {
    plain: 'always there',
    what: 'This gap has been in the same place day after day, so it is part of the site rather '
      + 'than something that happened to be parked there.',
  },
  transient: {
    plain: 'comes and goes',
    what: 'Something is blocking the view right now, but it was not there yesterday and may be '
      + 'gone tomorrow.',
  },
  occlusion: { plain: 'hidden behind something', what: 'An object stands between the camera and this ground.' },
  radiometric: {
    plain: 'too dark or too bright',
    what: 'The picture here is too dark, too washed out or too blurred to get anything from.',
  },
  empirical: {
    plain: 'people vanish here',
    what: 'Tracking keeps losing people at this exact spot, which is the system admitting a '
      + 'failure rather than predicting one.',
  },
  'px/m': {
    plain: 'pixels on target',
    what: 'How many pixels cover one metre at that distance. More pixels means more detail.',
  },

  // ── DREAMSTATE ────────────────────────────────────────────────────────────────────────────
  divergence: {
    plain: 'something is off',
    what: 'A moment when the scene stopped matching what this place normally looks like at this '
      + 'hour. It does not know WHAT changed.',
  },
  sigma: {
    plain: 'how unusual',
    what: 'How far this is from normal for this spot. Around 1 is everyday, 5 is rare, 8 is '
      + 'something you have effectively never seen here.',
  },
  maturity: {
    plain: 'how much it has learned',
    what: 'How far through learning this place the model is. Until it finishes it stays silent '
      + 'rather than guessing, so an empty screen early on is correct, not broken.',
  },
  'time bucket': {
    what: 'A stretch of the day (night, morning, midday and so on) learned separately, because a '
      + 'place behaves differently at 3am than at noon.',
  },
  threshold: {
    plain: 'how touchy it is',
    what: 'How unusual something has to be before it is worth telling you about. Lower means more '
      + 'reports, most of them harmless.',
  },
  'scene change': { what: 'Something about the place changed, and no person was behaving oddly at the time.' },
  'subject behaviour': { what: 'A person was moving unusually at the same moment, so this is probably about them.' },

  // ── GRAIN ─────────────────────────────────────────────────────────────────────────────────
  percentile: {
    plain: 'how rare',
    what: 'Where this ranks against everyone else who has walked here. 50 is typical; 1 means '
      + 'only one journey in a hundred looked like this.',
  },
  unjudged: {
    plain: 'no opinion',
    what: 'The model has not watched this spot enough to have a view, so it is deliberately not '
      + 'guessing.',
  },
  ordinary: { what: 'This movement is normal for this place. You are meant to ignore it.' },
  unusual: { what: 'Very few people have ever moved like this here.' },
  decidedness: {
    plain: 'how consistent',
    what: 'How strongly people agree on which way to go here. High means almost everyone walks '
      + 'the same way; low means it is a crossroads.',
  },
  observations: { plain: 'times seen', what: 'How many journeys the model has watched through this exact spot.' },
  dwell: { plain: 'standing still', what: 'How long somebody stayed put in one place.' },
  heading: { plain: 'direction', what: 'Which way somebody was facing and travelling.' },
  precedent: {
    plain: 'has this happened before',
    what: 'Past journeys that took a similar shape, and what you decided about them at the time.',
  },
  mute: { what: 'Mark a patch of ground so the model stops judging anybody standing in it.' },

  // ── EARDRUM ───────────────────────────────────────────────────────────────────────────────
  baseline: {
    plain: 'the healthy reading',
    what: 'A snapshot of how this surface behaves while it is working properly. Everything later '
      + 'is compared against it, so slow drift becomes visible.',
  },
  spectrum: {
    plain: 'which speeds it shakes at',
    what: 'A breakdown of the shaking into the speeds it happens at. A machine turning at a '
      + 'steady rate makes a spike.',
  },
  spectrogram: { plain: 'shaking over time', what: 'The same breakdown, but scrolling, so you can see it change.' },
  'noise floor': {
    plain: 'below this is nothing',
    what: 'The level under which this camera simply cannot tell shaking from picture noise. '
      + 'Anything below the dashed line is not real.',
  },
  prominence: { plain: 'how far it stands out', what: 'How far a spike rises above the surrounding noise.' },
  'reference probe': {
    plain: 'the still one',
    what: 'A probe on something that should not move. Its motion is subtracted from every other '
      + 'probe, so the camera shaking on its mount does not look like a machine fault.',
  },
  'modal analysis': {
    plain: 'how the structure rings',
    what: 'Works out the natural rhythms a structure rings at. If one of them drops over months, '
      + 'the structure has lost stiffness.',
  },
  damping: { plain: 'how fast it settles', what: 'How quickly the shaking dies away after something sets it off.' },
  harmonic: {
    plain: 'multiples of the running speed',
    what: 'Shaking at exactly two or three times the turning speed. Which one dominates points at '
      + 'a different kind of fault.',
  },
  rms: { plain: 'overall shaking', what: 'One number for how much this surface is moving in total.' },
  'structural band': {
    what: 'The range of shaking speeds a camera can measure. It sits far below speech, so this '
      + 'cannot pick up a conversation.',
  },

  // ── BEDROCK ───────────────────────────────────────────────────────────────────────────────
  fact: { what: 'One thing the system observed, with a start, an end, and a note of what saw it.' },
  'valid time': { plain: 'when it was true', what: 'The stretch of time the thing was actually happening.' },
  'transaction time': {
    plain: 'when we found out',
    what: 'When the system came to believe it, which can be much later than when it happened.',
  },
  predicate: { plain: 'the kind of fact', what: 'What sort of statement this is: was seen on, wore, was near, and so on.' },
  provenance: {
    plain: 'how we know',
    what: 'Which model said this, from which frame, and how sure it was.',
  },
  superseded: {
    plain: 'no longer believed',
    what: 'The system has since changed its mind. The old belief is kept, struck through, so you '
      + 'can see what was thought at the time.',
  },
  confidence: { what: 'How sure the model was. Thin and faint means it was not very sure.' },
  backfill: { what: 'Read the history already in the database and turn it into facts you can query.' },
}

/** Look a term up, case- and spacing-insensitively. */
export function lookup(term: string): Term | null {
  return GLOSSARY[term.trim().toLowerCase()] ?? null
}

/** The label to show for a term: the plain wording where there is one, else the term itself. */
export function plainLabel(term: string): string {
  return lookup(term)?.plain ?? term
}
