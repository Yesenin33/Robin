import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { api, isQueued } from '../api/client'
import type { Metric, Safety as SafetyT, Verdict } from '../api/types'
import { ProgressState } from '../components/States'
import { SwapSheet } from '../components/SwapSheet'
import { Avatar, toast } from '../components/ui'
import { ago, isAddress, shortAddr } from '../lib/format'
import { loadRecent, saveRecent } from '../lib/recent'
import { swapUrl } from '../lib/swap'

const VERDICT: Record<Verdict, { word: string; soft: string; ink: string; dot: string }> = {
  CLEAN: { word: 'Clean', soft: '#E4F6DD', ink: '#0D3B10', dot: '#0A8F0E' },
  RISKY: { word: 'Risky', soft: '#FFEFCC', ink: '#4A2C00', dot: '#C77700' },
  DANGER: { word: 'Danger', soft: '#FCE6E2', ink: '#5E120C', dot: '#C8281E' },
  // neutral on purpose: "we couldn't check" must never look like a result, least of all a green one
  UNKNOWN: { word: 'Unknown', soft: '#ECE9E1', ink: '#110E08', dot: '#5E594E' },
}
const TONE = { ok: 'var(--ink)', warn: 'var(--amber-text)', bad: 'var(--red)', unknown: 'var(--muted)' }

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
  const showing = !!ca && isAddress(ca)

  return (
    <div className="app-page">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <h1 className="display">Is this token safe?</h1>
        <div className="lead">Paste a token address. We check holders, liquidity and who can sell, in plain words.</div>
      </div>
      <AddressBar onSubmit={go} inputRef={input} />
      {recent.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div className="t-caption">Recently checked · a name isn't an identifier, so check the address</div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {recent.map(t => (
              <button key={t.ca} className={`chip ${t.ca === ca?.toLowerCase() ? 'on' : ''}`} onClick={() => go(t.ca)}>
                <b>{t.sym}</b><small>{shortAddr(t.ca)} · {ago(new Date(t.ts).toISOString())}</small>
              </button>
            ))}
          </div>
        </div>
      )}
      {showing
        ? <SafetyView key={ca} ca={ca!.toLowerCase()} onLeave={() => nav('/app/safety')} />
        : (
          <div style={{ padding: '32px 0', color: 'var(--body)', fontSize: 16, lineHeight: 1.5 }}>
            <b style={{ color: 'var(--ink)' }}>Nothing to check yet.</b> Paste an address above, or pick one you checked before.
            <div style={{ marginTop: 16 }}>
              <button className="btn btn-ghost btn-sm" onClick={() => void pasteFromClipboard(go, input.current)}>Paste address</button>
            </div>
          </div>
        )}
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
        <button type="button" className="btn btn-ghost" style={{ padding: '0 20px', fontSize: 15 }}
          onClick={() => void pasteFromClipboard(onSubmit, inputRef.current)}>Paste</button>
      )}
      <button type="submit" className="btn btn-primary" style={{ padding: '0 26px', fontSize: 15 }}>Check</button>
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
    return (
      <div className="notice-error" role="alert" style={{ borderRadius: 20, padding: 20, display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'flex-start' }}>
        <div><b>We couldn't run the safety check.</b> Nothing was bought or sold. Try again in a moment.</div>
        <button className="btn btn-primary btn-sm" onClick={() => setRun(n => n + 1)}>Try again</button>
      </div>
    )
  }
  const s = view.data
  const v = VERDICT[s.verdict]
  const extra = s.risks.filter(r => !s.summary.includes(r)) // the summary already quotes the top risks
  const canBuy = s.verdict === 'CLEAN' || s.verdict === 'RISKY'

  return (
    <>
      {s.refreshing && <div className="notice" role="status">Showing the check from {ago(s.scanned_at)}. A fresh one is running…</div>}
      <div className="grid-safety">
        <div className="stack">
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <Avatar seed={ca} letter={s.symbol ?? '?'} size={52} image={s.image_url} />
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 22, fontWeight: 700 }}>{s.name ?? s.symbol ?? 'Unknown token'}</div>
              <button className="num" onClick={() => { void navigator.clipboard?.writeText(ca); toast('Address copied') }}
                style={{ background: 'none', border: 0, padding: 0, fontSize: 14, color: 'var(--muted)', marginTop: 2, minHeight: 24 }}>
                {shortAddr(ca)} · tap to copy
              </button>
            </div>
          </div>
          <div style={{ borderRadius: 28, padding: 28, background: v.soft, display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <span style={{ width: 14, height: 14, borderRadius: '50%', background: v.dot, flex: 'none' }} />
              <span className="serif" style={{ fontSize: 56, lineHeight: 1, letterSpacing: '-.03em', color: v.ink }}>{v.word}</span>
              <span style={{ marginLeft: 'auto', fontSize: 13, fontWeight: 700, letterSpacing: '.08em', padding: '6px 12px', borderRadius: 99, background: v.dot, color: '#fff' }}>{s.verdict}</span>
            </div>
            <div style={{ fontSize: 18, lineHeight: 1.45, fontWeight: 500, color: v.ink }}>{s.summary}</div>
            {extra.length > 0 && (
              <ul style={{ margin: 0, paddingLeft: 20, display: 'flex', flexDirection: 'column', gap: 6, fontSize: 15, lineHeight: 1.45, color: v.ink }}>
                {extra.map(r => <li key={r}>{r}</li>)}
              </ul>
            )}
          </div>
        </div>

        <div className="stack" style={{ gap: 16 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            {s.metrics.map(m => <MetricCard key={m.key} m={m} />)}
          </div>
          {(s.holders_stale_at || s.stale) && (
            <div className="t-caption" style={{ fontWeight: 500 }}>
              {s.holders_stale_at ? `Holder numbers are from ${ago(s.holders_stale_at)}. We refresh them in the background.`
                : "We couldn't refresh this just now, so this is the previous check."}
            </div>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            {/* no filled call to action when we couldn't check the token or it looks like a scam */}
            <button className={`btn ${canBuy ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setSwap('buy')}>Buy</button>
            <button className="btn btn-ghost" onClick={() => setSwap('sell')}>Sell</button>
          </div>
          {s.verdict === 'DANGER' && <div className="t-caption" style={{ textAlign: 'center', fontWeight: 500 }}>Buying this one is not recommended.</div>}
          {s.verdict === 'UNKNOWN' && <div className="t-caption" style={{ textAlign: 'center', fontWeight: 500 }}>Unknown is not the same as safe.</div>}
          {s.verdict === 'RISKY' && <div className="t-caption" style={{ textAlign: 'center', fontWeight: 500 }}>Think about the amount before you buy.</div>}
        </div>
      </div>
      {swap && <SwapSheet side={swap} safety={s} onClose={() => setSwap(null)} fallbackUrl={swapUrl(ca, swap)} />}
    </>
  )
}

function MetricCard({ m }: { m: Metric }) {
  const color = m.tone === 'ok' && m.key === 'real_hands' ? 'var(--green-text)' : TONE[m.tone] ?? TONE.unknown
  return (
    <div className="card" style={{ borderRadius: 20, padding: 18, display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div className="t-caption">{m.label}</div>
      <div className="num" style={{ fontSize: 28, fontWeight: 600, letterSpacing: '-.03em', color }}>{m.value}</div>
      <div style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.4 }}>{m.hint}</div>
    </div>
  )
}
