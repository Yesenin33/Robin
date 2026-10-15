import { useEffect, useState } from 'react'

import { colorFor } from '../lib/format'

export function Avatar({ seed, letter, size = 44, color }: { seed: string; letter: string; size?: number; color?: string }) {
  return (
    <div className="avatar" style={{ width: size, height: size, background: color ?? colorFor(seed), fontSize: size * 0.38 }}>
      {letter.slice(0, 1).toUpperCase()}
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
