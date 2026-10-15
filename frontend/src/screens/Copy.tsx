import { useState } from 'react'

import { api, ApiError } from '../api/client'
import type { CopyItem } from '../api/types'
import { EmptyState, ErrorState, Skeleton } from '../components/States'
import { Avatar, toast } from '../components/ui'
import { ago, shortAddr, usd } from '../lib/format'
import { useAsync } from '../lib/useAsync'

const TAG = {
  low: { label: 'Low risk', bg: 'var(--green-soft)', fg: 'var(--green)' },
  medium: { label: 'Medium risk', bg: 'var(--amber-soft)', fg: 'var(--amber)' },
  high: { label: 'High risk', bg: 'var(--red-soft)', fg: 'var(--red)' },
}

export function Copy() {
  const { state, reload } = useAsync(async () => {
    try {
      return await api.copyFeed()
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return [] // feed not deployed yet
      throw e
    }
  }, [])

  return (
    <div className="app-page">
      <div>
        <div className="h1">Smart wallets</div>
        <div className="sub" style={{ marginTop: 4 }}>Traders with a strong track record. Copy a move with one tap.</div>
      </div>
      {state.status === 'loading' && <Skeleton rows={3} />}
      {state.status === 'error' && <ErrorState body="We couldn't load smart wallets." onRetry={() => void reload()} />}
      {state.status === 'ready' && (state.data.length
        ? <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>{state.data.map(w => <WalletCard key={w.address + w.ts} w={w} />)}</div>
        : <EmptyState emoji="👀" title="No smart wallets right now" body="Check back soon. We'll show traders worth following here." cta="Refresh" onCta={() => void reload()} />)}
    </div>
  )
}

function WalletCard({ w }: { w: CopyItem }) {
  const [on, setOn] = useState(w.following)
  const [busy, setBusy] = useState(false)
  const tag = TAG[w.risk_tag]

  const toggle = async () => {
    setBusy(true)
    setOn(!on)
    try {
      await api.copySubscribe(w.address, !on)
    } catch {
      setOn(on)
      toast("Couldn't update. Try again.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card" style={{ borderRadius: 22, padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Avatar seed={w.address} letter={w.address.slice(2, 3)} size={42} />
        <div style={{ flex: 1 }}>
          <div className="num" style={{ fontWeight: 700, fontSize: 15 }}>{shortAddr(w.address)}</div>
          <div style={{ fontSize: 13, color: 'var(--muted-2)', marginTop: 2 }}>{ago(w.ts)}</div>
        </div>
        <div style={{ fontSize: 12, fontWeight: 700, padding: '5px 10px', borderRadius: 99, background: tag.bg, color: tag.fg }}>{tag.label}</div>
      </div>
      <div style={{ fontSize: 18, fontWeight: 800 }}>{w.side === 'buy' ? 'Bought' : 'Sold'} {usd(w.amount_usd).replace(/\.00$/, '')} of {w.token_symbol}</div>
      <div style={{ fontSize: 14, color: 'var(--muted)', lineHeight: 1.45 }}>{w.reason}</div>
      <button disabled={busy} onClick={() => void toggle()} style={{
        height: 46, borderRadius: 14, border: '1px solid var(--green)', fontWeight: 800, fontSize: 15,
        background: on ? 'transparent' : 'var(--green)', color: on ? 'var(--green)' : 'var(--green-ink)',
      }}>{on ? '✓ Copying' : 'Copy'}</button>
    </div>
  )
}
