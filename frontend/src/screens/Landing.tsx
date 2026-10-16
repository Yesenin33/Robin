import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { Logo } from '../components/Logo'
import { usd } from '../lib/format'

const DEMO = [
  { l: 'H', c: '#F0D9A8', n: 'HOOD-X', v: '$5,240.10', p: '+8.4%', up: true },
  { l: 'R', c: '#D8D2E8', n: 'ROBO', v: '$3,610.00', p: '+3.1%', up: true },
  { l: 'P', c: '#C9DDE0', n: 'PLUM', v: '$1,834.22', p: '−5.2%', up: false },
]
const TRUST = [
  { d: 'M7 11V8a5 5 0 0 1 10 0v3 M5 11h14v10H5z', t: 'You hold your money', s: 'Your wallet, your keys. We can read balances but never move them.' },
  { d: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z M9 12l2 2 4-4', t: 'Every token checked', s: 'A plain verdict before you buy: clean, risky or danger.' },
  { d: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M8.5 12.5l2.5 2.5 4.5-5', t: 'Honest numbers', s: "If we can't check something, we say so. Unknown is never shown as safe." },
]

export function Landing() {
  const nav = useNavigate()
  const [live, setLive] = useState(14480.52) // demo ticker: the card says "Demo portfolio"
  useEffect(() => {
    const t = setInterval(() => setLive(v => v + (Math.random() - 0.45) * 5), 1500)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="page landing" style={{ gap: 32 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Link to="/" style={{ textDecoration: 'none' }}><Logo size={32} /></Link>
        <div style={{ display: 'flex', alignItems: 'center', gap: 20 }}>
          <span className="t-caption landing-tag">Built for Robinhood Chain</span>
          <button className="btn btn-primary btn-sm landing-open" style={{ height: 44, minHeight: 44 }} onClick={() => nav('/login')}>Open app</button>
        </div>
      </div>

      <div className="landing-hero">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
          <h1 className="display-xl" style={{ textWrap: 'balance' } as React.CSSProperties}>
            Your crypto portfolio on Robinhood Chain —{' '}
            <i style={{ background: 'linear-gradient(transparent 60%, #CCFF00 60%)', padding: '0 .08em' }}>simple and safe.</i>
          </h1>
          <div style={{ fontSize: 19, lineHeight: 1.5, color: 'var(--body)', maxWidth: 480 }}>
            See what you own, know what's risky, follow smart traders. No jargon.
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
            <button className="btn btn-primary" style={{ padding: '0 36px' }} onClick={() => nav('/login')}>Open app</button>
            <span style={{ fontSize: 14, color: 'var(--muted)' }}>Use the wallet you already have.</span>
          </div>
        </div>

        <div className="dark-card" style={{ borderRadius: 28, padding: 28, display: 'flex', flexDirection: 'column', gap: 20, boxShadow: '0 30px 60px rgba(17,14,8,.25)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ fontSize: 14, fontWeight: 600, color: '#B9B3A6' }}>Demo portfolio</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, fontWeight: 600, color: 'var(--lime)' }}>
              <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--lime)' }} />Demo
            </div>
          </div>
          <div>
            <div className="num" style={{ fontSize: 52, fontWeight: 600, letterSpacing: '-.035em', lineHeight: 1 }}>{usd(live)}</div>
            <div style={{ marginTop: 10, fontSize: 15, fontWeight: 600, color: 'var(--lime)' }}>+$312.40 · +2.2% today</div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            {DEMO.map(r => (
              <div key={r.n} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 0', borderTop: '1px solid rgba(255,255,255,.1)' }}>
                <div className="avatar" style={{ width: 36, height: 36, background: r.c, fontSize: 14 }}>{r.l}</div>
                <div style={{ flex: 1, fontWeight: 600, fontSize: 15 }}>{r.n}</div>
                <div style={{ textAlign: 'right' }}>
                  <div className="num" style={{ fontWeight: 600, fontSize: 15 }}>{r.v}</div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: r.up ? 'var(--lime)' : '#FF9C92' }}>{r.p}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="landing-trust">
        {TRUST.map(b => (
          <div key={b.t} style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#110E08" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ flex: 'none', marginTop: 1 }}><path d={b.d} /></svg>
            <div><div style={{ fontWeight: 700, fontSize: 17 }}>{b.t}</div><div style={{ fontSize: 15, lineHeight: 1.5, color: 'var(--muted)', marginTop: 4 }}>{b.s}</div></div>
          </div>
        ))}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12, fontSize: 14, color: 'var(--muted)' }}>
        <span>Chainfolio · Robinhood Chain (4663)</span><span>We read balances. We never hold or move your money.</span>
      </div>
    </div>
  )
}
