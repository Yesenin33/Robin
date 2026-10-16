import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

import { WalletConnectPanel } from '../components/WalletConnect'
import { useAuth } from '../lib/auth'

/** Sign-in is wallet-only: there is no e-mail path. The wallet is the account. */
export function Onboarding() {
  const nav = useNavigate()
  const { user, login, refresh } = useAuth()

  useEffect(() => { if (user) nav('/app/portfolio', { replace: true }) }, [user, nav])

  // A wallet connected elsewhere (Telegram "open in browser" -> link token) lands in storage: pick it up.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => { if (e.key === 'cf_token') void refresh() }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [refresh])

  return (
    <div className="page">
      <button onClick={() => nav('/')} style={{ alignSelf: 'flex-start', background: 'none', border: 0, color: 'var(--muted-2)', fontSize: 14, padding: 0 }}>← Back</button>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ fontSize: 30, fontWeight: 800, letterSpacing: '-.02em' }}>Let's get you in</div>
        <div style={{ color: 'var(--muted)', fontSize: 15, lineHeight: 1.5 }}>Connect your wallet — it's your account. It takes about 10 seconds.</div>
      </div>

      <WalletConnectPanel onConnected={login} />

      <div style={{ marginTop: 'auto', display: 'flex', gap: 10, alignItems: 'flex-start', color: 'var(--muted-2)', fontSize: 13, lineHeight: 1.5 }}>
        <span>🔒</span><span>Only you can move your money. We can never touch it.</span>
      </div>
    </div>
  )
}
