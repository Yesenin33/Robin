import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { usd } from '../lib/format'

const BADGES = [{ e: '🔒', t: 'You hold your money' }, { e: '🛡️', t: 'Every token safety-checked' }, { e: '✅', t: 'No hidden fees' }]

export function Landing() {
  const nav = useNavigate()
  const [live, setLive] = useState(12480.52) // demo ticker, as in the design
  useEffect(() => {
    const t = setInterval(() => setLive(v => v + (Math.random() - 0.45) * 6), 1500)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="page" style={{ gap: 28 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div className="brand">CHAINFOLIO</div>
        <div className="pill">Robinhood Chain</div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 8 }}>
        <h1 style={{ margin: 0, fontSize: 34, lineHeight: 1.1, fontWeight: 800, letterSpacing: '-.02em', textWrap: 'balance' } as React.CSSProperties}>
          Your crypto portfolio on Robinhood Chain — <span className="up">simple and safe</span>
        </h1>
        <div style={{ color: 'var(--text-2)', fontSize: 16, lineHeight: 1.5 }}>
          See what you own, know what's risky, follow smart traders. No jargon.
        </div>
      </div>
      <div className="card" style={{ borderRadius: 24, padding: 20, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ fontSize: 13, color: 'var(--muted-2)', fontWeight: 600 }}>Demo portfolio</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 700 }} className="up">
            <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--green)' }} />Live
          </div>
        </div>
        <div className="num" style={{ fontSize: 40, fontWeight: 800, letterSpacing: '-.02em' }}>{usd(live)}</div>
        <div className="up" style={{ fontWeight: 700, fontSize: 15 }}>▲ +$312.40 (2.6%) today</div>
        <div style={{ display: 'flex', height: 6, borderRadius: 99, overflow: 'hidden', gap: 2 }}>
          <div style={{ flex: 5, background: 'var(--green)' }} /><div style={{ flex: 3, background: '#4A90E2' }} /><div style={{ flex: 2, background: 'var(--muted-2)' }} />
        </div>
      </div>
      <button className="btn btn-primary" style={{ fontSize: 17 }} onClick={() => nav('/login')}>Open app</button>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, marginTop: 'auto' }}>
        {BADGES.map(b => (
          <div key={b.t} className="card" style={{ borderRadius: 16, padding: '14px 10px', textAlign: 'center', display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ fontSize: 20 }}>{b.e}</div>
            <div style={{ fontSize: 12, fontWeight: 700, lineHeight: 1.3 }}>{b.t}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
