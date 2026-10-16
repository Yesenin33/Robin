import { useEffect, useMemo, useRef, useState } from 'react'

import { api, ApiError } from '../api/client'
import type { Safety, SwapConfig, SwapQuote } from '../api/types'
import { shortAddr } from '../lib/format'
import { formatUnits, isRejection, parseUnits, pctText } from '../lib/swapMath'
import { CHAIN_HEX_ID, tradeProvider } from '../lib/wallet'
import type { Eip1193 } from '../lib/siwe'
import { Sheet } from './Sheet'
import { toast } from './ui'

const VERDICT_STYLE = {
  CLEAN: { c: 'var(--green)', bg: 'var(--green-soft)', t: 'Looks healthy' },
  RISKY: { c: 'var(--amber)', bg: 'var(--amber-soft)', t: 'Proceed with care' },
  DANGER: { c: 'var(--red)', bg: 'var(--red-soft)', t: 'Very likely a scam' },
  UNKNOWN: { c: 'var(--muted-2)', bg: 'var(--card)', t: "We couldn't check this token" },
} as const
const SLIPPAGE = [50, 100, 300]
const EXPLORER = 'https://robinhoodchain.blockscout.com'
const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))

type Phase =
  | { k: 'form' }
  | { k: 'sending'; i: number; what: 'wallet' | 'chain' }
  | { k: 'done'; hash: string }

/** Wait for a receipt through the user's own provider; a reverted tx is an error, never "done". */
async function receipt(eth: Eip1193, hash: string): Promise<void> {
  for (let i = 0; i < 90; i++) {
    const r = await eth.request({ method: 'eth_getTransactionReceipt', params: [hash] })
    if (r) { if (r.status === '0x0') throw new Error('The transaction failed on-chain. Nothing was spent except network fees.'); return }
    await sleep(2000)
  }
  throw new Error("We're still waiting for the network. Check your wallet for the result.")
}

export function SwapSheet({ side, safety, onClose, fallbackUrl }: {
  side: 'buy' | 'sell'; safety: Safety; onClose: () => void; fallbackUrl: string | null
}) {
  const [cfg, setCfg] = useState<SwapConfig | null | 'off'>(null)
  const [acct, setAcct] = useState<{ provider: Eip1193; account: string } | null | 'none'>(null)
  const [amountText, setAmountText] = useState('')
  const [payWith, setPayWith] = useState<'ETH' | 'USDG'>('ETH')
  const [slip, setSlip] = useState(100)
  const [quote, setQuote] = useState<SwapQuote | null>(null)
  const [qErr, setQErr] = useState<string | null>(null)
  const [qBusy, setQBusy] = useState(false)
  const [ack, setAck] = useState(false)
  const [phase, setPhase] = useState<Phase>({ k: 'form' })
  const [note, setNote] = useState<string | null>(null)
  const seq = useRef(0)

  const sym = safety.symbol ?? 'token'
  const vs = VERDICT_STYLE[safety.verdict]
  const risky = safety.verdict === 'DANGER' || safety.verdict === 'UNKNOWN'
  const decimalsIn = side === 'sell' ? undefined : payWith === 'ETH' ? 18 : 6

  useEffect(() => {
    api.swapConfig().then(c => setCfg(c.enabled ? c : 'off')).catch(() => setCfg('off'))
    tradeProvider().then(a => setAcct(a ?? 'none')).catch(() => setAcct('none'))
  }, [])

  // sells need the token's decimals, which the server reads: the amount is parsed there; here we only gate on a number
  const amountOk = useMemo(() => (decimalsIn ? parseUnits(amountText, decimalsIn) : parseUnits(amountText, 18)) !== null, [amountText, decimalsIn])

  // live quote, debounced; a late answer for an old input is discarded
  useEffect(() => {
    setQuote(null); setQErr(null); setAck(false)
    if (cfg === null || cfg === 'off' || acct === null || acct === 'none' || !amountOk) return
    const id = ++seq.current
    const t = setTimeout(async () => {
      setQBusy(true)
      try {
        const q = await api.swapQuote({ token: safety.address, side, amount: amountText.trim().replace(',', '.'), slippage_bps: slip, wallet: acct.account, pay_with: payWith })
        if (id === seq.current) setQuote(q)
      } catch (e) {
        if (id === seq.current) setQErr(e instanceof ApiError ? e.message : "We couldn't get a live quote right now.")
      } finally { if (id === seq.current) setQBusy(false) }
    }, 500)
    return () => clearTimeout(t)
  }, [amountText, payWith, slip, cfg, acct, amountOk, safety.address, side])

  async function confirm() {
    if (!quote || acct === null || acct === 'none') return
    const { provider, account } = acct
    setNote(null)
    try {
      try { await provider.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: CHAIN_HEX_ID }] }) } catch { /* checked below */ }
      const chain: string = await provider.request({ method: 'eth_chainId' })
      if (chain.toLowerCase() !== CHAIN_HEX_ID) throw new Error('Switch your wallet to Robinhood Chain and try again.')
      let last = ''
      for (let i = 0; i < quote.steps.length; i++) {
        const st = quote.steps[i]
        setPhase({ k: 'sending', i, what: 'wallet' })
        const hash: string = await provider.request({
          method: 'eth_sendTransaction', params: [{ from: account, to: st.tx.to, data: st.tx.data, value: st.tx.value }],
        })
        setPhase({ k: 'sending', i, what: 'chain' })
        await receipt(provider, hash)
        last = hash
      }
      setPhase({ k: 'done', hash: last })
    } catch (e) {
      setPhase({ k: 'form' })
      if (isRejection(e)) { setNote('Cancelled in your wallet. Nothing was sent.'); return }  // quiet: no error toast, no re-prompt
      setNote(e instanceof Error ? e.message : 'The wallet could not send this.')
    }
  }

  const title = `${side === 'buy' ? 'Buy' : 'Sell'} ${sym}`
  const verdictBlock = (
    <div style={{ background: vs.bg, boxShadow: `inset 0 0 0 1px ${vs.c}`, borderRadius: 'var(--r2)', padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div className="t-caption num">{sym} · {shortAddr(safety.address)} — a name is not an identifier, check the address</div>
      <div style={{ color: vs.c, fontWeight: 800, fontSize: 14 }}>{safety.verdict} · {vs.t}</div>
      <div className="t-caption" style={{ color: 'var(--text-2)' }}>{safety.summary}</div>
    </div>
  )

  // ---- the in-app flow is not available: say why, keep the external link as a fallback ----
  if (cfg === 'off' || acct === 'none') {
    return (
      <Sheet title={title} onClose={onClose}>
        {verdictBlock}
        <div className="notice">{cfg === 'off' ? "Trading inside Chainfolio isn't switched on yet." : "Connect a wallet that can send transactions to trade here."}</div>
        {fallbackUrl
          ? <a className="btn btn-primary" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', textDecoration: 'none' }} href={fallbackUrl} target="_blank" rel="noopener noreferrer">Open in Uniswap</a>
          : <button className="btn btn-ghost" onClick={onClose}>Close</button>}
      </Sheet>
    )
  }

  if (phase.k === 'done') {
    return (
      <Sheet title="Done" onClose={onClose}>
        <div className="t-body">Your {side === 'buy' ? 'purchase' : 'sale'} of {sym} went through.</div>
        <a className="num" style={{ wordBreak: 'break-all', fontSize: 13 }} href={`${EXPLORER}/tx/${phase.hash}`} target="_blank" rel="noopener noreferrer">{phase.hash}</a>
        <button className="btn btn-primary" onClick={() => { toast('Portfolio will refresh'); onClose() }}>Back to the app</button>
      </Sheet>
    )
  }

  const sending = phase.k === 'sending'
  const q = quote
  const outText = q ? `${formatUnits(q.amount_out_raw, q.decimals_out)} ${side === 'buy' ? sym : q.pay_with}` : '—'
  const minText = q ? `${formatUnits(q.min_out_raw, q.decimals_out)} ${side === 'buy' ? sym : q.pay_with}` : '—'
  const label = sending
    ? phase.what === 'wallet' ? `Confirm in your wallet (${phase.i + 1} of ${q?.steps.length ?? 1})…` : 'Waiting for the network…'
    : risky && !ack ? 'Tick the box above to continue' : (q?.steps.length ?? 1) > 1 ? 'Approve & swap' : 'Confirm in wallet'

  return (
    <Sheet title={title} onClose={sending ? () => undefined : onClose}>
      {verdictBlock}
      <div style={{ display: 'flex', gap: 8, alignItems: 'stretch' }}>
        <input className="input" style={{ flex: 1, minWidth: 0 }} inputMode="decimal" placeholder={side === 'buy' ? `Amount of ${payWith} to spend` : `Amount of ${sym} to sell`}
          value={amountText} onChange={e => setAmountText(e.target.value)} aria-label="Amount" disabled={sending} />
        <div role="group" aria-label={side === 'buy' ? 'Pay with' : 'Receive'} style={{ display: 'flex', gap: 4 }}>
          {(['ETH', 'USDG'] as const).map(p => (
            <button key={p} className={`chip ${payWith === p ? 'on' : ''}`} disabled={sending} onClick={() => setPayWith(p)} style={{ alignItems: 'center' }}><b>{p}</b></button>
          ))}
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <span className="t-caption">Slippage</span>
        {SLIPPAGE.map(b => <button key={b} className={`chip ${slip === b ? 'on' : ''}`} disabled={sending} onClick={() => setSlip(b)} style={{ alignItems: 'center', minWidth: 56 }}><b>{pctText(b)}</b></button>)}
      </div>

      {qBusy && <div className="t-caption" role="status">Getting a live quote…</div>}
      {qErr && <div className="notice-error" role="alert">{qErr}</div>}
      {q && (
        <div className="card" style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div className="row-kv"><span>You receive (estimate)</span><b className="num">{outText}</b></div>
          <div className="row-kv"><span>Minimum you'll get</span><b className="num">{minText}</b></div>
          <div className="row-kv"><span>Price impact</span><b className="num">{q.price_impact_pct == null ? "can't estimate" : `${q.price_impact_pct}%`}</b></div>
          <div className="row-kv"><span>Pool fee</span><b className="num">{pctText(q.fee_bps)}</b></div>
          <div className="row-kv"><span>Slippage allowed</span><b className="num">{pctText(q.slippage_bps)}</b></div>
          {q.steps.length > 1 && <div className="t-caption">First time with this token: {q.steps.length - 1} one-time approval{q.steps.length > 2 ? 's' : ''} in your wallet, then the swap.</div>}
        </div>
      )}
      {!q && !qBusy && !qErr && amountOk && <div className="t-caption">Enter an amount to see a live quote.</div>}

      {risky && q && (
        <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', fontSize: 14, lineHeight: '20px', minHeight: 44 }}>
          <input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} style={{ width: 24, height: 24, marginTop: 0, flex: 'none' }} />
          <span>{safety.verdict === 'DANGER' ? 'I understand this token looks like a scam and I could lose everything I put in.' : "I understand we couldn't check this token and it may not be safe."}</span>
        </label>
      )}
      {note && <div className="notice" role="status">{note}</div>}
      <button className="btn btn-primary" disabled={!q || sending || (risky && !ack)} onClick={() => void confirm()}
        style={safety.verdict === 'DANGER' ? { background: 'var(--red)', color: '#fff', boxShadow: 'none' } : undefined}>{label}</button>
      {fallbackUrl && <a className="t-caption" style={{ textAlign: 'center' }} href={fallbackUrl} target="_blank" rel="noopener noreferrer">Or open this trade in Uniswap</a>}
    </Sheet>
  )
}
