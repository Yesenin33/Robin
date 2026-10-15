import { useState } from 'react'

import { api } from '../api/client'
import type { TokenOut } from '../api/types'
import { clearLinkToken, readLinkToken } from '../lib/linkToken'
import { openExternal } from '../lib/telegram'
import { connectMode, connectWallet, insideTelegram, walletConnectEnabled } from '../lib/wallet'

/** "Connect wallet": injected wallet if present, otherwise WalletConnect (QR on desktop, app deep link on phones). */
export function WalletConnectPanel({ onConnected }: { onConnected: (t: TokenOut) => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mode = connectMode()
  const inTelegram = insideTelegram()

  const run = async (via: 'injected' | 'walletconnect') => {
    setError(null)
    setBusy(true)
    try {
      const t = await connectWallet(via, readLinkToken())
      clearLinkToken()
      onConnected(t)
    } catch (e: any) {
      setBusy(false)
      setError(
        e?.code === 4001 || e?.name === 'WalletCancelled' ? 'You closed the wallet window. Tap Connect wallet to try again.'
          : e?.status === 401 && /expired/i.test(e?.message ?? '') ? e.message
          : e?.message ?? "Couldn't connect your wallet.")
    }
  }

  const primary = () => {
    if (mode === 'unavailable') {
      setError('No wallet found in this browser. Open Chainfolio in a wallet app, or install a browser wallet.')
      return
    }
    void run(mode)
  }

  const openInBrowser = async () => {
    setError(null)
    try {
      const { token } = await api.linkToken()
      openExternal(`${window.location.origin}/login?link=${encodeURIComponent(token)}`)
    } catch {
      setError("Couldn't open your browser. Try again in a moment.")
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {error && <div className="notice-error" role="alert">{error}</div>}
      <button className="btn btn-primary" style={{ height: 54 }} disabled={busy} onClick={primary}>
        {busy ? 'Waiting for your wallet…' : 'Connect wallet'}
      </button>
      {busy && (
        <div style={{ color: 'var(--muted-2)', fontSize: 13, textAlign: 'center', lineHeight: 1.45 }}>
          Approve the request in your wallet app, then come back here.
        </div>
      )}
      {!busy && mode === 'injected' && walletConnectEnabled && (
        <button onClick={() => void run('walletconnect')} style={linkStyle}>Use another wallet (WalletConnect)</button>
      )}
      {!busy && inTelegram && (
        <button onClick={() => void openInBrowser()} style={linkStyle}>Open in browser instead</button>
      )}
      {!busy && !error && (
        <div style={{ color: 'var(--muted-2)', fontSize: 13, textAlign: 'center', lineHeight: 1.45 }}>
          Any Robinhood Chain wallet. Nothing to write down, nothing we can touch.
        </div>
      )}
    </div>
  )
}

const linkStyle = { background: 'none', border: 0, color: 'var(--muted-2)', fontSize: 13, padding: 6 } as const
