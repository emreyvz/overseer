// Match verdict (catalog 19). The operator must get ONE confident answer, not a
// raw probability that reads as doubt ("68%"). The backend still selects a single
// best candidate technically; here we translate its score into a decisive verdict
// plus an at-a-glance strength glyph — never a bare percentage.
export interface Verdict { label: string; bars: string; tone: 'lock' | 'firm' | 'review' }

export function matchVerdict(score: number, ambiguous = false): Verdict {
  // A near-tie in frame (two similar-looking objects) is never asserted as confirmed,
  // regardless of raw score — it drops to REVIEW so the operator adjudicates.
  if (ambiguous) return { label: 'MATCH · REVIEW', bars: '▮▯▯', tone: 'review' }
  if (score >= 0.65) return { label: 'MATCH CONFIRMED', bars: '▮▮▮', tone: 'lock' }
  if (score >= 0.5) return { label: 'MATCH', bars: '▮▮▯', tone: 'firm' }
  return { label: 'MATCH · REVIEW', bars: '▮▯▯', tone: 'review' }
}

// Banner line for a located target: "MATCH CONFIRMED · GATE-02  ▮▮▮"
export function matchBanner(cam: string, score: number, ambiguous = false): string {
  const v = matchVerdict(score, ambiguous)
  return `${v.label} · ${cam}  ${v.bars}`
}
