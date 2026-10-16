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
  CLEAN: { soft: '#E4F6DD', ink: '#0D3B10', word: 'Clean' },
  RISKY: { soft: '#FFEFCC', ink: '#4A2C00', word: 'Risky' },
  DANGER: { soft: '#FCE6E2', ink: '#5E120C', word: 'Danger' },
  UNKNOWN: { soft: '#ECE9E1', ink: '#110E08', word: 'Unknown' },
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
    <div style={{ background: vs.soft, color: vs.ink, borderRadius: 16, padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div className="num" style={{ fontSize: 12, opacity: .75 }}>{sym} · {shortAddr(safety.address)} — a name is not an identifier, check the address</div>
      <div style={{ fontSize: 14, lineHeight: 1.45 }}><b>{safety.verdict}</b> · {safety.summary}</div>
    </div>
  )
  const sheetButton = { display: 'flex', width: '100%' } as const

  // ---- the in-app flow is not available: say why, keep the external link as a fallback ----
  if (cfg === 'off' || acct === 'none') {
    return (
      <Sheet title={title} onClose={onClose}>
        {verdictBlock}
        <div className="notice">{cfg === 'off' ? "Trading inside Chainfolio isn't switched on yet." : 'Connect a wallet that can send transactions to trade here.'}</div>
        {fallbackUrl
          ? <a className="btn btn-primary" style={sheetButton} href={fallbackUrl} target="_blank" rel="noopener noreferrer">Open in Uniswap</a>
          : <button className="btn btn-ghost" onClick={onClose}>Close</button>}
      </Sheet>
    )
  }

  if (phase.k === 'done') {
    return (
      <Sheet title="Done" onClose={onClose}>
        <div className="t-body">Your {side === 'buy' ? 'purchase' : 'sale'} of {sym} went through.</div>
        <a className="num" style={{ wordBreak: 'break-all', fontSize: 13, color: 'var(--green-text)' }} href={`${EXPLORER}/tx/${phase.hash}`} target="_blank" rel="noopener noreferrer">{phase.hash}</a>
        <button className="btn btn-primary" onClick={() => { toast('Portfolio will refresh'); onClose() }}>Back to the app</button>
      </Sheet>
    )
  }

  const sending = phase.k === 'sending'
  const q = quote
  const outText = q ? `${formatUnits(q.amount_out_raw, q.decimals_out)} ${side === 'buy' ? sym : q.pay_with}` : '—'
  const minText = q ? `${formatUnits(q.min_out_raw, q.decimals_out)} ${side === 'buy' ? sym : q.pay_with}` : '—'
  const blocked = !q || (risky && !ack)
  const label = !amountOk ? 'Enter an amount' : !q ? 'Waiting for a quote…' : risky && !ack ? 'Tick the box above to continue' : (q.steps.length) > 1 ? 'Approve & swap' : 'Confirm in wallet'

  if (sending) {
    const step = phase.i + 1, of = q?.steps.length ?? 1
    return (
      <Sheet title={title} onClose={() => undefined}>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14, textAlign: 'center', padding: '24px 0' }} role="status" aria-live="polite">
          <div className="spinner" />
          <div style={{ fontWeight: 700, fontSize: 18 }}>{phase.what === 'wallet' ? `Confirm in your wallet (${step} of ${of})…` : 'Waiting for the network…'}</div>
          <div style={{ fontSize: 14, color: 'var(--muted)', lineHeight: 1.5, maxWidth: 300 }}>
            {phase.what === 'wallet' ? 'Your wallet is asking you to approve this trade. Nothing is sent until you do.'
              : "Your trade was sent. We're waiting for it to be confirmed on Robinhood Chain."}
          </div>
        </div>
      </Sheet>
    )
  }

  return (
    <Sheet title={title} onClose={onClose}>
      {verdictBlock}
      <div style={{ display: 'flex', gap: 8 }}>
        <input className="input" style={{ flex: 1, minWidth: 0, fontSize: 17, fontWeight: 600 }} inputMode="decimal"
          placeholder={side === 'buy' ? `Amount of ${payWith} to spend` : `Amount of ${sym} to sell`}
          value={amountText} onChange={e => setAmountText(e.target.value)} aria-label="Amount" />
        <div role="group" aria-label={side === 'buy' ? 'Pay with' : 'Receive'} style={{ display: 'flex', gap: 4 }}>
          {(['ETH', 'USDG'] as const).map(p => (
            <button key={p} className={`opt big ${payWith === p ? 'on' : ''}`} onClick={() => setPayWith(p)}>{p}</button>
          ))}
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <span className="t-caption">Slippage</span>
        {SLIPPAGE.map(b => <button key={b} className={`opt ${slip === b ? 'on' : ''}`} onClick={() => setSlip(b)}>{pctText(b)}</button>)}
      </div>

      {qBusy && <div className="t-caption" role="status">Getting a live quote…</div>}
      {qErr && <div className="notice-error" role="alert">{qErr}</div>}
      {q && (
        <div className="card" style={{ borderRadius: 18, padding: '6px 16px' }}>
          <div className="row-kv"><span>You receive (estimate)</span><b className="num">{outText}</b></div>
          <div className="row-kv"><span>Minimum you'll get</span><b className="num">{minText}</b></div>
          <div className="row-kv"><span>Price impact</span><b className="num">{q.price_impact_pct == null ? "can't estimate" : `${q.price_impact_pct}%`}</b></div>
          <div className="row-kv"><span>Pool fee</span><b className="num">{pctText(q.fee_bps)}</b></div>
          <div className="row-kv"><span>Slippage allowed</span><b className="num">{pctText(q.slippage_bps)}</b></div>
        </div>
      )}
      {q && q.steps.length > 1 && (
        <div className="t-caption" style={{ fontWeight: 500 }}>
          First time with this token: {q.steps.length - 1} one-time approval{q.steps.length > 2 ? 's' : ''} in your wallet, then the swap.
        </div>
      )}
      {!q && !qBusy && !qErr && amountOk && <div className="t-caption" style={{ fontWeight: 500 }}>Enter an amount to see a live quote.</div>}

      {risky && q && (
        <div onClick={() => setAck(a => !a)} role="checkbox" aria-checked={ack} tabIndex={0}
          onKeyDown={e => { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); setAck(a => !a) } }}
          style={{ display: 'flex', gap: 12, alignItems: 'flex-start', cursor: 'pointer', fontSize: 14, lineHeight: 1.45, minHeight: 44 }}>
          <span style={{ width: 26, height: 26, borderRadius: 8, background: ack ? 'var(--ink)' : 'transparent', boxShadow: 'inset 0 0 0 1.5px #110E08', color: 'var(--lime)', display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 'none', fontWeight: 700 }}>{ack ? '✓' : ''}</span>
          {safety.verdict === 'DANGER' ? 'I understand this token looks like a scam and I could lose everything I put in.' : "I understand we couldn't check this token and it may not be safe."}
        </div>
      )}
      {note && <div className="notice" role="status">{note}</div>}
      <button className="btn" disabled={blocked} onClick={() => void confirm()}
        style={{ background: blocked ? '#D9D5C9' : safety.verdict === 'DANGER' ? 'var(--red)' : 'var(--ink)', color: blocked ? '#6F6A5F' : safety.verdict === 'DANGER' ? '#fff' : 'var(--paper)', opacity: 1 }}>{label}</button>
      {fallbackUrl && <a className="t-caption" style={{ textAlign: 'center', fontWeight: 500 }} href={fallbackUrl} target="_blank" rel="noopener noreferrer">Or open this trade in Uniswap</a>}
    </Sheet>
  )
}
