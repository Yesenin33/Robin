import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { api, isQueued } from '../api/client'
import type { Metric, Safety as SafetyT, Verdict } from '../api/types'
import { EmptyState, ErrorState, Skeleton } from '../components/States'
import { Avatar, toast } from '../components/ui'
import { ago, isAddress, shortAddr } from '../lib/format'
import { swapUrl } from '../lib/swap'
import { useAsync } from '../lib/useAsync'

const VERDICT: Record<Verdict, { vc: string; vt: string; soft: string; icon: string }> = {
  CLEAN: { vc: 'var(--green)', vt: 'var(--green-ink)', soft: 'var(--green-soft)', icon: '✓' },
  RISKY: { vc: 'var(--amber)', vt: 'var(--amber-ink)', soft: 'var(--amber-soft)', icon: '!' },
  DANGER: { vc: 'var(--red)', vt: '#fff', soft: 'var(--red-soft)', icon: '✕' },
  // neutral grey on purpose: "we couldn't check" must never look like a result, least of all a green one
  UNKNOWN: { vc: 'var(--muted-2)', vt: 'var(--surface)', soft: 'var(--card)', icon: '?' },
}
const TONE = { ok: '#fff', warn: 'var(--amber)', bad: 'var(--red)', unknown: 'var(--muted-2)' }
const RECENT_KEY = 'cf_recent'

type Recent = { ca: string; sym: string }
const loadRecent = (): Recent[] => { try { return JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]') } catch { return [] } }
const saveRecent = (r: Recent) => {
  const list = [r, ...loadRecent().filter(x => x.ca !== r.ca)].slice(0, 3)
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(list)) } catch { /* ignore */ }
}

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))

/** GET /safety; a 202 means a scan is running — poll until it lands. */
async function fetchSafety(ca: string): Promise<SafetyT> {
  for (let i = 0; i < 24; i++) {
    const r = await api.safety(ca)
    if (!isQueued(r)) return r
    await sleep(5000)
  }
  throw new Error('scan timeout')
}

export function Safety() {
  const { ca } = useParams()
  const nav = useNavigate()
  const recent = loadRecent()

  return (
    <div className="app-page">
      <AddressBar onSubmit={a => nav(`/app/safety/${a}`)} />
      {recent.length > 0 && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {recent.map(t => {
            const on = t.ca === ca?.toLowerCase()
            return (
              <button key={t.ca} onClick={() => nav(`/app/safety/${t.ca}`)} style={{
                padding: '8px 14px', borderRadius: 99, fontWeight: 700, fontSize: 13,
                border: `1px solid ${on ? '#fff' : 'var(--line)'}`, background: on ? '#fff' : 'transparent', color: on ? 'var(--surface)' : 'var(--muted)',
              }}>{t.sym}</button>
            )
          })}
        </div>
      )}
      {ca && isAddress(ca)
        ? <SafetyView key={ca} ca={ca.toLowerCase()} />
        : <EmptyState emoji="🛡️" title="Nothing to check yet" body="Paste a token address and we'll tell you if it looks safe."
            cta="Paste address" onCta={async () => {
              const text = (await navigator.clipboard?.readText().catch(() => ''))?.trim() ?? ''
              if (isAddress(text)) nav(`/app/safety/${text}`)
              else toast('No token address in your clipboard')
            }} />}
    </div>
  )
}

function AddressBar({ onSubmit }: { onSubmit: (a: string) => void }) {
  const [v, setV] = useState('')
  return (
    <form onSubmit={e => { e.preventDefault(); if (isAddress(v)) { onSubmit(v.trim()); setV('') } else toast("That address doesn't look right") }}>
      <input className="input" style={{ height: 48, fontSize: 14 }} placeholder="Paste token address (0x…)" value={v}
        onChange={e => setV(e.target.value)} spellCheck={false} autoCapitalize="off" />
    </form>
  )
}

function SafetyView({ ca }: { ca: string }) {
  const { state, reload } = useAsync(async () => {
    const s = await fetchSafety(ca)
    saveRecent({ ca, sym: s.symbol ?? shortAddr(ca) })
    return s
  }, [ca])

  if (state.status === 'loading') return <Skeleton rows={3} />
  if (state.status === 'error') return <ErrorState body="We couldn't run the safety check." onRetry={() => void reload()} />
  const s = state.data
  const v = VERDICT[s.verdict]
  const extra = s.risks.filter(r => !s.summary.includes(r)) // summary already quotes the top risks

  const trade = (side: 'buy' | 'sell') => {
    const url = swapUrl(ca, side)
    if (!url) { toast('Trading is coming soon'); return }
    window.open(url, '_blank', 'noopener,noreferrer')
  }

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <Avatar seed={ca} letter={s.symbol ?? '?'} size={52} />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 22, fontWeight: 800 }}>{s.name ?? s.symbol ?? 'Unknown token'}</div>
          <button className="num" onClick={() => { void navigator.clipboard?.writeText(ca); toast('Address copied') }}
            style={{ background: 'none', border: 0, padding: 0, fontSize: 13, color: 'var(--muted-2)', marginTop: 2 }}>
            {shortAddr(ca)} · tap to copy
          </button>
        </div>
      </div>

      <div style={{ borderRadius: 24, padding: 22, background: v.soft, border: `1px solid ${v.vc}`, display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ display: 'inline-flex', alignSelf: 'flex-start', alignItems: 'center', gap: 8, background: v.vc, color: v.vt, fontWeight: 800, fontSize: 18, letterSpacing: '.08em', padding: '8px 16px', borderRadius: 99 }}>
          {v.icon} {s.verdict}
        </div>
        <div style={{ fontSize: 17, lineHeight: 1.45, fontWeight: 600 }}>{s.summary}</div>
        {extra.length > 0 && (
          <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--text-2)', fontSize: 14, lineHeight: 1.5 }}>
            {extra.map(r => <li key={r}>{r}</li>)}
          </ul>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        {s.metrics.map(m => <MetricCard key={m.key} m={m} />)}
      </div>
      {(s.holders_stale_at || s.stale) && (
        <div style={{ fontSize: 12, color: 'var(--muted-2)', textAlign: 'center', lineHeight: 1.4 }}>
          {s.holders_stale_at ? `Holder numbers are from ${ago(s.holders_stale_at)} — we'll update them shortly.`
            : "We couldn't refresh this just now, so this is the previous check."}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 4 }}>
        {/* no green call to action when we couldn't check the token */}
        <button className={`btn ${s.verdict === 'UNKNOWN' ? 'btn-ghost' : 'btn-primary'}`} style={{ height: 54, fontWeight: 800 }}
          onClick={() => trade('buy')}>Buy</button>
        <button className="btn btn-ghost" style={{ height: 54, fontWeight: 800 }} onClick={() => trade('sell')}>Sell</button>
      </div>
      {s.verdict === 'DANGER' && <div style={{ textAlign: 'center', fontSize: 12, color: 'var(--red-text)' }}>Buying this one is not recommended.</div>}
      {s.verdict === 'UNKNOWN' && <div style={{ textAlign: 'center', fontSize: 12, color: 'var(--muted-2)' }}>We couldn't check this one — don't treat it as safe.</div>}
    </>
  )
}

function MetricCard({ m }: { m: Metric }) {
  const color = m.tone === 'ok' && m.key === 'real_hands' ? 'var(--green)' : TONE[m.tone] ?? TONE.unknown
  return (
    <div className="card" style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ fontSize: 12, color: 'var(--muted-2)', fontWeight: 600 }}>{m.label}</div>
      <div className="num" style={{ fontSize: 24, fontWeight: 800, color }}>{m.value} <span style={{ fontSize: 16 }}>{m.emoji}</span></div>
      <div style={{ fontSize: 12, color: 'var(--muted-2)', lineHeight: 1.35 }}>{m.hint}</div>
    </div>
  )
}
