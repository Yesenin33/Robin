import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

import { WalletConnectPanel } from '../components/WalletConnect'
import { useAuth } from '../lib/auth'
import { insideTelegram } from '../lib/wallet'

/** The one blocking step for a session without a wallet: the wallet is the identity. */
export function ConnectGate() {
  const { login, logout, refresh } = useAuth()
  const nav = useNavigate()

  // "Open in browser" finishes in another app: pick the wallet up when the user comes back
  useEffect(() => {
    if (!insideTelegram()) return
    const t = setInterval(() => { void refresh() }, 3000)
    return () => clearInterval(t)
  }, [refresh])

  return (
    <div className="page">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 24 }}>
        <div style={{ fontSize: 30, fontWeight: 800, letterSpacing: '-.02em' }}>Connect a wallet to continue</div>
        <div style={{ color: 'var(--muted)', fontSize: 15, lineHeight: 1.5 }}>
          Your wallet is your Chainfolio account — it shows your portfolio and keeps your alerts.
        </div>
      </div>
      <WalletConnectPanel onConnected={login} />
      <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: 14, alignItems: 'center' }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', color: 'var(--muted-2)', fontSize: 13, lineHeight: 1.5 }}>
          <span>🔒</span><span>Only you can move your money. We can never touch it.</span>
        </div>
        <button onClick={() => { logout(); nav('/') }} style={{ background: 'none', border: 0, color: 'var(--muted-2)', fontSize: 13 }}>
          Sign out
        </button>
      </div>
    </div>
  )
}
