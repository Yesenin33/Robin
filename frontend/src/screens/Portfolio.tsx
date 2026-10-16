import { useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import type { Holding } from '../api/types'
import { EmptyState, ErrorState, Skeleton } from '../components/States'
import { Avatar, toast } from '../components/ui'
import { useAuth } from '../lib/auth'
import { amount, shortAddr, signedPct, usd } from '../lib/format'
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

function tagFor(h: Holding): { label: string; cls: string } | null {
  if (h.token === 'native') return null
  if (h.verdict === 'DANGER') return { label: 'Danger', cls: 'danger' }
  if (h.verdict === 'RISKY') return { label: 'Risky', cls: 'risky' }
  if (h.verdict === 'UNKNOWN') return { label: 'Unknown', cls: '' }
  if (!h.verdict && h.value_usd > 0) return { label: 'Unchecked', cls: '' }
  return null
}

function PortfolioView({ address }: { address: string }) {
  const { state, reload } = useAsync(() => api.portfolio(address), [address])
  const [refreshing, setRefreshing] = useState(false)

  const refresh = async () => { setRefreshing(true); await reload(true); setRefreshing(false) }

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
        <div className="num" style={{ textAlign: 'center', fontSize: 14, color: 'var(--muted)', wordBreak: 'break-all' }}>{address}</div>
      </div>
    )
  }

  const up = p.change_24h_usd >= 0
  const notCounted = p.not_counted_count ?? 0
  const excludedCount = p.excluded_count ?? 0
  // The glance card counts BADGES, not a partition: a row can be Risky and still be excluded from the
  // total, and splitting those across two buckets is what made the card read "Risky 0" while two rows in
  // the list right next to it carried a Risky badge.
  const clean = p.holdings.filter(h => h.verdict === 'CLEAN').length
  const risky = p.holdings.filter(h => h.verdict === 'RISKY' || h.verdict === 'DANGER').length
  const outOfTotal = notCounted + excludedCount
  // Several different contracts on this chain answer to the same symbol (the wallet holds four named USDG
  // and two named AI). Without a distinguishing mark those rows are indistinguishable, which reads to the
  // owner as "the count is wrong" - so any repeated symbol gets its short address shown.
  const seen: Record<string, number> = {}
  for (const h of p.holdings) { const k = (h.symbol || '?').toUpperCase(); seen[k] = (seen[k] ?? 0) + 1 }
  const dup = (h: Holding) => (seen[(h.symbol || '?').toUpperCase()] ?? 0) > 1

  return (
    <div className="app-page">
      <div className="grid-main-side">
        <div className="stack" style={{ gap: 24 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div className="label">Total balance</div>
              <button onClick={() => void refresh()} disabled={refreshing} style={{ height: 44, background: 'none', border: 0, fontSize: 14, fontWeight: 600, color: 'var(--muted)', padding: 0 }}>
                {refreshing ? 'Refreshing…' : 'Refresh'}
              </button>
            </div>
            <div className="num pf-total">{usd(p.total_usd)}</div>
            <div className={up ? 'up' : 'down'} style={{ display: 'flex', gap: 10, alignItems: 'center', fontSize: 16, fontWeight: 600 }}>
              <span>{up ? '+' : '−'}{usd(Math.abs(p.change_24h_usd))}</span>
              <span style={{ background: up ? 'var(--green-soft)' : 'var(--red-soft)', borderRadius: 99, padding: '3px 10px', fontSize: 14 }}>{signedPct(p.change_24h_pct)}</span>
              <span style={{ color: 'var(--muted)', fontWeight: 500, fontSize: 14 }}>today</span>
            </div>
          </div>

          {notCounted > 0 && (
            <div className="notice" role="note">
              <b style={{ color: 'var(--ink)' }}>{notCounted} {notCounted === 1 ? "token isn't" : "tokens aren't"} counted.</b>{' '}
              We couldn't confirm you could sell {notCounted === 1 ? 'it' : 'them'}, so {notCounted === 1 ? 'it stays' : 'they stay'} out of your total.
            </div>
          )}
          {(p.excluded_count ?? 0) > 0 && (
            <div className="notice" role="note">
              <b style={{ color: 'var(--ink)' }}>{p.excluded_count} {p.excluded_count === 1 ? 'token was' : 'tokens were'} left out.</b>{' '}
              {p.excluded_count === 1 ? 'Its pool is' : 'Their pools are'} too small to pay out what it looks like it's worth.
            </div>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div className="label">
              Your holdings · {p.holdings.length}
              {excludedCount > 0 && <span style={{ color: 'var(--muted)' }}>{' · '}{excludedCount} left out</span>}
            </div>
            <div className="group">
              {p.holdings.map(h => <HoldingRow key={h.token} h={h} dup={dup(h)} />)}
            </div>
            {(p.excluded?.length ?? 0) > 0 && (
              <details className="group" style={{ marginTop: 2 }}>
                <summary className="label" style={{ cursor: 'pointer', padding: '12px 18px', listStyle: 'none' }}>
                  Left out · {p.excluded!.length} — worth more on paper than its pool can pay
                </summary>
                {p.excluded!.map(h => <HoldingRow key={h.token} h={h} dup={false} />)}
              </details>
            )}
          </div>
        </div>

        <aside className="dark-card glance" style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="serif" style={{ fontSize: 26, letterSpacing: '-.02em', lineHeight: 1.1 }}>Safety at a glance</div>
          {[{ k: 'Clean', v: clean, c: '#0A8F0E' }, { k: 'Risky', v: risky, c: '#E59A18' }, { k: 'Not counted', v: outOfTotal, c: '#B9B3A6' }].map(g => (
            <div key={g.k} style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 15 }}>
              <span style={{ width: 10, height: 10, borderRadius: '50%', background: g.c }} />
              <span style={{ flex: 1 }}>{g.k}</span>
              <b className="num" style={{ fontSize: 20 }}>{g.v}</b>
            </div>
          ))}
          <div style={{ fontSize: 13, lineHeight: 1.5, color: '#B9B3A6', borderTop: '1px solid rgba(255,255,255,.12)', paddingTop: 14 }}>
            Open any token to see why it got its verdict.
          </div>
        </aside>
      </div>
    </div>
  )
}

function HoldingRow({ h, dup }: { h: Holding; dup: boolean }) {
  const native = h.token === 'native'
  const counted = h.counted !== false
  const tag = tagFor(h)
  const pct = h.pnl_pct ?? h.change_24h_pct
  const neg = (pct ?? 0) < 0
  const body = (
    <>
      <Avatar seed={h.token} letter={h.symbol} image={h.image_url} size={44} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 700, fontSize: 16, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {h.symbol}{tag && <span className={`tag ${tag.cls}`}>{tag.label}</span>}
        </div>
        <div style={{ color: 'var(--muted)', fontSize: 14, marginTop: 2 }}>
          {amount(h.amount)} {h.symbol}
          {dup && !native && <span className="num" style={{ fontSize: 12, marginLeft: 8, color: 'var(--muted)' }}>{shortAddr(h.token)}</span>}
        </div>
      </div>
      <div style={{ textAlign: 'right' }}>
        <div className="num" style={{ fontWeight: 700, fontSize: 16 }}>
          {h.price_usd == null ? '—' : counted ? usd(h.value_usd) : 'Not counted'}
        </div>
        <div title={h.price_usd == null ? 'No price yet' : !counted ? (h.not_counted_reason ?? '') : undefined}
          style={{ fontWeight: 600, fontSize: 14, marginTop: 2, maxWidth: 170, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', color: !counted || h.price_usd == null || pct == null ? 'var(--muted)' : neg ? 'var(--red)' : 'var(--green-text)' }}>
          {h.price_usd == null ? 'No price yet' : !counted ? (h.not_counted_reason ?? '') : pct == null ? '' : signedPct(pct)}
        </div>
      </div>
    </>
  )
  const style = { display: 'flex', alignItems: 'center', gap: 14, padding: '16px 18px', minHeight: 76, boxShadow: '0 -1px 0 var(--hairline)', textDecoration: 'none', color: 'var(--ink)', opacity: counted ? 1 : .62 } as const
  return native
    ? <div style={style} className="hrow">{body}</div>
    : <Link to={`/app/safety/${h.token}`} style={style} className="hrow">{body}</Link>
}
