import { useEffect, useState } from 'react'

/** Deterministic identicon derived from the address: same coin, same picture, every load, no network.
 *  DexScreener has no art for most of the tokens on this chain (measured: 0 of the owner's 47 holdings,
 *  WETH included), so a monogram letter was all we could show. A generated mark makes every coin its own
 *  thing, the way real wallets do it, and needs nothing from any API. */
function identicon(seed: string) {
  const s = (seed || '?').toLowerCase()
  let h = 0x811c9dc5
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0 }
  const next = () => { h ^= h << 13; h >>>= 0; h ^= h >> 17; h ^= h << 5; h >>>= 0; return h }
  // 5x5 grid built from the left half and mirrored, so it reads as a mark rather than noise
  const cells: boolean[] = new Array(25).fill(false)
  for (let c = 0; c < 3; c++) {
    for (let r = 0; r < 5; r++) cells[r * 5 + c] = (next() % 5) > 1
  }
  for (let r = 0; r < 5; r++) { cells[r * 5 + 3] = cells[r * 5 + 1]; cells[r * 5 + 4] = cells[r * 5 + 0] }
  // tokens sharing a symbol still get different marks; the hue is theirs alone
  const hue = next() % 360
  return { cells, fg: `hsl(${hue} 58% 42%)`, bg: `hsl(${hue} 42% 93%)` }
}

/** A token's own image when we have one; a deterministic identicon otherwise - and when the image is
 *  missing, blocked or broken (one bad URL must never break a row). */
export function Avatar({ seed, letter, size = 44, color, image }: { seed: string; letter: string; size?: number; color?: string; image?: string | null }) {
  const [failed, setFailed] = useState(false)
  const showImage = !!image && !failed
  const mark = identicon(seed)
  return (
    <div className="avatar" style={{ width: size, height: size, background: showImage ? 'var(--card)' : color ?? mark.bg, fontSize: size * 0.38, overflow: 'hidden', padding: 0 }}
      title={letter} aria-label={letter}>
      {showImage
        ? <img src={image!} alt="" width={size} height={size} loading="lazy" decoding="async" referrerPolicy="no-referrer"
            onError={() => setFailed(true)} style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
        : <svg viewBox="0 0 5 5" width={size} height={size} aria-hidden="true" shapeRendering="crispEdges" style={{ display: 'block' }}>
            {mark.cells.map((on, i) => on
              ? <rect key={i} x={i % 5} y={Math.floor(i / 5)} width={1.02} height={1.02} fill={mark.fg} />
              : null)}
          </svg>}
    </div>
  )
}

export function Toggle({ on }: { on: boolean }) {
  return (
    <div className="toggle" style={{ background: on ? 'var(--ink)' : 'var(--line-2)' }} role="switch" aria-checked={on}>
      <i style={{ left: on ? 23 : 3, background: on ? 'var(--lime)' : '#fff' }} />
    </div>
  )
}

let push: ((m: string) => void) | null = null
export const toast = (m: string) => push?.(m)

export function Toaster() {
  const [msg, setMsg] = useState<string | null>(null)
  useEffect(() => {
    let t: number
    push = m => { setMsg(m); clearTimeout(t); t = window.setTimeout(() => setMsg(null), 2200) }
    return () => { push = null; clearTimeout(t) }
  }, [])
  return msg ? <div className="toast" role="status">{msg}</div> : null
}
