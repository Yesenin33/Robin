import { useEffect, useState } from 'react'

import { colorFor } from '../lib/format'

/** A token's own image when we have one; a deliberate monogram otherwise - and when the image is missing, blocked
 *  or broken (one bad URL must never break a row). */
export function Avatar({ seed, letter, size = 44, color, image }: { seed: string; letter: string; size?: number; color?: string; image?: string | null }) {
  const [failed, setFailed] = useState(false)
  const showImage = !!image && !failed
  return (
    <div className="avatar" style={{ width: size, height: size, background: showImage ? 'var(--card)' : color ?? colorFor(seed), fontSize: size * 0.38 }}>
      {showImage
        ? <img src={image!} alt="" width={size} height={size} loading="lazy" decoding="async" referrerPolicy="no-referrer"
            onError={() => setFailed(true)} style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
        : letter.slice(0, 1).toUpperCase()}
    </div>
  )
}

export function Toggle({ on }: { on: boolean }) {
  return (
    <div className="toggle" style={{ background: on ? 'var(--green)' : 'var(--line-2)' }} role="switch" aria-checked={on}>
      <i style={{ left: on ? 23 : 3 }} />
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
