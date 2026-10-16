import { useState } from 'react'

import { api, ApiError } from '../api/client'
import type { CopyItem, RiskTag, Suggested } from '../api/types'
import { EmptyState, ErrorState, Skeleton } from '../components/States'
import { Avatar, toast } from '../components/ui'
import { ago, shortAddr, usd } from '../lib/format'
import { useAsync } from '../lib/useAsync'

const TAG = {
  low: { label: 'Low risk', bg: 'var(--green-soft)', fg: 'var(--green-text)' },
  medium: { label: 'Medium risk', bg: 'var(--amber-soft)', fg: 'var(--amber-text)' },
  high: { label: 'High risk', bg: 'var(--red-soft)', fg: 'var(--red-text)' },
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
      <div>
        <div className="h1">Smart wallets</div>
        <div className="sub" style={{ marginTop: 4 }}>Traders with a strong track record. Copy a move with one tap.</div>
      </div>
      {state.status === 'loading' && <Skeleton rows={3} />}
      {state.status === 'error' && <ErrorState body="We couldn't load smart wallets." onRetry={() => void reload()} />}
      {state.status === 'ready' && state.data.suggested.length > 0 && (
        <>
          <div className="label">Worth following</div>
          <div className="cols-2">
            {state.data.suggested.map(s => <SuggestedCard key={s.address} s={s} onFollow={() => void reload(true)} />)}
          </div>
          {state.data.feed.length > 0 && <div className="label">Latest moves</div>}
        </>
      )}
      {state.status === 'ready' && (state.data.feed.length
        ? <div className="cols-2">{state.data.feed.map(w => <WalletCard key={w.address + w.ts + w.tx_hash} w={w} />)}</div>
        : state.data.suggested.length === 0 && <EmptyState emoji="👀" title="No smart wallets right now" body="Check back soon. We'll show traders worth following here." cta="Refresh" onCta={() => void reload()} />)}
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
    <button disabled={busy} onClick={() => void toggle()} style={{
      height: 46, borderRadius: 14, border: '1px solid var(--green)', fontWeight: 800, fontSize: 15, minHeight: 46,
      background: on ? 'transparent' : 'var(--green)', color: on ? 'var(--green-text)' : 'var(--green-ink)',
    }}>{on ? '✓ Copying' : 'Copy'}</button>
  )
}

function Tag({ t }: { t: RiskTag }) {
  const tag = TAG[t]
  return <div style={{ fontSize: 12, fontWeight: 700, padding: '5px 10px', borderRadius: 99, background: tag.bg, color: tag.fg }}>{tag.label}</div>
}

function SuggestedCard({ s, onFollow }: { s: Suggested; onFollow: () => void }) {
  return (
    <div className="card" style={{ borderRadius: 22, padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Avatar seed={s.address} letter={s.address.slice(2, 3)} size={42} />
        <div style={{ flex: 1 }}>
          <div className="num" style={{ fontWeight: 700, fontSize: 15 }}>{shortAddr(s.address)}</div>
          <div className="up" style={{ fontSize: 13, marginTop: 2, fontWeight: 700 }}>{s.realised_pnl_usd >= 0 ? '+' : '-'}{usd(Math.abs(s.realised_pnl_usd)).replace(/\.\d\d$/, '')} realised</div>
        </div>
        <Tag t={s.risk_tag} />
      </div>
      <div style={{ fontSize: 14, color: 'var(--muted)', lineHeight: 1.45 }}>{s.reason}</div>
      <FollowButton address={s.address} following={s.following} onDone={onFollow} />
    </div>
  )
}

function WalletCard({ w }: { w: CopyItem }) {
  const amount = w.amount_usd != null ? ` ${usd(w.amount_usd).replace(/\.00$/, '')} of` : ''
  return (
    <div className="card" style={{ borderRadius: 22, padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Avatar seed={w.address} letter={w.address.slice(2, 3)} size={42} />
        <div style={{ flex: 1 }}>
          <div className="num" style={{ fontWeight: 700, fontSize: 15 }}>{shortAddr(w.address)}</div>
          <div style={{ fontSize: 13, color: 'var(--muted-2)', marginTop: 2 }}>{ago(w.ts)}</div>
        </div>
        <Tag t={w.risk_tag} />
      </div>
      <div style={{ fontSize: 18, fontWeight: 800, display: 'flex', alignItems: 'center', gap: 10 }}>
        <Avatar seed={w.token_address ?? w.token_symbol} letter={w.token_symbol} size={28} image={w.token_image_url} />
        <span>{w.side === 'buy' ? 'Bought' : 'Sold'}{amount} {w.token_symbol}</span>
      </div>
      <div style={{ fontSize: 14, color: 'var(--muted)', lineHeight: 1.45 }}>{w.reason}</div>
      <FollowButton address={w.address} following={w.following} />
    </div>
  )
}
