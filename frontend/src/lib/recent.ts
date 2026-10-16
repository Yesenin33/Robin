/** "Recently checked" tokens: kept in this browser only, expire after ~24 h. Pure so it can be unit-tested. */
export type Recent = { ca: string; sym: string; ts: number }
export const RECENT_TTL_MS = 24 * 3600 * 1000
const KEY = 'cf_recent'
const ADDR = /^0x[a-fA-F0-9]{40}$/

export const freshRecent = (list: unknown, now = Date.now()): Recent[] =>
  (Array.isArray(list) ? list : []).filter((r): r is Recent =>
    !!r && typeof r.ca === 'string' && ADDR.test(r.ca) && typeof r.ts === 'number' && now - r.ts >= 0 && now - r.ts < RECENT_TTL_MS)

export const loadRecent = (): Recent[] => {
  try { return freshRecent(JSON.parse(localStorage.getItem(KEY) ?? '[]')) } catch { return [] }
}
export const saveRecent = (r: Recent) => {
  const list = [r, ...loadRecent().filter(x => x.ca !== r.ca)].slice(0, 3)
  try { localStorage.setItem(KEY, JSON.stringify(list)) } catch { /* private mode */ }
}
