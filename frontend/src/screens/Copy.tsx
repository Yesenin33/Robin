import { useState } from 'react'
import { Link } from 'react-router-dom'

import { api, ApiError } from '../api/client'
import type { CopyItem, RiskTag, Suggested, Verdict } from '../api/types'
import { EmptyState, ErrorState } from '../components/States'
import { Avatar, toast } from '../components/ui'
import { ago, shortAddr, usd } from '../lib/format'
import { useAsync } from '../lib/useAsync'

const TAG: Record<RiskTag, { label: string; bg: string; fg: string }> = {
  low: { label: 'Low risk', bg: '#E4F6DD', fg: '#067A0A' },
  medium: { label: 'Medium risk', bg: '#FFEFCC', fg: '#8A5300' },
  high: { label: 'High risk', bg: '#FCE6E2', fg: '#8F1D15' },
}
const VT: Partial<Record<Verdict, { text: string; bg: string; fg: string }>> = {
  CLEAN: { text: 'Clean token', bg: '#E4F6DD', fg: '#0D3B10' },
  RISKY: { text: 'Risky token', bg: '#FFEFCC', fg: '#4A2C00' },
  DANGER: { text: 'Danger token', bg: '#FCE6E2', fg: '#5E120C' },
  UNKNOWN: { text: 'Unchecked token', bg: '#ECE9E1', fg: '#110E08' },
}

export function Copy() {
  const { state, reload } = useAsync(async () => {
    const [feed, suggested] = await Promise.all([
      api.copyFeed(),
      api.copySuggested().catch(e => { if (e instanceof ApiError && e.status === 404) return []; throw e }),
    ])
    return { feed, suggested }
  }, [])

  return (
    <div className="app-page">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <h1 className="display">Smart wallets</h1>
        <div className="lead">Traders ranked by what they actually made, not by followers. Copy a move with one tap.</div>
      </div>
      {state.status === 'loading' && (
        <div className="cols-3" aria-busy aria-label="Loading">{[1, 2, 3].map(k => <div key={k} className="skel" style={{ height: 220, borderRadius: 24 }} />)}</div>
      )}
      {state.status === 'error' && <ErrorState body="We couldn't load smart wallets." onRetry={() => void reload()} />}
      {state.status === 'ready' && state.data.suggested.length > 0 && (
        <>
          <div className="label">Worth following</div>
          <div className="cols-2">
            {state.data.suggested.map(s => <SuggestedCard key={s.address} s={s} onFollow={() => void reload(true)} />)}
          </div>
        </>
      )}
      {state.status === 'ready' && state.data.feed.length > 0 && (
        <>
          <div className="label" style={{ marginTop: 6 }}>Latest moves</div>
          <div className="cols-3">{state.data.feed.map(w => <MoveCard key={w.address + w.ts + w.tx_hash} w={w} />)}</div>
        </>
      )}
      {state.status === 'ready' && state.data.feed.length === 0 && state.data.suggested.length === 0 && (
        <EmptyState emoji="👀" title="No smart wallets right now" body="Check back soon. We'll show traders worth following here." cta="Refresh" onCta={() => void reload()} />
      )}
    </div>
  )
}

function FollowButton({ address, following, onDone }: { address: string; following: boolean; onDone?: () => void }) {
  const [on, setOn] = useState(following)
  const [busy, setBusy] = useState(false)
  const toggle = async () => {
    setBusy(true)
    setOn(!on)
    try {
      await api.copySubscribe(address, !on)
      onDone?.()
    } catch {
      setOn(on)
      toast("Couldn't update. Try again.")
    } finally {
      setBusy(false)
    }
  }
  return (
    <button disabled={busy} onClick={() => void toggle()} className={`btn btn-sm ${on ? '' : 'btn-primary'}`}
      style={{ width: '100%', fontSize: 16, ...(on ? { background: 'transparent', color: 'var(--ink)', boxShadow: 'inset 0 0 0 1.5px #110E08' } : {}) }}>
      {on ? 'Copying ✓' : 'Copy'}
    </button>
  )
}

function Tag({ t }: { t: RiskTag }) {
  const tag = TAG[t] ?? TAG.medium
  return <div style={{ fontSize: 13, fontWeight: 700, padding: '5px 11px', borderRadius: 99, background: tag.bg, color: tag.fg, whiteSpace: 'nowrap' }}>{tag.label}</div>
}

function SuggestedCard({ s, onFollow }: { s: Suggested; onFollow: () => void }) {
  return (
    <div className="card" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Avatar seed={s.address} letter={s.address.slice(2, 3)} size={44} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="num" style={{ fontWeight: 700, fontSize: 16 }}>{shortAddr(s.address)}</div>
          <div className="up" style={{ fontSize: 14, marginTop: 2, fontWeight: 600 }}>{s.realised_pnl_usd >= 0 ? '+' : '−'}{usd(Math.abs(s.realised_pnl_usd)).replace(/\.\d\d$/, '')} realised</div>
        </div>
        <Tag t={s.risk_tag} />
      </div>
      <div style={{ display: 'flex', gap: 18, fontSize: 13, color: 'var(--muted)' }}>
        <span><b style={{ color: 'var(--ink)', fontSize: 16 }}>{s.wins}</b> wins</span>
        <span><b style={{ color: 'var(--ink)', fontSize: 16 }}>{s.losses}</b> losses</span>
        <span><b style={{ color: 'var(--ink)', fontSize: 16 }}>{s.early_buys}</b> early buys</span>
      </div>
      <div style={{ fontSize: 15, color: 'var(--body)', lineHeight: 1.5 }}>{s.reason}</div>
      <FollowButton address={s.address} following={s.following} onDone={onFollow} />
    </div>
  )
}

function MoveCard({ w }: { w: CopyItem }) {
  const amount = w.amount_usd != null ? ` ${usd(w.amount_usd).replace(/\.00$/, '')} of` : ''
  const v = w.verdict ? VT[w.verdict] : undefined
  return (
    <div className="card" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Avatar seed={w.address} letter={w.address.slice(2, 3)} size={44} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="num" style={{ fontWeight: 700, fontSize: 16 }}>{shortAddr(w.address)}</div>
          <div style={{ fontSize: 14, color: 'var(--muted)', marginTop: 2 }}>{ago(w.ts)}</div>
        </div>
        <Tag t={w.risk_tag} />
      </div>
      <div className="serif" style={{ fontSize: 28, letterSpacing: '-.02em', lineHeight: 1.1, display: 'flex', alignItems: 'center', gap: 10 }}>
        <Avatar seed={w.token_address ?? w.token_symbol} letter={w.token_symbol} size={30} image={w.token_image_url} />
        <span>{w.side === 'buy' ? 'Bought' : 'Sold'}{amount} {w.token_symbol}</span>
      </div>
      {v && w.token_address && (
        <Link to={`/app/safety/${w.token_address}`} style={{ alignSelf: 'flex-start', fontSize: 13, fontWeight: 700, padding: '5px 11px', borderRadius: 99, background: v.bg, color: v.fg, textDecoration: 'none' }}>{v.text} →</Link>
      )}
      <div style={{ fontSize: 15, color: 'var(--body)', lineHeight: 1.5 }}>{w.reason}</div>
      <FollowButton address={w.address} following={w.following} />
    </div>
  )
}
