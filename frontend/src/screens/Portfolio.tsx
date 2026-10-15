import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { api } from '../api/client'
import type { Holding } from '../api/types'
import { EmptyState, ErrorState, Skeleton } from '../components/States'
import { Avatar, toast } from '../components/ui'
import { useAuth } from '../lib/auth'
import { amount, signedPct, usd } from '../lib/format'
import { signInWithWallet } from '../lib/siwe'
import { useAsync } from '../lib/useAsync'

export function Portfolio() {
  const { user, login } = useAuth()
  const address = user?.wallets[0]?.address

  if (!address) {
    const link = async () => {
      try { login(await signInWithWallet()) } catch (e: any) { toast(e?.message ?? "Couldn't connect your wallet.") }
    }
    return (
      <div className="app-page">
        <EmptyState emoji="🪙" title="No wallet yet" body="Connect a wallet to see what you own. We only read balances — we can never move your money." cta="Connect wallet" onCta={() => void link()} />
      </div>
    )
  }
  return <PortfolioView address={address} />
}

function PortfolioView({ address }: { address: string }) {
  const nav = useNavigate()
  const { state, reload } = useAsync(() => api.portfolio(address), [address])
  const [pulling, setPulling] = useState(false)
  const y0 = useRef(0)

  const onUp = async (y: number, el: HTMLElement) => {
    const scroller = el.closest('.scroll')
    if (y - y0.current > 60 && (scroller?.scrollTop ?? 0) === 0) {
      setPulling(true)
      await reload(true)
      setPulling(false)
    }
  }

  if (state.status === 'loading') return <div className="app-page"><Skeleton /></div>
  if (state.status === 'error' && !state.data) {
    return <div className="app-page"><ErrorState body="We couldn't reach your balance." onRetry={() => void reload()} /></div>
  }
  const p = state.data!

  if (!p.holdings.length) {
    return (
      <div className="app-page">
        <EmptyState emoji="🪙" title="No holdings yet" body="Send tokens to your address to get started. We'll check they're safe first."
          cta="Copy my address" onCta={() => { void navigator.clipboard?.writeText(address); toast('Address copied') }} />
      </div>
    )
  }

  const up = p.change_24h_usd >= 0
  return (
    <div className="app-page" onPointerDown={e => { y0.current = e.clientY }} onPointerUp={e => void onUp(e.clientY, e.currentTarget)}>
      <div style={{ display: 'flex', justifyContent: 'center', fontSize: 12, color: 'var(--faint)', height: 16, marginTop: -8 }}>
        {pulling ? 'Refreshing…' : '↓ Pull down to refresh'}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ color: 'var(--muted-2)', fontSize: 14, fontWeight: 600 }}>Total balance</div>
        <div className="num" style={{ fontSize: 44, fontWeight: 800, letterSpacing: '-.02em' }}>{usd(p.total_usd)}</div>
        <div className={up ? 'up' : 'down'} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 15, fontWeight: 700 }}>
          {up ? '▲ +' : '▼ -'}{usd(Math.abs(p.change_24h_usd))}
          <span style={{ background: up ? 'var(--green-soft)' : 'var(--red-soft)', borderRadius: 99, padding: '3px 10px', fontSize: 13 }}>{signedPct(p.change_24h_pct)}</span>
          <span style={{ color: 'var(--muted-2)', fontWeight: 500, fontSize: 13 }}>today</span>
        </div>
      </div>
      <div className="label" style={{ marginTop: 6 }}>Your holdings</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {p.holdings.map(h => <HoldingRow key={h.token} h={h} onOpen={() => h.token !== 'native' && nav(`/app/safety/${h.token}`)} />)}
      </div>
    </div>
  )
}

function HoldingRow({ h, onOpen }: { h: Holding; onOpen: () => void }) {
  const pct = h.pnl_pct ?? h.change_24h_pct
  const neg = (pct ?? 0) < 0
  return (
    <div onClick={onOpen} className="card" style={{ cursor: h.token === 'native' ? 'default' : 'pointer', padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 14 }}>
      <Avatar seed={h.token} letter={h.symbol} color={h.token === 'native' ? '#4A90E2' : undefined} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 700, fontSize: 16, display: 'flex', gap: 6, alignItems: 'center' }}>
          {h.symbol}
          {h.verdict === 'DANGER' && <span title="Failed safety check" style={{ fontSize: 12, color: 'var(--red)' }}>✕</span>}
          {h.verdict === 'RISKY' && <span title="Risky" style={{ fontSize: 12, color: 'var(--amber)' }}>!</span>}
        </div>
        <div style={{ color: 'var(--muted-2)', fontSize: 13, marginTop: 2 }}>{amount(h.amount)} {h.symbol}</div>
      </div>
      <div style={{ textAlign: 'right' }}>
        <div className="num" style={{ fontWeight: 800, fontSize: 16 }}>{h.price_usd == null ? '—' : usd(h.value_usd)}</div>
        {pct != null && (
          <div className={neg ? 'down' : 'up'} style={{ fontWeight: 700, fontSize: 13, marginTop: 2 }}>{neg ? '▼ ' : '▲ '}{signedPct(pct)}</div>
        )}
      </div>
    </div>
  )
}
