import { useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { Logo } from '../components/Logo'
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
    <div className="page" style={{ minHeight: '100dvh', padding: '28px 20px' }}>
      <Link to="/" style={{ alignSelf: 'center', textDecoration: 'none' }}><Logo size={32} /></Link>
      <div style={{ margin: 'auto', width: '100%', maxWidth: 440, display: 'flex', flexDirection: 'column', gap: 24, padding: '32px 0' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, textAlign: 'center' }}>
          <div className="serif" style={{ fontSize: 42, lineHeight: 1.05, letterSpacing: '-.03em' }}>Connect a wallet to continue</div>
          <div style={{ color: 'var(--body)', fontSize: 16, lineHeight: 1.5 }}>
            Your wallet is your Chainfolio account. It shows your portfolio and keeps your alerts.
          </div>
        </div>
        <WalletConnectPanel onConnected={login} />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, alignItems: 'center', marginTop: 8 }}>
          <div style={{ color: 'var(--muted)', fontSize: 14, lineHeight: 1.5 }}>Only you can move your money. We can never touch it.</div>
          <button onClick={() => { logout(); nav('/') }} style={{ background: 'none', border: 0, color: 'var(--muted)', fontSize: 14, fontWeight: 600, padding: 8, minHeight: 44 }}>Sign out</button>
        </div>
      </div>
    </div>
  )
}
