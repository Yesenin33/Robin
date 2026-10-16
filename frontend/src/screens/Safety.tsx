import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { api, isQueued } from '../api/client'
import type { Metric, Safety as SafetyT, Verdict } from '../api/types'
import { EmptyState, ErrorState, ProgressState } from '../components/States'
import { SwapSheet } from '../components/SwapSheet'
import { Avatar, toast } from '../components/ui'
import { ago, isAddress, shortAddr } from '../lib/format'
import { loadRecent, saveRecent } from '../lib/recent'
import { swapUrl } from '../lib/swap'

const VERDICT: Record<Verdict, { vc: string; vt: string; soft: string; icon: string }> = {
  CLEAN: { vc: 'var(--green)', vt: 'var(--green-ink)', soft: 'var(--green-soft)', icon: '✓' },
  RISKY: { vc: 'var(--amber)', vt: 'var(--amber-ink)', soft: 'var(--amber-soft)', icon: '!' },
  DANGER: { vc: 'var(--red)', vt: 'var(--on-red)', soft: 'var(--red-soft)', icon: '✕' },
  // neutral grey on purpose: "we couldn't check" must never look like a result, least of all a green one
  UNKNOWN: { vc: 'var(--muted-2)', vt: 'var(--bg)', soft: 'var(--card)', icon: '?' },
}
const TONE = { ok: 'var(--text)', warn: 'var(--amber-text)', bad: 'var(--red-text)', unknown: 'var(--muted-2)' }

const sleep = (ms: number, signal: AbortSignal) => new Promise<void>(res => {
  const t = setTimeout(res, ms)
  signal.addEventListener('abort', () => { clearTimeout(t); res() }, { once: true })
})

export type Waiting = { state: 'queued' | 'running'; elapsedS: number; stage?: string | null; etaS?: number | null }
export const STAGES: [string, string][] = [
  ['contract', 'Reading the contract'], ['market', 'Checking liquidity'], ['transfers', 'Reading transfers'],
  ['holders', 'Checking holders'], ['scoring', 'Scoring the risks'],
]
const GIVE_UP_S = 180

/** GET /safety returns at once. A 202 means the scan is queued/running in the worker: poll until it lands. */
async function fetchSafety(ca: string, signal: AbortSignal, onWait: (w: Waiting) => void): Promise<SafetyT | null> {
  const t0 = Date.now()
  for (;;) {
    const r = await api.safety(ca, signal)
    if (signal.aborted) return null
    if (!isQueued(r)) return r
    const elapsedS = Math.round((Date.now() - t0) / 1000)
    if (elapsedS > GIVE_UP_S) throw new Error('scan timeout')
    onWait({ state: r.state ?? 'queued', elapsedS, stage: r.stage, etaS: r.eta_s })
    await sleep((r.retry_after_s ?? 3) * 1000, signal)
    if (signal.aborted) return null
  }
}

export function Safety() {
  const { ca } = useParams()
  const nav = useNavigate()
  const recent = loadRecent()
  const input = useRef<HTMLInputElement>(null)
  const go = (a: string) => nav(`/app/safety/${a.trim().toLowerCase()}`)

  return (
    <div className="app-page">
      <AddressBar onSubmit={go} inputRef={input} />
      {recent.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div className="t-caption">Recently checked</div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {recent.map(t => (
              <button key={t.ca} className={`chip ${t.ca === ca?.toLowerCase() ? 'on' : ''}`} onClick={() => go(t.ca)}>
                <b>{t.sym}</b><small className="num">{shortAddr(t.ca)}</small>
              </button>
            ))}
          </div>
        </div>
      )}
      {ca && isAddress(ca)
        ? <SafetyView key={ca} ca={ca.toLowerCase()} onLeave={() => nav('/app/safety')} />
        : <EmptyState emoji="🛡️" title="Nothing to check yet" body="Paste a token address and we'll tell you if it looks safe."
            cta="Paste address" onCta={() => void pasteFromClipboard(go, input.current)} />}
    </div>
  )
}

/** Read the clipboard if the browser lets us. It often doesn't (iOS needs a gesture + permission, plain-http
 *  pages and some in-app browsers have no Clipboard API) - then say so and put the cursor in the field. */
export async function pasteFromClipboard(go: (a: string) => void, field: HTMLInputElement | null, read?: () => Promise<string>) {
  const reader = read ?? (navigator.clipboard?.readText ? () => navigator.clipboard.readText() : undefined)
  if (!reader) { toast("This browser won't let us read the clipboard. Long-press the field and choose Paste."); field?.focus(); return }
  let text = ''
  try { text = (await reader()).trim() } catch {
    toast("The clipboard is blocked here. Long-press the field and choose Paste."); field?.focus(); return
  }
  if (isAddress(text)) { go(text); return }
  if (field && text) { field.value = text; field.dispatchEvent(new Event('input', { bubbles: true })) }
  toast(text ? "That doesn't look like a token address" : 'Your clipboard is empty')
  field?.focus()
}

function AddressBar({ onSubmit, inputRef }: { onSubmit: (a: string) => void; inputRef: React.RefObject<HTMLInputElement> }) {
  const [v, setV] = useState('')
  const submit = () => {
    if (isAddress(v)) { onSubmit(v); setV('') } else toast(v.trim() ? "That address doesn't look right" : 'Paste or type a token address first')
  }
  return (
    <form onSubmit={e => { e.preventDefault(); submit() }} style={{ display: 'flex', gap: 8 }}>
      <input ref={inputRef} className="input" style={{ flex: 1, minWidth: 0 }} placeholder="Token address (0x…)" value={v}
        onChange={e => setV(e.target.value)} spellCheck={false} autoCapitalize="off" autoCorrect="off" inputMode="text"
        aria-label="Token address" />
      {v.trim() === '' && (
        <button type="button" className="btn btn-ghost" style={{ padding: '0 14px', fontSize: 14 }}
          onClick={() => void pasteFromClipboard(onSubmit, inputRef.current)}>Paste</button>
      )}
      <button type="submit" className="btn btn-primary" style={{ padding: '0 18px', fontSize: 15 }}>Check</button>
    </form>
  )
}

type View =
  | { status: 'wait'; w: Waiting }
  | { status: 'ready'; data: SafetyT }
  | { status: 'error' }

function SafetyView({ ca, onLeave }: { ca: string; onLeave: () => void }) {
  const [view, setView] = useState<View>({ status: 'wait', w: { state: 'queued', elapsedS: 0 } })
  const [run, setRun] = useState(0)
  const [swap, setSwap] = useState<'buy' | 'sell' | null>(null)
  const ctl = useRef<AbortController | null>(null)

  useEffect(() => {
    const c = new AbortController()
    ctl.current = c
    setView({ status: 'wait', w: { state: 'queued', elapsedS: 0 } })
    fetchSafety(ca, c.signal, w => setView({ status: 'wait', w }))
      .then(async d => {
        if (!d) return
        saveRecent({ ca, sym: d.symbol ?? shortAddr(ca), ts: Date.now() })
        setView({ status: 'ready', data: d })
        // an earlier verdict is on screen while a fresh check runs: swap it in quietly when it lands
        for (let i = 0; d.refreshing && i < 30 && !c.signal.aborted; i++) {
          await sleep(3000, c.signal)
          if (c.signal.aborted) return
          const r = await api.safety(ca, c.signal).catch(() => null)
          if (r && !isQueued(r) && !r.refreshing) { setView({ status: 'ready', data: r }); return }
        }
      })
      .catch(() => { if (!c.signal.aborted) setView({ status: 'error' }) })
    return () => c.abort()
  }, [ca, run])

  if (view.status === 'wait') {
    const w = view.w
    const cur = STAGES.findIndex(([k]) => k === w.stage)
    return <ProgressState title="Checking this token"
      detail={w.state === 'running' ? 'A checker is on it now.' : 'Waiting for a free checker…'}
      steps={w.state === 'running' ? STAGES.map(([, t]) => t) : undefined} current={cur >= 0 ? cur : 0} etaS={w.etaS}
      elapsedS={w.elapsedS} onCancel={() => { ctl.current?.abort(); onLeave() }} />
  }
  if (view.status === 'error') {
    return <ErrorState body="We couldn't run the safety check." onRetry={() => setRun(n => n + 1)} />
  }
  const s = view.data
  const v = VERDICT[s.verdict]
  const extra = s.risks.filter(r => !s.summary.includes(r)) // summary already quotes the top risks

  const trade = (side: 'buy' | 'sell') => setSwap(side)

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <Avatar seed={ca} letter={s.symbol ?? '?'} size={52} image={s.image_url} />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 22, fontWeight: 800 }}>{s.name ?? s.symbol ?? 'Unknown token'}</div>
          <button className="num" onClick={() => { void navigator.clipboard?.writeText(ca); toast('Address copied') }}
            style={{ background: 'none', border: 0, padding: 0, fontSize: 13, color: 'var(--muted-2)', marginTop: 2 }}>
            {shortAddr(ca)} · tap to copy
          </button>
        </div>
      </div>

      {s.refreshing && <div className="notice col-12" role="status">Showing the check from {ago(s.scanned_at)}. A fresh one is running…</div>}
      <div className="col-7" style={{ borderRadius: 24, padding: 22, background: v.soft, boxShadow: `inset 0 0 0 1px ${v.vc}`, display: 'flex', flexDirection: 'column', gap: 10 }}>
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

      <div className="col-5 sticky" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
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
      </div>
      {swap && <SwapSheet side={swap} safety={s} onClose={() => setSwap(null)} fallbackUrl={swapUrl(ca, swap)} />}
    </>
  )
}

function MetricCard({ m }: { m: Metric }) {
  const color = m.tone === 'ok' && m.key === 'real_hands' ? 'var(--green-text)' : TONE[m.tone] ?? TONE.unknown
  return (
    <div className="card" style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ fontSize: 12, color: 'var(--muted-2)', fontWeight: 600 }}>{m.label}</div>
      <div className="num" style={{ fontSize: 24, fontWeight: 800, color }}>{m.value} <span style={{ fontSize: 16 }}>{m.emoji}</span></div>
      <div style={{ fontSize: 12, color: 'var(--muted-2)', lineHeight: 1.35 }}>{m.hint}</div>
    </div>
  )
}
